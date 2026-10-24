# -*- coding: utf-8 -*-
"""真机判据：**抓图抓的是不是整扇窗**（DPI 缩放下最容易假成功的一步）。

## 为什么要有它（2026-10-24 凌晨真机事故）

本机 150% 缩放，Trae 窗口真实客户区 **2163×1353**。平台抓图不声明 DPI 感知时
`GetClientRect` 返回**逻辑坐标** 1442×902，而 `PrintWindow` 按**物理像素**画 ⇒
位图只装得下窗口**左上 2/3**，输入框被切掉；估位于是返回一个"很有把握的错位置"
（`rel_y=0.95`、`score=1.0`，实际落在聊天正文/底部工具条上）。

**症状是"三不报错"**：截图成功、估位成功、点下去打到别处 —— 典型"看起来成功"。
所以这条判据不看源码，只看**真抓一帧的尺寸**跟**窗口真实客户区**对不对得上。

## 判据（逐格）

| # | 判据 | 怎么判 |
|---|---|---|
| ① | 靶子窗口起来并解析到句柄 | 真 WinForms 窗口（powershell -STA） |
| ② | **地面真值**：窗口真实客户区（物理像素） | 子进程里先声明 DPI 再 `GetClientRect` |
| ③ | **抓图自己声明 DPI**：新进程直接抓图，尺寸 == 地面真值 | 子进程只 `import screen_vision`，不预先声明 |
| ④ | **对照**：先声明 DPI 再抓图，尺寸与③一致 | 两进程结果必须相同 |
| ⑤ | 抓到的图不是全黑 | `grab_png` 自带非零像素检查 |

**关于区分度（不假绿）**：本机缩放是 100% 时，声明与不声明 DPI 拿到的尺寸一样 ⇒
③④ 在**这台机器上**没有区分度。工具会**如实打印**"本机 100% 缩放，这条判据在这里测不出东西"，
而不是显示一片 OK 让人以为守住了。

退出码：0 全过 / 1 有红 / 2 环境没条件（非 Windows、没 PowerShell、窗口起不来）。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

TAG = "ACVISC%d" % (int(time.time()) % 100000)
_NO_WINDOW = 0x08000000 if os.name == "nt" else 0

FORM_PS = r"""
Add-Type -AssemblyName System.Windows.Forms
# 靶子窗口**必须自己声明 DPI 感知**：不然它按 96 DPI 建窗，
# 这台机器就算有缩放也造不出"声明/不声明不一样"的条件（判据就没区分度了）。
Add-Type -MemberDefinition '[DllImport("user32.dll")] public static extern bool SetProcessDpiAwarenessContext(System.IntPtr v); [DllImport("user32.dll")] public static extern bool SetProcessDPIAware();' -Name Dpi -Namespace W32 -ErrorAction SilentlyContinue | Out-Null
try { [W32.Dpi]::SetProcessDpiAwarenessContext([IntPtr](-4)) | Out-Null } catch { [W32.Dpi]::SetProcessDPIAware() | Out-Null }
$f = New-Object System.Windows.Forms.Form
$f.Text = '%s'
$f.Width = 900; $f.Height = 600
$f.StartPosition = 'CenterScreen'
$t = New-Object System.Windows.Forms.TextBox
$t.Multiline = $true; $t.Dock = 'Fill'
$f.Controls.Add($t)
[System.Windows.Forms.Application]::Run($f)
""" % TAG

#: 地面真值：先声明 DPI 感知，再读客户区（物理像素）
TRUTH_PY = r"""
import ctypes, json, sys
h = int(sys.argv[1])
u = ctypes.windll.user32
try:
    u.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
except Exception:
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        u.SetProcessDPIAware()
class R(ctypes.Structure):
    _fields_ = [("l", ctypes.c_long), ("t", ctypes.c_long),
                ("r", ctypes.c_long), ("b", ctypes.c_long)]
r = R()
u.GetClientRect(h, ctypes.byref(r))
dpi = 0
try:
    dpi = int(u.GetDpiForWindow(h))
except Exception:
    pass
print(json.dumps({"w": r.r - r.l, "h": r.b - r.t, "dpi": dpi}))
"""

#: ③ 新进程**不预先声明**，先原样读一次客户区（＝改之前那条路会拿到的值），再抓图
GRAB_PY = r"""
import ctypes, json, sys
h = int(sys.argv[2])


class R(ctypes.Structure):
    _fields_ = [("l", ctypes.c_long), ("t", ctypes.c_long),
                ("r", ctypes.c_long), ("b", ctypes.c_long)]


raw = R()
ctypes.windll.user32.GetClientRect(h, ctypes.byref(raw))   # 还没声明 DPI ⇒ 逻辑坐标（旧行为）
sys.path.insert(0, sys.argv[1])
from agent_community.platform import screen_vision as sv
if sys.argv[3] == "aware":
    from agent_community.platform import input_sim
    input_sim.ensure_dpi_aware()
    raw2 = R()
    ctypes.windll.user32.GetClientRect(h, ctypes.byref(raw2))
    raw = raw2                                            # 对照格：先声明再读
p = sv.grab_png(h)
print(json.dumps({"ok": bool(p.get("ok")), "client_w": p.get("client_w"),
                  "client_h": p.get("client_h"), "dpi_aware": p.get("dpi_aware"),
                  "raw_w": raw.r - raw.l, "raw_h": raw.b - raw.t,
                  "why": p.get("why") or ""}))
"""


def _run_json(code: str, *args: str) -> dict:
    """跑一小段 python 并把最后一行 JSON 解出来（失败给 `{}`，不假装成功）。"""
    try:
        out = subprocess.run([sys.executable, "-X", "utf8", "-c", code, *args],
                             capture_output=True, text=True, timeout=120,
                             encoding="utf-8", errors="replace")
    except Exception as e:                                    # noqa: BLE001 — 探针跑不起来要如实说
        print("   [探针跑不起来] %s: %s" % (type(e).__name__, e))
        return {}
    if out.returncode != 0:
        print("   [探针非零退出 %s] %s" % (out.returncode, (out.stderr or "").strip()[-300:]))
        return {}
    for line in reversed((out.stdout or "").strip().splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except ValueError:
                continue
    return {}


def main() -> int:
    from agent_community.platform import visual_launch as vl

    fails, skips = [], []

    def cell(name: str, ok: bool, detail: str = "") -> None:
        print("   %-46s %-6s %s" % (name, "OK" if ok else "★FAIL", detail))
        if not ok:
            fails.append(name)

    def skip(name: str, why: str) -> None:
        print("   %-46s %-6s %s" % (name, "如实跳过", why))
        skips.append(name)

    if os.name != "nt":
        print("非 Windows：抓屏这套（PrintWindow/GDI）只在这里有 —— 如实跳过，不算通过。")
        return 2

    print("=" * 100)
    print("视觉抓图真机判据 —— 抓的必须是**整扇窗**（靶子 %s）" % TAG)
    print("=" * 100)

    # productization: allow(靶子必须是真 WinForms 窗口（powershell -STA 起）；本判据只在 Windows 上有意义)
    form = subprocess.Popen(["powershell", "-NoProfile", "-STA", "-Command", FORM_PS],
                            creationflags=_NO_WINDOW,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        hwnd = 0
        for _ in range(30):
            time.sleep(0.4)
            try:
                wins = [w for w in (vl.list_windows().get("windows") or [])
                        if TAG in (w.get("title") or "")]
            except Exception as e:                            # noqa: BLE001 — 列不出窗口就如实说
                print("   [列窗口失败] %s: %s" % (type(e).__name__, e))
                wins = []
            if wins:
                hwnd = int(wins[0]["hwnd"])
                break
        if not hwnd:
            skip("① 靶子窗口出现", "WinForms 弹窗没起来（这台机器可能禁用了）")
            return 2
        cell("① 靶子窗口出现并解析到句柄", hwnd > 0, "hwnd=%s" % hwnd)

        truth = _run_json(TRUTH_PY, str(hwnd))
        cell("② 地面真值（真声明 DPI 后的客户区）",
             bool(truth) and int(truth.get("w") or 0) > 50,
             "%sx%s ｜ 窗口 DPI=%s" % (truth.get("w"), truth.get("h"), truth.get("dpi")))
        if not truth:
            return 2
        tw, th = int(truth["w"]), int(truth["h"])
        dpi = int(truth.get("dpi") or 96)

        naive = _run_json(GRAB_PY, str(ROOT), str(hwnd), "naive")
        cell("③ 新进程直接抓图（不预先声明）尺寸 == 地面真值",
             bool(naive.get("ok")) and naive.get("client_w") == tw and naive.get("client_h") == th,
             "抓图 %sx%s ｜ 真值 %sx%s ｜ dpi_aware=%s"
             % (naive.get("client_w"), naive.get("client_h"), tw, th, naive.get("dpi_aware")))

        aware = _run_json(GRAB_PY, str(ROOT), str(hwnd), "aware")
        same = (aware.get("client_w") == naive.get("client_w")
                and aware.get("client_h") == naive.get("client_h"))
        cell("④ 对照：先声明 DPI 再抓图，与③一致", bool(aware.get("ok")) and same,
             "%sx%s vs %sx%s" % (aware.get("client_w"), aware.get("client_h"),
                                 naive.get("client_w"), naive.get("client_h")))

        cell("⑤ 抓到的图不是全黑（有真内容）", bool(naive.get("ok")),
             str(naive.get("why") or "ok")[:60])

        # 区分度＝"不声明 DPI 时读到的客户区" 与真值是否不同。不同才说明这条判据能真的抓住那个 bug。
        raw_w, raw_h = naive.get("raw_w"), naive.get("raw_h")
        if raw_w and (raw_w != tw or raw_h != th):
            print("   %-46s %-6s %s" % ("⑥ 本机有没有区分度", "有",
                                        "不声明 DPI 时客户区是 %sx%s，真值 %sx%s（窗口 DPI=%s，%d%% 缩放）"
                                        "⇒ 抓图若不自己声明 DPI，③ 会拿到被切的小图并判红"
                                        % (raw_w, raw_h, tw, th, dpi, round(dpi * 100.0 / 96.0))))
        else:
            print("   %-46s %-6s %s" % ("⑥ 本机有没有区分度", "如实说明",
                                        "本机窗口 DPI=%s（100%% 缩放）⇒ 声明与不声明拿到的客户区相同，"
                                        "③④ 在这里**测不出**「没声明 DPI 就抓半张图」这个 bug，"
                                        "别把它当已守住（换一台有缩放的机器才真正验得到）" % dpi))
    finally:
        try:
            form.kill()
        except Exception:                                    # noqa: BLE001 — 收尾杀不掉不影响结论
            pass

    print("-" * 100)
    if fails:
        print("结论：**有 %d 格失败** ⇒ 抓图没保证抓全窗口（估位/框选都会落在错的地方）" % len(fails))
        return 1
    print("结论：**抓图抓的是整扇窗**（尺寸与真实客户区一致，DPI 由抓图自己声明）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
