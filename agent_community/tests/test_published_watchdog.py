# -*- coding: utf-8 -*-
"""守卫：**发布面看门狗**（`tools/watchdog.py`）—— 陌生人包里也该有自愈能力。

老板 2026-10-18 交办「继续开发」前留下的欠账：`deploy/`（原看门狗）**不在发布面**，
所以陌生人拿到的包里服务一崩就再也起不来。本文件锁住新看门狗的四条性质：

  ① **零第三方依赖**（只用标准库 —— 它是"环境坏了救命"的那只）；
  ② **源码里没有端口字面量**（端口只经 `agent_community.platform_address` 解析）；
  ③ 判活**只认 HTTP 200 + JSON 可解析**（"端口开着但服务是坏"不算活）；
  ④ 真起一个临时实例 ⇒ `--check` 必须是 0；把它停掉 ⇒ `--check` 必须是 1（**失败要说话**）。

⚠️ 这条要起真实例（约 10–20s），但它是"包给别人能不能自愈"的唯一判据。
"""
from __future__ import annotations

import os
import re
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.append(str(_ROOT))
TOOL = _ROOT / "tools" / "watchdog.py"


def _run(args, env_extra=None, timeout=120):
    env = {**os.environ}
    env.pop("PYTHONPATH", None)
    if env_extra:
        env.update(env_extra)
    return subprocess.run([sys.executable, str(TOOL), *args], cwd=_ROOT, capture_output=True,
                          text=True, encoding="utf-8", errors="replace", timeout=timeout, env=env)


def _free_port() -> int:
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])
    finally:
        s.close()


def test_watchdog_is_stdlib_only_and_has_no_port_literals():
    src = TOOL.read_text(encoding="utf-8")
    third_party = [m for m in re.findall(r"^\s*(?:import|from)\s+([A-Za-z_][\w.]*)", src, re.M)
                   if m.split(".")[0] not in (
                       "__future__", "argparse", "json", "os", "subprocess", "sys", "time",
                       "urllib", "pathlib", "agent_community",
                       "fcntl", "msvcrt")]        # 两把锁都在标准库里（POSIX / Windows 各一把）
    assert not third_party, "看门狗必须零第三方依赖，出现了：%s" % sorted(set(third_party))
    for bad in ("18920", "9104", "9105", "8000", "localhost:"):
        assert bad not in src, "看门狗源码里出现了端口/地址字面量 %r（必须走 platform_address）" % bad


def test_probe_requires_real_json_answer(tmp_path):
    """**端口开着但应答不健康**不算活：起一个只会回 200 空 body 的假服务。"""
    import http.server
    import threading

    class Fake(http.server.BaseHTTPRequestHandler):
        def do_GET(self):                                   # noqa: N802
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"not json")

        def log_message(self, *a):
            pass

    port = _free_port()
    srv = http.server.HTTPServer(("127.0.0.1", port), Fake)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        r = _run(["--check"], {"AC_BASE_URL": "http://127.0.0.1:%d" % port, "AC_PORT": str(port)})
        assert r.returncode == 1, "非 JSON 应答被当成「活着」了：%s" % (r.stdout + r.stderr)[-400:]
    finally:
        srv.shutdown()


def test_check_reports_down_then_up(tmp_path):
    """真起一个实例：先把 --check 打红（服务没起），再起服务打绿。"""
    port = _free_port()
    data = tmp_path / "data"
    env = {"AC_PORT": str(port), "AC_DATA_DIR": str(data),
           "AC_BASE_URL": "http://127.0.0.1:%d" % port}
    env.pop("PYTHONPATH", None)

    down = _run(["--check"], env)
    assert down.returncode == 1, "没有服务时 --check 竟然是绿的：%s" % (down.stdout + down.stderr)[-400:]
    assert "连不上" in (down.stdout + down.stderr) or "没活着" in (down.stdout + down.stderr)

    child_env = {**os.environ, **env, "PYTHONPATH": str(_ROOT)}
    child_env.pop("PYTHONPATH", None)
    child_env["PYTHONPATH"] = str(_ROOT)
    proc = subprocess.Popen([sys.executable, "-m", "agent_community.platform.server"],
                            cwd=str(_ROOT), env=child_env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        up = None
        for _ in range(60):
            up = _run(["--check"], env)
            if up.returncode == 0:
                break
            time.sleep(0.5)
        assert up is not None and up.returncode == 0, \
            "服务起来了但 --check 还是红的：%s" % ((up.stdout + up.stderr)[-500:] if up else "")
        assert "version=" in up.stdout
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except Exception:                                   # noqa: BLE001
            proc.kill()
