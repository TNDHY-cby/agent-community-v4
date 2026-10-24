# -*- coding: utf-8 -*-
"""真机判据：**统一投递层**——同一个目标按可用通道自动选路，每档都如实标"能不能核实"。

回答老板的问题「只能浏览器吗」：**不是**。四条通道各管一类目标：

| 通道 | 目标 | 实测验证方式 |
|---|---|---|
| `cdp` | Chromium/Electron（需开调试端口） | 页内 `innerText` 读回 |
| `native` | Win32/WinForms（有原生文本框子控件） | `WM_PASTE` 投递 + `WM_GETTEXT` 读回 |
| `uia` | 暴露 UI Automation 的应用（如 Win11 记事本 Document） | UIA 读回 |
| `clipboard` | 其它（半自动） | **不能核实**（人按 Ctrl+V） |

本脚本真起两个靶子：
① 一个 WinForms 窗口（有原生文本框）⇒ 期望走 `native` 且 `verified=True`；
② 一个 headless Chromium 页面（textarea）⇒ 期望走 `cdp` 且 `verified=True`；
③ 一个"没有任何输入框"的窗口 ⇒ 期望**不会**假装成功（退到 `clipboard` 且 `need_human=True`）。

退出码：0 全过 / 1 有红 / 2 环境没条件（没有 Chromium 等，如实跳过）。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

TAG = "ACDELIV%d" % (int(time.time()) % 100000)
CDP_PORT = int(os.environ.get("AC_CHECK_CDP_PORT") or "9444")
# 通用入口的自动发现按 AC_CDP_PORT 探测端口 —— 判据里就指到我们刚起的那个
os.environ["AC_CDP_PORT"] = str(CDP_PORT)
NOWIN = 0x08000000 if os.name == "nt" else 0

FORM_PS = r"""
Add-Type -AssemblyName System.Windows.Forms
$f = New-Object System.Windows.Forms.Form
$f.Text = '%s'
$f.Width = 620; $f.Height = 260
$f.StartPosition = 'CenterScreen'
$t = New-Object System.Windows.Forms.TextBox
$t.Multiline = $true; $t.Dock = 'Fill'
$f.Controls.Add($t)
$f.Add_Shown({ $f.Activate(); $t.Focus() })
[System.Windows.Forms.Application]::Run($f)
""" % TAG

BARE_PS = r"""
Add-Type -AssemblyName System.Windows.Forms
$f = New-Object System.Windows.Forms.Form
$f.Text = '%s'
$f.Width = 420; $f.Height = 200
$l = New-Object System.Windows.Forms.Label
$l.Text = 'no input here'; $l.Dock = 'Fill'
$f.Controls.Add($l)
$f.Add_Shown({ $f.Activate() })
[System.Windows.Forms.Application]::Run($f)
""" % (TAG + "-BARE")

PAGE = """<!doctype html><meta charset=utf-8><title>%s-WEB</title>
<body><textarea id=ta style="width:600px;height:80px"></textarea></body>""" % TAG


def _find_browser() -> str:
    for c in (
        os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"),
                     r"Microsoft\Edge\Application\msedge.exe"),
        os.path.join(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
                     r"Microsoft\Edge\Application\msedge.exe"),
        os.path.join(os.environ.get("ProgramFiles", r"C:\Program"), ""),
    ):
        if c and os.path.isfile(c):
            return c
    for n in ("msedge", "chrome", "chromium", "google-chrome"):
        p = shutil.which(n)
        if p:
            return p
    return ""


def _hwnd_of(tag: str, timeout: float = 15.0) -> int:
    from agent_community.platform import visual_launch as vl

    # ★必须**精确匹配标题**：先试相等，再试以它结尾——
    #   否则 TAG 会把 TAG-BARE（无输入框那个靶子）也匹配进来，判据就测错了窗口（实测踩到）。
    end = time.time() + timeout
    while time.time() < end:
        ws = [w for w in (vl.list_windows().get("windows") or [])]
        for w in ws:
            if (w.get("title") or "") == tag:
                return int(w["hwnd"])
        for w in ws:
            t = (w.get("title") or "")
            if t.startswith(tag) and "-BARE" not in t:
                return int(w["hwnd"])
        time.sleep(0.4)
    return 0


def main() -> int:
    from agent_community.platform import delivery

    fails, skips = [], []

    def cell(name: str, ok: bool, detail: str = "") -> None:
        print("   %-52s %-6s %s" % (name, "OK" if ok else "★FAIL", detail))
        if not ok:
            fails.append(name)

    def skip(name: str, why: str) -> None:
        print("   %-52s %-6s %s" % (name, "如实跳过", why))
        skips.append(name)

    if os.name != "nt":
        print("非 Windows：原生窗口与 UIA 两条通道都不存在（cdp 仍可用），如实跳过本判据")
        return 2

    print("=" * 100)
    print("统一投递层真机判据（四通道选路）—— 靶子 %s" % TAG)
    print("=" * 100)

    # ── ① native：WinForms 文本框 ──
    form = subprocess.Popen(["powershell", "-NoProfile", "-STA", "-Command", FORM_PS],
                            creationflags=NOWIN, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    # ── ③ 没有任何输入框的窗口 ──
    bare = subprocess.Popen(["powershell", "-NoProfile", "-STA", "-Command", BARE_PS],
                            creationflags=NOWIN, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    browser = None
    page_dir = None
    try:
        hwnd = _hwnd_of(TAG)
        if not hwnd:
            skip("① WinForms 靶子窗口", "没起来")
        else:
            ch = delivery.channels(hwnd, title=TAG)
            names = {c["channel"]: c["available"] for c in ch["channels"]}
            cell("① channels() 认得这是个「原生窗口」", names.get("native") is True,
                 "available=%s｜recommended=%s" % (names, ch["recommended"]))
            res = delivery.deliver("统一投递-原生通道-中文", hwnd=hwnd, title=TAG)
            cell("② native 通道：WM_PASTE 投递 + WM_GETTEXT 读回",
                 res.get("channel") == "native" and res.get("verified") is True,
                 "channel=%s verified=%s｜%s" % (res.get("channel"), res.get("verified"),
                                                 str(res.get("detail"))[:50]))

        # ── ③ 没有输入框的窗口 ⇒ 不许假装成功 ──
        bh = _hwnd_of(TAG + "-BARE", timeout=10)
        if not bh:
            skip("③ 无输入框窗口的兜底判据", "靶子窗口没起来")
        else:
            res3 = delivery.deliver("无输入框-不该成功", hwnd=bh, title=TAG + "-BARE")
            cell("③ 没有输入框 ⇒ 退到半自动并如实说未核实",
                 res3.get("channel") == "clipboard" and res3.get("verified") is None
                 and res3.get("need_human") is True,
                 "channel=%s verified=%s" % (res3.get("channel"), res3.get("verified")))

        # ── ④ CDP：headless Chromium ──
        exe = _find_browser()
        if not exe:
            skip("④ cdp 通道：headless Chromium 靶子", "这台机器没找到 Edge/Chrome")
        else:
            page_dir = Path(tempfile.mkdtemp(prefix="ac_deliv_page_"))
            pg = page_dir / "p.html"
            pg.write_text(PAGE, encoding="utf-8")
            prof = Path(tempfile.mkdtemp(prefix="ac_deliv_prof_"))
            url = "file:///" + str(pg).replace("\\", "/")
            browser = subprocess.Popen([exe, "--headless=new", "--disable-gpu", "--no-first-run",
                                        "--no-default-browser-check", "--disable-sync",
                                        "--remote-debugging-port=%d" % CDP_PORT,
                                        "--user-data-dir=%s" % prof, url],
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            from agent_community.platform import cdp_input

            probe = {"ok": False}
            for _ in range(40):
                time.sleep(0.4)
                probe = cdp_input.probe([CDP_PORT])
                if probe.get("ok"):
                    break
            if not probe.get("ok"):
                skip("④ cdp 通道：headless Chromium 靶子", "端口没起来")
            else:
                tg = [t for p in probe["ports"] for t in p["targets"]]
                t = next((x for x in tg if TAG in ((x.get("title") or "") + (x.get("url") or ""))),
                         tg[0] if tg else None)
                if not t:
                    skip("④ cdp 通道：找到靶子页面", "target 里没有我们的页面")
                else:
                    res4 = delivery.deliver("统一投递-CDP通道-中文一行", ws=t["ws"])
                    cell("④ cdp 通道：读回核对通过（不经鼠标键盘）",
                         res4.get("channel") == "cdp" and res4.get("verified") is True,
                         "channel=%s verified=%s mode=%s" % (res4.get("channel"), res4.get("verified"),
                                                             (res4.get("attempts") or [{}])[0].get("mode")))

                    # ── ⑤ 通用入口：**只给会话名**，让平台自己找到目标（自动发现 + 记忆）──
                    # 这条是"落地到通用框架"的核心：上层不该知道通道/句柄/端口。
                    from agent_community.platform import harness_adapter, input_anchor

                    class _Sess:
                        session_aliases = [TAG + "-WEB"]
                        session_ref = {}

                    fake_id = "harness-autodiscover-check"
                    harness_adapter.harness_manager.sessions[fake_id] = _Sess()
                    try:
                        r5 = delivery.deliver_to_harness(fake_id, "通用入口-自动发现")
                        src = (r5.get("resolved") or {}).get("source", "")
                        cell("⑤ 只给会话名 ⇒ 自动找到目标并读回核对",
                             r5.get("ok") is True and r5.get("channel") == "cdp"
                             and r5.get("verified") is True,
                             "channel=%s verified=%s 来源=%s" % (r5.get("channel"), r5.get("verified"), src))
                        r6 = delivery.deliver_to_harness(fake_id, "第二次走记忆")
                        cell("⑥ 第二次直接命中「记住的目标」（不再重新找）",
                             r6.get("ok") is True and "记忆" in ((r6.get("resolved") or {}).get("source") or ""),
                             "来源=%s" % ((r6.get("resolved") or {}).get("source") or ""))
                        # 收尾：别把假 harness 留在数据里
                        harness_adapter.harness_manager.sessions.pop(fake_id, None)
                        input_anchor.clear(fake_id)
                    finally:
                        harness_adapter.harness_manager.sessions.pop(fake_id, None)
    finally:
        for p in (form, bare, browser):
            try:
                if p:
                    p.kill()
            except Exception:                                # noqa: BLE001
                pass
        if page_dir:
            shutil.rmtree(page_dir, ignore_errors=True)

    print("-" * 100)
    if fails:
        print("结论：**有 %d 格失败** ⇒ 统一投递层的选路/核实没做到" % len(fails))
        return 1
    if skips:
        print("结论：**部分验证** —— 能测的都过了，%d 格如实跳过" % len(skips))
        return 2
    print("结论：**统一投递层真通了（native / cdp 都能核实；没输入框时不假装成功）**")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
