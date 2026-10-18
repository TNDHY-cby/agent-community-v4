# -*- coding: utf-8 -*-
"""**开机自启**：让平台（平台服务 + 看门狗）在登录后自己起来 —— 通用、可关、可查。

## 为什么有它（2026-10-18 真事故）

老板那台机器突然关机，重启后 **18920 打不开**：项目里明明有看门狗（`deploy/watchdog_all.py --daemon`），
但**没有任何"随机器启动"的机制**（启动文件夹空、也没有计划任务）⇒ 每个人重启后都得手工拉一次。
这违反「装好就能用」。本工具把这件事做成一条命令，并且**不藏本机路径**（生成物落在数据目录 / 用户目录，
源码里没有任何机器专属坐标）。

## 用法

```bash
python tools/autostart.py status          # 现在装没装（人类可读）
python tools/autostart.py status --json   # 机器可读（守卫用）
python tools/autostart.py enable          # 装（Windows: 启动文件夹快捷方式；Linux: systemd user unit）
python tools/autostart.py enable --dry-run   # 只打印会做什么，不落盘
python tools/autostart.py disable         # 卸（只删我们自己建的那一个）
```

## 边界（如实说）

- **Windows**：往「启动」文件夹放一个**快捷方式**（用户级，不需要管理员）；指向数据目录下的 `ac_watchdog.cmd`。
- **Linux**：生成 systemd **user** unit 到 `~/.config/systemd/user/`，并**打印**要执行的 `systemctl --user enable --now`
  （本工具不替你开 systemd；WSL 里 systemd 常没开 —— 那就如实显示"没生效"）。
- 其它平台：`status` 报 `supported=false`，`enable` 明确拒绝（**不假装成功**）。
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent.parent
#: 看门狗优先用**发布面**里的 `tools/watchdog.py`（陌生人也有）；dev-only 的 `deploy/watchdog_all.py` 作兜底。
#: ⚠️ `deploy/` 不在发布面 —— 所以发布出去的包只能靠 `tools/watchdog.py`；两个都没有就**明确报错**。
_WATCHDOG_CANDIDATES = (ROOT / "tools" / "watchdog.py", ROOT / "deploy" / "watchdog_all.py")
SHORTCUT_NAME = "外端Agent生产合作社-看门狗.lnk"
UNIT_NAME = "ac-platform.service"


def watchdog_path() -> Path:
    for p in _WATCHDOG_CANDIDATES:
        if p.exists():
            return p
    return _WATCHDOG_CANDIDATES[0]      # 不存在也要给出"应该在哪"，让报错可读


def data_dir() -> Path:
    """跟平台同一套口径：优先 `AC_DATA_DIR`，否则仓库下 `agent_community/data`。"""
    env = os.environ.get("AC_DATA_DIR")
    if env:
        return Path(env)
    try:
        sys.path.insert(0, str(ROOT))
        from agent_community.platform import env_spec
        return Path(env_spec.data_dir())
    except Exception:                                       # noqa: BLE001
        return ROOT / "agent_community" / "data"


def startup_dir(override: str = "") -> Path:
    if override:
        return Path(override)
    if os.name == "nt":
        appdata = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
    xdg = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(xdg) / "systemd" / "user"


def launcher_path() -> Path:
    return data_dir() / "autostart" / ("ac_watchdog.cmd" if os.name == "nt" else "ac_watchdog.sh")


def launcher_content() -> str:
    """生成启动器内容 —— **本机坐标全部由运行时发现**（不写死在源码里）。"""
    py = str(sys.executable)
    wd = str(watchdog_path())
    proj = str(ROOT)
    if os.name == "nt":
        return ("@echo off\r\n"
                "rem 由 tools/autostart.py 生成：开机/登录后拉起看门狗（它再拉起平台服务）\r\n"
                'cd /d "%s"\r\n'
                '"%s" "%s" --daemon --interval 30\r\n' % (proj, py, wd))
    return ("#!/usr/bin/env bash\n"
            "# 由 tools/autostart.py 生成：登录后拉起看门狗（它再拉起平台服务）\n"
            'cd "%s" || exit 1\n'
            '"%s" "%s" --daemon --interval 30\n' % (proj, py, wd))


def unit_content() -> str:
    return ("[Unit]\n"
            "Description=外端Agent生产合作社 平台看门狗\n"
            "After=network-online.target\n\n"
            "[Service]\n"
            "Type=simple\n"
            "ExecStart=/bin/bash %s\n"
            "Restart=on-failure\n"
            "RestartSec=10\n\n"
            "[Install]\n"
            "WantedBy=default.target\n" % launcher_path())


def _shortcut_target(sfile: Path) -> str:
    """读回 Windows 快捷方式的目标（PowerShell WScript.Shell）。"""
    if os.name != "nt":
        return ""
    ps = ("$s=(New-Object -ComObject WScript.Shell).CreateShortcut('%s');"
          "Write-Output $s.TargetPath" % str(sfile).replace("'", "''"))
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=30)
        return (r.stdout or "").strip()
    except Exception:                                       # noqa: BLE001
        return ""


def status(startup: str = "", unit_dir: str = "") -> dict:
    sup = os.name == "nt" or sys.platform.startswith("linux")
    info = {"supported": sup, "platform": platform.system(), "installed": False,
            "where": "", "detail": "", "launcher": str(launcher_path())}
    if os.name == "nt":
        target = Path(startup or startup_dir()) / SHORTCUT_NAME
        info["where"] = str(target)
        if target.exists():
            info["installed"] = True
            tgt = _shortcut_target(target)
            info["detail"] = "快捷方式存在" + ("（指向 %s）" % tgt if tgt else "（读不回目标）")
        else:
            info["detail"] = "启动文件夹里没有这个快捷方式 ⇒ 重启后平台不会自己起"
    elif sys.platform.startswith("linux"):
        unit = Path(unit_dir or startup_dir()) / UNIT_NAME
        info["where"] = str(unit)
        if unit.exists():
            info["installed"] = True
            enabled = subprocess.run(["systemctl", "--user", "is-enabled", UNIT_NAME],
                                     capture_output=True, text=True).stdout.strip() \
                if _has_systemctl() else ""
            info["detail"] = ("unit 存在；systemctl --user is-enabled = %s" % (enabled or "未知"))
        else:
            info["detail"] = "没有 systemd user unit ⇒ 重启后平台不会自己起"
    else:
        info["detail"] = "这个平台不支持自动安装自启（如实说）"
    return info


def _has_systemctl() -> bool:
    try:
        return subprocess.run(["systemctl", "--version"], capture_output=True).returncode == 0
    except Exception:                                       # noqa: BLE001
        return False


def enable(*, dry_run: bool = False, startup: str = "", unit_dir: str = "") -> int:
    if os.name not in ("nt",) and not sys.platform.startswith("linux"):
        print("★不支持这个平台的自启安装（如实说，不假装成功）")
        return 1
    lp = launcher_path()
    print("① 启动器：%s" % lp)
    print(launcher_content().rstrip())
    if os.name == "nt":
        target = Path(startup or startup_dir()) / SHORTCUT_NAME
        print("② 快捷方式：%s → 指向上面的 .cmd" % target)
    else:
        target = Path(unit_dir or startup_dir()) / UNIT_NAME
        print("② systemd user unit：%s" % target)
        print(unit_content().rstrip())
    if dry_run:
        print("\n（--dry-run：什么都没写）")
        return 0

    lp.parent.mkdir(parents=True, exist_ok=True)
    lp.write_text(launcher_content(), encoding="utf-8", newline="")
    target.parent.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        ps = ("$w=New-Object -ComObject WScript.Shell;"
              "$s=$w.CreateShortcut('%s');$s.TargetPath='%s';$s.Arguments='';"
              "$s.WindowStyle=7;$s.Description='外端Agent生产合作社 看门狗自启';$s.Save()"
              % (str(target).replace("'", "''"), str(lp).replace("'", "''")))
        r = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=60)
        if r.returncode != 0:
            print("★创建快捷方式失败：%s" % ((r.stdout or "") + (r.stderr or ""))[-300:])
            return 1
        print("\n已装：下次登录会自动起看门狗（它再拉起平台服务）。")
        print("立即生效：直接双击那个快捷方式，或运行 `%s`" % lp)
    else:
        target.write_text(unit_content(), encoding="utf-8", newline="")
        print("\n已生成 unit。启用（需要 systemd user 会话）：")
        print("  systemctl --user daemon-reload")
        print("  systemctl --user enable --now %s" % UNIT_NAME)
        print("  systemctl --user status %s" % UNIT_NAME)
        if not _has_systemctl():
            print("⚠️ 本机没有 systemctl ⇒ 这个 unit 不会生效（WSL 常见）。可用 crontab @reboot 或 task scheduler 替代。")
    return 0


def disable(*, startup: str = "", unit_dir: str = "") -> int:
    removed = []
    if os.name == "nt":
        target = Path(startup or startup_dir()) / SHORTCUT_NAME
    else:
        target = Path(unit_dir or startup_dir()) / UNIT_NAME
    if target.exists():
        target.unlink()
        removed.append(str(target))
    try:
        lp = launcher_path()
        if lp.exists():
            lp.unlink()
            removed.append(str(lp))
    except OSError:
        pass
    print("已卸：%s" % ("、".join(removed) if removed else "（本来就没装）"))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("status", "enable", "disable"):
        s = sub.add_parser(name)
        s.add_argument("--json", action="store_true")
        s.add_argument("--dry-run", action="store_true")
        s.add_argument("--startup", default="", help="覆盖启动文件夹（测试用）")
        s.add_argument("--unit-dir", default="", help="覆盖 systemd user 目录（测试用）")
    a = ap.parse_args()

    if a.cmd == "status":
        info = status(a.startup, a.unit_dir)
        if a.json:
            print(json.dumps(info, ensure_ascii=False))
            return 0
        print("开机自启：%s" % ("已装" if info["installed"] else "未装"))
        print("  位置：%s" % info["where"])
        print("  说明：%s" % info["detail"])
        print("  支持：%s（%s）" % ("是" if info["supported"] else "否", info["platform"]))
        return 0
    if a.cmd == "enable":
        return enable(dry_run=a.dry_run, startup=a.startup, unit_dir=a.unit_dir)
    return disable(startup=a.startup, unit_dir=a.unit_dir)


if __name__ == "__main__":
    raise SystemExit(main())
