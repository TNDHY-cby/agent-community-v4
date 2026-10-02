"""
MCP 协调层 — 将 agent_community-v4 平台能力暴露为 MCP Server（stdio 传输）。

设计目标：
1. 零第三方依赖（仅标准库），可直接被 Claude Code / Cursor / 任意 MCP 客户端挂载。
2. 最小侵入：不改 server.py 主线，通过 HTTP 调用平台 REST API（默认 127.0.0.1:18920）。
3. 工具设计对齐 Routa mcp-tools：覆盖注册发现 / 状态查询 / 工作间协作全流程。

挂载示例（Claude Code，在项目根目录即 agent_community 包的父目录）：
    claude --mcp-config '{"servers":{"ac4":{"command":"python","args":["-m","agent_community.mcp_server"]}}}'

⚠️ 本模块刻意放在 `agent_community/` 顶层而不是 `agent_community/platform/`：
   `platform/__init__.py` 里有 `from .server import app, ...`，任何 `platform.*` 子模块一被导入
   就会把整个 FastAPI 服务（fastapi/uvicorn + 静态前端挂载）拽进来，并向 **stdout 打印启动横幅**——
   而 MCP 走的就是 stdout，协议流会被横幅污染，客户端直接解析失败。
   放在顶层包后本模块保持真正的「零第三方依赖 + stdout 纯净」。

环境变量：
    AC4_BASE_URL  平台 REST 基址，默认 http://127.0.0.1:18920
    AC4_TOKEN     平台访问 Token（本地放行时可省略）
"""

from __future__ import annotations

import json
import os
import sys
import urllib.request
import urllib.parse
import uuid

BASE_URL = os.environ.get("AC4_BASE_URL", "http://127.0.0.1:18920").rstrip("/")
TOKEN = os.environ.get("AC4_TOKEN", "")
SERVER_NAME = "agent-community-v4-mcp"
SERVER_VERSION = "4.0.0"
# MCP 协议版本：列表按「新 → 旧」排列，首个即服务器默认（最高）版本。
# 规范（MCP Lifecycle, 2025-06-18）要求版本协商：客户端在 initialize 里带
# protocolVersion，服务器支持该版本就回同一个，否则回一个自己支持的版本。
SUPPORTED_PROTOCOL_VERSIONS = ["2025-06-18", "2025-03-26", "2024-11-05"]
PROTOCOL_VERSION = SUPPORTED_PROTOCOL_VERSIONS[0]


# ── 平台 HTTP 调用 ─────────────────────────────────────────────

def _headers() -> dict:
    h = {"Content-Type": "application/json", "Accept": "application/json"}
    if TOKEN:
        h["Authorization"] = f"Bearer {TOKEN}"
    return h


def _call(method: str, path: str, payload: dict | None = None, timeout: int = 30):
    url = BASE_URL + path
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, headers=_headers(), method=method)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8", "replace")
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"raw": raw}


def _get(path: str, timeout: int = 20):
    return _call("GET", path, timeout=timeout)


def _post(path: str, payload: dict, timeout: int = 60):
    return _call("POST", path, payload, timeout=timeout)


def _text(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2)


# ── 工具实现 ───────────────────────────────────────────────────

def t_status(_: dict) -> str:
    return _text(_get("/api/status"))


def t_list_harnesses(_: dict) -> str:
    return _text(_get("/api/harness/list"))


def t_list_agents(_: dict) -> str:
    return _text(_get("/api/agents"))


def t_list_tasks(_: dict) -> str:
    return _text(_get("/api/tasks"))


def t_get_task(args: dict) -> str:
    task_id = str(args.get("task_id", "")).strip()
    if not task_id:
        return _text({"error": "缺少 task_id"})
    return _text(_get(f"/api/task/{urllib.parse.quote(task_id)}"))


def t_create_workshop(args: dict) -> str:
    payload = {
        "title": str(args.get("title", "")).strip(),
        "description": str(args.get("description", "")).strip(),
        "task_id": str(args.get("task_id", "")).strip() or None,
        "max_participants": int(args.get("max_participants", 0) or 0),
    }
    if not payload["title"]:
        return _text({"error": "缺少 title"})
    return _text(_post("/api/workshop", payload))


def t_workshop_discuss(args: dict) -> str:
    ws_id = str(args.get("ws_id", "")).strip()
    content = str(args.get("content", "")).strip()
    if not ws_id or not content:
        return _text({"error": "缺少 ws_id 或 content"})
    payload = {"content": content, "agent": str(args.get("agent", "")).strip() or "user"}
    return _text(_post(f"/api/workshop/{urllib.parse.quote(ws_id)}/discuss", payload))


def t_workshop_review(args: dict) -> str:
    ws_id = str(args.get("ws_id", "")).strip()
    if not ws_id:
        return _text({"error": "缺少 ws_id"})
    payload = {
        "content": str(args.get("content", "")).strip(),
        "verdict": str(args.get("verdict", "")).strip() or "approved",
        "agent": str(args.get("agent", "")).strip() or "user",
    }
    return _text(_post(f"/api/workshop/{urllib.parse.quote(ws_id)}/review", payload))


def t_workshop_continue(args: dict) -> str:
    ws_id = str(args.get("ws_id", "")).strip()
    if not ws_id:
        return _text({"error": "缺少 ws_id"})
    return _text(_post(f"/api/workshop/{urllib.parse.quote(ws_id)}/continue", {}))


def t_workshop_interject(args: dict) -> str:
    ws_id = str(args.get("ws_id", "")).strip()
    content = str(args.get("content", "")).strip()
    if not ws_id or not content:
        return _text({"error": "缺少 ws_id 或 content"})
    payload = {
        "content": content,
        "priority": int(args.get("priority", 0) or 0),
        "agent": str(args.get("agent", "")).strip() or "user",
    }
    return _text(_post(f"/api/workshop/{urllib.parse.quote(ws_id)}/interject", payload))


def t_submit_task_result(args: dict) -> str:
    harness_id = str(args.get("harness_id", "")).strip()
    task_id = str(args.get("task_id", "")).strip()
    if not harness_id or not task_id:
        return _text({"error": "缺少 harness_id 或 task_id"})
    payload = {
        "task_id": task_id,
        "ok": bool(args.get("ok", True)),
        "content": str(args.get("content", "")).strip(),
        "artifacts": list(args.get("artifacts", []) or []),
        "confidence": float(args.get("confidence", 1.0) or 1.0),
    }
    return _text(_post("/api/harness/task-result", payload))


# ── 工具注册表 ─────────────────────────────────────────────────

TOOLS: list[dict] = [
    {
        "name": "status",
        "description": "查询平台运行状态：版本、已注册/在线 Agent 数、任务总数、Harness 数",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
        "fn": t_status,
    },
    {
        "name": "list_harnesses",
        "description": "列出平台已注册的 Harness（外部 Agent 接入实例）及其能力标签",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
        "fn": t_list_harnesses,
    },
    {
        "name": "list_agents",
        "description": "列出平台全部 Agent（含在线状态与能力）",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
        "fn": t_list_agents,
    },
    {
        "name": "list_tasks",
        "description": "列出平台全部任务（含状态/进度/所属工作间）",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
        "fn": t_list_tasks,
    },
    {
        "name": "get_task",
        "description": "按 task_id 查询任务详情（含讨论消息、委托、结果）",
        "inputSchema": {
            "type": "object",
            "properties": {"task_id": {"type": "string", "description": "任务 ID"}},
            "required": ["task_id"],
        },
        "fn": t_get_task,
    },
    {
        "name": "create_workshop",
        "description": "创建协作工作间：发起新任务并广播，Agent 举手加入讨论",
        "inputSchema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "任务标题"},
                "description": {"type": "string", "description": "任务描述"},
                "task_id": {"type": "string", "description": "可选，关联已有任务"},
                "max_participants": {"type": "integer", "description": "可选，参与上限"},
            },
            "required": ["title"],
        },
        "fn": t_create_workshop,
    },
    {
        "name": "workshop_discuss",
        "description": "在工作间发言：提交意见/澄清/补充（进入三级讨论流）",
        "inputSchema": {
            "type": "object",
            "properties": {
                "ws_id": {"type": "string", "description": "工作间 ID"},
                "content": {"type": "string", "description": "发言内容"},
                "agent": {"type": "string", "description": "可选，发言人，默认 user"},
            },
            "required": ["ws_id", "content"],
        },
        "fn": t_workshop_discuss,
    },
    {
        "name": "workshop_review",
        "description": "对工作间当前产出做审查收敛（approved / rejected / 意见）",
        "inputSchema": {
            "type": "object",
            "properties": {
                "ws_id": {"type": "string", "description": "工作间 ID"},
                "content": {"type": "string", "description": "审查意见"},
                "verdict": {"type": "string", "description": "approved|rejected"},
                "agent": {"type": "string", "description": "可选，审查人"},
            },
            "required": ["ws_id"],
        },
        "fn": t_workshop_review,
    },
    {
        "name": "workshop_continue",
        "description": "推进工作间进入下一轮（继续讨论/执行）",
        "inputSchema": {
            "type": "object",
            "properties": {"ws_id": {"type": "string", "description": "工作间 ID"}},
            "required": ["ws_id"],
        },
        "fn": t_workshop_continue,
    },
    {
        "name": "workshop_interject",
        "description": "向工作间插入高优先级打断意见（打断当前流程，可配优先级）",
        "inputSchema": {
            "type": "object",
            "properties": {
                "ws_id": {"type": "string", "description": "工作间 ID"},
                "content": {"type": "string", "description": "打断内容"},
                "priority": {"type": "integer", "description": "优先级，默认 0"},
                "agent": {"type": "string", "description": "可选，插入人"},
            },
            "required": ["ws_id", "content"],
        },
        "fn": t_workshop_interject,
    },
    {
        "name": "submit_task_result",
        "description": "以 Harness 身份回报任务执行结果（ok/content/artifacts）",
        "inputSchema": {
            "type": "object",
            "properties": {
                "harness_id": {"type": "string", "description": "Harness ID"},
                "task_id": {"type": "string", "description": "任务 ID"},
                "ok": {"type": "boolean", "description": "是否成功"},
                "content": {"type": "string", "description": "结果正文"},
                "artifacts": {"type": "array", "items": {"type": "string"}, "description": "产物路径"},
                "confidence": {"type": "number", "description": "置信度 0~1"},
            },
            "required": ["harness_id", "task_id"],
        },
        "fn": t_submit_task_result,
    },
]

_TOOL_MAP = {t["name"]: t for t in TOOLS}


# ── MCP JSON-RPC 处理 ─────────────────────────────────────────

def _rpc_error(code: int, message: str, data=None) -> dict:
    err = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    return {"jsonrpc": "2.0", "error": err}


def _handle(msg: dict) -> dict | None:
    method = msg.get("method", "")
    msg_id = msg.get("id")
    params = msg.get("params", {}) or {}

    if method == "initialize":
        # 版本协商（MCP Lifecycle）：支持客户端请求的版本就回同一个，否则回本服务器最高支持版本
        _requested = str(params.get("protocolVersion") or "").strip()
        _negotiated = _requested if _requested in SUPPORTED_PROTOCOL_VERSIONS else PROTOCOL_VERSION
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "protocolVersion": _negotiated,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            },
        }
    if method in ("notifications/initialized", "notifications/cancelled"):
        return None  # 通知无响应
    if method == "ping":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {}}
    if method == "tools/list":
        tools = [{k: v for k, v in t.items() if k != "fn"} for t in TOOLS]
        return {"jsonrpc": "2.0", "id": msg_id, "result": {"tools": tools}}
    if method == "tools/call":
        name = params.get("name", "")
        args = params.get("arguments", {}) or {}
        tool = _TOOL_MAP.get(name)
        if not tool:
            return _rpc_error(-32602, f"未知工具: {name}")
        try:
            out = tool["fn"](args)
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {"content": [{"type": "text", "text": out}], "isError": False},
            }
        except Exception as e:  # noqa: BLE001
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "content": [{"type": "text", "text": f"[平台调用失败] {e}"}],
                    "isError": True,
                },
            }
    return _rpc_error(-32601, f"未知方法: {method}")


def main() -> None:
    """stdio 循环：逐行读 JSON-RPC 请求，逐行回响应。"""
    while True:
        line = sys.stdin.readline()
        if not line:
            break
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        resp = _handle(msg)
        if resp is not None:
            sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
