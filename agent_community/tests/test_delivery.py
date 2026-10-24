# -*- coding: utf-8 -*-
"""守卫：**统一投递层**的选路与"不谎报"（老板 2026-10-23「只能浏览器吗」）。

真机判据在 `tools/delivery_check.py`（native/cdp 两条都真投真读回）；这里钉**选路逻辑**，
让它不依赖真窗口/真浏览器，双平台都能跑：

| # | 判据 |
|---|---|
| ① | 有页面 ws ⇒ 先走 cdp；cdp 成功就用它（不再碰别的通道） |
| ② | 没有 ws、但有 hwnd 且有原生文本框 ⇒ 走 native |
| ③ | cdp/native 都不通 ⇒ 退 uia；uia 也不通 ⇒ 退 clipboard，且 **verified=None + need_human** |
| ④ | 全部失败时 `ok=False` 且 `attempts` 里**每一档都有原因**（不许空着） |
| ⑤ | `prefer` 可以锁定某一档（排障用），失败不偷偷换档 |
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.append(str(_ROOT))


def _fake_bridge(monkeypatch, ok=True):
    """把剪贴板写入替掉（不让测试碰真剪贴板）。"""
    from agent_community.platform import wakeup_bridge as wbmod

    calls = {"n": 0}

    def fake_set(self, text):
        calls["n"] += 1
        if not ok:
            raise RuntimeError("模拟剪贴板被占用")

    monkeypatch.setattr(wbmod.wakeup_bridge.__class__, "_set_clipboard", fake_set, raising=False)
    return calls


def test_cdp_first_when_page_ws_given(monkeypatch):
    from agent_community.platform import cdp_input, delivery

    used = []

    def fake_run(coro):
        used.append("cdp")
        return {"ok": True, "verified": True, "mode": "insert", "detail": "读回通过", "readback": "x"}

    monkeypatch.setattr(cdp_input, "run", fake_run, raising=False)
    res = delivery.deliver("hello", ws="ws://x", hwnd=123)
    assert res["channel"] == "cdp" and res["verified"] is True, res
    assert used == ["cdp"]


def test_native_used_when_no_ws_and_window_has_text_control(monkeypatch):
    from agent_community.platform import delivery, visual_launch

    def fake_inject(args, timeout=45.0):
        assert "-Hwnd" in args, "原生通道必须按句柄定位（实测 -Pid 会认错窗口）"
        return 0, ("[target] hwnd=1 pid=2 class=x title=y\n"
                   "[focus] SetForegroundWindow=True is_foreground=True\n"
                   "[inject] WM_PASTE -> class=Edit  (5 chars)\n"
                   "[verify] control class=Edit text length=5 contains_expected=True")

    monkeypatch.setattr(visual_launch, "_run_inject", fake_inject, raising=False)
    res = delivery.deliver("hello", hwnd=1)
    assert res["channel"] == "native" and res["verified"] is True, res
    assert "WM_GETTEXT" in res["detail"] or "读回" in res["detail"], res["detail"]


def test_everything_fails_ends_with_honest_clipboard(monkeypatch):
    from agent_community.platform import delivery, visual_launch

    monkeypatch.setattr(visual_launch, "_run_inject",
                        lambda args, timeout=45.0: (3, "[FAIL] -Method wmpaste needs a text-control child; none found."),
                        raising=False)
    monkeypatch.setattr(delivery, "_run_uia",
                        lambda *a, **kw: (4, "[uia] ok=False why=no-editable-element"), raising=False)
    _fake_bridge(monkeypatch, ok=True)

    res = delivery.deliver("hello", hwnd=9)
    assert res["channel"] == "clipboard", res
    assert res["verified"] is None and res.get("need_human") is True, res
    reasons = [a.get("why") for a in res["attempts"] if a["channel"] in ("native", "uia")]
    assert all(reasons), "每一档失败都要有原因：%s" % res["attempts"]


def test_all_channels_fail_says_why_each(monkeypatch):
    from agent_community.platform import delivery, visual_launch

    monkeypatch.setattr(visual_launch, "_run_inject",
                        lambda args, timeout=45.0: (127, "注入脚本没能跑起来"), raising=False)
    monkeypatch.setattr(delivery, "_run_uia", lambda *a, **kw: (127, "非 Windows"), raising=False)
    _fake_bridge(monkeypatch, ok=False)

    res = delivery.deliver("hello", hwnd=9)
    assert res["ok"] is False and res["verified"] is False, res
    assert len(res["attempts"]) >= 3, res["attempts"]
    assert all(a.get("why") for a in res["attempts"]), res["attempts"]


def test_prefer_pins_one_channel(monkeypatch):
    from agent_community.platform import delivery, visual_launch

    monkeypatch.setattr(visual_launch, "_run_inject",
                        lambda args, timeout=45.0: (3, "[FAIL] text-control"), raising=False)
    _fake_bridge(monkeypatch, ok=True)
    res = delivery.deliver("hello", hwnd=9, prefer="native")
    assert [a["channel"] for a in res["attempts"]] == ["native"], res["attempts"]
    assert res["ok"] is False, "锁定档失败就该如实失败，不偷偷换档"
