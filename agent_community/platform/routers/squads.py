# -*- coding: utf-8 -*-
"""HTTP 端点：**分队（模块化工作间）**（`V138` §二 第二件）。

| 方法 | 路径 | 干什么 |
|---|---|---|
| `GET` | `/api/workshop/{id}/squads` | **三级视图**：工作间 → 分队 → 工位（含每个工位的任务数/未结数） |
| `POST` | `/api/workshop/{id}/squads` | 建一个**分队**（`{name, direction?, goal?, member_seqs?, squad_id?}`） |
| `GET` | `/api/workshop/{id}/squads/{squad_id}` | 看一个**分队** |
| `POST` | `/api/workshop/{id}/squads/{squad_id}` | 改一个**分队**（成员/状态/目标/方向） |

**纪律**（写进每个响应，别让调用方以为这里能"批量放行"）：
- 账本仍按 `workshop_id + task_seq`（分队 只是标签）；
- 验收仍**逐条**（没有"整 分队 一键通过"）；
- 分队 **没有自己的组长**（拍板唯一，见 `V135`）。
"""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from .. import squads as _squads
from .. import v5_roster as _roster

router = APIRouter()


def _ws(workshop_id: str):
    from .. import server as _srv

    return (_srv.workshops or {}).get(workshop_id)


def _no_ws(workshop_id: str):
    return JSONResponse({"ok": False, "code": "NO_SUCH_WORKSHOP",
                         "error": "没有这个工作间：%s" % workshop_id,
                         "next": "GET /api/workshops 看现有的"}, status_code=404)


def _lead_seq(workshop_id: str) -> int:
    """当前**组长**（`slot_dir == "_lead"`）的 member_seq；没有就 0。

    老板 2026-11-07 定词：「**组长不参加分队，他是组长**」⇒ 分队（原分队）里不许编进组长。
    前端做了显示层过滤，但**真正的门在数据层**（这里）。
    """
    for m in _members(workshop_id):
        if str((m or {}).get("slot_dir") or "").strip().strip("/") == "_lead":
            return int((m or {}).get("member_seq") or 0)
    return 0


def _reject_lead_in_squad(workshop_id: str, body: dict):
    """`member_seqs` 里含组长 ⇒ 返回 400 响应；否则 `None`。"""
    seqs = body.get("member_seqs")
    if not seqs:
        return None
    lead = _lead_seq(workshop_id)
    if not lead:
        return None
    try:
        bad = lead in [int(x) for x in seqs]
    except (TypeError, ValueError):
        return None
    if not bad:
        return None
    return JSONResponse({"ok": False, "code": "LEAD_NOT_IN_SQUAD",
                         "error": ("组长不参加分队：成员 %d 是 `_lead`，不能编进分队 —— "
                                   "他负责拍板与验收，编进分队会让「谁拍板」说不清" % lead),
                         "next": ("把 %d 从 member_seqs 里去掉；分队里只放干活的成员。"
                                  "（前端也会把它藏起来，但数据层同样不许落）" % lead)},
                        status_code=400)


def _members(workshop_id: str) -> list:
    try:
        return list((_roster.roster(workshop_id) or {}).get("members") or [])
    except Exception:                                          # noqa: BLE001 — 名单读不出来按空处理（只影响视图）
        return []


@router.get("/api/workshop/{workshop_id}/squads")
async def cells_view(workshop_id: str):
    """三级视图（只读）：`workshop → squads[] → members[]`，附每个工位的任务数。"""
    ws = _ws(workshop_id)
    if ws is None:
        return _no_ws(workshop_id)
    from .. import task_ledger as _tl

    tasks = []
    try:
        tasks = list(_tl.list_tasks(workshop_id) or [])
    except Exception:                                          # noqa: BLE001 — 账本读不出来时只影响计数
        tasks = []
    out = _squads.view(list(getattr(ws, "squads", None) or []), _members(workshop_id), tasks)
    out["workshop_id"] = workshop_id
    return out


@router.post("/api/workshop/{workshop_id}/squads")
async def cell_create(workshop_id: str, request: Request):
    """建 分队。**同 id 直接拒**（不静默覆盖）；超过上限提示该拆工作间而不是继续加**分队**。"""
    ws = _ws(workshop_id)
    if ws is None:
        return _no_ws(workshop_id)
    try:
        body = await request.json()
    except Exception:                                          # noqa: BLE001 — 空 body 会被下面拒
        body = {}
    body = body if isinstance(body, dict) else {}
    _bad = _reject_lead_in_squad(workshop_id, body)
    if _bad is not None:
        return _bad
    cur = list(getattr(ws, "squads", None) or [])
    new, err = _squads.new_squad(cur, name=str(body.get("name") or ""),
                               direction=str(body.get("direction") or ""),
                               goal=str(body.get("goal") or ""),
                               member_seqs=body.get("member_seqs") or [],
                               squad_id=str(body.get("squad_id") or ""))
    if err:
        return JSONResponse({"ok": False, "code": "CELL_INVALID", "error": err,
                             "next": "改一下再发；上限 %d 个 分队" % _squads.MAX_SQUADS},
                            status_code=400)
    ws.squads = new
    from ..server import save_state

    save_state()
    cid = str(new[-1]["squad_id"])
    return {"ok": True, "workshop_id": workshop_id, "cell": new[-1],
            "note": "**分队**只是**分组**：账本仍按 workshop_id+task_seq，验收仍逐条，**分队**没有自己的组长",
            "next": "对 分队 派活：POST …/dispatch 带 {\"squad_id\": \"%s\", ...}" % cid}


@router.get("/api/workshop/{workshop_id}/squads/{squad_id}")
async def cell_get(workshop_id: str, squad_id: str):
    ws = _ws(workshop_id)
    if ws is None:
        return _no_ws(workshop_id)
    c = _squads.find(list(getattr(ws, "squads", None) or []), squad_id)
    if not c:
        return JSONResponse({"ok": False, "code": "NO_SUCH_CELL",
                             "error": "没有这个**分队**：%s" % squad_id,
                             "next": "GET …/squads 看现有的"}, status_code=404)
    return {"ok": True, "workshop_id": workshop_id, "cell": c,
            "squad_members_of": _squads.squad_members_of(list(getattr(ws, "squads", None) or []), squad_id)}


@router.post("/api/workshop/{workshop_id}/squads/{squad_id}")
async def cell_update(workshop_id: str, squad_id: str, request: Request):
    ws = _ws(workshop_id)
    if ws is None:
        return _no_ws(workshop_id)
    try:
        body = await request.json()
    except Exception:                                          # noqa: BLE001
        body = {}
    body = body if isinstance(body, dict) else {}
    _bad = _reject_lead_in_squad(workshop_id, body)
    if _bad is not None:
        return _bad
    new, err = _squads.update_squad(list(getattr(ws, "squads", None) or []), squad_id, body)
    if err:
        return JSONResponse({"ok": False, "code": "CELL_INVALID", "error": err}, status_code=400)
    ws.squads = new
    from ..server import save_state

    save_state()
    return {"ok": True, "workshop_id": workshop_id, "cell": _squads.find(new, squad_id),
            "note": "验收仍逐条；**分队**没有自己的组长"}

@router.get("/api/workshop/{workshop_id}/split-advice")
async def split_advice(workshop_id: str):
    """**该不该拆成两个工作间**（`V138` §〇 三条件）—— 只给事实与理由，**不推荐**。

    三条都成立才 `consider_split`；否则 `keep`（用 分队 分组就够了）。
    """
    ws = _ws(workshop_id)
    if ws is None:
        return _no_ws(workshop_id)
    from .. import task_ledger as _tl

    try:
        tasks = list(_tl.list_tasks(workshop_id) or [])
    except Exception:                                          # noqa: BLE001 — 账本读不出来只影响建议精度
        tasks = []
    out = _squads.split_advice(list(getattr(ws, "squads", None) or []), _members(workshop_id), tasks)
    out["workshop_id"] = workshop_id
    return out
