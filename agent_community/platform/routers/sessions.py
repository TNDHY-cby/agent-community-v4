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


def _workshop_cost(workshop_id: str) -> dict:
    """该工作间**能可靠归到它**的任务累计用量（`Task.usage`，V75 P1-1 落地）。

    ⚠️ 诚实口径（实测约束）：`Task` 没有 `workshop_id`；工作间只有 `origin_task_id` 与
    `task_tree` 里可能登记的任务 id。**归不到就不编** —— 返回 `available: False` 并说清原因，
    顺便指向全局记账端点 `/api/ai/usage`。
    """
    from ..state import tasks as _tasks, workshops as _works

    ws = _works.get(workshop_id) if workshop_id else None
    ids: set = set()
    if ws is not None:
        origin = str(getattr(ws, "origin_task_id", "") or "")
        if origin:
            ids.add(origin)
        for node in (getattr(ws, "task_tree", None) or []):
            if isinstance(node, dict):
                tid = str(node.get("task_id") or node.get("origin_task_id") or "")
                if tid:
                    ids.add(tid)

    total = {"cost": 0.0, "total_tokens": 0, "cached_tokens": 0, "rounds": 0, "tasks_counted": 0}
    for tid in ids:
        task = _tasks.get(tid)
        if task is None:
            continue
        u = getattr(task, "usage", None) or {}
        if not u:
            continue
        total["tasks_counted"] += 1
        total["cost"] += float(u.get("cost", 0) or 0)
        total["total_tokens"] += int(u.get("total_tokens", 0) or 0)
        total["cached_tokens"] += int(u.get("cached_tokens", 0) or 0)
        total["rounds"] += int(u.get("rounds", 0) or 0)

    if not ids:
        return {"available": False,
                "why": "这条会话没有关联的任务 id（工作间是人工建的、或任务树没登记），"
                       "平台不按会话切分用量，所以不编一个数",
                "how_to_get": "全局用量看 GET /api/ai/usage；任务级用量看 GET /api/task/{id} 的 usage"}

    total["cost"] = round(total["cost"], 6)
    total["available"] = True
    total["basis"] = ("按该工作间可归属任务（origin_task_id + 任务树登记的 id）的累计用量加总"
                      if total["tasks_counted"] else
                      "这些任务还没有用量记录（桥没上报，或还没跑过）")
    return total


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
    # V75 P1-5：每条会话带上成本估计（同一工作间共享一份，按工作间算）
    _cost_cache: dict = {}
    for _row in rows:
        _wid = str(_row.get("workshop_id") or "")
        if _wid not in _cost_cache:
            _cost_cache[_wid] = _workshop_cost(_wid)
        _row["cost_estimate"] = _cost_cache[_wid]

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
