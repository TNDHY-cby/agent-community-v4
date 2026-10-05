# -*- coding: utf-8 -*-
"""platform/routers/sessions：会话可见性（V-15 步骤8，设计稿 §12.2.1）。

「保留待复用」策略（拍板 #4）的必要成本：会话只增不减，**必须让人看得见**，
否则重蹈「桥内存里一堆无人知晓的会话」的覆辙。

本路由提供 `GET /api/sessions?harness_id=&workshop_id=`：
列出活跃会话（session_id / 归属 / created_at / last_seen / status），
供 harness 卡片与工作间详情页展示「当前 N 个活跃会话」。

安全边界：
- 只读查询，不触发任何状态变更（列表查询本身记账一条 session.list 审计）。
- 过滤参数均为可选；空过滤 = 全量活跃会话（仅排除 closed）。
"""
from __future__ import annotations

from fastapi import APIRouter

from ..audit import audit_log as _audit_log

router = APIRouter()


@router.get("/api/sessions")
async def list_sessions(harness_id: str = "", workshop_id: str = ""):
    """列出活跃会话（排除 closed）+ 全局状态计数。

    对应设计稿 §12.2.1 交付物「GET /api/sessions?harness_id=&workshop_id=」。
    """
    _audit_log.record(
        "session.list",
        actor="user",
        target=workshop_id or harness_id or "",
        detail=f"harness_id={harness_id or '-'} workshop_id={workshop_id or '-'}",
    )
    from ..session_registry import (
        ST_ACTIVE,
        ST_CLOSE_REQ,
        ST_IDLE,
        ST_STALE,
        session_registry,
        sweep_and_audit,
    )

    # V-17 触发时机①：可见性面就是"人来看"的时刻 —— 先惰性收敛到期的 close_requested，
    # 再报真相。否则界面会长期堆着"待确认关闭"，把真正活跃的会话淹没。
    # 纯规则、无轮询、无 LLM；失败不阻断（sweep_and_audit 自身吞异常）。
    sweep_and_audit()

    rows = session_registry.list(
        harness_id=harness_id, workshop_id=workshop_id, only_open=True
    )
    counts = session_registry.counts()
    return {
        "success": True,
        "count": len(rows),
        "active_count": counts.get(ST_ACTIVE, 0),
        "idle_count": counts.get(ST_IDLE, 0),
        "close_requested_count": counts.get(ST_CLOSE_REQ, 0),
        "stale_count": counts.get(ST_STALE, 0),
        "total_count": counts.get("total", 0),
        "sessions": rows,
    }
