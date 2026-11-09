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

@router.get("/api/workshop/{workshop_id}/squads/{squad_id}/record")
async def squad_record(workshop_id: str, squad_id: str):
    """**分队履历**（`V140` §2.1 / 队列 47-1）：只读，从现有任务账本聚合，不新建表。

    只统计**已结束**的任务（通过/拒收），进行中的不进分母（否则越忙的队越难看）；
    `total < 3` 会带 `insufficient: true` 提醒"别据此判断"；**不排分、不排名**。
    `AC_SQUAD_HISTORY=0` 时返回空履历并说明是开关关掉了。
    """
    from fastapi.responses import JSONResponse

    from .. import squad_record as _sr
    from .. import task_ledger as _tl

    ws = _ws(workshop_id)
    if ws is None:
        return _no_ws(workshop_id)
    sq = _squads.find(list(getattr(ws, "squads", None) or []), squad_id)
    # ⚠️ `squads.find()` 找不到返回 **`{}`**（不是 None）⇒ 必须用 `not sq`，
    #   否则不存在的分队会返回 `ok:true, squad_id:null`（"看起来成功"的死状态，铁律 6 禁止）。
    if not sq:
        return JSONResponse({"ok": False, "code": "NO_SUCH_SQUAD", "retryable": False,
                             "error": "没有这个分队：%s" % squad_id,
                             "next": "GET …/squads 看有哪些分队"}, status_code=404)
    try:
        tasks = list(_tl.list_tasks(workshop_id) or [])
    except Exception as e:                                     # noqa: BLE001 — 账本读不出来就如实说，不假装"零任务"
        return JSONResponse({"ok": False, "code": "LEDGER_UNREADABLE", "retryable": True,
                             "error": "任务账本读不出来，没法算履历：%s: %s" % (type(e).__name__, e),
                             "next": "稍后重试；持续失败看 data/task_ledger.json 是否损坏"},
                            status_code=503)
    return _sr.build(sq, _members(workshop_id), tasks, workshop_id=workshop_id)


@router.post("/api/workshop/{workshop_id}/squads/{squad_id}/clone")
async def squad_clone(workshop_id: str, squad_id: str, request: Request):
    """**分队模板化**（`V140` §2.2 / 队列 47-2）：复制方向/目标/成员构成，**不复制任务历史**。

    body：`{name?, direction?, goal?, member_seqs?, copy_experience?}`
    - `copy_experience=true` ⇒ 返回**经验索引指针**（memories 的路径），**不含正文**
      —— 这就是老板要的"清其他任务上下文影响"；
    - `target_workshop_id` 只允许**同一工作间**（跨间迁移属另一档，明确拒并说清）。
    - `AC_SQUAD_TEMPLATE=0` ⇒ 404。
    """
    from fastapi.responses import JSONResponse

    from .. import env_spec as _env
    from .. import squad_record as _sr

    if not _sr.template_enabled():
        return JSONResponse({"ok": False, "code": "SQUAD_TEMPLATE_DISABLED", "retryable": False,
                             "error": "分队模板化已关闭（AC_SQUAD_TEMPLATE=0）",
                             "next": "要建副本把 AC_SQUAD_TEMPLATE 设回 1 并重启服务"}, status_code=404)
    ws = _ws(workshop_id)
    if ws is None:
        return _no_ws(workshop_id)
    try:
        body = await request.json()
    except Exception:                                          # noqa: BLE001 — 空 body = 全按源队复制
        body = {}
    body = body if isinstance(body, dict) else {}

    tgt_ws = str(body.get("target_workshop_id") or "").strip()
    if tgt_ws and tgt_ws != workshop_id:
        return JSONResponse({"ok": False, "code": "CROSS_WORKSHOP_MIGRATION_NOT_SUPPORTED",
                             "retryable": False,
                             "error": "跨工作间复制分队**没做**（涉及成员接管，属另一档）",
                             "next": "只在同一个工作间内建副本：去掉 target_workshop_id"}, status_code=400)

    squads = list(getattr(ws, "squads", None) or [])
    src = _squads.find(squads, squad_id)
    if not src:                                                # 同上：find 返回 {} 不是 None
        return JSONResponse({"ok": False, "code": "NO_SUCH_SQUAD", "retryable": False,
                             "error": "没有这个分队：%s" % squad_id,
                             "next": "GET …/squads 看有哪些分队"}, status_code=404)

    member_seqs = body.get("member_seqs")
    if member_seqs is not None:
        member_seqs = [int(x) for x in (member_seqs or []) if str(x).strip().isdigit()]
    payload = _sr.clone_payload(src, name=str(body.get("name") or ""),
                                direction=str(body.get("direction") if body.get("direction") is not None else ""),
                                goal=str(body.get("goal") if body.get("goal") is not None else ""),
                                member_seqs=member_seqs)
    payload["squad_id"] = _squads._auto_id(payload["name"]) if hasattr(_squads, "_auto_id") else ""
    if not payload["squad_id"]:
        payload["squad_id"] = "squad-clone-%d" % int(__import__("time").time())
    squads = [c for c in squads if str((c or {}).get("squad_id")) != payload["squad_id"]]
    squads.append(payload)
    ws.squads = squads

    out = {"ok": True, "squad": {k: payload.get(k) for k in
                                 ("squad_id", "name", "direction", "goal", "member_seqs", "copied_from")},
           "copied": ["direction", "goal", "成员构成"],
           "not_copied": ["任务历史（新队从零记履历）", "账本条目", "成员的会话上下文"],
           "note": "新分队**没有**旧队的任务历史：履历从 0 开始算（这是故意的）。"}
    if bool(body.get("copy_experience")):
        try:
            root = str(_env.get("AC_MIND_ROOT") or "")
        except Exception:                                      # noqa: BLE001 — 没配心智根就如实说"没指针可给"
            root = ""
        if not root:
            from pathlib import Path as _P

            from ..env_spec import data_dir as _dd

            root = str(_P(_dd()) / "minds")
        out["experience_index"] = _sr.experience_index(root)
    return out
