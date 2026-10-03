"""platform/routers/harness_common：harness 端点组的共享辅助与 WebSocket（V-13 自 harness.py 拆分）。

共享：_sv()（双副本活跃 server 取用）、_harness_identity_check（P1 准入校验）、
_maybe_pull_mcp_tools、能力/提示词更新辅助、/ws/harness WebSocket。
三个业务子模块只依赖本模块，彼此无跨组调用（拆分前已实测）。
"""
from __future__ import annotations
import sys
from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect
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
from ..state import _bridge_tests_inflight, agents, assistant_history, assistant_history_max, pending_activations, pending_bridge_tests, pending_pre_register, pending_tasks, workshops
from ..protocol import HarnessTool
from ..audit import audit_log as _audit_log
from ..identity import check_request_token as _check_request_token
from ..identity import issue_agent_token as _issue_agent_token

router = APIRouter()

def _sv():
    """返回活跃 server 副本（__main__，uvicorn 实际运行的实例）模块。"""
    return sys.modules["__main__"]


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
