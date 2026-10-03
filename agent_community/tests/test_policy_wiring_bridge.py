# -*- coding: utf-8 -*-
"""V-14 步骤5 测试：桥端点（bridge/generate 写盘 + auto-connect 启进程）的策略闸门 + actor 分级。

这是本片**最关键的一组断言**：
  - operator（人点一键注册）→ 放行（不堵 V-12 主流程）
  - external_ai / harness（AI 自主架桥）→ ASK，且**进程绝不能被启动 / 脚本绝不落盘**
"""
from __future__ import annotations

import asyncio
import json

import pytest

from agent_community.platform import policy as policy_mod
from agent_community.platform.policy import (
    ALLOW,
    ASK,
    BRIDGE_LAUNCH,
    BRIDGE_WRITE,
    ACTOR_HARNESS,
    ACTOR_OPERATOR,
    PolicyEngine,
)
from agent_community.platform.protocol import WakeupMethod
from agent_community.platform.routers import harness_messaging as hm


class FakeRequest:
    def __init__(self, body=None, headers=None):
        self.headers = headers or {}
        self._body = body
        self.base_url = "http://127.0.0.1:18920/"

    async def json(self):
        return self._body if self._body is not None else {}


def run(coro):
    return asyncio.run(coro)


def unpack(resp):
    if hasattr(resp, "status_code") and hasattr(resp, "body"):
        return resp.status_code, json.loads(resp.body)
    return 200, resp


class _Info:
    """最小 harness info 替身：file_poll + 有 wakeup_dir → 能走到启动分支。"""

    def __init__(self, wakeup_dir):
        self.wakeup_method = WakeupMethod.FILE_POLL
        self.wakeup_dir = str(wakeup_dir)
        self.acp_command = ""
        self.bridge_dir = ""
        self.bridge_status = ""


class _Sess:
    def __init__(self, wakeup_dir):
        self.info = _Info(wakeup_dir)


@pytest.fixture()
def eng(tmp_path, monkeypatch):
    e = PolicyEngine(data_dir=tmp_path / "_policy")
    monkeypatch.setattr(policy_mod, "policy_engine", e)
    monkeypatch.setattr(hm, "policy_engine", e, raising=False)
    return e


@pytest.fixture()
def harness_env(tmp_path, monkeypatch):
    """隔离 harness 会话与桥表；并把 time.sleep 打桩掉（端点里有 2 秒等存活）。"""
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    monkeypatch.setattr(hm.harness_manager, "sessions", {"h1": _Sess(inbox)}, raising=False)
    monkeypatch.setattr(hm.harness_manager, "bridges", {}, raising=False)
    import time as _t
    monkeypatch.setattr(_t, "sleep", lambda *a, **k: None)
    return inbox


def _stub_popen(monkeypatch):
    """替换 subprocess.Popen，返回调用计数（不真起进程）。"""
    import subprocess
    calls = {"n": 0, "cmd": None}

    class _FakeProc:
        pid = 99999
        returncode = None

        def poll(self):
            return None

    def fake_popen(cmd, *a, **k):
        calls["n"] += 1
        calls["cmd"] = cmd
        return _FakeProc()

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    return calls


# ── auto-connect：actor 分级（拍板项3 的核心）──────────────────────
class TestAutoConnectActorGrading:
    def test_operator_can_launch(self, eng, harness_env, monkeypatch):
        """人点一键注册 → 放行，真的走到启动（V-12 主流程不被堵）。"""
        calls = _stub_popen(monkeypatch)
        req = FakeRequest(body={"harness_id": "h1"})          # 无 token → operator

        status, payload = unpack(run(hm.harness_auto_connect(req)))

        assert calls["n"] == 1, f"operator 应被放行到启动，实得 {calls}"
        assert payload.get("success") is True
        assert payload.get("status") == "connected"

    def test_ai_cannot_launch_without_approval(self, eng, harness_env, monkeypatch):
        """AI 自主架桥 → 202 待审批，**进程绝不能被启动**。"""
        calls = _stub_popen(monkeypatch)
        req = FakeRequest(body={"harness_id": "h1"}, headers={"X-Agent-Token": "x"})

        status, payload = unpack(run(hm.harness_auto_connect(req)))

        assert status == 202, f"AI 发起应 202 待审批，实得 {status}"
        assert payload["status"] == "pending_approval"
        assert calls["n"] == 0, "ASK 时绝不能真的启动进程"
        assert eng.list_pending(only_pending=True), "应入待审批队列"

    def test_pending_record_carries_bridge_type(self, eng, harness_env, monkeypatch):
        _stub_popen(monkeypatch)
        req = FakeRequest(body={"harness_id": "h1"}, headers={"X-Agent-Token": "x"})
        run(hm.harness_auto_connect(req))
        rec = eng.list_pending(only_pending=True)[-1]
        assert rec["op"] == BRIDGE_LAUNCH
        assert "h1" in rec["target"] and "file_poll" in rec["target"]
        assert rec["actor"] == ACTOR_HARNESS

    def test_http_api_needs_no_bridge_and_no_gate(self, eng, harness_env, monkeypatch):
        """http_api 类由平台直接推送、无需桥 → 在原短路点返回，不产生待办。"""
        calls = _stub_popen(monkeypatch)
        hm.harness_manager.sessions["h1"].info.wakeup_method = WakeupMethod.HTTP_API
        req = FakeRequest(body={"harness_id": "h1"}, headers={"X-Agent-Token": "x"})

        _, payload = unpack(run(hm.harness_auto_connect(req)))

        assert payload.get("status") == "ready"
        assert calls["n"] == 0
        assert not eng.list_pending(only_pending=True), "无需桥的场景不该产生待办"


# ── bridge/generate：写盘前拦截 ───────────────────────────────────
class TestBridgeGenerateGate:
    @pytest.fixture()
    def bridge_env(self, tmp_path, monkeypatch):
        from agent_community.platform.routers import harness_bridge as hb
        monkeypatch.setattr(hb, "policy_engine", eng, raising=False)
        monkeypatch.setattr(
            hb.harness_manager, "sessions", {"h1": _Sess(tmp_path / "inbox")}, raising=False
        )
        return hb

    def test_operator_generate_allowed(self, eng, bridge_env, tmp_path, monkeypatch):
        """operator 生成桥脚本 → 放行（走 actor_override）。"""
        from agent_community.platform import policy as pm
        monkeypatch.setattr(pm, "policy_engine", eng)

        written = {"n": 0}

        def fake_generate(template, params, target):
            written["n"] += 1
            target.mkdir(parents=True, exist_ok=True)
            f = target / "bridge.py"
            f.write_text("# fake", encoding="utf-8")
            return f

        monkeypatch.setattr("agent_community.platform.bridge_factory.generate", fake_generate)

        req = FakeRequest(body={"template": "file_poll"})     # 无 token → operator
        status, payload = unpack(run(bridge_env.harness_bridge_generate("h1", req)))

        assert written["n"] == 1, "operator 应被放行到生成"
        assert payload.get("success") is True

    def test_ai_generate_asks_and_writes_nothing(self, eng, bridge_env, monkeypatch):
        """AI 生成桥脚本 → 202 待审批，**脚本绝不落盘**。"""
        from agent_community.platform import policy as pm
        monkeypatch.setattr(pm, "policy_engine", eng)

        written = {"n": 0}

        def fake_generate(*a, **k):
            written["n"] += 1
            raise AssertionError("ASK 时不该走到 generate（它会 write_text 落盘）")

        monkeypatch.setattr("agent_community.platform.bridge_factory.generate", fake_generate)

        req = FakeRequest(body={"template": "file_poll"}, headers={"X-Agent-Token": "x"})
        status, payload = unpack(run(bridge_env.harness_bridge_generate("h1", req)))

        assert status == 202, f"AI 发起应 202，实得 {status}"
        assert written["n"] == 0, "ASK 时绝不能真的写盘"
        assert eng.list_pending(only_pending=True)

    def test_deny_rule_blocks_generate(self, eng, bridge_env, monkeypatch):
        """配 DENY 规则 → 403，且不写盘（即使 operator 也不能越权）。"""
        from agent_community.platform import policy as pm
        from agent_community.platform.policy import DENY
        monkeypatch.setattr(pm, "policy_engine", eng)
        eng._config.setdefault("rules", []).append(
            {"id": "no.bridge", "op": BRIDGE_WRITE, "match": "*", "decision": DENY, "note": "禁生成桥"}
        )
        written = {"n": 0}
        monkeypatch.setattr("agent_community.platform.bridge_factory.generate",
                            lambda *a, **k: written.__setitem__("n", written["n"] + 1))

        req = FakeRequest(body={"template": "file_poll"})
        status, _ = unpack(run(bridge_env.harness_bridge_generate("h1", req)))
        assert status == 403
        assert written["n"] == 0
