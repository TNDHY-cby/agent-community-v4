# -*- coding: utf-8 -*-
"""守卫：**投递结果的唯一口径 `state`** ＋ "看不看得见输入框"的如实解释。

起因（2026-10-23 老板真机两连问）：
1) 界面写「✗ 没有投递」，下面却写着「已改用剪贴板 + Ctrl+V」—— 自相矛盾；
2) 界面写「成功把窗口拉到最前，**没有检测到输入框**」—— 用户以为产品漏了输入框，
   其实是 Electron 的输入框由渲染进程绘制，**系统层看不到**。

⇒ 后端给出两个**机器可读**字段，界面只看它们，不许从 `detail` 里猜：
- `state` ∈ `delivered_verified` / `sent_unverified` / `queued_for_human` / `not_sent`
- `text_control` = `{found: True|False|None, class?, why?}`（`found=False` 时 `why` 是给人看的一句话）

| # | 判据 |
|---|---|
| ① | 读回核对通过 ⇒ `delivered_verified` |
| ② | 我们按了键但核实不了 ⇒ `sent_unverified`（**不是**"没投递"） |
| ③ | 只把内容放进剪贴板（半自动，人还没贴）⇒ `queued_for_human`（`queued_only=True`） |
| ④ | 什么都没成 / 没找到目标 ⇒ `not_sent` |
| ⑤ | 目标没有系统可读输入框 ⇒ `text_control.found=False` **且** `why` 说清"不是没有输入框，而是系统层看不到" |
| ⑥ | 有可读控件 ⇒ `found=True` 且带 `class` | 
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.append(str(_ROOT))


def test_state_mapping_is_total_and_exclusive():
    from agent_community.platform import delivery as d

    assert d.state_of({"ok": True, "verified": True}) == "delivered_verified"
    assert d.state_of({"ok": True, "verified": None}) == "sent_unverified"
    assert d.state_of({"ok": True, "verified": None, "queued_only": True}) == "queued_for_human"
    assert d.state_of({"ok": False, "verified": False}) == "not_sent"
    assert d.state_of({}) == "not_sent", "空结果也必须落进 not_sent，不许返回 None"
    for st in ("delivered_verified", "sent_unverified", "queued_for_human", "not_sent"):
        assert st in d.STATES


def test_launch_always_carries_state_and_text_control(monkeypatch):
    from agent_community.platform import visual_launch as vl

    class M:
        session_aliases = ["示例窗口"]
        session_ref = {}

    # ① Electron 类：wmpaste 不适用 ⇒ 剪贴板档（按了键、核实不了）
    monkeypatch.setattr(vl, "_run_inject", lambda args, timeout=45.0: (
        0, "[focus] SetForegroundWindow=True is_foreground=True\n"
           "[clipboard] wrote=3 readback=ok\n"
           "[inject] clipboard + Ctrl+V  (3 chars)\n"
           "[verify] no text-control child found among 2 child window(s)."), raising=False)
    r = vl.launch(M(), "abc", method="wmpaste", verify=True)
    assert r["state"] == "sent_unverified", r
    assert r["text_control"]["found"] is False
    assert "不是它没有输入框" in r["text_control"]["why"], r["text_control"]

    # ② 原生控件：读回核对通过
    monkeypatch.setattr(vl, "_run_inject", lambda args, timeout=45.0: (
        0, "[focus] SetForegroundWindow=True is_foreground=True\n"
           "[inject] WM_PASTE -> class=Edit  (3 chars)\n"
           "[verify] control class=Edit text length=3 contains_expected=True"), raising=False)
    r2 = vl.launch(M(), "abc", method="wmpaste", verify=True)
    assert r2["state"] == "delivered_verified", r2
    assert r2["text_control"]["found"] is True and r2["text_control"].get("class") == "Edit", r2["text_control"]


def test_clipboard_only_path_is_queued_for_human(monkeypatch):
    """半自动档（只放剪贴板、没按键）必须标 `queued_for_human`，不能混进"送出去了"。"""
    from agent_community.platform import visual_launch as vl

    class M:
        session_aliases = ["示例窗口"]
        session_ref = {}

    monkeypatch.setattr(vl, "_run_inject", lambda args, timeout=45.0: (127, "注入脚本没能跑起来"),
                        raising=False)
    # ⚠️ 这条**不能依赖宿主机剪贴板**（Linux 上没有 xclip/xsel 时半自动档本来就写不进去）：
    #    我们测的是"把半自动档映射成 queued_for_human"这条口径，不是"这台机器的剪贴板能不能用"。
    monkeypatch.setattr(vl, "_clipboard", lambda member, text: {
        "ok": True, "method": "clipboard", "need_human": True, "verified": None,
        "queued_only": True,
        "detail": "已把 %d 字放进剪贴板（半自动档）" % len(text),
        "next_step": "点一下目标窗口按 Ctrl+V"}, raising=True)
    r = vl.launch(M(), "abc", method="clipboard", verify=False)
    assert r["state"] == "queued_for_human", r
    assert r.get("queued_only") is True and r.get("need_human") is True, r


def test_missing_window_is_not_sent(monkeypatch):
    from agent_community.platform import visual_launch as vl

    class M:
        session_aliases = ["示例窗口"]
        session_ref = {}

    monkeypatch.setattr(vl, "_run_inject", lambda args, timeout=45.0: (
        3, "[FAIL] no matching window (pass -Pid or -TitleLike)"), raising=False)
    r = vl.launch(M(), "abc", method="wmpaste", verify=False)
    assert r["state"] == "not_sent", r
    assert r["text_control"]["found"] is None, "找不到窗口时不该断言\"有没有输入框\"：%s" % r["text_control"]


def test_delivery_layer_also_carries_state_and_text_control(monkeypatch):
    from agent_community.platform import delivery, visual_launch

    monkeypatch.setattr(visual_launch, "_run_inject", lambda args, timeout=45.0: (
        3, "[FAIL] -Method wmpaste needs a text-control child; none found."), raising=False)
    monkeypatch.setattr(delivery, "_run_uia", lambda *a, **kw: (4, "[uia] ok=False why=no-editable-element"),
                        raising=False)
    monkeypatch.setattr(delivery, "_clipboard", create=True, new=lambda *a, **k: None) if False else None
    res = delivery.deliver("abc", hwnd=9)
    assert res["state"] in delivery.STATES, res
    assert "text_control" in res, res
    assert res["text_control"]["found"] is False, res["text_control"]


def test_clipboard_half_auto_says_queued_for_human_and_never_presses_enter(monkeypatch):
    """**没点进输入框**时的半自动档（2026-10-24 真机事故的回填）。

    实测症状：`POST …/deliver {"text":…,"enter":true}` 返回 `state=sent_unverified`（＝"已送出"），
    而目标输入框**还是空的**；更糟的是它还照按了回车 —— 框里要是用户自己写的半句话，
    就被提前发出去了。这条钉住「拿不到输入框位置时」的规矩：
      ① 字照样放进剪贴板（人工还能用），但对外必须是 `queued_for_human`；
      ② **不许**按 Ctrl+V（会粘到别的程序里），**也不许**按回车。
    """
    from agent_community.platform import delivery, input_sim
    from agent_community.platform.wakeup_bridge import wakeup_bridge

    # ⚠️ 必须打在**类**上：打在实例上时 pytest 事后会把"绑定方法"写回实例属性，
    #    反而把后面对类打的补丁遮住（实测：害得别的用例里剪贴板不再抛错）
    monkeypatch.setattr(type(wakeup_bridge), "_set_clipboard", lambda self, text: None, raising=False)
    pressed = []
    monkeypatch.setattr(input_sim, "send_keys",
                        lambda combo, **kw: (pressed.append(combo), {"ok": True, "combo": combo})[1],
                        raising=False)
    monkeypatch.setattr(delivery, "_do_send_action",
                        lambda *a, **kw: (pressed.append("send-action"),
                                          {"ok": True, "how": "enter", "note": "按了回车"})[1],
                        raising=False)

    res = delivery.deliver("请回复数字 7", prefer="clipboard", press_enter=True)   # 没有 anchor

    assert res["channel"] == "clipboard", res
    assert res.get("queued_only") is True, "剪贴板半自动档必须标 queued_only，否则对外会说成'已送出'：%s" % res
    assert res["state"] == "queued_for_human", res
    assert pressed == [], "没点进输入框就绝不能按任何键（Ctrl+V/回车）：%s" % pressed
    assert res.get("paste_ok") is False, res


def test_clipboard_clicks_then_pastes_and_reports_vision(monkeypatch):
    """**老板 2026-10-24 定的做法**：点输入框（光标落进去）→ 模拟键盘 Ctrl+V → 可选回车。

    判据（都按真结果，不按源码）：
      ① 真的点了输入框、真的按了 Ctrl+V，顺序是**先点后粘**；
      ② 粘贴前后用"输入框那条带的画面变化"作为读不回文字时的唯一硬信号；
      ③ 状态下不越界：读不回文字就只能到 `sent_unverified`（不许升成 delivered_verified）。
    """
    from agent_community.platform import delivery, input_sim, screen_vision

    order = []
    monkeypatch.setattr(input_sim, "click_in_window",
                        lambda hwnd, rx, ry, **kw: (order.append(("click", hwnd, rx, ry)),
                                                    {"ok": True})[1], raising=False)
    monkeypatch.setattr(input_sim, "ensure_foreground",
                        lambda hwnd, **kw: (order.append(("focus", hwnd)),
                                            {"ok": True, "foreground": hwnd, "why": ""})[1],
                        raising=False)
    monkeypatch.setattr(input_sim, "send_keys",
                        lambda combo, **kw: (order.append(("key", combo)),
                                             {"ok": True, "combo": combo})[1], raising=False)
    from agent_community.platform.wakeup_bridge import wakeup_bridge

    # ⚠️ 必须打在**类**上：打在实例上时 pytest 事后会把"绑定方法"写回实例属性，
    #    反而把后面对类打的补丁遮住（实测：害得别的用例里剪贴板不再抛错）
    monkeypatch.setattr(type(wakeup_bridge), "_set_clipboard", lambda self, text: None, raising=False)
    monkeypatch.setattr(delivery, "_do_send_action",
                        lambda *a, **kw: (order.append(("send",)), {"ok": True, "how": "enter",
                                                                   "note": "按了回车"})[1],
                        raising=False)

    # 造两张 40x12 的灰度图：粘贴前全 20，粘贴后在 "输入框那条带" 里画一条亮线
    def pic(band: bool) -> dict:
        rows = [bytearray([20] * 40) for _ in range(12)]
        if band:
            rows[8] = bytearray([200] * 40)
        return {"ok": True, "w": 40, "h": 12, "rows": rows, "client_w": 40, "client_h": 12}

    shots = [pic(False), pic(True)]
    monkeypatch.setattr(screen_vision, "grab_gray", lambda hwnd, **kw: shots.pop(0), raising=False)

    res = delivery.deliver("请回复数字 9", hwnd=7, prefer="clipboard", press_enter=True,
                           anchor={"rel_x": 0.5, "rel_y": 0.8})

    kinds = [o[0] for o in order]
    assert kinds == ["click", "focus", "click", "key", "send"], \
        "顺序必须是：先点(早) → 临粘前**重新**抬前台 → **重新**点一次 → Ctrl+V → 才回车：%s" % order
    assert order[-2] == ("key", "ctrl+v"), order
    assert order[-3][0] == "click", order
    assert res["paste_ok"] is True and res["paste_seen"] is True, res
    assert res["state"] == "sent_unverified", "读不回文字就只能到'送出了未核实'：%s" % res
    assert res.get("press_ok") is True, res


def test_clipboard_does_not_paste_when_window_cannot_come_to_front(monkeypatch):
    """抬不到前台就不按 Ctrl+V（不然内容会粘进**别人**的程序里）。"""
    from agent_community.platform import delivery, input_sim, screen_vision
    from agent_community.platform.wakeup_bridge import wakeup_bridge

    # ⚠️ 必须打在**类**上：打在实例上时 pytest 事后会把"绑定方法"写回实例属性，
    #    反而把后面对类打的补丁遮住（实测：害得别的用例里剪贴板不再抛错）
    monkeypatch.setattr(type(wakeup_bridge), "_set_clipboard", lambda self, text: None, raising=False)
    monkeypatch.setattr(input_sim, "click_in_window", lambda *a, **kw: {"ok": True}, raising=False)
    monkeypatch.setattr(input_sim, "ensure_foreground",
                        lambda hwnd, **kw: {"ok": False, "foreground": 99,
                                            "why": "抬不到前台（现在的前台是别的窗口 99）"}, raising=False)
    keys = []
    monkeypatch.setattr(input_sim, "send_keys",
                        lambda combo, **kw: (keys.append(combo), {"ok": True})[1], raising=False)
    monkeypatch.setattr(screen_vision, "grab_gray", lambda *a, **kw: {"ok": False, "why": "不该被调用"},
                        raising=False)

    res = delivery.deliver("请回复数字 9", hwnd=7, prefer="clipboard", press_enter=True,
                           anchor={"rel_x": 0.5, "rel_y": 0.8})

    assert keys == [], "抬不到前台就绝不能按键：%s" % keys
    assert res["paste_ok"] is False and res["state"] == "queued_for_human", res
    assert "没按 Ctrl+V" in res["detail"], res["detail"]
