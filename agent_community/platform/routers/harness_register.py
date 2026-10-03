"""platform/routers/harness_register：Harness 注册与生命周期（V-13 自 harness.py 拆分）。

承载 pre-register / probe-register / register / launch / heartbeat / DELETE 等端点。
拆分纪律：路由路径、参数、响应体与拆分前逐字一致（纯搬迁，零行为变更）。
"""
from __future__ import annotations
from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect
from ..protocol import HarnessInfo
from ..protocol import Message
from ..protocol import MessageType
from ..protocol import WakeupMethod
import asyncio
from .. import harness_launcher
from ..harness_adapter import harness_manager
from ..harness_adapter import harness_to_agent_card
from ..api_wakeup import probe_http_api
from ..state import _bridge_tests_inflight, agents, assistant_history, assistant_history_max, pending_activations, pending_bridge_tests, pending_pre_register, pending_tasks, workshops
from ..audit import audit_log as _audit_log
from ..identity import issue_agent_token as _issue_agent_token

router = APIRouter()

@router.post("/api/harness/pre-register")
async def harness_pre_register(request: Request):
    _audit_log.record("harness.pre_register", actor="user", target="", detail="初步注册")
    from ..server import Utf8JSONResponse, now_iso
    """阶段1：外端 Agent 初步消息注册 —— 向平台声明自己的部分信息（不必完整技术字段）。
    外端 Agent 主动告知平台「我要接入，我叫什么、大概是什么类型、有什么线索」，
    平台记下这个待完善注册意图，之后（阶段2）基于这些线索探测补齐技术配置。
    body: {
      "harness_id": "示例Harness-X",            # 必填：唯一标识
      "harness_name": "示例Harness-X",          # 必填：显示名
      "harness_type": "desktop-agent",   # 可选：类型猜测
      "process_hint": "示例进程名",       # 可选：进程名线索（供平台探测）
      "port_hint": 3721,                 # 可选：HTTP API 端口线索
      "path_hint": "",                   # 可选：安装路径线索
      "capabilities": [...],             # 可选：能力声明
      "description": "..."               # 可选：说明
    }
    返回 {"success": true, "pre_registered": true, "pending_probe": true, ...}
    """
    body = await request.json()
    hid = (body.get("harness_id") or "").strip()
    name = (body.get("harness_name") or hid).strip()
    if not hid:
        return Utf8JSONResponse({"error": "harness_id required"}, status_code=400)
    # 存为待探测的初步注册（放到 pending_pre_register 全局）
    entry = {
        "harness_id": hid,
        "harness_name": name,
        "harness_type": body.get("harness_type", "desktop-agent"),
        "process_hint": body.get("process_hint", ""),
        "port_hint": body.get("port_hint"),
        "path_hint": body.get("path_hint", ""),
        "capabilities": body.get("capabilities", []),
        "ai": body.get("ai", {}),
        "description": body.get("description", ""),
        "tools": body.get("tools", []),
        "ts": now_iso(),
    }
    pending_pre_register[hid] = entry
    print(f"[pre-register] {hid} 已初步注册（待探测补齐）", flush=True)
    return Utf8JSONResponse({
        "success": True,
        "pre_registered": True,
        "pending_probe": True,
        "harness_id": hid,
        "next": "POST /api/harness/probe-register 让平台探测补齐并正式注册",
    })

@router.post("/api/harness/probe-register")
async def harness_probe_register(request: Request):
    _audit_log.record("harness.probe_register", actor="user", target="", detail="探测注册")
    from ..server import Utf8JSONResponse, _append_hall, bcast_to_clients, save_state, validate_harness_api_url
    """阶段2：平台基于初步注册信息探测对象，补齐技术配置，完成正式注册。
    body: {"harness_id": "示例Harness-X", "process_hint": "...", "port_hint": 3721, ...}
    （可复用 pre-register 的线索，也可这里直接给）
    平台用 ProbeHarnessTool 探测真实接入方式（http_api/file_poll/acp），
    自动补齐 wakeup_method/api_base_url 等，然后走正式注册。
    """
    body = await request.json()
    hid = (body.get("harness_id") or "").strip()
    if not hid:
        return Utf8JSONResponse({"error": "harness_id required"}, status_code=400)
    # 合并初步注册线索
    pre = pending_pre_register.get(hid, {})
    process_hint = body.get("process_hint") or pre.get("process_hint", "") or hid
    port_hint = body.get("port_hint") or pre.get("port_hint")
    path_hint = body.get("path_hint") or pre.get("path_hint", "")
    # ── MCP 客户端类型：显式声明 wakeup_method=mcp 时跳过进程/端口探测，直连远程 MCP ──
    mcp_url = (str(body.get("api_base_url") or "").strip()
               or str(pre.get("api_base_url") or "").strip())
    is_mcp = (str(body.get("wakeup_method") or "").strip().lower() == "mcp"
              or str(pre.get("wakeup_method") or "").strip().lower() == "mcp")
    if is_mcp and not mcp_url:
        return Utf8JSONResponse({"error": "mcp 类型需要 api_base_url（远程 MCP 服务器地址，如 http://localhost:9527）"}, status_code=400)
    if is_mcp:
        name = pre.get("harness_name", hid)
        ok, err = validate_harness_api_url(mcp_url)
        if not ok:
            return Utf8JSONResponse({"error": f"api_base_url 校验失败: {err}"}, status_code=400)
        info = HarnessInfo(**{
            "harness_id": hid,
            "harness_name": name,
            "harness_type": pre.get("harness_type", "mcp-server"),
            "wakeup_method": "mcp",
            "api_base_url": mcp_url,
            "ai": pre.get("ai") or {"model_name": "unknown", "provider": "mcp",
                                    "capabilities": pre.get("capabilities", []), "description": ""},
            "tools": pre.get("tools", []),
            "description": pre.get("description", f"{name} MCP 客户端接入"),
        })
        info = await _maybe_pull_mcp_tools(info)
        sess, bridge = harness_manager.register(info)
        card = harness_to_agent_card(info)
        agents[card.agent_id] = card
        sys_msg = Message(
            type=MessageType.SYSTEM, from_agent="system",
            content=f"外部 Harness「{info.harness_name}」已接入（MCP 客户端，AI: {info.ai.model_name}）",
            payload={"harness": info.model_dump(), "agent": card.model_dump()},
        )
        _append_hall(sys_msg)
        await bcast_to_clients(sys_msg)
        save_state()
        pending_pre_register.pop(hid, None)
        return Utf8JSONResponse({
            "success": True,
            "harness_id": hid,
            "agent_id": card.agent_id,
            "wakeup_method": info.wakeup_method.value,
            "api_base_url": mcp_url,
            "probe": "mcp(client):skip-probe",
        })
    # 用 ProbeHarnessTool 探测
    from ..tools.assistant_tools import ProbeHarnessTool
    probe = ProbeHarnessTool()
    probe_result = await probe.execute(process_hint=process_hint, port_hint=port_hint, path_hint=path_hint)
    probe_text = probe_result.content if probe_result.success else ""
    # 解析探测结果，构造正式注册信息
    name = pre.get("harness_name", hid)
    info_dict = {
        "harness_id": hid,
        "harness_name": name,
        "harness_type": pre.get("harness_type", "desktop-agent"),
        "wakeup_method": pre.get("wakeup_method", "file_poll"),  # 默认，下面按探测覆盖
        "ai": pre.get("ai") or {"model_name": "unknown", "provider": "unknown", "capabilities": pre.get("capabilities", []), "description": ""},
        "tools": pre.get("tools", []),
        "description": pre.get("description", f"{name} 自动探测接入"),
    }
    # 从探测结果提取 http_api 配置
    detected = False
    if "http_api" in probe_text:
        import re as _re
        m = _re.search(r"api_base_url:\s*(http://[\d.:]+)", probe_text)
        if m:
            info_dict["wakeup_method"] = "http_api"
            info_dict["api_base_url"] = m.group(1)
            info_dict["api_message_path"] = "/message"
            detected = True
    if not detected and port_hint:
        # 显式给了端口线索 → 假设 http_api
        info_dict["wakeup_method"] = "http_api"
        info_dict["api_base_url"] = f"http://127.0.0.1:{port_hint}"
        info_dict["api_message_path"] = "/message"
    # 正式注册（复用 register 逻辑的核心：HarnessInfo + harness_manager.register + agent card + save_state）
    if info_dict.get("api_base_url"):
        ok, err = validate_harness_api_url(info_dict["api_base_url"])
        if not ok:
            return Utf8JSONResponse({"error": f"api_base_url 校验失败: {err}", "probe": probe_text[:500]}, status_code=400)
    try:
        info = HarnessInfo(**info_dict)
    except Exception as e:
        return Utf8JSONResponse({"error": f"构造注册信息失败: {e}", "probe": probe_text[:500]}, status_code=400)
    sess, bridge = harness_manager.register(info)
    card = harness_to_agent_card(info)
    agents[card.agent_id] = card
    sys_msg = Message(
        type=MessageType.SYSTEM, from_agent="system",
        content=f"外部 Harness「{info.harness_name}」已接入（AI: {info.ai.model_name}）",
        payload={"harness": info.model_dump(), "agent": card.model_dump()},
    )
    _append_hall(sys_msg)
    await bcast_to_clients(sys_msg)
    save_state()
    pending_pre_register.pop(hid, None)
    return Utf8JSONResponse({
        "success": True,
        "harness_id": hid,
        "agent_id": card.agent_id,
        "wakeup_method": info.wakeup_method.value,
        "api_base_url": getattr(info, "api_base_url", ""),
        "probe": probe_text[:500],
    })

@router.post("/api/harness/register")
async def register_harness(request: Request):
    from ..server import Utf8JSONResponse, _append_hall, _merge_experience_index, bcast_to_clients, save_state, validate_acp_command, validate_callback_url, validate_harness_api_url, validate_wakeup_dir
    """外部 Harness 注册接入"""
    body = await request.json()
    try:
        info = HarnessInfo(**body)
    except Exception as e:
        return Utf8JSONResponse({"error": f"参数错误: {e}"}, status_code=400)
    # ── 经验包登记（V1）：skill_index / knowledge_index 可放顶层或 metadata 内 ──
    _merge_experience_index(body, info)
    # 安全校验：回调 URL（防 SSRF）+ wakeup_dir 路径加固
    for ufield in ("callback_url", "wakeup_url"):
        u = getattr(info, ufield, "") or ""
        ok, err = validate_callback_url(u)
        if not ok:
            return Utf8JSONResponse({"error": f"{ufield} 校验失败: {err}"}, status_code=400)
    if getattr(info, "api_base_url", ""):
        ok, err = validate_harness_api_url(info.api_base_url)
        if not ok:
            return Utf8JSONResponse({"error": f"api_base_url 校验失败: {err}"}, status_code=400)
    ok, err = validate_wakeup_dir(info.wakeup_dir or "")
    if not ok:
        return Utf8JSONResponse({"error": f"wakeup_dir 校验失败: {err}"}, status_code=400)
    # V-4 修复：acp_command 校验（防 cmd /c 等解释器包装导致的 RCE）
    # MCP 客户端类型不需要本地拉起命令，豁免该校验（api_base_url 已做 SSRF 校验）
    if getattr(info, "wakeup_method", None) != WakeupMethod.MCP:
        ok, err = validate_acp_command(info.acp_command or "")
        if not ok:
            return Utf8JSONResponse({"error": f"acp_command 校验失败: {err}"}, status_code=400)
    # 注册到 manager
    sess, bridge = harness_manager.register(info)
    # ── MCP 客户端类型：tools 为空时从远程 MCP 服务器 tools/list 动态拉取并持久化 ──
    if getattr(info, "wakeup_method", None) == WakeupMethod.MCP:
        info = await _maybe_pull_mcp_tools(info)
        if info.tools:
            harness_manager.register(info)  # 重新注册以持久化动态 tools
    elif getattr(info, "api_base_url", ""):
        # ── 自动探测 HTTP API（http_api 类）：注册时若带 api_base_url，探测确认可用性
        #    并自动把 wakeup_method 升级为 http_api（平台就能直接 HTTP 推送唤醒，而非只写文件）
        _probe_ok, _probe_detail = probe_http_api(info.api_base_url, info.api_message_path or "/message")
        if _probe_ok:
            info.wakeup_method = WakeupMethod.HTTP_API
            print(f"[api_wakeup] {info.harness_id} 已自动探测到 HTTP API，设为 http_api：{_probe_detail}", flush=True)
            harness_manager.register(info)  # 重新注册以持久化更新
        else:
            print(f"[api_wakeup] {info.harness_id} HTTP API 探测失败：{_probe_detail}", flush=True)
    # 映射为 AgentCard 并注册到平台
    card = harness_to_agent_card(info)
    agents[card.agent_id] = card
    sys_msg = Message(
        type=MessageType.SYSTEM,
        from_agent="system",
        content=f"外部 Harness「{info.harness_name}」已接入（AI: {info.ai.model_name}）",
        payload={"harness": info.model_dump(), "agent": card.model_dump()},
    )
    _append_hall(sys_msg)
    await bcast_to_clients(sys_msg)
    # 同步注入平台助手对话上下文：让平台 Agent 在直聊窗口能了解到新接入的外部 Agent 目标信息
    assistant_history.append({
        "role": "system",
        "content": f"[系统] 外部 Harness「{info.harness_name}」已接入平台（AI: {info.ai.model_name}，"
                   f"harness_id: {info.harness_id or card.agent_id}）。后续沟通架桥可基于该目标进行。",
    })
    if len(assistant_history) > assistant_history_max:
        del assistant_history[:-assistant_history_max]
    save_state()
    # P1 身份凭证：为 Harness 签发 agent-token（幂等：已签发则沿用，不换新）
    existing_token = (info.metadata or {}).get("agent_token") or ""
    _token_pre = bool(existing_token)
    if not existing_token:
        existing_token = _issue_agent_token(info.harness_id)
        meta = dict(info.metadata or {})
        meta["agent_token"] = existing_token
        info.metadata = meta
        save_state()
    # P2-1 审计：harness 注册（含 agent-token 签发）
    _audit_log.record(
        "harness.register", actor="system", target=info.harness_id,
        detail=f"name={getattr(info, 'harness_name', '')} token={'reuse' if _token_pre else 'new'}",
    )
    return {
        "success": True,
        "harness_id": info.harness_id,
        "agent_id": card.agent_id,
        "card": card.model_dump(),
        "agent_token": existing_token,
    }

@router.post("/api/harness/launch")
async def api_harness_launch(request: Request):
    _audit_log.record("harness.launch", actor="user", target="", detail="启动 harness 进程")
    from ..server import Utf8JSONResponse
    """v1 新增：手动拉起 harness 进程（平台 Agent 学会「出去启动 harness」的入口）。
    body: {"harness_id": "示例Harness-X", "wait_online": true, "timeout": 60}
    - wait_online=true 时轮询等待上线（默认 60s）
    - 启动历史可在 /api/harness/launch-log 查看
    """
    try:
        body = await request.json()
    except Exception:
        return Utf8JSONResponse({"error": "JSON body required"}, status_code=400)
    harness_id = (body.get("harness_id") or "").strip()
    if not harness_id:
        return Utf8JSONResponse({"error": "harness_id required"}, status_code=400)
    wait_online = bool(body.get("wait_online", True))
    timeout = float(body.get("timeout", 60.0))
    sess = harness_manager.sessions.get(harness_id)
    info = sess.info if sess and sess.info else None
    if info is None:
        return Utf8JSONResponse({"error": f"harness {harness_id} 未注册"}, status_code=404)
    if not (info.acp_command or "").strip():
        return Utf8JSONResponse(
            {"error": f"harness {harness_id} 未配置 acp_command，平台无法拉起"},
            status_code=400,
        )
    ok, msg = await harness_launcher.ensure_harness_online(harness_id, timeout=timeout)
    return {
        "success": ok,
        "harness_id": harness_id,
        "message": msg,
        "online": harness_launcher._is_online(harness_id),
    }

@router.get("/api/harness/launch-log")
async def api_harness_launch_log():
    """v1 新增：查看 harness 启动历史。"""
    return {"success": True, "entries": harness_launcher.get_launch_log()}

@router.post("/api/harness/heartbeat")
async def harness_heartbeat(harness_id: str = ""):
    _audit_log.record("harness.heartbeat", actor="user", target="", detail="心跳")
    from ..server import Utf8JSONResponse
    """Harness 心跳保活"""
    # 支持 query param 和 JSON body
    if not harness_id:
        return Utf8JSONResponse({"error": "harness_id required（请通过 URL query 传入，如 /api/harness/heartbeat?harness_id=xxx）"}, status_code=400)
    harness_manager.heartbeat(harness_id)
    return {"success": True, "harness_id": harness_id}

@router.post("/api/harness/activation-result")
async def harness_activation_result(request: Request):
    _audit_log.record("harness.activation_result", actor="user", target="", detail="激活回报")
    from ..server import _assign_tasks
    """harness 桥回报激活结果。"""
    body = await request.json()
    ws = workshops.get(body.get("workshop_id", ""))
    if ws:
        mid = body.get("member_id")
        for m in ws.members:
            if m.member_id == mid:
                m.status = body.get("status", "entered")
                break
        # 全部进入后，自动派发工作任务
        if ws.members and all(m.status == "entered" for m in ws.members):
            asyncio.create_task(_assign_tasks(ws))
    return {"success": True}

@router.delete("/api/harness/{harness_id}")
async def unregister_harness(harness_id: str):
    _audit_log.record("harness.unregister", actor="user", target=harness_id, detail="注销 harness")
    from ..server import Utf8JSONResponse, _append_hall, _stop_bridge_processes, _trash_bridge_dir, bcast_to_clients, save_state
    """注销 Harness（删除注册信息 + 桥坐标 + 队列残留 + 终止桥进程 + 桥目录进回收站）"""
    sess = harness_manager.sessions.get(harness_id)
    if not sess:
        return Utf8JSONResponse({"error": "harness not found"}, status_code=404)
    bridge_dir = sess.info.bridge_dir or ""
    # 移除 agent 注册
    agents.pop(sess.agent_id, None)
    harness_manager.unregister(harness_id)
    # 清理该 harness 在待激活/待任务/待桥测试队列里的残留
    pending_activations.pop(harness_id, None)
    pending_tasks.pop(harness_id, None)
    pending_bridge_tests.pop(harness_id, None)
    # 清理桥：终止桥进程（后台线程），桥目录移入回收站
    bridge_cleanup = await asyncio.to_thread(_stop_bridge_processes, harness_id, bridge_dir)
    trash_result = await asyncio.to_thread(_trash_bridge_dir, bridge_dir)
    sys_msg = Message(
        type=MessageType.SYSTEM, from_agent="system",
        content=f"Harness「{harness_id}」已注销（注册信息与桥信息已删除，桥进程已终止，桥目录已清理）",
    )
    _append_hall(sys_msg)
    await bcast_to_clients(sys_msg)
    save_state()
    return {
        "success": True, "deleted": harness_id,
        "bridge_cleanup": bridge_cleanup, "trash": trash_result,
    }

