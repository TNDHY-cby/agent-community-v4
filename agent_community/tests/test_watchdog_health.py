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


class TestGuiShellDetection:
    """V19 缺陷②：GUI 壳判据必须按**本守护自己的 port**。

    ★2026-10-15（V50）**升级为行为判据**：原来这里是两条**源码文本**断言
      （"函数体里不许出现 18920" / "必须出现 `'{port}'`"）——
      按本项目铁律那不算守卫（它只保证写法、不保证行为），而且实现一改写法就误伤
      （跨平台化那一改就把它撞红了，红的原因和它想守的东西毫无关系）。
      现在判据抽成纯函数 `_looks_like_gui_shell(name, cmdline, port)`，**直接喂合成行**。
    """

    def test_recognizes_the_real_gui_shell_shape(self):
        cmd = r'"C:\py\python.exe" -m agent_community.gui --port 18920'
        assert wd._looks_like_gui_shell("python.exe", cmd, 18920) is True

    def test_other_port_is_not_our_shell(self):
        """★缺陷②本体：平台 GUI（18920）在跑，不能让 **9105** 的守护误入"只守护模式"。"""
        cmd = r'"C:\py\python.exe" -m agent_community.gui --port 18920'
        assert wd._looks_like_gui_shell("python.exe", cmd, 9105) is False

    def test_server_cmdline_is_not_a_gui_shell(self):
        cmd = r'"C:\py\python.exe" -m agent_community.platform.server --port 18920'
        assert wd._looks_like_gui_shell("python.exe", cmd, 18920) is False

    def test_non_python_process_is_not_a_shell(self):
        assert wd._looks_like_gui_shell("node.exe", "node gui.js --port 18920", 18920) is False

    def test_empty_cmdline_is_not_a_shell(self):
        assert wd._looks_like_gui_shell("python.exe", "", 18920) is False

    def test_exists_really_reads_the_process_table(self, monkeypatch):
        """行为判据（不是源码文本）：`_gui_shell_exists` 真的去读进程表，并按 port 过滤。"""
        if wd.PROBE is None:
            pytest.skip("本机 procprobe 加载失败：这条判据的前提不成立")
        rows = [(1, 0, "python.exe", r'python -m agent_community.gui --port 18920')]
        monkeypatch.setattr(wd.PROBE, "iter_processes", lambda: iter(rows))
        wd._gui_cache.update(ts=0.0, found=False)
        assert wd._gui_shell_exists(18920, cache_seconds=0.0) is True
        wd._gui_cache.update(ts=0.0, found=False)
        assert wd._gui_shell_exists(9105, cache_seconds=0.0) is False, \
            "别的端口的守护被平台 GUI 挡住了（V19 缺陷②回归）"


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
