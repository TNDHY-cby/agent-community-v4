# -*- coding: utf-8 -*-
"""真机判据：**表层交互**（模拟鼠标 + 键盘）真的能把文字送进目标窗口。

老板 2026-10-23 定的方向：不要在应用内部找办法，**像人一样从表层操作**。
本判据用"能读回的目标"（PowerShell WinForms 文本框）把整条链跑满，逐格判：

| # | 判据 | 为什么 |
|---|---|---|
| ① | 窗口能置顶且**解析到句柄** | 后面点击要用句柄算相对坐标 |
| ② | 「教一次位置」能把鼠标位置换成窗口内**相对坐标**（0~1） | 换窗口大小/换显示器仍然对得上 |
| ③ | 点那个位置 + 逐字键入 ⇒ **读回核对 contains_expected=True** | 这是"真的送进去了"的唯一硬证据 |
| ④ | 中文（非 ASCII）也能键入 | unicode 按键事件，不经过输入法 |
| ⑤ | 没教过位置 ⇒ **拒绝乱点**并如实说 | 乱点会点坏人家的东西 |

退出码：0 全过 / 1 有红 / 2 环境没条件（非 Windows / 无 PowerShell）。
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

TAG = "ACSURF%d" % (int(time.time()) % 100000)
_NO_WINDOW = 0x08000000 if os.name == "nt" else 0

FORM_PS = r"""
Add-Type -AssemblyName System.Windows.Forms
$f = New-Object System.Windows.Forms.Form
$f.Text = '%s'
$f.Width = 640; $f.Height = 300
$f.StartPosition = 'CenterScreen'
$t = New-Object System.Windows.Forms.TextBox
$t.Multiline = $true; $t.Dock = 'Fill'
$f.Controls.Add($t)
$f.Add_Shown({ $f.Activate(); $t.Focus() })
[System.Windows.Forms.Application]::Run($f)
""" % TAG


def main() -> int:
    from agent_community.platform import input_sim as ins
    from agent_community.platform import visual_launch as vl

    fails, skips = [], []

    def cell(name: str, ok: bool, detail: str = "") -> None:
        print("   %-48s %-6s %s" % (name, "OK" if ok else "★FAIL", detail))
        if not ok:
            fails.append(name)

    def skip(name: str, why: str) -> None:
        print("   %-48s %-6s %s" % (name, "如实跳过", why))
        skips.append(name)

    if not ins.supported():
        print(ins.platform_note())
        return 2

    print("=" * 96)
    print("表层交互（模拟鼠标键盘）真机判据 —— 靶子 %s" % TAG)
    print("=" * 96)
    print("   %s" % ins.platform_note())
    print("   屏幕 %s，鼠标 %s，DPI 感知=%s" % (ins.screen_size(), ins.cursor_pos(), ins.ensure_dpi_aware()))

    # productization: allow(靶子必须是真 WinForms 窗口（powershell -STA 起）；本判据只在 Windows 上有意义)
    form = subprocess.Popen(["powershell", "-NoProfile", "-STA", "-Command", FORM_PS],
                            creationflags=_NO_WINDOW,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        win = {}
        for _ in range(30):
            time.sleep(0.4)
            rc, out = vl._run_inject(["-TitleLike", TAG, "-List"])
            for line in (out or "").splitlines():
                if TAG in line:
                    win = vl._parse_list_line(line)
                    break
            if win:
                break
        if not win:
            skip("① 靶子窗口出现", "WinForms 弹窗没起来（这台机器可能禁用了）")
            return 2

        rc, out = vl._run_inject(["-TitleLike", TAG, "-FocusOnly"])
        hwnd = vl._hwnd_from_output(out)
        cell("① 置顶并解析到窗口句柄", rc == 0 and hwnd > 0,
             "hwnd=%s ｜ %s" % (hwnd, next((l for l in out.splitlines() if l.startswith("[focus]")), ""))[:80])

        rect = ins.window_client_rect(hwnd) if hwnd else {}
        cell("② 拿到窗口客户区矩形", bool(rect) and rect.get("width", 0) > 50,
             "%s" % (rect or "拿不到"))

        # 「教一次位置」= 把鼠标放到输入框上，记相对坐标。这里直接按窗体中心算（文本框铺满客户区）。
        anchor = ins.screen_to_relative(hwnd, rect["left"] + rect["width"] // 2,
                                        rect["top"] + rect["height"] // 2) if rect else {}
        cell("③ 教位置：屏幕坐标 → 窗口内相对坐标（0~1）",
             bool(anchor) and 0 <= anchor.get("rel_x", -1) <= 1 and 0 <= anchor.get("rel_y", -1) <= 1,
             "rel=(%s, %s) inside=%s" % (anchor.get("rel_x"), anchor.get("rel_y"), anchor.get("inside")))

        # ④ 真点 + 真键入（ASCII）
        text_en = "SURFACE-OK-2026"
        res = vl.surface_send(TAG, text_en, anchor={"rel_x": anchor.get("rel_x", 0.5),
                                                    "rel_y": anchor.get("rel_y", 0.5)})
        cell("④ 点位置 + 逐字键入 ⇒ 读回核对通过（ASCII）",
             bool(res.get("ok")) and res.get("verified") is True,
             "ok=%s verified=%s ｜ %s" % (res.get("ok"), res.get("verified"), str(res.get("detail"))[:70]))

        # ⑤ 中文（unicode 按键，不需要输入法）
        text_cn = "中文探针-表层"
        res2 = vl.surface_send(TAG, text_cn, anchor={"rel_x": anchor.get("rel_x", 0.5),
                                                     "rel_y": anchor.get("rel_y", 0.5)})
        cell("⑤ 中文也能键入（unicode 事件）⇒ 读回核对通过",
             bool(res2.get("ok")) and res2.get("verified") is True,
             "ok=%s verified=%s" % (res2.get("ok"), res2.get("verified")))

        # ⑥ 没教过位置 ⇒ 拒绝乱点
        res3 = vl.surface_send(TAG, "x", anchor=None)
        cell("⑥ 没教过位置 ⇒ 拒绝点击并如实说",
             res3.get("ok") is False and res3.get("need_human") is True and "没教过" in str(res3.get("detail")),
             str(res3.get("detail"))[:70])
    finally:
        try:
            form.kill()
        except Exception:                                    # noqa: BLE001
            pass

    print("-" * 96)
    if fails:
        print("结论：**有 %d 格失败** ⇒ 表层交互没真通" % len(fails))
        return 1
    if skips:
        print("结论：**部分验证** —— 能测的都过了，%d 格如实跳过" % len(skips))
        return 2
    print("结论：**表层交互真通了（置顶 / 教位置 / 点击 / 中文键入 / 读回核对 / 不乱点）**")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
