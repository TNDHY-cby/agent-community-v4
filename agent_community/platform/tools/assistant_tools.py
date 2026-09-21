"""平台助手对话专用工具 — 平台 Agent 在直聊窗口中的 Harness 运维能力。

平台 Agent 在 /api/assistant/chat 持续会话中可调用以下工具：
  list_harness    — 列出所有已注册 Harness（含桥状态、在线状态）
  launch_harness  — 启动指定 Harness 进程并等待上线
  bridge_test     — 对指定 Harness 的桥通道做实时测试（发测试消息等回报）
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from ..tool_registry import BaseTool, ToolSchema, ToolResult
from ..harness_adapter import harness_manager
from .. import harness_launcher


class ListHarnessTool(BaseTool):
    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name="list_harness",
            description=(
                "列出所有已注册的外部 Harness（外部 Agent），包含 harness_id、名称、"
                "AI 模型、桥目录、桥测试记录等状态，便于掌握当前平台有哪些外部 Agent 已接入。"
            ),
            parameters={
                "type": "object",
                "properties": {},
            },
        )

    async def execute(self, **params) -> ToolResult:
        sessions = dict(harness_manager.sessions or {})
        if not sessions:
            return ToolResult("list_harness", True, "当前没有任何已注册的 Harness")

        lines = []
        for hid, sess in sessions.items():
            info = getattr(sess, "info", None)
            name = getattr(info, "harness_name", hid)
            model = ""
            try:
                model = getattr(info.ai, "model_name", "")
            except Exception:
                model = ""
            bridge_dir = getattr(info, "bridge_dir", "") or ""
            bt = {}
            try:
                bt = (info.metadata or {}).get("bridge_test") or {}
            except Exception:
                bt = {}
            bridge_ok = "已通过" if bt.get("ok") else "未验证"
            online = "在线" if getattr(sess, "online", False) else "离线"
            lines.append(
                f"- {name}（{hid}）\n"
                f"  AI: {model} | 状态: {online} | 桥: {bridge_ok}\n"
                f"  桥目录: {bridge_dir or '未生成'}"
            )
        return ToolResult("list_harness", True, "已注册 Harness 列表：\n" + "\n".join(lines))


class LaunchHarnessTool(BaseTool):
    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name="launch_harness",
            description=(
                "启动（拉起）指定 Harness 的进程，并等待其上线。"
                "用于平台自动启动外部 Agent；要求该 Harness 已配置 acp_command。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "harness_id": {
                        "type": "string",
                        "description": "目标 Harness 的 harness_id（见 list_harness）",
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "等待上线超时秒数，默认 60",
                    },
                },
                "required": ["harness_id"],
            },
        )

    async def execute(self, **params) -> ToolResult:
        harness_id = (params.get("harness_id") or "").strip()
        if not harness_id:
            return ToolResult("launch_harness", False, "harness_id 必填")
        try:
            timeout = min(max(int(params.get("timeout", 60)), 5), 300)
        except Exception:
            timeout = 60

        sess = (harness_manager.sessions or {}).get(harness_id)
        if not sess:
            return ToolResult("launch_harness", False, f"harness {harness_id} 未注册")

        info = getattr(sess, "info", None)
        # 已在线则直接报告
        if getattr(sess, "online", False):
            return ToolResult("launch_harness", True, f"harness {harness_id} 已在线，无需启动")

        ok, msg = await harness_launcher.ensure_harness_online(harness_id, timeout=timeout)
        if ok:
            return ToolResult("launch_harness", True, f"启动成功：{msg}")
        return ToolResult("launch_harness", False, f"启动失败：{msg}")


class BridgeTestTool(BaseTool):
    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name="bridge_test",
            description=(
                "对指定 Harness 的桥通道做实时验证：检查桥目录与桥进程，"
                "然后通过桥通道发送测试消息并等待对象侧真实回报，确认桥是否通畅。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "harness_id": {
                        "type": "string",
                        "description": "目标 Harness 的 harness_id（见 list_harness）",
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "等待回报超时秒数，默认 10，范围 2-30",
                    },
                },
                "required": ["harness_id"],
            },
        )

    async def execute(self, **params) -> ToolResult:
        harness_id = (params.get("harness_id") or "").strip()
        if not harness_id:
            return ToolResult("bridge_test", False, "harness_id 必填")
        try:
            timeout = min(max(int(params.get("timeout", 10)), 2), 30)
        except Exception:
            timeout = 10

        sess = (harness_manager.sessions or {}).get(harness_id)
        if not sess:
            return ToolResult("bridge_test", False, f"harness {harness_id} 未注册")

        info = getattr(sess, "info", None)
        bridge_dir = getattr(info, "bridge_dir", "") or ""
        lines = []

        # 1) 桥目录检查
        dir_ok = bool(bridge_dir) and os.path.isdir(bridge_dir)
        lines.append(f"桥目录: {bridge_dir or '未记录'}" + ("（存在）" if dir_ok else "（不存在）"))

        # 2) 桥进程检查（与平台同口径：匹配 harness_id / bridge_dir）
        procs = _find_bridge_processes(harness_id, bridge_dir)
        lines.append(f"桥进程: {'运行中 ' + str(len(procs)) + ' 个' if procs else '未发现'}")

        # 3) 实时通道测试：发测试消息等回报
        test_id = uuid4().hex[:12]
        payload = {
            "type": "bridge_test",
            "harness_id": harness_id,
            "test_id": test_id,
            "sent_at": datetime.now().isoformat(),
            "message": "平台助手桥验证：请确认桥通道正常，然后回报 bridge-test-result",
            "report_endpoint": "/api/harness/bridge-test-result",
        }
        method = _harness_wakeup_method(harness_id)
        if method == "file_poll":
            send_ok = _file_poll_send(harness_id, payload)
        else:
            send_ok = _pending_push(_pending_bridge_tests(), harness_id, payload)
        lines.append(f"测试消息: 已通过 {method} 发送" if send_ok else "测试消息发送失败")

        if not send_ok:
            lines.append("结论: 桥通道不可达，请先确认 Harness 已启动、桥脚本已运行。")
            return ToolResult("bridge_test", False, "\n".join(lines))

        # 等待回报
        deadline = asyncio.get_event_loop().time() + timeout
        reported = False
        ok = False
        while asyncio.get_event_loop().time() < deadline:
            await asyncio.sleep(0.5)
            bt = {}
            try:
                bt = (sess.metadata or {}).get("bridge_test") or {}
            except Exception:
                bt = {}
            if bt.get("test_id") == test_id:
                reported = True
                ok = bool(bt.get("ok"))
                break

        if reported and ok:
            lines.append(f"结论: 桥通道正常（test_id={test_id}，已收到对象侧回报）")
            return ToolResult("bridge_test", True, "\n".join(lines))
        if reported:
            lines.append(f"结论: 桥通道存在但回报异常（test_id={test_id}）")
            return ToolResult("bridge_test", False, "\n".join(lines))
        lines.append(f"结论: 等待 {timeout}s 未收到回报，桥通道可能未打通")
        return ToolResult("bridge_test", False, "\n".join(lines))


# ---------- 以下为复用 server 内部机制的延迟导入封装 ----------

def _pending_bridge_tests():
    from ..server import pending_bridge_tests
    return pending_bridge_tests


def _pending_push(queue, hid, item):
    from ..server import _pending_push as fn
    return fn(queue, hid, item)


def _file_poll_send(harness_id, payload):
    from ..server import _file_poll_send as fn
    return fn(harness_id, payload)


def _harness_wakeup_method(harness_id):
    from ..server import _harness_wakeup_method as fn
    return fn(harness_id)


def _find_bridge_processes(harness_id, bridge_dir=""):
    from ..server import _find_bridge_processes as fn
    return fn(harness_id, bridge_dir)


class ProbeHarnessTool(BaseTool):
    """探测一个外部对象（harness）的真实接入方式：进程、监听端口、HTTP API 端点。

    平台 Agent 用它「带着对象摸清楚」——用户只需给一个线索（进程名/端口/安装路径），
    平台 Agent 扫描进程和端口、探测候选 HTTP API，返回探测结果和推荐的接入配置，
    从而自动确定 wakeup_method（http_api / file_poll / acp）和 api_base_url 等字段。
    """

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name="probe_harness",
            description=(
                "探测一个外部 Agent/Harness 的真实接入方式：扫描本机进程（按进程名/路径线索）、"
                "扫描监听端口、探测候选端口的 /message 或 /api 端点，判断它是 HTTP API 型、文件型还是 ACP 型。"
                "用于注册前「摸清对象」，自动给出推荐的 wakeup_method 和 api_base_url 等注册配置。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "process_hint": {
                        "type": "string",
                        "description": "进程名线索，如 electron、python、node（模糊匹配）",
                    },
                    "port_hint": {
                        "type": "integer",
                        "description": "已知或猜测的 HTTP API 端口（可选）",
                    },
                    "path_hint": {
                        "type": "string",
                        "description": "安装/工作目录线索（可选），用于进一步定位",
                    },
                },
            },
        )

    async def execute(self, **params) -> ToolResult:
        import subprocess
        process_hint = (params.get("process_hint") or "").strip()
        port_hint = params.get("port_hint")
        path_hint = (params.get("path_hint") or "").strip()

        lines = []

        # 1) 扫进程（匹配进程名/命令行/路径线索）
        proc_candidates = []
        try:
            out = subprocess.check_output(
                ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
                 "Get-CimInstance Win32_Process | Where-Object { $_.Name -match 'electron|python|node|claude' } | ForEach-Object { $_.ProcessId.ToString() + '|' + $_.Name + '|' + ($_.CommandLine -replace '\\s+',' ') }"],
                timeout=25, encoding="utf-8", errors="replace",
            )
            for line in out.splitlines():
                line = line.strip()
                if not line:
                    continue
                if process_hint and process_hint.lower() in line.lower():
                    proc_candidates.append(line[:200])
                elif path_hint and path_hint.lower() in line.lower():
                    proc_candidates.append(line[:200])
            if proc_candidates:
                lines.append(f"进程扫描（线索「{process_hint or path_hint}」）命中 {len(proc_candidates)} 个：")
                for p in proc_candidates[:5]:
                    lines.append("  " + p[:180])
            else:
                # 兜底：列出所有非系统进程的 python/electron/node
                lines.append(f"进程扫描未按线索命中，列出候选进程（electron/python/node）：")
                for line in out.splitlines()[:10]:
                    if any(k in line for k in ("electron", "python", "node")):
                        lines.append("  " + line.strip()[:150])
        except Exception as e:
            lines.append(f"进程扫描失败: {e}")

        # 2) 扫监听端口
        ports = []
        try:
            out = subprocess.check_output(
                ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
                 "Get-NetTCPConnection -State Listen | Where-Object { $_.LocalAddress -in '127.0.0.1','0.0.0.0','::' } | Select-Object -ExpandProperty LocalPort -Unique | Sort-Object"],
                timeout=20, encoding="utf-8", errors="replace",
            )
            ports = [int(x) for x in out.split() if x.strip().isdigit()]
        except Exception as e:
            lines.append(f"端口扫描失败: {e}")

        # 3) 探测候选端口的 HTTP API（用 python urllib，可靠）
        candidates = []
        if port_hint and isinstance(port_hint, int):
            candidates.append(port_hint)
        skip = {80, 443, 135, 445, 3389, 8080, 22, 21}  # 系统常见端口，跳过
        for p in ports:
            if p not in skip and 1000 <= p <= 60000 and p not in candidates:
                candidates.append(p)
            if len(candidates) >= 12:
                break

        api_hits = []
        import urllib.request as _ur
        for p in candidates[:12]:
            for path in ("/", "/message", "/api", "/api/status"):
                url = f"http://127.0.0.1:{p}{path}"
                try:
                    req = _ur.Request(url, method="GET")
                    with _ur.urlopen(req, timeout=2) as resp:
                        api_hits.append(f"  {url} -> HTTP {resp.status}")
                        break
                except _ur.HTTPError as e:
                    # 400/405 也说明端口有 HTTP 服务
                    api_hits.append(f"  {url} -> HTTP {e.code}（有服务）")
                    break
                except Exception:
                    pass

        if api_hits:
            lines.append("探测到的 HTTP API 端点：")
            lines.extend(api_hits[:8])
        else:
            lines.append("未探测到明显 HTTP API（可能为 file_poll 文件型或 acp 型）")

        # 4) 推荐配置
        lines.append("\n推荐接入配置（供注册参考）：")
        if api_hits:
            first = api_hits[0]
            try:
                port = first.split(":")[2].split("/")[0]
            except Exception:
                port = str(port_hint or "未知")
            lines.append(f"  wakeup_method: http_api")
            lines.append(f"  api_base_url: http://127.0.0.1:{port}")
            lines.append(f"  api_message_path: /message")
        elif proc_candidates:
            lines.append("  wakeup_method: file_poll（未发现 HTTP API，文件型）或 acp（若有 CLI 启动命令）")
        else:
            lines.append("  线索不足，需用户补充进程名/端口/路径")

        return ToolResult("probe_harness", True, "\n".join(lines))

