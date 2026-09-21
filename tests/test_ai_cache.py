"""AICache 与 CacheProvider 单元测试。"""
from __future__ import annotations

import asyncio
import os
import tempfile

import pytest

from agent_community.platform.ai_cache import AICache
from agent_community.platform.ai_provider import AIProvider, CacheProvider


class EchoProvider(AIProvider):
    """记录调用次数并返回 echo 的假 Provider，用于验证缓存是否拦截。"""

    def __init__(self):
        self.calls = 0

    @property
    def provider_type(self) -> str:
        return "echo"

    async def chat(self, system_prompt: str, user_message: str) -> str:
        self.calls += 1
        return f"echo:{user_message}"

    async def classify(self, query, candidates, context=""):
        self.calls += 1
        return {"selected": [], "reason": "echo"}

    async def chat_with_tools(self, messages, tools):
        return None

    async def close(self):
        pass


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


class TestAICache:
    def test_set_get(self):
        c = AICache(ttl=3600, max_entries=10, cache_dir="")
        key = c.make_key("sys", "hello")
        assert c.get(key) is None
        c.set(key, "world")
        assert c.get(key) == "world"

    def test_ttl_expire(self):
        c = AICache(ttl=0, max_entries=10, cache_dir="")
        c.ttl = -1  # 立即过期
        key = c.make_key("s", "m")
        c.set(key, "v")
        assert c.get(key) is None

    def test_max_entries_lru(self):
        c = AICache(ttl=3600, max_entries=3, cache_dir="")
        for i in range(4):
            c.set(f"k{i}", f"v{i}")
        assert c.size <= 3

    def test_persist_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            c1 = AICache(ttl=3600, max_entries=10, cache_dir=d)
            key = c1.make_key("sys", "persist-me")
            c1.set(key, "stored")
            c2 = AICache(ttl=3600, max_entries=10, cache_dir=d)
            assert c2.get(key) == "stored"

    def test_key_differs_by_model(self):
        c = AICache()
        assert c.make_key("s", "m", "a") != c.make_key("s", "m", "b")


class TestCacheProvider:
    def test_hit_no_inner_call(self):
        c = AICache(ttl=3600, max_entries=10, cache_dir="")
        inner = EchoProvider()
        p = CacheProvider(inner, cache=c)
        key = c.make_key("sys", "q1", "echo")
        c.set(key, "cached-reply")
        reply = run(p.chat("sys", "q1"))
        assert reply == "cached-reply"
        assert inner.calls == 0

    def test_miss_calls_and_writes(self):
        c = AICache(ttl=3600, max_entries=10, cache_dir="")
        inner = EchoProvider()
        p = CacheProvider(inner, cache=c)
        reply = run(p.chat("sys", "q2"))
        assert reply == "echo:q2"
        assert inner.calls == 1
        # 第二次直接命中
        reply2 = run(p.chat("sys", "q2"))
        assert reply2 == "echo:q2"
        assert inner.calls == 1

    def test_error_not_cached(self):
        class ErrProvider(EchoProvider):
            async def chat(self, system_prompt, user_message):
                self.calls += 1
                return "[Error: boom]"

        c = AICache(ttl=3600, max_entries=10, cache_dir="")
        inner = ErrProvider()
        p = CacheProvider(inner, cache=c)
        r1 = run(p.chat("s", "e"))
        r2 = run(p.chat("s", "e"))
        assert r1 == r2 == "[Error: boom]"
        assert inner.calls == 2

    def test_classify_never_cached(self):
        c = AICache(ttl=3600, max_entries=10, cache_dir="")
        inner = EchoProvider()
        p = CacheProvider(inner, cache=c)
        for _ in range(2):
            run(p.classify("q", [{"id": "a"}]))
        assert inner.calls == 2
