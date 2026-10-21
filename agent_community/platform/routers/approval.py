# -*- coding: utf-8 -*-
"""人工审批闸门端点（V76 §2.2）。逻辑在 `platform/approval.py`，这里只做 HTTP 与留痕。"""
from __future__ import annotations

from fastapi import APIRouter, Request

from .. import approval as ap
from ..audit import audit_log as _audit_log

router = APIRouter()


def _task(task_id: str):
    from ..state import tasks

    return tasks.get(task_id)


def _workshop(wid: str):
    from ..state import workshops

    return workshops.get(wid)


@router.get("/api/tasks/{task_id}/approval")
async def task_approval(task_id: str):
    """当前待批什么。**没有就说没有**（state=none + 一句为什么），不返回空壳。"""
    from ..server import Utf8JSONResponse

    task = _task(task_id)
    if task is None:
        return Utf8JSONResponse({"error": "没有这个任务：%s" % task_id}, status_code=404)
    rec = dict(getattr(task, "approval", None) or {})
    st = str(getattr(task.status, "value", task.status) or "")
    if not rec.get("required"):
        return {"task_id": task_id, "state": "none",
                "why": "这个任务/工作间没开审批闸门（默认关）", "task_status": st}
    if st == "waiting_approval":
        return {"task_id": task_id, "state": "waiting", "task_status": st,
                "reason": rec.get("reason") or "等人点确认",
                "resume_to": rec.get("resume_to") or "", "at": rec.get("at") or "",
                "how_to": "POST 本端点 {decision: approve|reject|cancel, note: 说明}"}
    return {"task_id": task_id, "state": "decided", "task_status": st,
            "decision": rec.get("decision") or "", "note": rec.get("note") or "",
            "decided_at": rec.get("decided_at") or ""}


@router.post("/api/tasks/{task_id}/approval")
async def decide(task_id: str, request: Request):
    from ..server import Utf8JSONResponse, save_state

    task = _task(task_id)
    if task is None:
        return Utf8JSONResponse({"error": "没有这个任务：%s" % task_id}, status_code=404)
    try:
        body = await request.json()
    except Exception:                                        # noqa: BLE001
        body = {}
    body = body if isinstance(body, dict) else {}

    ok, info = ap.check_decision(task, str(body.get("decision") or ""), str(body.get("note") or ""))
    if not ok:
        return Utf8JSONResponse({"error": info, "task_status": str(getattr(task.status, "value", ""))},
                                status_code=400)

    rec = ap.exit_record(task, info, str(body.get("note") or ""), by=str(body.get("by") or "user"))
    resume_to = rec.get("resume_to") or "delegating"
    if info == "approve":
        target = resume_to
    elif info == "reject":
        target = "delegating"
    else:
        target = "failed"          # cancel：按"终止"处理（现有终态集里没有 cancelled，用 failed + 说明）
        rec["note"] = ("用户终止：" + rec["note"]).strip("：")

    task.approval = rec
    from ..protocol import TaskStatus

    try:
        task.status = TaskStatus(target)
    except ValueError:
        return Utf8JSONResponse({"error": "目标状态不合法，已拒绝（target=%s）" % target},
                                status_code=409)

    try:                                                     # V76：审批决策进运行快照
        from .. import run_snapshot as _rs

        _rs.append(task, "approval", summary="审批：%s" % info,
                   detail={"decision": info, "note": rec.get("note") or ""},
                   actor=rec.get("by") or "user")
    except Exception as _e:                                  # noqa: BLE001
        print("[snapshot] 审批快照失败：%s" % _e, flush=True)
    _audit_log.record("task.approval.%s" % info, actor="user", target=task_id,
                      detail="审批决策：%s%s" % (info, ("｜" + rec["note"]) if rec.get("note") else ""))
    save_state()
    return {"success": True, "task_id": task_id, "decision": info,
            "task_status": str(getattr(task.status, "value", task.status))}


@router.post("/api/workshops/{wid}/approval-policy")
async def set_policy(wid: str, request: Request):
    """开/关这个工作间的审批闸门。**默认关**，开了才在"回执验证通过"后停下来等人批。"""
    from ..server import Utf8JSONResponse, save_state

    ws = _workshop(wid)
    if ws is None:
        return Utf8JSONResponse({"error": "没有这个工作间：%s" % wid}, status_code=404)
    try:
        body = await request.json()
    except Exception:                                        # noqa: BLE001
        body = {}
    body = body if isinstance(body, dict) else {}
    if "approval_required" not in body:
        return Utf8JSONResponse({"error": "要传 approval_required: true|false"}, status_code=400)
    required = bool(body.get("approval_required"))
    if not hasattr(ws, "approval_policy"):
        ws.approval_policy = {}
    ws.approval_policy = {"approval_required": required}
    _audit_log.record("workshop.approval_policy", actor="user", target=wid,
                      detail="审批闸门 %s" % ("开" if required else "关"))
    save_state()
    return {"success": True, "workshop_id": wid, "approval_required": required}
