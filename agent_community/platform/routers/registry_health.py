# -*- coding: utf-8 -*-
"""名册健康端点（V85 §3）。"""
from __future__ import annotations

from fastapi import APIRouter, Request

from .. import registry_health as rh
from ..audit import audit_log as _audit_log

router = APIRouter()


@router.get("/api/registry/health")
async def registry_health():
    return rh.overview()


@router.post("/api/harness/{hid}/hide")
async def hide_harness(hid: str, request: Request):
    from ..server import Utf8JSONResponse

    try:
        body = await request.json()
    except Exception:                                        # noqa: BLE001
        body = {}
    body = body if isinstance(body, dict) else {}
    if "on" not in body:
        return Utf8JSONResponse({"error": "要传 on: true|false（隐藏/恢复）"}, status_code=400)
    out = rh.set_hidden(hid, bool(body["on"]), str(body.get("reason") or ""))
    _audit_log.record("harness.hide", actor="user", target=hid,
                      detail="%s %s" % ("隐藏" if body["on"] else "恢复", body.get("reason") or ""))
    return out


@router.post("/api/harness/{hid}/appeal")
async def appeal_harness(hid: str, request: Request):
    try:
        body = await request.json()
    except Exception:                                        # noqa: BLE001
        body = {}
    body = body if isinstance(body, dict) else {}
    out = rh.appeal(hid, str(body.get("note") or ""))
    _audit_log.record("harness.appeal", actor="user", target=hid,
                      detail="申诉恢复：%s" % (body.get("note") or "")[:120])
    return out
