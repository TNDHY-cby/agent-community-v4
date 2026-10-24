# -*- coding: utf-8 -*-
"""守卫：**界面改版后还能不能找到输入框**（老板 2026-10-23 问的那句）。

诚实边界（写进判据，别自欺）：
- 教过的**相对位置**（0~1）能扛：窗口移动 / 缩放 / 换显示器 / 改 DPI；
- **扛不住界面改版**（侧栏变宽、聊天面板挪位）⇒ 必须有一条"自愈"和一条"说清楚"的路：
  ① CDP 可用时：投递成功后**自动校准**（问页面那个输入框现在在哪，刷新相对位置）；
  ② CDP 不可用时：位置失准只能靠人再看一眼（脚本已明说"未核实"），并提供历史位置可换回。

| # | 判据 |
|---|---|
| ① | 教位置时**存窗口尺寸 + 历史**（最近 3 次），换版后可回溯 |
| ② | CDP 投递成功后**自动刷新** anchor（`source=auto-from-cdp`） |
| ③ | 自动校准失败**不影响投递结果**，但要留话（不静默） |
| ④ | 取不到输入框位置时，校准跳过并说明原因（不伪造坐标） |
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


def test_anchor_keeps_window_size_and_history():
    from agent_community.platform import input_anchor as ia

    ia.set_anchor("h1", 0.5, 0.8, frame="render-child", win_w=1542, win_h=1362)
    ia.set_anchor("h1", 0.55, 0.85, frame="render-child", win_w=1600, win_h=1362)
    rec = ia.get("h1")
    assert rec["rel_x"] == 0.55 and rec["win_w"] == 1600 and rec["frame"] == "render-child"
    assert len(rec["history"]) == 1 and rec["history"][0]["rel_x"] == 0.5, \
        "旧位置要留着（界面改版后能用上）：%s" % (rec["history"],)
    # 再多教几次，历史最多留 3 条（不无限膨胀）
    for i in range(6):
        ia.set_anchor("h1", 0.6 + i * 0.01, 0.9, frame="render-child")
    assert len(ia.get("h1")["history"]) <= 3, ia.get("h1")


def test_delivery_to_harness_auto_calibrates_anchor_from_cdp(monkeypatch):
    from agent_community.platform import cdp_input, delivery, input_anchor

    monkeypatch.setattr(delivery, "resolve_target", lambda hid, **k: {
        "ok": True, "name": "示例会话", "ws": "ws://127.0.0.1:9222/devtools/page/X",
        "hwnd": 0, "title": "示例会话", "idx": 0, "source": "按会话名在调试端口里找到页面"})
    monkeypatch.setattr(delivery, "deliver", lambda text, **k: {
        "ok": True, "channel": "cdp", "verified": True, "attempts": [], "state": "delivered_verified",
        "detail": "读回核对通过"})

    async def fake_box(cdp, idx=0):
        return {"ok": True, "x": 0.42, "y": 0.88, "tag": "TEXTAREA"}

    monkeypatch.setattr(cdp_input, "element_box", fake_box, raising=True)

    class _FakeCDP:
        def __init__(self, ws, timeout=8.0):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(cdp_input, "CDP", _FakeCDP, raising=True)

    res = delivery.deliver_to_harness("h1", "喂")
    assert res["ok"] is True and res["channel"] == "cdp"
    rec = input_anchor.get("h1")
    assert rec and rec["rel_x"] == 0.42 and rec["rel_y"] == 0.88, rec
    assert rec["source"] == "auto-from-cdp" and rec["frame"] == "cdp-auto", rec


def test_calibration_failure_does_not_break_delivery(monkeypatch, capsys):
    from agent_community.platform import cdp_input, delivery

    monkeypatch.setattr(delivery, "resolve_target", lambda hid, **k: {
        "ok": True, "name": "示例会话", "ws": "ws://x/y", "hwnd": 0, "title": "示例会话",
        "idx": 0, "source": "记忆（上次成功过的页面）"})
    monkeypatch.setattr(delivery, "deliver", lambda text, **k: {
        "ok": True, "channel": "cdp", "verified": True, "attempts": [],
        "state": "delivered_verified", "detail": "读回核对通过"})

    async def boom(cdp, idx=0):
        return {"ok": False, "why": "页面里没有这样的输入框（界面改版了？）"}

    monkeypatch.setattr(cdp_input, "element_box", boom, raising=True)

    class _FakeCDP:
        def __init__(self, ws, timeout=8.0):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(cdp_input, "CDP", _FakeCDP, raising=True)

    res = delivery.deliver_to_harness("h1", "喂")
    assert res["ok"] is True, "校准失败不许影响投递结果"
    out = capsys.readouterr().out
    assert "位置自动校准跳过" in out and "界面改版" in out, out
