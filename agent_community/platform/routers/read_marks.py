# -*- coding: utf-8 -*-
"""`routers/read_marks`：大厅「未读回报」的**后端水位**（跨设备）。

对外的两条（与前端 `FE-33` 约定一致）：

```
GET  /api/workshop/{ws_id}/read        读水位 + 未读条数（只读）
POST /api/workshop/{ws_id}/read        推进水位（默认推进到当前最大 seq；只前进不后退）
```

**为什么放后端**：`FE-33` 第一版把水位放 `localStorage`，换浏览器/换设备就不同步、清缓存就重置
（它自己在 §5 如实写了）。这里给一个跨设备的数，前端只需把 `localStorage` 换成这两个端点。

**未读口径一处真相**：在 `platform/read_marks.unread_of()` —— 前端别自己再算一遍。
"""
from __future__ import annotations

from fastapi import APIRouter, Request

from ..state import workshops

router = APIRouter(tags=["read-marks"])


def _ws(ws_id: str):
    return workshops.get(str(ws_id or ""))


def _no_ws(ws_id: str):
    from fastapi.responses import JSONResponse

    return JSONResponse({"ok": False, "code": "NO_SUCH_WORKSHOP",
                         "error": "没有这个工作间：%s" % ws_id,
                         "next": "去大厅刷新一下；工作间被删了就换一个"}, status_code=404)


@router.get("/api/workshop/{ws_id}/read")
async def read_mark_get(ws_id: str):
    """读水位 + 未读（**只读**，不改任何状态）。

    `initialized=false` 表示这个工作间还没被读过 —— 前端可按自己的"基线"策略处理
    （`FE-33` §1：首次出现时用 `max_seq` 初始化，免得一开局全红）。**平台不替用户决定**。
    """
    from .. import read_marks as _rm

    ws = _ws(ws_id)
    if ws is None:
        return _no_ws(ws_id)
    st = _rm.get(ws_id)
    u = _rm.unread_of(getattr(ws, "discussion", None) or [], st["seq"])
    return {"ok": True, "workshop_id": ws_id, "seq": st["seq"],
            "initialized": st["initialized"], "at": st["at"],
            "unread": u["unread"], "max_seq": u["max_seq"],
            "non_user_total": u["non_user_total"],
            "first_unread_seq": u["first_unread_seq"],
            "scope": "单用户（本机这台平台）；跨设备同步，跨账号不做"}


@router.post("/api/workshop/{ws_id}/read")
async def read_mark_post(ws_id: str, request: Request):
    """推进水位。body 可选 `{seq: N}`（不给就推进到**当前最大 seq**）；`{reset: true}` 回到"没看过"。

    只前进不后退（要回退请显式 `reset`）—— 免得"翻一下旧消息"把提醒吃没了。
    """
    from fastapi.responses import JSONResponse

    from .. import read_marks as _rm

    ws = _ws(ws_id)
    if ws is None:
        return _no_ws(ws_id)
    try:
        body = await request.json()
    except Exception:                                          # noqa: BLE001 — 没 body 就是"推到最新"
        body = {}
    body = body if isinstance(body, dict) else {}

    if body.get("reset"):
        out = _rm.reset(ws_id)
    else:
        want = body.get("seq")
        if want is None:
            _u = _rm.unread_of(getattr(ws, "discussion", None) or [], _rm.UNREAD_INITIAL)
            want = _u["max_seq"]
        out = _rm.mark(ws_id, want)
        if not out.get("ok"):
            return JSONResponse(out, status_code=400)

    st = _rm.get(ws_id)
    u = _rm.unread_of(getattr(ws, "discussion", None) or [], st["seq"])
    return {**out, "ok": True, "workshop_id": ws_id,
            "unread": u["unread"], "max_seq": u["max_seq"],
            "at": st["at"]}
