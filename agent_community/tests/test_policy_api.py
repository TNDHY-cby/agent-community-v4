# -*- coding: utf-8 -*-
"""V-14 步骤4 测试：策略自省 + 待审批端点。

重点断言设计稿 §六 验证矩阵：
  #15 **AI 无决定权** —— 带 X-Agent-Token 的审批请求必须 403
  #14 改 policy.json 后策略立即生效（reload 端点）
  #3/#4 人工批准/拒绝流转正确
"""
from __future__ import annotations

import asyncio
import json

import pytest

from agent_community.platform import policy as policy_mod
from agent_community.platform.policy import (
    ASK,
    SHELL_EXEC,
    ACTOR_OPERATOR,
    PolicyEngine,
)
from agent_community.platform.routers import policy as policy_api


class _Query:
    def __init__(self, d=None):
        self._d = d or {}

    def get(self, key, default=""):
        return self._d.get(key, default)


class FakeRequest:
    def __init__(self, body=None, headers=None):
        self.headers = headers or {}
        self.query_params = _Query()
        self._body = body

    async def json(self):
        return self._body if self._body is not None else {}


def run(coro):
    return asyncio.run(coro)


def unpack(resp):
    if hasattr(resp, "status_code") and hasattr(resp, "body"):
        return resp.status_code, json.loads(resp.body)
    return 200, resp


@pytest.fixture()
def eng(tmp_path, monkeypatch):
    e = PolicyEngine(data_dir=tmp_path)
    monkeypatch.setattr(policy_mod, "policy_engine", e)
    monkeypatch.setattr(policy_api, "policy_engine", e)
    return e


# ── 自省 ─────────────────────────────────────────────────────────
class TestDescribe:
    def test_describe_reports_config_and_counts(self, eng):
        status, payload = unpack(run(policy_api.api_policy_describe()))
        assert status == 200
        assert SHELL_EXEC in payload["default_by_op"]
        assert payload["human_actors"] == [ACTOR_OPERATOR]
        assert "pending_count" in payload
        assert payload["config_source"] in ("seed", "file")

    def test_pending_list_empty_then_filled(self, eng):
        _, p0 = unpack(run(policy_api.api_policy_pending()))
        assert p0["count"] == 0

        eng.request(SHELL_EXEC, actor="external_ai", target="echo hi")
        _, p1 = unpack(run(policy_api.api_policy_pending()))
        assert p1["count"] == 1
        assert p1["pending"][0]["op"] == SHELL_EXEC


# ── 人工审批 ─────────────────────────────────────────────────────
class TestHumanResolve:
    def test_operator_approves(self, eng):
        rec = eng.request(SHELL_EXEC, actor="external_ai", target="echo hi")
        req = FakeRequest(body={"approve": True, "note": "允许本次"})
        status, payload = unpack(run(policy_api.api_policy_resolve(rec["id"], req)))
        assert status == 200 and payload["ok"] is True
        assert payload["record"]["status"] == "approved"
        assert payload["record"]["resolved_by"] == ACTOR_OPERATOR

    def test_operator_rejects(self, eng):
        rec = eng.request(SHELL_EXEC, actor="external_ai", target="echo hi")
        req = FakeRequest(body={"approve": False, "note": "不许"})
        _, payload = unpack(run(policy_api.api_policy_resolve(rec["id"], req)))
        assert payload["record"]["status"] == "rejected"

    def test_resolve_unknown_404(self, eng):
        status, _ = unpack(run(policy_api.api_policy_resolve("nope", FakeRequest(body={}))))
        assert status == 404

    def test_double_resolve_409(self, eng):
        rec = eng.request(SHELL_EXEC, actor="external_ai", target="echo hi")
        run(policy_api.api_policy_resolve(rec["id"], FakeRequest(body={"approve": True})))
        status, _ = unpack(run(policy_api.api_policy_resolve(rec["id"], FakeRequest(body={"approve": False}))))
        assert status == 409

    def test_approval_is_audited(self, eng):
        rec = eng.request(SHELL_EXEC, actor="external_ai", target="echo hi")
        run(policy_api.api_policy_resolve(rec["id"], FakeRequest(body={"approve": True})))
        from agent_community.platform.audit import audit_log
        evs = audit_log.query(limit=20, event="policy.approval")
        assert evs, "审批必须留痕"
        assert rec["id"] in str(evs[0].get("detail", ""))


# ── 验证矩阵 #15：AI 无决定权 ─────────────────────────────────────
class TestAiHasNoDecisionPower:
    def test_ai_cannot_approve(self, eng):
        """带 X-Agent-Token 的调用方 → 403，且**待办保持 pending**。"""
        rec = eng.request(SHELL_EXEC, actor="external_ai", target="echo hi")
        req = FakeRequest(body={"approve": True}, headers={"X-Agent-Token": "whatever"})
        status, payload = unpack(run(policy_api.api_policy_resolve(rec["id"], req)))
        assert status == 403, f"AI 审批必须 403，实得 {status}"
        assert payload["error"] == "forbidden"
        assert eng.get_pending(rec["id"])["status"] == "pending", \
            "AI 被拒后待办不得被改动"

    def test_ai_cannot_reload_policy(self, eng):
        """AI 不得改策略（运维动作）。"""
        req = FakeRequest(headers={"X-Agent-Token": "whatever"})
        status, _ = unpack(run(policy_api.api_policy_reload(req)))
        assert status == 403

    def test_operator_can_reload(self, eng):
        status, payload = unpack(run(policy_api.api_policy_reload(FakeRequest())))
        assert status == 200
        assert "config_source" in payload


# ── 验证矩阵 #14：改配置即改策略 ──────────────────────────────────
class TestConfigDrivenViaApi:
    def test_reload_picks_up_edited_config(self, eng):
        """改 policy.json → reload → 新策略立即生效（不重启服务）。"""
        # 出厂：file.write = allow
        from agent_community.platform.policy import ALLOW, FILE_WRITE
        assert eng.evaluate(FILE_WRITE, target="a.md").decision == ALLOW

        cfg = json.loads(eng.config_path.read_text(encoding="utf-8"))
        cfg["default_by_op"][FILE_WRITE] = ASK
        eng.config_path.write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")

        run(policy_api.api_policy_reload(FakeRequest()))
        assert eng.evaluate(FILE_WRITE, target="a.md").decision == ASK

    def test_describe_reflects_edited_defaults(self, eng):
        from agent_community.platform.policy import DENY, PLUGIN_INSTALL
        cfg = json.loads(eng.config_path.read_text(encoding="utf-8"))
        cfg["default_by_op"][PLUGIN_INSTALL] = DENY
        eng.config_path.write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
        run(policy_api.api_policy_reload(FakeRequest()))

        _, payload = unpack(run(policy_api.api_policy_describe()))
        assert payload["default_by_op"][PLUGIN_INSTALL] == DENY
