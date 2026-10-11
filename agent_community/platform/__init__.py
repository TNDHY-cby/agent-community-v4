# -*- coding: utf-8 -*-
"""外端Agent生产合作社（External Agent Community） Platform — 平台核心模块（v4）

⚠️ 2026-10-06（38 号实施任务书）改为**惰性导出**：
原实现 `from .server import app, agents, tasks, discussion_rooms` —— 任何
`import agent_community.platform.x`（例如协议适配器要复用 `platform.identity` 的边缘鉴权）
都会被连带拽入整个 FastAPI 应用：重量级 + stdout 污染
（见 AGENTS.md「协议适配器放顶层包——`platform/__init__.py` 会拽入 FastAPI 并污染 stdout」）。
改为 PEP 562 `__getattr__`：`from agent_community.platform import app` 的**属性访问行为不变**，
而 `from agent_community.platform import identity` 这类**子模块导入不再拖起 server**。
"""
from __future__ import annotations

import importlib
from typing import Any

_LAZY = ("app", "agents", "tasks", "discussion_rooms")

__all__ = ["app", "agents", "tasks", "discussion_rooms"]


def __getattr__(name: str) -> Any:
    if name in _LAZY:
        _server = importlib.import_module(".server", __name__)
        return getattr(_server, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
