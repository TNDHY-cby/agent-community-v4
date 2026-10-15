# -*- coding: utf-8 -*-
"""`platform/visual_launch`：**视觉拉起**必须"能送进去"且**失败要说话**（V51 §4）。

背景（2026-10-15 核实）：步骤 9「更新视觉脚本」一直只做到"名字进数据" ——
`tools/inject_window.ps1` 早就在仓库里、也实测可靠，但**平台代码从没调用过它**。
本文件守的就是"这一环真的接上了，而且不许假装成功"。

判据（全部按**真结果**，不看源码文本）：
  ① 没命名 ⇒ 如实说"还没有会话名"，**不做任何注入尝试**；
  ② 非 Windows 上 `wmpaste` ⇒ **如实返回不支持**，且**不许偷偷去动剪贴板**；
  ③ `clipboard` 兜底 ⇒ **真写进系统剪贴板**（能读回来就比对；读不回来也必须说清装什么）；
  ④ 窗口不匹配（脚本 `[FAIL]` + exit 3）⇒ **绝不许返回成功**；
  ⑤ `auto` 在 wmpaste 失败时**退兜底档**，并在 detail 里说清"为什么退的"。
"""
from __future__ import annotations

import os
import subprocess
import sys

import pytest

_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
if _ROOT not in sys.path:
    sys.path.append(_ROOT)

from agent_community.platform import visual_launch as vl  # noqa: E402


class _Member:
    """最小成员替身：只带视觉拉起关心的两个字段（与 `WorkshopMember` 同形）。"""

    def __init__(self, aliases=None, session_ref=None):
        self.member_id = "m0"
        self.display_name = "示例Harness-A"
        self.role = "组员"
        self.session_aliases = list(aliases or [])
        self.session_ref = dict(session_ref or {})


def _read_clipboard() -> str | None:
    """读回系统剪贴板（Windows 才读得到；其它平台返回 None ⇒ 该用例退化为"只看它有没有说话"）。

    ⚠️**读的时候必须逼 PowerShell 吐 UTF-8**（`[Console]::OutputEncoding`）：本机控制台是 cp936，
    直接用 utf-8 解它的输出会把**正常的中文**看成乱码 —— 那就分不清"剪贴板真的坏了"还是"我读坏了"
    （本文件第一次跑就是这么误判的）。
    """
    if os.name != "nt":
        return None
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-Command",
                            "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; Get-Clipboard -Raw"],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=30)
        return r.stdout if r.returncode == 0 else None
    except Exception:  # noqa: BLE001
        return None


# ── ① 没命名：如实说，且什么都不做 ───────────────────────────────────────────
def test_unnamed_member_is_honest_and_does_nothing():
    m = _Member()
    opt = vl.options(m, probe=False)
    assert opt["named"] is False
    assert any("还没有会话名" in n for n in opt["notes"]), opt
    res = vl.launch(m, "hello")
    assert res["ok"] is False
    assert "会话名" in res["detail"]
    assert res["next_step"], "失败必须给出下一步（失败要说话）"


def test_session_name_falls_back_to_session_ref():
    m = _Member(session_ref={"session_name": "ws-x-项目-组员-m0"})
    assert vl.session_name_of(m) == "ws-x-项目-组员-m0"


def test_session_name_prefers_first_alias():
    m = _Member(aliases=["平台生成名", "用户加的别名"],
                session_ref={"session_name": "旧的"})
    assert vl.session_name_of(m) == "平台生成名", "身份名必须取 aliases[0]（V25 §4.1）"


# ── ② 非 Windows：如实不支持，且不偷偷动剪贴板 ───────────────────────────────
def test_wmpaste_is_honest_on_non_windows(monkeypatch):
    m = _Member(aliases=["ws-1-项目-组员-m0"])
    monkeypatch.setattr(vl, "_IS_WINDOWS", False)
    called = {"clip": 0}
    monkeypatch.setattr(vl, "_clipboard", lambda *_a, **_k: called.__setitem__("clip", called["clip"] + 1))
    res = vl.launch(m, "hi", method="wmpaste")
    assert res["ok"] is False
    assert "非 Windows" in res["detail"], res
    assert called["clip"] == 0, "明确要 wmpaste 时不许偷偷改走剪贴板（那会动用户剪贴板）"
    opt = vl.options(m, probe=False)
    wm = next(x for x in opt["methods"] if x["method"] == "wmpaste")
    assert wm["available"] is False and "不支持" in wm["detail"]


# ── ③ clipboard 兜底：真写；写不了就说清装什么 ────────────────────────────────
def test_clipboard_fallback_really_writes_or_says_what_to_install():
    m = _Member(aliases=["ws-1-项目-组员-m0"])
    text = "V51-clipboard-probe-中文标记"
    res = vl.launch(m, text, method="clipboard")
    back = _read_clipboard()
    if res["ok"]:
        assert res["need_human"] is True, "兜底档必须说清需要人点一下（V25 §5 档 f）"
        assert res["next_step"]
        if back is not None:
            assert text in back, "说写进剪贴板了，读回来却不是它：%r" % back[:120]
    else:
        assert "剪贴板" in res["detail"], res
        assert "xclip" in res["next_step"] or "剪贴板" in res["next_step"], res


# ── ④ 窗口不匹配：绝不返回成功；⑤ auto 要退兜底并说明 ─────────────────────────
def test_missing_window_never_reports_success(monkeypatch):
    m = _Member(aliases=["ws-1-项目-组员-m0"])
    monkeypatch.setattr(vl, "_IS_WINDOWS", True)
    monkeypatch.setattr(vl, "_run_inject",
                        lambda *a, **k: (3, "[FAIL] no matching window (pass -Pid or -TitleLike; use -List to see what exists)"))
    res = vl.launch(m, "hi", method="wmpaste")
    assert res["ok"] is False, "脚本明确报 [FAIL]，平台绝不能返回成功"
    assert "no matching window" in res["detail"]
    assert res["next_step"]


def test_auto_falls_back_to_clipboard_and_says_why(monkeypatch):
    m = _Member(aliases=["ws-1-项目-组员-m0"])
    monkeypatch.setattr(vl, "_IS_WINDOWS", True)
    monkeypatch.setattr(vl, "_run_inject", lambda *a, **k: (3, "[FAIL] no matching window"))
    monkeypatch.setattr(vl, "_clipboard",
                        lambda *_a, **_k: {"ok": True, "method": "clipboard", "need_human": True,
                                           "detail": "已放进剪贴板", "next_step": "点一下会话再 Ctrl+V"})
    res = vl.launch(m, "hi", method="auto")
    assert res["method"] == "clipboard" and res["ok"] is True
    assert "wmpaste 不行" in res["detail"] and "no matching window" in res["detail"], \
        "退兜底时要说清「为什么退」（否则用户以为一直走的剪贴板）"


def test_inject_success_is_reported_with_evidence(monkeypatch):
    """成功路径：脚本输出 `[inject] WM_PASTE …` ⇒ 才允许报 ok=True，并把证据带回来。"""
    m = _Member(aliases=["ws-1-项目-组员-m0"])
    monkeypatch.setattr(vl, "_IS_WINDOWS", True)
    monkeypatch.setattr(vl, "_run_inject",
                        lambda *a, **k: (0, "[target] hwnd=1 pid=2 class=Edit title=X\n"
                                            "[inject] WM_PASTE -> class=Edit  (42 chars)\n[inject] Enter"))
    res = vl.launch(m, "hi", method="wmpaste")
    assert res["ok"] is True
    assert "WM_PASTE" in res["detail"]


# ── 只读性：options 不许改成员、不许有副作用 ─────────────────────────────────
def test_options_is_side_effect_free(monkeypatch):
    m = _Member(aliases=["ws-1-项目-组员-m0"])
    before = (list(m.session_aliases), dict(m.session_ref))
    monkeypatch.setattr(vl, "_IS_WINDOWS", False)          # 不真去列窗口
    a1 = vl.options(m, probe=False)
    a2 = vl.options(m, probe=False)
    assert a1 == a2, "同一输入两次结果不一致 ⇒ 有隐藏状态"
    assert (list(m.session_aliases), dict(m.session_ref)) == before, "options() 改了成员数据"


def test_bad_method_is_rejected_with_reason():
    m = _Member(aliases=["ws-1-项目-组员-m0"])
    res = vl.launch(m, "hi", method="ocr-magic")
    assert res["ok"] is False and "未知的拉起方式" in res["detail"]
    assert "auto" in res["next_step"]


# ── `verify=True`：把"我们发出去了"升级成"读回来核对过" ───────────────────────
def test_verify_flag_is_actually_passed_and_pass_is_reported(monkeypatch):
    m = _Member(aliases=["ws-1-项目-组员-m0"])
    monkeypatch.setattr(vl, "_IS_WINDOWS", True)
    seen = {}

    def fake(args, timeout=45.0):
        seen["args"] = list(args)
        return (0, "[inject] WM_PASTE -> class=Edit  (2 chars)\n"
                   "[verify] control class=Edit text length=2 contains_expected=True")

    monkeypatch.setattr(vl, "_run_inject", fake)
    res = vl.launch(m, "hi", method="wmpaste", verify=True)
    assert "-Verify" in seen["args"], "verify=True 却没把 -Verify 传给脚本（等于没校验）"
    assert res["ok"] is True and res.get("verified") is True, res


def test_verify_is_on_by_default(monkeypatch):
    """★默认就该读回核对（2026-10-15 实测：脚本说 `[inject] WM_PASTE (15 chars)`，
    受控靶子自报文本框 `len=0` —— **根本没落进去**。默认不核对就会报假成功）。"""
    m = _Member(aliases=["ws-1-项目-组员-m0"])
    monkeypatch.setattr(vl, "_IS_WINDOWS", True)
    seen = {}
    monkeypatch.setattr(vl, "_run_inject",
                        lambda args, timeout=45.0: (seen.__setitem__("args", list(args)) or
                                                    (0, "[inject] WM_PASTE -> class=Edit  (2 chars)\n"
                                                        "[verify] control class=Edit text length=2 contains_expected=True")))
    res = vl.launch(m, "hi", method="wmpaste")
    assert "-Verify" in seen["args"], "默认没带 -Verify（那就会拿「我们送了」当「送到了」）"
    assert res["verified"] is True


def test_when_not_verifying_it_says_so_instead_of_implying_success(monkeypatch):
    """明确不核对时：`verified=None`，且 detail **必须明说"这只是送出了输入"**（不许让人以为已验证）。"""
    m = _Member(aliases=["ws-1-项目-组员-m0"])
    monkeypatch.setattr(vl, "_IS_WINDOWS", True)
    monkeypatch.setattr(vl, "_run_inject",
                        lambda *a, **k: (0, "[inject] WM_PASTE -> class=Edit  (2 chars)\n[inject] Enter"))
    res = vl.launch(m, "hi", method="wmpaste", verify=False)
    assert res["ok"] is True and res["verified"] is None
    assert "没有读回核对" in res["detail"], res["detail"]


# ── `-List` 的解析：**长类名那一列只剩一个空格**（2026-10-15 真踩到的 bug）────────
def test_list_line_parses_even_when_class_overflows_its_column():
    """★真事故：`-List` 用 `{2,-28}` 对齐，而 WinForms/Electron 的类名**超过 28 字符** ⇒
    列间只剩**一个**空格。原来按"两个空格"切分 ⇒ `class` 把 ` title=` 一起吃掉、
    **title 解析成 None** ⇒ `options()` 永远报"0 个候选窗口"（界面显示没有可注入的窗口，实际有）。

    本用例用的就是**当时那一行原文**。
    """
    line = ("  hwnd=1969346      pid=12704   "
            "class=WindowsForms10.Window.8.app.0.8b7966_r8_ad1 title=ws-parse-项目-组员-m0")
    it = vl._parse_list_line(line)
    assert it.get("hwnd") == "1969346"
    assert it.get("pid") == "12704"
    assert it.get("class") == "WindowsForms10.Window.8.app.0.8b7966_r8_ad1", it
    assert it.get("title") == "ws-parse-项目-组员-m0", "长类名把 title 吃掉了：%r" % it


def test_list_windows_filters_by_title_and_never_reports_zero_when_a_match_exists(monkeypatch):
    """过滤要按**解析出来的 title** 判，且命中时不许报 0。"""
    real = ("visible top-level windows: 2\n"
            "  hwnd=263678       pid=8840    class=Qt5152QWindowToolSaveBits    title=Marvis\n"
            "  hwnd=1903810      pid=36208   "
            "class=WindowsForms10.Window.8.app.0.8b7966_r8_ad1 title=ws-raw-项目-组员-m0\n")
    monkeypatch.setattr(vl, "_run_inject", lambda *a, **k: (0, real))
    got = vl.list_windows("ws-raw-项目-组员-m0")
    assert got["ok"] is True
    assert len(got["windows"]) == 1, got
    assert got["windows"][0]["title"] == "ws-raw-项目-组员-m0"
    assert vl.list_windows("不存在的会话名")["windows"] == []


def test_verify_mismatch_is_a_failure_not_a_success(monkeypatch):
    """★核心判据：脚本 `exit 4`（读回不匹配）⇒ **绝不能报成功**。

    这是"失败要说话"的最硬一条：注入 API 返回了、脚本也打了 `[inject] …`，
    但**文本根本没进到那个控件**。若这里报成功，用户会以为 agent 被叫醒了。
    """
    m = _Member(aliases=["ws-1-项目-组员-m0"])
    monkeypatch.setattr(vl, "_IS_WINDOWS", True)
    monkeypatch.setattr(vl, "_run_inject",
                        lambda *a, **k: (4, "[inject] WM_PASTE -> class=Edit  (2 chars)\n"
                                            "[verify] control class=Edit text length=0 contains_expected=False"))
    res = vl.launch(m, "hi", method="wmpaste", verify=True)
    assert res["ok"] is False and res.get("verified") is False
    assert "读回校验失败" in res["detail"]
    assert res["next_step"], "失败必须给出下一步"


def test_verify_without_text_control_says_it_cannot_self_prove(monkeypatch):
    """没有标准文本框（Electron/Qt 常态）⇒ **如实说"没法自证"**，不当成失败、也不假装成功。"""
    m = _Member(aliases=["ws-1-项目-组员-m0"])
    monkeypatch.setattr(vl, "_IS_WINDOWS", True)
    monkeypatch.setattr(vl, "_run_inject",
                        lambda *a, **k: (0, "[inject] WM_PASTE -> class=Chrome_WidgetWin_1  (2 chars)\n"
                                            "[verify] no text-control child found among 7 child window(s)."))
    res = vl.launch(m, "hi", method="wmpaste", verify=True)
    assert res["ok"] is True, "送进去了但读不回来，不该当成失败"
    assert res.get("verified") is None, "读不回来就必须说「没能自证」，不能标成 True"
    assert "no text-control child" in res["detail"]
