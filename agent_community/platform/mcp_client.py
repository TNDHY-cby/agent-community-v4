"""MCP 客户端适配层（最小实现）

平台以 MCP 客户端身份连接远程 Streamable HTTP MCP 服务器（如 IACMCPServer，
http://localhost:9527 或公网隧道 URL），将远程工具（tools/list）注册为外端
Agent 并支持工作间调用（tools/call）。

Streamable HTTP transport 流程（JSON-RPC 2.0 over POST）：
    initialize → notifications/initialized → tools/list | tools/call

实现约束（最小侵入 / 向后兼容）：
- 每次调用新建会话（initialize → initialized → 方法 → 结束），不维持长连接，
  符合 Streamable HTTP 无状态模型，也避免与平台既有 WS/PIPE 桥互扰。
- 响应兼容 JSON 与 text/event-stream（SSE）两种 Content-Type。
- 连接前 URL 校验复用平台 validate_harness_api_url（防 SSRF：允许回环/本机、
  禁云元数据地址、域名须解析公网 IP）。
"""
from __future__ import annotations

import json
from typing import Any

import httpx

MCP_DEFAULT_TIMEOUT = 15.0
MCP_CALL_TIMEOUT = 120.0
MCP_PROTOCOL_VERSION = "2025-06-18"  # 现行稳定版本；服务端可能返回其他版本，宽容处理


class McpError(Exception):
    """MCP 客户端错误（连接 / 协议 / 工具调用）"""


class McpAsyncPendingError(McpError):
    """MCP 异步调用标记：服务器以 202 Accepted 接受调用但未同步返回结果。

    当前最小实现不回收异步结果，调用方应将此状态显式转成"待确认/未完成"
    的 task-result，避免把 pending 误判为调用成功。
    """


def validate_mcp_url(url: str) -> tuple[bool, str]:
    """MCP URL 校验：复用平台防 SSRF 思路（允许回环/本机，禁云元数据，域名须解析公网）。"""
    # ★2026-10-25 修（外部 agent 报的）：这里写成 `..server` 会去 `agent_community.server` 找（不存在）
    #   ⇒ `validate_mcp_url` 必抛 ImportError，被上层 try 吞掉后表现成"地址不合法/拉不到工具"。
    from .server import validate_harness_api_url  # 延迟导入避免循环

    return validate_harness_api_url(url)


async def _post_json_raw(url: str, payload: dict, timeout: float = MCP_DEFAULT_TIMEOUT,
                         session_id: str | None = None) -> tuple:
    """POST 一次 JSON-RPC，返回 `(解析后的消息, Mcp-Session-Id)`。

    ★2026-10-25（外部 agent 实测报的第三个坑）：**Streamable HTTP 的 MCP 服务器要求
    `initialize` 之后带 `Mcp-Session-Id` 头**，否则一律 `400 Missing session ID` ——
    原来这里完全没处理会话头，所以"拉工具表"永远失败、`tools` 一直是 `[]`。
    现在：发请求时带上已有 session；响应里给了新 session 就记下来。
    """
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    if session_id:
        headers["Mcp-Session-Id"] = session_id
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
            resp = await client.post(url, json=payload, headers=headers)
    except httpx.HTTPError as e:
        raise McpError(f"MCP 连接失败: {e}") from e
    new_sid = resp.headers.get("mcp-session-id") or session_id
    ctype = (resp.headers.get("content-type") or "").lower()
    if "text/event-stream" in ctype:
        data_lines: list[str] = []
        for line in resp.text.splitlines():
            line = line.strip()
            if line.startswith("data:"):
                data = line[5:].strip()
                if data:
                    data_lines.append(data)
        for data in data_lines:
            try:
                return json.loads(data), new_sid
            except json.JSONDecodeError:
                continue
        if data_lines:
            joined = "".join(data_lines)
            try:
                return json.loads(joined), new_sid
            except json.JSONDecodeError as e:
                raise McpError(f"MCP SSE data 载荷非 JSON: {joined[:200]}") from e
        raise McpError(f"MCP SSE 响应无有效 data 载荷 (HTTP {resp.status_code})")
    if resp.status_code >= 400:
        raise McpError(f"MCP HTTP {resp.status_code}: {resp.text[:300]}")
    if resp.status_code in (202, 204) or not resp.content:
        raise McpAsyncPendingError(
            f"MCP HTTP {resp.status_code} 空响应：服务器异步接受（pending），当前最小实现不回收异步结果"
        )
    try:
        return resp.json(), new_sid
    except Exception as e:
        raise McpError(f"MCP 响应非 JSON: {resp.text[:200]}") from e


async def mcp_post_json(url: str, payload: dict, timeout: float = MCP_DEFAULT_TIMEOUT,
                        session_id: str | None = None) -> Any:
    """POST JSON-RPC 请求，兼容 JSON 与 SSE 两种响应体（**只要消息**，会话头由 `_post_json_raw` 管）。"""
    msg, _sid = await _post_json_raw(url, payload, timeout, session_id)
    return msg


def _raise_if_error(msg: dict) -> None:
    if isinstance(msg, dict) and msg.get("error"):
        err = msg["error"]
        raise McpError(f"MCP JSON-RPC error: {err.get('code')} {err.get('message')}")


async def mcp_open_session(url: str, timeout: float = MCP_DEFAULT_TIMEOUT) -> tuple:
    """`initialize` 握手并返回 `(serverInfo 结果, Mcp-Session-Id)`（Streamable HTTP 必须带回来）。"""
    payload = {
        "jsonrpc": "2.0",
        "id": "mcp-init",
        "method": "initialize",
        "params": {
            "protocolVersion": MCP_PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "agent-community-mcp-client", "version": "1.0"},
        },
    }
    msg, sid = await _post_json_raw(url, payload, timeout)
    _raise_if_error(msg)
    result = msg.get("result")
    if not isinstance(result, dict):
        raise McpError("initialize 响应缺少 result")
    return result, sid


async def mcp_initialize(url: str, timeout: float = MCP_DEFAULT_TIMEOUT) -> dict:
    """initialize 握手，返回 serverInfo / protocolVersion。"""
    result, _sid = await mcp_open_session(url, timeout)
    return result


async def mcp_notify_initialized(url: str, timeout: float = MCP_DEFAULT_TIMEOUT,
                                 session_id: str | None = None) -> None:
    """发送 initialized 通知（JSON-RPC notification，无 id）。失败不阻塞（部分实现忽略通知）。"""
    payload = {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}}
    try:
        await mcp_post_json(url, payload, timeout, session_id)
    except McpError:
        pass


async def mcp_list_tools(url: str, timeout: float = MCP_DEFAULT_TIMEOUT) -> list[dict]:
    """动态发现远程工具。返回 [{name, description, inputSchema, ...}]"""
    _init, sid = await mcp_open_session(url, timeout)          # 会话头必须一路带着
    await mcp_notify_initialized(url, timeout, sid)
    msg = await mcp_post_json(
        url, {"jsonrpc": "2.0", "id": "mcp-list", "method": "tools/list", "params": {}},
        timeout, sid)
    _raise_if_error(msg)
    result = msg.get("result")
    tools = result.get("tools") if isinstance(result, dict) else None
    if not isinstance(tools, list):
        raise McpError("tools/list 响应缺少 tools 数组")
    return tools


async def mcp_call_tool(
    url: str, name: str, arguments: dict | None = None, timeout: float = MCP_CALL_TIMEOUT
) -> dict:
    """调用远程工具，返回规范化结果 {ok, text, raw}。

    同步 content 结果（text / structuredContent）正常返回；服务器返回
    异步 pending（202 Accepted / 空体 / result.status=="pending"）时，
    显式标记 ok=False 并附 [MCP async] 说明，避免被上层误判为调用成功。
    """
    _init, sid = await mcp_open_session(url, timeout)            # 会话头必须一路带着（同 list_tools）
    await mcp_notify_initialized(url, timeout, sid)
    payload = {
        "jsonrpc": "2.0",
        "id": "mcp-call",
        "method": "tools/call",
        "params": {"name": name, "arguments": arguments or {}},
    }
    try:
        msg = await mcp_post_json(url, payload, timeout, sid)
    except McpAsyncPendingError as e:
        return {
            "ok": False,
            "text": f"[MCP async] {e}",
            "raw": {"_async_pending": True, "tool": name},
        }
    _raise_if_error(msg)
    result = msg.get("result")
    if isinstance(result, dict) and result.get("status") == "pending":
        # 服务端在 JSON 响应体内声明 pending（未同步完成）
        return {
            "ok": False,
            "text": "[MCP async] 工具调用返回 status=pending（异步执行中），当前最小实现不回收异步结果",
            "raw": result,
        }
    is_err = bool(result.get("isError")) if isinstance(result, dict) else False
    text_parts = []
    if isinstance(result, dict):
        content = result.get("content")
        if isinstance(content, list):
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    text_parts.append(str(item.get("text", "")))
        # 结构化内容兜底展示
        if not text_parts and result.get("structuredContent") is not None:
            text_parts.append(json.dumps(result["structuredContent"], ensure_ascii=False))
    text = "\n".join(p for p in text_parts if p)
    if not text:
        text = json.dumps(result, ensure_ascii=False)
    return {"ok": not is_err, "text": text, "raw": result}
