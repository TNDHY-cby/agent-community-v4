# -*- coding: utf-8 -*-
"""V-14 拍板 #1（选 B「底线钳制」）的单测：黑名单不可用时的安全底线。

背景：`_dangerous()` 原实现在黑名单不可用时返回 `None` —— 与「查了，不危险」
**无法区分**，于是 builtin.danger 不命中、决策继续往下。
出厂 `default_by_op["shell.exec"]="ask"` 所以不漏，但**只要用户把它改成 allow，
安全底线就没了**。安全底线不该依赖用户配置。

拍板 #1 选 B 的三条理由（对应本文件的三组用例）：
  1. **审计可回溯**：`rule_id` 必须区分「命令危险」与「检查器坏了」   -> TestAuditDistinguishes
  2. **DENY 过度 = fail-dead**：只钳 ALLOW，DENY 保持、ASK 本就是底线 -> TestClampOnlyAllow
  3. **不能无条件前置**：那会盖掉用户自己配的 DENY                   -> TestCustomDenyWins
"""
from __future__ import annotations

import pytest

from pathlib import Path

from agent_community.platform import policy as pm
from agent_community.platform.policy import (
    ALLOW,
    ASK,
    DENY,
    SHELL_EXEC,
    FILE_WRITE,
    ACTOR_OPERATOR,
    ACTOR_PLATFORM_AI,
    PolicyEngine,
    _UNAVAILABLE,
    _dangerous,
)


def _break_blacklist(monkeypatch):
    """让 core.security._is_dangerous 抛异常 —— 模拟黑名单不可用。"""
    def boom(_cmd=""):
        raise ImportError("core/security 模块缺失")

    monkeypatch.setattr("agent_community.platform.core.security._is_dangerous",
                        boom, raising=False)


@pytest.fixture()
def cfg_path(tmp_path):
    """出厂默认 + 把 shell.exec 设成 allow（DS 指出缺口出现的唯一前提）。"""
    import json
    import copy
    d = tmp_path / "_pf"
    d.mkdir(parents=True, exist_ok=True)
    cfg = copy.deepcopy(pm._SEED_CONFIG)
    cfg["default_by_op"][SHELL_EXEC] = ALLOW          # ← 关键前提
    (d / "policy.json").write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
    return d


# ── 1. 底线钳制 ──────────────────────────────────────────────────
class TestFloorClamp:
    def test_checker_broken_clamps_allow_to_ask(self, cfg_path, monkeypatch):
        """黑名单不可用 + shell.exec 明确 allow -> **钳到 ASK**，不静默放行。"""
        _break_blacklist(monkeypatch)
        eng = PolicyEngine(data_dir=cfg_path)
        ev = eng.evaluate(SHELL_EXEC, actor=ACTOR_PLATFORM_AI, target="echo hi")
        assert ev.decision == ASK, f"应钳到 ASK，实得 {ev.decision}"
        assert ev.rule_id == "builtin.danger_unavailable", \
            f"rule_id 必须标明检查器坏了，实得 {ev.rule_id!r}"

    def test_clamp_only_affects_shell(self, cfg_path, monkeypatch):
        """钳制只针对 shell.exec —— 其它类别的 allow 不受牵连。"""
        _break_blacklist(monkeypatch)
        eng = PolicyEngine(data_dir=cfg_path)
        ev = eng.evaluate(FILE_WRITE, actor=ACTOR_PLATFORM_AI, target="a.md")
        assert ev.decision == ALLOW, f"file.write 不该被牵连，实得 {ev.decision}"

    def test_default_ask_unchanged_when_broken(self, cfg_path, monkeypatch):
        """出厂默认本就是 ASK 的类别，钳制后仍是 ASK（无变化）。"""
        _break_blacklist(monkeypatch)
        eng = PolicyEngine(data_dir=cfg_path)
        ev = eng.evaluate(SHELL_EXEC, actor=ACTOR_PLATFORM_AI, target="ls")
        assert ev.decision == ASK


# ── 2. 不得盖掉用户配的 DENY ──────────────────────────────────────
class TestCustomDenyWins:
    def test_custom_deny_survives_checker_break(self, cfg_path, monkeypatch):
        """用户配的 DENY 在黑名单坏掉时**仍生效** —— 无条件前置会盖掉它，故不前置。"""
        import json
        cfg = json.loads((cfg_path / "policy.json").read_text(encoding="utf-8"))
        cfg["rules"].append({"id": "user.deny_rf", "op": SHELL_EXEC,
                             "match": "rm -rf *", "decision": DENY,
                             "note": "用户自定义"})
        (cfg_path / "policy.json").write_text(json.dumps(cfg, ensure_ascii=False),
                                              encoding="utf-8")
        _break_blacklist(monkeypatch)
        eng = PolicyEngine(data_dir=cfg_path)

        # 黑名单坏了，但用户 DENY 规则优先
        ev = eng.evaluate(SHELL_EXEC, target="rm -rf /tmp/x")
        assert ev.decision == DENY, f"用户 DENY 不得被钳制盖掉，实得 {ev.decision}"
        assert ev.rule_id == "user.deny_rf"

    def test_floor_still_applies_after_deny_rule_miss(self, cfg_path, monkeypatch):
        """没命中用户规则的命令，仍然吃到底线钳制。"""
        import json
        cfg = json.loads((cfg_path / "policy.json").read_text(encoding="utf-8"))
        cfg["rules"].append({"id": "user.deny_rf", "op": SHELL_EXEC,
                             "match": "rm -rf *", "decision": DENY, "note": ""})
        (cfg_path / "policy.json").write_text(json.dumps(cfg, ensure_ascii=False),
                                              encoding="utf-8")
        _break_blacklist(monkeypatch)
        eng = PolicyEngine(data_dir=cfg_path)

        ev = eng.evaluate(SHELL_EXEC, target="dir b")   # 不匹配 rm -rf *
        assert ev.decision == ASK
        assert ev.rule_id == "builtin.danger_unavailable"


# ── 3. 审计可回溯：两种原因必须是不同 rule_id ─────────────────────
class TestAuditDistinguishes:
    def test_checker_broken_vs_dangerous_have_distinct_ids(self, cfg_path, monkeypatch):
        """原因 A「检查器坏了」与原因 B「命令确实危险」rule_id 必须不同。"""
        # 检查器坏了
        _break_blacklist(monkeypatch)
        eng = PolicyEngine(data_dir=cfg_path)
        broken = eng.evaluate(SHELL_EXEC, target="echo hi")
        monkeypatch.undo()

        # 检查器正常，命令确实危险
        eng2 = PolicyEngine(data_dir=cfg_path)
        danger = eng2.evaluate(SHELL_EXEC, target="rm -rf /")

        assert broken.rule_id == "builtin.danger_unavailable"
        assert danger.rule_id == "builtin.danger"
        assert broken.rule_id != danger.rule_id, \
            "两种原因混用同一 rule_id -> 审计无法回溯"
        assert danger.decision == DENY, "危险命令仍应 DENY（正常路径不受影响）"

    def test_broken_checker_leaves_audit_trail_in_note(self, cfg_path, monkeypatch):
        """note 要能说清「为什么被拦」，供审计回溯。"""
        _break_blacklist(monkeypatch)
        eng = PolicyEngine(data_dir=cfg_path)
        ev = eng.evaluate(SHELL_EXEC, target="whoami")
        assert "黑名单检查器不可用" in ev.note, f"note 应说明原因，实得 {ev.note!r}"
        assert "安全底线" in ev.note


# ── 4. 三态区分（_dangerous 的基础语义）──────────────────────────
class TestThreeStates:
    def test_normal_states_unaffected(self, monkeypatch):
        """检查器正常时：命中 -> 字符串；不危险 -> None；都不是哨兵。"""
        r_danger = _dangerous("rm -rf /")
        r_safe = _dangerous("echo hi")
        assert r_danger is not None and r_danger is not _UNAVAILABLE
        assert r_safe is None
        assert r_danger != _UNAVAILABLE

    def test_broken_returns_sentinel(self, monkeypatch):
        _break_blacklist(monkeypatch)
        assert _dangerous("echo hi") is _UNAVAILABLE
        assert _dangerous("rm -rf /") is _UNAVAILABLE

    def test_broken_sentinel_is_not_truthy_hit(self, monkeypatch):
        """哨兵不能被 builtin.danger 当成「命中危险」（否则 fail-dead）。"""
        _break_blacklist(monkeypatch)
        eng = PolicyEngine(data_dir=Path("/nonexistent"))
        # 规则层不命中
        rule = {"id": "builtin.danger", "op": SHELL_EXEC,
                "match": "__builtin_dangerous__", "decision": DENY, "note": ""}
        assert eng._rule_hit(rule, SHELL_EXEC, "echo hi") is False, \
            "哨兵不得让 builtin.danger 误命中"
        # 判定落到 ASK（底线），不是 DENY
        assert eng.evaluate(SHELL_EXEC, target="echo hi").decision == ASK
