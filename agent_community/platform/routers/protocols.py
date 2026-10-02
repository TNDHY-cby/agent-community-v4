# -*- coding: utf-8 -*-
"""platform/routers/protocols：协议目录 API（V-11）。

GET /api/protocols —— 返回平台对外暴露的全部接入方式，含运行时端点坐标、状态、适用场景。
供前端渲染（register.html 协议面板 / index.html 设置页）、外端 agent 自选、A2A supportedInterfaces 派生。
"""
from __future__ import annotations

from fastapi import APIRouter, Request

from ..protocol_catalog import get_protocol_catalog

router = APIRouter()


@router.get("/api/protocols")
async def list_protocols(request: Request):
    """返回协议目录（单一事实源）。

    每项含：id / name / direction / kind / status / best_for / example / requires / endpoint
    status: available（可用）| partial（部分实现）| not_implemented（规划中）
    """
    host = request.headers.get("host", "127.0.0.1").split(":")[0]
    catalog = get_protocol_catalog(host=host)
    return {"protocols": catalog, "total": len(catalog)}
