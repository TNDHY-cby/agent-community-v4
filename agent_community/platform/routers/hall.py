# -*- coding: utf-8 -*-
"""大厅广播通知（`/api/hall/*`）—— 2026-10-23 老板要「大厅做广播通知之类的功能」。

口径（与全项目一致，别在这里发明新规矩）：

- **一条广播 = 一条大厅消息**：`from_agent=platform`、`to_agent=broadcast`、
  `payload.message_type=notice`、`payload.level=info|warn|urgent`。
  这样它天然会出现在大厅里、也会被 WebSocket 推给所有开着的页面 —— **不另造一套通知存储**
  （另一套就是"两份口径"，刷新后两份会打架）。
- **只追加**：通知没有"已读/撤回"（后端没有这条数据；要撤回就得有状态机，另起设计）。
- **失败要说话**：空文本、超长、非法级别一律 400 + 中文原因；读取端点只读。
- **不写盘**：与大厅消息同生命周期（进程内 `hall_messages`，上限 `hall_max`）。
  重启即清空 —— 这是"广播"而不是"公告板"；要长期留档应走审计/事件，不是这里。
"""
from __future__ import annotations

from fastapi import APIRouter, Request

router = APIRouter()

#: 一条广播最多多少字（够贴一段通知，又不会把大厅刷屏）
MAX_TEXT = 500
LEVELS = ("info", "warn", "urgent")
LEVEL_TEXT = {"info": "通知", "warn": "提醒", "urgent": "紧急"}


def _utc_json(status_code: int, payload: dict):
    from ..server import Utf8JSONResponse

    return Utf8JSONResponse(payload, status_code=status_code)


@router.post("/api/hall/notice")
async def hall_post_notice(request: Request):
    """发一条大厅广播（写）：`{text, level?}` → `{ok, notice:{id,text,level,at}}`。

    - `text` 必填，1~`MAX_TEXT` 字；
    - `level` 缺省 `info`（`warn` 提醒 / `urgent` 紧急，只影响界面样式与措辞，不改投递路径）。
    """
    from ..server import Message, MessageType, _append_hall, bcast_to_clients

    try:
        body = await request.json()
    except Exception:                                     # noqa: BLE001 — 坏 JSON 也要说人话
        body = {}
    body = body if isinstance(body, dict) else {}
    text = str(body.get("text") or "").strip()
    level = str(body.get("level") or "info").strip().lower()

    if not text:
        return _utc_json(400, {"ok": False, "error": "广播内容是空的 —— 写一句话再发"})
    if len(text) > MAX_TEXT:
        return _utc_json(400, {"ok": False,
                               "error": "广播最多 %d 字（现在是 %d 字）—— 长内容请发布任务或写进工作间"
                                        % (MAX_TEXT, len(text))})
    if level not in LEVELS:
        return _utc_json(400, {"ok": False,
                               "error": "级别只能是 info / warn / urgent（收到的是 %r）" % level})

    msg = Message(type=MessageType.SYSTEM, from_agent="platform", to_agent="broadcast",
                  content=text, payload={"message_type": "notice", "level": level})
    _append_hall(msg)
    try:
        await bcast_to_clients(msg)                       # 推给所有开着的页面（失败不影响已追加）
    except Exception as e:                                # noqa: BLE001 — 推送失败要记账，但广播本身已成立
        print("[hall-notice] 已追加，但 WebSocket 推送失败：%s: %s" % (type(e).__name__, e), flush=True)
    return {"ok": True, "notice": {"id": msg.id, "text": text, "level": level,
                                  "level_text": LEVEL_TEXT.get(level, "通知"), "at": msg.timestamp}}


@router.get("/api/hall/notices")
async def hall_list_notices(limit: int = 20):
    """读最近的广播（只读）：`{ok, notices:[{id,text,level,level_text,at}], total}`。

    从 `hall_messages` 里筛 `payload.message_type == "notice"`，**不另存一份**。
    """
    from ..server import hall_messages

    try:
        n = max(1, min(int(limit), 100))
    except Exception:                                     # noqa: BLE001 — 参数坏了就用默认
        n = 20
    rows = []
    for m in hall_messages:
        payload = getattr(m, "payload", None) or {}
        if payload.get("message_type") != "notice":
            continue
        lv = str(payload.get("level") or "info")
        rows.append({"id": getattr(m, "id", ""), "text": getattr(m, "content", ""),
                     "level": lv, "level_text": LEVEL_TEXT.get(lv, "通知"),
                     "at": getattr(m, "timestamp", "")})
    return {"ok": True, "notices": rows[-n:], "total": len(rows)}
