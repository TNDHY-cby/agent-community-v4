# -*- coding: utf-8 -*-
"""任务一族（router 端点组）

## 端点（11 个）

```
GET /api/tasks
POST /api/task/{task_id}/start
GET /api/tasks/{task_id}/workshop
GET /api/task/{task_id}
GET /api/tasks/tree
GET /api/task/{task_id}/tree
POST /api/task/{task_id}/parent
POST /api/task/{task_id}/discuss
GET /api/tasks/stale
POST /api/task/{task_id}/recover
POST /api/tasks/recover-stale
```

## 为什么在 `routers/` 里

2026-10-21 从 `server.py` 整块搬出（审计点名"巨无霸"结构债）。**函数体原样搬，行为不变**；
`server` 里的全局在**函数体内惰性 import**（模块级 import 会与 `server` 形成循环，见 `routers/docking.py`）；
注册走 `app.include_router`。

搬出工具：`python tools/move_endpoints.py`（可复用，别手工搬）。
"""
from __future__ import annotations

import json
from datetime import datetime

from fastapi import APIRouter, Request

router = APIRouter()

@router.get("/api/tasks")
async def list_tasks(limit: int = None):
    from ..server import tasks
    task_list = [t.model_dump() for t in tasks.values()]
    # 按创建时间倒序
    task_list.sort(key=lambda t: t.get("created_at", ""), reverse=True)
    if limit:
        task_list = task_list[:limit]
    return {"tasks": task_list}

@router.post("/api/task/{task_id}/start")
async def task_start(task_id: str):
    # V77：启动前先过预算/超时闸（超了不许再花）
    try:
        from .. import approval as _ap
        from .. import run_budget as _rb
        from ..state import tasks as _tasks

        _t_obj = _tasks.get(task_id)
        if _t_obj is not None:
            _chk = _rb.check(_t_obj, _ap.workshop_of_task(_t_obj))
            if _chk["state"] != "ok":
                from ..server import Utf8JSONResponse

                return Utf8JSONResponse({"error": _chk["why"], "state": _chk["state"],
                                         "how_to_fix": _chk["how_to_fix"]}, status_code=409)
    except Exception as _e:                                  # noqa: BLE001 — 闸门本身坏了不该挡住启动，但要留痕
        print("[budget] 启动闸判定失败（放行）：%s" % _e, flush=True)
    """启动任务 — v4：AI 分析拆解 → 创建讨论室 → 拓扑执行 + 审查"""
    from ..server import TaskStatus, Utf8JSONResponse, agents, discussion_engine, discussion_rooms, orchestrator, task_to_room, tasks
    task = tasks.get(task_id)
    if not task:
        return Utf8JSONResponse({"error": "任务不存在"}, status_code=404)
    # 修复：TaskStatus 枚举无 DISCUSSING，改用 IN_DISCUSSION
    task.status = TaskStatus.IN_DISCUSSION
    # 创建讨论室
    agent_ids = [aid for aid in agents if aid != orchestrator.AGENT_ID]
    room = discussion_engine.create_room(
        task=task,
        agent_ids=agent_ids,
        analysis={"phase": "init", "task_id": task_id},
    )
    discussion_rooms[room.room_id] = room
    task_to_room[task_id] = room.room_id
    return {
        "ok": True,
        "task_id": task_id,
        "status": task.status.value,
        "room_id": room.room_id,
        "participants": agent_ids,
    }

@router.get("/api/tasks/{task_id}/workshop")
async def api_task_workshop(task_id: str):
    """由任务反查它自动建成的工作间（老板主流程 ②的反查口）。

    没有则 404 —— 前端据此区分「这条任务还没建间」与「房间已建好，去选人」。
    """
    from ..server import Utf8JSONResponse, _find_workshop_of_task
    _ws = _find_workshop_of_task(task_id)
    if _ws is None:
        return Utf8JSONResponse({"error": "该任务还没有自动建成的现工作间"}, status_code=404)
    return {"success": True, "task_id": task_id, "workshop_id": _ws.workshop_id,
            "name": _ws.name, "status": _ws.status,
            "member_count": len(_ws.members or []),
            "workspace_dir": _ws.workspace_dir,
            "next_step": ("选成员（POST /api/workshop/{id}/select-members 由 AI 初选，"
                          "或 POST /api/workshop/{id}/members 手工加）"
                          if not _ws.members else "名单已定，可开工")}

@router.get("/api/task/{task_id}")
async def get_task(task_id: str):
    from ..server import Utf8JSONResponse, tasks
    if task_id not in tasks:
        return Utf8JSONResponse({"error": "task not found"}, status_code=404)
    return tasks[task_id].model_dump()

@router.get("/api/tasks/tree")
async def list_tasks_tree():
    """全局任务树：按 parent_id 组装嵌套结构（根任务在前，含 children 子任务）。"""
    from ..server import _task_tree_build, tasks
    task_list = [t.model_dump() for t in tasks.values()]
    task_list.sort(key=lambda t: t.get("created_at", ""), reverse=True)
    return {"success": True, "total": len(task_list), "tree": _task_tree_build(task_list)}

@router.get("/api/task/{task_id}/tree")
async def get_task_subtree(task_id: str):
    """以某任务为根的子树导航：返回该任务及其全部后代（children 嵌套）。"""
    from ..server import Utf8JSONResponse, tasks
    if task_id not in tasks:
        return Utf8JSONResponse({"error": "task not found"}, status_code=404)
    by_id = {t["id"]: dict(t) for t in (x.model_dump() for x in tasks.values())}
    for node in by_id.values():
        node["children"] = []
    for node in by_id.values():
        pid = node.get("parent_id") or ""
        if pid and pid in by_id:
            by_id[pid]["children"].append(node)
    return {"success": True, "tree": by_id[task_id]}

@router.post("/api/task/{task_id}/parent")
async def set_task_parent(task_id: str, request: Request):
    """设置任务父节点，构建任务树。

    body: {parent_id}
    - parent_id: 父任务 id（可选；空或省略则把任务设为根任务）
    - 校验：父任务必须存在；禁止挂到自身或自身子孙之下（防环）
    """
    from ..server import Utf8JSONResponse, save_state, tasks
    if task_id not in tasks:
        return Utf8JSONResponse({"error": "task not found"}, status_code=404)
    body = await request.json()
    parent_id = (body.get("parent_id") or "").strip()
    if parent_id:
        if parent_id == task_id:
            return Utf8JSONResponse({"error": "任务不能作为自身的父节点"}, status_code=400)
        if parent_id not in tasks:
            return Utf8JSONResponse({"error": f"父任务不存在：{parent_id}"}, status_code=400)
        # 防环：沿 parent 链上溯，若遇到 task_id 说明会成环
        cur = parent_id
        seen = set()
        while cur:
            if cur == task_id:
                return Utf8JSONResponse({"error": "不能将任务挂到自身子孙之下（会形成环）"}, status_code=400)
            if cur in seen:
                break
            seen.add(cur)
            cur = tasks[cur].parent_id
    tasks[task_id].parent_id = parent_id
    tasks[task_id].updated_at = datetime.now().isoformat()
    save_state()
    return {"success": True, "task_id": task_id, "parent_id": parent_id}

@router.post("/api/task/{task_id}/discuss")
async def api_task_discuss(task_id: str, request: Request):
    """旧前端：进入讨论室 / 发送讨论消息"""
    from ..server import DiscussionRoom, DiscussionRoomStatus, Message, MessageType, Utf8JSONResponse, bcast_to_clients, discussion_rooms, tasks
    body = await request.json()
    content = body.get("message", "")
    if task_id in tasks:
        m = Message(type=MessageType.CHAT, from_agent="user", content=content, task_id=task_id)
        if task_id not in discussion_rooms:
            discussion_rooms[task_id] = DiscussionRoom(
                room_id=task_id,
                task_id=task_id,
                status=DiscussionRoomStatus.NEGOTIATING,
            )
        tasks[task_id].messages.append(m)
        await bcast_to_clients(m)
        return {"success": True}
    return Utf8JSONResponse({"error": "任务不存在"}, status_code=404)

@router.get("/api/tasks/stale")
async def list_stale_tasks():
    """列出所有卡死任务（供前端/批量回收使用）。返回 stale 列表，含判定原因。"""
    from ..server import _is_stale_task, tasks
    stale = []
    for t in tasks.values():
        r = _is_stale_task(t)
        if r["stale"]:
            stale.append({
                "task_id": t.id,
                "title": t.title,
                "status": t.status.value,
                "created_at": t.created_at,
                "updated_at": t.updated_at,
                "reason": r["reason"],
            })
    return {"success": True, "stale_count": len(stale), "stale": stale}

@router.post("/api/task/{task_id}/recover")
async def recover_task(task_id: str, request: Request):
    """回收卡死任务（单任务）。

    body: {action: "resume" | "archive"}（可选，默认 archive）
    - archive：置为 archived 终态（写入 completed_at），任务归档不再参与活跃统计；
    - resume ：重置为可继续状态——有存活讨论室则回 in_discussion，否则回 broadcasting
               （可重新广播/继续推进）。
    - 非卡死/终态任务返回 409，不破坏既有数据。
    """
    from ..server import TaskStatus, Utf8JSONResponse, _TASK_TERMINAL, _is_stale_task, discussion_rooms, now_iso, save_state, tasks
    task = tasks.get(task_id)
    if not task:
        return Utf8JSONResponse({"error": "task not found"}, status_code=404)
    verdict = _is_stale_task(task)
    try:
        body = await request.json()
    except Exception:
        body = {}
    action = (body.get("action") or "archive").strip().lower()
    if action not in ("resume", "archive"):
        return Utf8JSONResponse({"error": f"action 仅支持 resume/archive，收到: {action}"}, status_code=400)
    if task.status in _TASK_TERMINAL:
        return Utf8JSONResponse({"error": f"任务已是终态 {task.status.value}，无需回收"}, status_code=409)
    old_status = task.status.value
    now = now_iso()
    if action == "resume":
        if task.room_id and task.room_id in discussion_rooms:
            task.status = TaskStatus.IN_DISCUSSION
            note = f"卡死回收后重置为 in_discussion（保留讨论室 {task.room_id}），可继续讨论"
        else:
            task.status = TaskStatus.BROADCASTING
            note = "卡死回收后重置为 broadcasting，可重新广播/推进"
    else:
        task.status = TaskStatus.ARCHIVED
        task.completed_at = now
        note = "卡死回收归档"
    task.updated_at = now
    task.result = (task.result or "") + f"\n[回收] {note}"
    save_state()
    return {
        "success": True,
        "task_id": task_id,
        "old_status": old_status,
        "new_status": task.status.value,
        "action": action,
        "reason": verdict["reason"],
        "note": note,
    }

@router.post("/api/tasks/recover-stale")
async def recover_stale_tasks(request: Request):
    """批量回收全部卡死任务。

    body: {task_ids?: [...], action?: "resume"|"archive"}（可选，默认回收全部卡死任务并归档）
    - 不传 task_ids：对 GET /api/tasks/stale 判定的全部卡死任务执行回收；
    - 传入 task_ids：仅对指定 id 中确实卡死的任务执行回收（非卡死/终态计入 skipped）。
    返回 {recycled: [...], skipped: [...]}。
    """
    from ..server import TaskStatus, Utf8JSONResponse, _is_stale_task, discussion_rooms, now_iso, save_state, tasks
    try:
        body = await request.json()
    except Exception:
        body = {}
    action = (body.get("action") or "archive").strip().lower()
    if action not in ("resume", "archive"):
        return Utf8JSONResponse({"error": f"action 仅支持 resume/archive，收到: {action}"}, status_code=400)
    want_ids = body.get("task_ids")
    if want_ids is not None and not isinstance(want_ids, list):
        return Utf8JSONResponse({"error": "task_ids 必须是数组"}, status_code=400)
    recycled, skipped = [], []
    for t in tasks.values():
        if want_ids is not None and t.id not in want_ids:
            continue
        verdict = _is_stale_task(t)
        if not verdict["stale"]:
            if want_ids is not None:
                skipped.append({"task_id": t.id, "reason": verdict["reason"]})
            continue
        old_status = t.status.value
        now = now_iso()
        if action == "resume":
            if t.room_id and t.room_id in discussion_rooms:
                t.status = TaskStatus.IN_DISCUSSION
                note = "卡死回收后重置为 in_discussion，可继续讨论"
            else:
                t.status = TaskStatus.BROADCASTING
                note = "卡死回收后重置为 broadcasting，可重新广播"
        else:
            t.status = TaskStatus.ARCHIVED
            t.completed_at = now
            note = "卡死回收归档"
        t.updated_at = now
        t.result = (t.result or "") + f"\n[回收] {note}"
        recycled.append({"task_id": t.id, "old_status": old_status, "new_status": t.status.value,
                         "reason": verdict["reason"]})
    save_state()
    return {"success": True, "action": action, "recycled": recycled, "skipped": skipped,
            "recycled_count": len(recycled), "skipped_count": len(skipped)}
