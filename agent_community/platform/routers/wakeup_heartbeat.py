# -*- coding: utf-8 -*-
"""唤醒与心跳一族（router 端点组）

## 端点（7 个）

```
POST /api/wakeup/trigger
POST /api/wakeup/mutual
GET /api/wakeup/status
POST /api/wakeup/response
POST /api/heartbeat
GET /api/heartbeat
GET /api/heartbeat/stale
```

## 为什么在 `routers/` 里

2026-10-21 从 `server.py` 整块搬出（审计点名"巨无霸"结构债）。**函数体原样搬，行为不变**；
`server` 里的全局在**函数体内惰性 import**（模块级 import 会与 `server` 形成循环，见 `routers/docking.py`）；
注册走 `app.include_router`。

搬出工具：`python tools/move_endpoints.py`（可复用，别手工搬）。
"""
from __future__ import annotations

import json
import time
from uuid import uuid4

from fastapi import APIRouter, Request

router = APIRouter()

@router.post("/api/wakeup/trigger")
async def api_wakeup_trigger(request: Request):
    """手动触发唤醒：向指定任务的所有 Harness 发送唤醒消息"""
    from ..server import HarnessStatus, Utf8JSONResponse, _register_wakeup_inflight, _write_pipe_request, harness_manager, pipe_request_to_task, tasks
    body = await request.json()
    task_id = body.get("task_id", "")
    harness_ids = body.get("harness_ids", [])  # 可选，指定要唤醒的 Harness；留空则全部
    if not task_id or task_id not in tasks:
        return Utf8JSONResponse({"error": "task not found"}, status_code=404)
    task = tasks[task_id]
    command = task.description
    # 收集目标 Harness
    harnesses = []
    for sess in harness_manager.sessions.values():
        if sess.status != HarnessStatus.ONLINE:
            continue
        if harness_ids and sess.harness_id not in harness_ids:
            continue
        wakeup_cfg = sess.metadata.get("wakeup", {})
        harnesses.append({
            "harness_id": sess.harness_id,
            "harness_name": sess.info.harness_name,
            "wakeup_method": wakeup_cfg.get("wakeup_method", "clipboard"),
            "wakeup_url": wakeup_cfg.get("wakeup_url", sess.info.callback_url or ""),
            "wakeup_dir": wakeup_cfg.get("wakeup_dir", ""),
            "callback_url": sess.info.callback_url or "",
            "agent_id": sess.agent_id,
        })
    if not harnesses:
        return {"success": False, "message": "无在线 Harness 可唤醒"}
    _register_wakeup_inflight(task_id, [h["harness_id"] for h in harnesses])  # V-16：手动唤醒同样登记名单
    msg_id = _write_pipe_request(
        "wakeup-agent", "wakeup_task", task_id,
        payload={"command": command, "harnesses": harnesses},
    )
    pipe_request_to_task[msg_id] = task_id
    return {
        "success": True,
        "task_id": task_id,
        "harness_count": len(harnesses),
        "harnesses": [h["harness_id"] for h in harnesses],
    }

@router.post("/api/wakeup/mutual")
async def api_wakeup_mutual(request: Request):
    """互助唤醒：一个 Harness 内的 AI 请求唤醒其他 Harness
    请求格式：
    {
        "from_harness_id": "cursor-001",
        "target_harness_ids": ["示例harness-a", "示例harness-b"],
        "task_title": "需要帮忙分析这个函数",
        "task_description": "详细的上下文描述...",
        "reason": "需要视觉能力来理解截图"
    }
    """
    from ..server import HarnessStatus, Utf8JSONResponse, _write_pipe_request, harness_manager, pipe_request_to_task
    body = await request.json()
    from_harness_id = body.get("from_harness_id", "")
    target_ids = body.get("target_harness_ids", [])
    task_title = body.get("task_title", "互助唤醒请求")
    task_description = body.get("task_description", "")
    reason = body.get("reason", "")
    if not from_harness_id:
        return Utf8JSONResponse({"error": "from_harness_id required"}, status_code=400)
    if not target_ids:
        return Utf8JSONResponse({"error": "target_harness_ids required"}, status_code=400)
    # 查找目标 Harness 的完整信息
    target_harnesses = []
    for tid in target_ids:
        sess = harness_manager.sessions.get(tid)
        if sess and sess.status == HarnessStatus.ONLINE:
            wakeup_cfg = sess.metadata.get("wakeup", {})
            target_harnesses.append({
                "harness_id": sess.harness_id,
                "harness_name": sess.info.harness_name,
                "wakeup_method": wakeup_cfg.get("wakeup_method", "clipboard"),
                "wakeup_url": wakeup_cfg.get("wakeup_url", sess.info.callback_url or ""),
                "wakeup_dir": wakeup_cfg.get("wakeup_dir", ""),
                "callback_url": sess.info.callback_url or "",
                "agent_id": sess.agent_id,
            })
        else:
            # 离线 Harness 也加入（可能被剪贴板方式唤醒）
            target_harnesses.append({
                "harness_id": tid,
                "harness_name": tid,
                "wakeup_method": "clipboard",
                "wakeup_url": "",
                "wakeup_dir": "",
                "callback_url": "",
                "agent_id": f"harness-{tid}",
            })
    if not target_harnesses:
        return {"success": False, "message": "所有目标 Harness 均不在线"}
    # 创建一个临时 task_id 用于追踪
    task_id = f"mutual-{uuid4().hex[:8]}"
    # 写入 mutual_wakeup 消息给 WakeupAgent
    msg_id = _write_pipe_request(
        "wakeup-agent", "mutual_wakeup", task_id,
        payload={
            "from_harness_id": from_harness_id,
            "target_harness_ids": target_ids,
            "target_harnesses": target_harnesses,
            "task_description": f"{task_title}\n{task_description}",
            "reason": reason,
        },
    )
    pipe_request_to_task[msg_id] = task_id
    return {
        "success": True,
        "task_id": task_id,
        "from_harness_id": from_harness_id,
        "target_count": len(target_harnesses),
        "targets": [h["harness_id"] for h in target_harnesses],
    }

@router.get("/api/wakeup/status")
async def api_wakeup_status():
    """查看唤醒 Agent 和所有 Harness 的唤醒配置"""
    from ..server import HarnessStatus, agents, harness_manager
    harness_wakeup = []
    for sess in harness_manager.sessions.values():
        wakeup_cfg = sess.metadata.get("wakeup", {})
        harness_wakeup.append({
            "harness_id": sess.harness_id,
            "harness_name": sess.info.harness_name,
            "status": sess.status.value,
            "wakeup_method": wakeup_cfg.get("wakeup_method", sess.info.wakeup_method.value if sess.info.wakeup_method else "clipboard"),
            "wakeup_url": wakeup_cfg.get("wakeup_url", sess.info.wakeup_url or sess.info.callback_url or ""),
            "wakeup_dir": wakeup_cfg.get("wakeup_dir", sess.info.wakeup_dir or ""),
            "ai_model": sess.info.ai.model_name,
            "agent_id": sess.agent_id,
        })
    return {
        "wakeup_agent_registered": "wakeup-agent" in agents,
        "total_harnesses": len(harness_manager.sessions),
        "online_harnesses": sum(1 for s in harness_manager.sessions.values() if s.status == HarnessStatus.ONLINE),
        "harnesses": harness_wakeup,
    }

@router.post("/api/wakeup/response")
async def api_wakeup_response(request: Request):
    """v5 新增：供外部 Harness 喊人专员回调举手判断结果。
    请求体：
    {
        "harness_id": "xxx",
        "harness_name": "xxx",
        "task_id": "xxx",
        "hand_raised": true/false,
        "capability_claim": "能贡献的能力和角色（100字以内）",
        "model_used": "使用的 AI 模型名称"
    }
    """
    from ..server import Utf8JSONResponse, _clear_wakeup_inflight, _handle_wakeup_result, _wakeup_inflight, harness_manager
    body = await request.json()
    harness_id = body.get("harness_id", "unknown")
    harness_name = body.get("harness_name", harness_id)
    task_id = body.get("task_id", "")
    hand_raised = body.get("hand_raised", False)
    capability_claim = body.get("capability_claim", "")
    model_used = body.get("model_used", "")
    if not task_id:
        return Utf8JSONResponse({"error": "task_id required"}, status_code=400)
    # V-16 归属校验：仅接受平台本次唤醒流程内、且已注册的 harness 回报
    inflight = _wakeup_inflight.get(task_id)
    if not inflight or time.time() > inflight.get("expire_ts", 0):
        if inflight:
            _clear_wakeup_inflight(task_id)
        print(f"[安全][WakeupResponse] {harness_id} 回报 task={task_id} 不在唤醒流程中，已拒绝", flush=True)
        return Utf8JSONResponse({"error": "该任务当前无进行中的唤醒流程，拒绝回报"}, status_code=403)
    if harness_id not in inflight.get("hids", set()):
        print(f"[安全][WakeupResponse] {harness_id} 不在 task={task_id} 的唤醒名单中，已拒绝", flush=True)
        return Utf8JSONResponse({"error": "该 harness 不在本次唤醒名单中，拒绝回报"}, status_code=403)
    sess = harness_manager.sessions.get(harness_id)
    if not sess:
        print(f"[安全][WakeupResponse] {harness_id} 未注册，已拒绝举手回报", flush=True)
        return Utf8JSONResponse({"error": "harness 未注册，拒绝回报"}, status_code=403)
    reg_name = getattr(sess.info, "harness_name", "") or ""
    if reg_name and harness_name and str(harness_name) != str(reg_name):
        print(f"[安全][WakeupResponse] {harness_id} 回报名称 {harness_name!r} 与注册名 {reg_name!r} 不一致，已拒绝", flush=True)
        return Utf8JSONResponse({"error": "回报 harness_name 与注册信息不一致"}, status_code=403)
    print(
        f"[WakeupResponse] {harness_name} ({harness_id}) "
        f"task={task_id} hand_raised={hand_raised}"
    )
    # 收集举手/拒绝信息，走原有 _handle_wakeup_result 流程
    if hand_raised:
        hands = [{
            "harness_id": harness_id,
            "harness_name": harness_name,
            "capability_claim": capability_claim,
            "model_used": model_used,
            "protocol": "http_callback",
        }]
        await _handle_wakeup_result(task_id, {
            "hands": hands,
            "rejected": [],
            "timeouts": [],
            "errors": [],
        })
    else:
        rejected = [{
            "harness_id": harness_id,
            "harness_name": harness_name,
            "reason": "refused",
        }]
        await _handle_wakeup_result(task_id, {
            "hands": [],
            "rejected": rejected,
            "timeouts": [],
            "errors": [],
        })
    return {"success": True, "task_id": task_id, "hand_raised": hand_raised}

@router.post("/api/heartbeat")
async def api_heartbeat(request: Request):
    """Agent 周期心跳上报。

    body: {agent_id?, harness_id, harness_name?, status?: "alive"|"idle"|"busy", interval?: 秒, note?}
    - harness 必须已注册（仿 /api/wakeup/response 归属校验），未注册拒绝 403；
    - 未传 agent_id 时用 harness_id 兜底；
    - 纯内存登记，不落盘、不阻塞主流程（落盘交给 save_state 既有链路）。
    """
    from ..server import Utf8JSONResponse, harness_manager, heartbeat_registry
    try:
        body = await request.json()
    except Exception:
        return Utf8JSONResponse({"error": "body 必须为 JSON"}, status_code=400)
    harness_id = str(body.get("harness_id", "")).strip()
    if not harness_id:
        return Utf8JSONResponse({"error": "harness_id required"}, status_code=400)
    sess = harness_manager.sessions.get(harness_id)
    if not sess:
        print(f"[安全][Heartbeat] {harness_id} 未注册，拒绝心跳上报", flush=True)
        return Utf8JSONResponse({"error": "harness 未注册，拒绝上报"}, status_code=403)
    reg_name = getattr(sess.info, "harness_name", "") or ""
    body_name = str(body.get("harness_name", "") or "").strip()
    if reg_name and body_name and body_name != reg_name:
        return Utf8JSONResponse({"error": "harness_name 与注册信息不一致"}, status_code=403)
    agent_id = str(body.get("agent_id", "") or harness_id).strip()
    try:
        interval = float(body.get("interval") or 30.0)
    except (TypeError, ValueError):
        interval = 30.0
    status = str(body.get("status") or "alive").strip()
    note = str(body.get("note") or "").strip()
    # ⚠️ 2026-10-08 修：`interval` 之前**解析了却从未使用**（`beat()` 不接收 interval，只有
    # `register()` 会写进记录）⇒ 文档承诺的 `interval?: 秒` 实际无效，调用方**无法缩短失联判定窗口**
    # （实测踩到：想用 interval=1 秒造"失联"来验证掉线抢救链，结果 4 秒后仍判在线，
    #  因为记录里的 interval 一直是默认 30 秒 ⇒ 要 60 秒才失联）。
    # 现在：带 interval 时先更新该 agent 的心跳配置，再 beat。
    try:
        _prev = heartbeat_registry.status(agent_id) or {}
        if abs(float(interval) - float(_prev.get("interval") or 0)) > 1e-6:
            heartbeat_registry.register(agent_id, harness_id=harness_id,
                                        harness_name=str(body.get("harness_name") or ""),
                                        interval=interval)
    except Exception as _e:  # noqa: BLE001
        print(f"[heartbeat] interval 更新失败(不影响心跳): {_e}", flush=True)
    rec = heartbeat_registry.beat(agent_id, status=status, note=note, harness_id=harness_id)
    return {"success": True, "agent_id": agent_id, "harness_id": harness_id,
            "status": rec.get("status"), "last_seen": rec.get("last_seen")}

@router.get("/api/heartbeat")
async def api_heartbeat_status():
    """查看全部 Agent 心跳登记：在线 / 失联概览。"""
    from ..server import heartbeat_registry
    return {
        "total": len(heartbeat_registry._registry),
        "online": heartbeat_registry.online(),
        "stale": heartbeat_registry.stale(),
        "timeout_scale": heartbeat_registry.timeout_scale,
    }

@router.get("/api/heartbeat/stale")
async def api_heartbeat_stale():
    """失联（超时未上报）Agent 列表。孤儿任务回收复用 V-18 端点。"""
    from ..server import heartbeat_registry
    stale = heartbeat_registry.stale()
    return {
        "stale_count": len(stale),
        "stale": stale,
        "hint": "任务卡死判定与回收复用 GET /api/tasks/stale 与 POST /api/tasks/recover-stale",
    }
