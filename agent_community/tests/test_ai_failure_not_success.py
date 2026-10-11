# -*- coding: utf-8 -*-
"""★"AI 调用失败被当成正常返回"这一类缺陷（2026-10-11 真机实测发现）。

**真机上发生的**（不是推演）：平台 AI 账户余额不足，provider 回
    `[Error: HTTP 402] {"error": {"message": "Insufficient account balance"}}`
而**这段文本切片之后是合法 JSON**（`{"error":{...}}`）⇒ 凡
`reply.find("{") … json.loads(...)` 的调用点都会把它当成一次**正常返回**：
    · 选人接口回 `success=True` + 空名单 + 文案"可能是 AI 返回空名单"
    · 编排器把"AI 没答"当"分析为空" ⇒ 静默 `return None`
**真正的原因（402 余额不足）一个字都没提** —— 典型的"看起来成功"死状态（准则⑤）。

本文件锁三件事：
1. `ai_error_text()` 的判据本身（认错误、不误伤正常回复）；
2. **选人接口**改判后必须回 `success=False` + `ai_unavailable` + 把 402 原文带出来；
3. **智能填充**同理（不许把"没答"当"提取到空字段"）。
"""
from __future__ import annotations

import asyncio

import pytest

from agent_community.platform.ai_provider import AI_ERROR_PREFIX, ai_error_text, ai_is_error

REAL_402 = ('[Error: HTTP 402] {"error": {"code": "402", '
            '"message": "Insufficient account balance", "type": "insufficient_balance"}}')


# ── 1) 判据本身 ────────────────────────────────────────────────────────────
def test_real_402_payload_is_recognized():
    assert ai_error_text(REAL_402) == REAL_402
    assert ai_is_error(REAL_402) is True


@pytest.mark.parametrize("normal", [
    '{"members": []}',
    '好的，我建议这样安排：{"members":[{"role":"码农"}]}',
    '前面有说明\n{"error": "业务字段名叫 error 不算调用失败"}',   # 正文里出现 "error" 键，不是调用失败
    '',
    None,
])
def test_normal_replies_are_not_flagged(normal):
    """反向断言：**不能**把正常回复（哪怕正文里有 error 字样）误判成调用失败。"""
    assert ai_is_error(normal) is False


def test_error_without_http_is_still_caught():
    assert ai_is_error("[Error: HTTPCallbackProvider 未配置 callback_url]") is True
    assert AI_ERROR_PREFIX == "[Error:"


# ── 2) 选人接口：失败必须如实说 ─────────────────────────────────────────────
class _Req:
    def __init__(self, body):
        self._body = body

    async def json(self):
        return self._body


def run(c):
    return asyncio.run(c)


@pytest.fixture()
def select_env(monkeypatch):
    """把选人接口的依赖最小化：1 个 harness + 一个"只会回错误文本"的 AI。"""
    from agent_community.platform import state as state_mod
    from agent_community.platform.routers import workshops_assign as wa
    from agent_community.platform.workshop import Workshop

    ws = Workshop(workshop_id="w-ai401", name="AI失败探针", workspace_dir="",
                  hall_content="做一个记账工具", members=[])
    ws.status = "selecting"
    state_mod.workshops[ws.workshop_id] = ws

    class _AI:
        async def chat(self, system, user):
            return REAL_402

    class _HM:
        @staticmethod
        def list_sessions():
            return [{"harness_id": "h1", "harness_name": "示例Harness-A",
                     "ai": {"model_name": "示例模型", "capabilities": ["coding"]}}]

    monkeypatch.setattr(wa, "harness_manager", _HM(), raising=False)
    monkeypatch.setattr(wa, "_sv", lambda: type("S", (), {"ai_provider": _AI()})())
    # 讨论上下文取空的，避免牵连其它模块
    import agent_community.platform.server as srv
    monkeypatch.setattr(srv, "_discussion_ctx", lambda *a, **k: "", raising=False)
    yield ws
    state_mod.workshops.pop(ws.workshop_id, None)


def test_select_members_reports_ai_failure_honestly(select_env):
    """★核心：AI 报 402 时，**不许**回 success=True + 空名单 + "可能是 AI 返回空名单"。"""
    from agent_community.platform.routers import workshops_assign as wa
    res = run(wa.workshop_select_members(select_env.workshop_id))
    assert res.get("success") is False, f"把 AI 失败当成正常返回了：{res}"
    assert res.get("error_code") == "ai_unavailable"
    assert "AI 调用失败" in (res.get("error") or "")
    assert "402" in (res.get("error") or ""), "要把真正的原因（402 余额不足）带出来"
    assert res.get("advice"), "要告诉用户怎么办（检查配置/额度，或手工指定名单）"


# ── 3) 智能填充：同一处判据 ────────────────────────────────────────────────
def test_prefill_reports_ai_failure(monkeypatch):
    from agent_community.platform.routers import harness_bridge as hb

    class _AI:
        async def chat(self, system, user):
            return REAL_402

    monkeypatch.setattr(hb, "_sv", lambda: type("S", (), {"ai_provider": _AI()})())
    res = run(hb.harness_prefill(_Req({"text": "我的记账小工具"})))
    assert res.get("success") is False, f"智能填充把 AI 失败当成功了：{res}"
    assert res.get("error_code") == "ai_unavailable"
    assert "402" in (res.get("error") or "")
    assert res.get("hint"), "要给人可行动的建议"
