# -*- coding: utf-8 -*-
"""V-14 步骤2 测试：策略闸门接入真实工具后的行为。

关键断言方式：**看副作用**，不看返回文案。
ASK/DENY 时命令绝不能真的跑起来 —— 用一个从不存在到存在的标记文件证明。
"""
from __future__ import annotations

import asyncio

import pytest

from agent_community.platform import policy as policy_mod
from agent_community.platform.policy import (
    ALLOW,
    ASK,
    HARNESS_LAUNCH,
    SHELL_EXEC,
    ACTOR_PLATFORM_AI,
    PolicyEngine,
)
from agent_community.platform.tools.shell_tool import ShellExecTool


@pytest.fixture()
def temp_policy(tmp_path, monkeypatch):
    """把模块单例换成临时目录的引擎，避免污染真实 data/。"""
    eng = PolicyEngine(data_dir=tmp_path)
    monkeypatch.setattr(policy_mod, "policy_engine", eng)
    return eng


def run(coro):
    return asyncio.run(coro)


class TestShellToolGating:
    def test_ask_does_not_execute(self, temp_policy, tmp_path):
        """默认策略 shell.exec=ASK → 命令**不得执行**，返回待办 id。"""
        marker = tmp_path / "should_not_exist.txt"
        cmd = f"New-Item -Path '{marker}' -ItemType File -Force | Out-Null"

        res = run(ShellExecTool().execute(command=cmd))

        assert res.success is False, "ASK 时不应报告成功"
        assert not marker.exists(), "ASK 时命令绝不能真的执行（副作用文件不该出现）"
        assert temp_policy.list_pending(only_pending=True), "应已入待审批队列"
        assert "待办" in res.content or "审批" in res.content

    def test_dangerous_command_blocked_by_floor(self, temp_policy):
        """黑名单底线仍在：危险命令被拦，且不进 ASK 队列（直接拒）。"""
        before = len(temp_policy.list_pending(only_pending=False))
        res = run(ShellExecTool().execute(command="rm -rf /"))
        assert res.success is False
        assert "安全拦截" in res.content, f"应是黑名单拦截，实得 {res.content[:80]}"
        assert len(temp_policy.list_pending(only_pending=False)) == before, \
            "黑名单命中应直接拒，不入待审批队列"

    def test_allow_executes(self, temp_policy, tmp_path):
        """把 shell.exec 改成 allow（模拟用户配置）→ 命令真的执行。"""
        cfg = temp_policy._config
        cfg.setdefault("default_by_op", {})[SHELL_EXEC] = ALLOW

        marker = tmp_path / "allowed_marker.txt"
        cmd = f"New-Item -Path '{marker}' -ItemType File -Force | Out-Null"

        res = run(ShellExecTool().execute(command=cmd))

        assert res.success is True, f"ALLOW 时应执行成功，实得 {res.content[:120]}"
        assert marker.exists(), "ALLOW 时命令应真的执行（副作用文件应出现）"

    def test_policy_decision_is_audited(self, temp_policy):
        """每次判定都进审计（决策可追溯）。"""
        run(ShellExecTool().execute(command="echo hi"))
        from agent_community.platform.audit import audit_log
        hits = [e for e in audit_log.query(limit=50, event="policy.decision")]
        assert hits, "应记录 policy.decision 审计事件"
        assert any(e.get("actor") == ACTOR_PLATFORM_AI for e in hits), \
            "工具层的 actor 应为 platform_ai（非 operator，故不吃 override）"

    def test_ask_pending_carries_op_and_target(self, temp_policy):
        """待办记录带 op/target/actor，便于人工判断。"""
        run(ShellExecTool().execute(command="echo hello world"))
        recs = temp_policy.list_pending(only_pending=True)
        assert recs, "应有一条待办"
        r = recs[-1]
        assert r["op"] == SHELL_EXEC
        assert "echo hello world" in r["target"]
        assert r["actor"] == ACTOR_PLATFORM_AI

    def test_approved_pending_does_not_auto_execute(self, temp_policy, tmp_path):
        """批准只是**解除闸门**，不代执行 —— 下次调用才跑（避免审批即执行的反直觉语义）。"""
        marker = tmp_path / "after_approve.txt"
        cmd = f"New-Item -Path '{marker}' -ItemType File -Force | Out-Null"

        run(ShellExecTool().execute(command=cmd))
        rec = temp_policy.list_pending(only_pending=True)[-1]
        out = temp_policy.resolve(rec["id"], True, by="operator", note="允许")
        assert out.get("ok") is True
        assert not marker.exists(), "批准动作本身不应触发执行"


class TestLaunchHarnessGating:
    def test_launch_asks_by_default(self, temp_policy):
        """拉起 harness 进程 = 最严档，默认 ASK。"""
        ev = temp_policy.evaluate(HARNESS_LAUNCH, actor=ACTOR_PLATFORM_AI, target="h1")
        assert ev.decision == ASK
        assert ev.rule_id == "default_by_op"

    def test_launch_tool_returns_pending_without_starting(self, temp_policy, monkeypatch):
        """工具层：ASK 时**不得调用** harness_launcher（真启动点）。"""
        from agent_community.platform.tools import assistant_tools

        called = {"n": 0}

        async def fake_ensure(harness_id, timeout=60):
            called["n"] += 1
            return True, "started"

        monkeypatch.setattr(assistant_tools.harness_launcher,
                            "ensure_harness_online", fake_ensure)

        # 伪造一个已注册且离线的 session
        class _Sess:
            online = False
            info = None

        monkeypatch.setattr(assistant_tools.harness_manager, "sessions",
                            {"h1": _Sess()}, raising=False)

        res = run(assistant_tools.LaunchHarnessTool().execute(harness_id="h1"))

        assert res.success is False
        assert called["n"] == 0, "ASK 时绝不能真的去启动进程"
        assert "待办" in res.content or "审批" in res.content

    def test_launch_tool_allows_when_policy_allows(self, temp_policy, monkeypatch):
        """策略改成 allow → 工具才真的启动。"""
        from agent_community.platform.tools import assistant_tools

        temp_policy._config.setdefault("default_by_op", {})[HARNESS_LAUNCH] = ALLOW
        called = {"n": 0}

        async def fake_ensure(harness_id, timeout=60):
            called["n"] += 1
            return True, "started"

        monkeypatch.setattr(assistant_tools.harness_launcher,
                            "ensure_harness_online", fake_ensure)

        class _Sess:
            online = False
            info = None

        monkeypatch.setattr(assistant_tools.harness_manager, "sessions",
                            {"h1": _Sess()}, raising=False)

        res = run(assistant_tools.LaunchHarnessTool().execute(harness_id="h1"))
        assert called["n"] == 1, "ALLOW 时应调用启动函数"
        assert res.success is True

    def test_launch_tool_online_shortcircuits_before_gate(self, temp_policy, monkeypatch):
        """已在线 → 直接报告，不必打扰人审批（避免无意义待办）。"""
        from agent_community.platform.tools import assistant_tools

        class _Sess:
            online = True
            info = None

        monkeypatch.setattr(assistant_tools.harness_manager, "sessions",
                            {"h1": _Sess()}, raising=False)

        res = run(assistant_tools.LaunchHarnessTool().execute(harness_id="h1"))
        assert res.success is True
        assert "已在线" in res.content
        assert not temp_policy.list_pending(only_pending=True), "在线短路不应产生待办"
