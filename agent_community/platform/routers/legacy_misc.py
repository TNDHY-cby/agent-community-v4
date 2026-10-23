# -*- coding: utf-8 -*-
"""旧前端兼容与杂项一族（router 端点组）

## 端点（9 个）

```
GET /api/rooms
POST /api/auth/logout
GET /api/status
GET /api/bus/stats
POST /api/session/save
GET /api/session/restore
GET /api/events
GET /api/budget/{task_id}
POST /api/command
```

## 为什么在 `routers/` 里

2026-10-21 从 `server.py` 整块搬出（审计点名"巨无霸"结构债）。**函数体原样搬，行为不变**；
`server` 里的全局在**函数体内惰性 import**（模块级 import 会与 `server` 形成循环，见 `routers/docking.py`）；
注册走 `app.include_router`。

搬出工具：`python tools/move_endpoints.py`（可复用，别手工搬）。
"""
from __future__ import annotations

import json
import asyncio
from uuid import uuid4
from datetime import datetime

from fastapi import APIRouter, Request

router = APIRouter()

@router.get("/api/rooms")
async def list_rooms():
    from ..server import discussion_rooms
    return {"rooms": [r.model_dump() for r in discussion_rooms.values()]}

@router.post("/api/auth/logout")
async def api_auth_logout():
    """退出外部访问（V65）：清掉 `?token=` 种的同源 cookie。

    只影响**携带方式**，不吊销令牌本身（要吊销请改 `AC4_TOKEN` 并重启）。
    """
    from ..server import EXT_TOKEN_COOKIE
    from fastapi.responses import JSONResponse
    r = JSONResponse({"success": True, "note": "已清掉外部访问 cookie；令牌本身未吊销"})
    r.delete_cookie(EXT_TOKEN_COOKIE, path="/")
    return r

@router.get("/api/status")
async def api_status():
    """服务状态检查接口 — CLI start 自检用

    ★V60（2026-10-20，前端通道 201 申请）：增加 `ai` 段，让界面**不用猜 agent id**
    就能如实显示"当前配置的平台 AI 通不通"。
    ⚠️ **零成本铁律**：本端点被界面/看门狗/CLI 高频轮询 ⇒ **绝不在里面主动探活**，
    `reachable` 只来自 V-16 自检的缓存结论；没验过就如实给 `null`（未知），不猜。
    """
    from ..server import PIPE_DIR, TaskStatus, _PKG_VERSION, _ai_health_snapshot, agents, data_health, harness_manager, orchestrator, tasks, ws_agents
    online_agents = sum(1 for aid in agents if (
        aid in ws_agents or harness_manager.is_harness_agent(aid) or aid == orchestrator.AGENT_ID
    ))
    try:                                     # ★V82：单实例状态（降级时界面能看出原因）
        from ..singleton import state as _sstate

        _sing = _sstate()
    except Exception as _e:                  # noqa: BLE001
        _sing = {"policy": "unknown", "why": str(_e)[:80]}
    try:                                     # ★V86：凭据体检摘要（**只读账本、绝不在轮询里体检**）
        from ..credentials import ledger_summary_light as _cred_light

        _creds = _cred_light()
    except Exception as _e:                  # noqa: BLE001
        _creds = {"scanned": False, "why": str(_e)[:80]}
    return {
        "singleton": _sing,
        "status": "running",
        "version": _PKG_VERSION,
        "credentials": _creds,
        # ★2026-10-23（数据被清空事故后）：启动时算好的"数据健康"摘要。
        # 界面据此显示横幅（"状态是空的、但数据目录里有历史痕迹"），别再让用户自己猜。
        "data_health": data_health(),
        "agents_registered": len(agents),
        "agents_online": online_agents,
        "tasks_total": len(tasks),
        "tasks_active": sum(1 for t in tasks.values() if t.status not in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.ARCHIVED)),
        "harnesses": len(harness_manager.sessions),
        "pipe_dir": str(PIPE_DIR),
        "ai": _ai_health_snapshot(),
    }

@router.get("/api/bus/stats")
async def bus_stats():
    """业务统计 — 旧版前端兼容"""
    from ..server import DiscussionRoomStatus, TaskStatus, discussion_rooms, harness_manager, tasks
    total_msgs = sum(len(t.messages) for t in tasks.values())
    return {
        "tasks_total": len(tasks),
        "tasks_active": sum(1 for t in tasks.values() if t.status not in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.ARCHIVED)),
        "total_messages": total_msgs,
        "rooms_total": len(discussion_rooms),
        "rooms_active": sum(1 for r in discussion_rooms.values() if r.status == DiscussionRoomStatus.NEGOTIATING),
        "harnesses_total": len(harness_manager.sessions),
        "harnesses_online": sum(1 for s in harness_manager.sessions.values() if s.status.value == "online"),
    }

@router.post("/api/session/save")
async def session_save():
    """保存会话 — 旧版前端兼容（当前为无操作占位）"""
    return {"ok": True, "saved_at": datetime.now().isoformat()}

@router.get("/api/session/restore")
async def session_restore():
    """恢复会话 — 旧版前端兼容"""
    return {"ok": True, "restored": False, "last_saved": None}

@router.get("/api/events")
async def list_events(limit: int = 50):
    """全局事件流 — 旧版前端兼容"""
    from ..server import tasks
    events = []
    for t in tasks.values():
        for msg in t.messages[-20:]:
            events.append({
                "event_id": str(uuid4()),
                "type": msg.type.value,
                "from_agent": msg.from_agent,
                "content": msg.content[:200],
                "task_id": t.id,
                # `Message` 的时间字段叫 `timestamp`（不是 created_at）；
                # 兜底用任务的 created_at（两者都经 _ts 归一成字符串后比较）
                "created_at": getattr(msg, "timestamp", None) or getattr(t, "created_at", None),
            })
    # ⚠️2026-10-21 实测（真请求抓到的 500，**搬家前就有的缺陷**）：
    #   ① `Message` 模型**没有 `created_at`**，它叫 `timestamp` ⇒ 老代码 `msg.created_at`
    #      直接 `AttributeError` ⇒ 整个端点 500。之前没炸只是因为 tasks 里没消息、循环没进去。
    #   ② `created_at` 在这条链路上**可能是 datetime 也可能是 str**（新版 Message 用 datetime，
    #      兜底用 `t.created_at` 字符串）⇒ 直接 sort 会在"两种都有"时抛 TypeError。
    #   ⇒ 统一取**真实存在的字段**，并把时间归一成可比较的字符串。
    def _ts(v):
        if v is None:
            return ""
        if isinstance(v, str):
            return v
        try:
            return v.isoformat()
        except Exception:                                      # noqa: BLE001 — 非日期就退化成 str（排序用，不参与回显）
            return str(v)

    events.sort(key=lambda e: _ts(e.get("created_at")), reverse=True)
    if limit:
        events = events[:limit]
    return {"events": events, "total": len(events)}

@router.get("/api/budget/{task_id}")
async def task_budget(task_id: str):
    """任务预算信息 — 旧版前端兼容"""
    from ..server import Utf8JSONResponse, tasks
    task = tasks.get(task_id)
    if not task:
        return Utf8JSONResponse({"error": "任务不存在"}, status_code=404)
    return {
        "task_id": task_id,
        "budget": getattr(task, "budget", None),
        "agent_bids": getattr(task, "agent_bids", []),
    }

@router.post("/api/command")
async def api_command(request: Request):
    from ..server import MAX_TASKS, Message, MessageType, Task, TaskStatus, Utf8JSONResponse, _append_hall, _auto_create_workshop_for_task, _broadcast_and_collect, _orchestrated_flow, _trigger_wakeup, ai_provider, bcast_to_clients, save_state, tasks, ws_agents
    body = await request.json()
    # 兼容旧前端 {input: text} 和 WebSocket {command: text} 两种格式
    command = (body.get("input") or body.get("command", "")).strip()
    if not command:
        return Utf8JSONResponse({"error": "command 不能为空"}, status_code=400)
    title = command[:30] + ("..." if len(command) > 30 else "")
    task = Task(
        title=title, description=command,
        status=TaskStatus.BROADCASTING,
    )
    # V-12 修复：任务总数上限，防无限创建耗尽内存/磁盘
    if len(tasks) >= MAX_TASKS:
        return Utf8JSONResponse({"error": f"任务总数已达上限 {MAX_TASKS}，请清理历史任务后再试"}, status_code=429)
    tasks[task.id] = task
    save_state()
    # 广播任务创建
    create_msg = Message(
        type=MessageType.EVENT,
        from_agent="user", content=command,
        task_id=task.id,
        payload={"event": "task_created", "task": task.model_dump()},
    )
    _append_hall(create_msg)
    await bcast_to_clients(create_msg)
    # 老板主流程 ②：广播即**自动新建工作间**（幂等；失败不影响广播）。
    # ③ 一级讨论就在这个房间里进行；④ 选成员由用户在房间里点选（AI 初选）。
    _auto_ws = _auto_create_workshop_for_task(task, command)
    if _auto_ws is not None:
        create_msg.payload["workshop_id"] = _auto_ws.workshop_id
    # v7: 有 AI Provider 时走 Orchestrator 智能调度
    if ai_provider:
        print(f"[api_command] 调度 {task.id}, ws_agents={list(ws_agents)}", flush=True)
        asyncio.create_task(_orchestrated_flow(task, command))
    else:
        # 无 AI 时回退到传统全局广播
        asyncio.create_task(_broadcast_and_collect(task.id, command))
        asyncio.create_task(_trigger_wakeup(task.id, command))
    return {"success": True, "task_id": task.id, "title": task.title,
            "workshop_id": (_auto_ws.workshop_id if _auto_ws is not None else ""),
            "task": task.model_dump()}
