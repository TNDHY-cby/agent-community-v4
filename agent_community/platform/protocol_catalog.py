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


def _catalog_base() -> str:
    """平台基址，用于示例字符串 —— **运行时解析，不写死本机端口**。

    ★2026-10-13（老板：「本地网址不应该引入」「本地端口因人而异」）：
      原来这里的示例写死 `http://127.0.0.1:18920`；用户换端口后示例就是错的。
      取不到解析器（这个模块要保持可单独 import）时才退回默认端口。
    """
    try:
        from ..platform_address import base_url
        return base_url()
    except Exception:  # noqa: BLE001
        import os
        return "http://127.0.0.1:" + (os.environ.get("AC_PORT") or "18920")


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
        "layer": "transport_generic",
        "semantics": "通用通道：注册/心跳/回报和全部 REST。**不表达**身份与任务归属，那些在平台的名单与账本里",
        "name": "HTTP REST",
        "direction": "bidirectional",      # inbound / outbound / bidirectional
        "kind": "http",
        "status": "available",             # available / not_implemented / disabled
        "best_for": "通用默认；harness 注册/心跳/消息回报",
        "example": f"curl {_catalog_base()}/api/status",
        "requires": "",
    },
    {
        "id": "ws",
        "layer": "transport_events",
        "semantics": "事件流（平台→对端/前端单向推）。**不承载**请求与回执语义",
        "name": "WebSocket",
        "direction": "outbound",
        "kind": "ws",
        "status": "partial",               # 半实现：有占位端口，见 waker_protocol
        "best_for": "长连接实时通信（规划中）",
        "example": _catalog_base().replace("http://", "ws://") + "/ws",
        "requires": "",
    },
    {
        "id": "pipe",
        "layer": "transport_local",
        "semantics": "本机进程间交换。跨机器无意义",
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
        "layer": "transport_session",
        "semantics": "会话轮次（拉起进程→initialize→session→prompt）：**有回合语义**，但对端必须是可被拉起的进程",
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
        "layer": "transport_tools",
        "semantics": "**工具面**：它调用平台的工具与技能（14 个）。**不表达**身份、任务归属与验收责任，也不能让平台反过来驱动它的模型",
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
        "layer": "transport_task",
        "semantics": "**对等任务语义**：AgentCard 发现、任务派发/查询/撤回、产物回传（一元）。离「让对端干完整活」最近，但不含验收与证据规则",
        "name": "A2A（Agent-to-Agent）",
        "direction": "bidirectional",
        "kind": "http",
        "status": "available",
        "best_for": "外部 A2A 生态 Agent（Google ADK / LangGraph / AutoGen）发现与派发",
        "example": "GET http://<平台主机>:<A2A端口>/.well-known/agent-card.json",
        # 38 号实施任务书（候选C / 稿A C1+C4）：默认绑回环 + 入站 Bearer 鉴权（读卡公开、调用须凭据）
        "requires": (
            "独立进程：python -m agent_community.a2a_server（默认绑 127.0.0.1；"
            "非回环需鉴权就绪，否则 fail-closed 拒绝监听）；POST 调用带 Authorization: Bearer <agent_token>"
        ),
    },
    {
        "id": "sse",
        "layer": "transport_events",
        "semantics": "事件流（未实现）。客户端来问就如实说不支持",
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
        "layer": "transport_task",
        "semantics": "与 A2A 同一套任务语义的二进制绑定（一元 RPC），适合高频/跨机",
        "name": "gRPC",
        "direction": "outbound",
        "kind": "grpc",
        "status": "available" if _grpc_available() else "disabled",
        "best_for": "高性能二进制传输（A2A 第二绑定）",
        "example": "python -m agent_community.grpc_gateway --port 9105",
        # 38 号实施任务书（稿A C1+C2）：默认绑回环 + 入站 Bearer 拦截器（与 9104 同一决策函数）
        "requires": (
            "pip install grpcio grpcio-tools（可选依赖，未装则本协议自动禁用）；"
            "默认绑 127.0.0.1；调用需 metadata authorization: Bearer <agent_token>"
        ),
    },
]


def _runtime_status(entry: dict) -> str:
    """条目的**运行时**状态。

    ⚠️ 2026-10-06 修：`_PROTOCOLS` 里的 `status` 是**模块导入时**求值的，
    于是"装了 grpcio 但服务没重启"时目录仍一直报 `disabled` ——
    与 `_grpc_available()` docstring 承诺的"随环境变化"不符（实测：装完 grpcio 后
    运行中的平台仍报 disabled，只有重启才变）。凡"随环境变化"的状态必须**调用时**算。
    """
    if entry.get("id") == "grpc":
        return "available" if _grpc_available() else "disabled"
    return str(entry.get("status", ""))


# ── 开箱形态与对外图标（2026-10-22 加；起因见下方注释）────────────────────
#
# ★事故：README 曾写「8 种协议」，把 WS（部分）、SSE（规划中）、gRPC（要另装 grpcio）
#   都算成"已通"。懂行的人一眼看穿，**对外夸大一次，信任就没了**。
# 根因：README 的矩阵是**手写**的，而真实状态在代码里 —— 两处必然漂移。
# 解法：把"对外该显示什么"也做成代码里的**派生结果**，README 矩阵由守卫逐行比对。
#
#: 每份协议"开箱即用"的形态：
#:   builtin      —— 平台进程自带，起服务就能用
#:   sidecar      —— 需**另起一个进程**，但依赖已在 requirements.txt 里（MCP / A2A）
#:   optional_dep —— 需**另装可选依赖**（gRPC 的 grpcio），未装即 `disabled`
_OPENBOX_MODE: dict[str, str] = {
    "http": "builtin", "ws": "builtin", "pipe": "builtin", "acp": "builtin",
    "mcp": "sidecar", "a2a": "sidecar", "sse": "builtin", "grpc": "optional_dep",
}


def openbox_mark(entry: dict) -> str:
    """对外该用什么说法：`ready` / `partial` / `optional` / `planned` / `absent`。

    README 的协议矩阵、`/api/protocols` 的展示都只认这一个派生结果（一处真相）。
    """
    status = _runtime_status(entry)
    mode = entry.get("mode") or _OPENBOX_MODE.get(entry.get("id", ""), "builtin")
    if status == "available":
        return "optional" if mode == "optional_dep" else "ready"
    if status == "partial":
        return "partial"
    if status == "not_implemented":
        return "planned"
    return "absent"          # disabled（如未装 grpcio 的 gRPC）


#: 图标与短词：README 矩阵必须与之逐行一致（守卫见 test_doc_credibility）
OPENBOX_ICON = {"ready": "✅", "partial": "⚠️", "optional": "🔒", "planned": "🔒", "absent": "🔒"}
OPENBOX_TEXT = {"ready": "已通", "partial": "部分", "optional": "可选",
                "planned": "规划中", "absent": "未安装"}


def get_protocol_catalog(host: str = "", http_port: int = 0) -> list[dict[str, Any]]:
    """返回完整协议目录（含运行时端点坐标）。

    host/http_port 由调用方传入（通常从 request 的 Host 头解析）。
    **没传时按"本实例自己的地址"填**（`platform_address`）—— 而不是写死一个本机端口：
      老板 2026-10-13：「本地端口因人而异」，所以默认值必须**运行时算**。
    """
    if not host or not http_port:
        try:
            from ..platform_address import HOST as _h, resolve_port as _rp
            host = host or _h
            http_port = http_port or _rp()
        except Exception:  # noqa: BLE001
            import os
            host = host or "127.0.0.1"
            http_port = http_port or int(os.environ.get("AC_PORT") or 18920)
    out = []
    for p in _PROTOCOLS:
        entry = dict(p)
        pid = entry["id"]
        entry["status"] = _runtime_status(entry)      # 随环境变化的状态在调用时算
        # 开箱形态 + 对外口径（README 矩阵与本接口共用，避免两处手写漂移）
        entry["mode"] = _OPENBOX_MODE.get(pid, "builtin")
        entry["openbox"] = openbox_mark(entry)
        # 填充运行时端点
        if pid == "http":
            entry["endpoint"] = f"http://{host}:{http_port}/api"
        elif pid == "ws":
            entry["endpoint"] = f"ws://{host}:{http_port}/ws"
        elif pid == "mcp":
            entry["endpoint"] = "stdio: python -m agent_community.mcp_server"
        elif pid == "a2a":
            entry["endpoint"] = f"http://{host}:9104/.well-known/agent-card.json"
        elif pid == "grpc":
            # 端点只在**真能起网关**时才给（grpcio 缺失 -> disabled -> 空端点）。
            # 这条守住原设计意图：不得凭空捏造端点（对应既有测试
            # `TestEndpointFilling::test_other_protocols_have_empty_endpoint` 的初衷）。
            entry["endpoint"] = f"{host}:9105" if entry["status"] == "available" else ""
        else:
            entry["endpoint"] = ""
        out.append(entry)
    return out


def get_available_protocol_ids() -> list[str]:
    """返回已实现协议的 id 列表（供 select_endpoint / A2A supportedInterfaces 使用）。

    状态一律取**运行时**值（见 `_runtime_status`），保证"装没装 grpcio"当场反映，
    而不是沿用模块导入时的快照。
    """
    return [p["id"] for p in _PROTOCOLS if _runtime_status(p) == "available"]
