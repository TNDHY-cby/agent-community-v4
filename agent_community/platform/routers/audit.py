"""platform/routers/audit：P2-1 轻量操作审计查询（V-10 自 retired 副本回收）。

只读端点：`GET /api/audit?limit=&event=&actor=`
数据源：`platform/audit.py` 的 `audit_log` 单例（纯内存 ring buffer + 惰性落盘 data/audit.json）。

拆包原则与其它 router 一致：APIRouter 由 server.py include_router 挂载；
audit_log 模块级单例在 `__main__` 与 `agent_community.platform.server` 两个副本下
都解析为同一个绝对模块名 `agent_community.platform.audit`，不存在双副本状态分裂。
"""
from __future__ import annotations

from fastapi import APIRouter

router = APIRouter()

from ..audit import audit_log as _audit_log  # noqa: E402  (router 之后导入，保持与其它 router 同风格)


@router.get("/api/audit")
async def audit_query(limit: str = "100", event: str = "", actor: str = ""):
    """P2-1 审计面板：查询最近操作审计事件（只读）。

    参数：limit（默认 100，最大 500）、event（如 harness.register / workshop.create）、
    actor（user / system / 具体 harness_id）。按时间倒序返回。

    limit 声明为 str 再手工夹紧：FastAPI 的 `limit: int` 会把 `?limit=abc` 直接判成
    422，与「非法值回落默认、不抛错」的输入健壮性约定不符（沿用 4.2.2 教训）。
    """
    try:
        _lim = max(1, min(500, int(limit)))
    except (TypeError, ValueError):
        _lim = 100
    return {"events": _audit_log.query(limit=_lim, event=event or None, actor=actor or None)}
