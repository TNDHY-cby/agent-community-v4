# -*- coding: utf-8 -*-
"""运行快照 + 工作间流程定义端点（V76 §3.2 / §3.3）。只读为主；导入只**新增**节点。"""
from __future__ import annotations

from fastapi import APIRouter, Request

from .. import run_snapshot as rs
from ..audit import audit_log as _audit_log

router = APIRouter()

#: 工作间流程节点允许的 kind（前端画布用；未知 kind 一律 400 并列出支持值）
NODE_KINDS = ("task", "subtask", "review", "approval", "note")


def _task(task_id: str):
    from ..state import tasks

    return tasks.get(task_id)


def _ws(wid: str):
    from ..state import workshops

    return workshops.get(wid)


@router.get("/api/tasks/{task_id}/snapshots")
async def list_snapshots(task_id: str, limit: int = 50):
    from ..server import Utf8JSONResponse

    task = _task(task_id)
    if task is None:
        return Utf8JSONResponse({"error": "没有这个任务：%s" % task_id}, status_code=404)
    rows = rs.list_of(task, limit)
    return {"task_id": task_id, "count": len(rows), "total": len(getattr(task, "snapshots", []) or []),
            "cap": rs.max_snapshots(), "snapshots": rows}


@router.get("/api/tasks/{task_id}/snapshot")
async def get_snapshot(task_id: str, snap_id: str = ""):
    from ..server import Utf8JSONResponse

    task = _task(task_id)
    if task is None:
        return Utf8JSONResponse({"error": "没有这个任务：%s" % task_id}, status_code=404)
    rec = rs.find(task, snap_id)
    if rec is None:
        return Utf8JSONResponse({"error": "没有这条快照：%s（用 /api/tasks/%s/snapshots 看有哪些）"
                                          % (snap_id, task_id)}, status_code=404)
    return rec


@router.get("/api/workshops/{wid}/workflow")
async def export_workflow(wid: str):
    """导出流程（用现有 `task_tree`，edges 由 parent_id 推出）——画布存盘用。"""
    from ..server import Utf8JSONResponse

    ws = _ws(wid)
    if ws is None:
        return Utf8JSONResponse({"error": "没有这个工作间：%s" % wid}, status_code=404)
    nodes, edges = [], []
    for node in (getattr(ws, "task_tree", None) or []):
        if not isinstance(node, dict):
            continue
        nid = str(node.get("node_id") or "")
        if not nid:
            continue
        nodes.append({"node_id": nid, "label": node.get("label") or "",
                      "kind": node.get("kind") or "task",
                      "status": node.get("status") or "",
                      "parent_id": node.get("parent_id") or ""})
        if node.get("parent_id"):
            edges.append({"from": str(node["parent_id"]), "to": nid, "kind": "parent"})
    return {"workshop_id": wid, "name": getattr(ws, "name", "") or "",
            "nodes": nodes, "edges": edges}


@router.post("/api/workshops/{wid}/workflow")
async def import_workflow(wid: str, request: Request):
    """导入流程：**只新增节点**（不删、不改既有节点），未知字段/kind 一律 400 并说清。"""
    from ..server import Utf8JSONResponse, save_state

    ws = _ws(wid)
    if ws is None:
        return Utf8JSONResponse({"error": "没有这个工作间：%s" % wid}, status_code=404)
    try:
        body = await request.json()
    except Exception:                                        # noqa: BLE001
        body = {}
    body = body if isinstance(body, dict) else {}
    nodes = body.get("nodes")
    if not isinstance(nodes, list) or not nodes:
        return Utf8JSONResponse({"error": "要传 nodes: [...]（至少一个节点）"}, status_code=400)

    allowed = {"node_id", "label", "kind", "status", "parent_id", "parents", "note"}
    existing = {str(n.get("node_id")) for n in (getattr(ws, "task_tree", None) or [])
                if isinstance(n, dict)}
    added, skipped = [], []
    for raw in nodes:
        if not isinstance(raw, dict):
            return Utf8JSONResponse({"error": "nodes 里每一项都要是对象"}, status_code=400)
        unknown = set(raw) - allowed
        if unknown:
            return Utf8JSONResponse({"error": "不认识的字段：%s" % sorted(unknown),
                                     "supported": sorted(allowed)}, status_code=400)
        kind = str(raw.get("kind") or "task")
        if kind not in NODE_KINDS:
            return Utf8JSONResponse({"error": "不支持节点类型 kind=%s" % kind,
                                     "supported": list(NODE_KINDS)}, status_code=400)
        nid = str(raw.get("node_id") or "")
        if not nid:
            return Utf8JSONResponse({"error": "每个节点都要 node_id"}, status_code=400)
        if nid in existing:
            skipped.append(nid)          # 已存在的**不改**（防误毁）
            continue
        ws.task_tree = list(getattr(ws, "task_tree", None) or []) + [{
            "node_id": nid, "label": str(raw.get("label") or "")[:120], "kind": kind,
            "status": str(raw.get("status") or "planned"),
            "parent_id": str(raw.get("parent_id") or ""), "note": str(raw.get("note") or "")[:200],
            "resources": [], "created_at": "",
        }]
        existing.add(nid)
        added.append(nid)

    _audit_log.record("workshop.workflow.import", actor="user", target=wid,
                      detail="导入节点 %d 个（跳过已存在 %d 个）" % (len(added), len(skipped)))
    save_state()
    return {"success": True, "workshop_id": wid, "added": added, "skipped_existing": skipped}
