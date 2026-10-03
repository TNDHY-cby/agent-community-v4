# -*- coding: utf-8 -*-
"""V-14 步骤3 测试：file_tools / web_tool / plugins 三处接入。

沿用 test_plugins.py 的 FakeRequest 替身风格；断言仍以**副作用**为主
（文件是否真被写、Popen 是否真被调用），不只比文案。
"""
from __future__ import annotations

import asyncio
import json

import pytest

from agent_community.platform import policy as policy_mod
from agent_community.platform.policy import (
    ALLOW,
    ASK,
    DENY,
    FILE_WRITE,
    NETWORK_EGRESS,
    SHELL_EXEC,
    ACTOR_HARNESS,
    ACTOR_OPERATOR,
    ACTOR_PLATFORM_AI,
    PolicyEngine,
    actor_from_request,
)
from agent_community.platform.tools.file_tools import WriteFileTool
from agent_community.platform.tools.web_tool import WebFetchTool


class _Query:
    def __init__(self, d=None):
        self._d = d or {}

    def get(self, key, default=""):
        return self._d.get(key, default)


class FakeRequest:
    """最小 Request 替身：headers + query_params + async json()。"""

    def __init__(self, body=None, headers=None, query=None):
        self.headers = headers or {}
        self.query_params = _Query(query)
        self._body = body

    async def json(self):
        return self._body if self._body is not None else {}


def run(coro):
    return asyncio.run(coro)


@pytest.fixture()
def temp_policy(tmp_path, monkeypatch):
    eng = PolicyEngine(data_dir=tmp_path)
    monkeypatch.setattr(policy_mod, "policy_engine", eng)
    return eng


def _deny_rule(eng, op, pattern, rid="test.deny"):
    eng._config.setdefault("rules", []).append(
        {"id": rid, "op": op, "match": pattern, "decision": DENY, "note": "测试用拒绝规则"}
    )


# ── file_tools ───────────────────────────────────────────────────
class TestWriteFileTool:
    def test_allow_writes_file(self, temp_policy, tmp_path):
        """file.write 出厂 ALLOW → 真写盘。"""
        target = tmp_path / "out" / "a.txt"
        res = run(WriteFileTool().execute(path=str(target), content="hello"))
        assert res.success is True, res.content[:120]
        assert target.exists() and target.read_text(encoding="utf-8") == "hello"

    def test_deny_blocks_write(self, temp_policy, tmp_path):
        """配 DENY 规则 → 文件**不得**被创建。"""
        target = tmp_path / "out" / "secret.txt"
        _deny_rule(temp_policy, FILE_WRITE, "*secret*")
        res = run(WriteFileTool().execute(path=str(target), content="x"))
        assert res.success is False
        assert not target.exists(), "DENY 时绝不能真写盘"
        assert "拒绝" in res.content or "策略" in res.content

    def test_ask_blocks_write(self, temp_policy, tmp_path):
        """把 file.write 改成 ask → 挂起，不写盘。"""
        temp_policy._config.setdefault("default_by_op", {})[FILE_WRITE] = ASK
        target = tmp_path / "pending.txt"
        res = run(WriteFileTool().execute(path=str(target), content="x"))
        assert res.success is False
        assert not target.exists(), "ASK 时绝不能真写盘"
        assert temp_policy.list_pending(only_pending=True)

    def test_write_decision_audited(self, temp_policy, tmp_path):
        from agent_community.platform.audit import audit_log
        run(WriteFileTool().execute(path=str(tmp_path / "b.txt"), content="y"))
        evs = audit_log.query(limit=30, event="policy.decision")
        assert any(f"op={FILE_WRITE}" in str(e.get("detail", "")) for e in evs)


# ── web_tool ─────────────────────────────────────────────────────
class TestWebFetchTool:
    def test_deny_blocks_before_network(self, temp_policy, monkeypatch):
        """DENY 命中时**在发起网络请求之前**就返回（不产生外呼）。"""
        _deny_rule(temp_policy, NETWORK_EGRESS, "*pastebin.com*")

        called = {"n": 0}

        class _Boom:
            def __init__(self, *a, **k):
                called["n"] += 1

        import httpx
        monkeypatch.setattr(httpx, "AsyncClient", _Boom)

        res = run(WebFetchTool().execute(url="http://pastebin.com/x"))
        assert res.success is False
        assert called["n"] == 0, "DENY 时绝不能发起网络请求"
        assert "拒绝" in res.content or "策略" in res.content

    def test_bad_scheme_rejected_before_policy(self, temp_policy):
        """非 http(s) URL 仍由原校验拦下（策略闸门之前的既有防线不动）。"""
        res = run(WebFetchTool().execute(url="file:///etc/passwd"))
        assert res.success is False
        assert "http://" in res.content


# ── actor 推断 ────────────────────────────────────────────────────
class TestActorFromRequest:
    def test_agent_token_header_means_harness(self):
        req = FakeRequest(headers={"X-Agent-Token": "abc"})
        assert actor_from_request(req) == ACTOR_HARNESS

    def test_no_header_means_operator(self):
        assert actor_from_request(FakeRequest()) == ACTOR_OPERATOR

    def test_empty_token_value_is_not_harness(self):
        """空值不算 harness（防止发空头冒充）。"""
        assert actor_from_request(FakeRequest(headers={"X-Agent-Token": ""})) == ACTOR_OPERATOR

    def test_broken_request_does_not_raise(self):
        class Weird:
            @property
            def headers(self):
                raise RuntimeError("boom")
        assert actor_from_request(Weird()) == ACTOR_OPERATOR


# ── plugins 路由 ──────────────────────────────────────────────────
@pytest.fixture()
def plugin_env(tmp_path, monkeypatch, temp_policy):
    """把 server 的 PLUGINS_FILE / ALLOWED_TOKENS 指向临时环境。"""
    from agent_community.platform import server as server_mod
    from agent_community.platform.routers import plugins as plugins_mod

    pf = tmp_path / "plugins.json"
    monkeypatch.setattr(server_mod, "PLUGINS_FILE", pf, raising=False)
    monkeypatch.setattr(server_mod, "ALLOWED_TOKENS", {"tok"}, raising=False)
    monkeypatch.setattr(plugins_mod, "_load_plugins",
                        lambda: json.loads(pf.read_text(encoding="utf-8")) if pf.exists() else {})
    monkeypatch.setattr(plugins_mod, "_save_plugins",
                        lambda d: pf.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8"))
    return plugins_mod, pf


class TestPluginsGating:
    def test_cmd_invoke_asks_and_does_not_spawn(self, plugin_env, temp_policy, monkeypatch):
        """cmd 插件 invoke 按执行类处理 → 默认 ASK，**Popen 不得被调用**。"""
        plugins_mod, pf = plugin_env
        pf.write_text(json.dumps({"p1": {"type": "cmd", "target": "echo hi"}}), encoding="utf-8")

        import subprocess
        spawned = {"n": 0}
        real_popen = subprocess.Popen

        def fake_popen(*a, **k):
            spawned["n"] += 1
            return real_popen(*a, **k)

        # 注意：plugins.py 在 cmd 分支内做的是**函数级** `import subprocess`，
        # 所以没有模块属性可打桩 —— 必须补全局 subprocess.Popen。
        monkeypatch.setattr(subprocess, "Popen", fake_popen)

        req = FakeRequest(body={"command": "echo hi"}, headers={"Authorization": "Bearer tok"})
        import asyncio as _a
        resp = _a.run(plugins_mod.api_plugins_invoke("p1", req))

        assert spawned["n"] == 0, "ASK 时绝不能真的起进程"
        assert getattr(resp, "status_code", None) == 202, \
            f"应返回 202 待审批，实得 {getattr(resp, 'status_code', resp)}"
        assert temp_policy.list_pending(only_pending=True)

    def test_cmd_invoke_denied_by_blacklist_rule(self, plugin_env, temp_policy):
        """cmd 插件跑危险命令 → 被 builtin.danger（op=shell.exec）直接 DENY。"""
        plugins_mod, pf = plugin_env
        pf.write_text(json.dumps({"p2": {"type": "cmd", "target": "rm -rf /"}}), encoding="utf-8")

        req = FakeRequest(body={"command": "rm -rf /"}, headers={"Authorization": "Bearer tok"})
        import asyncio as _a
        resp = _a.run(plugins_mod.api_plugins_invoke("p2", req))
        assert getattr(resp, "status_code", None) == 403, \
            f"危险命令应 403，实得 {getattr(resp, 'status_code', resp)}"

    def test_plugin_add_asks(self, plugin_env, temp_policy):
        """注册插件（埋可执行能力）→ 默认 ASK，返回 202 且**未落盘**。"""
        plugins_mod, pf = plugin_env
        req = FakeRequest(body={"name": "newp", "type": "http", "target": "http://x.example"})
        import asyncio as _a
        resp = _a.run(plugins_mod.api_plugins_add(req))
        assert getattr(resp, "status_code", None) == 202
        assert not pf.exists() or "newp" not in pf.read_text(encoding="utf-8"), \
            "ASK 时插件不得真的注册进去"

    def test_plugin_add_allowed_when_policy_allows(self, plugin_env, temp_policy):
        """把 plugin.install 改成 allow → 正常注册。"""
        plugins_mod, pf = plugin_env
        from agent_community.platform.policy import PLUGIN_INSTALL
        temp_policy._config.setdefault("default_by_op", {})[PLUGIN_INSTALL] = ALLOW

        req = FakeRequest(body={"name": "okp", "type": "http", "target": "http://x.example"})
        import asyncio as _a
        resp = _a.run(plugins_mod.api_plugins_add(req))
        assert not hasattr(resp, "status_code") or getattr(resp, "status_code", 200) == 200
        assert "okp" in pf.read_text(encoding="utf-8")
