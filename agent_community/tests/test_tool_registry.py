# -*- coding: utf-8 -*-
"""基座测试：`platform/tool_registry.py` —— Tool Calling 基础设施。

**为什么算"基层"**：平台助手（`assistant_tools.py` / `shell_tool.py` /
`file_tools.py` / `web_tool.py` / `bridge_tool.py`）全部实现 `BaseTool`
并注册进 `ToolRegistry`，LLM 的工具调用最终都经这里分发。此前零测试。

重点锁死：
- OpenAI function-calling 两种消息形态（tools 数组 / tool 结果消息）**格式必须对**，
  错一个字段 LLM 侧就是静默不调用；
- 分发**永不抛异常**：未知工具 / 参数错 / 工具内部炸，都要变成 `success=False` 的
  `ToolResult`。平台助手链路靠这个不中断。
"""
from __future__ import annotations

import asyncio
from abc import ABC

import pytest

from agent_community.platform.tool_registry import (
    BaseTool,
    ToolRegistry,
    ToolResult,
    ToolSchema,
)


def run(coro):
    return asyncio.run(coro)


class _EchoTool(BaseTool):
    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name="echo",
            description="回显输入",
            parameters={"type": "object",
                        "properties": {"text": {"type": "string"}},
                        "required": ["text"]},
        )

    async def execute(self, **params) -> ToolResult:
        return ToolResult(tool_name="echo", success=True,
                          content=f"echo:{params.get('text')}")


class _BoomTool(BaseTool):
    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(name="boom", description="一定炸", parameters={})

    async def execute(self, **params) -> ToolResult:
        raise ValueError("downstream exploded")


class _StrictTool(BaseTool):
    """显式形参（不收 **kwargs）—— LLM 传错参数名时会真抛 TypeError。"""

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(name="strict", description="严格签名", parameters={})

    async def execute(self, text: str) -> ToolResult:
        return ToolResult(tool_name="strict", success=True, content=text)


def _tool(name, *, exc=None):
    class _T(BaseTool):
        @property
        def schema(self):
            return ToolSchema(name=name, description=name, parameters={})

        async def execute(self, **params):
            if exc:
                raise exc
            return ToolResult(tool_name=name, success=True, content=name)

    return _T()


def test_base_tool_is_abstract():
    """BaseTool 不能直接被实例化（schema/execute 是抽象方法）。"""
    with pytest.raises(TypeError):
        BaseTool()  # type: ignore[abstract]


# ══════════════════════════════════════════════════════════════════
# A. 注册 / 查询
# ══════════════════════════════════════════════════════════════════
class TestRegistry:
    def test_register_and_get(self):
        reg = ToolRegistry()
        t = _EchoTool()
        reg.register(t)
        assert reg.get("echo") is t
        assert reg.count == 1
        assert reg.list_names() == ["echo"]

    def test_get_missing_returns_none(self):
        assert ToolRegistry().get("nope") is None

    def test_same_name_overwrites(self):
        """同名覆盖（设计承诺），且 count 不增长 —— 防重复注册把池子撑爆。"""
        reg = ToolRegistry()
        first, second = _tool("dup"), _tool("dup")
        reg.register(first)
        reg.register(second)
        assert reg.get("dup") is second
        assert reg.count == 1

    def test_register_many(self):
        reg = ToolRegistry()
        reg.register_many([_tool("a"), _tool("b"), _tool("c")])
        assert sorted(reg.list_names()) == ["a", "b", "c"]
        assert reg.count == 3

    def test_empty_registry(self):
        reg = ToolRegistry()
        assert reg.count == 0 and reg.list_names() == [] and reg.get_openai_schemas() == []


# ══════════════════════════════════════════════════════════════════
# B. OpenAI 格式（格式错 = LLM 静默不调用）
# ══════════════════════════════════════════════════════════════════
class TestOpenAIFormats:
    def test_tool_schema_shape(self):
        d = _EchoTool().schema.to_openai()
        assert d["type"] == "function"
        assert set(d["function"]) == {"name", "description", "parameters"}
        assert d["function"]["name"] == "echo"
        assert d["function"]["parameters"]["required"] == ["text"]

    def test_registry_generates_tools_array(self):
        reg = ToolRegistry()
        reg.register(_EchoTool())
        arr = reg.get_openai_schemas()
        assert isinstance(arr, list) and len(arr) == 1
        assert all(x["type"] == "function" for x in arr)

    def test_tool_result_message_shape(self):
        msg = ToolResult(tool_name="echo", success=True, content="hi").to_message("call_1")
        assert msg == {"role": "tool", "tool_call_id": "call_1", "content": "hi"}

    def test_failure_result_still_serializable(self):
        """失败结果也要能进 messages —— 否则工具一失败整轮对话就断。"""
        msg = ToolResult(tool_name="boom", success=False, content="炸了").to_message("c2")
        assert msg["role"] == "tool" and msg["content"] == "炸了"


# ══════════════════════════════════════════════════════════════════
# C. 分发：永不抛异常
# ══════════════════════════════════════════════════════════════════
class TestExecute:
    def test_success_path(self):
        reg = ToolRegistry()
        reg.register(_EchoTool())
        r = run(reg.execute("echo", "c1", {"text": "你好"}))
        assert r.success is True and r.content == "echo:你好"
        assert r.tool_name == "echo"

    def test_unknown_tool_returns_error_result(self):
        reg = ToolRegistry()
        reg.register(_EchoTool())
        r = run(reg.execute("ghost", "c1", {}))
        assert r.success is False
        assert "ghost" in r.content
        assert "echo" in r.content, "错误信息应列出可用工具，便于 LLM 自我纠正"

    def test_wrong_params_reported_as_parameter_error(self):
        """LLM 传错参数名 → TypeError → 归为「参数错误」，与内部异常区分开。

        ⚠️ 必须用**显式形参**的工具（`_StrictTool`）：收 `**params` 的工具会把
        意外关键字默默吞掉，根本不会抛 TypeError —— 用错夹具会得到一条假绿。
        """
        reg = ToolRegistry()
        reg.register(_StrictTool())
        r = run(reg.execute("strict", "c1", {"unexpected_kw": 1}))
        assert r.success is False
        assert "参数错误" in r.content

    def test_strict_tool_accepts_correct_params(self):
        """对照组：同一个严格工具传对参数必须成功（否则上一条断言可能是假的）。"""
        reg = ToolRegistry()
        reg.register(_StrictTool())
        r = run(reg.execute("strict", "c1", {"text": "hi"}))
        assert r.success is True and r.content == "hi"

    def test_internal_exception_does_not_propagate(self):
        """工具内部抛异常必须被吞成结果 —— 平台助手链路不能被打断。"""
        reg = ToolRegistry()
        reg.register(_BoomTool())
        r = run(reg.execute("boom", "c1", {}))
        assert r.success is False
        assert "ValueError" in r.content, "应带异常类型，便于排障"
        assert "downstream exploded" in r.content

    def test_execute_never_raises(self):
        """对一批恶意/异常工具统一断言：分发层绝不把异常漏出去。"""
        reg = ToolRegistry()
        reg.register(_BoomTool())
        reg.register(_tool("t-typeerr", exc=TypeError("bad")))
        reg.register(_tool("t-keyerr", exc=KeyError("k")))
        for name in ("boom", "t-typeerr", "t-keyerr", "never-registered"):
            r = run(reg.execute(name, "c", {}))
            assert isinstance(r, ToolResult)
            assert r.success is False
