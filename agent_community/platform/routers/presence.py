# -*- coding: utf-8 -*-
"""HTTP 端点：**成员在不在动**（`platform/presence`，只读）。

| 方法 | 路径 | 干什么 |
|---|---|---|
| `GET` | `/api/workshop/{id}/presence` | 每个成员的最后痕迹（任务/产物）与是否失联 + 每个判定的人话理由 |

**口径**：只有**有在途任务**（`issued`/`working`）的成员才可能被判失联；空闲成员不算；
阈值 `AC_PRESENCE_SILENT_MIN`（默认 120 分钟）。**不看窗口画面**（那是监控室）。
"""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from .. import presence as _pr

router = APIRouter()


def _ws(workshop_id: str):
    from .. import server as _srv

    return (_srv.workshops or {}).get(workshop_id)


@router.get("/api/workshop/{workshop_id}/presence")
async def workshop_presence(workshop_id: str):
    if _ws(workshop_id) is None:
        return JSONResponse({"ok": False, "code": "NO_SUCH_WORKSHOP",
                             "error": "没有这个工作间：%s" % workshop_id}, status_code=404)
    from .. import artifact_inbox as _inbox
    from .. import task_ledger as _tl
    from .. import v5_roster as _roster

    tasks = list(_tl.list_tasks(workshop_id) or [])
    try:
        members = list((_roster.roster(workshop_id) or {}).get("members") or [])
    except Exception:                                          # noqa: BLE001 — 名单读不出来就空着（不猜）
        members = []
    try:
        events = [e for e in _inbox.events(limit=500)
                  if str((e or {}).get("workshop_id") or "") == workshop_id]
    except Exception:                                          # noqa: BLE001 — 事件读不出来只影响"产物痕迹"
        events = []
    out = _pr.plan(members, tasks, events)
    out["workshop_id"] = workshop_id
    return out

@router.get("/api/workshop/{workshop_id}/lead-todo")
async def workshop_lead_todo(workshop_id: str):
    """**组长今天该管谁**（`V134` 催办 + `V131` 痕迹可见性的合成视图，只读）。

    三类事：`silent`（有人有在途任务却不动）、`no_review`（产物到了等验收）、
    `no_receipt`（任务派出去了没回执）。只分「先处理 / 可以等等」两档，**不排分**。
    """
    if _ws(workshop_id) is None:
        return JSONResponse({"ok": False, "code": "NO_SUCH_WORKSHOP",
                             "error": "没有这个工作间：%s" % workshop_id}, status_code=404)
    from .. import artifact_inbox as _inbox
    from .. import lead_todo as _lt
    from .. import task_ledger as _tl
    from .. import v5_roster as _roster

    tasks = list(_tl.list_tasks(workshop_id) or [])
    try:
        members = list((_roster.roster(workshop_id) or {}).get("members") or [])
    except Exception:                                          # noqa: BLE001 — 名单读不出来就空着
        members = []
    try:
        events = [e for e in _inbox.events(limit=500)
                  if str((e or {}).get("workshop_id") or "") == workshop_id]
    except Exception:                                          # noqa: BLE001 — 事件读不出来只影响"产物痕迹"
        events = []
    return _lt.build(members, tasks, events, workshop_id)

@router.get("/api/workshop/{workshop_id}/lead-digest")
async def workshop_lead_digest(workshop_id: str):
    """**组长日报（只算不写）**：把"该管谁"压成一段人话返回。"""
    data = _todo_data(workshop_id)
    if data is None:
        return JSONResponse({"ok": False, "code": "NO_SUCH_WORKSHOP",
                             "error": "没有这个工作间：%s" % workshop_id}, status_code=404)
    from .. import lead_digest as _ld

    out = _ld.build(workshop_id, _lt_build(workshop_id, data))
    out["workshop_id"] = workshop_id
    return out


@router.post("/api/workshop/{workshop_id}/lead-digest")
async def workshop_lead_digest_send(workshop_id: str, request: Request):
    """**真发日报**：写进组长 `_lead/_notice.md` + 通知账记 `written`；**同一天只发一次**。"""
    ws = _ws(workshop_id)
    if ws is None:
        return JSONResponse({"ok": False, "code": "NO_SUCH_WORKSHOP",
                             "error": "没有这个工作间：%s" % workshop_id}, status_code=404)
    try:
        body = await request.json()
    except Exception:                                          # noqa: BLE001 — 空 body = 真发
        body = {}
    body = body if isinstance(body, dict) else {}
    from .. import lead_digest as _ld

    data = _todo_data(workshop_id) or {}
    out = _ld.send(workshop_id, ws, data, dry_run=bool(body.get("dry_run")))
    if out.get("code") == "NO_WORKSPACE_DIR":
        return JSONResponse(out, status_code=409)
    out["workshop_id"] = workshop_id
    return out

@router.get("/api/workshop/{workshop_id}/members/{member_seq}/my-todo")
async def member_my_todo(workshop_id: str, member_seq: int):
    """**成员自己该干什么**（只读，一条查询就够）：我手上的任务 + 每条我该做什么 + 给我的通知。"""
    if _ws(workshop_id) is None:
        return JSONResponse({"ok": False, "code": "NO_SUCH_WORKSHOP",
                             "error": "没有这个工作间：%s" % workshop_id}, status_code=404)
    from .. import member_todo as _mt
    from .. import notice_log as _nl
    from .. import task_ledger as _tl
    from .. import v5_roster as _roster

    try:
        members = list((_roster.roster(workshop_id) or {}).get("members") or [])
    except Exception:                                          # noqa: BLE001 — 名单读不出来就空着
        members = []
    me = next((m for m in members if int((m or {}).get("member_seq") or 0) == int(member_seq)), None)
    if me is None:
        return JSONResponse({"ok": False, "code": "NO_SUCH_MEMBER",
                             "error": "这个工作间名单里没有成员 %d" % int(member_seq),
                             "next": "GET …/roster 看有哪些；成员要先进名单才会有活"},
                            status_code=404)
    tasks = list(_tl.list_tasks(workshop_id) or [])
    try:
        notices = list((_nl.log(workshop_id, limit=200).get("items") or []))
    except Exception:                                          # noqa: BLE001 — 通知账读不出来只影响通知那块
        notices = []
    return _mt.build(me, tasks, notices, workshop_id)

def _todo_data(workshop_id: str):
    """取算待办要用的三份数据；工作间不在 ⇒ None（拉取逻辑在 `lead_todo.collect`，一处真相）。"""
    if _ws(workshop_id) is None:
        return None
    from .. import lead_todo as _lt

    return _lt.collect(workshop_id)


def _lt_build(workshop_id: str, data: dict) -> dict:
    from .. import lead_todo as _lt

    return _lt.build(data.get("members") or [], data.get("tasks") or [],
                     data.get("events") or [], workshop_id)

@router.get("/api/lead-todo-all")
async def lead_todo_all():
    """**跨工作间**的"今天该管谁"（大厅首页用；只读）。

    `?status=active` 只算未关闭的工作间（默认全部）。收件事件**只读一次**再按间分组，
    避免 N 个间读 N 遍盘。
    """
    from fastapi import Query  # noqa: F401 — 仅为类型清晰，FastAPI 已处理

    from .. import artifact_inbox as _inbox
    from .. import lead_todo as _lt
    from .. import server as _srv

    wss = dict(_srv.workshops or {})
    try:
        events = list(_inbox.events(limit=1000))
    except Exception:                                          # noqa: BLE001 — 事件读不出来只影响"产物痕迹"
        events = []
    by_ws: dict = {}
    for e in events:
        by_ws.setdefault(str((e or {}).get("workshop_id") or ""), []).append(e)
    entries = []
    for wid, ws in wss.items():
        entries.append({"workshop_id": str(wid), "name": str(getattr(ws, "name", "") or ""),
                        "status": str(getattr(ws, "status", "") or ""),
                        "members": [], "tasks": [], "events": by_ws.get(str(wid), []),
                        "ws": ws})
    # 名单与任务按间取（顺序读，量级小；读不到就空着，不猜）
    from .. import task_ledger as _tl
    from .. import v5_roster as _roster

    for e in entries:
        wid = e["workshop_id"]
        try:
            e["tasks"] = list(_tl.list_tasks(wid) or [])
        except Exception:                                      # noqa: BLE001 — 读不到就空着
            e["tasks"] = []
        try:
            e["members"] = list((_roster.roster(wid) or {}).get("members") or [])
        except Exception:                                      # noqa: BLE001 — 读不到就空着
            e["members"] = []
    out = _lt.build_all(entries)
    out["generated_at"] = __import__("time").strftime("%Y-%m-%dT%H:%M:%S")
    return out
