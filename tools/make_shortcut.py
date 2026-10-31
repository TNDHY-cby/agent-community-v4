# -*- coding: utf-8 -*-
"""tools/make_shortcut.py —— 生成/修复桌面「启动合作社」快捷方式（V72）。

## 为什么需要它

老板桌面那个 `.lnk` 是 2026-10-17 手工建的，**指向了另一个副本**
（`…\\external-agents-community\\启动合作社.cmd`）⇒ 服务没跑时双击，
会用**那份副本的数据**起平台，自己的工作间/成员不在里面。这类"指错地方"的快捷方式
在别人机器上也一样会犯，所以**做成工具**：路径全部由脚本按自身位置算出来，不写死。

## 用法

    python tools/make_shortcut.py            # 生成/覆盖桌面快捷方式（指向本仓库）
    python tools/make_shortcut.py --check    # 只检查：指向的文件真的存在、且在当前仓库内
    python tools/make_shortcut.py --where    # 打印桌面路径与当前指向（不修改）

非 Windows 平台**如实说"不支持"**（`.lnk` 是 Windows 的东西），退出码 3。
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

REPO = Path(__file__).resolve().parents[1]
#: 双击入口是 **.vbs**：由 `wscript.exe` 跑，**天生无窗口**（cmd 必闪一下黑框），
#: 而且能逐个试解释器（PATH 上的 `python.exe` 可能是应用商店别名，一跑就退）。
#: 生成物由 `tools/gen_entry.py` 从模板产出（GBK+CRLF）。
TARGET = REPO / "启动合作社.vbs"
LNK_NAME = "启动合作社.lnk"


def _ps(script: str) -> str:
    # `.lnk` 是 Windows 独有的东西：非 Windows **如实不支持**（main 里直接 exit 3），
    # 这里的平台分支就是那句"如实说"的实现。
    if os.name != "nt":
        raise RuntimeError("这个平台没有 .lnk（Windows 快捷方式）")
    # 中文路径必须让 PowerShell 按 UTF-8 吐回来：系统 ANSI 是 GBK，
    # 不设这句，read_lnk 回读的路径全是乱码 ⇒ --check 误报"文件不存在"（10-29 实咬过）。
    script = "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; " + script
    r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout or "").strip()[:400])
    return (r.stdout or "").strip()


def desktop_dir() -> Path:
    """桌面目录 —— **先问系统，别猜**（猜错就是"只对我这台机器成立"）。

    顺序：
    ① **Windows**：PowerShell `GetFolderPath('Desktop')`（可能被 OneDrive 重定向，`~/Desktop` 会猜错）；
    ② **Linux**：`XDG_DESKTOP_DIR` 环境变量 → `xdg-user-dir DESKTOP` → `~/.config/user-dirs.dirs` 里那一行；
       （中文/其它语言的桌面目录是「桌面」「デスクトップ」等，`~/Desktop` 根本不存在）
    ③ **macOS**：`~/Desktop`（这个是真的固定）；
    ④ 全都不行：**如实返回 `~/Desktop` 并说明这是兜底**——调用方会打印，不假装成功。
    """
    import sys as _sys

    # ① Windows：系统 API
    if _sys.platform == "win32":
        try:
            p = _ps("[Environment]::GetFolderPath('Desktop')")
            if p and Path(p).exists():
                return Path(p)
        except Exception:                                    # noqa: BLE001
            pass

    # ② Linux：XDG 三路
    if _sys.platform.startswith("linux"):
        env = os.environ.get("XDG_DESKTOP_DIR")
        if env and Path(env).exists():
            return Path(env)
        try:
            out = subprocess.run(["xdg-user-dir", "DESKTOP"], capture_output=True,
                                 text=True, timeout=5)
            cand = (out.stdout or "").strip()
            if cand and Path(cand).exists():
                return Path(cand)
        except Exception:                                    # noqa: BLE001 — 没装 xdg-user-dirs 很常见
            pass
        cfg = Path(os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config")) / "user-dirs.dirs"
        try:
            for line in cfg.read_text(encoding="utf-8", errors="replace").splitlines():
                line = line.strip()
                if line.startswith("XDG_DESKTOP_DIR="):
                    val = line.split("=", 1)[1].strip().strip('"')
                    val = val.replace("$HOME", str(Path.home()))
                    if val and Path(val).exists():
                        return Path(val)
        except Exception:                                    # noqa: BLE001
            pass

    # ③ macOS / ④ 兜底
    fallback = Path.home() / "Desktop"
    print("[make_shortcut] 没能从系统问出桌面目录，按兜底用 %s"
          "（若不对，请把快捷方式手工放到你的桌面）" % fallback, flush=True)
    return fallback


def read_lnk(path: Path) -> dict:
    """读一个 .lnk 的字段（目标/参数/工作目录）。"""
    script = (
        "$s=(New-Object -ComObject WScript.Shell).CreateShortcut('%s');"
        "@{target=$s.TargetPath;args=$s.Arguments;workdir=$s.WorkingDirectory} |"
        " ConvertTo-Json -Compress" % str(path).replace("'", "''")
    )
    import json

    return json.loads(_ps(script))


def write_lnk(path: Path, target: Path, workdir: Path, args: str = "") -> None:
    script = (
        "$s=(New-Object -ComObject WScript.Shell).CreateShortcut('%s');"
        "$s.TargetPath='%s';$s.Arguments='%s';$s.WorkingDirectory='%s';"
        "$s.IconLocation='%s,137';$s.WindowStyle=1;$s.Description='生产合作社 · 双击启动（先播过场，自动进大厅）';"
        "$s.Save()" % (str(path).replace("'", "''"), str(target).replace("'", "''"),
                       args.replace("'", "''"), str(workdir).replace("'", "''"),
                       r"C:\WINDOWS\System32\shell32.dll")
    )
    _ps(script)


def check(quiet: bool = False) -> int:
    """判据：快捷方式指向的必须**是当前仓库里的启动入口**，且文件真的存在。"""
    d = desktop_dir()
    lnk = d / LNK_NAME
    problems = []
    if not lnk.exists():
        problems.append("桌面没有 %s（跑一次 `python tools/make_shortcut.py` 生成）" % LNK_NAME)
    else:
        info = read_lnk(lnk)
        tgt = Path(info.get("target") or "")
        if not tgt.exists():
            problems.append("快捷方式指向的文件不存在：%s" % tgt)
        else:
            try:
                tgt.resolve().relative_to(REPO.resolve())
            except Exception:                                # noqa: BLE001
                problems.append("快捷方式指向了**别的副本**（应在 %s 内）：%s" % (REPO, tgt))
        if not TARGET.exists():
            problems.append("本仓库缺少启动入口：%s" % TARGET)
    if problems:
        if not quiet:
            print("✗ 快捷方式不合规：")
            for p in problems:
                print("   · " + p)
        return 1
    if not quiet:
        print("✓ 快捷方式 OK：%s" % lnk)
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="生成/修复桌面「启动合作社」快捷方式")
    ap.add_argument("--check", action="store_true", help="只检查，不修改")
    ap.add_argument("--where", action="store_true", help="打印桌面路径与当前指向")
    args = ap.parse_args(argv)

    if os.name != "nt":
        print("这个平台没有 .lnk（Windows 快捷方式）；Linux/macOS 请把 `启动合作社.cmd` 换成等价的桌面入口。")
        return 3

    d = desktop_dir()
    lnk = d / LNK_NAME
    if args.where:
        print("桌面：%s" % d)
        print("应指向：%s" % TARGET)
        if lnk.exists():
            print("实际指向：%s" % read_lnk(lnk))
        else:
            print("实际：没有这个快捷方式")
        return 0
    if args.check:
        return check()

    if not TARGET.exists():
        print("✗ 缺启动入口：%s" % TARGET)
        return 1
    write_lnk(lnk, TARGET, REPO)
    print("已写入：%s → %s" % (lnk, TARGET))
    return check()


if __name__ == "__main__":
    raise SystemExit(main())
