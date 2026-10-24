# -*- coding: utf-8 -*-
"""守卫：**一次跑完的视觉激活**（新建会话 → 点输入框 → 粘贴 → 发送）。

起因（老板 2026-10-24 原话）：「**新建会话之后就没办法发送通知了**」——
分两个按钮两次请求，中间那段（已经点了新建会话、界面正切过去）没人管。

| # | 判据 |
|---|---|
| ① | 顺序固定：**前台自证 → 新建会话 → 输入框(粘贴) → 发送 → 收尾还前台**，一步不乱 |
| ② | 任一步失败就**停在原地**并说清是哪一步（后面的步骤不再执行，不"带病往下点"） |
| ③ | 发送优先点**框好的发送按钮**；没框才按回车 |
| ④ | 收尾把前台还回调用前那个窗口（`restore_focus`），还不了要写清 |
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.append(str(_ROOT))


def _fake_env(monkeypatch, *, click_ok=True, keys_ok=True, changed=True, fg_ok=True):
    from agent_community.platform import input_sim, screen_vision
    from agent_community.platform.wakeup_bridge import wakeup_bridge

    order = []
    monkeypatch.setattr(input_sim, "ensure_foreground",
                        lambda h, **k: (order.append("focus"),
                                        {"ok": fg_ok, "why": "" if fg_ok else "抬不到前台"})[1],
                        raising=True)
    monkeypatch.setattr(input_sim, "foreground_window", lambda: 777, raising=True)
    monkeypatch.setattr(input_sim, "set_foreground",
                        lambda h, **k: (order.append(("restore", h)), {"ok": True})[1], raising=True)
    monkeypatch.setattr(input_sim, "click_in_window",
                        lambda h, rx, ry, **k: (order.append(("click", round(rx, 3), round(ry, 3))),
                                                {"ok": click_ok, "why": "" if click_ok else "被挡住"})[1],
                        raising=True)
    monkeypatch.setattr(input_sim, "send_keys",
                        lambda c, **k: (order.append(("key", c)),
                                        {"ok": keys_ok, "combo": c, "why": "" if keys_ok else "按键失败"})[1],
                        raising=True)
    monkeypatch.setattr(wakeup_bridge, "_set_clipboard", lambda t: order.append(("clip", t)), raising=False)
    monkeypatch.setattr(screen_vision, "grab_gray",
                        lambda h, **k: {"ok": True, "w": 8, "h": 8,
                                        "rows": [bytearray(8) for _ in range(8)]}, raising=True)
    monkeypatch.setattr(screen_vision, "band_diff",
                        lambda a, b, y, **k: {"ok": True, "ratio": 0.02, "changed": changed, "why": ""},
                        raising=True)
    return order


REG = {"input": {"x": 0.2, "y": 0.8, "w": 0.4, "h": 0.1},
       "send": {"x": 0.62, "y": 0.8, "w": 0.04, "h": 0.04},
       "sessions": {"x": 0.02, "y": 0.1, "w": 0.2, "h": 0.7},
       "new_session": {"x": 0.03, "y": 0.05, "w": 0.1, "h": 0.04}}


def test_full_chain_order_and_send_button(monkeypatch):
    from agent_community.platform import visual_run

    order = _fake_env(monkeypatch)
    r = visual_run.run(4321, REG, "请回复数字 7", settle_new=0.0, with_new_session=True)
    assert r["ok"] is True, r
    names = [s["name"] for s in r["steps"]]
    assert names == ["前台", "新建会话", "输入框", "发送"], names
    kinds = [o[0] if isinstance(o, tuple) else o for o in order]
    assert kinds == ["focus", "click", "click", "clip", "key", "click", "restore"], order
    # 新建会话点的是它自己的中心；发送点的是框好的发送按钮（不是回车）
    assert order[1][1:] == (0.08, 0.07), order[1]
    assert ("key", "enter") not in order, order
    assert r["state"] == "sent_unverified" and r["focus_restored"] is True, r


def test_default_is_no_new_session_just_current_one(monkeypatch):
    """老板 2026-10-24：**不做会话选择**——用户自己把那个会话点开，平台只管把通知送到当前会话。

    ⇒ 默认**不点「新建会话」**（少一步、少一次误点），要新建的场合才显式传 true。
    """
    from agent_community.platform import visual_run

    order = _fake_env(monkeypatch)
    r = visual_run.run(4321, REG, "请回复数字 7", settle_new=0.0)
    names = [s["name"] for s in r["steps"]]
    assert names == ["前台", "输入框", "发送"], names
    kinds = [o[0] if isinstance(o, tuple) else o for o in order]
    # 前台 → 点输入框 → 剪贴板 → Ctrl+V → 点发送按钮 → 还前台（**没有**点新建会话那一下）\n    assert kinds == ["focus", "click", "clip", "key", "click", "restore"], order


def test_send_falls_back_to_enter_when_no_send_box(monkeypatch):
    from agent_community.platform import visual_run

    order = _fake_env(monkeypatch)
    reg = {k: v for k, v in REG.items() if k != "send"}
    r = visual_run.run(4321, reg, "你好", settle_new=0.0)
    assert r["ok"] is True, r
    assert ("key", "enter") in order, order
    assert [s["name"] for s in r["steps"]][-1] == "发送"


def test_stops_at_first_failure(monkeypatch):
    """抬不到前台 ⇒ 一步都不点（后面的步骤不许"带病往下走"）。"""
    from agent_community.platform import visual_run

    order = _fake_env(monkeypatch, fg_ok=False)
    r = visual_run.run(4321, REG, "你好", settle_new=0.0)
    assert r["ok"] is False and r["steps"][0]["name"] == "前台", r
    assert not [o for o in order if isinstance(o, tuple) and o[0] == "click"], order

    # 点「新建会话」被挡住 ⇒ 停在"新建会话"，不点输入框
    monkeypatch.undo()
    order2 = _fake_env(monkeypatch, click_ok=False)
    r2 = visual_run.run(4321, REG, "你好", settle_new=0.0, with_new_session=True)
    assert r2["ok"] is False, r2
    assert [s["name"] for s in r2["steps"]] == ["前台", "新建会话"], r2["steps"]
    assert "新建会话" in (r2.get("why") or ""), r2


def test_no_input_box_is_an_honest_failure(monkeypatch):
    from agent_community.platform import visual_run

    _fake_env(monkeypatch)
    r = visual_run.run(4321, {"new_session": REG["new_session"]}, "你好", settle_new=0.0)
    assert r["ok"] is False and "没框过输入框" in (r.get("why") or ""), r
