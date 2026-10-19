# -*- coding: utf-8 -*-
"""判据：**受控重启端点 `POST /api/admin/restart`**（V58，老板 2026-10-19「最优解就行」拍板）。

```bash
python tools/admin_restart_check.py     # 约 60~90 秒（含"等它自己回来"）
```

## 为什么单列

这个端点是**唯一能"停自己"的入口**。它的错法全是致命的：
没有看门狗时放行 ⇒ 平台**永久宕机**；判断看门狗时误认别人的 ⇒ 重启后自己再也起不来。
所以判据必须证明**该拒的都拒了、该活的真活了**，而不是"接口返回 200"。

## 五格判据

| 格 | 判据 |
|---|---|
| ① | **没有看门狗时被拒（409）**，且服务**仍然活着**（再打 `/api/status` 仍 200）——最要紧：不许把平台弄死 |
| ② | 缺 `confirm` ⇒ 400（不静默执行） |
| ③ | 非本机来源（`Origin: https://evil.example`）⇒ 403 |
| ④ | 有看门狗时 ⇒ `restart_scheduled=true`，且服务在 ≤90s 内**自己回来**（`/api/status` 200、版本不变） |
| ⑤ | 审计有 `system.restart` |
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent.parent
WATCHDOG = ROOT / "tools" / "watchdog.py"


def free_port() -> int:
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])
    finally:
        s.close()


def _clean_env(**over) -> dict:
    keep = ("PATH", "PYTHON", "SYSTEM", "WINDIR", "TEMP", "TMP", "HOME", "USER",
            "APPDATA", "LOCALAPPDATA", "COMSPEC", "PATHEXT", "LANG", "LC_", "LD_LIBRARY")
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("AC_") and k.upper().startswith(keep)}
    env.update({k: str(v) for k, v in over.items()})
    return env


def req(base: str, path: str, body=None, hdr=None, timeout=20):
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if hdr:
        headers.update(hdr)
    r = urllib.request.Request(base + path, data=data,
                               method="POST" if body is not None else "GET", headers=headers)
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(raw)
        except Exception:                                    # noqa: BLE001
            return e.code, {"raw": raw[:300]}
    except Exception as e:                                   # noqa: BLE001
        return 0, {"error": "%s: %s" % (type(e).__name__, str(e)[:120])}


def main() -> int:
    port = free_port()
    base = "http://127.0.0.1:%d" % port
    data_dir = Path(tempfile.mkdtemp(prefix="ac_restart_"))
    env = _clean_env(AC_PORT=port, AC_DATA_DIR=data_dir, PYTHONPATH=str(ROOT),
                     PYTHONIOENCODING="utf-8", AC_PYTHON=str(sys.executable),
                     AC_BASE_URL=base)
    fails = 0

    def cell(name: str, ok: bool, detail: str = "") -> None:
        nonlocal fails
        print("   %-46s %-6s %s" % (name, "OK" if ok else "★FAIL", detail))
        fails += 0 if ok else 1

    def up(timeout: float = 40.0) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if req(base, "/api/status", timeout=3)[0] == 200:
                return True
            time.sleep(0.6)
        return False

    print("=" * 90)
    print("受控重启端点判据（实例 %s，数据目录 %s）" % (base, data_dir))
    print("=" * 90)
    srv = subprocess.Popen([sys.executable, "-m", "agent_community.platform.server"],
                           cwd=str(ROOT), env=env,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    wd = None
    wlog = None
    try:
        if not up(60):
            print("★实例没起来")
            return 2

        # ① 没有看门狗 ⇒ 409，且服务仍活着
        st1, b1 = req(base, "/api/admin/restart", {"confirm": True})
        alive = up(8)
        cell("① 没有看门狗时被拒（409）且服务仍活着",
             st1 == 409 and "看门狗" in str((b1 or {}).get("error") or "") and alive,
             "HTTP %s ｜ 服务仍活=%s ｜ %s" % (st1, alive,
                                              str((b1 or {}).get("error"))[:40]))

        # ② 缺 confirm ⇒ 400
        st2, b2 = req(base, "/api/admin/restart", {})
        cell("② 缺 confirm ⇒ 400（不静默执行）",
             st2 == 400 and "confirm" in str((b2 or {}).get("error") or ""),
             "HTTP %s" % st2)

        # ③ 非本机来源 ⇒ 403
        st3, b3 = req(base, "/api/admin/restart", {"confirm": True},
                      {"Origin": "https://evil.example"})
        cell("③ 非本机来源 ⇒ 403", st3 == 403,
             "HTTP %s ｜ %s" % (st3, str((b3 or {}).get("error"))[:36]))

        # ④ 起看门狗 ⇒ 真能重启并自己回来
        wlog = open(data_dir / "watchdog_stdout.log", "w", encoding="utf-8", errors="replace")
        wd = subprocess.Popen([sys.executable, "-u", str(WATCHDOG), "--daemon", "--interval", "3"],
                              cwd=str(ROOT), env=env, stdout=wlog, stderr=subprocess.STDOUT)
        # 等看门狗把 pid 记录写出来（写方口径：<AC_DATA_DIR>/logs/watchdog.pid）
        pidf = data_dir / "logs" / "watchdog.pid"
        deadline = time.time() + 45
        while time.time() < deadline:
            try:
                if pidf.is_file() and pidf.read_text(encoding="utf-8", errors="replace").strip():
                    break
            except OSError:          # Windows 上看门狗正在写 ⇒ 短暂占用，重试即可
                pass
            time.sleep(0.8)
        st4, b4 = req(base, "/api/admin/restart", {"confirm": True})
        came_back = up(90)
        st_chk, bchk = req(base, "/api/status", timeout=5)
        ver = str((bchk or {}).get("version") or "")
        cell("④ 有看门狗 ⇒ 真重启且自己回来（版本不变）",
             st4 == 200 and (b4 or {}).get("restart_scheduled") is True
             and came_back and st_chk == 200 and bool(ver),
             "HTTP %s ｜ 回来=%s ｜ 版本=%s ｜ watchdog=%s" % (
                 st4, came_back, ver or "?", (b4 or {}).get("watchdog_pids")))

        # ⑤ 审计
        st5, au = req(base, "/api/audit")
        evs = {str(r.get("event") or "") for r in ((au or {}).get("events") or [])}
        cell("⑤ 审计有 system.restart", "system.restart" in evs,
             "事件数=%d ｜ 有=%s" % (len(evs), "system.restart" in evs))
    finally:
        if wd is not None:
            wd.terminate()
            try:
                wd.wait(timeout=10)
            except Exception:                                # noqa: BLE001
                wd.kill()
        if wlog is not None:
            wlog.close()
        try:
            srv.terminate()
            try:
                srv.wait(timeout=10)
            except Exception:                                # noqa: BLE001
                srv.kill()
        except Exception:                                    # noqa: BLE001
            pass
        # 兜底：把这次可能被看门狗重生的服务也收掉（按端口找）
        try:
            from pathlib import Path as _P
            sys.path.insert(0, str(ROOT))
            from agent_community.platform import sysprobe
            for pid in sysprobe.listening_pids(port):
                sysprobe.kill_pid(pid, tree=True)
        except Exception:                                    # noqa: BLE001
            pass
        import shutil
        shutil.rmtree(data_dir, ignore_errors=True)

    print("-" * 90)
    print("结论：%s" % ("**受控重启端点全绿（没有看门狗就拒 / 缺确认就拒 / 非本机就拒 / 有看门狗能真重启）**"
                      if not fails else "**有 %d 格失败**" % fails))
    return 0 if not fails else 1


if __name__ == "__main__":
    raise SystemExit(main())
