"""HTTP API 唤醒模块 v1（2026-08-29）

让平台通过 harness 自带的 HTTP API 自动唤醒并推送任务，而不是只写文件等 harness 轮询。
典型场景：某桌面 Agent（示例名 Harness-X）自带 HTTP API（端口 3721，POST /message），
平台 POST 消息 → Harness-X 入队自主处理 → Harness-X 写 outbox 回报 → 平台接入讨论区。

三个能力：
1. probe_http_api(base_url)      — 探测 harness HTTP API 是否可用，返回 (ok, 探测详情)
2. send_http_api_message(cfg, payload) — 向 harness HTTP API 推送一条消息
3. poll_outbox_replies(cfg, from_id)  — 轮询 harness outbox 回报，匹配 reply_to

协议约定（对齐典型 http_api harness 现状，可扩展）：
- 入站：POST {api_base_url}{api_message_path}  body: {"from_id","content","channel"}
- 出站：harness 写 {api_outbox_dir}/reply_*.json  字段: {"timestamp","message_id","text","reply_to"}
"""

from __future__ import annotations

import json
import os
import time
import urllib.request
import urllib.parse
import urllib.error
from datetime import datetime
from pathlib import Path
from typing import Optional


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """SSRF 纵深防御：禁止跟随重定向，避免经校验的公网 URL 302 跳内网/元数据地址。"""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(req.full_url, code, msg, headers, fp)


_OPENER = urllib.request.build_opener(_NoRedirectHandler)


def _check_http_scheme(base_url: str) -> bool:
    """纵深防御：出站前强校验协议白名单（外层已有 validate_harness_api_url，此处兜底）。"""
    return base_url.lower().startswith(("http://", "https://"))


def probe_http_api(base_url: str, message_path: str = "/message", timeout: float = 3.0) -> tuple[bool, str]:
    """探测 harness HTTP API 是否可用。

    对候选的 {base_url}{message_path} 发一个极小的 POST（或 GET 探测）：
    - 能连上且返回结构像 API → (True, 详情)
    - 连不上/超时 → (False, 错误)

    Returns: (ok, detail)
    """
    if not base_url:
        return False, "api_base_url 为空"
    if not _check_http_scheme(base_url):
        return False, "仅支持 http/https"
    base = base_url.rstrip("/")
    url = f"{base}{message_path}" if message_path else base
    try:
        # 先用一个探测性 POST（content 为空串，harness 应返回 400 或 200，能证明服务活着）
        probe_body = json.dumps({
            "from_id": "platform-probe",
            "content": "",
            "channel": "probe",
        }, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            url, data=probe_body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with _OPENER.open(req, timeout=timeout) as resp:
            status = resp.status
            raw = resp.read().decode("utf-8", "replace")[:200]
            return True, f"HTTP API 可达 {url}（HTTP {status}）：{raw}"
    except Exception as e:
        return False, f"HTTP API 探测失败 {url}: {e}"


def send_http_api_message(
    base_url: str,
    content: str,
    from_id: str = "agent-community-platform",
    channel: str = "API",
    message_path: str = "/message",
    timeout: float = 15.0,
) -> tuple[bool, str]:
    """向 harness HTTP API 推送一条消息（唤醒 + 派任务）。

    Returns: (是否成功推送, 说明)
    """
    if not base_url:
        return False, "api_base_url 为空，无法 HTTP 推送"
    if not content:
        return False, "content 为空"
    if not _check_http_scheme(base_url):
        return False, "仅支持 http/https"
    base = base_url.rstrip("/")
    url = f"{base}{message_path}" if message_path else base
    payload = {
        "from_id": from_id,
        "content": content,
        "channel": channel,
    }
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    try:
        with _OPENER.open(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", "replace")
            ok = resp.status < 300
            return ok, body[:300]
    except Exception as e:
        return False, f"HTTP API 推送失败: {e}"


def _parse_outbox_reply(path: Path) -> Optional[dict]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return None
        return data
    except Exception:
        return None


def poll_outbox_replies(
    outbox_dir: str,
    reply_to: Optional[str] = None,
    consume: bool = True,
) -> list[dict]:
    """轮询 harness outbox 目录，读取回报文件。

    Args:
        outbox_dir: harness 回报目录
        reply_to: 若指定，只返回 reply_to 匹配的回报
        consume: True 则把已读的回报移到 processed/（避免重复处理）

    Returns: 匹配的回报列表 [{timestamp, message_id, text, reply_to, _file}]
    """
    if not outbox_dir:
        return []
    outbox = Path(outbox_dir)
    if not outbox.exists():
        return []
    processed = outbox / "processed"
    if consume:
        processed.mkdir(parents=True, exist_ok=True)

    matches = []
    for f in sorted(outbox.glob("reply_*.json")):
        data = _parse_outbox_reply(f)
        if not data:
            continue
        data["_file"] = str(f)
        if reply_to is not None:
            if data.get("reply_to") != reply_to:
                continue
        matches.append(data)
        if consume:
            target = processed / f"{int(time.time()*1000)}_{f.name}"
            try:
                f.rename(target)
            except Exception:
                try:
                    f.replace(target)
                except Exception:
                    pass
    return matches


def wait_outbox_reply(
    outbox_dir: str,
    reply_to: str,
    timeout: float = 60.0,
    poll_interval: float = 2.0,
    consume: bool = True,
) -> Optional[dict]:
    """等待 harness 在 outbox 写入匹配 reply_to 的回报（阻塞轮询）。

    Returns: 回报 dict，超时返回 None
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        replies = poll_outbox_replies(outbox_dir, reply_to=reply_to, consume=consume)
        if replies:
            return replies[0]
        time.sleep(poll_interval)
    return None
