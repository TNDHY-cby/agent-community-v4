# -*- coding: utf-8 -*-
"""V-27 平台生命周期端点测试：`GET /api/system/lifecycle` 与 `POST /api/system/shutdown`。

⚠️ **本文件绝不真杀进程**：`watchdog_pids` / `port_pids` / `port_listening` / `kill_pid` /
`_schedule_self_exit` 全部打桩。真杀进程的验证走**隔离实例**手工实测（见 V27 稿与提交说明）。
"""
from __future__ import annotations

import asyncio

import pytest

from agent_community.platform.routers import system as sysmod


def run(coro):
    return asyncio.run(coro)


class _Req:
    """最小请求替身：端点只用到 headers / client / url.port。"""

    def __init__(self, origin=None, referer=None, pin=None, port=18920, client="127.0.0.1"):
        h = {}
        if origin:
            h["Origin"] = origin
        if referer:
            h["Referer"] = referer
        if pin is not None:
            h["X-Shutdown-Pin"] = pin
        self.headers = h
        self.client = type("C", (), {"host": client})()
        self.url = type("U", (), {"port": port})()


class TestOriginCheck:
    def test_no_origin_is_allowed(self):
        ok, why = sysmod.origin_allowed(_Req())
        assert ok and why == "no-origin"

    @pytest.mark.parametrize("origin", ["http://127.0.0.1:18920", "http://localhost:5173",
                                        "http://[::1]:18920"])
    def test_local_origins_allowed(self, origin):
        ok, why = sysmod.origin_allowed(_Req(origin=origin))
        assert ok and why == "local-origin"

    def test_foreign_origin_rejected(self):
        """★这是加来源校验的原因：网页也能朝本机端口发请求。"""
        ok, why = sysmod.origin_allowed(_Req(origin="https://evil.example.com"))
        assert not ok and "evil.example.com" in why

    def test_referer_used_when_origin_absent(self):
        ok, _ = sysmod.origin_allowed(_Req(referer="https://evil.example.com/page"))
        assert not ok


class TestPin:
    def test_pin_from_env(self, monkeypatch):
        monkeypatch.setenv("AC4_SHUTDOWN_PIN", "1234")
        assert sysmod.shutdown_pin() == "1234"

    def test_pin_from_env_file(self, monkeypatch, tmp_path):
        monkeypatch.delenv("AC4_SHUTDOWN_PIN", raising=False)
        (tmp_path / "deploy").mkdir()
        (tmp_path / "deploy" / ".env").write_text(
            "# 注释\nAC4_SHUTDOWN_PIN=  abcd  \nOTHER=1\n", encoding="utf-8")
        monkeypatch.setattr(sysmod, "_REPO_ROOT", tmp_path, raising=False)
        assert sysmod.shutdown_pin() == "abcd"

    def test_empty_pin_means_no_check(self, monkeypatch, tmp_path):
        monkeypatch.delenv("AC4_SHUTDOWN_PIN", raising=False)
        monkeypatch.setattr(sysmod, "_REPO_ROOT", tmp_path, raising=False)
        assert sysmod.shutdown_pin() == ""


class _StubKiller:
    """记录顺序、模拟"端口还能不能听"。"""

    def __init__(self, listening_after=()):
        self.order: list = []
        self.killed: list = []
        self.self_exit_scheduled = False
        self.listening = {9104, 9105, 18920}
        self.stuck = set(listening_after)      # 这些端口"杀不掉"

    def install(self, monkeypatch):
        def _watchdogs(server_port=None):
            self.order.append("watchdog")
            return [111, 222]

        def _port_pids(port):
            return [9000 + port % 100]

        def _kill(pid, tree=False):
            self.killed.append((pid, tree))
            return True

        def _listening(port):
            # 打桩语义：**没被"卡住"的端口视为已停止**（self.stuck 模拟"杀不掉的端口"）
            return port in self.stuck

        def _alive(pid):
            return False

        monkeypatch.setattr(sysmod, "watchdog_pids", _watchdogs, raising=False)
        monkeypatch.setattr(sysmod, "_cmdline_has", lambda pid, needle: True, raising=False)
        monkeypatch.setattr(sysmod, "port_pids", _port_pids, raising=False)
        monkeypatch.setattr(sysmod, "kill_pid", _kill, raising=False)
        monkeypatch.setattr(sysmod, "port_listening", _listening, raising=False)
        monkeypatch.setattr(sysmod, "_pid_alive", _alive, raising=False)

        def _sched(delay=0.8):
            self.self_exit_scheduled = True
        monkeypatch.setattr(sysmod, "_schedule_self_exit", _sched, raising=False)


class TestShutdownEndpoint:
    def test_foreign_origin_is_403(self, monkeypatch):
        from agent_community.platform.routers.system import system_shutdown
        k = _StubKiller(); k.install(monkeypatch)
        out = run(system_shutdown(_Req(origin="https://evil.example.com")))
        assert out.status_code == 403
        assert k.killed == [] and not k.self_exit_scheduled, "被拒时不许有任何关停动作"

    def test_wrong_pin_is_403_when_pin_set(self, monkeypatch):
        from agent_community.platform.routers.system import system_shutdown
        monkeypatch.setenv("AC4_SHUTDOWN_PIN", "1234")
        k = _StubKiller(); k.install(monkeypatch)
        out = run(system_shutdown(_Req(pin="0000")))
        assert out.status_code == 403
        assert k.killed == [] and not k.self_exit_scheduled

    def test_correct_pin_and_local_origin_stops_in_order(self, monkeypatch):
        """★顺序断言：看门狗 → A2A → grpc → 平台（先停看门狗，否则它会把服务拉回来）。"""
        from agent_community.platform.routers.system import system_shutdown
        monkeypatch.setenv("AC4_SHUTDOWN_PIN", "1234")
        k = _StubKiller()
        k.stuck = set()                      # 都能杀掉
        k.install(monkeypatch)
        out = run(system_shutdown(_Req(origin="http://127.0.0.1:18920", pin="1234")))
        assert k.order[0] == "watchdog", f"必须先停看门狗，实得 {k.order}"
        assert k.self_exit_scheduled, "最后要关掉平台自己（响应返回后）"
        assert out["success"] is True
        assert set(out["stopped"]) >= {"watchdog", "a2a", "grpc", "platform"}
        assert out["still_running"] == []

    def test_unstoppable_target_is_reported_not_hidden(self, monkeypatch):
        """★准则⑤：没关掉的必须出现在 still_running，不许自称"全部关闭"。"""
        from agent_community.platform.routers.system import system_shutdown
        monkeypatch.setenv("AC4_SHUTDOWN_PIN", "1234")
        k = _StubKiller(listening_after={9105})
        k.install(monkeypatch)
        k.listening = {18920, 9104, 9105}
        out = run(system_shutdown(_Req(origin="http://127.0.0.1:18920", pin="1234")))
        assert any("9105" in x for x in out["still_running"]), out
        assert "没关掉" in out["message"]

    def test_no_pin_configured_allows_local_call(self, monkeypatch, tmp_path):
        from agent_community.platform.routers.system import system_shutdown
        monkeypatch.delenv("AC4_SHUTDOWN_PIN", raising=False)
        monkeypatch.setattr(sysmod, "_REPO_ROOT", tmp_path, raising=False)
        k = _StubKiller(); k.install(monkeypatch)
        out = run(system_shutdown(_Req(origin="http://localhost:18920")))
        assert out["success"] is True and k.self_exit_scheduled


class TestLifecycleEndpoint:
    def test_shape(self, monkeypatch):
        from agent_community.platform.routers.system import system_lifecycle
        monkeypatch.setattr(sysmod, "watchdog_pids", lambda: [7], raising=False)
        monkeypatch.setattr(sysmod, "port_listening", lambda p: p == 9104, raising=False)
        monkeypatch.setenv("AC4_SHUTDOWN_PIN", "9999")
        out = run(system_lifecycle(_Req()))
        assert out["watchdog_running"] is True
        assert out["a2a_listening"] is True and out["grpc_listening"] is False
        assert out["pin_required"] is True
        assert out["server_port"] == 18920

class TestWatchdogScope:
    """★防"误杀别的实例的看门狗"：没有本实例记录时**一个都不杀**，并如实警告。"""

    def test_no_record_means_no_kill_and_a_warning(self, monkeypatch, tmp_path):
        from agent_community.platform.routers.system import system_shutdown
        monkeypatch.delenv("AC4_SHUTDOWN_PIN", raising=False)
        monkeypatch.setattr(sysmod, "_REPO_ROOT", tmp_path, raising=False)
        calls = []

        def _kill(pid, tree=False):
            calls.append(pid)
            return True
        monkeypatch.setattr(sysmod, "watchdog_pids", lambda port=None: [], raising=False)
        monkeypatch.setattr(sysmod, "port_pids", lambda p: [1], raising=False)
        monkeypatch.setattr(sysmod, "port_listening", lambda p: False, raising=False)
        monkeypatch.setattr(sysmod, "kill_pid", _kill, raising=False)
        monkeypatch.setattr(sysmod, "_schedule_self_exit", lambda delay=0.8: None, raising=False)
        out = system_shutdown_sync()
        assert "未找到本实例的看门狗记录" in out["detail"]["watchdog"]
        assert out["warnings"] and "手动停" in out["warnings"][0]
        assert 111 not in calls and 222 not in calls, "不许杀任何未确认的看门狗"


def system_shutdown_sync():
    from agent_community.platform.routers.system import system_shutdown
    return run(system_shutdown(_Req(origin="http://127.0.0.1:18920")))

class TestPortOwnershipGuard:
    """★防"在隔离实例点关停，把生产的 A2A/grpc 杀了"：只杀**本项目**的进程。"""

    def test_foreign_port_owner_is_not_killed(self, monkeypatch, tmp_path):
        from agent_community.platform.routers.system import system_shutdown
        monkeypatch.delenv("AC4_SHUTDOWN_PIN", raising=False)
        monkeypatch.setattr(sysmod, "_REPO_ROOT", tmp_path, raising=False)
        monkeypatch.setattr(sysmod, "watchdog_pids", lambda port=None: [], raising=False)
        monkeypatch.setattr(sysmod, "port_pids", lambda p: [4242], raising=False)
        monkeypatch.setattr(sysmod, "port_listening", lambda p: True, raising=False)
        monkeypatch.setattr(sysmod, "_cmdline_has", lambda pid, needle: False, raising=False)
        killed = []
        monkeypatch.setattr(sysmod, "kill_pid", lambda pid, tree=False: killed.append(pid) or True, raising=False)
        monkeypatch.setattr(sysmod, "_schedule_self_exit", lambda delay=0.8: None, raising=False)
        out = run(system_shutdown(_Req(origin="http://127.0.0.1:18920")))
        assert 4242 not in killed, "非本项目进程不许杀"
        assert any("非本项目进程" in x for x in out["still_running"] + [out["detail"].get("a2a", "")]), out
