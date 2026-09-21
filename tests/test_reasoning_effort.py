"""思考强度调节测试：OpenAICompatibleProvider._apply_thinking 与请求体注入。

对齐 DeepSeek 官方思考模式文档：
- 开关: {"thinking": {"type": "enabled/disabled"}}
- 强度: "reasoning_effort": "low/high/max"
- 官方映射: low→low, medium→high, high→high, xhigh→high, max→max
- off = 不注入（不思考，省钱优先，且对其它 OpenAI 兼容后端无感知）
"""
from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

from agent_community.platform.ai_provider import OpenAICompatibleProvider


class CaptureClient:
    """捕获最后一次 post 的 json body，返回标准 OpenAI 响应。"""

    def __init__(self):
        self.last_body = None

    async def post(self, url, json=None):
        self.last_body = json
        return SimpleNamespace(
            status_code=200,
            json=lambda: {
                "choices": [{"message": {"content": "ok", "tool_calls": None}}],
            },
        )

    async def aclose(self):
        pass


@pytest.fixture
def capture(monkeypatch):
    client = CaptureClient()
    monkeypatch.setattr(OpenAICompatibleProvider, "_get_client", lambda self: client)
    return client


@pytest.mark.parametrize(
    "effort,expect_thinking,expect_effort",
    [
        ("off", None, None),
        ("", None, None),
        ("low", {"type": "enabled"}, "low"),
        ("medium", {"type": "enabled"}, "high"),
        ("high", {"type": "enabled"}, "high"),
        ("max", {"type": "enabled"}, "max"),
    ],
)
def test_chat_thinking_injection(capture, effort, expect_thinking, expect_effort):
    p = OpenAICompatibleProvider(reasoning_effort=effort)
    import asyncio

    asyncio.run(p.chat("sys", "hi"))
    body = capture.last_body
    if expect_thinking is None:
        assert "thinking" not in body
        assert "reasoning_effort" not in body
    else:
        assert body["thinking"] == expect_thinking
        assert body["reasoning_effort"] == expect_effort


def test_chat_with_tools_injection(capture):
    p = OpenAICompatibleProvider(reasoning_effort="high")
    import asyncio

    asyncio.run(
        p.chat_with_tools(
            [{"role": "user", "content": "hi"}],
            [{"type": "function", "function": {"name": "f", "parameters": {}}}],
        )
    )
    assert capture.last_body["thinking"] == {"type": "enabled"}
    assert capture.last_body["reasoning_effort"] == "high"


def test_env_override_default(monkeypatch, capture):
    monkeypatch.setenv("AC_AI_REASONING_EFFORT", "high")
    p = OpenAICompatibleProvider()
    assert p.reasoning_effort == "high"
    import asyncio

    asyncio.run(p.chat("sys", "hi"))
    assert capture.last_body["thinking"] == {"type": "enabled"}
    assert capture.last_body["reasoning_effort"] == "high"


def test_constructor_beats_env(monkeypatch, capture):
    monkeypatch.setenv("AC_AI_REASONING_EFFORT", "max")
    p = OpenAICompatibleProvider(reasoning_effort="low")
    assert p.reasoning_effort == "low"


def test_invalid_effort_no_injection(capture):
    p = OpenAICompatibleProvider(reasoning_effort="crazy")
    import asyncio

    asyncio.run(p.chat("sys", "hi"))
    assert "thinking" not in capture.last_body
    assert "reasoning_effort" not in capture.last_body


def test_default_model_is_flash():
    p = OpenAICompatibleProvider()
    assert p.model == "deepseek-v4-flash"
