# -*- coding: utf-8 -*-
"""守卫：**点不同协议，必须显示不同的功能卡片**（老板 2026-10-25 特别交代）。

> 老板原话：「好，注意点击不同协议显示的功能卡片不同」

这张表是界面的数据源（`platform/protocol_tree.py`），所以判据钉在数据上：

| # | 判据 |
|---|---|
| ① | 五步齐全，且**第 1 步可跳过**（`skippable=true`），其余四步不可跳 |
| ② | 每个协议**至少一张卡片**，且每张卡片都写了 `action`（点了到底干什么，界面据此绑定） |
| ③ | **任意两个协议的卡片不是同一套**（否则"点不同协议显示不同卡片"就成了空话） |
| ④ | `mcp`/`http_api` 这类**不需要开会话**（`needs_session=false`），桌面/桥类**需要** |
| ⑤ | 每个协议都有必填字段与"怎么测"的说明（不然第 2/4 步没法做） |
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.append(str(_ROOT))


def _tree() -> dict:
    from agent_community.platform import protocol_tree as pt

    return pt.build()


def test_five_steps_with_first_skippable():
    d = _tree()
    assert [s["id"] for s in d["steps"]] == [1, 2, 3, 4, 5], d["steps"]
    assert d["steps"][0]["skippable"] is True, "第 1 步（问协议）要能跳过"
    assert all(s["skippable"] is False for s in d["steps"][1:]), d["steps"]


def test_every_protocol_has_cards_and_every_card_has_an_action():
    d = _tree()
    assert d["protocols"], "没有协议"
    for p in d["protocols"]:
        assert p["cards"], "%s 一张卡片都没有" % p["id"]
        for c in p["cards"]:
            assert c.get("title"), p["id"]
            assert c.get("action"), "%s 的卡片「%s」没写 action（界面只能瞎猜）" % (p["id"], c["title"])


def test_no_two_protocols_show_the_same_card_set():
    d = _tree()
    seen = {}
    for p in d["protocols"]:
        key = tuple(c["title"] for c in p["cards"])
        assert key not in seen, "%s 与 %s 的卡片完全一样（老板要求「不同协议不同卡片」）" % (p["id"], seen.get(key))
        seen[key] = p["id"]


def test_session_need_matches_protocol_nature():
    d = _tree()
    by = {p["id"]: p for p in d["protocols"]}
    assert by["mcp"]["needs_session"] is False and by["http_api"]["needs_session"] is False
    assert by["clipboard"]["needs_session"] is True and by["file_poll"]["needs_session"] is True
    # 被平台叫醒的能力也要与协议相符
    assert by["mcp"]["can_be_woken"] is True and by["clipboard"]["can_be_woken"] is False


def test_each_protocol_says_what_is_required_and_how_to_test():
    d = _tree()
    for p in d["protocols"]:
        assert p.get("required"), "%s 没写必填字段" % p["id"]
        assert p.get("test_how"), "%s 没写怎么测" % p["id"]
        assert p.get("next_step"), "%s 没写下一步" % p["id"]
