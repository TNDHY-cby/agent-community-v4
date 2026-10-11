# -*- coding: utf-8 -*-
"""第二批"AI 错误文本被当成正常返回"的守护（2026-10-12）。

第一批（2026-10-11）修了 3 处高风险：选人接口 / 智能填充 / 编排器。
本文件锁**第二批**：三个 provider 的 `pick_agents`、`ai_external` 的兜底分类、
以及 `server` 的"任务分工预填"。

**为什么第二批也要做**：`ai_provider` 把失败**以文本返回**（`[Error: HTTP 402] {...}`），
而这段文本**切片后是合法 JSON** ⇒ 凡是 `reply.find("{") … json.loads(...)` 的地方都会
把它当成一次正常返回。后果分两种，都很坏：
  · `pick_agents` → 返回 `{"selected": [], "reason": "无法解析 AI 回复"}`
    ⇒ 看上去像"AI 判断没有合适的 agent"，实际是"AI 根本没答"；
  · `server` 的预填 → 走 else 分支 `return {"success": True, ...}` ⇒ **假成功**。

判据统一取自 `ai_provider.ai_error_text`（单一真相，不在这里另写一份）。
"""
from __future__ import annotations

import asyncio

import pytest

from agent_community.platform.ai_provider import ai_error_text

REAL_402 = ('[Error: HTTP 402] {"error": {"code": "402", '
            '"message": "Insufficient account balance"}}')


def run(c):
    return asyncio.run(c)


# ── 1) 判据本身（复用，不重写）────────────────────────────────────────────
def test_shared_judge_still_recognises_the_error_text():
    assert ai_error_text(REAL_402) == REAL_402
    assert ai_error_text('{"selected": []}') == ""


# ── 2) 三个 provider 的 classify（方法名是 classify，不是 pick_agents） ────────────────────────────────────────
def _provider_with_reply(reply: str):
    """取一个 provider 实例，并把它自己的 chat 换成回固定文本。"""
    from agent_community.platform import ai_provider as ap

    cls = getattr(ap, "OpenAICompatibleProvider", None) or getattr(ap, "HTTPCallbackProvider")
    prov = cls(api_key="k", base_url="http://127.0.0.1:1", model="m")

    async def _chat(system, user):
        return reply

    prov.chat = _chat                      # type: ignore[method-assign]
    return prov


def test_classify_reports_ai_failure_not_empty_selection(monkeypatch):
    """★AI 报错时不许回"选了空名单"——要说出是**调用失败**。"""
    prov = _provider_with_reply(REAL_402)
    out = run(prov.classify("任务", [], ""))
    assert out.get("selected") == []
    assert out.get("error_code") == "ai_unavailable", f"把 AI 失败当成正常返回了：{out}"
    assert "402" in str(out.get("reason")), out


def test_classify_still_parses_normal_reply():
    """反向：正常回复必须照旧能解析（别把好路径改坏）。"""
    prov = _provider_with_reply('好的：{"selected": ["a1"], "reason": "它合适"}')
    out = run(prov.classify("任务", [], ""))
    assert out.get("selected") == ["a1"], out
    assert out.get("error_code") is None


# ── 3) ai_external 的兜底分类：走规则降级而不是"空选择" ────────────────────
def test_ai_external_classify_falls_back_to_rules_on_ai_error():
    """该路径原本只能"解析不到就 pass"，现在应**明确走规则降级**并带上原因。"""
    import inspect

    from agent_community.platform import ai_external as ax
    src = inspect.getsource(ax)
    assert "ai_error_text" in src, "少了判据：AI 错误文本又会被当成正常回复"
    assert "rule_classify_result" in src, "应降级到规则分类，而不是留一个空的成功结果"


# ── 4) server 的任务分工预填：不许假成功 ──────────────────────────────────
def test_prefill_proposal_returns_failure_when_ai_errors(monkeypatch):
    """★**行为**级证明（不只源码级）：AI 报错时 `prefill_proposal` 必须
    `success=False` + `error_code=ai_unavailable`，**不许**再回 `success=True`。

    修复前它走 else 分支 `return {"success": True, ...}` ⇒ 界面显示"预填成功但内容为空"
    （假成功）。这里把 provider 的 chat 换成"真实的失败形态"（错误文本，不抛异常
    —— 那是 provider 的既有契约），然后直接调那个函数。
    """
    import asyncio

    from agent_community.platform import server as srv

    class _DeadAI:
        """最小 provider：按**既有契约**返回错误文本（失败=文本，不抛异常）。"""

        async def chat(self, system_prompt, user_message):
            return REAL_402

    # ⚠️ 这里必须补 **server 自己的全局** `ai_provider`：
    #   `prefill_proposal` 开头就是 `if not ai_provider: return {...'AI 后端未配置'}`，
    #   读的是 server 模块的全局名。补错地方（比如补到 __main__）会走到那句提前返回，
    #   测出来的就不是"AI 报错"这条路径了 —— 我第一版就补错了，报的错文对不上。
    monkeypatch.setattr(srv, "ai_provider", _DeadAI(), raising=False)

    RoomCls = getattr(srv, "DiscussionRoom", None)
    assert RoomCls is not None, "找不到 DiscussionRoom（改名了？请同步本测试）"
    room = RoomCls(room_id="prove-room", task_id="t-prove",
                   task_title="预填验证", task_description="随便写")
    srv.discussion_rooms["prove-room"] = room

    try:
        # 注意签名：`prefill_proposal(room_id)` —— 只接 room_id（不接 request）
        out = asyncio.run(srv.prefill_proposal("prove-room"))
    finally:
        srv.discussion_rooms.pop("prove-room", None)

    # 有的实现返回响应对象，统一取 body
    if hasattr(out, "body"):
        import json as _json
        try:
            out = _json.loads(out.body.decode("utf-8"))
        except Exception:  # noqa: BLE001
            pass
    assert isinstance(out, dict), f"返回了意外类型：{out!r}"
    assert out.get("success") is not True, \
        f"AI 报错却回了成功 ⇒ 假成功复发：{out}"
    assert out.get("error_code") == "ai_unavailable", out
    assert "402" in str(out.get("error") or ""), "要把真正原因（402 余额不足）带出来"


def test_prefill_proposal_has_error_guard():
    """源码级：预填函数（`prefill_proposal`）必须在解析前先判错误文本。

    否则 AI 失败会走 else 分支 `return {"success": True, ...}` ⇒ **假成功**
    （界面显示"预填成功"但内容全空 —— 准则⑤最反对的那种状态）。
    ⚠️ 取源码要**按函数定义**取，不要"从某个标签往前找 N 个字符"：
      我第一版就是那么写的，窗口没覆盖到函数体 ⇒ **误报"缺少守护"**。
    """
    import inspect

    from agent_community.platform import server as srv
    fn = getattr(srv, "prefill_proposal", None)
    assert fn is not None, "找不到 prefill_proposal（改名了？请同步本测试）"
    body = inspect.getsource(fn)
    assert "ai_error_text" in body, "预填路径缺少'先认错误文本'的守护 ⇒ 假成功会复发"
    assert "ai_unavailable" in body, \
        "失败要带上 error_code，界面才能区分'AI 不可用'与'预填内容为空'"
    # 判据必须在**解析之前**（放到后面等于没判）
    i_guard = body.find("ai_error_text")
    i_parse = body.find("json.loads(")
    assert 0 < i_guard < i_parse, "判据应放在解析之前"
