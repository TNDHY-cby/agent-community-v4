# -*- coding: utf-8 -*-
"""守卫：**按相似程度搜窗口**（老板 2026-10-24：「平台根据相似程度搜索窗口」）。

为什么不能只做"标题包含"：用户输 `trea` 时 `TRAE CN` 一条都不会命中 —— 真想输对就不用模糊名了。

| # | 判据 |
|---|---|
| ① | 拼错/少一个字母也能排到第一位（`trea` → `TRAE CN`） |
| ② | 前缀相同 / 被包含要略微加权（`设置` 与 `设置 - 系统` 都该排前面） |
| ③ | **不沾边的别塞进来**：`sim < 0.3` 丢掉，一条没有就回空（宁少不滥） |
| ④ | 排序按 `sim` 从高到低，`limit` 生效 |
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.append(str(_ROOT))

WINS = [{"hwnd": "1", "title": "TRAE CN"},
        {"hwnd": "2", "title": "Trae CN"},
        {"hwnd": "3", "title": "Marvis"},
        {"hwnd": "4", "title": "设置"},
        {"hwnd": "5", "title": "设置 - 系统"},
        {"hwnd": "6", "title": "10-23 - 文件资源管理器"}]


def test_typo_still_finds_the_window():
    from agent_community.platform import visual_launch as vl

    got = vl.fuzzy_candidates(WINS, "trea")
    assert got, got
    assert got[0]["title"] == "TRAE CN", got
    assert float(got[0]["sim"]) >= 0.3, got[0]


def test_prefix_and_containment_are_boosted():
    from agent_community.platform import visual_launch as vl

    got = vl.fuzzy_candidates(WINS, "设置")
    titles = [g["title"] for g in got]
    assert titles[0].startswith("设置"), titles
    assert set(titles[:2]) <= {"设置", "设置 - 系统"}, titles


def test_nothing_similar_returns_empty():
    from agent_community.platform import visual_launch as vl

    assert vl.fuzzy_candidates(WINS, "zzzz") == []
    assert vl.fuzzy_candidates(WINS, "") == []


def test_sorted_by_sim_and_limited():
    from agent_community.platform import visual_launch as vl

    got = vl.fuzzy_candidates(WINS, "trae", limit=1)
    assert len(got) == 1, got
    sims = [g["sim"] for g in vl.fuzzy_candidates(WINS, "trae")]
    assert sims == sorted(sims, reverse=True), sims
