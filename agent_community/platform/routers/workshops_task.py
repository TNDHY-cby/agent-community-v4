"""platform/routers/workshops_task：任务树与资源（V-13 自 workshops.py 拆分）。

承载 halt-and-reset、resources CRUD、任务树 tree/* 等端点。
拆分纪律：路由路径、参数、响应体与拆分前逐字一致（纯搬迁，零行为变更）。
"""
from __future__ import annotations
import sys
from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect
from ..task_state_machine import CREATED
from ..task_state_machine import DISCUSSING
from ..task_state_machine import EV_COMPLETE
from ..task_state_machine import EV_DROP
from ..task_state_machine import EV_RESOLVE
from ..task_state_machine import EV_RESUME
from ..task_state_machine import EV_TIMEOUT
from ..task_state_machine import EXECUTING
from ..protocol import Message
from ..protocol import MessageType
from pathlib import Path
from ..protocol import Task
from ..protocol import TaskStatus
from ..workshop import Workshop
from ..workshop import WorkshopMember
from ..experience_v2 import harness_reputation_bonus as _v2_harness_reputation_bonus
from ..experience_v2 import evaluate_workshop_completion as _v2_evaluate_workshop_completion
from ..audit import audit_log as _audit_log
from ..ai_external import run_ai_call as ai_external_run_ai_call
import asyncio
from ..memory import capability_ledger
from ..harness_adapter import harness_manager
import json
import os
from ..task_state_machine import should_interject
from ..memory import task_memory
from uuid import uuid4
from ..workshop import write_resources_manifest
from ..workshop import write_workspace_files
from ..state import interject_store, pending_activations, task_state_machine, tasks, workshops

router = APIRouter()

@router.post("/api/workshop/{ws_id}/halt-and-reset")
async def workshop_halt_and_reset(ws_id: str, request: Request):
    from ..server import Utf8JSONResponse, _EXPERIENCE_INDEX_KEYS, _dispatch_to_harness, now_iso, save_state
    """确认走弯路：停止任务 → 反思总结入资料库 → 擦除成员/组长记忆 → 从头开始。

    body:
      reason: str         弯路原因（必填，写入复盘文档）
      summary: str        经验总结（选填）
      wipe_members: bool  是否擦除员工成员记忆（默认 true）
      wipe_leader: bool   是否擦除组长记忆（默认 false）
      restart: bool       是否重置工作间回 draft 可重新开始（默认 true）
    """
    ws = workshops.get(ws_id)
    if not ws:
        return Utf8JSONResponse({"error": "工作间不存在"}, status_code=404)
    body = await request.json()
    reason = str(body.get("reason") or "").strip()
    if not reason:
        return Utf8JSONResponse({"error": "缺少 reason（弯路原因）"}, status_code=400)
    summary = str(body.get("summary") or "").strip()
    wipe_members = bool(body.get("wipe_members", True))
    wipe_leader = bool(body.get("wipe_leader", False))
    restart = bool(body.get("restart", True))

    results = {"discussion_cleared": False, "task_memory_removed": 0, "harness_index_cleared": []}

    # 1. 停止任务 + 状态机重置
    ws.status = "draft"
    task_state_machine.set_state(ws_id, CREATED, stage="halt_reset")
    for m in ws.members:
        m.status = "pending"
    for attr in (
        "_auto_review_done", "_review_suff_notified", "_assign_dispatched",
        "_vote_tally", "_vote_invited", "_review_notified", "_leader_ack_seq",
        "_parallel_done",
    ):
        try:
            if hasattr(ws, attr):
                delattr(ws, attr)
        except Exception:
            pass

    # 2. 反思总结归档（DETOUR_SUMMARY.md）
    ws_dir = Path(ws.workspace_dir)
    try:
        ws_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    detour_path = ws_dir / "DETOUR_SUMMARY.md"
    try:
        lines = [
            "# 弯路复盘总结（纠偏记录）", "",
            f"- 工作间：{ws.name}（{ws.workshop_id}）",
            f"- 归档时间：{now_iso()}",
            "",
            "## 错误路径（已否定）", "",
            f"{reason}", "",
            "## 正确路径（推荐）", "",
            f"{summary or '（未总结，建议按错误路径反推正确做法）'}", "",
            "## 重置前最后讨论记录", "",
        ]
        recent = [m for m in ws.discussion if isinstance(m, dict) and not m.get("meta", {}).get("system")][-20:]
        if not recent:
            lines.append("（无讨论记录）")
        else:
            for m in recent:
                who = m.get("display_name") or m.get("role") or "未知"
                ts = str(m.get("timestamp") or "")[:19]
                lines.append(f"- [{ts}] {who}：{str(m.get('content'))[:300]}")
        detour_path.write_text("\n".join(lines), encoding="utf-8")
    except Exception as _e:
        print(f"[halt_reset] 弯路复盘归档失败: {_e}", flush=True)

    # 2b. 任务树落纠偏节点：错误路(dropped) + 正路(active)，复盘资源挂正路
    wrong_node = {
        "node_id": f"t_{uuid4().hex[:8]}",
        "label": f"错误路径（已否定）：{reason[:40]}",
        "parent_id": "",
        "kind": "correction",
        "status": "dropped",
        "note": reason,
        "resources": [],
        "created_at": now_iso(),
    }
    right_node = {
        "node_id": f"t_{uuid4().hex[:8]}",
        "label": f"正确路径（推荐）：{summary[:40] if summary else '按错误路径反推正确做法'}",
        "parent_id": "",
        "kind": "correction",
        "status": "active",
        "note": summary or "",
        "resources": [],
        "created_at": now_iso(),
    }
    ws.task_tree.append(wrong_node)
    ws.task_tree.append(right_node)
    results["tree"] = {"wrong_node": wrong_node["node_id"], "right_node": right_node["node_id"]}

    # 3. 入工作间资料库（自动登记，挂正路节点）
    try:
        detour_res = {
            "rid": uuid4().hex[:8],
            "name": "弯路复盘总结",
            "kind": "file",
            "path": str(detour_path),
            "note": f"确认走弯路后的反思总结：{reason[:60]}",
            "uploader": "platform",
            "task_node": right_node["node_id"],
            "created_at": now_iso(),
        }
        ws.resources.append(detour_res)
        right_node.setdefault("resources", []).append(detour_res["rid"])
        write_resources_manifest(ws)
    except Exception as _e:
        print(f"[halt_reset] 资料库登记失败: {_e}", flush=True)

    # 4. 擦除记忆
    if wipe_members or wipe_leader:
        # 4a. 讨论上下文清零（工作间会话记忆）
        ws.discussion = []
        results["discussion_cleared"] = True
        # 4b. 平台历史任务记忆（按工作间 ID / 名称删除）
        for frag in (ws.workshop_id, ws.name):
            try:
                results["task_memory_removed"] += task_memory.remove_by_fragment(frag)
            except Exception as _e:
                print(f"[halt_reset] 任务记忆擦除失败: {_e}", flush=True)
        # 4c. harness 经验索引清零 + 通知成员记忆已重置
        targets = []
        if wipe_members:
            targets += [m for m in ws.members if m.role != "组长"]
        if wipe_leader:
            targets += [m for m in ws.members if m.role == "组长"]
        seen_hid = set()
        for m in targets:
            hid = (m.harness_ids or [None])[0] if m else None
            if not hid or hid in seen_hid:
                continue
            seen_hid.add(hid)
            sess = harness_manager.sessions.get(hid)
            if sess is not None:
                cleared = []
                metas = [getattr(sess, "metadata", None)]
                if getattr(sess, "info", None):
                    metas.append(getattr(sess.info, "metadata", None))
                for meta in metas:
                    if not isinstance(meta, dict):
                        continue
                    for key in _EXPERIENCE_INDEX_KEYS:
                        if isinstance(meta.get(key), list) and meta.get(key):
                            meta[key] = []
                            if key not in cleared:
                                cleared.append(key)
                results["harness_index_cleared"].append({"hid": hid, "cleared": cleared})
            try:
                _dispatch_to_harness(hid, {
                    "type": "notice",
                    "workshop_id": ws_id,
                    "member_id": m.member_id,
                    "role": m.role,
                    "message": "【记忆重置】工作间确认走弯路，你的任务记忆与经验索引已被平台擦除。请忘记本工作间既往结论，等待重新激活后从零开始。",
                }, kind="notice")
            except Exception as _e:
                print(f"[halt_reset] 记忆重置通知派发失败: {_e}", flush=True)
    save_state()

    # 5. 从头开始
    if restart:
        note = "工作间已重置为 draft，可从一级讨论重新开始（点击「开始工作」激活）。"
    else:
        note = "工作间已停止并保留在 draft。"
    print(f"[halt_reset] 工作间 {ws_id} 走弯路重置完成：reason={reason[:40]}", flush=True)
    return {
        "success": True,
        "status": ws.status,
        "note": note,
        "results": results,
        "detour_path": str(detour_path),
    }

@router.get("/api/workshop/{ws_id}/resources")
async def list_resources(ws_id: str):
    from ..server import Utf8JSONResponse
    """列出工作间资源库（全部成员可读）。"""
    ws = workshops.get(ws_id)
    if not ws:
        return Utf8JSONResponse({"error": "工作间不存在"}, status_code=404)
    items = []
    for r in ws.resources:
        item = dict(r)
        item.setdefault("kind", "file")
        items.append(item)
    return {"success": True, "workshop_id": ws_id, "resources": items}

@router.post("/api/workshop/{ws_id}/resources")
async def add_resource(ws_id: str, request: Request):
    from ..server import Utf8JSONResponse, _append_msg, _tree_find, now_iso, save_state
    """登记资源到工作间资源库。

    需求：组长生成工作间文件夹/产出后上传登记，供其他成员查看。
    body: {name, kind(file/dir/link), path, note, task_node, uploader}
    - path: 资源所在路径（工作区内相对路径或绝对路径均可，记录路径用）
    - task_node: 关联任务树节点标识（可选，与任务树联系的挂载点）
    """
    ws = workshops.get(ws_id)
    if not ws:
        return Utf8JSONResponse({"error": "工作间不存在"}, status_code=404)
    body = await request.json()
    name = (body.get("name") or "").strip()
    if not name:
        return Utf8JSONResponse({"error": "资源名称不能为空"}, status_code=400)
    path = (body.get("path") or "").strip()
    if not path:
        return Utf8JSONResponse({"error": "资源路径不能为空"}, status_code=400)
    kind = (body.get("kind") or "file").strip() or "file"
    if kind not in ("file", "dir", "link"):
        kind = "file"
    res = {
        "rid": uuid4().hex[:8],
        "name": name,
        "kind": kind,
        "path": path,
        "note": (body.get("note") or "").strip(),
        "uploader": (body.get("uploader") or "").strip(),
        "task_node": (body.get("task_node") or "").strip(),
        "created_at": now_iso(),
    }
    # 可选：登记时直接挂载到任务树节点（node_id 双向关联）
    node_id = (body.get("node_id") or "").strip()
    if node_id:
        node = _tree_find(ws, node_id)
        if node is None:
            return Utf8JSONResponse({"error": f"任务树节点不存在：{node_id}"}, status_code=400)
        res["task_node"] = node_id
        attached = node.setdefault("resources", [])
        if res["rid"] not in attached:
            attached.append(res["rid"])
    ws.resources.append(res)
    try:
        write_resources_manifest(ws)
    except Exception as _e:
        print(f"[workshop] 资源清单落盘失败: {_e}", flush=True)
    _append_msg(ws, "notice", f"组长登记资源「{name}」（{kind}）→ {path}", zone=3)
    save_state()
    return {"success": True, "resource": res}

@router.delete("/api/workshop/{ws_id}/resources/{rid}")
async def remove_resource(ws_id: str, rid: str):
    from ..server import Utf8JSONResponse, _append_msg, save_state
    """从资源库移除登记（不删除实体文件，仅解除共享）。"""
    ws = workshops.get(ws_id)
    if not ws:
        return Utf8JSONResponse({"error": "工作间不存在"}, status_code=404)
    before = len(ws.resources)
    ws.resources = [r for r in ws.resources if r.get("rid") != rid]
    if len(ws.resources) == before:
        return Utf8JSONResponse({"error": "资源不存在"}, status_code=404)
    try:
        write_resources_manifest(ws)
    except Exception as _e:
        print(f"[workshop] 资源清单落盘失败: {_e}", flush=True)
    _append_msg(ws, "notice", f"已从资源库移除登记：{rid}", zone=3)
    save_state()
    return {"success": True}

@router.get("/api/workshop/{ws_id}/tree")
async def get_task_tree(ws_id: str):
    from ..server import Utf8JSONResponse, _tree_build
    """获取工作间任务树（嵌套结构，含节点挂载的资源明细）。"""
    ws = workshops.get(ws_id)
    if not ws:
        return Utf8JSONResponse({"error": "工作间不存在"}, status_code=404)
    return {"success": True, "workshop_id": ws_id, "tree": _tree_build(ws)}

@router.post("/api/workshop/{ws_id}/tree/node")
async def create_tree_node(ws_id: str, request: Request):
    from ..server import Utf8JSONResponse, _append_msg, _tree_find, _tree_persist, now_iso
    """创建任务树节点。

    body: {label, parent_id, kind, note, status}
    - label: 节点名称（必填）
    - parent_id: 父节点 node_id（可选，空则挂根）
    - kind: task(任务) / correction(纠偏) / phase(阶段)（默认 task）
    - note: 说明（可选）
    - status: active / done / bypassed / dropped（默认 active；纠偏错误路用 dropped）
    """
    ws = workshops.get(ws_id)
    if not ws:
        return Utf8JSONResponse({"error": "工作间不存在"}, status_code=404)
    body = await request.json()
    label = (body.get("label") or "").strip()
    if not label:
        return Utf8JSONResponse({"error": "节点名称不能为空"}, status_code=400)
    parent_id = (body.get("parent_id") or "").strip()
    if parent_id and _tree_find(ws, parent_id) is None:
        return Utf8JSONResponse({"error": f"父节点不存在：{parent_id}"}, status_code=400)
    kind = (body.get("kind") or "task").strip() or "task"
    if kind not in ("task", "correction", "phase"):
        kind = "task"
    status = (body.get("status") or "active").strip() or "active"
    if status not in ("active", "done", "bypassed", "dropped"):
        status = "active"
    node = {
        "node_id": f"t_{uuid4().hex[:8]}",
        "label": label,
        "parent_id": parent_id,
        "kind": kind,
        "status": status,
        "note": (body.get("note") or "").strip(),
        "resources": [],
        "created_at": now_iso(),
    }
    ws.task_tree.append(node)
    _tree_persist(ws)
    _append_msg(ws, "notice", f"任务树新增节点「{label}」（{kind}/{status}）", zone=3)
    return {"success": True, "node": node}

@router.post("/api/workshop/{ws_id}/tree/node/{node_id}/status")
async def update_tree_node_status(ws_id: str, node_id: str, request: Request):
    from ..server import Utf8JSONResponse, _append_msg, _tree_find, _tree_persist
    """更新任务树节点状态（active/done/bypassed/dropped）。"""
    ws = workshops.get(ws_id)
    if not ws:
        return Utf8JSONResponse({"error": "工作间不存在"}, status_code=404)
    node = _tree_find(ws, node_id)
    if node is None:
        return Utf8JSONResponse({"error": "节点不存在"}, status_code=404)
    body = await request.json()
    status = (body.get("status") or "").strip()
    if status not in ("active", "done", "bypassed", "dropped"):
        return Utf8JSONResponse({"error": "非法状态，应为 active/done/bypassed/dropped"}, status_code=400)
    old = node.get("status", "")
    node["status"] = status
    _tree_persist(ws)
    _append_msg(ws, "notice", f"任务树节点「{node.get('label')}」状态 {old} → {status}", zone=3)
    return {"success": True, "node": node}

@router.post("/api/workshop/{ws_id}/tree/node/{node_id}/attach")
async def attach_resource_to_node(ws_id: str, node_id: str, request: Request):
    from ..server import Utf8JSONResponse, _append_msg, _tree_find, _tree_persist
    """把资源库资源挂到任务树节点（资源与任务双向关联）。

    body: {rid} 资源库资源 ID（必填，须存在）
    """
    ws = workshops.get(ws_id)
    if not ws:
        return Utf8JSONResponse({"error": "工作间不存在"}, status_code=404)
    node = _tree_find(ws, node_id)
    if node is None:
        return Utf8JSONResponse({"error": "节点不存在"}, status_code=404)
    body = await request.json()
    rid = (body.get("rid") or "").strip()
    res = next((r for r in ws.resources if r.get("rid") == rid), None)
    if res is None:
        return Utf8JSONResponse({"error": f"资源库中不存在该资源：{rid}"}, status_code=400)
    attached = node.setdefault("resources", [])
    if rid not in attached:
        attached.append(rid)
    res["task_node"] = node["node_id"]
    _tree_persist(ws)
    _append_msg(ws, "notice", f"资源「{res.get('name')}」已挂载到任务树节点「{node.get('label')}」", zone=3)
    return {"success": True, "node": node, "resource": res}
