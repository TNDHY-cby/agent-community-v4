# -*- coding: utf-8 -*-
"""多协议接入示例 — 同一个任务，分别用 MCP 和 A2A 两条路接入平台。

这个示例演示「多种接口多种选择，让外端 agent 自己选最合适的」：
- 路径 A：MCP（平台作为工具服务端，外部 MCP 客户端挂载调用）
- 路径 B：A2A（Agent-to-Agent 标准协作，外部 Agent 发现与派发）
- 路径 C：HTTP REST（通用默认，直连平台 API）

用法（先启动平台，再运行本示例）：
    python -m agent_community.examples.protocol_multi_access

不需要安装任何第三方依赖，全部走标准库 HTTP + JSON-RPC。
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.request

BASE_URL = "http://127.0.0.1:18920"
A2A_URL = "http://127.0.0.1:9104"


def _get(path: str, base: str = BASE_URL, timeout: int = 10) -> dict:
    with urllib.request.urlopen(base + path, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def _post(path: str, body: dict, base: str = BASE_URL, timeout: int = 10) -> dict:
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        base + path, data=data,
        headers={"Content-Type": "application/json; charset=utf-8"}, method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def section(title: str):
    print(f"\n{'─'*60}")
    print(f"  {title}")
    print(f"{'─'*60}")


# ── 路径 C：HTTP REST（最直接）───────────────────────────────
def demo_http():
    section("路径 C：HTTP REST 直连平台")
    status = _get("/api/status")
    print(f"  平台状态: {status.get('status')}  版本: {status.get('version')}")
    print(f"  已注册 harness: {status.get('harnesses', '?')}  活跃任务: {status.get('tasks_active', '?')}")

    protocols = _get("/api/protocols")
    print(f"  协议目录: {protocols.get('total', '?')} 种接入方式")
    for p in protocols.get("protocols", []):
        icon = {"available": "✅", "partial": "⚠️", "not_implemented": "🔒", "disabled": "⏸"}.get(p["status"], "?")
        print(f"    {icon} {p['name']:<28} {p['direction']:<16} {p['status']}")
    print("  → 一行命令即可查看：curl http://127.0.0.1:18920/api/protocols")


# ── 路径 A：MCP（平台作为工具服务端）─────────────────────────
def demo_mcp():
    section("路径 A：MCP — 平台作为工具服务端（供 Claude Code / Cursor 等挂载）")
    print("  MCP 是 stdio 协议，外部 MCP 客户端通过子进程挂载：")
    print("    claude --mcp-config '{\"servers\":{\"ac4\":{\"command\":\"python\",")
    print("      \"args\":[\"-m\",\"agent_community.mcp_server\"]}}}'")
    print()
    print("  下面用标准库模拟一个最小 MCP 客户端，演示工具发现与调用：")

    import os as _os
    _env = dict(_os.environ, PYTHONIOENCODING="utf-8")
    proc = subprocess.Popen(
        [sys.executable, "-u", "-m", "agent_community.mcp_server"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL, text=True, encoding="utf-8",
        errors="replace", bufsize=1, env=_env,
    )

    def mcp_call(method, params=None, rid=1):
        proc.stdin.write(json.dumps({
            "jsonrpc": "2.0", "id": rid, "method": method, "params": params or {},
        }) + "\n")
        proc.stdin.flush()
        return json.loads(proc.stdout.readline())

    try:
        r = mcp_call("initialize", {
            "protocolVersion": "2025-06-18", "capabilities": {},
            "clientInfo": {"name": "protocol-demo", "version": "1.0"},
        })
        info = r.get("result", {}).get("serverInfo", {})
        print(f"  ✅ 握手成功: {info.get('name')} v{info.get('version')}")

        r = mcp_call("tools/list", rid=2)
        tools = r.get("result", {}).get("tools", [])
        print(f"  ✅ 发现 {len(tools)} 个工具: {', '.join(t['name'] for t in tools[:5])}…")

        r = mcp_call("tools/call", {"name": "status", "arguments": {}}, rid=3)
        text = r.get("result", {}).get("content", [{}])[0].get("text", "")
        print(f"  ✅ 调用 status 工具: 回包 {len(text)} 字符")
    finally:
        proc.terminate()
        proc.wait(timeout=5)


# ── 路径 B：A2A（Agent-to-Agent 标准协作）────────────────────
def demo_a2a():
    section("路径 B：A2A — Agent-to-Agent 标准协作（外部生态 Agent 发现与派发）")
    print("  A2A 是 HTTP + JSON-RPC 协议，外部 Agent 通过 Agent Card 发现平台：")

    # 尝试 A2A 服务端
    a2a_running = False
    proc = subprocess.Popen(
        [sys.executable, "-u", "-m", "agent_community.a2a_server", "--port", "9104"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        for _ in range(10):
            time.sleep(0.5)
            try:
                card = _get("/.well-known/agent-card.json", base=A2A_URL)
                a2a_running = True
                break
            except Exception:
                continue

        if not a2a_running:
            print("  ⚠️ A2A 服务端未启动（可能端口被占用），跳过演示")
            return

        print(f"  ✅ Agent Card 发现: {card.get('name')}")
        print(f"     描述: {card.get('description', '')[:60]}…")
        si = card.get("supportedInterfaces", [])
        print(f"     支持接口: {len(si)} 个")
        for iface in si:
            print(f"       · {iface.get('protocolBinding')} v{iface.get('protocolVersion')}  {iface.get('url', '')}")

        # 用 A2A JSON-RPC 查询 Agent
        body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "GetExtendedAgentCard", "params": {}}).encode()
        req = urllib.request.Request(
            A2A_URL + "/", data=body,
            headers={"Content-Type": "application/json"}, method="POST",
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            result = json.loads(resp.read().decode("utf-8", "replace"))
        print(f"  ✅ GetExtendedAgentCard: HTTP 200, keys={sorted(result.get('result', {}).keys())[:5]}")
        print("  → 启动 A2A 服务端: python -m agent_community.a2a_server --port 9104")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


# ── 路径选择策略 ──────────────────────────────────────────────
def demo_selection():
    section("路径选择策略（多种接口多种选择）")
    print("  harness 注册时声明 transports + preferred_transport，平台自动选路：")
    print()
    print('    POST /api/harness/register')
    print('    body: {')
    print('      "harness_id": "示例Harness-A",')
    print('      "transports": ["http", "mcp", "a2a"],')
    print('      "preferred_transport": "a2a",   // 显式偏好，可选')
    print('      ...')
    print('    }')
    print()
    print("  平台选择顺序：preferred（若已实现）→ 端点 priority 降序 → 声明顺序")
    print("  未实现的协议（sse/grpc）自动从选择池剔除，绝不选到空实现。")


if __name__ == "__main__":
    print("╔══════════════════════════════════════════════════════════╗")
    print("║   外端Agent生产合作社 — 多协议接入示例                    ║")
    print("║   同一件事，三种协议，按需选择                            ║")
    print("╚══════════════════════════════════════════════════════════╝")

    try:
        demo_http()
    except Exception as e:
        print(f"  ❌ 平台不可达: {e}")
        print("  请先启动平台: python -m agent_community.platform.server --port 18920")
        sys.exit(1)

    try:
        demo_mcp()
    except Exception as e:
        print(f"  ❌ MCP 演示失败: {e}")

    try:
        demo_a2a()
    except Exception as e:
        print(f"  ❌ A2A 演示失败: {e}")

    demo_selection()
    print()
    print("✅ 多协议接入演示完成。")
