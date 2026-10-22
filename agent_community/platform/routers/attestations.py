# -*- coding: utf-8 -*-
"""可验证附属记录端点（V83 §3）。"""
from __future__ import annotations

from fastapi import APIRouter, Request

from .. import attestations as at
from ..audit import audit_log as _audit_log

router = APIRouter()


def _info(subject_type: str, subject_id: str):
    if subject_type != "harness":
        return None
    try:
        from ..harness_adapter import harness_manager

        sess = harness_manager.sessions.get(subject_id)
        return getattr(sess, "info", None) if sess else None
    except Exception:                                        # noqa: BLE001 — 有意静默：取不到 harness 就退化为"没有可扫描信息"，扫描项会如实报"判不了"
        return None


@router.post("/api/attest/{subject_type}/{subject_id}")
async def create_attestation(subject_type: str, subject_id: str, request: Request):
    from ..server import Utf8JSONResponse

    try:
        body = await request.json()
    except Exception:                                        # noqa: BLE001
        body = {}
    body = body if isinstance(body, dict) else {}
    kind = str(body.get("kind") or "").strip().lower() or "audit"
    try:
        rec = at.append(subject_type, subject_id, kind, info=_info(subject_type, subject_id),
                        note=str(body.get("note") or ""), actor=str(body.get("by") or "user"),
                        att_id=str(body.get("att_id") or ""))
    except ValueError as e:
        return Utf8JSONResponse({"error": str(e), "supported": {"subject_type": list(at.SUBJECTS),
                                                                "kind": list(at.KINDS)}}, status_code=400)
    except FileExistsError as e:
        return Utf8JSONResponse({"error": str(e)}, status_code=409)
    _audit_log.record("attest.%s" % kind, actor="user", target=str(subject_id),
                      detail="新增附属记录 %s" % rec["att_id"])
    return rec


@router.get("/api/attest/{subject_type}/{subject_id}")
async def list_attestations(subject_type: str, subject_id: str):
    from ..server import Utf8JSONResponse

    if subject_type not in at.SUBJECTS:
        return Utf8JSONResponse({"error": "subject_type 只支持：%s" % "、".join(at.SUBJECTS),
                                 "supported": list(at.SUBJECTS)}, status_code=400)
    rows = at.list_of(subject_type, subject_id)
    return {"subject_type": subject_type, "subject_id": str(subject_id),
            "count": len(rows), "records": rows}


@router.get("/api/attest/{subject_type}/{subject_id}/{att_id}")
async def get_attestation(subject_type: str, subject_id: str, att_id: str):
    from ..server import Utf8JSONResponse

    rec = at.get_one(subject_type, subject_id, att_id)
    if rec is None:
        return Utf8JSONResponse({"error": "没有这条记录：%s（用列表端点看有哪些）" % att_id}, status_code=404)
    return rec


@router.post("/api/attest/verify")
async def verify_attestation(request: Request):
    from ..server import Utf8JSONResponse

    try:
        body = await request.json()
    except Exception:                                        # noqa: BLE001
        body = {}
    if not isinstance(body, dict) or "signature" not in body:
        return Utf8JSONResponse({"error": "要传一条带 signature 的记录（列表端点返回的整条即可）"},
                                status_code=400)
    return at.verify(body)
