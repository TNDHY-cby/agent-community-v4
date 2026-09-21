"""叠加链冒烟测试：Gate → Cache → Usage → 真实后端 全链路行为。"""
from __future__ import annotations

import asyncio
import tempfile

import pytest

from agent_community.platform.ai_cache import AICache
from agent_community.platform.ai_provider import (
    CacheProvider,
    FakeProvider,
    GateProvider,
    UsageProvider,
)
from agent_community.platform.ai_usage import AIUsage


class CountingFake(FakeProvider):
    def __init__(self):
        super().__init__(reply_template="【fake】{hash}")
        self.chat_calls = 0
        self.classify_calls = 0

    async def chat(self, system_prompt: str, user_message: str) -> str:
        self.chat_calls += 1
        return await super().chat(system_prompt, user_message)

    async def classify(self, query, candidates, context=""):
        self.classify_calls += 1
        return await super().classify(query, candidates, context)


def build_stack(tmpdir: str, inner: CountingFake | None = None):
    cache = AICache(ttl=3600, max_entries=64, cache_dir=tmpdir + "/cache")
    usage = AIUsage(usage_dir=tmpdir + "/usage")
    usage.price_per_1k = 0.0
    inner = inner or CountingFake()
    p: object = UsageProvider(inner, usage=usage)
    p = CacheProvider(p, cache=cache)
    return GateProvider(p), inner, usage


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


class TestProviderStack:
    def test_gate_short_circuit_zero_inner_call(self):
        with tempfile.TemporaryDirectory() as d:
            p, inner, _ = build_stack(d)
            reply = run(p.chat("sys", "你好"))
            assert reply == "你好，我在线"
            assert inner.chat_calls == 0

    def test_miss_goes_to_inner_and_caches(self):
        with tempfile.TemporaryDirectory() as d:
            p, inner, usage = build_stack(d)
            r1 = run(p.chat("sys", "帮我分析这段代码"))
            assert r1.startswith("【fake】")
            assert inner.chat_calls == 1
            # 二次相同输入：缓存命中，inner 不调用，usage 不新增记录
            r2 = run(p.chat("sys", "帮我分析这段代码"))
            assert r2 == r1
            assert inner.chat_calls == 1
            lines = open(usage._path, encoding="utf-8").read().strip().splitlines()
            assert len(lines) == 1

    def test_classify_short_circuit_single_candidate(self):
        with tempfile.TemporaryDirectory() as d:
            p, inner, _ = build_stack(d)
            result = run(p.classify("帮我分析代码", [{"id": "h1", "name": "Code", "capabilities": ["coding"]}]))
            assert result["selected"] == ["h1"]
            assert inner.classify_calls == 0

    def test_classify_ambiguous_goes_inner(self):
        with tempfile.TemporaryDirectory() as d:
            p, inner, _ = build_stack(d)
            result = run(p.classify(
                "帮我分析代码",
                [
                    {"id": "h1", "name": "A", "capabilities": ["coding"]},
                    {"id": "h2", "name": "B", "capabilities": ["coding"]},
                ],
            ))
            # 多候选命中同一能力 → 放行底层 fake（空选择）
            assert result["selected"] == []
            assert inner.classify_calls == 1

    def test_usage_blocked_short_circuits(self):
        with tempfile.TemporaryDirectory() as d:
            inner = CountingFake()
            p, inner, usage = build_stack(d, inner)
            # 先写入足够成本触发日熔断
            usage.daily_budget = 0.001
            usage.price_per_1k = 1.0
            usage.record("x", "测试文本" * 300, "回复内容" * 300)
            assert usage.blocked()
            reply = run(p.chat("sys", "随便聊聊长一点的复杂问题"))
            assert "预算熔断" in reply
            assert inner.chat_calls == 0
