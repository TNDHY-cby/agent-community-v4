# -*- coding: utf-8 -*-
"""辩论模式的**退役守卫**（2026-10-31 反转）。

历史：本文件原是 V52 的"接线守卫"（面板必须在、必须挂进加载流程）。
老板 10-31 口径：「平台AI讨论彻底删；没用的功能卡片还留着干什么」⇒
工作间页的辩论条 + 陈述表单（连同全部 JS）**整体退役**。

守卫随之反转：现在盯的是这些部件**不许再出现** ——
防止哪天又有人把辩论面板/指路按钮加回来。
后端 `platform/debate.py` 与 `/debate` 端点未动（不在本守卫射程内）。
"""
from __future__ import annotations

import os
import re
import sys

_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
if _ROOT not in sys.path:
    sys.path.append(_ROOT)

FRONTEND = os.path.join(_ROOT, "agent_community", "frontend")

#: 这些部件一旦在**活代码**里出现，就是退役失败
_FORBIDDEN = [
    'id="debateBar"',
    'id="debateForm"',
    'id="debateState"',
    "function loadDebate(",
    "function toggleDebate(",
    "function submitDebateStatement(",
    "function runDebate(",
    "function paintDebateBar(",
    "function openStructuredDiscuss(",
    "openStructuredDiscussFromHub",
    "/debate/statements",
    "/debate/run",
    "去结构化讨论",
]

_CSS_COMMENT = re.compile(r"/\*.*?\*/", re.S)
_HTML_COMMENT = re.compile(r"<!--.*?-->", re.S)
_LINE_COMMENT = re.compile(r"(?m)^\s*//.*$")


def strip_comments(src: str) -> str:
    """把 HTML/JS/CSS 注释全剥掉 —— 退役说明写在注释里是允许的，活代码里不许有。"""
    src = _HTML_COMMENT.sub(" ", src)
    src = _CSS_COMMENT.sub(" ", src)
    src = _LINE_COMMENT.sub(" ", src)
    return src


def _read(name: str) -> str:
    with open(os.path.join(FRONTEND, name), encoding="utf-8") as f:
        return f.read()


def test_debate_panel_is_gone_from_workshop():
    live = strip_comments(_read("workshop.html"))
    found = [k for k in _FORBIDDEN if k in live]
    assert not found, "辩论面板/指路按钮在 workshop.html 的活代码里复活了：%s" % found


def test_structured_discuss_pointer_is_gone_everywhere():
    """「去结构化讨论」指路按钮（点开是对接中心）在哪个产品页面都不许再有。"""
    for name in ("workshop.html", "index.html", "v5.html"):
        live = strip_comments(_read(name))
        assert "去结构化讨论" not in live, \
            "%s 的活代码里还有「去结构化讨论」入口（点开是对接中心，语义不通）" % name


def test_discussion_zones_are_read_only_with_notice():
    """三处讨论区（组长对话框/讨论区/车间）改成只读后，口径说明必须在。"""
    src = _read("workshop.html")
    for anchor in ('id="discussion2"', 'id="discussion3"'):
        i = src.find(anchor)
        assert i != -1, "找不到 %s（前端结构变了，请同步本守卫）" % anchor
        seg = src[i:i + 900]
        assert "只读" in seg, "%s 之后的输入区没写「只读」口径说明" % anchor


def test_send_debate_fetch_is_not_called_on_load():
    """加载流程里不许再打辩论端点（面板没了，请求也不许留）。"""
    live = strip_comments(_read("workshop.html"))
    assert "/debate" not in live, "workshop.html 活代码里还有 /debate 请求"
