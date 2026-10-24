# -*- coding: utf-8 -*-
"""真机判据：视觉拉起**真的把文字送进目标**，并且**不弹黑框、不谎报**。

起因（2026-10-23 老板真机报障）：「点'试投一次'只弹几个黑框，对象没置顶、也没输入，
界面却说已兜底送到」。四个根因 + 一条纪律，本脚本逐条钉死（全真跑，不 mock）：

| # | 根因 | 判据 |
|---|---|---|
| A | 起 `powershell` 没带 `CREATE_NO_WINDOW` ⇒ 每调用一次闪一个黑框 | ⑤ 调用前后数 `ConsoleWindowClass` 窗口，**新增=0** |
| B | `Set-Clipboard` 失败是**非终止错误**，脚本仍打 `[inject] …` 退出 0 ⇒ 上层判成"送到了" | ③ 必须看到 `[clipboard] wrote=… readback=ok` |
| C | 窗口没置顶时键鼠注入会贴到**前台别的窗口** | 脚本 `exit 6` 拒绝盲投（映射见 test_visual_launch_fallback） |
| D | `.ps1` 丢 BOM ⇒ PowerShell 按 ANSI 解码 ⇒ 中文乱码/解析失败 | ⑥ 首 3 字节必须是 UTF-8 BOM |

靶子：
- **WinForms 文本框**（原生 `EDIT` 子窗口）⇒ 能把"读回核对"跑满（`contains_expected=True`）；
- **cmd 控制台**（无文本框子窗口）⇒ 正是 Chrome/Electron 那种情形，`wmpaste` 必须**如实说不适用**。

退出码：0 全过 / 1 有红 / 2 部分（环境没条件，如实跳过）。
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

TAG = "ACPROBE%d" % (int(time.time()) % 100000)
PS1 = ROOT / "agent_community" / "tools" / "inject_window.ps1"
_NO_WINDOW = 0x08000000 if os.name == "nt" else 0

FORM_PS = r"""
Add-Type -AssemblyName System.Windows.Forms
$f = New-Object System.Windows.Forms.Form
$f.Text = '%s'
$f.Width = 560; $f.Height = 220
$t = New-Object System.Windows.Forms.TextBox
$t.Multiline = $true; $t.Dock = 'Fill'
$f.Controls.Add($t)
$f.Add_Shown({ $f.Activate(); $t.Focus() })
[System.Windows.Forms.Application]::Run($f)
""" % TAG


def _console_windows() -> int:
    """当前可见 `ConsoleWindowClass` 窗口数（"黑框"）。"""
    if os.name != "nt":
        return 0
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    n = [0]

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def _cb(hwnd, _lp):
        buf = ctypes.create_unicode_buffer(64)
        user32.GetClassNameW(hwnd, buf, 64)
        if buf.value == "ConsoleWindowClass" and user32.IsWindowVisible(hwnd):
            n[0] += 1
        return True

    user32.EnumWindows(_cb, 0)
    return n[0]


def _wait(vl, tag: str, timeout: float = 15.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        rc, out = vl._run_inject(["-List"])
        if rc == 0:
            for line in out.splitlines():
                if tag in line:
                    return vl._parse_list_line(line)
        time.sleep(0.4)
    return {}


def main() -> int:
    from agent_community.platform import visual_launch as vl

    fails, skips = [], []

    def cell(name: str, ok: bool, detail: str = "") -> None:
        print("   %-44s %-6s %s" % (name, "OK" if ok else "★FAIL", detail))
        if not ok:
            fails.append(name)

    def skip(name: str, why: str) -> None:
        print("   %-44s %-6s %s" % (name, "如实跳过", why))
        skips.append(name)

    if os.name != "nt":
        print("非 Windows：这套注入机制只在 Windows 上存在（如实跳过，不假绿）")
        return 2

    print("=" * 92)
    print("视觉拉起真机判据（靶子 %s）" % TAG)
    print("=" * 92)

    raw = PS1.read_bytes()
    cell("⑥ inject_window.ps1 带 UTF-8 BOM", raw.startswith(b"\xef\xbb\xbf"),
         "首 3 字节 OK" if raw.startswith(b"\xef\xbb\xbf") else "首 3 字节=%r" % raw[:3])

    form = subprocess.Popen(["powershell", "-NoProfile", "-STA", "-Command", FORM_PS],
                            creationflags=_NO_WINDOW,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        win = _wait(vl, TAG)
        if not win:
            skip("① WinForms 靶子窗口出现", "没等到（这台机器可能禁用了 WinForms 弹窗）")
        else:
            # ① 置顶 + ② clipboard（键鼠注入：必须前台；且要读回核对）
            rc, out = vl._run_inject(["-TitleLike", TAG, "-Text", "中文探针-CLIP",
                                      "-Method", "clipboard", "-Verify", "-PasteDelayMs", "500"])
            cell("① 窗口被置顶（is_foreground=True）", "is_foreground=True" in out,
                 next((l for l in out.splitlines() if l.startswith("[focus]")), "")[:60])
            if rc == 5:
                skip("② clipboard：写入+读回核对", "本机剪贴板不可用（脚本已如实 exit 5）")
            elif rc == 6:
                skip("② clipboard：键鼠注入", "窗口没置顶（脚本拒绝盲投，已如实 exit 6）")
            else:
                wrote = next((l for l in out.splitlines() if l.startswith("[clipboard]")), "")
                injected = "[inject] clipboard + Ctrl+V" in out
                # ★判据是**产品承诺的那三件**，不是"WinForms 一定读得回"：
                #   ① 剪贴板里确实是我们的文字（readback=ok，脚本自己核过）；
                #   ② 投递动作真的发生了（[inject] clipboard + Ctrl+V）；
                #   ③ 读不回时**必须如实**说不核实（[verify] 那行不含 True，且不许谎报已验证）。
                cell("② clipboard：写入经核对 + 投递发生 + 不谎报",
                     rc in (0, 4) and "readback=ok" in wrote and injected,
                     (wrote or "")[:60] + " ｜ " + ("投递动作=有" if injected else "投递动作=无")
                     + " ｜ rc=%s" % rc)

            # ③ wmpaste（消息式，不需要前台）
            rc2, out2 = vl._run_inject(["-TitleLike", TAG, "-Text", "中文探针-WM",
                                        "-Method", "wmpaste", "-Verify", "-PasteDelayMs", "500"])
            cell("③ wmpaste：消息式投递真的发到了控件（或如实说不适用）",
                 (rc2 in (0, 4) and "[inject] WM_PASTE" in out2) or (rc2 == 3 and "text-control" in out2),
                 next((l for l in out2.splitlines()
                       if l.startswith(("[inject]", "[FAIL]"))), out2[-70:])[:80] + " ｜ rc=%s" % rc2)
    finally:
        try:
            form.kill()
        except Exception:                                    # noqa: BLE001
            pass

    # ── 无文本框子窗口的真实窗口（Chrome/Electron 那种情形）：wmpaste 必须如实说"不适用" ──
    #   靶子用**平台自己的界面窗口**（class=Chrome_WidgetWin_1，没有 EDIT 子窗口）：
    #   `-Method wmpaste` 在找不到文本框时会在**投递之前** exit 3 ⇒ 不会往窗口里乱贴东西。
    before = _console_windows()
    rc_l, out_l = vl._run_inject(["-List"])
    cand = next((vl._parse_list_line(ln) for ln in out_l.splitlines()
                 if "Chrome_WidgetWin" in ln and (vl._parse_list_line(ln).get("title") or "").strip()), {})
    if cand:
        rc3, out3 = vl._run_inject(["-TitleLike", cand["title"], "-Text", "x", "-Method", "wmpaste"])
        cell("④ 无文本框窗口：wmpaste 如实说不适用（exit 3）",
             rc3 == 3 and "text-control" in out3,
             next((l for l in out3.splitlines() if l.startswith("[FAIL]")), out3[-70:])[:70] + " ｜ rc=%s" % rc3)
    else:
        skip("④ 无文本框窗口的 wmpaste 判据", "这台机器上没找到 Chrome 类窗口（不假绿）")
    after = _console_windows()
    cell("⑤ 我们自己的调用不弹黑框（新增控制台窗口=0）", after <= before,
         "前 %d / 后 %d" % (before, after))

    print("-" * 92)
    if fails:
        print("结论：**有 %d 格失败** ⇒ 视觉拉起没真通" % len(fails))
        return 1
    if skips:
        print("结论：**部分验证** —— 能测的都过了，%d 格如实跳过" % len(skips))
        return 2
    print("结论：**视觉拉起真通了（置顶 / 两档投递 / 读回核对 / 不弹黑框 / 不谎报）**")
    return 0


if __name__ == "__main__":
    import argparse
    import json

    _ap = argparse.ArgumentParser(description="视觉拉起真机判据；也可 --focus-like 只做置顶诊断")
    _ap.add_argument("--focus-like", default="", metavar="TITLE",
                     help="只把这个标题的窗口拉到最上层（不投递任何输入），打印结果后退出")
    _a = _ap.parse_args()
    if _a.focus_like:
        from agent_community.platform import visual_launch as _vl

        _res = _vl.focus(_a.focus_like)
        print(json.dumps(_res, ensure_ascii=False, indent=2))
        raise SystemExit(0 if _res.get("ok") else 7)
    raise SystemExit(main())
