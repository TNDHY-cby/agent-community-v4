# -*- coding: utf-8 -*-
"""platform/protocol_catalog：协议目录单一事实源（V-11）。

一处描述平台对外暴露的每种接入方式，供：
- GET /api/protocols（前端渲染 / 外端 agent 自选）
- A2A supportedInterfaces[]（派生投影）
- 内部 select_endpoint 选择逻辑（参考已实现状态）

设计原则：可发现、可声明、可选择、不撒谎（未实现的协议标 not_implemented，从选择池剔除）。
"""
from __future__ import annotations

from typing import Any


def _grpc_available() -> bool:
    """动态检测 grpcio 是否安装（gRPC 协议状态随环境变化）。"""
    try:
        import grpc  # noqa: F401
        return True
    except ImportError:
        return False

# 协议目录（静态定义部分；运行时 host/port 由调用方填充）
_PROTOCOLS: list[dict[str, Any]] = [
    {
        "id": "http",
        "name": "HTTP REST",
        "direction": "bidirectional",      # inbound / outbound / bidirectional
        "kind": "http",
        "status": "available",             # available / not_implemented / disabled
        "best_for": "通用默认；harness 注册/心跳/消息回报",
        "example": "curl http://127.0.0.1:18920/api/status",
        "requires": "",
    },
    {
        "id": "ws",
        "name": "WebSocket",
        "direction": "outbound",
        "kind": "ws",
        "status": "partial",               # 半实现：有占位端口，见 waker_protocol
        "best_for": "长连接实时通信（规划中）",
        "example": "ws://127.0.0.1:18920/ws",
        "requires": "",
    },
    {
        "id": "pipe",
        "name": "Pipe（本地进程间）",
        "direction": "outbound",
        "kind": "pipe",
        "status": "available",
        "best_for": "本地 harness 进程间通信",
        "example": "见 agent_community/examples/pending_poll_bridge.py",
        "requires": "",
    },
    {
        "id": "acp",
        "name": "ACP（Agent Client Protocol）",
        "direction": "outbound",
        "kind": "stdio",
        "status": "available",
        "best_for": "CLI / 桌面 Agent（拉起子进程走 JSON-RPC stdio）",
        "example": "注册时 acp_command=...；见 agent_community/examples/acp_harness_bridge.py",
        "requires": "",
    },
    {
        "id": "mcp",
        "name": "MCP（Model Context Protocol）",
        "direction": "bidirectional",
        "kind": "stdio",
        "status": "available",
        "best_for": "Claude Code / Cursor 等 MCP 客户端挂载；平台也可作为 MCP 客户端调外部工具",
        "example": "claude --mcp-config '{\"servers\":{\"ac4\":{\"command\":\"python\",\"args\":[\"-m\",\"agent_community.mcp_server\"]}}}'",
        "requires": "独立进程：python -m agent_community.mcp_server",
    },
    {
        "id": "a2a",
        "name": "A2A（Agent-to-Agent）",
        "direction": "bidirectional",
        "kind": "http",
        "status": "available",
        "best_for": "外部 A2A 生态 Agent（Google ADK / LangGraph / AutoGen）发现与派发",
        "example": "GET http://127.0.0.1:9104/.well-known/agent-card.json",
        "requires": "独立进程：python -m agent_community.a2a_server",
    },
    {
        "id": "sse",
        "name": "SSE（Server-Sent Events）",
        "direction": "outbound",
        "kind": "http",
        "status": "not_implemented",
        "best_for": "单向事件流（规划中）",
        "example": "",
        "requires": "",
    },
    {
        "id": "grpc",
        "name": "gRPC",
        "direction": "outbound",
        "kind": "grpc",
        "status": "available" if _grpc_available() else "disabled",
        "best_for": "高性能二进制传输（A2A 第二绑定）",
        "example": "python -m agent_community.grpc_gateway --port 9105",
        "requires": "pip install grpcio grpcio-tools（可选依赖，未装则本协议自动禁用）",
    },
]


def get_protocol_catalog(host: str = "127.0.0.1", http_port: int = 18920) -> list[dict[str, Any]]:
    """返回完整协议目录（含运行时端点坐标）。

    host/http_port 由调用方传入（通常从 request 的 Host 头解析）。
    """
    out = []
    for p in _PROTOCOLS:
        entry = dict(p)
        pid = entry["id"]
        # 填充运行时端点
        if pid == "http":
            entry["endpoint"] = f"http://{host}:{http_port}/api"
        elif pid == "ws":
            entry["endpoint"] = f"ws://{host}:{http_port}/ws"
        elif pid == "mcp":
            entry["endpoint"] = "stdio: python -m agent_community.mcp_server"
        elif pid == "a2a":
            entry["endpoint"] = f"http://{host}:9104/.well-known/agent-card.json"
        else:
            entry["endpoint"] = ""
        out.append(entry)
    return out


def get_available_protocol_ids() -> list[str]:
    """返回已实现协议的 id 列表（供 select_endpoint / A2A supportedInterfaces 使用）。"""
    return [p["id"] for p in _PROTOCOLS if p["status"] == "available"]
