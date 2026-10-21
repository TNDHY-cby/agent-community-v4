# -*- coding: utf-8 -*-
"""守卫：**MCP 工具分面**（V75 P1-3）。

判据：
① **不许有孤儿工具** —— 每只工具必须落在某个组里（加了新工具忘了分组，这条会红）；
② 默认组（不设 env）= 当前全部现有工具（**不许把现有能力挡掉**）；
③ 显式收窄（`AC_MCP_TOOLSETS=api`）→ `active_tools()` 变少，且 `tools/list` 只回这些；
④ 未启用的组调用必须被拒、并说清怎么开（不许静默失败）；
⑤ 空组（browser/devices/plan）如实为空，**不许编占位工具**。
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.append(str(_ROOT))

from agent_community import mcp_server as m  # noqa: E402


def test_no_orphan_tools():
    grouped = set()
    for names in m.TOOLSETS.values():
        grouped.update(names)
    all_names = {t["name"] for t in m.TOOLS}
    orphans = all_names - grouped
    assert not orphans, "这些工具没分到任何组（请补进 TOOLSETS）：%s" % sorted(orphans)
    ghosts = grouped - all_names
    assert not ghosts, "TOOLSETS 里写了不存在的工具：%s" % sorted(ghosts)


def test_default_keeps_all_existing_tools(monkeypatch):
    monkeypatch.delenv("AC_MCP_TOOLSETS", raising=False)
    active = {t["name"] for t in m.active_tools()}
    all_names = {t["name"] for t in m.TOOLS}
    assert active == all_names, "默认组把现有工具挡掉了（回归）：缺 %s" % sorted(all_names - active)


def test_narrowing_works(monkeypatch):
    monkeypatch.setenv("AC_MCP_TOOLSETS", "api")
    active = {t["name"] for t in m.active_tools()}
    assert active == set(m.TOOLSETS["api"]), active
    assert len(active) < len(m.TOOLS), "收窄后应当变少"


def test_all_keyword(monkeypatch):
    monkeypatch.setenv("AC_MCP_TOOLSETS", "all")
    assert {t["name"] for t in m.active_tools()} == {t["name"] for t in m.TOOLS}


def test_unknown_group_falls_back_and_empty_groups_stay_empty(monkeypatch):
    monkeypatch.setenv("AC_MCP_TOOLSETS", "不存在的组")
    active = {t["name"] for t in m.active_tools()}
    assert active, "写了不存在的组时要退回默认，而不是把所有工具关掉"
    for empty in ("browser", "devices", "plan"):
        assert m.TOOLSETS[empty] == [], "%s 还没有工具，不许编占位：%r" % (empty, m.TOOLSETS[empty])


def test_call_of_disabled_tool_is_refused(monkeypatch):
    monkeypatch.setenv("AC_MCP_TOOLSETS", "api")
    assert m.tool_enabled("status") is True
    assert m.tool_enabled("submit_task_result") is False, "被收窄掉的工具要能被识别出来（供调用处拒绝）"
