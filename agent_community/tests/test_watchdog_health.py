# -*- coding: utf-8 -*-
"""V19 补测（28 号步骤2 点名的三条）：`--health tcp` 健康判据 / GUI 正则不硬编码 / 启动闸按端口幂等。

背景（V19 稿 §2.1 的两处前置缺陷）：
  ① 看门狗 `probe()` 原本是 "TCP **且** GET /api/status" —— gRPC 网关没有 /api/status，
     健康恒 false → **每轮重拉**、反复抢端口；
  ② `_gui_shell_exists` 正则原本硬编码 `18920|{port}` —— 给 9105 用时，只要平台 GUI 在跑
     （命令行含 18920），9105 守护会误入"只守护模式" → **网关挂了也不拉**。
"""
from __future__ import annotations

import io
import os
import socket
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from agent_community.tools import watchdog as wd  # noqa: E402

DEV_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
AUTOSTART_PS1 = os.path.join(DEV_ROOT, "watchdog_autostart.ps1")


def _free_tcp_listener() -> tuple[socket.socket, int]:
    """起一个**只有 TCP、没有 HTTP** 的监听（模拟网关 9105 这类目标）。"""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("127.0.0.1", 0))
    s.listen(8)
    return s, s.getsockname()[1]


class _HealthHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self):
        body = b'{"status": "running"}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


def _http_health_server() -> tuple[ThreadingHTTPServer, int]:
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _HealthHandler)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, srv.server_address[1]


@pytest.fixture
def tcp_only():
    s, port = _free_tcp_listener()
    yield port
    s.close()


@pytest.fixture
def http_health():
    srv, port = _http_health_server()
    yield port
    srv.shutdown()
    srv.server_close()


class TestHealthModes:
    """V19 决策点②：`--health tcp`（拍板已通过）。"""

    def test_default_mode_is_http_unchanged(self):
        """默认必须是 http —— 18920 现有路径**行为零变化**是拍板前提。"""
        assert wd._HEALTH_MODE == "http", "默认模式被改动会影响 18920 现有守护"

    def test_tcp_only_listener_fails_in_http_mode(self, tcp_only):
        """缺陷①的复现：只有 TCP 没有 /api/status → http 判据恒 false。"""
        ok, detail = wd.probe(tcp_only, mode="http")
        assert ok is False

    def test_tcp_only_listener_passes_in_tcp_mode(self, tcp_only):
        """修复：--health tcp 下必须判为健康（否则看门狗会每轮重拉）。"""
        ok, detail = wd.probe(tcp_only, mode="tcp")
        assert ok is True, detail
        assert "TCP" in detail

    def test_tcp_mode_still_detects_dead_port(self, http_health):
        """tcp 模式不能"见谁都绿"：端口没监听必须报 false。"""
        ok, _ = wd.probe(http_health + 1000, mode="tcp")
        assert ok is False

    def test_global_mode_plumbing(self, tcp_only, monkeypatch):
        """`--health tcp` 经 main() 设置全局 —— probe 不带 mode 时必须尊重它。"""
        monkeypatch.setattr(wd, "_HEALTH_MODE", "tcp")
        ok, detail = wd.probe(tcp_only)
        assert ok is True
        monkeypatch.setattr(wd, "_HEALTH_MODE", "http")
        ok2, _ = wd.probe(tcp_only)
        assert ok2 is False

    def test_http_mode_still_requires_running_status(self, http_health):
        """历史判据不受影响：有 HTTP 但没到 /api/status 的仍按原逻辑。"""
        ok, detail = wd.probe(http_health, mode="http")
        assert ok is True, detail
        # 不可达的另一段
        ok2, _ = wd.probe(http_health + 1000, mode="http")
        assert ok2 is False


class TestGuiShellRegexNotHardcoded:
    """V19 缺陷②：GUI 壳正则不得硬编码 18920。"""

    def test_function_body_has_no_hardcoded_18920(self):
        src = io.open(wd.__file__, encoding="utf-8").read()
        i = src.find("def _gui_shell_exists")
        assert i > 0, "找不到 _gui_shell_exists"
        body = src[i:src.find("\ndef ", i + 1)]
        assert "18920" not in body, (
            "_gui_shell_exists 里仍有硬编码 18920 —— 会让非 18920 的守护"
            "因平台 GUI 在跑而误入只守护模式（网关挂了不拉）")

    def test_filter_uses_current_port(self):
        src = io.open(wd.__file__, encoding="utf-8").read()
        i = src.find("def _gui_shell_exists")
        body = src[i:src.find("\ndef ", i + 1)]
        assert "'{port}'" in body, "GUI 壳过滤应按本守护自己的 port"


class TestAutostartPortAware:
    """28 号步骤2 第三条：`Start-Watchdog` 按端口幂等（源码级锁意图；实测见 §7 验证）。"""

    @pytest.fixture(scope="class")
    def ps1(self):
        if not os.path.exists(AUTOSTART_PS1):
            pytest.skip("watchdog_autostart.ps1 是 dev-only 文件，发布副本里没有（已随发布冻结跳过）")
        return io.open(AUTOSTART_PS1, encoding="utf-8-sig").read()

    def test_start_guard_is_port_aware(self, ps1):
        """旧版是"存在任意守护就跳过"——第二个守护会被直接挡掉，必须按 Kind/端口识别。"""
        i = ps1.find("function Start-Watchdog")
        body = ps1[i:ps1.find("\nfunction ", i + 1)]
        assert "Get-WatchdogProcs -Kind $Kind" in body, "启动闸必须按目标端口识别"
        assert "守护已在运行，不重复启动" not in body, "旧的无差别幂等闸必须移除"

    def test_grpc_target_passes_health_tcp(self, ps1):
        i = ps1.find("function Start-Watchdog")
        body = ps1[i:ps1.find("\nfunction ", i + 1)]
        assert "'--health', 'tcp'" in body, "网关目标必须带 --health tcp"

    def test_default_target_is_platform(self, ps1):
        """默认 Target=platform ⇒ 与 V19 之前行为一致（拍板前提）。"""
        assert "[string]$Target = 'platform'" in ps1

    def test_second_autostart_file_declared(self, ps1):
        assert "ac_v4_watchdog_grpc.cmd" in ps1, "第二守护需独立 .cmd（隔离模型）"
