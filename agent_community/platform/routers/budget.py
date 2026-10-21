# -*- coding: utf-8 -*-
"""执行预算端点（V77 第一半）：看 / 设 / 判定。"""
from __future__ import annotations

from fastapi import APIRouter, Request

from .. import approval as ap
from .. import run_budget as rb
from ..audit import audit_log as _audit_log

router = APIRouter()


def _task(task_id: str):
    from ..state import tasks

    return tasks.get(task_id)


@router.get("/api/tasks/{task_id}/budget")
async def get_budget(task_id: str):
    from ..server import Utf8JSONResponse

    task = _task(task_id)
    if task is None:
        return Utf8JSONResponse({"error": "没有这个任务：%s" % task_id}, status_code=404)
    res = rb.check(task, ap.workshop_of_task(task))
    return {"task_id": task_id, **res}


@router.post("/api/tasks/{task_id}/budget")
async def set_budget(task_id: str, request: Request):
    """设预算：`{max_cost, max_rounds, max_minutes}`，任一传 0/空 = 取消该项限制。"""
    from ..server import Utf8JSONResponse, save_state

    task = _task(task_id)
    if task is None:
        return Utf8JSONResponse({"error": "没有这个任务：%s" % task_id}, status_code=404)
    try:
        body = await request.json()
    except Exception:                                        # noqa: BLE001
        body = {}
    body = body if isinstance(body, dict) else {}
    try:
        cur = rb.set_budget(task, body)
    except ValueError as e:
        return Utf8JSONResponse({"error": str(e), "supported": list(rb.FIELDS)}, status_code=400)
    _audit_log.record("task.budget.set", actor="user", target=task_id, detail="预算：%s" % cur)
    save_state()
    return {"success": True, "task_id": task_id, "budget": cur}


@router.get("/api/tasks/{task_id}/budget/check")
async def check_budget(task_id: str):
    from ..server import Utf8JSONResponse

    task = _task(task_id)
    if task is None:
        return Utf8JSONResponse({"error": "没有这个任务：%s" % task_id}, status_code=404)
    return rb.check(task, ap.workshop_of_task(task))


@router.post("/api/workshops/{wid}/budget-default")
async def set_workshop_budget(wid: str, request: Request):
    """工作间默认预算（任务没设时继承）。传 0/空 = 取消该项。"""
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
    cur = dict(getattr(ws, "budget_default", None) or {})
    for k in rb.FIELDS:
        if k not in body:
            continue
        if body[k] in (None, "", 0, "0"):
            cur.pop(k, None)
            continue
        try:
            v = float(body[k])
        except (TypeError, ValueError):
            return Utf8JSONResponse({"error": "%s 必须是数字" % k}, status_code=400)
        if v <= 0:
            return Utf8JSONResponse({"error": "%s 必须大于 0（想去掉就传 0）" % k}, status_code=400)
        cur[k] = v
    ws.budget_default = cur
    _audit_log.record("workshop.budget_default", actor="user", target=wid, detail="默认预算：%s" % cur)
    save_state()
    return {"success": True, "workshop_id": wid, "budget_default": cur}


@router.post("/api/tasks/{task_id}/stop")
async def stop_task(task_id: str, request: Request):
    """按预算/超时或人工意愿**停下来**（留痕 + 快照），不是偷偷改状态。"""
    from ..protocol import TaskStatus
    from ..server import Utf8JSONResponse, save_state

    task = _task(task_id)
    if task is None:
        return Utf8JSONResponse({"error": "没有这个任务：%s" % task_id}, status_code=404)
    try:
        body = await request.json()
    except Exception:                                        # noqa: BLE001
        body = {}
    body = body if isinstance(body, dict) else {}
    why = str(body.get("why") or "用户停止")[:200]
    st = str(getattr(task.status, "value", task.status) or "")
    if st in ("completed", "archived"):
        return Utf8JSONResponse({"error": "这个任务已经是终态（%s），不用停" % st}, status_code=400)

    from .. import run_snapshot as _rs

    _rs.append(task, "fail", summary="停止：%s" % why, actor=str(body.get("by") or "user"))
    task.status = TaskStatus.FAILED
    task.result = (getattr(task, "result", None) or "")
    _audit_log.record("task.stop", actor="user", target=task_id, detail=why)
    save_state()
    return {"success": True, "task_id": task_id, "task_status": "failed", "why": why}


@router.post("/api/tasks/{task_id}/restart")
async def restart_task(task_id: str, request: Request):
    """重跑：**保留历史用量与快照**（不抹账），只把状态放回可派活 + 记一条快照。"""
    from ..protocol import TaskStatus
    from ..server import Utf8JSONResponse, save_state

    task = _task(task_id)
    if task is None:
        return Utf8JSONResponse({"error": "没有这个任务：%s" % task_id}, status_code=404)
    try:
        body = await request.json()
    except Exception:                                        # noqa: BLE001
        body = {}
    body = body if isinstance(body, dict) else {}
    st = str(getattr(task.status, "value", task.status) or "")
    if st not in ("failed", "archived", "completed"):
        return Utf8JSONResponse(
            {"error": "只有失败/归档/完成的任务能重跑（当前：%s）" % st,
             "how_to_fix": "先 POST /api/tasks/{id}/stop 停下来再重跑"}, status_code=400)

    res = rb.check(task, ap.workshop_of_task(task))
    if res["state"] != "ok":
        return Utf8JSONResponse({"error": "预算不允许重跑：%s" % res["why"],
                                 "how_to_fix": "先调高预算（POST /api/tasks/{id}/budget）再重跑"},
                                status_code=409)

    from .. import run_snapshot as _rs

    _rs.append(task, "dispatch", summary="重跑（保留历史用量）",
               detail={"why": str(body.get("why") or "")[:120]}, actor=str(body.get("by") or "user"))
    task.status = TaskStatus.CREATED
    _audit_log.record("task.restart", actor="user", target=task_id, detail="重跑")
    save_state()
    return {"success": True, "task_id": task_id, "task_status": "created",
            "usage_kept": dict(getattr(task, "usage", None) or {})}
