# -*- coding: utf-8 -*-
"""工作流执行端点（V78）：一次推进一步 / 看状态 / 复位。逻辑全在 `platform/workflow_engine.py`。"""
from __future__ import annotations

from fastapi import APIRouter, Request

from .. import workflow_engine as we
from ..audit import audit_log as _audit_log

router = APIRouter()


class _NodeAsTask:
    """节点在"还没有任务对象"时的替身：让 `run_budget.check` 能用统一口径判。"""

    def __init__(self, node_id: str):
        self.id = node_id
        self.usage = {}
        self.budget = {}
        self.created_at = ""


def _ws(wid: str):
    from ..state import workshops

    return workshops.get(wid)


def _node_of_task(task_id: str):
    """按任务反查它对应的流程节点（节点的 node_id 与任务 id 可能不同，用 label/node_id 兜）。"""
    from ..state import tasks, workshops

    # 先按节点找（节点不一定有对应任务对象；没有任务也要能标完成）
    for ws in workshops.values():
        for node in (getattr(ws, "task_tree", None) or []):
            if isinstance(node, dict) and str(node.get("node_id") or "") == task_id:
                return ws, node
    return None, None


@router.get("/api/workshops/{wid}/workflow/run")
async def workflow_run_status(wid: str):
    from ..server import Utf8JSONResponse

    ws = _ws(wid)
    if ws is None:
        return Utf8JSONResponse({"error": "没有这个工作间：%s" % wid}, status_code=404)
    return {"workshop_id": wid, **we.status(ws)}


@router.post("/api/workshops/{wid}/workflow/run")
async def workflow_run_step(wid: str, request: Request):
    """推进一步（幂等）：派下一个可跑节点；预算超了 409；审批型节点停住等人批。"""
    from .. import approval as ap
    from .. import run_budget as rb
    from .. import run_snapshot as rs
    from ..server import Utf8JSONResponse, save_state
    from ..state import tasks

    ws = _ws(wid)
    if ws is None:
        return Utf8JSONResponse({"error": "没有这个工作间：%s" % wid}, status_code=404)

    try:
        body = await request.json()
    except Exception:                                        # noqa: BLE001
        body = {}
    body = body if isinstance(body, dict) else {}

    batch = we.next_nodes(ws)          # V79：同层可并行（上限 AC_WORKFLOW_BATCH）
    if not batch:
        st = we.status(ws)
        return {"advanced": False, "advanced_nodes": [], "state": st["state"],
                "why": st["why"], "next": None}
    node = batch[0]

    node_id = str(node["node_id"])
    kind = str(node.get("kind") or "task")

    # 预算闸（V77）：按节点关联的任务判；没有关联任务就不能判，如实说明但不拦
    task = tasks.get(node_id)
    if task is not None:
        chk = rb.check(task, ws)
    else:
        # ★D3：节点没有对应任务时，**不能再默认 ok**（那是"静默放行"）。
        #   改为按工作间默认预算判一次；仍判不出 ⇒ 明确说"判不了"，并在响应里带 note。
        chk = rb.check(_NodeAsTask(node_id), ws)
        chk["note"] = ("这个节点没有对应任务（node_id=%s），预算按工作间默认口径估算；"
                       "要精确控制请给节点建任务" % node_id)
    if chk["state"] != "ok":
        return Utf8JSONResponse({"advanced": False, "node_id": node_id, "state": chk["state"],
                                 "error": chk["why"], "how_to_fix": chk["how_to_fix"]},
                                status_code=409)

    # 审批型节点：进闸门后**不推进**
    if kind == "approval":
        we.mark(node, "waiting_approval")
        if task is not None:
            task.approval = ap.enter(task, required=True,
                                     reason="流程节点 %s 需要人工确认" % node_id,
                                     resume_to=str(getattr(task.status, "value", task.status) or ""))
            from ..protocol import TaskStatus

            task.status = TaskStatus.WAITING_APPROVAL
        _audit_log.record("workshop.workflow.run", actor="user", target=wid,
                          detail="节点 %s 需审批，已停下" % node_id)
        save_state()
        return {"advanced": False, "node_id": node_id, "state": "waiting_approval",
                "why": "这个节点需要人工确认（V76 审批闸门），批完再调用本端点继续"}

    we.mark(node, "running")
    if task is not None:
        rs.append(task, "dispatch", summary="流程推进：%s" % (node.get("label") or node_id),
                  detail={"node_id": node_id, "kind": kind}, actor=str(body.get("by") or "user"))
        node["task_id"] = task.id
    advanced = [{"node_id": node_id, "kind": kind}]
    blocked_now = []
    for extra in batch[1:]:                          # 同层其它可跑节点：一起派
        eid = str(extra["node_id"])
        if str(extra.get("kind") or "task") == "approval":
            continue                                 # 审批节点不批量推，逐个等人批
        # ★D1：同批**每个节点都要过预算闸**（原来只判了 batch[0]，等于三份并发只记一份）
        _et_chk = tasks.get(eid)
        _echk = rb.check(_et_chk if _et_chk is not None else _NodeAsTask(eid), ws)
        if _echk["state"] != "ok":
            we.mark(extra, "blocked")
            blocked_now.append({"node_id": eid, "why": _echk.get("why") or "",
                                "how_to_fix": _echk.get("how_to_fix") or ""})
            continue
        we.mark(extra, "running")
        et = tasks.get(eid)
        if et is not None:
            rs.append(et, "dispatch", summary="流程推进（并行）：%s" % (extra.get("label") or eid),
                      detail={"node_id": eid, "batch": [a["node_id"] for a in advanced]},
                      actor=str(body.get("by") or "user"))
        advanced.append({"node_id": eid, "kind": str(extra.get("kind") or "task")})

    _audit_log.record("workshop.workflow.run", actor="user", target=wid,
                      detail="推进 %d 个节点：%s" % (len(advanced),
                                                    ",".join(a["node_id"] for a in advanced)[:140]))
    save_state()
    return {"advanced": True, "advanced_nodes": advanced, "node_id": node_id, "kind": kind,
            "state": "running", "blocked_nodes": blocked_now,
            "next": (we.status(ws)["next"] or {})}


@router.post("/api/workshops/{wid}/workflow/reset")
async def workflow_reset(wid: str):
    """把 blocked / running 复位成 planned（人工干预后重跑），留痕。"""
    from ..server import Utf8JSONResponse, save_state

    ws = _ws(wid)
    if ws is None:
        return Utf8JSONResponse({"error": "没有这个工作间：%s" % wid}, status_code=404)
    touched = we.reset_blocked(ws)
    _audit_log.record("workshop.workflow.reset", actor="user", target=wid,
                      detail="复位 %d 个节点：%s" % (len(touched), ",".join(touched)[:120]))
    save_state()
    return {"success": True, "workshop_id": wid, "reset": touched}


@router.post("/api/tasks/{task_id}/workflow-node/done")
async def mark_node_done(task_id: str, request: Request):
    """回执方/人把某个节点标完成（引擎据此放行子节点）。"""
    from .. import run_snapshot as rs
    from ..server import Utf8JSONResponse, save_state
    from ..state import tasks

    ws, node = _node_of_task(task_id)
    if node is None:
        return Utf8JSONResponse({"error": "没有找到这个任务对应的流程节点：%s" % task_id},
                                status_code=404)
    try:
        body = await request.json()
    except Exception:                                        # noqa: BLE001
        body = {}
    body = body if isinstance(body, dict) else {}

    routed_to = ""
    if bool(body.get("ok", True)):
        we.mark(node, "done")
        state = "done"
    else:
        out = we.on_fail(ws, node)               # V79：retry → 有备选就重路由 → 否则 blocked
        state, routed_to = out["state"], out["routed_to"]
    task = tasks.get(task_id)
    if task is not None:
        rs.append(task, "complete" if state == "done" else "fail",
                  summary="节点 %s：%s" % (str(node.get("node_id")), state),
                  actor=str(body.get("by") or "harness"))
    save_state()
    return {"success": True, "node_id": str(node.get("node_id")), "state": state,
            "routed_to": routed_to}
