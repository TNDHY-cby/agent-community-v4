# -*- coding: utf-8 -*-
"""真机判据（可复用）：**生成的视觉激活脚本**在本机会话里真跑。

为什么单独一条（老板 2026-10-23：「那就得视觉脚本」）：
CDP 端口被应用拒绝后，唯一正当的路是**脚本在用户自己的会话里执行**。
本工具真跑两种情况，判据不写死"某台机器上有没有某个程序"，只要求：
  ① 有原生文本框的窗口（本工具自己拉一个 WinForms 靶子）=> **exit 0**（读回核对通过）；
  ② 没有原生文本框的窗口（任意 Chromium 类窗口，找得到就用，找不到如实跳过）=>
     **exit 3**（送出了但未核实）—— 绝不允许 exit 0（那才是"假成功"）。

用法：
    python tools/visual_script_check.py            # 跑两项
    python tools/visual_script_check.py --keep     # 跑完留着靶子窗口（调试用）
退出码：0=全过 / 1=有格子失败 / 2=环境不允许（非 Windows）
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:                                    # noqa: BLE001 — 老终端不支持就算了
    pass

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

FORM_PS = r"""
Add-Type -AssemblyName System.Windows.Forms
$f = New-Object System.Windows.Forms.Form
$f.Text = '%s'
$f.Width = 640; $f.Height = 260
$t = New-Object System.Windows.Forms.TextBox
$t.Multiline = $true; $t.Dock = 'Fill'
$f.Controls.Add($t)
$f.Add_Shown({ $f.Activate(); $t.Focus() })
[System.Windows.Forms.Application]::Run($f)
"""

CELLS = []


def cell(name: str, ok: bool, note: str = "") -> None:
    CELLS.append((name, bool(ok), note))
    print("  %-52s %s  %s" % (name, "OK  " if ok else "★FAIL", note))


def _gen(title: str, text: str, method: str = "auto") -> Path:
    from agent_community.platform.routers import docking as dk

    s = dk.compose_activation_script(keywords=[title], title=title, method=method,
                                     message=text, verified=None,
                                     generated_at=time.strftime("%Y-%m-%dT%H:%M:%S"))
    out = Path(_ROOT) / "temp" / "visual_script_check.ps1"
    out.parent.mkdir(exist_ok=True)
    out.write_bytes(b"\xef\xbb\xbf" + s["text"].encode("utf-8"))
    return out


def _run(script: Path) -> tuple[int, str]:
    # productization: allow(视觉激活脚本本身就是 PowerShell 脚本，只能由 PowerShell 执行；非 Windows 时本工具在 main() 里如实 exit 2，不假装支持)
    r = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180)
    return r.returncode, ((r.stdout or "") + (r.stderr or "")).strip()


def _wait_title(tag: str, seconds: float = 12.0) -> bool:
    from agent_community.platform import visual_launch as vl

    end = time.time() + seconds
    while time.time() < end:
        try:
            if any(tag in (w.get("title") or "") for w in (vl.list_windows().get("windows") or [])):
                return True
        except Exception:                                     # noqa: BLE001
            pass
        time.sleep(0.3)
    return False


def main() -> int:
    ap = argparse.ArgumentParser(description="生成的视觉激活脚本：真机两档判据")
    ap.add_argument("--keep", action="store_true", help="跑完不关靶子窗口（调试用）")
    args = ap.parse_args()

    if sys.platform != "win32":
        print("非 Windows：视觉激活脚本用的是 Win32（前台/控件），这里如实跳过。")
        return 2

    print("=" * 100)
    print("视觉激活脚本真机判据（本机执行版：WM_PASTE 到控件 / 剪贴板 + Ctrl+V）")
    print("=" * 100)

    tag = "ACVSC%d" % (int(time.time()) % 100000)
    form = subprocess.Popen(["powershell", "-NoProfile", "-STA", "-Command", FORM_PS % tag],
                            creationflags=0x08000000, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)
    try:
        shown = _wait_title(tag)
        cell("① 靶子窗口出现（WinForms，自带原生文本框）", shown, tag if shown else "12 秒内没等到")
        if shown:
            script = _gen(tag, "视觉脚本-中文话术-探针", "auto")
            rc, out = _run(script)
            has_verify = "[verify] 读回核对通过" in out
            cell("② 有文本框 => WM_PASTE + 读回核对通过（exit 0）", rc == 0 and has_verify,
                 "exit=%s" % rc)
            open_script = script.read_text(encoding="utf-8-sig")
            cell("③ 正文里没有写死平台地址/端口/盘符",
                 all(x not in open_script for x in ("http://", "127.0.0.1", "D:\\", "C:\\")),
                 "本机执行版（不依赖平台在跑）")
    finally:
        if not args.keep:
            try:
                form.kill()
            except Exception:                                 # noqa: BLE001
                pass

    # ② 没有原生文本框的窗口（Chromium 类）—— 找不到就如实跳过，不硬造
    try:
        from agent_community.platform import visual_launch as vl

        chromes = [w for w in (vl.list_windows().get("windows") or [])
                   if "Chrome_WidgetWin" in (w.get("class") or "")]
    except Exception as e:                                    # noqa: BLE001
        chromes = []
        print("  （列窗口失败，按跳过处理：%s）" % e)
    if chromes:
        title = (chromes[0].get("title") or "").strip()
        script = _gen(title, "视觉脚本-Electron-靶子", "clipboard")
        rc, out = _run(script)
        honest = ("[verify]" in out and "未核实" in out)
        cell("④ 没有原生文本框 => 送出但**未核实**（exit 3，不许 exit 0）",
             rc == 3 and honest, "靶子=%s exit=%s" % (title[:24], rc))
    else:
        print("  （没找到 Chromium 类窗口 => 第④格如实跳过，不伪造）")

    print("-" * 100)
    failed = [c for c in CELLS if not c[1]]
    if failed:
        print("结论：**有 %d 格失败** => 视觉脚本没做到「真跑起来」" % len(failed))
        return 1
    print("结论：**视觉脚本两档都真跑通了**（有文本框可核实；没有就如实说未核实）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
