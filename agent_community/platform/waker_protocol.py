"""喊人专员发包协议 v5

定义三种通用发包协议的具体实现，供 WakeupAdapter 和 server.py 调用：
- file_poll_send: 写 JSON 到 Harness 监听目录
- http_callback_send: POST JSON 到 callback_url
- websocket_send: 发消息到 ws endpoint

所有协议统一 30s 超时，失败自动回退到 file_poll 兜底。
"""

from __future__ import annotations
import asyncio
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

import httpx


# ── 常量 ──────────────────────────────────────────────────────

WAKER_TIMEOUT = 30.0          # 统一超时（秒）
DEFAULT_POLL_BASE = Path(os.environ.get("TEMP", ".")) / "agent_waker_pipe"


# ── 公共数据结构 ──────────────────────────────────────────────

class WakerTask:
    """下发给外部 Harness 专员的举手判断任务"""
    def __init__(self, task_id: str, command: str, waker_id: str = ""):
        self.task_id = task_id
        self.command = command
        self.waker_id = waker_id
        self.sent_at = datetime.now().isoformat()

    def to_dict(self) -> dict:
        return {
            "type": "waker_task",
            "task_id": self.task_id,
            "command": self.command,
            "waker_id": self.waker_id,
            "sent_at": self.sent_at,
        }


class WakerResponse:
    """外部 Harness 专员返回的举手判断结果"""
    def __init__(self, task_id: str, harness_id: str, harness_name: str,
                 hand_raised: bool, capability_claim: str = "",
                 model_used: str = "", protocol: str = ""):
        self.task_id = task_id
        self.harness_id = harness_id
        self.harness_name = harness_name
        self.hand_raised = hand_raised
        self.capability_claim = capability_claim
        self.model_used = model_used
        self.protocol = protocol

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "harness_id": self.harness_id,
            "harness_name": self.harness_name,
            "hand_raised": self.hand_raised,
            "capability_claim": self.capability_claim,
            "model_used": self.model_used,
            "protocol": self.protocol,
        }


# ═══════════════════════════════════════════════════════════════
# 协议 1: file_poll — 写入 JSON 到指定目录
# ═══════════════════════════════════════════════════════════════

async def file_poll_send(
    harness_info: dict,      # 含 harness_id / harness_name / wakeup_dir / callback_url 等
    task: WakerTask,
    poll_base: Path = DEFAULT_POLL_BASE,
) -> tuple[bool, Optional[str]]:
    """通过文件轮询发送举手判断任务，等待回复文件。

    harness_info 所需字段：
        - harness_id: str
        - harness_name: str
        - wakeup_dir: str (监听目录)

    Returns:
        (ok, response_text_or_error)
        超时返回 (False, None)
    """
    hid = harness_info.get("harness_id", "?")
    hname = harness_info.get("harness_name", hid)

    watch_dir = Path(harness_info.get("wakeup_dir", "")) if harness_info.get("wakeup_dir") else (
        poll_base / f"waker_{hid}"
    )
    watch_dir.mkdir(parents=True, exist_ok=True)
    reply_dir = watch_dir / "replies"
    reply_dir.mkdir(parents=True, exist_ok=True)

    # 写入任务文件
    ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    msg_path = watch_dir / f"waker_task_{ts}_{task.task_id[:8]}.json"
    content = {
        "type": "waker_task",
        "task_id": task.task_id,
        "command": task.command,
        "waker_id": task.waker_id,
        "sent_at": task.sent_at,
        "expected_response_format": {
            "hand_raised": "bool — 是否举手参与",
            "capability_claim": "str — 能贡献的能力和角色（100字以内）",
            "model_used": "str — 使用的 AI 模型名称",
        },
    }
    msg_path.write_text(json.dumps(content, ensure_ascii=False, indent=2), encoding="utf-8")

    # 轮询等待回复
    deadline = asyncio.get_event_loop().time() + WAKER_TIMEOUT
    while asyncio.get_event_loop().time() < deadline:
        reply_pattern = f"waker_reply_{task.task_id[:8]}_*.json"
        replies = sorted(reply_dir.glob(reply_pattern))
        if replies:
            try:
                data = json.loads(replies[0].read_text(encoding="utf-8"))
                replies[0].unlink()  # 消费回复
                resp_text = json.dumps(data, ensure_ascii=False)
                return True, resp_text
            except Exception as e:
                return False, f"file_poll 读取回复失败 [{hname}]: {e}"
        await asyncio.sleep(1.0)

    # 超时
    return False, None


# ═══════════════════════════════════════════════════════════════
# 协议 2: http_callback — POST JSON 到 callback_url
# ═══════════════════════════════════════════════════════════════

async def http_callback_send(
    harness_info: dict,
    task: WakerTask,
) -> tuple[bool, Optional[str]]:
    """通过 HTTP POST 发送举手判断任务到 callback_url。

    harness_info 所需字段：
        - harness_id / harness_name
        - callback_url: str (或 wakeup_url)

    Returns:
        (ok, response_text_or_error)
        超时/连接失败返回 (False, error_string)
    """
    hid = harness_info.get("harness_id", "?")
    hname = harness_info.get("harness_name", hid)

    url = harness_info.get("callback_url", "") or harness_info.get("wakeup_url", "")
    if not url:
        return False, f"http_callback [{hname}]: 无回调 URL"

    body = {
        "type": "waker_task",
        "task_id": task.task_id,
        "command": task.command,
        "waker_id": task.waker_id,
        "sent_at": task.sent_at,
        "message": (
            f"【举手判断任务】\n"
            f"任务ID: {task.task_id}\n"
            f"任务内容: {task.command}\n\n"
            f"请评估你是否能参与此任务。如果能，回复 JSON：\n"
            f'{{"hand_raised": true, "capability_claim": "你的能力和角色（100字以内）", "model_used": "模型名称"}}\n'
            f"如果不能，回复 JSON：\n"
            f'{{"hand_raised": false, "reason": "原因"}}'
        ),
    }

    try:
        async with httpx.AsyncClient(timeout=WAKER_TIMEOUT) as c:
            r = await c.post(url, json=body)
            if r.status_code == 200:
                return True, r.text
            return False, f"http_callback [{hname}]: HTTP {r.status_code}"
    except httpx.TimeoutException:
        return False, f"http_callback [{hname}]: 超时 ({WAKER_TIMEOUT}s)"
    except Exception as e:
        return False, f"http_callback [{hname}]: {e}"


# ═══════════════════════════════════════════════════════════════
# 协议 3: websocket — 发消息到 ws endpoint
# ═══════════════════════════════════════════════════════════════

async def websocket_send(
    harness_info: dict,
    task: WakerTask,
) -> tuple[bool, Optional[str]]:
    """通过 WebSocket 发送举手判断任务。

    harness_info 所需字段：
        - harness_id / harness_name
        - ws_endpoint: str (ws://host:port/path)
        或通过 harness_manager 获取已连接的 ws

    Returns:
        (ok, response_text_or_error)
        超时/连接失败返回 (False, error_string)
    """
    hid = harness_info.get("harness_id", "?")
    hname = harness_info.get("harness_name", hid)

    ws_url = harness_info.get("ws_endpoint", "")
    if not ws_url:
        return False, f"websocket [{hname}]: 无 ws endpoint"

    body = {
        "type": "waker_task",
        "task_id": task.task_id,
        "command": task.command,
        "waker_id": task.waker_id,
        "sent_at": task.sent_at,
        "message": (
            f"【举手判断任务】\n"
            f"任务ID: {task.task_id}\n"
            f"任务内容: {task.command}\n\n"
            f"请评估你是否能参与此任务。回复 JSON：\n"
            f'{{"hand_raised": true/false, "capability_claim": "...", "model_used": "..."}}'
        ),
    }

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(WAKER_TIMEOUT, connect=10.0)) as c:
            # 使用 httpx 的 WebSocket 支持（如果可用）
            # 注意：httpx 不原生支持 WS，这里使用 HTTP fallback 模拟
            # 实际生产环境应使用 websockets 库
            r = await c.post(ws_url.replace("ws://", "http://").replace("wss://", "https://"),
                             json=body)
            if r.status_code == 200:
                return True, r.text
            return False, f"websocket [{hname}]: HTTP {r.status_code}"
    except httpx.TimeoutException:
        return False, f"websocket [{hname}]: 超时 ({WAKER_TIMEOUT}s)"
    except Exception as e:
        return False, f"websocket [{hname}]: {e}"


# ═══════════════════════════════════════════════════════════════
# 统一发包入口 — 按协议分派，失败回退到 file_poll 兜底
# ═══════════════════════════════════════════════════════════════

async def dispatch_waker_task(
    harness_info: dict,
    task: WakerTask,
    protocols: list[str] | None = None,
    poll_base: Path = DEFAULT_POLL_BASE,
) -> tuple[bool, str, Optional[str]]:
    """按 Harness 声明的协议列表依次尝试，失败则回退到 file_poll 兜底。

    Args:
        harness_info: Harness 信息字典
        task: 举手判断任务
        protocols: 协议优先级列表（默认 ["http_callback", "websocket", "file_poll"]）
        poll_base: file_poll 的基准目录

    Returns:
        (ok, protocol_used, response_or_error)
    """
    if protocols is None:
        protocols = ["http_callback", "websocket", "file_poll"]

    hid = harness_info.get("harness_id", "?")
    hname = harness_info.get("harness_name", hid)

    ok, resp = False, None
    last_error = ""

    for proto in protocols:
        if proto == "http_callback":
            ok, resp = await http_callback_send(harness_info, task)
        elif proto == "websocket":
            ok, resp = await websocket_send(harness_info, task)
        elif proto == "file_poll":
            ok, resp = await file_poll_send(harness_info, task, poll_base)
        else:
            continue

        if ok:
            return True, proto, resp
        last_error = resp or f"unknown error on {proto}"

    # 所有协议都失败，最后强制 file_poll 兜底
    if "file_poll" not in protocols:
        ok2, resp2 = await file_poll_send(harness_info, task, poll_base)
        if ok2:
            return True, "file_poll(fallback)", resp2
        last_error = resp2 or last_error

    return False, "all_failed", last_error


# ═══════════════════════════════════════════════════════════════
# 举手判断解析
# ═══════════════════════════════════════════════════════════════

def parse_waker_response(raw_response: str) -> dict:
    """解析外部 Harness 的举手判断回复。

    支持 JSON 和纯文本两种格式。
    返回 {"hand_raised": bool, "capability_claim": str, "model_used": str}
    """
    raw = raw_response.strip() if raw_response else ""

    # 尝试 JSON 解析
    if raw.startswith("{"):
        try:
            data = json.loads(raw)
            return {
                "hand_raised": data.get("hand_raised", False),
                "capability_claim": data.get("capability_claim", data.get("response", ""))[:200],
                "model_used": data.get("model_used", ""),
            }
        except json.JSONDecodeError:
            pass

    # 纯文本模式：关键词判断
    lower = raw.lower()[:100]
    is_hand = any(kw in lower for kw in ["举手", "参与", "可以", "能", "我来", "raise", "join", "accept", "true"])
    is_reject = any(kw in lower for kw in ["拒绝", "pass", "不参与", "不", "false"])

    if is_reject and not is_hand:
        return {"hand_raised": False, "capability_claim": "", "model_used": ""}

    if is_hand:
        return {"hand_raised": True, "capability_claim": raw[:200], "model_used": ""}

    # 默认视为不举手
    return {"hand_raised": False, "capability_claim": "", "model_used": ""}
