# -*- coding: utf-8 -*-
"""守卫：**框选区域**（老板 2026-10-23 夜的设计：让用户在截图上框出输入框和发送按钮）。

| # | 判据 |
|---|---|
| ① | 存两个框 → 读回来一致（相对坐标夹到 0~1），`center_of` 给框中心 |
| ② | 只给一个框也行（只框输入框 = 粘完你自己点发送） |
| ③ | 框给得不全（缺 y / w=0）⇒ **400 说清缺什么**，不静默 |
| ④ | 用户给的关键词要留住（`keywords`），换下一次框选仍然保留 |
| ⑤ | 投递时**框选优先**于"教过的位置"（用户亲手框的最可信） |
| ⑥ | `press_enter` 真的会"发"：框了发送按钮就点它，没框就按回车，失败要写进 `press_note` |
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.append(str(_ROOT))


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("AC_DATA_DIR", str(tmp_path))
    yield


def test_store_and_read_regions():
    from agent_community.platform import input_regions as ir

    r = ir.set_regions("h1", input_box={"x": 0.1, "y": 0.8, "w": 0.8, "h": 0.08},
                       send_box={"x": 0.92, "y": 0.8, "w": 0.06, "h": 0.06},
                       keywords=["在这里输入"], frame={"w": 1442, "h": 902})
    assert r["ok"] and r["regions"]["input"]["w"] == 0.8
    rec = ir.get("h1")
    assert rec["keywords"] == ["在这里输入"] and rec["frame"]["w"] == 1442
    c = ir.center_of(rec["input"])
    assert abs(c["rel_x"] - 0.5) < 1e-6 and abs(c["rel_y"] - 0.84) < 1e-6, c


def test_only_input_box_is_enough_and_keywords_stick():
    from agent_community.platform import input_regions as ir

    ir.set_regions("h2", input_box={"x": 0, "y": 0.9, "w": 1, "h": 0.1}, keywords=["发消息"])
    assert ir.get("h2")["send"] is None
    # 再框一次发送按钮，关键词不该丢
    ir.set_regions("h2", send_box={"x": 0.9, "y": 0.9, "w": 0.08, "h": 0.08})
    assert ir.get("h2")["keywords"] == ["发消息"]


def test_bad_box_is_rejected_with_reason():
    from agent_community.platform import input_regions as ir

    r = ir.set_regions("h3", input_box={"x": 0.1, "w": 0.5, "h": 0.05})   # 缺 y
    assert r["ok"] is False and "x/y/w/h" in r["why"], r
    r2 = ir.set_regions("h3", input_box={"x": 0.1, "y": 0.2, "w": 0, "h": 0.05})
    assert r2["ok"] is False, r2
    r3 = ir.set_regions("h3")
    assert r3["ok"] is False and "至少要框" in r3["why"], r3


def test_delivery_prefers_regions_over_taught_anchor(monkeypatch):
    from agent_community.platform import delivery, input_anchor, input_regions

    input_anchor.set_anchor("h4", 0.11, 0.22)                 # 教过的（旧方式）
    input_regions.set_regions("h4", input_box={"x": 0.2, "y": 0.8, "w": 0.6, "h": 0.1})  # 框选的
    monkeypatch.setattr(delivery, "resolve_target", lambda hid, **k: {
        "ok": True, "name": "示例", "ws": "", "hwnd": 4321, "title": "示例", "idx": 0,
        "source": "手动绑定的目标"})
    seen = {}

    def fake_deliver(text, **k):
        seen.update(k)
        return {"ok": True, "channel": "clipboard", "verified": None, "attempts": [],
                "state": "sent_unverified", "detail": "x"}

    monkeypatch.setattr(delivery, "deliver", fake_deliver, raising=True)
    delivery.deliver_to_harness("h4", "喂")
    # 点的是**框的正中**（老板 2026-10-24 拍板：「脚本点击时默认点击中间」）：x 0.5；y = 0.8 + 0.1*0.5 = 0.85
    assert seen["anchor"]["rel_x"] == 0.5 and seen["anchor"]["rel_y"] == 0.85, seen["anchor"]


def test_input_point_defaults_to_center():
    """框选输入框时**点哪儿**：默认正中（老板原话）。`at` 只是留给以后微调。"""
    from agent_community.platform import input_regions as ir

    box = {"x": 0.1, "y": 0.8, "w": 0.8, "h": 0.15}
    p = ir.input_point(box)
    c = ir.center_of(box)
    assert p == c, "默认必须等于框中心：%s vs %s" % (p, c)
    assert 0.8 < p["rel_y"] < 0.95, p
    # 显式给 at 时也要夹在框里（不给到边线上）
    assert ir.input_point(box, at=0.0)["rel_y"] > 0.8
    assert ir.input_point(box, at=1.0)["rel_y"] < 0.95


def test_press_enter_clicks_send_button_and_reports(monkeypatch):
    from agent_community.platform import delivery, input_sim

    called = {}

    def fake_click(hwnd, rx, ry, button="left"):
        called.update(hwnd=hwnd, rx=rx, ry=ry)
        return {"ok": True}

    monkeypatch.setattr(input_sim, "click_in_window", fake_click, raising=True)
    r = delivery._do_send_action(999, {"rel_x": 0.93, "rel_y": 0.85})
    assert r["ok"] and r["how"] == "click-send", r
    assert called["rx"] == 0.93 and called["hwnd"] == 999, called
    assert "发送按钮" in r["note"]


def test_press_enter_falls_back_to_enter_key(monkeypatch):
    from agent_community.platform import delivery, input_sim

    monkeypatch.setattr(input_sim, "send_keys", lambda combo, **k: {"ok": True}, raising=True)
    r = delivery._do_send_action(0, None)
    assert r["ok"] and r["how"] == "enter" and "回车" in r["note"], r
