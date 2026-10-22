# -*- coding: utf-8 -*-
"""凭据体检端点（V86 §4）。

口径：**绝不回显明文**（响应里只有位置、指纹、强度、原因）。
`scan` 是写请求（会更新账本），所以：① 进前台白名单 ② 不许页面加载时自动调
③ **永不**放进外部只读档（`EXTERNAL_READONLY_DENY`）。
"""
from __future__ import annotations

from fastapi import APIRouter

from .. import credentials as cred
from ..audit import audit_log as _audit_log

router = APIRouter()


@router.get("/api/credentials")
async def credentials_overview():
    return cred.overview()


@router.post("/api/credentials/scan")
async def credentials_scan():
    out = cred.scan_and_record()
    s = out.get("summary") or {}
    _audit_log.record("credentials.scan", actor="user", target="credentials",
                      detail="体检 %d 处（弱值 %d / 桥产物明文 %d / 跳过 %d）"
                             % (s.get("found", 0), s.get("weak", 0),
                                s.get("plaintext_in_bridge", 0), s.get("skipped", 0)))
    return out
