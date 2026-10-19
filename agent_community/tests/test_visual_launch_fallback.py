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
