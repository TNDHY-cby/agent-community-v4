# -*- coding: utf-8 -*-
"""V-14 步骤1 单测：策略引擎核心（纯单元，不接线）。

覆盖设计稿 §六 验证矩阵中可在单元层验证的条目：
  1  危险命令 → DENY
  2  普通 shell 命令零配置 → ASK
  5  plugin.install 零配置 → ASK
  6  file.write → ALLOW（不阻断开发）
  7  规则优先级：黑名单 DENY 覆盖分类默认
  8  未配置策略文件 → 用内置种子（不崩）
  11 auto-connect by operator → ALLOW
  12 auto-connect by external_ai → ASK
  13 bridge.write 落盘前被拦（AI 发起）→ ASK
  14 改 policy.json 后策略立即生效（证明不硬编码）
  15 AI 无决定权：resolve(by=ai) → forbidden

全部用临时 data_dir，不污染真实 data/。
"""
from __future__ import annotations

import json

import pytest

from agent_community.platform.policy import (
    ALLOW,
    ASK,
    BRIDGE_LAUNCH,
    BRIDGE_WRITE,
    CONFIG_MUTATE,
    DENY,
    FILE_DELETE,
    FILE_WRITE,
    NETWORK_EGRESS,
    PLUGIN_INSTALL,
    PLUGIN_INVOKE,
    SHELL_EXEC,
    ACTOR_EXTERNAL_AI,
    ACTOR_HARNESS,
    ACTOR_OPERATOR,
    ACTOR_UNKNOWN,
    PolicyEngine,
)


@pytest.fixture()
def engine(tmp_path):
    """零配置引擎：首次加载会生成 policy.json（出厂策略 C）。"""
    return PolicyEngine(data_dir=tmp_path)


# ── 1 / 2：shell.exec 的两条路 ────────────────────────────────────
class TestShellExec:
    def test_dangerous_command_denied(self, engine):
        """1. 危险命令 → DENY（内置黑名单规则排第一）。"""
        for cmd in ("rm -rf /", "format C:", "shutdown /s"):
            ev = engine.evaluate(SHELL_EXEC, actor=ACTOR_EXTERNAL_AI, target=cmd)
            assert ev.decision == DENY, f"{cmd} 应被拒，实得 {ev.decision}"
            assert ev.rule_id == "builtin.danger"
            assert ev.blocked

    def test_normal_command_defaults_to_ask(self, engine):
        """2. 普通命令零配置 → ASK（不静默执行，也不阻断）。"""
        ev = engine.evaluate(SHELL_EXEC, actor=ACTOR_EXTERNAL_AI, target="echo hello")
        assert ev.decision == ASK, f"普通命令应 ASK，实得 {ev.decision}"
        assert ev.rule_id == "default_by_op"
        assert ev.needs_approval

    def test_danger_deny_beats_default_ask(self, engine):
        """7. 规则优先级：DENY 覆盖分类默认。"""
        assert engine.evaluate(SHELL_EXEC, target="rm -rf /").decision == DENY
        assert engine.evaluate(SHELL_EXEC, target="echo hi").decision == ASK


# ── 3~6：其余操作类别的分类别默认 ─────────────────────────────────
class TestDefaultByOp:
    def test_plugin_install_asks(self, engine):
        """5. 插件安装零配置 → ASK。"""
        ev = engine.evaluate(PLUGIN_INSTALL, actor=ACTOR_EXTERNAL_AI, target="some-plugin")
        assert ev.decision == ASK

    def test_file_write_allows(self, engine):
        """6. 普通写文件 → ALLOW（不阻断开发）。"""
        ev = engine.evaluate(FILE_WRITE, actor=ACTOR_EXTERNAL_AI, target="notes.md")
        assert ev.decision == ALLOW

    def test_file_delete_asks(self, engine):
        """删除 → ASK（高危档）。"""
        assert engine.evaluate(FILE_DELETE, target="a.txt").decision == ASK

    def test_low_risk_allows(self, engine):
        """网络出网 / 插件调用 / 配置变更 → ALLOW。"""
        assert engine.evaluate(NETWORK_EGRESS, target="api.example.com").decision == ALLOW
        assert engine.evaluate(PLUGIN_INVOKE, target="p").decision == ALLOW
        assert engine.evaluate(CONFIG_MUTATE, target="cfg").decision == ALLOW

    def test_unknown_op_falls_back_to_allow(self, engine):
        """兜底：未配置的类别放行（不把开发堵死）。"""
        ev = engine.evaluate("some.unknown.op", target="x")
        assert ev.decision == ALLOW
        assert ev.rule_id == "fallback"


# ── 11~13：桥端点 + actor 分级（拍板项3）──────────────────────────
class TestBridgeActorGrading:
    def test_bridge_launch_by_operator_allowed(self, engine):
        """11. 人从本机 UI 一键注册 → 放行（产品主流程不被堵）。"""
        ev = engine.evaluate(BRIDGE_LAUNCH, actor=ACTOR_OPERATOR, target="h1")
        assert ev.decision == ALLOW, f"operator 应放行，实得 {ev.decision}"
        assert ev.rule_id == f"actor_override:{ACTOR_OPERATOR}"

    def test_bridge_launch_by_external_ai_asks(self, engine):
        """12. AI 自主启动桥进程 → ASK（实测 auto-connect 真 Popen）。"""
        ev = engine.evaluate(BRIDGE_LAUNCH, actor=ACTOR_EXTERNAL_AI, target="h1")
        assert ev.decision == ASK

    def test_bridge_launch_by_harness_asks(self, engine):
        """harness 本体回调同样不走 operator 放行。"""
        assert engine.evaluate(BRIDGE_LAUNCH, actor=ACTOR_HARNESS, target="h1").decision == ASK

    def test_bridge_write_by_ai_asks(self, engine):
        """13. 桥脚本落盘（实测会写可执行文件），AI 发起 → ASK。"""
        ev = engine.evaluate(BRIDGE_WRITE, actor=ACTOR_EXTERNAL_AI, target="h1:cli_acp")
        assert ev.decision == ASK

    def test_unknown_actor_does_not_get_operator_override(self, engine):
        """unknownow actor 不得沾 operator 的光。"""
        assert engine.evaluate(BRIDGE_LAUNCH, actor=ACTOR_UNKNOWN, target="h1").decision == ASK


# ── 8 / 14：配置驱动（代码不硬编码）────────────────────────────────
class TestConfigDriven:
    def test_seeds_config_file_on_first_load(self, tmp_path):
        """8. 首次加载生成 policy.json，用户看得见改得动。"""
        eng = PolicyEngine(data_dir=tmp_path)
        assert eng.config_path.exists(), "首次加载应生成 policy.json"
        data = json.loads(eng.config_path.read_text(encoding="utf-8"))
        assert data["default_by_op"][SHELL_EXEC] == ASK
        assert eng.config_source in ("seed", "file")

    def test_missing_config_does_not_crash(self, tmp_path):
        """8b. 配置损坏 → 回落种子，不崩。"""
        (tmp_path).mkdir(exist_ok=True)
        (tmp_path / "policy.json").write_text("{ this is not json", encoding="utf-8")
        eng = PolicyEngine(data_dir=tmp_path)
        assert eng.evaluate(SHELL_EXEC, target="echo hi").decision == ASK

    def test_editing_config_changes_policy(self, tmp_path):
        """14. 改 policy.json 即改策略 —— 证明策略不在代码里。"""
        eng = PolicyEngine(data_dir=tmp_path)
        # 出厂：写文件放行
        assert eng.evaluate(FILE_WRITE, target="x.md").decision == ALLOW

        # 用户把 file.write 改成 ask
        cfg = json.loads(eng.config_path.read_text(encoding="utf-8"))
        cfg["default_by_op"][FILE_WRITE] = ASK
        eng.config_path.write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")

        eng.reload()
        assert eng.evaluate(FILE_WRITE, target="x.md").decision == ASK, \
            "改配置文件后策略必须立即生效（不硬编码）"

    def test_custom_deny_rule_wins_over_default(self, tmp_path):
        """自定义 DENY 规则排前 → 覆盖分类默认。"""
        eng = PolicyEngine(data_dir=tmp_path)
        cfg = json.loads(eng.config_path.read_text(encoding="utf-8"))
        cfg["rules"].append({
            "id": "custom.no_secret_dir",
            "op": FILE_WRITE,
            "match": "*secret*",
            "decision": DENY,
            "note": "禁写含 secret 的路径",
        })
        eng.config_path.write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
        eng.reload()
        assert eng.evaluate(FILE_WRITE, target="data/secret.json").decision == DENY
        assert eng.evaluate(FILE_WRITE, target="data/notes.json").decision == ALLOW

    def test_instances_do_not_share_seed_config(self, tmp_path):
        """回归：实例改配置**不得污染模块级种子**。

        2026-10-03 由 test_policy_wiring 的两个失败用例暴露：原来用 `dict(_SEED_CONFIG)`
        浅拷贝，嵌套的 default_by_op 与种子共享对象 —— 一个实例改成 allow，
        后续新建的实例也变成 allow（生产上表现为「策略被暗中改掉」）。
        """
        from agent_community.platform.policy import _SEED_CONFIG

        seed_before = _SEED_CONFIG["default_by_op"][SHELL_EXEC]
        a = PolicyEngine(data_dir=tmp_path / "a")
        a._config.setdefault("default_by_op", {})[SHELL_EXEC] = ALLOW

        assert _SEED_CONFIG["default_by_op"][SHELL_EXEC] == seed_before, \
            "改实例配置污染了模块级种子"

        b = PolicyEngine(data_dir=tmp_path / "b")
        assert b.evaluate(SHELL_EXEC, target="echo hi").decision == seed_before, \
            "新实例应拿到未被污染的出厂默认"


# ── 15：AI 无决定权（拍板项2 固化在代码里）─────────────────────────
class TestNoAiDecisionPower:
    def test_request_then_human_approve(self, engine):
        """人工审批通过 → approved。"""
        rec = engine.request(SHELL_EXEC, actor=ACTOR_EXTERNAL_AI,
                             target="echo hi", reason="普通命令待批")
        assert rec["status"] == "pending"
        out = engine.resolve(rec["id"], True, by=ACTOR_OPERATOR, note="允许")
        assert out.get("ok") is True
        assert out["record"]["status"] == "approved"
        assert out["record"]["resolved_by"] == ACTOR_OPERATOR

    def test_human_reject(self, engine):
        rec = engine.request(SHELL_EXEC, actor=ACTOR_EXTERNAL_AI, target="echo hi")
        out = engine.resolve(rec["id"], False, by=ACTOR_OPERATOR, note="不许")
        assert out["record"]["status"] == "rejected"

    def test_ai_cannot_resolve(self, engine):
        """15. 关键：AI 不得拥有 ASK 决定权（信任根不可替代）。"""
        rec = engine.request(SHELL_EXEC, actor=ACTOR_EXTERNAL_AI, target="echo hi")
        for ai in (ACTOR_EXTERNAL_AI, ACTOR_HARNESS, "assistant_ai", ACTOR_UNKNOWN):
            out = engine.resolve(rec["id"], True, by=ai)
            assert out.get("error") == "forbidden", f"{ai} 不应有审批权，实得 {out}"
        # 且该待办仍是 pending，没被偷偷改成 approved
        assert engine.get_pending(rec["id"])["status"] == "pending"

    def test_double_resolve_rejected(self, engine):
        rec = engine.request(SHELL_EXEC, actor=ACTOR_EXTERNAL_AI, target="echo hi")
        engine.resolve(rec["id"], True, by=ACTOR_OPERATOR)
        again = engine.resolve(rec["id"], False, by=ACTOR_OPERATOR)
        assert again.get("error") == "already_resolved"

    def test_resolve_unknown_id(self, engine):
        assert engine.resolve("nope", True, by=ACTOR_OPERATOR).get("error") == "not_found"

    def test_list_pending_filters(self, engine):
        a = engine.request(SHELL_EXEC, actor=ACTOR_EXTERNAL_AI, target="a")
        b = engine.request(SHELL_EXEC, actor=ACTOR_EXTERNAL_AI, target="b")
        engine.resolve(a["id"], True, by=ACTOR_OPERATOR)
        pending = engine.list_pending(only_pending=True)
        assert [r["id"] for r in pending] == [b["id"]]
        assert len(engine.list_pending(only_pending=False)) == 2


# ── 队列独立落盘（不与配置混存）──────────────────────────────────
class TestPendingStorage:
    def test_pending_saved_to_separate_file(self, tmp_path):
        """队列落 policy_pending.json，**不写回 policy.json**（防覆盖用户编辑）。"""
        eng = PolicyEngine(data_dir=tmp_path)
        cfg_before = eng.config_path.read_text(encoding="utf-8")
        eng.request(SHELL_EXEC, actor=ACTOR_EXTERNAL_AI, target="echo hi")

        assert eng.pending_path.exists(), "队列应独立落盘"
        assert eng.config_path.read_text(encoding="utf-8") == cfg_before, \
            "入队不得改动 policy.json（否则会覆盖用户刚做的编辑）"

    def test_pending_survives_reload(self, tmp_path):
        eng = PolicyEngine(data_dir=tmp_path)
        rec = eng.request(SHELL_EXEC, actor=ACTOR_EXTERNAL_AI, target="echo hi")
        eng2 = PolicyEngine(data_dir=tmp_path)
        got = eng2.get_pending(rec["id"])
        assert got is not None and got["status"] == "pending"

    def test_config_pending_key_ignored(self, tmp_path):
        """老格式把 pending 写在 policy.json 里 → 被忽略，不参与运行时。"""
        payload = {
            "version": 1,
            "default_by_op": {SHELL_EXEC: DENY},
            "pending": [{"id": "legacy", "status": "pending"}],
        }
        (tmp_path / "policy.json").write_text(json.dumps(payload), encoding="utf-8")
        eng = PolicyEngine(data_dir=tmp_path)
        assert eng.evaluate(SHELL_EXEC, target="echo hi").decision == DENY
        assert eng.list_pending(only_pending=False) == []


# ── 自省 ─────────────────────────────────────────────────────────
class TestDescribe:
    def test_describe_shape(self, engine):
        d = engine.describe()
        assert d["config_source"] in ("seed", "file")
        assert SHELL_EXEC in d["default_by_op"]
        assert d["human_actors"] == [ACTOR_OPERATOR]
        assert isinstance(d["rules"], list) and d["rules"]

    def test_evaluation_to_dict_for_audit(self, engine):
        ev = engine.evaluate(SHELL_EXEC, actor=ACTOR_EXTERNAL_AI, target="rm -rf /")
        d = ev.to_dict()
        assert d["decision"] == DENY
        assert d["rule_id"] == "builtin.danger"
        assert d["actor"] == ACTOR_EXTERNAL_AI
        assert d["target"] == "rm -rf /"
