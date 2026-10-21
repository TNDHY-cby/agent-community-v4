# -*- coding: utf-8 -*-
"""tools/launcher_check.py —— 启动器判据（V72）：**真跑五格**，不查源码文本。

格 1  冷启动：另起一个端口 + 另一个数据目录 ⇒ 过场页**先出来**、阶段单调前进、最终 `ready`、平台真活；
格 2  已在跑：再跑一次 ⇒ 立刻 `ready`，**不重复拉进程**（同端口只允许一个平台监听者）；
格 3  端口被别人占着 ⇒ `failed` 且 `hint` 非空（**不许只说"失败"**）；
格 4  没有可用解释器 ⇒ `failed` 且提示里有"pip install -r requirements.txt"；
格 5  产品化：全程只改 `AC_PORT` / `AC_DATA_DIR`（**不改一行代码**），且仓库没被写脏。

用法：python tools/launcher_check.py [-v]
退出码：0＝五格全过；1＝有格红。
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

REPO = Path(__file__).resolve().parents[1]
PY = sys.executable
RESULTS = []


def ok(name: str, passed: bool, detail: str = "") -> None:
    RESULTS.append((name, passed, detail))
    print("%s %s%s" % ("✓" if passed else "✗", name, ("  —— " + detail) if detail else ""))


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def http_json(url: str, timeout: float = 3.0):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def http_ok(url: str, timeout: float = 3.0) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return 200 <= int(r.status) < 300
    except Exception:                                        # noqa: BLE001
        return False


class Launch:
    """跑一个启动器子进程，把它的过场页地址与 /state 拿在手里。"""

    def __init__(self, env_extra: dict, extra_args=()):
        self.env = dict(os.environ)
        self.env.update(env_extra)
        self.out = tempfile.NamedTemporaryFile("w+", delete=False, suffix=".txt", encoding="utf-8")
        self.err = tempfile.NamedTemporaryFile("w+", delete=False, suffix=".txt", encoding="utf-8")
        self.proc = subprocess.Popen(
            [PY, str(REPO / "tools" / "launcher.py"), "--no-open", "--print-url",
             "--keep", "2", "--keep-failed", "2", *extra_args],
            cwd=str(REPO), env=self.env, stdout=self.out, stderr=self.err)

    def wait_url(self, timeout: float = 8.0):
        """等过场页地址出现（判"1 秒内出画面"用真实时间量）。"""
        t0 = time.time()
        while time.time() - t0 < timeout:
            try:
                self.out.flush()
                txt = Path(self.out.name).read_text(encoding="utf-8", errors="replace").strip()
                if txt.startswith("http://"):
                    return txt.splitlines()[0].strip(), time.time() - t0
            except Exception:                                # noqa: BLE001
                pass
            time.sleep(0.05)
        return None, timeout

    def state(self, base: str):
        return http_json(base.rstrip("/") + "/state", timeout=2.0)

    def wait_phase(self, base: str, phase: str, timeout: float):
        t0 = time.time()
        last = None
        while time.time() - t0 < timeout:
            try:
                last = self.state(base)
                if last.get("phase") == phase:
                    return last, time.time() - t0
            except Exception:                                # noqa: BLE001
                pass
            time.sleep(0.2)
        return last, time.time() - t0

    def stop(self):
        try:
            self.proc.terminate()
            self.proc.wait(timeout=10)
        except Exception:                                    # noqa: BLE001
            try:
                self.proc.kill()
            except Exception:                                # noqa: BLE001
                pass


def kill_port(port: int) -> None:
    """收尾：把这次检查起的平台进程收掉（只杀**监听这个端口**的 pid）。"""
    sys.path.insert(0, str(REPO))
    try:
        from agent_community.platform import sysprobe

        for pid in sysprobe.listening_pids(port) or []:
            try:
                sysprobe.kill_pid(pid, tree=True)
            except Exception:                                # noqa: BLE001
                pass
    except Exception:                                        # noqa: BLE001
        pass


def case_cold_start(tmp: Path) -> None:
    port = free_port()
    data = tmp / "cold_data"
    L = Launch({"AC_PORT": str(port), "AC_DATA_DIR": str(data), "AC_LAUNCHER_PYTHON": ""})
    base = None
    try:
        base, dt = L.wait_url()
        ok("①-a 过场页地址 ≤1 秒出现", base is not None and dt <= 1.0,
           "%.2f 秒" % dt if base else "没等到地址")
        if not base:
            return
        page_ok = http_ok(base)
        ok("①-b 服务没起来时过场页已经能打开（先出画面）", page_ok)
        seen = []
        t0 = time.time()
        st = None
        while time.time() - t0 < 60:
            try:
                st = L.state(base)
            except Exception:                                # noqa: BLE001
                st = None
            if st:
                seen.append(int(st.get("step") or 0))
                if st.get("phase") == "ready":
                    break
            time.sleep(0.3)
        ok("①-c 最终 ready", bool(st) and st.get("phase") == "ready",
           "phase=%s" % (st or {}).get("phase"))
        ok("①-d 阶段单调前进（不倒退、不跳假进度）",
           bool(seen) and all(b >= a for a, b in zip(seen, seen[1:])))
        url = (st or {}).get("platform_url") or ""
        ok("①-e 平台真活（/api/status 200）", bool(url) and http_ok(url + "api/status"))
        ok("①-f 数据落在 AC_DATA_DIR（不在仓库里）",
           (data / "logs").exists() and not (REPO / "agent_community" / "data" / "harness").exists())

        # ② 已在跑：再跑一次
        L2 = Launch({"AC_PORT": str(port), "AC_DATA_DIR": str(data)})
        try:
            base2, _ = L2.wait_url()
            st2, dt2 = L2.wait_phase(base2, "ready", 15) if base2 else (None, 15)
            ok("②-a 再跑一次立刻 ready（不重复拉进程）", bool(st2) and st2.get("phase") == "ready",
               "%.1f 秒" % dt2)
            from agent_community.platform import sysprobe

            listeners = sysprobe.listening_pids(port) or []
            ok("②-b 同端口只有一个平台监听者", len(listeners) == 1, "listeners=%s" % listeners)
        finally:
            L2.stop()
    finally:
        L.stop()
        kill_port(port)


def case_port_busy(tmp: Path) -> None:
    port = free_port()
    s = socket.socket()
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("127.0.0.1", port))
    s.listen(5)                                              # 占着，但不是平台
    L = Launch({"AC_PORT": str(port), "AC_DATA_DIR": str(tmp / "busy_data")})
    try:
        base, _ = L.wait_url()
        st, _ = L.wait_phase(base, "failed", 20) if base else (None, 20)
        ok("③ 端口被别的程序占着 ⇒ failed 且说清怎么办",
           bool(st) and st.get("phase") == "failed" and bool(st.get("hint")),
           "text=%s" % (st or {}).get("text"))
    finally:
        L.stop()
        try:
            s.close()
        except Exception:                                    # noqa: BLE001
            pass


def case_no_interpreter(tmp: Path) -> None:
    port = free_port()
    L = Launch({"AC_PORT": str(port), "AC_DATA_DIR": str(tmp / "no_py_data"),
                "AC_LAUNCHER_PYTHON": str(tmp / "not-a-python" / "python.exe")})
    try:
        base, _ = L.wait_url()
        st, _ = L.wait_phase(base, "failed", 25) if base else (None, 25)
        hint = (st or {}).get("hint") or ""
        ok("④ 没有可用解释器 ⇒ failed 且给安装命令",
           bool(st) and st.get("phase") == "failed" and "pip install -r requirements.txt" in hint,
           "hint=%s" % hint[:60])
    finally:
        L.stop()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="启动器判据（V72）")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)

    print("=" * 66)
    print("启动器判据（V72）—— 真跑：冷启动 / 已在跑 / 端口被占 / 缺解释器 / 换端口换数据目录")
    print("=" * 66)
    sys.path.insert(0, str(REPO))
    with tempfile.TemporaryDirectory(prefix="ac_launcher_check_") as td:
        tmp = Path(td)
        for fn in (case_cold_start, case_port_busy, case_no_interpreter):
            try:
                fn(tmp)
            except Exception as e:                           # noqa: BLE001
                import traceback

                if args.verbose:
                    traceback.print_exc()
                ok("%s 执行异常" % fn.__name__, False, "%s: %s" % (type(e).__name__, e))

    bad = [r for r in RESULTS if not r[1]]
    print("-" * 66)
    print("结论：%s（%d 格，红 %d）" % ("全过" if not bad else "有格红", len(RESULTS), len(bad)))
    return 0 if not bad else 1


if __name__ == "__main__":
    raise SystemExit(main())
