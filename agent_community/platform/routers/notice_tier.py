# -*- coding: utf-8 -*-
"""HTTP 端点：**通知投递账**（`V134` ⑧）与**档位对照表**（`V134` ⑨）。

| 方法 | 路径 | 干什么 |
|---|---|---|
| `GET` | `/api/workshop/{id}/notice-log` | 通知账：每条通知的目标/通道/结果/原因（`written`≠已送达） |
| `GET` | `/api/tier-matrix` | 档位 × 四件事（派活/通知/产物/验收）的**唯一对照表** |
| `GET` | `/api/workshop/{id}/tier-plan` | 这个工作间每个成员按档位该怎么处理（读矩阵，不另写一份） |

最后一条是为了让"矩阵"真的被用上：界面/组长能看到"这个成员在四件事上会怎样"，
而不是又去三处代码里各猜一套。
"""
from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from .. import notice_log as _nl
from .. import tier_matrix as _tm

router = APIRouter()


def _ws(workshop_id: str):
    from .. import server as _srv

    return (_srv.workshops or {}).get(workshop_id)


@router.get("/api/workshop/{workshop_id}/notice-log")
async def notice_log(workshop_id: str, limit: int = 100):
    """**通知账**（只读）：谁被通知了、走哪个通道、结果是什么。

    `written` = 只写进了通知文件（**对方看没看不计为已送达**）；`delivered` = 真投递且核过；
    `failed` = 试了没成（带真实原因）。
    """
    if _ws(workshop_id) is None:
        return JSONResponse({"ok": False, "code": "NO_SUCH_WORKSHOP",
                             "error": "没有这个工作间：%s" % workshop_id}, status_code=404)
    return _nl.log(workshop_id, limit=limit)


@router.get("/api/tier-matrix")
async def tier_matrix():
    """**档位 × 四件事**的唯一对照表（一处真相；新增档位必须填满整行）。"""
    out = _tm.matrix()
    out["gaps"] = _tm.gaps()
    return out


@router.get("/api/workshop/{workshop_id}/tier-plan")
async def tier_plan(workshop_id: str):
    """这个工作间每个成员**按档位**在四件事上会被怎么处理（读矩阵，不另写一份）。"""
    if _ws(workshop_id) is None:
        return JSONResponse({"ok": False, "code": "NO_SUCH_WORKSHOP",
                             "error": "没有这个工作间：%s" % workshop_id}, status_code=404)
    from .. import v5_roster as _roster

    try:
        members = list((_roster.roster(workshop_id) or {}).get("members") or [])
    except Exception:                                          # noqa: BLE001 — 名单读不出来就空着，别崩
        members = []
    rows = []
    for m in members:
        tier = str((m or {}).get("tier") or "legacy_unknown")
        rows.append({"member_seq": (m or {}).get("member_seq"),
                     "slot_dir": (m or {}).get("slot_dir") or "",
                     "tier": tier,
                     "label": (_tm.TIER_MATRIX.get(tier) or {}).get("label") or tier,
                     "axes": {a: {"code": _tm.code_of(tier, a),
                                  "meaning": _tm.meaning_of(_tm.code_of(tier, a))}
                              for a in _tm.AXES}})
    return {"ok": True, "workshop_id": workshop_id, "axes": list(_tm.AXES),
            "axis_labels": dict(_tm.AXIS_LABELS), "members": rows,
            "note": ("每一格的行为码都取自 `/api/tier-matrix`（一处真相）；"
                     "矩阵改了，这里跟着变 —— 别在别处再写一套档位判断")}
