# -*- coding: utf-8 -*-
"""守卫：**视觉估位**（老板 2026-10-23 夜定的方向）。

老板原话：「把鼠标放在输入框上面怎么点击平台里面的按钮？让脚本学习位置根本不合理，
我的设计是利用视觉脚本去检测大致方位（例如输入框一般都在中下方）、关键词、图像。」

⇒ 判据就按这三条能力写（关键词要能读到文字，读不到时必须**如实说没用到**）：

| # | 判据 |
|---|---|
| ① | 给一张"中下方有一条浅色宽带"的合成图 ⇒ 定位到那条带（y 在 0.6~1.0） |
| ② | 给一张"上半部有宽带、下半部空"的图 ⇒ **不去点上半部**（方位先验：聊天框在中下方） |
| ③ | 给一张"全黑/全平"的图 ⇒ 如实说"没把握"，`ok=False`（**绝不瞎点**） |
| ④ | 返回里 `keywords_used=False`（读不到文字就不假装用了关键词） |
| ⑤ | `grab_gray` 在非 Windows 上如实说抓不了（不抛异常） |
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.append(str(_ROOT))


def _pic(rows: list[list[int]]) -> dict:
    h = len(rows)
    w = len(rows[0])
    return {"ok": True, "w": w, "h": h, "rows": [bytearray(r) for r in rows],
            "client_w": w * 10, "client_h": h * 10, "step": 10, "why": ""}


def _canvas(w=100, h=100, base=20):
    return [[base] * w for _ in range(h)]


def test_finds_band_in_lower_half_and_reports_reasons():
    from agent_community.platform import screen_vision as sv

    rows = _canvas()
    # 中下方放一条"输入框"：y 78~88，比页面亮一点点（暗色主题实测只差 10 个灰阶）
    for y in range(78, 88):
        rows[y] = [32] * 100
    r = sv.find_input_box(_pic(rows))
    assert r["ok"] is True, r
    assert 0.6 <= r["rel_y"] <= 1.0, r
    assert r["score"] >= sv.MIN_SCORE
    assert r["keywords_used"] is False, "读不到文字就不许说用了关键词"
    assert any("中下方" in x for x in r["reasons"]), r["reasons"]


def test_ignores_a_band_in_the_upper_half():
    from agent_community.platform import screen_vision as sv

    rows = _canvas()
    for y in range(10, 22):
        rows[y] = [60] * 100          # 上半部一条很亮的带（像工具栏）
    r = sv.find_input_box(_pic(rows))
    # 要么没把握，要么定位到下方；绝不许把上半部当输入框
    assert (r["ok"] is False) or (r["rel_y"] >= 0.45), r


def test_flat_image_says_no_confidence():
    from agent_community.platform import screen_vision as sv

    r = sv.find_input_box(_pic(_canvas()))
    assert r["ok"] is False and "把握" in r["why"] or "没找到" in r["why"], r


def test_non_windows_reports_honestly(monkeypatch):
    from agent_community.platform import screen_vision as sv

    monkeypatch.setattr(sv, "_IS_WINDOWS", False, raising=True)
    g = sv.grab_gray(123)
    assert g["ok"] is False and "Windows" in g["why"], g
