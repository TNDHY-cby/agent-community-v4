# -*- coding: utf-8 -*-
"""platform/routers/relay：**中转**相关端点（V66 M1）——终端侧看状态、发内容、看收件箱。

- `GET  /api/relay/status`：连没连上中转、最后成功/错误、收件箱条数（界面用来显示"跨端可用吗"）；
- `POST /api/relay/send`：`{to_terminal, payload, kind?}` ⇒ 经中转发给另一台终端；
- `GET  /api/relay/inbox`：本地收件箱（`limit` 可选，默认 50）。

**全部本机来源**（转发是本地动作，别让外部直接调用）。
"""
from __future__ import annotations

from fastapi import APIRouter, Request

from ..audit import audit_log as _audit_log

router = APIRouter()


def _local_only(request: Request):
    from .system import origin_allowed
    from ..server import Utf8JSONResponse
    ok, why = origin_allowed(request)
    if not ok:
        return Utf8JSONResponse({"error": "来源不是本机", "detail": why}, status_code=403)
    return None


@router.get("/api/relay/status")
async def relay_status():
    """中转状态（**默认未配置也不报错**：本机一切照常，只是跨端不可用）。"""
    from .. import relay_client as client
    return {"success": True, **client.status()}


@router.post("/api/relay/send")
async def relay_send(request: Request):
    """经中转把内容发给另一台终端。"""
    deny = _local_only(request)
    if deny is not None:
        return deny
    from ..server import Utf8JSONResponse
    from .. import relay_client as client
    body = await request.json()
    to = str((body or {}).get("to_terminal") or "").strip()
    if not to:
        return Utf8JSONResponse({"error": "to_terminal 必填（对方终端 ID）",
                                 "how_to_fix": "填对方在它自己机器上配的 AC_HUB_TERMINAL_ID"}, status_code=400)
    if not client.is_configured():
        return Utf8JSONResponse({
            "error": "本机没有配置中转，发不出去",
            "how_to_fix": ("设 AC_HUB_URL（中转地址）与 AC_HUB_TERMINAL_ID（本机名字）后重启平台；"
                           "没配中转时本机功能一切照常，只是跨端不可用"),
        }, status_code=400)
    st, res = client.send(to, (body or {}).get("payload"),
                          kind=str((body or {}).get("kind") or "text"))
    _audit_log.record("relay.send", actor="user", target=to,
                      detail="HTTP %s ｜ %s" % (st, str((res or {}).get("note") or "")[:80]))
    if st == 200:
        return {"success": True, "to_terminal": to, **(res or {})}
    return Utf8JSONResponse({
        "success": False, "error": str((res or {}).get("error") or "发送失败（HTTP %s）" % st),
        "how_to_fix": "先 `GET /api/relay/status` 看中转连没连上；连不上就检查 AC_HUB_URL 与令牌",
    }, status_code=502 if st == 0 else st)


@router.get("/api/relay/inbox")
async def relay_inbox(limit: int = 50):
    """本地收件箱（**落盘在本机**；中转只负责转发，不存业务数据）。"""
    from .. import relay_client as client
    data = client.load_inbox()
    msgs = (data.get("messages") or [])
    n = max(1, min(200, int(limit or 50)))
    return {"success": True, "total": len(msgs), "cursor": int(data.get("cursor") or 0),
            "messages": msgs[-n:], "limit": n}
