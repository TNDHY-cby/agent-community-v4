# -*- coding: utf-8 -*-
"""HTTP 端点：**催办**（`platform/nudge`）。

| 方法 | 路径 | 干什么 |
|---|---|---|
| `GET` | `/api/workshop/{id}/nudge` | **只读**：现在该催谁、催什么（附阈值） |
| `POST` | `/api/workshop/{id}/nudge` | **真发**：写各自 `_notice.md` + 通知账记 `written`；`{dry_run: true}` 只回清单不写 |

**纪律**：同一条任务、同一天**只催一次**（用通知账的 `ref=nudge:…:<日期>` 去重），不刷屏；
催了也只记 `written`（平台不知道对方看没看）。
"""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from .. import nudge as _nd

router = APIRouter()


def _ws(workshop_id: str):
    from .. import server as _srv

    return (_srv.workshops or {}).get(workshop_id)


def _no_ws(workshop_id: str):
    return JSONResponse({"ok": False, "code": "NO_SUCH_WORKSHOP",
                         "error": "没有这个工作间：%s" % workshop_id,
                         "next": "GET /api/workshops 看现有的"}, status_code=404)


@router.get("/api/workshop/{workshop_id}/nudge")
async def nudge_plan(workshop_id: str):
    """**该催谁**（只读）：`issued/working` 超阈值催成员回执；`delivered` 超阈值催组长验收。"""
    if _ws(workshop_id) is None:
        return _no_ws(workshop_id)
    from .. import task_ledger as _tl
    from .. import v5_roster as _roster

    tasks = list(_tl.list_tasks(workshop_id) or [])
    try:
        members = list((_roster.roster(workshop_id) or {}).get("members") or [])
    except Exception:                                          # noqa: BLE001 — 名单读不出来只影响 slot 名
        members = []
    out = _nd.plan(tasks, members=members)
    out["workshop_id"] = workshop_id
    out["lines"] = _nd.summarize(out["items"])
    return out


@router.post("/api/workshop/{workshop_id}/nudge")
async def nudge_send(workshop_id: str, request: Request):
    """**真催**：写 `_notice.md`（成员 / 组长）+ 通知账 `written`；`{dry_run: true}` 只回清单。"""
    ws = _ws(workshop_id)
    if ws is None:
        return _no_ws(workshop_id)
    try:
        body = await request.json()
    except Exception:                                          # noqa: BLE001 — 空 body = 真发
        body = {}
    body = body if isinstance(body, dict) else {}
    dry = bool(body.get("dry_run"))
    from .. import nudge as _nd

    out = _nd.apply(workshop_id, ws, dry_run=dry)
    if not out.get("ok"):
        return JSONResponse(out, status_code=409)
    out["workshop_id"] = workshop_id
    out["next"] = ("GET …/nudge 复核；成员回执后状态会离开 issued/working"
                   if not dry else "去掉 dry_run 再发一次就是真催")
    return out
