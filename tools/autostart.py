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
import re
import shutil
import subprocess
import sys
import time
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


def autostart_dir() -> Path:
    """开机自启**入口脚本**的落点（默认 `<仓库>/deploy/autostart`）。

    ★2026-10-23 搬过一次：原来放在 `data/autostart/`（**运行数据**目录里）。
    后果是"登录自启的入口住在可变数据里" —— 换 `AC_DATA_DIR`、清数据、换实例，
    入口就凭空消失，而**登录时什么都不会起**（死状态、且没有任何提示）。
    仓库里的 `deploy/` 才是稳定的；它**不随发布同步**（dev-only）⇒ 里面写本机绝对路径不会外泄。

    `AC_AUTOSTART_DIR` 可覆盖（测试/多实例用，别把真仓库写脏）。
    """
    env = (os.environ.get("AC_AUTOSTART_DIR") or "").strip()
    d = Path(env) if env else (ROOT / "deploy" / "autostart")
    d.mkdir(parents=True, exist_ok=True)
    return d


def legacy_launcher_path() -> Path:
    """老位置（`data/autostart/`）—— 只用于**迁移**与"残留"提示。"""
    ext = "ac_watchdog.ps1" if os.name == "nt" else "ac_watchdog.sh"
    return data_dir() / "autostart" / ext


def launcher_path() -> Path:
    """Windows 用 **.ps1**（不是 .cmd）—— 见 `launcher_content()` 的实测理由。"""
    ext = "ac_watchdog.ps1" if os.name == "nt" else "ac_watchdog.sh"
    return autostart_dir() / ext


def launcher_content() -> str:
    """生成启动器内容 —— **本机坐标全部由运行时发现**（不写死在源码里）。

    ## 为什么 Windows 用 `.ps1` 而不是 `.cmd`（2026-10-18 实测）

    这台机器的数据目录带**中文**（`…\\agent_community\\data\\autostart\\`）。实测四组对照：

    | 用例 | 结果 |
    |---|---|
    | `echo hi >> "%TEMP%\\t1.log"`（ASCII 路径） | 写了 |
    | `echo hi >> "<中文路径>\\t2.log"` | **静默不写**（cmd 把 UTF-8 脚本里的中文按 cp936 解释 ⇒ 重定向目标变乱码） |
    | `cd /d "<中文路径>"` 后再 echo | 写了（cd 的参数没事） |
    | `"%PY%" -c "print(123)" > "%TEMP%\\t4.log"` | 写了 |

    ⇒ `.cmd` + 中文路径的**重定向**是不可靠的（正是"看门狗一声不响什么都没起"的真因）。
    PowerShell 5.1 读带 BOM 的 UTF-8 脚本时对 Unicode 路径是稳的 ⇒ Windows 侧改用 `.ps1`。
    """
    py = str(sys.executable)
    wd = str(watchdog_path())
    proj = str(ROOT)
    # 日志放 `data/logs/`（运行数据该在的地方）；**入口脚本**不再放 data 里（见 `autostart_dir()`）。
    # ⚠️ 这里**不许 mkdir**：`--dry-run` 也会调用本函数来打印内容，
    #    而"dry-run 一个文件都不写"是既有判据（test_autostart.py 抓过我一次）。
    #    目录由生成的脚本自己在启动时建（下面那行 New-Item / mkdir -p）。
    logdir = data_dir() / "logs"
    log = str(logdir / "autostart.log")
    if os.name == "nt":
        return ("# 由 tools/autostart.py 生成：登录后拉起看门狗（它再拉起平台服务）\n"
                "$ErrorActionPreference = 'Continue'\n"
                "Set-Location -LiteralPath '%s'\n"
                # 日志目录可能还没建（换过数据目录/清过数据）⇒ 先建，别让日志静默丢失
                "New-Item -ItemType Directory -Force -Path '%s' | Out-Null\n"
                "Add-Content -LiteralPath '%s' -Value ('[' + (Get-Date -Format s) + '] launcher start')\n"
                "& '%s' -u '%s' --daemon --interval 30 2>&1 | Out-File -LiteralPath '%s' -Append -Encoding utf8\n"
                "Add-Content -LiteralPath '%s' -Value ('[' + (Get-Date -Format s) + '] launcher exit code=' + $LASTEXITCODE)\n"
                % (proj.replace("'", "''"), str(logdir).replace("'", "''"),
                   log.replace("'", "''"), py.replace("'", "''"),
                   wd.replace("'", "''"), log.replace("'", "''"), log.replace("'", "''")))
    return ("#!/usr/bin/env bash\n"
            "# 由 tools/autostart.py 生成：登录后拉起看门狗（它再拉起平台服务）\n"
            'cd "%s" || exit 1\n'
            'mkdir -p "%s"\n'
            'echo "[$(date)] launcher start" >> "%s"\n'
            '"%s" -u "%s" --daemon --interval 30 >> "%s" 2>&1\n'
            'echo "[$(date)] launcher exit=$?" >> "%s"\n'
            % (proj, logdir, log, py, wd, log, log))


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


def _shortcut_args(sfile: Path) -> str:
    """读回快捷方式的**参数**（自启的真正落点常常在参数里，例如 `-File "<脚本>"`）。"""
    if os.name != "nt":
        return ""
    ps = ("$s=(New-Object -ComObject WScript.Shell).CreateShortcut('%s');"
          "Write-Output $s.Arguments" % str(sfile).replace("'", "''"))
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=30)
        return (r.stdout or "").strip()
    except Exception:                                       # noqa: BLE001
        return ""


_QUOTED_PATH = re.compile(r'"([A-Za-z]:\\[^"]+)"')


def _ps1_parses(path: Path) -> tuple:
    """入口 `.ps1` 到底**能不能被 PowerShell 解析**（真解析，不是查文本）。

    返回 (ok, 说明)。没有 PowerShell 时返回 (True, "（本机没 PowerShell，未做解析校验）")——
    那是"没做成"，所以在 detail 里如实带上这句话，别当成绿。
    """
    exe = shutil.which("powershell") or shutil.which("pwsh")
    if not exe:
        return True, "（本机没 PowerShell，未做解析校验）"
    ps = ("$t = Get-Content -Raw -LiteralPath '%s'; $e = $null; "
          "[void][System.Management.Automation.Language.Parser]::ParseInput($t, [ref]$null, [ref]$e); "
          "if ($e.Count -gt 0) { Write-Output $e[0].Message; exit 1 } else { exit 0 }"
          % str(path).replace("'", "''"))
    try:
        r = subprocess.run([exe, "-NoProfile", "-NonInteractive", "-Command", ps],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=120)
    except Exception as e:                                    # noqa: BLE001 — 解析器起不来要说
        return True, "（解析校验没跑成：%s）" % e
    if r.returncode == 0:
        return True, "入口脚本可被 PowerShell 解析"
    return False, ((r.stdout or "") + (r.stderr or "")).strip()[:160] or "解析器返回非 0"


def _paths_in_args(args: str) -> list:
    """从快捷方式参数里挑出**真实存在的绝对路径候选**（-File / --daemon 的参数等）。"""
    return [m.group(1) for m in _QUOTED_PATH.finditer(args or "")]


def _stale_artifacts() -> list:
    """**两个目录**（新落点 + 老落点）里当前不该有的旧入口文件。

    两件事都踩过：
    · `data/autostart/ac_watchdog.cmd`（旧版生成的，还是 **UTF-8 无 BOM**）——
      `.cmd` 由 cmd.exe 按 ANSI/GBK 读脚本，里面那句 `cd /d "D:\\…"` 的中文路径会被读坏，
      **一旦有人以为"还有个 cmd 入口"去双击，它一定失败**；
    · 搬家后老位置残留的 `ac_watchdog.ps1`（指向旧 data 目录）—— 留着会让人以为"入口在 data 里还活着"。
    """
    out = []
    keep = launcher_path().resolve()
    for d in {autostart_dir(), legacy_launcher_path().parent}:
        if not d.is_dir():
            continue
        for f in d.iterdir():
            if not f.is_file() or f.suffix.lower() not in (".cmd", ".ps1", ".vbs", ".sh"):
                continue
            if ".old_" in f.name:
                continue                                  # 已经标过的历史残留，不再刷
            try:
                if f.resolve() == keep:
                    continue
            except OSError:                                   # noqa: BLE001 — 有意静默：resolve 失败＝文件刚被删，跳过即可（下一次扫描自然消失）
                pass
            out.append(str(f))
    return out


def status(startup: str = "", unit_dir: str = "") -> dict:
    sup = os.name == "nt" or sys.platform.startswith("linux")
    info = {"supported": sup, "platform": platform.system(), "installed": False,
            "where": "", "detail": "", "launcher": str(launcher_path()),
            "ok": False, "broken": [], "stale": _stale_artifacts()}
    if os.name == "nt":
        target = Path(startup or startup_dir()) / SHORTCUT_NAME
        task = _task_state()
        info["where"] = str(target)
        info["task"] = task
        if target.exists() or task.get("installed"):
            info["installed"] = True
            bits = []
            if target.exists():
                tgt = _shortcut_target(target)
                args = _shortcut_args(target)
                # ★2026-10-23：只报"指向 powershell.exe"没有任何用 —— 真正会执行的脚本在**参数**里。
                #   原来 `status` 就停在这一步：仓库搬家 / 换了 AC_DATA_DIR / data 被清掉之后，
                #   快捷方式还在、`status` 照样说"已装"，登录时却什么都没起（**看起来成功**）。
                script = ""
                for cand in _paths_in_args(args):
                    if cand.lower().endswith((".ps1", ".py", ".vbs", ".cmd")):
                        script = cand
                        break
                bits.append("启动文件夹快捷方式（%s%s）"
                            % (tgt or "?", ("，脚本 %s" % script) if script else ""))
                if script:
                    sp = Path(script)
                    if sp.exists():
                        bits.append("脚本可达")
                    else:
                        info["broken"].append(script)
                        bits.append("★脚本**不存在**：%s" % script)
                else:
                    info["broken"].append("参数里找不到脚本路径")
                    bits.append("★参数里找不到脚本路径（登录后大概什么都不会起）")
            if task.get("installed"):
                bits.append("计划任务 %s（%s）" % (TASK_NAME, task.get("state") or "已建"))
            info["detail"] = "；".join(bits)
            # ★2026-10-23：入口脚本里写的是**本机绝对路径**（解释器 + 看门狗脚本）。
            #   解释器换位置（换 Python / 删 uv 缓存）或仓库搬家，登录后就是"什么都没起"，
            #   而原来的 status 只看快捷方式在不在 ⇒ 照样报"已装"。这里逐个验。
            lp = launcher_path()
            if lp.is_file():
                text = lp.read_text(encoding="utf-8", errors="replace")
                deps = [p for p in _paths_in_args(text.replace("'", '"'))
                        if p.lower().endswith((".exe", ".py"))]
                info["entry_deps"] = deps
                miss = [p for p in deps if not Path(p).exists()]
                if miss:
                    info["broken"].extend(miss)
                    bits.append("★入口脚本里的依赖**不存在**：%s" % "、".join(miss))
                elif deps:
                    bits.append("入口脚本的解释器/看门狗都在（%d 个）" % len(deps))
                # ★2026-10-23：**能解析**才算能跑。BOM 丢了/文件被改坏 ⇒ 登录时 ParserError，
                #   而"文件存在 + 依赖都在"照样会让人以为没事（PS 5.1 对无 BOM 的中文脚本按 ANSI 读）。
                ok_parse, why_parse = _ps1_parses(lp) if os.name == "nt" else (True, "")
                if not ok_parse:
                    info["broken"].append("入口脚本解析不过")
                    bits.append("★入口脚本**解析不过**（登录时一定失败）：%s" % why_parse)
                elif why_parse:
                    bits.append(why_parse)
            elif info["installed"]:
                info["broken"].append(str(lp))
                bits.append("★入口脚本不存在：%s" % lp)
            info["detail"] = "；".join(bits)
            info["ok"] = bool(info["installed"] and not info["broken"])
        else:
            info["detail"] = "启动文件夹和计划任务里都没有 ⇒ 重启后平台不会自己起"
    elif sys.platform.startswith("linux"):
        unit = Path(unit_dir or startup_dir()) / UNIT_NAME
        info["where"] = str(unit)
        if unit.exists():
            info["installed"] = True
            enabled = subprocess.run(["systemctl", "--user", "is-enabled", UNIT_NAME],
                                     capture_output=True, text=True).stdout.strip() \
                if _has_systemctl() else ""
            info["detail"] = ("unit 存在；systemctl --user is-enabled = %s" % (enabled or "未知"))
            info["ok"] = True
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


# ── Windows 计划任务（比"启动文件夹"可靠：由 Task Scheduler 拥有，不随某个会话/父进程死）──
TASK_NAME = "AC-Platform-Watchdog"


def _task_state() -> dict:
    """计划任务在不在、什么状态（读不到就如实说读不到）。"""
    try:
        r = subprocess.run(["schtasks", "/Query", "/TN", TASK_NAME, "/FO", "LIST"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
    except Exception as e:                                  # noqa: BLE001
        return {"installed": False, "state": "", "error": "%s: %s" % (type(e).__name__, e)}
    if r.returncode != 0:
        return {"installed": False, "state": ""}
    state = ""
    for line in (r.stdout or "").splitlines():
        if "Status" in line or "状态" in line:
            state = line.split(":", 1)[-1].strip() or line.split("：", 1)[-1].strip()
    return {"installed": True, "state": state}


def _ps_invocation(script: Path) -> str:
    """Windows 启动入口统一走 PowerShell（能正确处理中文路径；`.cmd` 的重定向不行，见 launcher_content）。"""
    return ('powershell.exe -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "%s"'
            % script)


def enable_task(*, dry_run: bool = False) -> int:
    """建一个**登录时运行**的计划任务（Windows）。任务名固定，便于 `disable` 干净删除。"""
    lp = launcher_path()
    cmd = _ps_invocation(lp)
    print("① 启动器：%s" % lp)
    if dry_run:
        print("② 计划任务 %s：schtasks /Create /F /SC ONLOGON /TN %s /TR %s"
              % (TASK_NAME, TASK_NAME, cmd))
        print("\n（--dry-run：什么都没写）")
        return 0
    lp.parent.mkdir(parents=True, exist_ok=True)
    # ★2026-10-19（本机 pytest 抓到）：这里原先写 `encoding="utf-8"`（**无 BOM**），
    #   而启动器是含中文的 .ps1 ⇒ 别人的 Windows PowerShell 5.1 会按 ANSI 读、直接 ParserError
    #   ⇒ **开机自启在别人机器上静默失败**（正是"只在我这台机器成立"那类缺陷）。
    #   守卫 `agent_community/tests/test_ps1_bom.py` 会扫 data/autostart 下所有 .ps1。
    lp.write_text(launcher_content(), encoding="utf-8-sig", newline="")

    args = ["schtasks", "/Create", "/F", "/SC", "ONLOGON", "/TN", TASK_NAME, "/TR", cmd,
            "/RL", "LIMITED"]
    r = subprocess.run(args, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=60)
    out = ((r.stdout or "") + (r.stderr or "")).strip()
    if r.returncode != 0:
        print("★建计划任务失败：%s" % out[-300:])
        low = out.lower()
        if "access is denied" in low or "拒绝访问" in out:
            print("   原因：建**登录时运行**的计划任务需要管理员权限（本会话没提升）。")
            print("   办法二选一：① 以管理员身份重跑 `python tools/autostart.py enable --mode task`；"
                  "② 用默认的启动文件夹方案（`--mode startup`，不需要管理员，登录后同样会起）。")
        return 1
    print("② 计划任务已建：%s（登录时自动运行）" % TASK_NAME)
    return 0


def disable_task() -> int:
    r = subprocess.run(["schtasks", "/Delete", "/F", "/TN", TASK_NAME], capture_output=True,
                       text=True, encoding="utf-8", errors="replace", timeout=60)
    ok = r.returncode == 0
    print("计划任务 %s：%s" % (TASK_NAME, "已删除" if ok else "本来就没有/删不掉"))
    return 0


def run_task_now() -> int:
    """立刻运行一次那个计划任务（等价于"下次登录时会发生的事"，但不用等）。"""
    r = subprocess.run(["schtasks", "/Run", "/TN", TASK_NAME], capture_output=True,
                       text=True, encoding="utf-8", errors="replace", timeout=60)
    ok = r.returncode == 0
    print("计划任务 %s：%s" % (TASK_NAME, "已触发" if ok else "触发失败：%s"
                              % ((r.stdout or "") + (r.stderr or "")).strip()[-200:]))
    return 0 if ok else 1


def enable(*, dry_run: bool = False, startup: str = "", unit_dir: str = "") -> int:
    if os.name not in ("nt",) and not sys.platform.startswith("linux"):
        print("★不支持这个平台的自启安装（如实说，不假装成功）")
        return 1
    lp = launcher_path()
    print("① 启动器：%s" % lp)
    print(launcher_content().rstrip())
    if os.name == "nt":
        target = Path(startup or startup_dir()) / SHORTCUT_NAME
        print("② 快捷方式：%s → %s" % (target, _ps_invocation(lp)))
    else:
        target = Path(unit_dir or startup_dir()) / UNIT_NAME
        print("② systemd user unit：%s" % target)
        print(unit_content().rstrip())
    if dry_run:
        print("\n（--dry-run：什么都没写）")
        return 0

    lp.parent.mkdir(parents=True, exist_ok=True)
    # Windows 侧必须带 **UTF-8 BOM**：PowerShell 5.1 没有 BOM 时会按 ANSI 读，中文路径就废了
    lp.write_text(launcher_content(), encoding=("utf-8-sig" if os.name == "nt" else "utf-8"), newline="")
    # ★2026-10-23 迁移：老位置（`data/autostart/`）里的入口改名留证，别让它继续冒充"现行入口"。
    legacy = legacy_launcher_path()
    if legacy.exists() and legacy.resolve() != lp.resolve():
        try:
            legacy.rename(legacy.with_name(legacy.name + ".old_" + time.strftime("%Y%m%d")))
            print("   老入口已改名留证：%s" % legacy)
        except OSError as e:                                  # noqa: BLE001 — 说清楚，但不挡着装新的
            print("   ⚠️ 老入口没改名（%s）；它已不再被使用，建议手工删掉" % e)
    target.parent.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        ps = ("$w=New-Object -ComObject WScript.Shell;"
              "$s=$w.CreateShortcut('%s');$s.TargetPath='powershell.exe';"
              "$s.Arguments='-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File \"%s\"';"
              "$s.WindowStyle=7;$s.Description='外端Agent生产合作社 看门狗自启';$s.Save()"
              % (str(target).replace("'", "''"), str(lp).replace("'", "''")))
        r = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=60)
        if r.returncode != 0:
            print("★创建快捷方式失败：%s" % ((r.stdout or "") + (r.stderr or ""))[-300:])
            return 1
        print("\n已装：下次登录会自动起看门狗（它再拉起平台服务）。")
        print("立即生效：`powershell -NoProfile -ExecutionPolicy Bypass -File \"%s\"`" % lp)
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
    for name in ("status", "check", "enable", "disable", "run-now"):
        s = sub.add_parser(name)
        s.add_argument("--json", action="store_true")
        s.add_argument("--dry-run", action="store_true")
        s.add_argument("--startup", default="", help="覆盖启动文件夹（测试用）")
        s.add_argument("--unit-dir", default="", help="覆盖 systemd user 目录（测试用）")
        s.add_argument("--mode", default="both", choices=("startup", "task", "both"),
                       help="Windows：启动文件夹 / 计划任务 / 两者都装（默认 both；计划任务更可靠）")
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
        if info.get("broken"):
            print("  ★断链：%s" % "；".join(info["broken"]))
            print("     修：`python tools/autostart.py enable`（重写入口并重指快捷方式）")
        for s in info.get("stale") or []:
            print("  · 旧入口残留（当前模式不用，建议删）：%s" % s)
        return 0
    if a.cmd == "check":
        # ★2026-10-23：给"登录后到底会不会自己起"一个**可脚本化**的判据。
        #   退出码：0=装好且入口可达；1=装了但断链（红）；2=没装（没做成，不算通过）
        info = status(a.startup, a.unit_dir)
        if a.json:
            print(json.dumps(info, ensure_ascii=False))
        if info.get("broken"):
            print("[FAIL] 开机自启断链：%s ⇒ 登录后不会起（修：python tools/autostart.py enable）"
                  % "；".join(info["broken"]))
            return 1
        if not info["installed"]:
            print("[没做成] 开机自启没装 ⇒ 重启/登录后平台不会自己起（修：python tools/autostart.py enable）")
            return 2
        if not info.get("ok"):
            print("[FAIL] 开机自启状态不对劲：%s" % info["detail"])
            return 1
        print("[OK] 开机自启已装且入口可达：%s" % info["detail"])
        return 0
    if a.cmd == "enable":
        if os.name == "nt" and a.mode in ("task", "both"):
            rc = enable_task(dry_run=a.dry_run)
            if rc or a.mode == "task":
                return rc
        return enable(dry_run=a.dry_run, startup=a.startup, unit_dir=a.unit_dir)
    if a.cmd == "run-now":
        return run_task_now()
    # disable：两种都删（幂等）
    rc = 0
    if os.name == "nt":
        rc = disable_task()
    return disable(startup=a.startup, unit_dir=a.unit_dir) or rc


if __name__ == "__main__":
    raise SystemExit(main())
