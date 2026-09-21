"""唤醒桥接层 v4

职责：为每个 Harness 提供多路唤醒通道，支持：
1. HTTP 回调 — 对能接收回调的 Harness 直接 POST 唤醒消息
2. 文件轮询 — 对 Trae CN 等不可回调的 Harness，写入监听目录
3. 剪贴板桥接 — 最后兜底，写入剪贴板供用户手动粘贴

与 harness_adapter 的关系：
- harness_adapter 负责 Harness 注册/会话管理/消息桥接
- wakeup_bridge 仅负责唤醒通知，不参与后续协商与委托
"""

from __future__ import annotations
import asyncio
import json
import os
import socket
import ipaddress
from datetime import datetime
from pathlib import Path
from typing import Optional

import httpx

from .protocol import WakeupMethod, WakeupMessage, HarnessInfo

_FORBIDDEN_HOSTS = {
    "localhost", "127.0.0.1", "::1", "0.0.0.0",
    "169.254.169.254", "metadata.google.internal",
    "metadata.azure.internal", "metadata", "instance-data",
}


def _validate_url(url: str) -> tuple[bool, str]:
    """SSRF 防护：禁止向本机/内网/元数据地址发回调。"""
    import urllib.parse as _up
    if not url:
        return True, ""
    try:
        parsed = _up.urlparse(url)
    except Exception:
        return False, "URL 无法解析"
    if parsed.scheme not in ("http", "https"):
        return False, "仅支持 http/https"
    host = (parsed.hostname or "").lower().rstrip(".")
    if not host:
        return False, "URL 缺少主机名"
    if host in _FORBIDDEN_HOSTS:
        return False, f"禁止回调本机/内网/元数据地址: {host}"
    try:
        infos = socket.getaddrinfo(host, parsed.port or 80, proto=socket.IPPROTO_TCP)
        for info in infos:
            ip = ipaddress.ip_address(info[4][0])
            if (ip.is_private or ip.is_loopback or ip.is_link_local
                    or ip.is_reserved or ip.is_multicast or ip.is_unspecified):
                return False, f"回调域名解析到内网/保留地址: {host} -> {ip}"
    except Exception:
        return False, f"回调域名无法解析: {host}"
    return True, ""


class WakeupBridge:
    """唤醒消息桥接器 — 支持 HTTP / 文件轮询 / 剪贴板三种方式"""

    def __init__(self, poll_base_dir: Path = Path(os.environ.get("TEMP", ".")) / "agent_wakeup_pipe"):
        self.poll_base_dir = poll_base_dir
        self.poll_base_dir.mkdir(parents=True, exist_ok=True)

    async def wake(
        self,
        harness_info: HarnessInfo,
        wakeup_msg: WakeupMessage,
        timeout: float = 60.0,
    ) -> tuple[bool, str, Optional[str]]:
        """向单个 Harness 发送唤醒消息。

        Returns:
            (ok, method_used, error_or_response)
            - ok: 是否成功送达
            - method_used: 实际使用的唤醒方式
            - error_or_response: 失败时为错误信息，成功时可能是举手/拒绝回复
        """
        method = harness_info.wakeup_method

        # 1) HTTP 回调优先
        if method == WakeupMethod.HTTP and (harness_info.wakeup_url or harness_info.callback_url):
            return await self._wake_via_http(harness_info, wakeup_msg, timeout)

        # 2) 文件轮询
        if method == WakeupMethod.FILE_POLL:
            return await self._wake_via_file_poll(harness_info, wakeup_msg, timeout)

        # 3) 剪贴板兜底
        return await self._wake_via_clipboard(harness_info, wakeup_msg)

    # ── HTTP 回调 ──────────────────────────────────────────────

    async def _wake_via_http(
        self, info: HarnessInfo, msg: WakeupMessage, timeout: float
    ) -> tuple[bool, str, Optional[str]]:
        """POST 唤醒消息到 Harness 回调地址（带 SSRF 校验）"""
        url = info.wakeup_url or info.callback_url
        msg.wakeup_method = WakeupMethod.HTTP

        ok, err = _validate_url(url)
        if not ok:
            return False, "http", f"回调地址被拒绝: {err}"

        try:
            async with httpx.AsyncClient(timeout=30.0) as c:
                r = await c.post(url, json={
                    "type": "wakeup",
                    "message": msg.model_dump(),
                })
                if r.status_code == 200:
                    data = r.json()
                    resp = data.get("response", data.get("content", ""))
                    return True, "http", resp
                return False, "http", f"HTTP {r.status_code}"
        except Exception as e:
            return False, "http", str(e)

    # ── 文件轮询 ───────────────────────────────────────────────

    async def _wake_via_file_poll(
        self, info: HarnessInfo, msg: WakeupMessage, timeout: float
    ) -> tuple[bool, str, Optional[str]]:
        """写入监听目录，等待 Harness 侧脚本检测后回复"""
        watch_dir = Path(info.wakeup_dir) if info.wakeup_dir else self._default_watch_dir(info.harness_id)
        watch_dir.mkdir(parents=True, exist_ok=True)
        reply_dir = watch_dir / "replies"
        reply_dir.mkdir(parents=True, exist_ok=True)

        msg.wakeup_method = WakeupMethod.FILE_POLL

        # 写入唤醒消息
        ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        msg_path = watch_dir / f"wakeup_{ts}_{msg.id}.json"
        msg_path.write_text(json.dumps(msg.model_dump(), ensure_ascii=False), encoding="utf-8")

        # 等待回复（轮询 replies 目录）
        deadline = asyncio.get_event_loop().time() + timeout
        while asyncio.get_event_loop().time() < deadline:
            replies = sorted(reply_dir.glob(f"reply_{msg.id}_*.json"))
            if replies:
                try:
                    reply_path = replies[0]
                    data = json.loads(reply_path.read_text(encoding="utf-8"))
                    reply_path.unlink()  # 消费回复
                    response = data.get("response", data.get("content", ""))
                    return True, "file_poll", response
                except Exception as e:
                    return False, "file_poll", f"读取回复失败: {e}"
            await asyncio.sleep(1.0)

        return False, "file_poll", "超时未回复"

    def _default_watch_dir(self, harness_id: str) -> Path:
        return self.poll_base_dir / f"wakeup_{harness_id}"

    # ── 剪贴板桥接（兜底）─────────────────────────────────────

    async def _wake_via_clipboard(
        self, info: HarnessInfo, msg: WakeupMessage
    ) -> tuple[bool, str, Optional[str]]:
        """写入系统剪贴板，供用户手动粘贴到 Harness 会话"""
        msg.wakeup_method = WakeupMethod.CLIPBOARD

        clipboard_text = (
            f"===== 新任务通知 =====\n"
            f"任务ID: {msg.task_id}\n"
            f"标题: {msg.task_title}\n"
            f"描述: {msg.task_description}\n"
            f"发起时间: {msg.sent_at}\n"
            f"----------------------------\n"
            f"请将此消息粘贴到 Harness「{info.harness_name}」的会话中，\n"
            f"让 Harness 内的 AI 决定是否参与。\n"
            f"如果能参与，请回复: 举手 {msg.task_id} <你的能力和角色>\n"
            f"如果不能，请回复: 拒绝 {msg.task_id}\n"
            f"============================"
        )

        try:
            self._set_clipboard_windows(clipboard_text)
            return True, "clipboard", None
        except Exception as e:
            return False, "clipboard", f"剪贴板写入失败: {e}"

    def _set_clipboard_windows(self, text: str):
        """Windows 剪贴板写入（使用 PowerShell）"""
        import subprocess
        # 将文本通过管道传给 clip.exe
        proc = subprocess.run(
            ["powershell", "-Command", f"Set-Clipboard -Value $input"],
            input=text,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.strip())

    # ── 批量唤醒 ────────────────────────────────────────────────

    async def wake_all(
        self,
        harnesses: list[HarnessInfo],
        wakeup_msg: WakeupMessage,
        timeout: float = 60.0,
    ) -> list[dict]:
        """向所有 Harness 发送唤醒消息，并行执行。

        Returns:
            [{harness_id, harness_name, ok, method, response, error}, ...]
        """
        tasks = [self.wake(info, wakeup_msg, timeout) for info in harnesses]
        results = await asyncio.gather(*tasks)

        return [
            {
                "harness_id": harnesses[i].harness_id,
                "harness_name": harnesses[i].harness_name,
                "ok": results[i][0],
                "method": results[i][1],
                "response": results[i][2] if results[i][0] else None,
                "error": None if results[i][0] else results[i][2],
            }
            for i in range(len(harnesses))
        ]


# ═══════════════════════════════════════════════════════════════
# 互助唤醒桥接
# ═══════════════════════════════════════════════════════════════

class MutualWakeupBridge:
    """Harness 之间互助唤醒：一个 Harness 内的 AI 可请求唤醒其他 Harness。

    工作流：
    1. Harness A 内的 AI 提交互助唤醒请求 → POST /api/wakeup/mutual
    2. WakeupAgent 收到请求后，向目标 Harness B 发送唤醒消息
    3. Harness B 回复后，结果返回给 Harness A
    """

    def __init__(self, wakeup_bridge: WakeupBridge):
        self.wakeup_bridge = wakeup_bridge

    async def mutual_wake(
        self,
        from_harness_id: str,
        target_harness_ids: list[str],
        task_title: str,
        task_description: str = "",
        reason: str = "",
    ) -> dict:
        """Harness 间的互助唤醒请求。

        Args:
            from_harness_id: 发起唤醒请求的 Harness
            target_harness_ids: 目标 Harness 列表
            task_title: 任务标题
            task_description: 任务描述
            reason: 唤醒理由

        Returns:
            {success, total, woken, failed, details: [...]}
        """
        # 这里先占位，实际需要 harness_adapter 的 harness_manager 配合
        # 由 server.py 在 API 端注入 harness_manager 引用
        return {
            "success": False,
            "total": len(target_harness_ids),
            "woken": 0,
            "failed": len(target_harness_ids),
            "detail": "需要 server.py 注入 harness_manager 引用后可用",
        }


# 全局单例
wakeup_bridge = WakeupBridge()
mutual_wakeup_bridge = MutualWakeupBridge(wakeup_bridge)
