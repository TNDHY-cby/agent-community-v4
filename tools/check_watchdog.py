# -*- coding: utf-8 -*-
"""判据：**发布面看门狗真能把死掉的服务救活**（活体实验，隔离实例，不碰你正在跑的那个）。

老板 2026-10-18 留的欠账：`deploy/` 的看门狗不在发布面 ⇒ 陌生人包里服务一崩就再也起不来。
`tools/watchdog.py` 补上了这只，但"它到底能不能救活"必须**真跑**才算数 —— 本文件就是那一步。

```bash
python tools/check_watchdog.py            # 约 20–40 秒
```

四格判据：
  ① 起初是挂的（`--check` = 1）；
  ② 看门狗自己把服务**拉起来**（`--check` = 0）；
  ③ **手动杀掉**平台进程模拟崩溃 ⇒ 看门狗**又救活**（这是关键：只验第 ② 步等于只验了"能起一次"）；
  ④ 守护日志里留下"重启"记录（失败要说话）。
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent.parent
TOOL = ROOT / "tools" / "watchdog.py"


def free_port() -> int:
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])
    finally:
        s.close()


def _check(env) -> int:
    return subprocess.run([sys.executable, str(TOOL), "--check"], cwd=str(ROOT), env=env,
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace").returncode


def kill_listener(port: int) -> int:
    """按端口杀掉监听进程 —— 走 `platform/procprobe.py`（平台侧唯一一份进程探测真相）。"""
    sys.path.insert(0, str(ROOT))
    from agent_community.platform import procprobe
    n = 0
    for pid in procprobe.listening_pids(port) or []:
        if pid == os.getpid():
            continue
        try:
            procprobe.kill_pid(pid)
            n += 1
        except Exception:                                    # noqa: BLE001
            pass
    return n


def main() -> int:
    port = free_port()
    data = Path(tempfile.mkdtemp(prefix="ac_wd_judge_"))
    env = {**os.environ, "AC_PORT": str(port), "AC_DATA_DIR": str(data),
           "AC_BASE_URL": "http://127.0.0.1:%d" % port, "PYTHONPATH": str(ROOT)}
    print("=" * 82)
    print("看门狗救活判据（隔离实例：端口 %d，数据目录 %s）" % (port, data))
    print("=" * 82)

    fails = 0

    def cell(name: str, ok: bool, detail: str = "") -> None:
        nonlocal fails
        print("   %-28s %-6s %s" % (name, "OK" if ok else "★FAIL", detail))
        fails += 0 if ok else 1

    cell("① 起初是挂的", _check(env) == 1)

    logf = open(data / "watchdog_stdout.log", "w", encoding="utf-8", errors="replace")
    wd = subprocess.Popen([sys.executable, "-u", str(TOOL), "--daemon", "--interval", "3"],
                          cwd=str(ROOT), env=env, stdout=logf, stderr=subprocess.STDOUT)
    try:
        deadline = time.time() + 60
        while time.time() < deadline and _check(env) != 0:
            time.sleep(1.0)
        cell("② 看门狗把服务拉起来", _check(env) == 0)

        killed = kill_listener(port)
        deadline = time.time() + 60
        while time.time() < deadline and _check(env) != 0:
            time.sleep(1.0)
        cell("③ 崩溃后又被救活", killed > 0 and _check(env) == 0, "杀掉 %d 个监听进程" % killed)
    finally:
        wd.terminate()
        try:
            wd.wait(timeout=10)
        except Exception:                                    # noqa: BLE001
            wd.kill()
        logf.close()

    log_file = data / "logs" / "watchdog.log"
    text = log_file.read_text(encoding="utf-8", errors="replace") if log_file.exists() else ""
    cell("④ 日志里留下重启记录", "重启" in text, "%d 行" % len(text.splitlines()))

    kill_listener(port)          # 收尾，别留孤儿进程
    print("-" * 82)
    if fails:
        print("结论：**有 %d 格失败**" % fails)
        print("--- 守护日志尾 ---")
        print("\n".join(text.splitlines()[-12:]))
    else:
        print("结论：**看门狗真能救活服务（能起 + 崩了能再起）**")
    return 0 if not fails else 1


if __name__ == "__main__":
    raise SystemExit(main())
