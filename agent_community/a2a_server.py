"""
A2A 暴露层 — 将 agent_community-v4 平台暴露为 A2A（Agent-to-Agent）服务端。

设计目标：
1. 零第三方依赖（仅标准库），实现 A2A 协议服务端：Agent Card 发现 + JSON-RPC 任务/消息。
2. 最小侵入：不改 server.py 主线，通过 HTTP 调用平台 REST API（默认 127.0.0.1:18920）。
3. 价值：外部 A2A 生态 Agent（Google ADK、LangGraph、微软 AutoGen 等）可通过标准协议
   发现本平台并派发任务；平台内部仍走原有三级讨论/共识链路。

启动（在项目根目录，即 agent_community 包的父目录）：
    python -m agent_community.a2a_server            # 默认 0.0.0.0:9104
    python -m agent_community.a2a_server --port 9105

⚠️ 与 mcp_server.py 同理，本模块刻意放在 `agent_community/` 顶层而非 `platform/`：
   `platform/__init__.py` 会 `from .server import app, ...`，把整个 FastAPI 服务拽进来
   （并污染 stdout），而本层同样宣称「零第三方依赖」。

发现（供外部 Agent 注册）：
    GET http://<host>:9104/.well-known/agent-card.json   # A2A v1.0 标准路径
    GET http://<host>:9104/.well-known/agent.json         # v0.2 兼容路径

环境变量：
    AC4_BASE_URL  平台 REST 基址，默认 http://127.0.0.1:18920
    AC4_TOKEN     平台访问 Token（本地放行时可省略）
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import urllib.request
import urllib.parse
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BASE_URL = os.environ.get("AC4_BASE_URL", "http://127.0.0.1:18920").rstrip("/")
TOKEN = os.environ.get("AC4_TOKEN", "")
AGENT_NAME = "External Agent Community v4"
AGENT_DESCRIPTION = (
    "本地运行的外端 Agent 生产协作平台：注册/发现、工作间三级讨论、"
    "提案投票共识、委托执行与审查收敛。"
    "对外以 A2A 标准协议提供任务协作能力。"
)
AGENT_VERSION = "4.0.0"
AGENT_URL_TEMPLATE = "http://{host}:{port}/"


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
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {"raw": raw}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}


def _get(path: str, timeout: int = 20):
    return _call("GET", path, timeout=timeout)


def _post(path: str, payload: dict, timeout: int = 60):
    return _call("POST", path, payload, timeout=timeout)


def _harness_skills() -> list[dict]:
    """汇总平台各 Harness 的能力标签为 A2A skill 列表。"""
    data = _get("/api/harness/list")
    skills: list[dict] = []
    items = data if isinstance(data, list) else []
    if isinstance(data, dict):
        items = data.get("harnesses") or data.get("data") or []
    if isinstance(items, list):
        for h in items:
            caps = h.get("capabilities") or []
            for c in caps:
                skills.append({
                    "id": f"{h.get('harness_id') or h.get('id') or 'harness'}:{c}",
                    "name": c,
                    "description": f"由 Harness {h.get('name') or h.get('harness_id')} 提供",
                })
    # 平台自身能力
    builtin = [
        "workshop_discussion", "proposal_voting", "consensus",
        "delegation", "review", "interject",
    ]
    for b in builtin:
        skills.append({"id": f"platform:{b}", "name": b, "description": "平台内建协作能力"})
    return skills


def _agent_card(host: str, port: int) -> dict:
    base_url = AGENT_URL_TEMPLATE.format(host=host, port=port)
    return {
        "name": AGENT_NAME,
        "description": AGENT_DESCRIPTION,
        "url": base_url,
        "provider": {"organization": "External Agent Community", "url": ""},
        "version": AGENT_VERSION,
        "documentationUrl": "",
        "capabilities": {
            "streaming": False,
            "pushNotifications": False,
            "stateTransitionHistory": True,
        },
        "skills": _harness_skills(),
        "authentication": {"schemes": [], "credentials": None},
        "defaultInputModes": ["text"],
        "defaultOutputModes": ["text"],
        # A2A v1.0: supportedInterfaces[]（替代 v0.3 的 preferredTransport + additionalInterfaces）
        # 每个接口声明 url + protocolBinding + protocolVersion，客户端按需选择
        "supportedInterfaces": [
            {"url": base_url, "protocolBinding": "JSONRPC", "protocolVersion": "1.0"},
        ],
    }


# ── A2A JSON-RPC 处理 ─────────────────────────────────────────

def _rpc_success(msg_id, result) -> dict:
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def _rpc_error(msg_id, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


def _text_of(message: dict) -> str:
    parts = message.get("parts", []) or []
    texts = [p.get("text", "") for p in parts if p.get("text")]
    return "\n".join(texts) or str(message)


def _task_to_a2a(ws_id: str, task: dict | None, status: str = "working") -> dict:
    """把平台工作间/任务映射为 A2A Task 对象。"""
    return {
        "id": ws_id,
        "status": status,  # submitted / working / completed / failed / input-required / canceled
        "artifacts": [],
        "history": [],
        "metadata": {
            "task_title": (task or {}).get("title", ""),
            "task_status": (task or {}).get("status", ""),
            "ws_status": (task or {}).get("ws_status", ""),
        },
    }


def handle_rpc(msg: dict, host: str, port: int) -> dict:
    method = msg.get("method", "")
    msg_id = msg.get("id")
    params = msg.get("params", {}) or {}

    # ── A2A v1.0 方法名 → v0.3 旧名兼容映射（规范名优先，旧名保留过渡）──
    # v1.0 把 tasks/send + message/send 合并为 SendMessage：有 taskId=发消息，无=建任务
    if method == "SendMessage":
        method = "message/send" if str(params.get("taskId", "")).strip() else "tasks/send"
    elif method == "GetTask":
        method = "tasks/get"
    elif method == "CancelTask":
        method = "tasks/cancel"
    elif method == "GetExtendedAgentCard":
        method = "agent/get"
    elif method == "ListTasks":
        # v1.0 新增：列出全部任务
        resp = _get("/api/tasks")
        items = resp.get("tasks", []) if isinstance(resp, dict) else []
        tasks = [_task_to_a2a(t.get("task_id", ""), t, t.get("status", "working")) for t in items[:50]]
        return _rpc_success(msg_id, {"tasks": tasks, "total": len(items)})

    if method == "agent/get":
        return _rpc_success(msg_id, _agent_card(host, port))

    if method == "tasks/send":
        message = params.get("message", {}) or {}
        title = _text_of(message).strip()
        if not title:
            return _rpc_error(msg_id, -32602, "message 缺少文本内容")
        task_id = str(params.get("taskId", "")).strip() or uuid.uuid4().hex[:8]
        # 平台侧：创建工作间（含任务），触发广播与讨论
        resp = _post("/api/workshop", {
            "title": title[:200],
            "description": title,
            "task_id": task_id,
        })
        if not resp.get("ok", True):
            return _rpc_error(msg_id, -32000, f"平台创建任务失败: {resp}")
        ws_id = (resp.get("workshop_id") or resp.get("ws_id")
                 or (resp.get("data") or {}).get("ws_id") or "")
        return _rpc_success(msg_id, _task_to_a2a(ws_id, resp, "working"))

    if method == "tasks/get":
        task_id = str(params.get("taskId", "")).strip()
        if not task_id:
            return _rpc_error(msg_id, -32602, "缺少 taskId")
        # 先按 task_id 查任务，再尝试按工作间 id 查
        task = _get(f"/api/task/{urllib.parse.quote(task_id)}")
        ws_id = task_id
        if isinstance(task, dict):
            room = task.get("room_id")
            if room:
                ws_id = room
        detail = _get(f"/api/room/by-task/{urllib.parse.quote(task_id)}")
        status = "working"
        if isinstance(task, dict) and task.get("status") in ("completed", "failed"):
            status = task["status"]
        return _rpc_success(msg_id, _task_to_a2a(ws_id, task, status))

    if method == "tasks/cancel":
        return _rpc_success(msg_id, {"id": str(params.get("taskId", "")), "status": "canceled"})

    if method == "message/send":
        task_id = str(params.get("taskId", "")).strip()
        message = params.get("message", {}) or {}
        content = _text_of(message)
        if not task_id or not content:
            return _rpc_error(msg_id, -32602, "缺少 taskId 或 message 文本")
        # 转发为平台任务讨论消息
        resp = _post(f"/api/task/{urllib.parse.quote(task_id)}/discuss", {
            "content": content,
            "agent": "user",
        })
        if not resp.get("ok", True):
            return _rpc_error(msg_id, -32000, f"平台转发消息失败: {resp}")
        return _rpc_success(msg_id, {"taskId": task_id, "messageId": uuid.uuid4().hex[:12]})

    if method == "message/get":
        return _rpc_success(msg_id, {"id": str(params.get("taskId", "")), "messages": []})

    return _rpc_error(msg_id, -32601, f"未知方法: {method}")


# ── HTTP 服务 ─────────────────────────────────────────────────

class A2AHandler(BaseHTTPRequestHandler):
    server_version = "AC4-A2A/4.0.0"

    def _send_json(self, obj: dict, code: int = 200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        _p = self.path.rstrip("/")
        # v1.0 标准发现路径 + v0.2 兼容路径
        if _p in ("/.well-known/agent-card.json", "/.well-known/agent.json"):
            host = self.headers.get("Host", "localhost").split(":")[0]
            port = self.server.server_port
            self._send_json(_agent_card(host, port))
        elif _p == "/health":
            self._send_json({"status": "ok"})
        else:
            self._send_json({"error": "not found", "hint": "/.well-known/agent-card.json"}, 404)

    def do_POST(self):
        if self.path.split("?")[0].rstrip("/") not in ("", "/"):
            self._send_json({"error": "not found"}, 404)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length).decode("utf-8", "replace")
            msg = json.loads(raw)
        except Exception:  # noqa: BLE001
            self._send_json({"jsonrpc": "2.0", "error": {"code": -32700, "message": "解析失败"}}, 400)
            return
        host = self.headers.get("Host", "localhost").split(":")[0]
        port = self.server.server_port
        resp = handle_rpc(msg, host, port)
        self._send_json(resp)

    def log_message(self, fmt, *args):  # noqa: A003
        sys.stderr.write("[a2a] " + (fmt % args) + "\n")


def main() -> None:
    ap = argparse.ArgumentParser(description="A2A 暴露层（agent_community-v4）")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=9104)
    args = ap.parse_args()

    httpd = ThreadingHTTPServer((args.host, args.port), A2AHandler)
    print(f"[a2a] Agent Card: http://{args.host}:{args.port}/.well-known/agent-card.json")
    print(f"[a2a] JSON-RPC : POST http://{args.host}:{args.port}/")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
