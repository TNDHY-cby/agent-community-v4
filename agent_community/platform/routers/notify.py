# -*- coding: utf-8 -*-
"""通知渠道端点（V80 §B.2）：看配了什么 / 测一发。**不回显任何凭据明文。**"""
from __future__ import annotations

from fastapi import APIRouter, Request

from .. import notify as nf

router = APIRouter()


@router.get("/api/notify/channels")
async def channels():
    cfg = nf.configured()
    return {"channels": cfg, "supported": ["webhook", "smtp"],
            "note": "只做不需要申请第三方账号的渠道；飞书/钉钉/企微的自定义机器人本质也是 webhook，填 URL 即可"}


@router.post("/api/notify/test")
async def test_send(request: Request):
    from ..server import Utf8JSONResponse

    try:
        body = await request.json()
    except Exception:                                        # noqa: BLE001
        body = {}
    body = body if isinstance(body, dict) else {}
    ch = str(body.get("channel") or "webhook").strip().lower()
    text = str(body.get("text") or "合作社通知测试：如果你看到这条，说明通道是通的")

    if ch == "webhook":
        return nf.send_webhook(text)
    if ch == "smtp":
        return nf.send_mail(str(body.get("subject") or "合作社通知测试"), text)
    return Utf8JSONResponse({"error": "channel 只支持 webhook / smtp", "supported": ["webhook", "smtp"]},
                            status_code=400)
