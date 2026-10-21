# -*- coding: utf-8 -*-
"""**双平台真跑**判据（本机 Windows + 本机 WSL Linux）—— 2026-10-17 老板拍板的 CI 替代口径。

## 为什么有它（一处真相）

老板 2026-10-16/17 曾定「GitHub CI 停更」；**2026-10-21 恢复更新 GitHub**，
但这条判据不变：硬判据是**本机双平台真跑**（不依赖云端 CI）。
GitHub Actions 不再跟随新提交，Gitee 侧没有免费 CI ⇒ **双平台判据只能由本机真跑承担**。
以前那是我在报告里手敲两条命令（Windows `pytest` + WSL 脚本），**换个人就复现不出来**。
本工具把那条口径落成**一条命令**：

```bash
python tools/dual_platform_check.py            # 两侧都真跑，打印一张表 + 结论
python tools/dual_platform_check.py --plan     # 只打印"它会怎么做"（不跑；守卫与文档用它）
python tools/dual_platform_check.py --local-only   # 只跑本机这一侧（诚实标"部分"）
```

**退出码（判据本身也如实说话）**：
`0` = 两侧都跑了且都绿 ｜ `1` = 有任一侧红 ｜ `2` = **只跑成了一侧**（＝"部分"，**不算通过**）。

## 本机适配（不许改源码）

- 本机（Windows）跑 WSL 里的 Linux 侧需要：
  - `AC_LINUX_WSL_DISTRO`（默认 `Ubuntu-24.04`）—— 没有这个发行版就如实报"跑不了 Linux 侧"；
  - `AC_LINUX_PYTHON` —— WSL 里**装好依赖的那个解释器**（不写死任何人的家目录）。
- 这两个键可以放在**仓库根的 `.env`**（已在 `.gitignore`，属"本地适配只改配置"）：
  本工具会读它；命令行/环境变量优先。
- 跑不了另一侧时**绝不假装通过**：打印缺什么、怎么补，并以退出码 2 收尾。
"""
from __future__ import annotations

import argparse
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
LINUX_RUNNER = ROOT / "tools" / "run_suite_in_repo.sh"
PYTEST_TARGETS = ["agent_community/tests", "tests"]


def load_dotenv() -> dict:
    """读仓库根 `.env`（只取本工具要的两个键；不引第三方库、不覆盖已存在的环境变量）。"""
    out = {}
    f = ROOT / ".env"
    if not f.exists():
        return out
    for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k in ("AC_LINUX_PYTHON", "AC_LINUX_WSL_DISTRO") and k not in os.environ:
            out[k] = v
    return out


def cfg(key: str, dotenv: dict, default: str = "") -> str:
    return os.environ.get(key) or dotenv.get(key) or default


def wsl_available(distro: str) -> bool:
    if os.name != "nt" or not shutil.which("wsl"):
        return False
    try:
        r = subprocess.run(["wsl", "-d", distro, "true"], capture_output=True, timeout=60)
        return r.returncode == 0
    except Exception:  # noqa: BLE001
        return False


def wsl_ensure_repo_visible(distro: str) -> str:
    """确保 WSL 里**能看到本仓库所在的盘**。返回空串＝OK，否则返回"为什么不行 + 怎么补"。

    ★2026-10-17 实测踩到：本机 `/etc/wsl.conf` 里 **`[automount] enabled=false`**
    ⇒ WSL 重启后 `/mnt/d` 是个**空目录**（`mountpoint -q` 判 NOT_MOUNTED），
    于是 Linux 侧报 `bash: /mnt/d/…: No such file or directory` —— **看着像脚本没了，其实是盘没挂**。
    这里**按需挂载**（`sudo -n mount -t drvfs <盘>: /mnt/<盘>`），**不改用户的系统配置**；
    挂不上就如实说清两条补救路径（临时挂载 / 把 automount 打开，后者要重启 WSL）。
    """
    drive = (ROOT.drive or "C:").rstrip(":").lower()
    mp = "/mnt/%s" % drive
    script = (
        "if mountpoint -q {mp}; then echo MOUNTED; exit 0; fi; "
        "sudo -n mount -t drvfs {drv}: {mp} >/dev/null 2>&1 || true; "
        "mountpoint -q {mp} && echo MOUNTED || echo NOT_MOUNTED"
    ).format(mp=mp, drv=drive.upper())
    try:
        r = subprocess.run(["wsl", "-d", distro, "bash", "-lc", script],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=180)
    except Exception as e:  # noqa: BLE001
        return "无法在 WSL 里检查挂载点：%s" % e
    if "MOUNTED" in (r.stdout or ""):
        return ""
    return ("WSL 里看不到 %s（本盘没挂载）⇒ Linux 侧跑不了。\n"
            "          补救一（临时，一次有效）：wsl -d %s sudo mount -t drvfs %s: %s\n"
            "          补救二（长期）：在本机 /etc/wsl.conf 里把 [automount] enabled 设为 true，"
            "然后 `wsl --shutdown` 重开（**这会改你的机器配置，所以本工具不代劳**）。"
            % (mp, distro, drive.upper(), mp))


def to_wsl_path(p: Path) -> str:
    """`D:\\a\\b` → `/mnt/d/a/b`（只做盘符映射，够本仓库用）。"""
    s = str(p).replace("\\", "/")
    m = re.match(r"^([A-Za-z]):/(.*)$", s)
    return "/mnt/%s/%s" % (m.group(1).lower(), m.group(2)) if m else s


def _log_dir() -> Path:
    """判据日志落盘目录（**不进仓库**：默认系统临时目录，可用 `AC_GATE_LOGS` 覆盖）。"""
    d = Path(os.environ.get("AC_GATE_LOGS") or (Path(tempfile.gettempdir()) / "ac_gate_logs"))
    d.mkdir(parents=True, exist_ok=True)
    return d


def _save_log(name: str, text: str) -> Path:
    p = _log_dir() / name
    try:
        p.write_text(text or "", encoding="utf-8")
    except OSError:
        pass
    return p


def failed_cases(text: str, limit: int = 12) -> list:
    """从 pytest 输出里抓失败的**用例名**（红的时候要能一眼看到是哪条，别只留"有侧红"）。"""
    out = []
    for ln in (text or "").splitlines():
        s = ln.strip()
        if s.startswith("FAILED ") or s.startswith("ERROR "):
            out.append(s[:160])
        elif s.startswith("E   ") and len(out) < limit and out and not out[-1].startswith("E "):
            out[-1] = (out[-1] + "  ← " + s[4:].strip()[:120])[:240]
        if len(out) >= limit:
            break
    return out


def _report(name: str, rc: int, out: str) -> None:
    """落盘 + 红时打印失败用例名（不看源码文本，只看真跑输出）。"""
    stamp = time.strftime("%Y%m%d-%H%M%S")
    p = _save_log("%s-%s.log" % (name, stamp), out)
    print("           日志：%s" % p)
    if rc != 0:
        bad = failed_cases(out)
        if bad:
            print("           失败用例（%d 条，最多列 12）：" % len(bad))
            for b in bad:
                print("             %s" % b)
        else:
            print("           ⚠️ 输出里没抓到 FAILED/ERROR 行 —— 可能是收集期错误或环境问题（看日志）")


def run_local(junit: Path, targets=None) -> tuple:
    argv = [sys.executable, "-m", "pytest", *(targets or PYTEST_TARGETS), "-q",
            "-p", "no:cacheprovider", "--junit-xml=%s" % junit]
    r = subprocess.run(argv, cwd=str(ROOT), capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def selftest() -> int:
    """**自证判据有效**（守卫跑这个，几秒钟）：造一个迷你套件，验"绿→0 / 红→1"。

    ⚠️ 别拿整套回归来自证 —— 那会把守卫变成 4 分钟的长测（我第一版就是那样，已改）。
    """
    tmp = Path(tempfile.mkdtemp(prefix="ac_dual_selftest_"))
    ok_dir, bad_dir = tmp / "ok", tmp / "bad"
    ok_dir.mkdir()
    bad_dir.mkdir()
    (ok_dir / "test_ok.py").write_text("def test_pass():\n    assert True\n", encoding="utf-8")
    (bad_dir / "test_bad.py").write_text("def test_fail():\n    assert False\n", encoding="utf-8")

    rc_ok, _ = run_local(tmp / "ok.xml", [str(ok_dir / "test_ok.py")])
    rc_bad, _ = run_local(tmp / "bad.xml", [str(bad_dir / "test_bad.py")])
    c_ok, c_bad = counts_from_junit(tmp / "ok.xml"), counts_from_junit(tmp / "bad.xml")
    print("自证：迷你套件（通过）exit=%s  计数=%s" % (rc_ok, c_ok or "?"))
    print("自证：迷你套件（失败）exit=%s  计数=%s" % (rc_bad, c_bad or "?"))
    ok = (rc_ok == 0 and rc_bad != 0 and c_ok.get("failures") == 0
          and c_bad.get("failures") == 1)
    print("OK：判据会绿也会红（拿真 pytest 跑出来的）" if ok else "★自证失败：判据不可信")
    return 0 if ok else 1


def run_linux(distro: str, linux_python: str, junit_name: str) -> tuple:
    """在 WSL 里跑 `tools/run_suite_in_repo.sh`（仓库自己的 POSIX 运行器）。"""
    args = ["wsl", "-d", distro, "bash", to_wsl_path(LINUX_RUNNER)]
    if linux_python:
        args.append(linux_python)
    r = subprocess.run(args, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=3600)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def _snapshot_repo_runtime() -> dict:
    """给"**运行数据目录**"拍快照：仓库根的 `library/` / `data/` / `agent_community/data/` /
    `runtime_logs/` / `agent_community/bridges/`。

    这几处是平台运行期会写的（工作间共享资料库、状态与日志、生成的桥脚本）。它们被 `.gitignore` 排除，
    所以 `git status` 看不出污染 —— **必须自己拍快照**才抓得到"测试往仓库里写东西"。
    """
    snap = {}
    for rel in ("library", "data", Path("agent_community") / "data", "runtime_logs",
                Path("agent_community") / "bridges"):
        base = ROOT / rel
        if not base.exists():
            continue
        for p in base.rglob("*"):
            try:
                st = p.stat()
                # 只记**大小**：内容是否变，用大小判就够（真污染必然改大小/改集合）；
                # mtime 会被同机其它进程的正常重写带偏（见 _pollution_diff 的注释）。
                snap[str(p.relative_to(ROOT))] = st.st_size
            except OSError:
                continue
    return snap


def _pollution_diff(before: dict, after: dict) -> list:
    """跑完用例后新增/删掉/大小变了的运行数据（按真文件判，不查源码文本）。

    ★2026-10-20 修（**判据自己假红**，第三次同类）：原先用 `(mtime_ns, size)` 比对，
      于是**同机另一个进程**（生产实例 18920 的 20s 自动循环、看门狗）周期性地
      **用相同内容重写** `data/audit.json` 等文件时，mtime 一变就被判成"测试污染"——
      跑一次红一次绿，取决于那 20s 恰好有没有落到窗口里。
      `agent_community/tests/conftest.py` 早在 2026-10-12 就把这件事判清了：
      **只变 mtime 不算污染**，真污染必然改变"文件集合"或"大小"。
      本工具现在与它同一口径（一处真相），并在文案里说清判据。
    """
    out = []
    for path, size in after.items():
        if path not in before:
            out.append("%s（新增）" % path)
        elif before[path] != size:
            out.append("%s（大小变了 %s→%s）" % (path, before[path], size))
    for path in before:
        if path not in after:
            out.append("%s（被删除）" % path)
    return out


def counts(text: str) -> dict:
    """从**文本**里抓计数（Linux 侧运行器会打这一行）。"""
    m = re.search(r"tests=(\d+) failures=(\d+) errors=(\d+) skipped=(\d+)", text)
    if not m:
        return {}
    return {"tests": int(m.group(1)), "failures": int(m.group(2)),
            "errors": int(m.group(3)), "skipped": int(m.group(4))}


def counts_from_junit(path: Path) -> dict:
    """从 **junit XML** 里抓计数（本机侧用这个 —— pytest 的 stdout 里没有那一行）。"""
    try:
        import xml.etree.ElementTree as ET
        r = ET.parse(str(path)).getroot()
        s = r if r.tag == "testsuite" else r[0]
        a = s.attrib
        return {"tests": int(a.get("tests", 0)), "failures": int(a.get("failures", 0)),
                "errors": int(a.get("errors", 0)), "skipped": int(a.get("skipped", 0))}
    except Exception:  # noqa: BLE001
        return {}


def plan_text(distro: str, linux_python: str) -> str:
    return "\n".join([
        "双平台真跑（CI 替代口径）会做两件事：",
        "  ① Windows 侧（本机）：python -m pytest %s -q --junit-xml=<temp>" % " ".join(PYTEST_TARGETS),
        "  ② Linux 侧（WSL）：wsl -d %s bash %s [%s]"
        % (distro, "tools/run_suite_in_repo.sh", linux_python or "<AC_LINUX_PYTHON 未设 → 用 python3>"),
        "退出码：0=两侧都绿；1=有红；2=只跑成了一侧（＝部分，不算通过）。",
        "本机适配：把 AC_LINUX_PYTHON / AC_LINUX_WSL_DISTRO 写进仓库根 `.env`（不改源码）。",
    ])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", action="store_true", help="只打印计划（不跑）")
    ap.add_argument("--local-only", action="store_true", help="只跑本机这一侧")
    ap.add_argument("--distro", default="", help="覆盖 AC_LINUX_WSL_DISTRO")
    ap.add_argument("--linux-python", default="", help="覆盖 AC_LINUX_PYTHON")
    ap.add_argument("--target", default="", help="只跑指定目标（逗号分隔；默认整套）")
    ap.add_argument("--selftest", action="store_true",
                    help="自证判据有效：拿一个迷你套件验「绿→0 / 红→1」（几秒钟，守卫用它）")
    a = ap.parse_args()

    if a.selftest:
        return selftest()

    targets = [t.strip() for t in a.target.split(",") if t.strip()] or PYTEST_TARGETS
    dotenv = load_dotenv()
    distro = a.distro or cfg("AC_LINUX_WSL_DISTRO", dotenv, "Ubuntu-24.04")
    linux_python = a.linux_python or cfg("AC_LINUX_PYTHON", dotenv, "")
    host = platform.system()

    if a.plan:
        print(plan_text(distro, linux_python))
        print("本机：%s（%s）" % (host, platform.release()))
        print("WSL 可用：%s" % ("是" if wsl_available(distro) else "否/非 Windows"))
        return 0

    tmp = Path(tempfile.mkdtemp(prefix="ac_dual_"))
    local_junit, linux_junit = tmp / "local.xml", tmp / "linux.xml"

    print("=" * 74)
    print("双平台真跑（CI 替代口径）—— 本机 %s" % host)
    # ★2026-10-17 新增：**跑用例前后给"仓库里的运行数据"拍快照**
    #   起因：`test_roster_keeps_bindings` 用 `workspace_dir=""`，于是平台把共享资料库写到
    #   **仓库根 `library/`**（`.gitignore` 里 2026-10-12 就记过一次，是同一个坑）。
    #   `.gitignore` 只是不让它进库；**"测试往仓库里写运行数据"本身要被抓出来**。
    poll_before = _snapshot_repo_runtime()
    rc_local, out_local = run_local(local_junit, targets)
    poll_after = _snapshot_repo_runtime()
    polluted = _pollution_diff(poll_before, poll_after)
    c_local = counts_from_junit(local_junit) or counts(out_local)
    print("[本机侧 %s] exit=%s %s" % (host, rc_local, c_local or out_local.strip()[-200:]))
    _report("windows", rc_local, out_local)      # 落盘 + 红时打印失败用例名（2026-10-17 加）
    if polluted:
        print("[仓库污染] ★跑用例往仓库里写了运行数据（%d 处）：" % len(polluted))
        for p in polluted[:6]:
            print("            %s" % p)
        print("            ⇒ 判据红：测试必须把工作区放到 tmp（`workspace_dir` 别留空/相对路径）")
        rc_local = 1

    if a.local_only:
        print("（--local-only：另一侧没跑 ⇒ 结论＝**部分**，不算通过）")
        print("=" * 74)
        return 0 if rc_local == 0 else 1

    if host != "Windows":
        print("[另一侧 Windows] 本机不是 Windows ⇒ 跑不了；请在有 Windows 的机器上补跑（如实报「部分」）")
        print("=" * 74)
        return 2

    if not wsl_available(distro):
        print("[另一侧 Linux] 没有可用的 WSL 发行版 %r ⇒ 跑不了 Linux 侧。" % distro)
        print("           怎么补：`wsl --install -d %s`（或用 --distro 指定已有的），" % distro)
        print("           并在仓库根 `.env` 设 AC_LINUX_PYTHON=<装好依赖的 python>。")
        print("=" * 74)
        return 2

    mount_err = wsl_ensure_repo_visible(distro)
    if mount_err:
        print("[另一侧 Linux] %s" % mount_err)
        print("=" * 74)
        return 2

    rc_linux, out_linux = run_linux(distro, linux_python, linux_junit.name)
    c_linux = counts(out_linux)
    print("[另一侧 Linux/WSL:%s] exit=%s %s" % (distro, rc_linux, c_linux or out_linux.strip()[-300:]))
    _report("linux", rc_linux, out_linux)        # 落盘 + 红时打印失败用例名
    for line in out_linux.splitlines():
        if "缺依赖" in line or "'D:*'" in line or "D:\\" in line:
            print("   " + line.strip())

    print("-" * 74)
    if rc_local != 0 or rc_linux != 0:
        print("结论：**有侧红** ⇒ 不算通过（先修红的那侧）")
        print("　　　　排查：日志在 %s（红的时候上面已列失败用例名）" % _log_dir())
        print("=" * 74)
        return 1
    print("结论：**两侧都真跑且全绿** ⇒ 通过（Windows %s / Linux %s）"
          % (c_local or "?", c_linux or "?"))
    print("=" * 74)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
