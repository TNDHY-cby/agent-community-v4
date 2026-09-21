"""AIUsage 与 UsageProvider 单元测试（预算计数与熔断）。"""
from __future__ import annotations

import asyncio
import os
import tempfile

import pytest

from agent_community.platform.ai_provider import AIProvider, UsageProvider
from agent_community.platform.ai_usage import AIUsage, _estimate_tokens


class EchoProvider(AIProvider):
    def __init__(self):
        self.calls = 0

    @property
    def provider_type(self) -> str:
        return "echo"

    async def chat(self, system_prompt: str, user_message: str) -> str:
        self.calls += 1
        return f"echo:{user_message}"

    async def classify(self, query, candidates, context=""):
        return {"selected": ["a"], "reason": "echo"}

    async def chat_with_tools(self, messages, tools):
        return None

    async def close(self):
        pass


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


class TestAIUsage:
    def test_estimate_tokens(self):
        assert _estimate_tokens("") == 0
        assert _estimate_tokens("你好") == 2
        assert _estimate_tokens("abc") == 0  # 3 字符 < 4，不足 1 token
        assert _estimate_tokens("abcd") == 1

    def test_record_and_spent(self):
        with tempfile.TemporaryDirectory() as d:
            u = AIUsage(usage_dir=d, )
            u.price_per_1k = 1.0  # 1 元 / 1K tokens
            u.record("echo", "你好你好", "回复回复")
            assert u.spent_daily() > 0
            assert u.spent_monthly() > 0
            assert os.path.exists(os.path.join(d, "usage.jsonl"))

    def test_blocked_daily(self):
        with tempfile.TemporaryDirectory() as d:
            u = AIUsage(usage_dir=d)
            u.daily_budget = 0.001
            u.price_per_1k = 1.0
            # 写入足够大的成本记录
            u.record("echo", "测试文本" * 200, "回复内容" * 200)
            assert u.blocked() is True

    def test_not_blocked_when_budget_zero(self):
        u = AIUsage(usage_dir="")
        u.daily_budget = 0
        u.monthly_budget = 0
        assert u.blocked() is False


class TestUsageProvider:
    def test_records_call(self):
        with tempfile.TemporaryDirectory() as d:
            u = AIUsage(usage_dir=d)
            u.price_per_1k = 0.0
            inner = EchoProvider()
            p = UsageProvider(inner, usage=u)
            run(p.chat("sys", "hello"))
            assert inner.calls == 1
            assert u.spent_daily() >= 0

    def test_blocked_short_circuit(self):
        with tempfile.TemporaryDirectory() as d:
            u = AIUsage(usage_dir=d)
            u.daily_budget = 0.001
            u.price_per_1k = 1.0
            u.record("echo", "x" * 500, "y" * 500)  # 触发熔断
            assert u.blocked()
            inner = EchoProvider()
            p = UsageProvider(inner, usage=u)
            reply = run(p.chat("sys", "hello"))
            assert "预算熔断" in reply
            assert inner.calls == 0

    def test_classify_blocked(self):
        with tempfile.TemporaryDirectory() as d:
            u = AIUsage(usage_dir=d)
            u.daily_budget = 0.001
            u.price_per_1k = 1.0
            u.record("echo", "x" * 500, "y" * 500)
            inner = EchoProvider()
            p = UsageProvider(inner, usage=u)
            result = run(p.classify("q", [{"id": "a"}]))
            assert result["selected"] == []
            assert inner.calls == 0

    def test_chat_with_tools_blocked(self):
        with tempfile.TemporaryDirectory() as d:
            u = AIUsage(usage_dir=d)
            u.daily_budget = 0.001
            u.price_per_1k = 1.0
            u.record("echo", "x" * 500, "y" * 500)
            inner = EchoProvider()
            p = UsageProvider(inner, usage=u)
            resp = run(p.chat_with_tools([{"role": "user", "content": "hi"}], []))
            assert resp.content is not None and "预算熔断" in resp.content
            assert inner.calls == 0
