"""Tool Calling 基础设施 — 工具注册表与抽象基类。

定义：
  ToolSchema   — 工具的 JSON Schema 定义（name + description + parameters）
  ToolResult   — 工具执行结果
  BaseTool     — 工具抽象基类
  ToolRegistry — 工具注册中心（注册 / 查询 / 批量生成 OpenAI tools 数组 / 执行）
"""

from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ToolSchema:
    """工具的元数据定义，对应 OpenAI function calling 的 function 字段。"""
    name: str
    description: str
    parameters: dict   # JSON Schema 对象

    def to_openai(self) -> dict:
        """转为 OpenAI tools 数组中的单个元素。"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


@dataclass
class ToolResult:
    """工具执行结果。"""
    tool_name: str
    success: bool
    content: str

    def to_message(self, tool_call_id: str) -> dict:
        """转为 OpenAI messages 中的 tool 角色消息。"""
        return {
            "role": "tool",
            "tool_call_id": tool_call_id,
            "content": self.content,
        }


class BaseTool(ABC):
    """工具抽象基类。子类必须覆盖 schema 类属性并实现 execute()。"""

    @property
    @abstractmethod
    def schema(self) -> ToolSchema:
        """返回工具的元数据定义。"""
        ...

    @abstractmethod
    async def execute(self, **params) -> ToolResult:
        """执行工具逻辑。参数由 LLM 根据 schema.parameters 传入。"""
        ...


class ToolRegistry:
    """工具注册中心。管理所有可用工具，提供注册、查询、批量生成和分发执行。"""

    def __init__(self):
        self._tools: dict[str, BaseTool] = {}

    # ── 注册 ──────────────────────────────────────────────
    def register(self, tool: BaseTool) -> None:
        """注册一个工具实例。同名覆盖。"""
        self._tools[tool.schema.name] = tool

    def register_many(self, tools: list[BaseTool]) -> None:
        """批量注册。"""
        for t in tools:
            self.register(t)

    # ── 查询 ──────────────────────────────────────────────
    def get(self, name: str) -> Optional[BaseTool]:
        """按名获取工具实例。"""
        return self._tools.get(name)

    def list_names(self) -> list[str]:
        """列出所有已注册工具名。"""
        return list(self._tools.keys())

    @property
    def count(self) -> int:
        return len(self._tools)

    # ── OpenAI 格式 ───────────────────────────────────────
    def get_openai_schemas(self) -> list[dict]:
        """生成 OpenAI function calling 所需的 tools 数组。"""
        return [t.schema.to_openai() for t in self._tools.values()]

    # ── 执行 ──────────────────────────────────────────────
    async def execute(self, name: str, tool_call_id: str, params: dict) -> ToolResult:
        """按名执行工具。工具不存在时返回错误结果。"""
        tool = self._tools.get(name)
        if tool is None:
            return ToolResult(
                tool_name=name,
                success=False,
                content=f"工具 '{name}' 未注册。可用工具: {self.list_names()}",
            )
        try:
            return await tool.execute(**params)
        except TypeError as e:
            return ToolResult(
                tool_name=name,
                success=False,
                content=f"参数错误: {e}",
            )
        except Exception as e:
            return ToolResult(
                tool_name=name,
                success=False,
                content=f"执行异常: {type(e).__name__}: {e}",
            )
