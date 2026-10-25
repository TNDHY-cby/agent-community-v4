# -*- coding: utf-8 -*-
"""守卫：**平台自己要引导对方加 `session_post`**（老板 2026-10-25 原话「平台也要有引导让对方加」）。

判据（数据层，真调模块）：

| # | 判据 |
|---|---|
| ① | 引导文本里必须说清三件事：工具名 `session_post`、入参 `text`、"要真的出现在它自己的会话里" |
| ② | 引导要带上这个 harness 自己的信息（它的 id / 它当前的工具表），不是一份通用空话 |
| ③ | 工具表里已有"会话内发言"工具时 `has_session_post` 必须为 True（别把有能力也说成没有） |
| ④ | 名字族判定与后端 `docking._SESSION_POST_HINTS` **同一口径**（一处真相） |
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.append(str(_ROOT))


def _spg():
    from agent_community.platform import session_post_guide as m
    return m


def test_guide_says_tool_name_param_and_where_it_lands():
    md = _spg().build_md(harness_id="demo-harness", tools=["echo"])
    assert "session_post" in md, md[:400]
    assert "`text`" in md, "必须写清入参是 text"
    assert "真的出现在你自己的会话" in md, "必须说清这句话要落在它自己的会话里"


def test_guide_carries_this_harness_facts():
    md = _spg().build_md(harness_id="demo-harness", tools=["echo", "health"])
    assert "demo-harness" in md, "引导里要带它自己的 harness_id"
    assert "`echo`" in md and "`health`" in md, "引导里要带它当前的工具表（让它知道平台读到了什么）"


def test_has_session_post_matches_backend_hint_family():
    from agent_community.platform.routers import docking

    spg = _spg()
    assert set(spg.SESSION_POST_HINTS) == set(docking._SESSION_POST_HINTS), \
        "两处名单必须一致（一处真相：改一边就要改另一边）"
    assert spg.has_session_post(["session_post"]) is True
    assert spg.has_session_post(["harness_info", "echo", "health"]) is False
    assert spg.has_session_post([]) is False


def test_json_variant_agrees_with_md():
    j = _spg().build_json(harness_id="demo", tools=["say"])
    assert j["has_session_post"] is True and j["tool_name"] == "session_post" and j["param"] == "text"
    assert "session_post" in j["guide_md"]


def test_guide_demands_current_session_and_title():
    """老板真机第三轮：消息写进去了，但写到了**另一个会话** ⇒ 引导必须要求「写当前会话 + 带标题」。"""
    md = _spg().build_md(harness_id="demo", tools=["session_post", "session_messages"])
    assert "用户当前正在看的那个会话" in md, "必须要求写进用户当前看的会话"
    assert "session_title" in md, "必须要求返回会话标题，用户才能核对"
    assert "就写进那一个" in md, "调用方给了 session_id 就必须写那一个"
