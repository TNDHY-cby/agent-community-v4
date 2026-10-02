"""platform/routers/harness：Harness 注册/心跳/消息/桥端点组（V-9 自 server.py 拆分）。

拆分原则：
- 可变全局状态统一引用 ..state（python -m 双副本共享同一组对象）
- server 内部辅助函数/常量在函数体内延迟 from ..server import（避免循环导入）
- ai_provider / ai_provider_config 由 __main__ 副本持有，经 _sv() 访问
"""
from __future__ import annotations

import sys

from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect

router = APIRouter()


def _sv():
    """返回活跃 server 副本（__main__，uvicorn 实际运行的实例）模块。"""
    return sys.modules["__main__"]


# ── 独立模块引用（与 server.py 共享同一实例）──
from ..protocol import HarnessInfo
from ..protocol import HarnessMessage
from ..protocol import HarnessStatus
from ..protocol import Message
from ..protocol import MessageType
from pathlib import Path
from ..protocol import WakeupMethod
from fastapi import WebSocketDisconnect
from ..ai_external import run_ai_call as ai_external_run_ai_call
import asyncio
from datetime import datetime
from .. import harness_launcher
from ..harness_adapter import harness_manager
from ..harness_adapter import harness_to_agent_card
import json
import os
from ..api_wakeup import probe_http_api
from ..api_wakeup import send_http_api_message
import time
from uuid import uuid4

# ── 共享可变状态（..state 单例，双副本一致）──
from ..state import _bridge_tests_inflight, agents, assistant_history, assistant_history_max, pending_activations, pending_bridge_tests, pending_pre_register, pending_tasks, workshops
from ..protocol import HarnessTool


async def _maybe_pull_mcp_tools(info: HarnessInfo) -> HarnessInfo:
    """MCP 客户端类型：注册时 tools 为空则从远程 MCP 服务器 tools/list 动态拉取。

    失败不阻断注册（工具为空时平台按“无工具外端 Agent”对待，调用时仍可回退重拉）。
    """
    if getattr(info, "wakeup_method", None) != WakeupMethod.MCP:
        return info
    if not (getattr(info, "api_base_url", "") or "").strip():
        return info
    if info.tools:
        return info
    try:
        from ..mcp_client import mcp_list_tools

        remote = await mcp_list_tools(info.api_base_url)
    except Exception as e:
        print(f"[mcp_client] {info.harness_id} tools/list 拉取失败: {e}", flush=True)
        return info
    if not remote:
        return info
    tools: list[HarnessTool] = []
    for t in remote:
        if not isinstance(t, dict) or not t.get("name"):
            continue
        tools.append(
            HarnessTool(
                name=str(t["name"]),
                description=str(t.get("description") or ""),
                parameters=t.get("inputSchema") or {},
                capability_tag=str(t.get("capability_tag") or ""),
            )
        )
    if tools:
        info.tools = tools
        print(f"[mcp_client] {info.harness_id} 动态拉取 tools/list 共 {len(tools)} 个工具", flush=True)
    return info

# ── P1 身份凭证 / P2-1 审计（自 retired 副本回收）──
from ..audit import audit_log as _audit_log
from ..identity import check_request_token as _check_request_token
from ..identity import issue_agent_token as _issue_agent_token


def _harness_identity_check(harness_id: str, headers):
    """P1 身份凭证：目标 harness 已签发 agent-token 时，校验 X-Agent-Token 头。

    返回 None 表示通过；返回响应对象表示拒绝（401 缺头 / 403 校验失败）。
    向后兼容：未签发 token 的存量旧 harness 直接放行，不破坏既有桥。
    """
    from ..server import Utf8JSONResponse
    if not harness_id:
        return None
    sess = harness_manager.sessions.get(harness_id)
    if not sess:
        # 未知 harness：交由后续既有逻辑处理
        return None
    verdict = _check_request_token(
        harness_id, getattr(sess.info, "metadata", None) or {}, headers
    )
    if verdict is None:
        return None
    _status, _message = verdict
    return Utf8JSONResponse({"error": _message}, status_code=_status)

@router.post("/api/harness/pre-register")
async def harness_pre_register(request: Request):
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
    from ..server import Utf8JSONResponse
    """Harness 心跳保活"""
    # 支持 query param 和 JSON body
    if not harness_id:
        return Utf8JSONResponse({"error": "harness_id required（请通过 URL query 传入，如 /api/harness/heartbeat?harness_id=xxx）"}, status_code=400)
    harness_manager.heartbeat(harness_id)
    return {"success": True, "harness_id": harness_id}

@router.post("/api/harness/message")
async def harness_message(request: Request):
    from ..server import Utf8JSONResponse, _sanitize_harness_content
    """接收来自 Harness 的消息回复"""
    body = await request.json()
    try:
        msg = HarnessMessage(**body)
    except Exception as e:
        return Utf8JSONResponse({"error": f"消息格式错误: {e}"}, status_code=400)
    # P1 身份凭证：目标 harness 已签发 token 时，强制校验 X-Agent-Token 头（向后兼容）
    _id_resp = _harness_identity_check(msg.harness_id, request.headers)
    if _id_resp:
        return _id_resp
    # V-14：外部 harness 主动上报内容做提示注入防护（进 pending futures 前）
    if isinstance(msg.content, str) and msg.content.strip():
        msg.content = _sanitize_harness_content(msg.content)
    harness_manager.handle_reply(msg)
    # 更新会话状态
    if msg.harness_id in harness_manager.sessions:
        harness_manager.sessions[msg.harness_id].last_heartbeat = datetime.now().isoformat()
        harness_manager.sessions[msg.harness_id].message_count += 1
    return {"success": True, "msg_id": msg.id}

@router.get("/api/harness/list")
async def list_harnesses():
    """列出所有已接入的 Harness"""
    return {"harnesses": harness_manager.list_sessions()}

@router.get("/api/harness/pending-activations")
async def harness_pending_activations(harness_id: str):
    """harness 桥轮询：领取该 harness 名下的待激活任务。"""
    acts = pending_activations.pop(harness_id, [])
    return {"activations": acts}

@router.post("/api/harness/activation-result")
async def harness_activation_result(request: Request):
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

@router.get("/api/harness/pending-tasks")
async def harness_pending_tasks(harness_id: str):
    """harness 桥轮询：领取该 harness 名下的待执行任务。"""
    tasks = pending_tasks.pop(harness_id, [])
    return {"tasks": tasks}

@router.post("/api/harness/task-result")
async def harness_task_result(request: Request):
    from ..server import _append_msg, _apply_harness_reply, _do_complete_workshop, _do_continue_workshop, _get_decision_mode, _sanitize_harness_content, _settle_vote, save_state
    """harness 桥回报任务结果。"""
    body = await request.json()
    # P1 身份凭证：若该 harness 已签发 token，强制校验 X-Agent-Token（向后兼容）
    _hid0 = str(body.get("harness_id") or "").strip()
    if _hid0:
        _id_resp = _harness_identity_check(_hid0, request.headers)
        if _id_resp:
            return _id_resp
    # ── 委托链结果兑现：无论 ok 真假，都要把回报路由给等待中的 Future ──
    # 否则 bridge.execute_subtask / review_subtask 会空等 300s / 30s 超时，
    # orchestrator.execute_layers 整条任务链表现为“永久卡在 broadcasting”。
    try:
        _hid = str(body.get("harness_id") or "").strip()
        _did = str(body.get("delegation_id") or "").strip()
        if not _hid:
            # 兼容旧桥：按 workshop/member 反查 harness
            _ws0 = workshops.get(body.get("workshop_id", ""))
            _mid0 = body.get("member_id")
            if _ws0 and _mid0:
                for _m in getattr(_ws0, "members", []):
                    if _m.member_id == _mid0:
                        _aid = getattr(_m, "agent_id", "") or ""
                        if _aid:
                            _hid = harness_manager.id_to_harness.get(_aid, "")
                        break
        if _hid and not _did:
            # 兜底：该桥只有一个待决委托时，视为它的结果
            _br = harness_manager.bridges.get(_hid)
            if _br:
                _pkeys = [k for k, _f in list(_br._pending.items()) if not _f.done()]
                if len(_pkeys) == 1:
                    _did = _pkeys[0]
        if _hid:
            _reply_content = _sanitize_harness_content(
                str(body.get("result") or body.get("text") or body.get("reply") or "")
            )  # V-14：外部内容提示注入防护
            _reply_msg = HarnessMessage(
                harness_id=_hid,
                direction="from_harness",
                msg_type="result",
                content=_reply_content,
                task_id=str(body.get("task_id") or ""),
                delegation_id=_did,
                payload={"ok": bool(body.get("ok", True)), "raw": body},
            )
            harness_manager.handle_reply(_reply_msg)
            print(
                f"[harness_reply] 回报路由: harness={_hid} delegation={_did or '-'} ok={body.get('ok', True)}",
                flush=True,
            )
    except Exception as _e:
        print(f"[harness_reply] 回报路由异常: {_e}", flush=True)
    ws = workshops.get(body.get("workshop_id", ""))
    if ws:
        mid = body.get("member_id")
        # ── R3：决策模式回报（组长独裁 decision / 举手表决 vote）──
        # 仅在 review 态且带明确裁决字段时接管收口；普通回报不受影响
        _dm = str(body.get("decision") or "").strip().lower()
        _vt = str(body.get("vote") or "").strip().lower()
        if ws.status == "review" and mid:
            if _dm in ("continue", "complete") and _get_decision_mode(ws.workshop_id) == "leader":
                _append_msg(ws, "notice", f"【组长裁决】组长回报 decision={_dm}，按裁决执行。", zone=3)
                if _dm == "complete":
                    _do_complete_workshop(ws, by="leader")
                else:
                    _do_continue_workshop(ws, by="leader")
                save_state()
            if _vt in ("continue", "complete") and _get_decision_mode(ws.workshop_id) == "vote":
                _settle_vote(ws, mid, _vt)
        # 通用回报接入：harness 通过 task-result 回报结果/发言，统一接入讨论区
        # （http_api 类 harness 也可通过 HTTP 直接回报到这里）
        result = _sanitize_harness_content(
            body.get("result") or body.get("text") or body.get("reply") or ""
        )  # V-14：进入讨论区前做提示注入防护
        hid = body.get("harness_id") or ""
        ok_flag = bool(body.get("ok", True))  # 桥 report_task 回传；旧桥未传默认 True（成功语义）
        # 结构化卡点上报：可选 status(done/blocked/progress/none，大小写不敏感) + summary 短文本
        status = body.get("status") or ""
        summary = body.get("summary") or ""
        if not ok_flag and (status or "").strip().lower() not in ("done", "blocked", "progress"):
            # 系统级失败占位（会话缺失/重建失败等）不是成员真实发言，但必须在讨论区可见：
            # 写入讨论区并标记为失败+系统消息（meta.status=failed / meta.system=True），
            # 同时 notify_leader=False —— 不推进组长事件水位、不参与事件判定，
            # 避免历史死循环「失败回报 → 唤醒组长 → 再次派发」。
            print(f"[harness_reply] {hid} 任务未完成回报（ok=false）已作为失败提示写入讨论区: {result[:100]}", flush=True)
            _apply_harness_reply(body.get("workshop_id", ""), mid or "", result, hid, source="harness_http",
                                 status="failed", summary=summary, notify_leader=False)
        else:
            _apply_harness_reply(body.get("workshop_id", ""), mid or "", result, hid, source="harness_http",
                                 status=status, summary=summary)
    return {"success": True}

@router.post("/api/harness/api-probe")
async def harness_api_probe(request: Request):
    from ..server import Utf8JSONResponse, save_state, validate_harness_api_url
    """探测 harness 自带 HTTP API 是否可用（http_api 类唤醒）。
    body: {"harness_id": "..."} 或 {"base_url": "...", "message_path": "/message"}
    若 base_url 缺省则从该 harness 注册信息取 api_base_url。
    探测成功自动把该 harness 的 wakeup_method 升级为 http_api。
    """
    body = await request.json()
    hid = (body.get("harness_id") or "").strip()
    base_url = (body.get("base_url") or "").strip()
    message_path = (body.get("message_path") or "/message").strip()
    if not base_url and hid:
        sess = harness_manager.sessions.get(hid)
        if sess and sess.info:
            base_url = getattr(sess.info, "api_base_url", "") or ""
            message_path = getattr(sess.info, "api_message_path", "") or message_path
    if not base_url:
        return Utf8JSONResponse({"error": "base_url 或 harness_id 需要提供（且该 harness 已配置 api_base_url）"}, status_code=400)
    ok, err = validate_harness_api_url(base_url)
    if not ok:
        return Utf8JSONResponse({"error": f"base_url 校验失败: {err}"}, status_code=400)
    ok, detail = probe_http_api(base_url, message_path)
    if ok and hid:
        sess = harness_manager.sessions.get(hid)
        if sess and sess.info:
            sess.info.wakeup_method = WakeupMethod.HTTP_API
            sess.info.api_base_url = base_url
            sess.info.api_message_path = message_path
            harness_manager.register(sess.info)
            save_state()
    return {"success": ok, "harness_id": hid, "detail": detail}

@router.post("/api/harness/api-message")
async def harness_api_message(request: Request):
    from ..server import Utf8JSONResponse, validate_harness_api_url
    """向 harness 的 HTTP API 推送一条测试消息（验证 http_api 唤醒链路）。
    body: {"harness_id": "...", "content": "...", "from_id": "..."}
    平台用该 harness 的 api_base_url/api_message_path 推送。
    """
    body = await request.json()
    hid = (body.get("harness_id") or "").strip()
    content = (body.get("content") or "").strip()
    if not hid or not content:
        return Utf8JSONResponse({"error": "harness_id 和 content 必填"}, status_code=400)
    sess = harness_manager.sessions.get(hid)
    if not sess or not sess.info:
        return Utf8JSONResponse({"error": f"harness {hid} 未注册"}, status_code=404)
    base_url = getattr(sess.info, "api_base_url", "") or ""
    message_path = getattr(sess.info, "api_message_path", "") or "/message"
    if not base_url:
        return Utf8JSONResponse({"error": f"harness {hid} 未配置 api_base_url，无法 HTTP 推送"}, status_code=400)
    ok, err = validate_harness_api_url(base_url)
    if not ok:
        return Utf8JSONResponse({"error": f"harness {hid} 的 api_base_url 校验失败: {err}"}, status_code=400)
    from_id = body.get("from_id") or f"agent-community-{hid}"
    ok, detail = send_http_api_message(base_url, content, from_id=from_id, message_path=message_path)
    return {"success": ok, "ok": ok, "harness_id": hid, "detail": detail, "base_url": base_url}

@router.post("/api/harness/bridge-test")
async def harness_bridge_test(request: Request):
    from ..server import Utf8JSONResponse, _file_poll_send, _harness_wakeup_method, _pending_push, _register_bridge_test_inflight
    """平台测试桥功能：向指定 harness 的桥发送一条测试消息。
    分发按 wakeup_method：
      - file_poll：写测试 JSON 到 harness 的 inbox（wakeup_dir），由文件桥/对象侧检测
      - 其余：进 pending_bridge_tests 队列，由通用 pending 桥轮询领取
    对象侧收到后应回报 POST /api/harness/bridge-test-result。
    """
    body = await request.json()
    hid = body.get("harness_id", "")
    if not hid:
        return Utf8JSONResponse({"error": "harness_id required"}, status_code=400)
    sess = harness_manager.sessions.get(hid)
    if not sess:
        return Utf8JSONResponse({"error": f"harness {hid} 未注册"}, status_code=404)
    test_id = uuid4().hex[:12]
    payload = {
        "type": "bridge_test",
        "harness_id": hid,
        "test_id": test_id,
        "sent_at": datetime.now().isoformat(),
        "message": "平台桥测试：请确认桥通道正常，然后回报 bridge-test-result",
        "report_endpoint": "/api/harness/bridge-test-result",
    }
    method = _harness_wakeup_method(hid)
    if method == "file_poll":
        ok = _file_poll_send(hid, payload)
        if not ok:
            return Utf8JSONResponse({"error": "写入 inbox 失败（wakeup_dir 不可写）"}, status_code=500)
        print(f"[bridgetest] file_poll 已写 inbox: {hid} test_id={test_id}", flush=True)
    else:
        _pending_push(pending_bridge_tests, hid, payload)
        print(f"[bridgetest] 已入 pending 队列: {hid} test_id={test_id}", flush=True)
    _register_bridge_test_inflight(hid, test_id)  # V-17：登记待回报测试，供回报归属校验
    return {"success": True, "harness_id": hid, "test_id": test_id, "sent_via": method}

@router.get("/api/harness/pending-bridge-tests")
async def harness_pending_bridge_tests(harness_id: str):
    """harness 桥轮询：领取该 harness 名下的桥测试任务。"""
    tests = pending_bridge_tests.pop(harness_id, [])
    return {"tests": tests}

@router.post("/api/harness/bridge-test-result")
async def harness_bridge_test_result(request: Request):
    from ..server import Utf8JSONResponse, save_state
    """harness 桥回报测试结果（对象侧确认桥通道正常）。"""
    body = await request.json()
    hid = body.get("harness_id", "")
    if not hid:
        return Utf8JSONResponse({"error": "harness_id required"}, status_code=400)
    sess = harness_manager.sessions.get(hid)
    if not sess:
        return Utf8JSONResponse({"error": f"harness {hid} 未注册"}, status_code=404)
    # V-17 归属校验：test_id 必须匹配平台近期发出的测试，且该测试未被回报过
    inflight = _bridge_tests_inflight.get(hid)
    body_test_id = str(body.get("test_id") or "").strip()
    if not inflight or inflight.get("test_id") != body_test_id:
        print(f"[安全][bridgetest] {hid} 回报 test_id={body_test_id!r} 与平台发出记录不匹配，已拒绝", flush=True)
        return Utf8JSONResponse(
            {"error": "桥测试回报不匹配：test_id 与平台发出的测试不一致（请先由平台发起测试）"},
            status_code=403,
        )
    if inflight.get("reported"):
        print(f"[安全][bridgetest] {hid} 重复回报 test_id={body_test_id}，已拒绝", flush=True)
        return Utf8JSONResponse({"error": "该测试已回报过，拒绝重复提交"}, status_code=403)
    if time.time() > inflight.get("expire_ts", 0):
        _bridge_tests_inflight.pop(hid, None)
        print(f"[安全][bridgetest] {hid} 回报超期（test_id={body_test_id}），已拒绝", flush=True)
        return Utf8JSONResponse({"error": "测试回报已过期，请重新发起桥测试"}, status_code=403)
    inflight["reported"] = True
    ok = bool(body.get("ok"))
    record = {
        "test_id": body.get("test_id", ""),
        "ok": ok,
        "echo": body.get("echo", ""),
        "at": datetime.now().isoformat(),
    }
    sess.metadata["bridge_test"] = record
    # 同步到 info.metadata 以便 save_state 持久化（session 层 metadata 不落盘）
    sess.info.metadata = dict(sess.info.metadata or {})
    sess.info.metadata["bridge_test"] = record
    if ok:
        sess.info.bridge_status = "tested"
    print(f"[bridgetest] {hid} 测试结果: ok={ok} echo={body.get('echo','')[:60]}", flush=True)
    save_state()
    return {"success": True, "harness_id": hid, "ok": ok}

@router.get("/api/harness/bridge-templates")
async def harness_bridge_templates():
    """列出平台内置桥模板库（按接口类型划分），供平台 Agent / 外部选择模板。"""
    from ..bridge_factory import list_templates
    templates = list_templates()
    return {"success": True, "count": len(templates), "templates": templates}

@router.post("/api/harness/{harness_id}/bridge/generate")
async def harness_bridge_generate(harness_id: str, request: Request):
    from ..server import Utf8JSONResponse, _platform_base_url, save_state
    """平台标准构桥 API：按 harness 注册信息渲染内置模板生成桥脚本。
    治本设计：平台内置桥模板库（cli_acp / file_poll / pending_poll 等），此处只做模板复制+配置注入的机械操作，
    不依赖 LLM 自由设计。生成后自动登记桥坐标（bridge_dir）。
    body: {"template": "file_poll", "out_dir": "可选输出目录"}
    """
    from ..bridge_factory import generate, safe_slug, list_templates, BridgeTemplateError
    try:
        body = await request.json()
    except Exception:
        body = {}
    template = (body.get("template") or "cli_acp").strip()
    out_dir = (body.get("out_dir") or "").strip()
    sess = harness_manager.sessions.get(harness_id)
    if not sess:
        return Utf8JSONResponse({"error": f"harness {harness_id} 未注册"}, status_code=404)
    available = {t["name"] for t in list_templates()}
    if template not in available:
        return Utf8JSONResponse({"error": f"模板不存在: {template}，可用: {sorted(available)}"}, status_code=400)
    info = sess.info
    # 平台自址：桥用同一地址回连
    try:
        platform_url = str(request.base_url).rstrip("/")
    except Exception:
        platform_url = _platform_base_url()
    # 按模板 interface_type 组装参数（模板 required_fields 兜底校验）
    if template == "cli_acp":
        acp_command = (info.acp_command or "").strip()
        if not acp_command:
            return Utf8JSONResponse(
                {"error": f"harness {harness_id} 未配置 acp_command，无法生成 cli_acp 桥"},
                status_code=400,
            )
        ai = info.ai
        params = {
            "HARNESS_ID": harness_id,
            "ACP_COMMAND": acp_command,
            "ACP_CWD": (info.acp_cwd or "").strip() or str(Path(__file__).resolve().parent.parent),
            "MODEL_NAME": (getattr(ai, "model_name", "") if ai else "") or "unknown-model",
            "PROVIDER": (getattr(ai, "provider", "") if ai else "") or "unknown",
            "DESCRIPTION": (getattr(ai, "description", "") if ai else "") or f"agent_community harness {harness_id}",
        }
    elif template == "file_poll":
        wakeup_dir = (info.wakeup_dir or "").strip()
        if not wakeup_dir:
            return Utf8JSONResponse(
                {"error": f"harness {harness_id} 未配置 wakeup_dir（inbox），无法生成 file_poll 桥"},
                status_code=400,
            )
        params = {
            "HARNESS_ID": harness_id,
            "INBOX_DIR": wakeup_dir,
            "TRIGGER_CMD": (body.get("trigger_cmd") or "").strip(),
            "PLATFORM_URL": platform_url,
        }
    elif template == "pending_poll":
        work_dir = (body.get("work_dir") or "").strip()
        if not work_dir:
            work_dir = str(Path(__file__).resolve().parent.parent / "bridges" / safe_slug(harness_id))
        params = {
            "HARNESS_ID": harness_id,
            "WORK_DIR": work_dir,
            "TRIGGER_CMD": (body.get("trigger_cmd") or "").strip(),
            "PLATFORM_URL": platform_url,
        }
    else:
        return Utf8JSONResponse({"error": f"暂不支持自动生成模板: {template}"}, status_code=400)
    bridges_root = Path(__file__).resolve().parent.parent / "bridges"
    if out_dir:
        # V-6 修复：out_dir 必须位于平台 bridges 根目录内，防任意文件写入
        p = Path(out_dir)
        if not p.is_absolute():
            p = bridges_root / p
        try:
            p.resolve().relative_to(bridges_root.resolve())
        except (ValueError, OSError):
            return Utf8JSONResponse(
                {"error": f"out_dir 必须位于平台 bridges 目录内: {bridges_root}"},
                status_code=400,
            )
        target = p
    else:
        target = bridges_root / safe_slug(harness_id)
    try:
        bridge_file = generate(template, params, target)
    except BridgeTemplateError as e:
        return Utf8JSONResponse({"error": f"桥生成参数校验失败: {e}"}, status_code=400)
    except Exception as e:
        return Utf8JSONResponse({"error": f"桥生成失败: {e}"}, status_code=500)
    # 自动登记桥坐标
    sess.info.bridge_dir = str(target)
    if sess.info.bridge_status != "tested":
        sess.info.bridge_status = "reported"
    save_state()
    return {
        "success": True,
        "harness_id": harness_id,
        "template": template,
        "bridge_file": str(bridge_file),
        "bridge_dir": str(target),
        "bridge_status": sess.info.bridge_status,
        "launch_hint": f'python -u "{bridge_file}" --url {_platform_base_url()}',
    }

@router.post("/api/harness/bridge-path")
async def harness_bridge_path(request: Request):
    from ..server import Utf8JSONResponse, save_state
    """对象建好桥、平台测试通过后，对象告知桥文件路径，平台记录到 harness 信息。
    body: {"harness_id": "...", "bridge_dir": "桥文件所在文件夹路径"}
    """
    body = await request.json()
    hid = body.get("harness_id", "")
    bridge_dir = (body.get("bridge_dir") or "").strip()
    if not hid:
        return Utf8JSONResponse({"error": "harness_id required"}, status_code=400)
    if not bridge_dir:
        return Utf8JSONResponse({"error": "bridge_dir required"}, status_code=400)
    sess = harness_manager.sessions.get(hid)
    if not sess:
        return Utf8JSONResponse({"error": f"harness {hid} 未注册"}, status_code=404)
    sess.info.bridge_dir = bridge_dir
    if sess.info.bridge_status != "tested":
        sess.info.bridge_status = "reported"
    print(f"[bridge] {hid} 桥坐标已记录: {bridge_dir} status={sess.info.bridge_status}", flush=True)
    save_state()
    return {"success": True, "harness_id": hid, "bridge_dir": bridge_dir, "bridge_status": sess.info.bridge_status}

@router.post("/api/harness/{harness_id}/activate")
async def harness_activate_window(harness_id: str):
    from ..server import Utf8JSONResponse, _activate_windows_by_pids, _find_bridge_processes
    """Harness 监控室：将指定 harness 对应的软件窗口激活并置顶。"""
    sess = harness_manager.sessions.get(harness_id)
    if not sess:
        return Utf8JSONResponse({"error": f"harness {harness_id} 未注册"}, status_code=404)
    info = getattr(sess, "info", None)
    bridge_dir = (getattr(info, "bridge_dir", "") or "") if info else ""
    hints = [harness_id]
    if info:
        hn = (getattr(info, "harness_name", "") or "").strip()
        if hn and hn != harness_id:
            hints.append(hn)
    procs = await asyncio.to_thread(_find_bridge_processes, harness_id, bridge_dir)
    pids = [p.get("pid") for p in procs if isinstance(p, dict) and p.get("pid")]
    result = await asyncio.to_thread(_activate_windows_by_pids, pids, hints)
    result["harness_id"] = harness_id
    if not result.get("success"):
        result["hint"] = "未找到该 harness 的可激活窗口（桥进程可能无主窗口）"
    return result

@router.post("/api/harness/bridge-verify")
async def harness_bridge_verify(request: Request):
    from ..server import Utf8JSONResponse, _file_poll_send, _find_bridge_processes, _harness_wakeup_method, _pending_push, _register_bridge_test_inflight, save_state
    """平台级桥验证：综合检查桥坐标、桥进程、历史测试，并实时发测试等待真实回报。
    判定标准：实时通道检查必须收到对象侧回报（test_id 匹配且 ok=true）才算通过，
    不采信页面上的 bridge_status 字段（防状态造假/残留）。
    """
    body = await request.json()
    hid = body.get("harness_id", "")
    if not hid:
        return Utf8JSONResponse({"error": "harness_id required"}, status_code=400)
    try:
        timeout = min(max(int(body.get("timeout", 10)), 2), 30)
    except Exception:
        timeout = 10
    sess = harness_manager.sessions.get(hid)
    if not sess:
        return Utf8JSONResponse({"error": f"harness {hid} 未注册"}, status_code=404)
    info = sess.info
    bridge_dir = getattr(info, "bridge_dir", "") or ""
    # 1) 桥坐标检查
    dir_check = {"recorded": bool(bridge_dir), "exists": False, "path": bridge_dir}
    if bridge_dir:
        dir_check["exists"] = os.path.isdir(bridge_dir)
        if dir_check["exists"]:
            try:
                files = sorted(p.name for p in Path(bridge_dir).iterdir())
                dir_check["file_count"] = len(files)
                dir_check["recent_files"] = files[-5:]
            except Exception as e:
                dir_check["file_count"] = -1
                dir_check["error"] = str(e)
    # 2) 桥进程检查（与删除清理同一匹配口径，只查不杀）
    procs = await asyncio.to_thread(_find_bridge_processes, hid, bridge_dir)
    proc_check = {"running": len(procs) > 0, "processes": procs[:5]}
    # 3) 历史测试记录（仅参考，不作为通过依据）
    bt = (info.metadata or {}).get("bridge_test") or {}
    hist_check = {
        "has_record": bool(bt.get("test_id")),
        "ok": bool(bt.get("ok")),
        "at": bt.get("at", ""),
    }
    # 4) 实时通道检查：发新测试，等对象侧真实回报
    test_id = uuid4().hex[:12]
    payload = {
        "type": "bridge_test",
        "harness_id": hid,
        "test_id": test_id,
        "sent_at": datetime.now().isoformat(),
        "message": "平台桥验证：请确认桥通道正常，然后回报 bridge-test-result",
        "report_endpoint": "/api/harness/bridge-test-result",
    }
    method = _harness_wakeup_method(hid)
    if method == "file_poll":
        send_ok = _file_poll_send(hid, payload)
    else:
        send_ok = _pending_push(pending_bridge_tests, hid, payload)
    _register_bridge_test_inflight(hid, test_id)  # V-17：登记待回报测试，供回报归属校验
    channel_check = {"sent": send_ok, "sent_via": method, "test_id": test_id, "reported": False, "ok": False}
    if send_ok:
        loop = asyncio.get_event_loop()
        deadline = loop.time() + timeout
        while loop.time() < deadline:
            rec = sess.metadata.get("bridge_test") or {}
            if rec.get("test_id") == test_id:
                channel_check["reported"] = True
                channel_check["ok"] = bool(rec.get("ok"))
                channel_check["echo"] = rec.get("echo", "")
                channel_check["reported_at"] = rec.get("at", "")
                break
            await asyncio.sleep(0.5)
    # 汇总：目录存在 + 进程在跑 + 实时回报 ok 才算通过
    ok = bool(dir_check["exists"] and proc_check["running"] and channel_check["sent"] and channel_check["ok"])
    report = {
        "harness_id": hid,
        "checked_at": datetime.now().isoformat(),
        "bridge_dir": dir_check,
        "bridge_process": proc_check,
        "history_test": hist_check,
        "live_channel": channel_check,
        "ok": ok,
    }
    # 验证结果留痕并持久化
    sess.metadata["bridge_verify"] = report
    sess.info.metadata = dict(sess.info.metadata or {})
    sess.info.metadata["bridge_verify"] = report
    save_state()
    print(f"[verify] {hid} 验证完成 ok={ok} dir={dir_check['exists']} proc={proc_check['running']} "
          f"live_sent={channel_check['sent']} live_ok={channel_check['ok']}", flush=True)
    return report

@router.post("/api/harness/prefill")
async def harness_prefill(request: Request):
    from ..server import Utf8JSONResponse, validate_acp_command, validate_callback_url, validate_harness_api_url, validate_wakeup_dir
    """AI 识别智能填入：根据自然语言描述提取 harness 结构化字段。"""
    body = await request.json()
    text = (body.get("text") or "").strip()
    if not text:
        return Utf8JSONResponse({"error": "描述不能为空"}, status_code=400)
    if not _sv().ai_provider:
        return Utf8JSONResponse({"error": "AI Provider 未配置，无法智能填入"}, status_code=400)
    system = (
        "你是 harness 信息解析助手。从用户对 harness 的描述中提取结构化字段，只输出 JSON，不要任何解释。\n"
        '输出格式：{"harness_id":"","harness_name":"","harness_type":"","model_name":"","provider":"","capabilities":["..."],"wakeup_method":"","callback_url":"","wakeup_dir":"","acp_command":"","acp_cwd":"","description":""}\n'
        "规则：harness_id 用完整名称（与显示名一致），不要用简写；capabilities 用简短能力标签（如 coding/file_ops/web_search）；"
        "wakeup_method 从 http/file_poll/clipboard/acp 里选（描述提到'回调地址/HTTP POST'→http，'文件轮询/监听目录'→file_poll，'剪贴板'→clipboard，'ACP/子进程/CLI 拉起'→acp）；description 保留坐标、能力、限制。"
    )
    try:
        reply = await ai_external_run_ai_call(
            _sv().ai_provider.chat(system, f"用户对 harness 的描述：\n{text}"),
            label="server.harness_smart_fill",
        )
        start, end = reply.find("{"), reply.rfind("}") + 1
        if start >= 0 and end > start:
            fields = json.loads(reply[start:end])
            if not isinstance(fields, dict):
                return {"success": False, "error": "AI 返回 JSON 不是对象"}
            # V-15 修复：对 AI 回填的关键字段做安全校验，非法值剔除并附警告，
            # 防止注入的 acp_command / 恶意回调 URL 被直接回填进注册表单。
            warnings: list[str] = []
            for uf in ("callback_url", "wakeup_url"):
                uv = str(fields.get(uf) or "").strip()
                if uv:
                    ok, err = validate_callback_url(uv)
                    if not ok:
                        fields[uf] = ""
                        warnings.append(f"{uf} 非法已清空: {err}")
            ab = str(fields.get("api_base_url") or "").strip()
            if ab:
                ok, err = validate_harness_api_url(ab)
                if not ok:
                    fields["api_base_url"] = ""
                    warnings.append(f"api_base_url 非法已清空: {err}")
            ac = str(fields.get("acp_command") or "").strip()
            if ac:
                ok, err = validate_acp_command(ac)
                if not ok:
                    fields["acp_command"] = ""
                    warnings.append(f"acp_command 非法已清空: {err}")
            wd = str(fields.get("wakeup_dir") or "").strip()
            if wd:
                ok, err = validate_wakeup_dir(wd)
                if not ok:
                    fields["wakeup_dir"] = ""
                    warnings.append(f"wakeup_dir 非法已清空: {err}")
            resp: dict = {"success": True, "fields": fields}
            if warnings:
                resp["warnings"] = warnings
            return resp
        return {"success": False, "error": "AI 返回无法解析为 JSON"}
    except Exception as e:
        return {"success": False, "error": f"智能填入失败: {e}"}

@router.delete("/api/harness/{harness_id}")
async def unregister_harness(harness_id: str):
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

@router.patch("/api/harness/{harness_id}/capabilities")
async def update_harness_capabilities(harness_id: str, request: Request):
    from ..server import Utf8JSONResponse, _append_hall, bcast_to_clients, save_state
    """精化外端 Harness 的能力标签列表（更新注册信息 + 平台 AgentCard）"""
    body = await request.json()
    caps = body.get("capabilities", [])
    if not isinstance(caps, list) or not all(isinstance(x, str) for x in caps):
        return Utf8JSONResponse({"error": "capabilities 必须是字符串列表"}, status_code=400)
    caps = list(dict.fromkeys(c.strip() for c in caps if c and c.strip()))
    sess = harness_manager.sessions.get(harness_id)
    if not sess:
        return Utf8JSONResponse({"error": "harness not found"}, status_code=404)
    sess.info.ai.capabilities = caps
    card = agents.get(sess.agent_id)
    if card:
        card.capabilities = caps
    save_state()
    sys_msg = Message(
        type=MessageType.SYSTEM, from_agent="system",
        content=f"Harness「{harness_id}」能力列表已精化: {', '.join(caps) if caps else '（空）'}",
    )
    _append_hall(sys_msg)
    await bcast_to_clients(sys_msg)
    return {"success": True, "harness_id": harness_id, "agent_id": sess.agent_id, "capabilities": caps}

@router.get("/api/harness/{harness_id}/activation_prompt")
async def get_harness_activation_prompt(harness_id: str):
    from ..server import Utf8JSONResponse
    """查看外端 Harness 的唤醒提示词模板（含 {role}/{workspace_dir} 占位符，激活时由平台填充）。"""
    sess = harness_manager.sessions.get(harness_id)
    if not sess:
        return Utf8JSONResponse({"error": "harness not found"}, status_code=404)
    prompt = sess.metadata.get("activation_prompt") or ""
    if not prompt:
        prompt = (sess.info.metadata or {}).get("activation_prompt") or ""
    return {"success": True, "harness_id": harness_id, "agent_id": sess.agent_id, "prompt": prompt}

@router.patch("/api/harness/{harness_id}/activation_prompt")
async def update_harness_activation_prompt(harness_id: str, request: Request):
    from ..server import Utf8JSONResponse, save_state
    """修改外端 Harness 的唤醒提示词模板（同步持久化，注册时不再被自动生成覆盖）。"""
    body = await request.json()
    prompt = (body.get("prompt") or "").strip()
    if not prompt:
        return Utf8JSONResponse({"error": "prompt 不能为空"}, status_code=400)
    sess = harness_manager.sessions.get(harness_id)
    if not sess:
        return Utf8JSONResponse({"error": "harness not found"}, status_code=404)
    sess.metadata["activation_prompt"] = prompt
    sess.info.metadata = dict(sess.info.metadata or {})
    sess.info.metadata["activation_prompt"] = prompt
    save_state()
    return {"success": True, "harness_id": harness_id, "agent_id": sess.agent_id, "prompt": prompt}

@router.patch("/api/harness/{harness_id}/experience-index")
async def update_harness_experience_index(harness_id: str, request: Request):
    from ..server import Utf8JSONResponse, _merge_experience_index, save_state
    """登记/更新该 Harness 的经验包索引（skill_index / knowledge_index）。

    参照 activation_prompt 的 metadata 持久化模式：写入 sess.info.metadata 与
    sess.metadata 并 save_state()。body: {"skill_index": [...], "knowledge_index": [...]}。
    返回更新后的索引（含每类条目数），供前端/登记方确认。
    """
    body = await request.json()
    if not isinstance(body, dict):
        return Utf8JSONResponse({"error": "body 需为 JSON 对象"}, status_code=400)
    sess = harness_manager.sessions.get(harness_id)
    if not sess:
        return Utf8JSONResponse({"error": "harness not found"}, status_code=404)
    merged = _merge_experience_index(body, sess.info)
    if not merged:
        return Utf8JSONResponse(
            {"error": "需要合法 skill_index 或 knowledge_index（列表，条目含非空 name）"},
            status_code=400,
        )
    # 同步到运行时 metadata（读取端双查兜底）
    sess.metadata.update(merged)
    save_state()
    return {
        "success": True,
        "harness_id": harness_id,
        "agent_id": sess.agent_id,
        "skill_index": sess.info.metadata.get("skill_index", []),
        "knowledge_index": sess.info.metadata.get("knowledge_index", []),
    }

@router.post("/api/harness/peer-route")
async def get_peer_route(request: Request):
    from ..server import Utf8JSONResponse
    """查询目标 Harness Agent 的路由信息，供直连委托/审查使用。
    平台退为路由注册中心：当双 Harness 均在线时，
    委托和审查不再经平台中转，而是直接 HTTP POST 到对端。
    """
    body = await request.json()
    to_agent_id = body.get("to_agent_id", "")
    to_hid = harness_manager.id_to_harness.get(to_agent_id)
    if not to_hid:
        return Utf8JSONResponse(
            {"error": "target agent is not a harness"}, status_code=404
        )
    sess = harness_manager.sessions.get(to_hid)
    if not sess or sess.status != HarnessStatus.ONLINE:
        return Utf8JSONResponse({"error": "target harness offline"}, status_code=404)
    return {
        "harness_id": to_hid,
        "agent_id": to_agent_id,
        "callback_url": sess.info.callback_url,
        "transport": sess.transport.value,
        "harness_name": sess.info.harness_name,
    }

@router.websocket("/ws/harness/{harness_id}")
async def harness_ws(ws: WebSocket, harness_id: str):
    from ..server import _ws_auth_ok
    """外部 Harness 通过 WebSocket 长连接接入"""
    if not _ws_auth_ok(ws):
        await ws.close(code=4401, reason="unauthorized")
        return
    bridge = harness_manager.bridges.get(harness_id)
    if not bridge:
        await ws.close(code=4000, reason="harness not registered")
        return
    await ws.accept()
    bridge.attach_ws(ws)
    if harness_id in harness_manager.sessions:
        harness_manager.sessions[harness_id].status = HarnessStatus.ONLINE
        harness_manager.sessions[harness_id].last_heartbeat = datetime.now().isoformat()
    try:
        while True:
            raw = await ws.receive_text()
            data = json.loads(raw)
            msg = HarnessMessage(**data)
            harness_manager.handle_reply(msg)
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        if harness_id in harness_manager.sessions:
            harness_manager.sessions[harness_id].status = HarnessStatus.OFFLINE
