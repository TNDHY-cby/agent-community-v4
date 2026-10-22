# -*- coding: utf-8 -*-
"""可发现端点（V84 §3）：`/.well-known/` 清单 + 单卡 + 开关。"""
from __future__ import annotations

from fastapi import APIRouter, Request

from .. import discovery as dv
from ..audit import audit_log as _audit_log

router = APIRouter()


def _all():
    from ..state import workshops

    return list(workshops.values())


@router.get("/.well-known/workshops.json")
async def well_known_workshops():
    return dv.listing(_all())


@router.get("/.well-known/workshop/{wid}.json")
async def well_known_workshop(wid: str):
    from ..server import Utf8JSONResponse
    from ..state import workshops

    ws = workshops.get(wid)
    if ws is None:
        return Utf8JSONResponse({"error": "没有这个工作间：%s" % wid}, status_code=404)
    if not bool(getattr(ws, "discoverable", False)):
        return Utf8JSONResponse({"error": "这个工作间没有开启对外发现（默认关）",
                                 "how_to_fix": "工作间主人可 POST /api/workshops/{wid}/discoverable {on:true} 打开"},
                                status_code=404)
    return dv.card(ws)


@router.post("/api/workshops/{wid}/discoverable")
async def set_discoverable(wid: str, request: Request):
    from ..server import Utf8JSONResponse, save_state
    from ..state import workshops

    ws = workshops.get(wid)
    if ws is None:
        return Utf8JSONResponse({"error": "没有这个工作间：%s" % wid}, status_code=404)
    try:
        body = await request.json()
    except Exception:                                        # noqa: BLE001
        body = {}
    body = body if isinstance(body, dict) else {}
    if "on" not in body:
        return Utf8JSONResponse({"error": "要传 on: true|false"}, status_code=400)
    ws.discoverable = bool(body["on"])
    _audit_log.record("workshop.discoverable", actor="user", target=wid,
                      detail="对外发现 %s" % ("开" if ws.discoverable else "关"))
    save_state()
    return {"success": True, "workshop_id": wid, "discoverable": ws.discoverable,
            "where": "/.well-known/workshop/%s.json" % wid}
