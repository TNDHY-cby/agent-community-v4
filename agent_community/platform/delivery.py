# -*- coding: utf-8 -*-
"""**统一投递层**：同一个目标，按"能用哪条通道"自动挑一条，并把结果如实标出来。

老板 2026-10-23 追问「只能浏览器吗」——**不是**。浏览器只是其中一条通道。真实世界的目标分四类，
每条通道的**覆盖与代价**都不一样（这也是为什么不写"万能投递"）：

| 目标类型 | 首选通道 | 需要什么 | 能核实吗 |
|---|---|---|---|
| Chromium / Electron 内核的应用（含各种编辑器、浏览器、IDE） | **cdp**（`Input.insertText` / 合成 paste / Ctrl+V） | 目标自己开 `--remote-debugging-port` | **能**（读 `innerText`/`value`） |
| Win32 / WinForms / 老应用（有原生文本框控件） | **native**（`WM_PASTE` + `WM_GETTEXT` 读回） | 无（进程在同会话即可） | **能**（WM_GETTEXT 读回） |
| 暴露 UI Automation 的应用（系统记事本、办公套件…） | **uia**（`ValuePattern.SetValue` / `TextPattern`） | Windows + 应用暴露 UIA | **能**（UIA 读回） |
| 其它任何窗口（Electron 未开端口、画布渲染、游戏…） | **clipboard**（半自动） | 无 | **不能**（人按 Ctrl+V；我们如实说未核实） |

> ⚠️ **反私人定制**：本模块**只认能力，不认产品名** —— 任何具体应用都只是"Electron 类 / Win32 类"的一个实例，
> 代码里出现产品名就是私人定制（守卫 `tests/test_delivery_harness.py` 会红）。

判据：`tools/delivery_check.py`（真窗口真投递）＋ `tests/test_delivery.py`、`tests/test_delivery_harness.py`。
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
UIA_PS1 = PROJECT_ROOT / "agent_community" / "tools" / "uia_set.ps1"

#: 通道优先级（"能核实"的排前面；都不可用时退半自动）
ORDER = ("cdp", "native", "uia", "clipboard")


def _hidden():
    from .spawn import spawn_kwargs

    return spawn_kwargs(detach=False, no_window=True)


def _run_uia(*args, timeout: float = 60.0) -> tuple:
    """跑 `uia_set.ps1`，返回 `(rc, 输出)`。非 Windows / 没脚本 ⇒ `(127, 原因)`。"""
    if os.name != "nt":
        return 127, "非 Windows：UI Automation 只在 Windows 上存在"
    if not UIA_PS1.is_file():
        return 127, "找不到 uia_set.ps1（它随仓库发布）"
    argv = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(UIA_PS1)] + list(args)
    try:
        r = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=timeout, **_hidden())
        return r.returncode, ((r.stdout or "") + (r.stderr or "")).strip()
    except subprocess.TimeoutExpired:
        return 124, "UI Automation 超时（%ss）—— 目标可能在弹模态框" % int(timeout)
    except Exception as e:                                   # noqa: BLE001
        return 127, "UI Automation 没跑起来：%s: %s" % (type(e).__name__, e)


def _uia_usable(hwnd: int) -> tuple:
    """UIA 能否拿到可编辑元素：`(ok, 说明)`（只读查询，不写入）。"""
    if not hwnd:
        return False, "没有窗口句柄，UIA 无从下手"
    rc, out = _run_uia("-Hwnd", str(int(hwnd)), "-List", timeout=45)
    if rc != 0:
        line = next((ln for ln in out.splitlines() if ln.startswith("[uia]")), out[-120:])
        return False, line
    first = next((ln for ln in out.splitlines() if ln.strip().startswith("[uia] #")), "")
    return bool(first), (first or "UIA 找不到可编辑元素")


def hwnd_pid(hwnd: int) -> int:
    """窗口所属进程 id（拿不到返回 0）。"""
    if os.name != "nt":
        return 0
    import ctypes
    from ctypes import wintypes

    pid = wintypes.DWORD(0)
    ctypes.WinDLL("user32", use_last_error=True).GetWindowThreadProcessId(
        wintypes.HWND(int(hwnd)), ctypes.byref(pid))
    return int(pid.value)


def channels(hwnd: int | None = None, *, ws: str = "", title: str = "") -> dict:
    """列出**这个目标**当前能用哪些通道、以及各自的理由（给界面显示，别让人猜）。

    返回 `{ok, hwnd, title, channels:[{channel, available, why}], recommended}`。
    """
    hwnd = int(hwnd or 0)
    out = []
    # ① CDP：需要页面的 ws 地址（从 /api/docking/web/probe 来）
    out.append({"channel": "cdp", "available": bool(ws),
                "why": "已指定页面 ws 地址" if ws else "没指定调试页面（目标要先开 --remote-debugging-port）"})
    # ② native：靠"窗口里有没有原生文本框子控件"，投递时用 WM_PASTE + WM_GETTEXT 读回
    if hwnd:
        from . import input_sim as _ins  # 复用平台分支判断（不重复写 os.name 逻辑）

        rect = _ins.window_rect(hwnd) or {}
        out.append({"channel": "native", "available": bool(rect),
                    "why": ("窗口 %sx%s；投递时用 WM_PASTE，能读回就算核实"
                            % (rect.get("width"), rect.get("height"))) if rect else "拿不到窗口矩形"})
    else:
        out.append({"channel": "native", "available": False, "why": "没有窗口句柄（只有浏览器目标时用 cdp）"})
    # ③ UIA：只读探一下有没有可编辑元素
    if hwnd:
        ok, why = _uia_usable(hwnd)
        out.append({"channel": "uia", "available": ok, "why": why})
    else:
        out.append({"channel": "uia", "available": False, "why": "没有窗口句柄"})
    # ④ clipboard：永远"可用"，但**不能核实**
    out.append({"channel": "clipboard", "available": True,
                "why": "把内容放进剪贴板，人点一下目标再 Ctrl+V（半自动，**不能核实**）"})
    rec = next((c["channel"] for c in out if c["available"] and c["channel"] != "clipboard"), "clipboard")
    return {"ok": True, "hwnd": hwnd, "title": title, "channels": out, "recommended": rec}


# ─────────────────────────────────────────────────────────────────────────────
# 状态口径（**给界面看的唯一字段**）：state
#   2026-10-23 老板真机看到自相矛盾：「✗ 没有投递」下面却写着「已改用剪贴板 + Ctrl+V」。
#   根因不是谁写错字，而是返回里**没有单一字段**能表达到底到哪一步：
#   ok=True 可能是真送达，也可能是只按了键/只放进剪贴板。
#   ⇒ 以后界面**只看 state**（ok/verified/need_human 保留兼容，但不再用来渲染）：
#     delivered_verified  读回核对通过（真送达）
#     sent_unverified     我们确实送出/按了键，但没法核实（Electron 常态）
#     queued_for_human    只把内容放进了剪贴板，**还没送到**（要人按 Ctrl+V）
#     not_sent            什么都没成（或目标没找到）
# ─────────────────────────────────────────────────────────────────────────────
STATES = ("delivered_verified", "sent_unverified", "queued_for_human", "not_sent")


#: 目标看不看得见输入框的说明（**给用户的解释**，2026-10-23 老板真机问的那句）
WHY_NO_INPUT_BOX = (
    "这个窗口的输入框是应用自己画的（Electron/浏览器类常见），**系统层看不到它** —— "
    "不是它没有输入框，而是平台拿不到那个控件。要全自动投递：让目标带 "
    "--remote-debugging-port=9222 启动（走 CDP）；否则用「手动绑定目标 + 剪贴板半自动档」："
    "平台把内容放进剪贴板，你点一下输入框按 Ctrl+V。"
)


def text_control_of(detail: str = "", attempts=None) -> dict:
    """判断"系统层有没有看到可读的输入框"，并给一句人话解释。

    为什么单独抽：老板真机看到"成功拉起 + 没有检测到输入框"，会以为产品漏了输入框；
    实际情况是 Electron 的输入框由渲染进程绘制，系统层（子窗口 / WM_GETTEXT）看不到。
    界面应显示 	ext_control.found 与 	ext_control.why，**不要自己从 detail 里猜**。

    解析三种来源（都在注入脚本的输出里）：
    - [verify] control class=Edit … ⇒ found=True，带 class；
    - [inject] WM_PASTE -> class=Edit … ⇒ found=True，带 class；
    - 
o text-control child / 
o-editable-element / 	ext-control ⇒ found=False + 解释。
    """
    lines = [str(detail or "")]
    for a in (attempts or []):
        lines.append(str((a or {}).get("why") or ""))
    blob = "\n".join(x for x in lines if x)
    low = blob.lower()

    # 注入脚本里两种说法都要认：
    #   ① [verify] no text-control child found …（读回阶段）
    #   ② [FAIL] -Method wmpaste needs a text-control child; none found.（投递阶段）
    if ("no text-control child" in low or "needs a text-control child" in low
            or "no-editable-element" in low):
        return {"found": False, "why": WHY_NO_INPUT_BOX}
    m = re.search(r"control class=([^\s]+)", blob)
    if m:
        return {"found": True, "class": m.group(1).strip(), "why": ""}
    m2 = re.search(r"\[inject\][^\n]*?class=([^\s]+)", blob)
    if m2:
        return {"found": True, "class": m2.group(1).strip(), "why": ""}
    return {"found": None, "why": ""}


def state_of(res: dict) -> str:
    """把一次投递结果归一成 state（**一处真相**，别在各端点自己推）。"""
    r = res or {}
    if r.get("verified") is True:
        return "delivered_verified"
    if r.get("queued_only"):
        return "queued_for_human"
    if r.get("ok") and r.get("verified") is None:
        return "sent_unverified"
    return "not_sent"


def deliver(text: str, *, hwnd: int = 0, title: str = "", ws: str = "", idx: int = 0,
            prefer: str = "", press_enter: bool = False, anchor: dict | None = None,
            send_rel: dict | None = None) -> dict:
    """四通道投递（对外入口）：结果里**一定带 state**（界面只看这个，别自己推）。

    ⚠️ 2026-10-24 真机修：这里原来有一句 `if "_extra" in locals(): res.update(_extra)` ——
    `_extra` 是 `_deliver_core` 的局部变量，**在这是永远不存在的名字**，于是
    「press_enter 必须说话」（`press_ok/press_how/press_note`）这条定稿口径**从来没生效过**：
    真机上带 `enter:true` 投递，返回里根本没有 press_* 字段。
    现在由 `_deliver_core` 自己把 `_press` 并进返回体（一处真相，别再靠外部拼）。
    """
    res = _deliver_core(text, hwnd=hwnd, title=title, ws=ws, idx=idx, prefer=prefer,
                        press_enter=press_enter, anchor=anchor, send_rel=send_rel)
    res["state"] = state_of(res)
    res["text_control"] = text_control_of(res.get("detail") or "", res.get("attempts"))
    return res


def _do_send_action(hwnd: int, send_rel: dict | None, title: str = "") -> dict:
    """投递成功后的"发送"动作：优先点**框好的发送按钮**，否则按回车。返回如实说明。"""
    from . import input_sim

    if send_rel and hwnd:
        ck = input_sim.click_in_window(int(hwnd), float(send_rel.get("rel_x") or 0.5),
                                       float(send_rel.get("rel_y") or 0.5))
        if ck.get("ok"):
            return {"ok": True, "how": "click-send",
                    "note": "点了框好的发送按钮（相对位置 %.3f,%.3f）"
                            % (float(send_rel.get("rel_x") or 0), float(send_rel.get("rel_y") or 0))}
        return {"ok": False, "how": "click-send",
                "note": "点发送按钮失败：%s" % (ck.get("why") or "点不到")}
    k = input_sim.send_keys("enter")
    return {"ok": bool(k.get("ok")), "how": "enter",
            "note": "按了回车" if k.get("ok") else "按回车失败：%s" % (k.get("why") or "")}


def _deliver_core(text: str, *, hwnd: int = 0, title: str = "", ws: str = "", idx: int = 0,
            prefer: str = "", press_enter: bool = False, anchor: dict | None = None,
            send_rel: dict | None = None) -> dict:
    """按优先级投递：`cdp → native → uia → clipboard`，每步都如实记录尝试结果。

    返回 `{ok, channel, verified, attempts:[…], detail, next_step}`；
    `verified` 三态：`True` 读回核对通过 / `False` 读回不一致 / `None` 没核实的条件。
    """
    text = str(text or "")
    if not text:
        return {"ok": False, "channel": "", "verified": None, "attempts": [],
                "detail": "要投的文本是空的", "next_step": "传入 text"}
    plan = (prefer,) if prefer else ORDER
    attempts = []

    # ★2026-10-23（老板实测第二轮）：Electron/浏览器里"窗口在前台 ≠ 焦点在输入框"，
    #   剪贴板档要按 Ctrl+V，所以**先点一下教过的输入框位置**再走。点不到 ⇒ 如实失败、不盲投。
    click_note = ""
    clicked = False
    if anchor and anchor.get("rel_x") is not None:
        try:
            from . import input_sim, visual_launch as _vl

            if title:
                try:
                    _vl.focus(title)
                except Exception:                             # noqa: BLE001 — 抬不起来也照常往下走
                    pass
            ck = input_sim.click_in_window(int(hwnd or 0), float(anchor["rel_x"]),
                                           float(anchor["rel_y"])) if hwnd else {}
            if ck.get("ok"):
                clicked = True
                click_note = "已先点一下教过的输入框位置(%.3f,%.3f)" % (
                    float(anchor["rel_x"]), float(anchor["rel_y"]))
                # 点了也要留痕（原来这句只赋给一个没人用的变量 ⇒ 外面看不到"点过没有"）
                attempts.append({"channel": "click", "ok": True, "why": click_note})
            else:
                why = ck.get("why") or "点不到目标窗口"
                attempts.append({"channel": "click", "ok": False, "why": why})
                return {"ok": False, "channel": "", "verified": False, "attempts": attempts,
                        "detail": "先点输入框失败：%s" % why,
                        "next_step": "把目标窗口恢复/切到最前（别被挡）再送；或重新「教一次位置」",
                        "state": "not_sent"}
        except Exception as e:                                # noqa: BLE001 — 点击出错不该炸掉整条路
            print("[delivery] 点击教过的位置出错（按未点击继续）：%s: %s" % (type(e).__name__, e),
                  flush=True)

    for ch in plan:
        if ch == "cdp":
            if not ws:
                attempts.append({"channel": "cdp", "ok": False, "why": "没有页面 ws 地址"})
                continue
            from . import cdp_input

            try:
                res = cdp_input.run(cdp_input.deliver(ws, text, idx=idx, mode="auto"))
            except Exception as e:                           # noqa: BLE001
                attempts.append({"channel": "cdp", "ok": False, "why": "%s: %s" % (type(e).__name__, e)})
                continue
            attempts.append({"channel": "cdp", "ok": bool(res.get("ok")),
                             "why": res.get("detail", "")[:160], "mode": res.get("mode")})
            if res.get("ok"):
                _press = {}
                if press_enter:
                    _send = _do_send_action(int(hwnd or 0), send_rel, title)
                    _press = {"press_note": _send["note"], "press_ok": _send["ok"],
                              "press_how": _send["how"]}
                return {"ok": True, "channel": "cdp", "verified": res.get("verified", True),
                        "attempts": attempts, "detail": res.get("detail", ""),
                        "readback": res.get("readback", ""), "next_step": "", **_press}
        elif ch == "native":
            if not hwnd:
                attempts.append({"channel": "native", "ok": False, "why": "没有窗口句柄"})
                continue
            from . import visual_launch as vl

            # 用**句柄**定位（实测：-Pid 对我们自己起的 WinForms 窗口匹配不到；
            # 句柄是平台侧本来就有的事实，最不会认错窗口）
            rc, out = vl._run_inject(["-Hwnd", str(int(hwnd)), "-Text", text,
                                      "-Method", "wmpaste", "-Verify"])
            if rc == 3 and "text-control" in out:
                attempts.append({"channel": "native", "ok": False,
                                 "why": "这个窗口没有原生文本框（Electron/画布类常见）"})
                continue
            ok = rc == 0 and "contains_expected=True" in out
            # 失败**必须有原因**：脚本没跑起来时输出里没有 [verify]/[FAIL] 行，
            # 那就拿输出尾部兜底（实测：这一格原来会留下空 why，等于失败不说话）。
            why = next((ln for ln in out.splitlines()
                        if ln.startswith(("[verify]", "[FAIL]"))), "")
            if not why.strip():
                why = out.strip() or ("注入脚本没有输出（rc=%s）" % rc)
            attempts.append({"channel": "native", "ok": ok, "why": why[:160]})
            if ok:
                _press = {}
                if press_enter:
                    _send = _do_send_action(int(hwnd or 0), send_rel, title)
                    _press = {"press_note": _send["note"], "press_ok": _send["ok"],
                              "press_how": _send["how"]}
                return {"ok": True, "channel": "native", "verified": True, "attempts": attempts,
                        "detail": "用 WM_PASTE 投进去，WM_GETTEXT 读回核对通过", "next_step": "",
                        **_press}
        elif ch == "uia":
            if not hwnd:
                attempts.append({"channel": "uia", "ok": False, "why": "没有窗口句柄"})
                continue
            rc, out = _run_uia("-Hwnd", str(int(hwnd)), "-Text", text)
            ok = rc == 0 and "readback=ok" in out
            why = next((ln for ln in out.splitlines() if ln.startswith("[uia]")), out[-160:])
            attempts.append({"channel": "uia", "ok": ok, "why": why[:160]})
            if ok:
                _press = {}
                if press_enter:
                    _send = _do_send_action(int(hwnd or 0), send_rel, title)
                    _press = {"press_note": _send["note"], "press_ok": _send["ok"],
                              "press_how": _send["how"]}
                return {"ok": True, "channel": "uia", "verified": True, "attempts": attempts,
                        "detail": "用 UI Automation 写入，读回核对通过", "next_step": "",
                        **_press}
        elif ch == "clipboard":
            from .wakeup_bridge import wakeup_bridge

            try:
                from . import input_sim, screen_vision as _sv

                # 半自动档的价值就在这里：**不管点没点进去，字都先放进剪贴板**
                # （用户手动点一下按 Ctrl+V 也能用），下面再决定要不要替它按。
                wakeup_bridge._set_clipboard(text)
                attempts.append({"channel": "clipboard", "ok": True, "why": "已放进剪贴板"})
                if not clicked:
                    # ★没点进输入框 ⇒ **绝不按 Ctrl+V**：那时前台可能是用户别的程序，
                    #   粘过去就把内容塞进人家正在写的东西里了（比"没送出去"糟得多）。
                    attempts.append({"channel": "paste", "ok": False,
                                     "why": "没有可靠的输入框位置，没敢按 Ctrl+V"})
                    return {"ok": True, "channel": "clipboard", "verified": None,
                            "attempts": attempts, "need_human": True, "queued_only": True,
                            "paste_ok": False, "paste_seen": None,
                            "detail": "内容已经放进剪贴板；**没有**点进输入框，所以**没有**按 Ctrl+V"
                                      "（没位置就乱粘会把内容塞进别的程序）",
                            "next_step": "先框一次输入框（或让它自己估位成功），再重试；"
                                         "也可以手动点一下输入框按 Ctrl+V"}
                # ★2026-10-24 老板定：「点击输入框（浮标出现在输入框）后直接用脚本模拟键盘 Ctrl+V」
                #   读不回文字（Electron 没有系统可见控件），所以用两个硬信号兜底：
                #   ① 点前自证（落点确实在这个窗口上，见上面 clicked）；
                #   ② 粘贴**前后**输入框那一条带的画面变化（band_diff）。
                # ⚠️ 真机踩到（2026-10-24 第一次跑，paste_seen=False）：
                #   上面那次点击发生在很早，之后还走了 native/uia 两档（各一次 PowerShell 注入，好几秒），
                #   等真到这一步时**前台已经不是目标窗口**了 ⇒ 粘贴等于没粘。
                #   ⇒ 临粘之前必须**重新抬前台 + 重新点一次**，两步都要自证，不成立就不按。
                _fg = input_sim.ensure_foreground(int(hwnd)) if hwnd else {"ok": False, "why": "没有窗口句柄"}
                if not _fg.get("ok"):
                    attempts.append({"channel": "paste", "ok": False, "why": _fg.get("why") or ""})
                    return {"ok": True, "channel": "clipboard", "verified": None,
                            "attempts": attempts, "need_human": True, "queued_only": True,
                            "paste_ok": False, "paste_seen": None,
                            "detail": "字已在剪贴板，但**没按 Ctrl+V**：%s" % (_fg.get("why") or ""),
                            "next_step": "把目标窗口显式点到最前再重试；也可以手动按 Ctrl+V"}
                _ck2 = input_sim.click_in_window(int(hwnd), float(anchor["rel_x"]),
                                                 float(anchor["rel_y"]))
                if not _ck2.get("ok"):
                    attempts.append({"channel": "paste", "ok": False,
                                     "why": _ck2.get("why") or "临粘之前那一下没点进去"})
                    return {"ok": True, "channel": "clipboard", "verified": None,
                            "attempts": attempts, "need_human": True, "queued_only": True,
                            "paste_ok": False, "paste_seen": None,
                            "detail": "字已在剪贴板，但**没按 Ctrl+V**：临粘之前那一下没点进输入框（%s）"
                                      % (_ck2.get("why") or ""),
                            "next_step": "确认目标窗口没被挡住；或手动点一下输入框按 Ctrl+V"}
                import time as _t

                _t.sleep(0.2)                                 # 让目标把焦点/光标落稳再粘
                _before = _sv.grab_gray(int(hwnd)) if hwnd else {}
                _k = input_sim.send_keys("ctrl+v")
                _paste_ok = bool(_k.get("ok"))
                _seen = None
                _ratio = None
                if _paste_ok:
                    _t.sleep(0.35)                            # 让目标把粘贴渲染出来再抓
                    _after = _sv.grab_gray(int(hwnd)) if hwnd else {}
                    dd = _sv.band_diff(_before, _after, float(anchor["rel_y"]))
                    if dd.get("ok"):
                        _seen = bool(dd.get("changed"))
                        _ratio = dd.get("ratio")
                attempts.append({"channel": "paste", "ok": _paste_ok,
                                 "why": ("已按 Ctrl+V（框内画面变化 %s）" % _ratio) if _paste_ok
                                        else "按 Ctrl+V 失败：%s" % (_k.get("why") or "")})
                if not _paste_ok:
                    return {"ok": False, "channel": "clipboard", "verified": False,
                            "attempts": attempts, "paste_ok": False, "paste_seen": None,
                            "detail": "字已经在剪贴板，但**没按成 Ctrl+V**：%s"
                                      % (_k.get("why") or "按键失败"),
                            "next_step": "确认目标窗口没被系统拦截；或手动点输入框按 Ctrl+V"}
                _press = {}
                if press_enter:
                    if _seen is True:
                        _send = _do_send_action(int(hwnd or 0), send_rel, title)
                        _press = {"press_note": _send["note"], "press_ok": _send["ok"],
                                  "press_how": _send["how"]}
                    else:
                        # 看不出粘进去 ⇒ **不按回车**：空框按了没事，但框里要是有用户自己写的半句话，
                        # 这一下就把它提前发出去了（2026-10-24 真机第一版就差点这么干）。
                        _press = {"press_ok": False, "press_how": "",
                                  "press_note": "没有按回车：看不出字进了输入框（框内画面没变/没法比对），"
                                                "按了可能把你框里已有的内容发出去"}
                _vision = ("框内画面变了（%.2f%% 像素），像是粘进去了" % (100.0 * (_ratio or 0))
                           if _seen else
                           ("框内画面没变 ⇒ 大概没粘进去" if _seen is False else "画面没法比对"))
                return {"ok": True, "channel": "clipboard", "verified": None, "attempts": attempts,
                        "paste_ok": True, "paste_seen": _seen, "paste_ratio": _ratio,
                        "detail": ("已点输入框 + 按 Ctrl+V；%s。"
                                   "**读不回文字**（这窗口没有系统可见的输入框），只能到"
                                   "「送出了、没核实」这一档" % _vision),
                        "next_step": "" if _seen else
                                     "看一眼目标输入框里有没有这句话；没有就重新框一次输入框",
                        **_press}
            except Exception as e:                           # noqa: BLE001
                attempts.append({"channel": "clipboard", "ok": False,
                                 "why": "剪贴板/粘贴这一档出错：%s: %s" % (type(e).__name__, e)})

    return {"ok": False, "channel": "", "verified": False, "attempts": attempts,
            "detail": "所有通道都没成", "next_step": "看 attempts 里每一档的原因"}


# ─────────────────────────────────────────────────────────────────────────────
# 通用入口：**给某个 harness 送一段话**
#   为什么要有这一层（老板 2026-10-23「禁止私人化…落地到通用框架」）：
#   上层（界面按钮、脚本激活、唤醒、任务提示）不该各自知道"该用哪条通道、目标是哪个窗口"。
#   它们只说"把这段话送给这个 harness"，由这里**按名字自动找目标**，找不到再说怎么补。
# ─────────────────────────────────────────────────────────────────────────────

def _session_name(harness_id: str) -> str:
    """harness 的**会话名**（视觉/浏览器匹配用的键）。没有就返回空串（由调用方如实说）。"""
    try:
        from .harness_adapter import harness_manager

        sess = harness_manager.sessions.get(str(harness_id or ""))
        if sess is None:
            return ""
        names = list(getattr(sess, "session_aliases", None) or [])
        if names:
            return str(names[0]).strip()
        ref = getattr(sess, "session_ref", None) or {}
        return str((ref or {}).get("session_name") or "").strip()
    except Exception:                                        # noqa: BLE001 — 读不到就当没有名字
        return ""


def _match_target_by_name(name: str) -> dict:
    """在开了调试端口的页面里找"标题/url 含这个名字"的那个 target（不猜：找不到就返回空）。"""
    if not name:
        return {}
    from . import cdp_input

    probe = cdp_input.probe()
    if not probe.get("ok"):
        return {}
    low = name.lower()
    cands = [t for p in probe["ports"] for t in p["targets"]
             if low in ((t.get("title") or "") + " " + (t.get("url") or "")).lower()]
    if not cands:
        return {}
    return {"ws": cands[0]["ws"], "port": None, "target": cands[0]}


def _match_window_by_name(name: str) -> dict:
    """在可见窗口里找"标题含这个名字"的窗口（同名取面积最大 = 主窗口）。"""
    if not name:
        return {}
    from . import visual_launch

    hits = [w for w in (visual_launch.list_windows().get("windows") or [])
            if name.lower() in (w.get("title") or "").lower()]
    if not hits:
        return {}
    return {"hwnd": int(hits[0]["hwnd"]), "title": hits[0].get("title") or ""}


def resolve_target(harness_id: str, *, name_override: str = "") -> dict:
    """**这个 harness 现在能把话送到哪儿**：优先记过的配置，其次按名字自动找。

    返回 `{ok, name, ws, hwnd, title, source, why}`；`source` 说清是"记过的配置"还是"按名字找的"。
    找不到时 `why` 要**能照着做**（比如"会话还没命名"）。
    """
    from . import input_anchor

    name = str(name_override or "").strip() or _session_name(harness_id)
    # ① **手动绑定优先**（2026-10-23，前端反馈"已登记员工还没有会话名 ⇒ 送不了话"）：
    #    用户在候选列表里点一下就把目标定下来 —— 通用动作，不靠猜标题，也不需要先命名会话。
    bind = input_anchor.get_bind(harness_id) if str(harness_id or "").strip() else None
    if bind:
        return {"ok": True, "name": name or (bind.get("title") or ""),
                "ws": bind.get("ws") or "", "hwnd": int(bind.get("hwnd") or 0),
                "title": bind.get("title") or "", "idx": int(bind.get("idx") or 0),
                "source": "手动绑定的目标", "why": ""}

    if not name:
        return {"ok": False, "name": "", "ws": "", "hwnd": 0, "title": "", "source": "",
                "why": ("既没有**手动绑定**目标，也还没有**会话名** —— 平台认不出它是哪个窗口/页面；"
                        "在候选列表里点一下「绑定到这个员工」，或先走「建立会话/重命名会话」。")}
    prof = input_anchor.get_web(harness_id) or {}
    if prof.get("ws"):
        return {"ok": True, "name": name, "ws": prof["ws"], "hwnd": 0,
                "title": prof.get("title") or "", "idx": int(prof.get("idx") or 0),
                "source": "记忆（上次成功过的页面）", "why": ""}
    hit = _match_target_by_name(name)
    if hit:
        return {"ok": True, "name": name, "ws": hit["ws"], "hwnd": 0,
                "title": (hit["target"].get("title") or ""), "idx": 0,
                "source": "按会话名在调试端口里找到页面", "why": ""}
    win = _match_window_by_name(name)
    if win:
        return {"ok": True, "name": name, "ws": "", "hwnd": win["hwnd"], "title": win["title"],
                "idx": 0, "source": "按会话名找到窗口", "why": ""}
    return {"ok": False, "name": name, "ws": "", "hwnd": 0, "title": "", "source": "",
            "why": ("按会话名「%s」既没找到开了调试端口的页面，也没找到同名窗口 —— "
                    "目标没开/没在最前，或名字对不上" % name)}


def deliver_to_harness(harness_id: str, text: str, *, prefer: str = "", idx: int | None = None,
                       press_enter: bool = False) -> dict:
    """**通用送话入口**：按 harness 的会话名自动定位目标，再走四通道投递。

    成功后把用到的目标记进 `input_anchor`（下次直接命中，不再重新找）；
    找不到目标时 `ok=False` 且 `why` 是**可执行**的一句话（不许含糊）。
    """
    target = resolve_target(harness_id)
    if not target.get("ok"):
        return {"ok": False, "channel": "", "verified": False, "attempts": [],
                "resolved": target, "detail": target.get("why", "没定位到目标"),
                "next_step": ("先确认这个 agent 的会话名与它窗口/页面标题一致（或先把调试端口开起来），"
                              "然后重试")}
    use_idx = int(idx if idx is not None else (target.get("idx") or 0))
    _anchor = None
    _send_rel = None
    # ★框选优先（老板 2026-10-23 夜：「让用户框出输入框和发送按钮」）：
    #   用户亲手框的区域最可信，其次才是"教过的位置"，最后才是视觉估位。
    try:
        from . import input_regions as _ir

        _reg = _ir.get(harness_id)
        if _reg and _reg.get("input"):
            # 点**上三分之一**而不是正中（2026-10-24 真机：大框正中会偏到底下那排小按钮上，
            # 见 `input_regions.input_point` 的说明）；发送按钮仍然点正中。
            _c = _ir.input_point(_reg["input"])
            _anchor = {"rel_x": _c["rel_x"], "rel_y": _c["rel_y"]}
        if _reg and _reg.get("send"):
            _send_rel = _ir.center_of(_reg["send"])
    except Exception as e:                                   # noqa: BLE001 — 读不到就当没框过
        print("[delivery] 读框选区域失败（继续用其他方式）：%s: %s" % (type(e).__name__, e), flush=True)
    if not _anchor:
        try:
            from . import input_anchor as _ia

            _anchor = _ia.get(harness_id)
        except Exception:                                    # noqa: BLE001 — 读不到就当没教过
            _anchor = None
    # ★2026-10-23（老板定方向）：**没人教也能干**——教位置在交互上不成立（鼠标只有一只），
    #   所以没教过就让"视觉脚本自己看"：方位先验（中下方）+ 图像（宽、带、内部均匀）。
    _anchor_source = "taught" if _anchor else ""
    if not _anchor and int(target.get("hwnd") or 0):
        try:
            from . import screen_vision as _sv

            guess = _sv.guess_input_anchor(int(target["hwnd"]))
            if guess.get("ok"):
                _anchor = {"rel_x": guess["rel_x"], "rel_y": guess["rel_y"]}
                _anchor_source = "auto-vision(%.2f)" % float(guess.get("score") or 0)
            else:
                print("[delivery] 视觉估位没把握：%s" % (guess.get("why") or "—"), flush=True)
        except Exception as e:                                # noqa: BLE001 — 估不出来照旧投（不点）
            print("[delivery] 视觉估位出错（不点，继续投）：%s: %s" % (type(e).__name__, e),
                  flush=True)
    res = deliver(text, hwnd=int(target.get("hwnd") or 0), title=target.get("title") or "",
                  ws=target.get("ws") or "", idx=use_idx, prefer=prefer,
                  press_enter=press_enter, anchor=_anchor, send_rel=_send_rel)
    res["resolved"] = target
    if _anchor_source:
        res["anchor_source"] = _anchor_source
    if res.get("ok"):
        from . import input_anchor

        if target.get("ws"):
            input_anchor.set_web(harness_id, ws=target["ws"], idx=use_idx,
                                 title=target.get("title") or "")
            # ★自动校准「教过的位置」（2026-10-23 老板问：UI 变了脚本还能找到输入框吗）：
            #   CDP 可用时直接问页面"那个输入框现在在哪"，把相对位置刷新掉 —— 界面改版也能自愈。
            #   校准失败**不影响**投递结果（只是少一次校准），但要留话，不静默。
            try:
                from . import cdp_input

                async def _box():
                    async with cdp_input.CDP(target["ws"]) as cdp:
                        return await cdp_input.element_box(cdp, use_idx)

                box = cdp_input.run(_box())
                if box.get("ok"):
                    input_anchor.set_anchor(harness_id, box["x"], box["y"],
                                            title=target.get("title") or "",
                                            source="auto-from-cdp", frame="cdp-auto")
                else:
                    print("[delivery] 位置自动校准跳过：%s" % (box.get("why") or "取不到输入框位置"),
                          flush=True)
            except Exception as e:                            # noqa: BLE001 — 校准失败不影响投递
                print("[delivery] 位置自动校准失败（投递不受影响）：%s: %s"
                      % (type(e).__name__, e), flush=True)
    return res


def deliver_by_name(name: str, text: str, *, prefer: str = "", idx: int = 0,
                    press_enter: bool = False) -> dict:
    """按**用户给的名字**（窗口标题片段 / 关键词）送话 —— 脚本激活那条路用它。

    和 deliver_to_harness 的区别只有一个：名字从哪来。
    这里不碰 harness_manager，所以脚本里存的是标题/关键词也能用；
    定位阶梯与投递通道完全同一套（能核实的优先），**一处真相**。
    """
    target = resolve_target("", name_override=name)
    if not target.get("ok"):
        return {"ok": False, "channel": "", "verified": False, "attempts": [],
                "resolved": target, "detail": target.get("why", "没定位到目标"),
                "next_step": "按这个名字没找到目标：确认目标窗口/页面开着，或换一个标题片段重试"}
    res = deliver(text, hwnd=int(target.get("hwnd") or 0), title=target.get("title") or "",
                  ws=target.get("ws") or "", idx=int(idx or target.get("idx") or 0),
                  prefer=prefer, press_enter=press_enter)
    res["resolved"] = target
    return res
