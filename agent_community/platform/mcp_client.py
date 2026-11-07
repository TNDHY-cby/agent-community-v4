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

from agent_community.spec_versions import MCP_SUPPORTED_MAX

MCP_DEFAULT_TIMEOUT = 15.0
MCP_CALL_TIMEOUT = 120.0
# 我们**自己**作为 MCP 客户端时声明的版本：与「服务端支持到哪一版」同一份口径（一处真相）。
# 服务端可能返回其他版本，宽容处理（不因为版本号不同就报错）。
MCP_PROTOCOL_VERSION = MCP_SUPPORTED_MAX


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
        # ★2026-11-03：pending 时要把**句柄**交出去，上层才能去查（原来只留了一句话）
        h = getattr(e, "handle", None) or str(e)
        return {
            "ok": False, "async": True, "handle": str(h),
            "text": f"[MCP async] {e}",
            "raw": {"_async_pending": True, "tool": name, "handle": str(h)},
        }
    _raise_if_error(msg)
    result = msg.get("result")
    if isinstance(result, dict) and result.get("status") == "pending":
        # 服务端在 JSON 响应体内声明 pending（未同步完成）——把句柄交出去，让上层能查
        h = (str(result.get("taskId") or result.get("task_id") or result.get("id") or "")
             or str(result.get("jobId") or result.get("job_id") or ""))
        return {
            "ok": False, "async": True, "handle": h,
            "text": ("[MCP async] 工具调用返回 status=pending（异步执行中）"
                     + ("，句柄 %s —— 用 mcp_task_get 去查" % h if h else "，但它没给句柄")),
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


# ===== MCP 侧异步任务（老板 2026-11-03 要求补）===============================
#   `mcp_call_tool` 原来遇到 pending 只说"不回收"。这里补三件事：
#     ① 探测对方用哪种异步约定（标准 Tasks 扩展 / 自定义 job 工具 / 都不支持）；
#     ② 按约定查一次状态（`mcp_task_get`）；
#     ③ 把 pending 的**句柄**交给上层，别只留在文字里。
#
#   ⚠️ 不假设任何一家：MCP Tasks 是实验性扩展，各家名字都不一样，
#      探测不到就**如实说不支持** —— 不许编一个"看起来在跑"的状态。

#: "自定义 job 工具"的常见命名（对方只给工具、不给 Tasks 扩展时）。
#: 这不是协议，是**约定俗成的几个名字**；一个都不在 ⇒ 如实说不支持。
_TASK_QUERY_TOOL_HINTS = (
    "get_task", "task_status", "get_task_status", "check_task", "poll_task",
    "get_job", "job_status", "get_job_status", "check_job", "poll_job",
)


def mcp_task_convention(tools: list[dict] | None = None,
                        capabilities: dict | None = None) -> dict:
    """判断这个 MCP Server 用哪种**异步任务**约定。纯函数，可单测。

    返回 `{"kind": "tasks"|"job_tool"|"", "tool": <查状态工具名>, "why": "<一句话>"}`：
      · `tasks`    —— 声明了 MCP Tasks 扩展（`capabilities.tasks` 存在且为真）；
      · `job_tool` —— 没声明 Tasks，但工具表里有"查任务状态"这类工具（`tool` 给名字）；
      · `""`       —— 都不支持（只能同步等；长任务会超时，如实说）。
    """
    caps = capabilities if isinstance(capabilities, dict) else {}
    tk = caps.get("tasks")
    if tk:
        return {"kind": "tasks", "tool": "", "why": "它声明了 MCP Tasks 扩展"}
    names = {str((x or {}).get("name") or "") for x in (tools or []) if isinstance(x, dict)}
    for hint in _TASK_QUERY_TOOL_HINTS:
        if hint in names:
            return {"kind": "job_tool", "tool": hint,
                    "why": "工具表里有 `%s`（自定义异步任务约定）" % hint}
    return {"kind": "", "tool": "",
            "why": ("它既没声明 MCP Tasks 扩展，也没有 `%s` 这类查状态工具 —— "
                    "只能同步等结果，长任务会超时" % "/".join(_TASK_QUERY_TOOL_HINTS[:3]))}


async def mcp_task_get(url: str, handle: str, convention: dict | None = None,
                       timeout: float = MCP_DEFAULT_TIMEOUT,
                       args_key: str = "taskId") -> dict:
    """按句柄查一次异步任务状态。

    两种约定分别处理：
      · `tasks`    —— 标准 JSON-RPC：`method="tasks/get"`，`params={"taskId": <handle>}`；
      · `job_tool` —— 调对方那个"查状态"工具：`tools/call{name: <tool>, arguments:{<args_key>: handle}}`。
    `convention` 不给就现场探测一次（多花一次 `tools/list`，但**不猜**）。

    返回 `{"ok", "state", "text", "terminal", "raw", "convention"}`：
      · `state` 归一化成 `working/done/failed/cancelled/unknown`（各家字段名不同）；
      · `terminal=True` 才算有最终结果（和 A2A 那条一个口径）。
    """
    conv = convention if isinstance(convention, dict) and convention.get("kind") else None
    if conv is None:
        tools = await mcp_list_tools(url, timeout)
        init, _sid = await mcp_open_session(url, timeout)
        # ⚠️ **既有 bug（2026-11-03 引入，2026-11-04 判据第 ⑩ 格抓到）**：
        #    `mcp_open_session` 返回的**已经是解包后的 initialize result**，
        #    原来这里又套了一层 `.get("result")` ⇒ `capabilities` 永远为空
        #    ⇒ **「标准 Tasks 扩展」永远探测不到**（只有 job_tool 那条靠工具名能命中）。
        #    只在"调用方没给 convention、要现场探测"时才走这条路，所以之前一直没暴露。
        _init = init if isinstance(init, dict) else {}
        if isinstance(_init.get("result"), dict):
            _init = _init["result"]                            # 兼容两种形状，别赌
        conv = mcp_task_convention(tools, _init.get("capabilities") or {})

    kind = str(conv.get("kind") or "")
    if not kind:
        return {"ok": False, "state": "unsupported", "terminal": True, "text": "",
                "convention": conv,
                "error": "这个 MCP Server 不支持异步任务（%s）" % (conv.get("why") or "")}

    if kind == "tasks":
        _init, sid = await mcp_open_session(url, timeout)
        await mcp_notify_initialized(url, timeout, sid)
        msg = await mcp_post_json(url, {"jsonrpc": "2.0", "id": "mcp-task-get",
                                        "method": "tasks/get",
                                        "params": {"taskId": handle}}, timeout, sid)
        _raise_if_error(msg)
        raw = msg.get("result") or {}
    else:  # job_tool
        call = await mcp_call_tool(url, str(conv.get("tool")), {args_key: handle}, timeout)
        raw = call.get("raw") or {}
        # ★2026-11-03（判据逼出来的）：工具返回的通常是
        #   {"content":[{"type":"text","text":"<一段 JSON 字符串>"}]}，
        #   状态在**那段文本里**，不在 raw 顶层。不解析就永远拿不到状态（实测 state_raw 为空）。
        _txt = str(call.get("text") or "").strip()
        if _txt:
            try:
                import json as _json

                _parsed = _json.loads(_txt)
                if isinstance(_parsed, dict):
                    raw = _parsed
            except Exception:                                  # noqa: BLE001 — 不是 JSON 就按原文
                raw = {"text": _txt} if not raw else raw

    # 一处真相：`status` 可能是对象、可能带 TASK_STATE_ 前缀（A2A 那边同一个坑）
    from .a2a_result_poll import raw_state_of as _raw_state_of

    state_raw = _raw_state_of(raw)
    _map = {"working": "working", "running": "working", "in_progress": "working",
            "pending": "working", "queued": "working", "submitted": "working",
            "completed": "done", "complete": "done", "done": "done", "succeeded": "done",
            "failed": "failed", "error": "failed", "rejected": "failed",
            "cancelled": "cancelled", "canceled": "cancelled"}
    state = _map.get(state_raw, "unknown")
    text = ""
    for k in ("text", "result", "output", "content"):
        v = (raw or {}).get(k)
        if isinstance(v, str) and v.strip():
            text = v.strip()
            break
        if isinstance(v, list):
            seg = "".join(str((x or {}).get("text") or "") for x in v if isinstance(x, dict))
            if seg.strip():
                text = seg.strip()
                break
    return {"ok": True, "state": state, "state_raw": state_raw,
            "terminal": state in ("done", "failed", "cancelled"),
            "text": text, "raw": raw, "convention": conv}
