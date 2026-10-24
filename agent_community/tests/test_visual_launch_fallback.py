# -*- coding: utf-8 -*-
"""守卫：视觉拉起的两条真故障（老板 2026-10-20 真机报的）。

判据（按真行为）：
  ① **编码**：`inject_window.ps1 -List` 的中文窗口标题到了 Python 这边**不能是替换字符**
     （原来 PowerShell 按 GBK 写 stdout、Python 按 UTF-8 读 ⇒ 界面上一片 `◆`）。
     ⚠️ 只在 Windows 上真跑（其它平台该脚本**如实不支持**，跳过而不是假装通过）。
  ② **兜底**：`wmPaste` 对"没有文本框子窗"的目标（Chrome/Electron 常态）返回 exit=3 时，
     平台要**自动改用「剪贴板 + Ctrl+V」**并把原因写进 detail —— 不许只报失败让人自己贴。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.append(str(_ROOT))


@pytest.mark.skipif(sys.platform != "win32", reason="窗口注入只在 Windows 上有；其它平台如实跳过")
def test_window_titles_are_not_mojibake():
    """真跑 `-List`：中文标题必须正常，不许出现替换字符（U+FFFD）。"""
    from agent_community.platform import visual_launch
    r = visual_launch.list_windows()
    assert r.get("ok") is True, "列举窗口失败：%s" % r.get("detail")
    titles = [w.get("title") or "" for w in (r.get("windows") or [])]
    assert titles, "一个窗口都没扫到（先确认桌面有可见窗口）"
    bad = [t for t in titles if "\ufffd" in t]
    assert not bad, "窗口标题出现乱码（编码没对齐）：%r" % bad[:3]


def test_wmpaste_unsupported_falls_back_to_automatic_clipboard(monkeypatch):
    """exit=3（该窗口没有文本框子窗）⇒ 自动改用脚本的全自动剪贴板档，并说清为什么。"""
    from agent_community.platform import visual_launch as vl

    calls = []

    def fake_run(args, timeout=45.0):
        calls.append(list(args))
        if len(calls) == 1:
            # 第一次：wmPaste 不适用
            assert "-Method" in args and args[args.index("-Method") + 1] == "wmpaste"
            return 3, "[FAIL] -Method wmpaste needs a text-control child; none found."
        # 第二次：剪贴板档成功
        assert args[args.index("-Method") + 1] == "clipboard"
        return 0, ("[focus] SetForegroundWindow=True is_foreground=True\n"
                   "[inject] clipboard + Ctrl+V  (12 chars)\n"
                   "[inject] Enter\n"
                   "[verify] contains_expected=True")

    monkeypatch.setattr(vl, "_run_inject", fake_run, raising=False)

    class M:
        session_aliases = ["ws-示例-码农-m0"]
        session_ref = {}

    out = vl.launch(M(), "hello 你好", method="wmpaste", verify=True)
    assert len(calls) == 2, "必须自动重试一次剪贴板档：%s" % calls
    assert out["ok"] is True and out["method"] == "clipboard"
    assert out.get("fallback_from") == "wmpaste"
    assert "wmpaste 不行" in out["detail"] and "剪贴板" in out["detail"], out["detail"]
    assert out["verified"] is True, "读回校验通过时要如实标 True：%s" % out


def test_missing_window_must_not_fall_back_to_fake_success(monkeypatch):
    """★两种 `rc==3` 必须分开：**"没找到窗口"不许兜底成成功**（否则就是假成功）。

    实测来源：既有守卫 `test_visual_launch.py::test_missing_window_never_reports_success`
    抓到了我改动引入的回归 —— 我把"没有文本框"和"没有窗口"混成一种，于是窗口根本不存在时
    也去退剪贴板、返回 ok=True。这条把两者**钉死**。
    """
    from agent_community.platform import visual_launch as vl

    def fake_run(args, timeout=45.0):
        return 3, "[FAIL] no matching window (pass -Pid or -TitleLike; use -List to see what exists)"

    monkeypatch.setattr(vl, "_run_inject", fake_run, raising=False)

    class M:
        session_aliases = ["ws-示例-码农-m0"]
        session_ref = {}

    out = vl.launch(M(), "hi", method="wmpaste", verify=False)
    assert out["ok"] is False, "没找到窗口却报成功＝假成功：%s" % out
    assert "no matching window" in out["detail"]
    assert out.get("next_step")


# ─────────────────────────────────────────────────────────────────────────────
# 2026-10-23：老板真机报「点'试投一次'只弹几个黑框，目标没置顶也没输入，
# 界面却说已兜底送到」。以下四条把"黑框 / 假成功 / 盲投 / 编码"逐个钉住。
# ─────────────────────────────────────────────────────────────────────────────

def test_clipboard_write_failure_is_reported_not_faked(monkeypatch):
    """★核心：剪贴板写不进去（脚本 exit 5）时**绝不能报"已送到"**。

    真机实测：`Set-Clipboard` 抛 `ExternalException: Requested Clipboard operation did not succeed.`
    （剪贴板被别的程序占着），而当时脚本照常往下走、上层就报"已自动兜底送到会话" ——
    用户看到的是"什么都没发生"。现在脚本 exit 5，这里要求上层如实说。
    """
    from agent_community.platform import visual_launch as vl

    calls = []

    def fake_run(args, timeout=45.0):
        calls.append(args[args.index("-Method") + 1])
        if len(calls) == 1:
            return 3, "[FAIL] -Method wmpaste needs a text-control child; none found."
        return 5, ("[FAIL] clipboard write failed: Requested Clipboard operation did not succeed.\n"
                   "[inject] clipboard + Ctrl+V  (12 chars)")

    monkeypatch.setattr(vl, "_run_inject", fake_run, raising=False)
    # 半自动兜底（把内容放剪贴板让人自己按）也让它失败，露出真实的第 5 号出口
    monkeypatch.setattr(vl, "_clipboard", lambda member, text: {
        "ok": False, "method": "clipboard", "need_human": True,
        "detail": "剪贴板写入失败：Requested Clipboard operation did not succeed."},
        raising=True)

    class M:
        session_aliases = ["ws-示例-m0"]
        session_ref = {}

    out = vl.launch(M(), "hello", method="wmpaste", verify=False)
    assert calls == ["wmpaste", "clipboard"], calls
    assert out["ok"] is False, "剪贴板没写进去却报成功＝假成功：%s" % out
    assert "剪贴板" in out["detail"], out["detail"]
    assert "已自动兜底送到" not in out.get("next_step", ""), out["next_step"]


def test_not_foreground_refuses_to_inject_blind(monkeypatch):
    """窗口没置顶（脚本 exit 6）⇒ **不投递**，并告诉用户"先点一下目标窗口"。

    为什么必须拒：`clipboard` / `sendkeys` 是把按键送到**当前前台窗口**；
    目标没置顶时按 Ctrl+V，内容会贴进**前台那个别的程序**里去。
    """
    from agent_community.platform import visual_launch as vl

    def fake_run(args, timeout=45.0):
        if args[args.index("-Method") + 1] == "wmpaste":
            return 3, "[FAIL] -Method wmpaste needs a text-control child; none found."
        return 6, ("[focus] SetForegroundWindow=False is_foreground=False\n"
                   "[FAIL] target window is not in the foreground -> refuse to inject keystrokes.")

    monkeypatch.setattr(vl, "_run_inject", fake_run, raising=False)

    class M:
        session_aliases = ["ws-示例-m0"]
        session_ref = {}

    out = vl.launch(M(), "hello", method="wmpaste", verify=False)
    assert out["ok"] is False, "没置顶还报成功＝把内容贴到别的窗口去了：%s" % out
    assert out.get("need_human") is True
    assert "没有投递" in out["detail"], out["detail"]
    assert "点一下" in out["next_step"], out["next_step"]


def test_clipboard_evidence_is_required_before_claiming_sent(monkeypatch):
    """即使 exit 0，只要**没有剪贴板写入证据**，也不许说"送出去了"。"""
    from agent_community.platform import visual_launch as vl

    def fake_run(args, timeout=45.0):
        if args[args.index("-Method") + 1] == "wmpaste":
            return 3, "[FAIL] -Method wmpaste needs a text-control child; none found."
        # 只有"[inject] …"、没有"[clipboard] wrote=" ⇒ 不足以证明内容进了剪贴板
        return 0, "[inject] clipboard + Ctrl+V  (12 chars)"

    monkeypatch.setattr(vl, "_run_inject", fake_run, raising=False)
    monkeypatch.setattr(vl, "_clipboard", lambda member, text: {
        "ok": True, "method": "clipboard", "need_human": True,
        "detail": "已把 5 字放进剪贴板（半自动档）"}, raising=True)

    class M:
        session_aliases = ["ws-示例-m0"]
        session_ref = {}

    out = vl.launch(M(), "hello", method="wmpaste", verify=False)
    # 没有写入证据 ⇒ 走"退回半自动"那条路（内容在剪贴板里，人自己按 Ctrl+V）
    assert out.get("fallback_from") == "wmpaste→clipboard", out
    assert out.get("need_human") is True
    assert "没送进去" in out["detail"] or "半自动" in out["detail"], out["detail"]


def test_unverified_success_never_claims_delivered(monkeypatch):
    """读不回（目标没有可读文本框，Electron 常态）时：

    ① `verified` 必须是 `None`（不许冒充"已验证"）；
    ② 文案**不许**写"已送达/已送到"；
    ③ 必须**如实说明没核实**，并把"人该干什么"写清（内容已在剪贴板 ⇒ 点输入框再 Ctrl+V）。

    ★2026-10-23 老板真机实测（Trae）：剪贴板写了、窗口置顶了、Ctrl+V 按了，**输入框仍然空**
    —— 原因是 Electron 内部焦点不在输入框，平台拿不到它的输入元素。所以这里除了"不许谎报"，
    还要求 `need_human=True` 与可执行动作。
    """
    from agent_community.platform import visual_launch as vl

    def fake_run(args, timeout=45.0):
        if args[args.index("-Method") + 1] == "wmpaste":
            return 3, "[FAIL] -Method wmpaste needs a text-control child; none found."
        return 0, ("[focus] SetForegroundWindow=True is_foreground=True\n"
                   "[clipboard] wrote=5 readback=ok\n"
                   "[inject] clipboard + Ctrl+V  (5 chars)\n"
                   "[inject] Enter\n"
                   "[verify] no text-control child found among 0 child window(s).")

    monkeypatch.setattr(vl, "_run_inject", fake_run, raising=False)

    class M:
        session_aliases = ["ws-示例-m0"]
        session_ref = {}

    out = vl.launch(M(), "hello", method="wmpaste", verify=True)
    assert out["ok"] is True and out["verified"] is None, out
    assert "已送达" not in out["next_step"] and "已送到" not in out["next_step"], out["next_step"]
    assert "没核实" in out["detail"] or "没有读回核对" in out["detail"], out["detail"]
    assert out.get("need_human") is True, "读不回却要人不管＝把'没送到'当'送到了'：%s" % out
    assert "剪贴板" in out["next_step"] and "输入框" in out["next_step"], out["next_step"]


def test_focus_only_reports_true_when_window_raised(monkeypatch):
    """`focus()`：脚本报 `[focus-only] ok=True` ⇒ ok=True（**只置顶，不投递任何输入**）。"""
    from agent_community.platform import visual_launch as vl

    monkeypatch.setattr(vl, "_run_inject", lambda args, timeout=45.0: (
        0,
        "[focus] SetForegroundWindow=True is_foreground=True\n"
        "[focus-only] ok=True  is_foreground=True  （已在最上层，未投递任何输入）"), raising=False)
    r = vl.focus("ws-示例-m0")
    assert r["ok"] is True and r["foreground"] is True, r
    assert "未投递" in r["detail"], r["detail"]


def test_focus_only_surfaces_refusal_with_action(monkeypatch):
    """置顶失败（脚本 exit 7）⇒ ok=False，且下一步**可执行**（点一下 / 用管理员重跑）。"""
    from agent_community.platform import visual_launch as vl

    monkeypatch.setattr(vl, "_run_inject", lambda args, timeout=45.0: (
        7,
        "[focus] SetForegroundWindow=False is_foreground=False\n"
        "[focus-only] ok=False is_foreground=False （没能置顶，未投递任何输入）"), raising=False)
    r = vl.focus("ws-示例-m0")
    assert r["ok"] is False and r["foreground"] is False, r
    assert "点一下" in r["next_step"] and "管理员" in r["next_step"], r["next_step"]
