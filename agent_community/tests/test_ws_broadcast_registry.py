# -*- coding: utf-8 -*-
"""守卫：**WS 广播必须送到外部连接**（2026-10-25 真事故）。

真事：`python -m agent_community.platform.server` 时模块名是 `__main__`，
而 `routers/*` 的 `from ..server import ...` 会再导入一份 ⇒ 两份 `ws_clients`：
`/ws` 加进 `__main__` 那份，`bcast_to_clients` 遍历另一份（永远空）
⇒ **外部客户端一帧都收不到**，前端实时更新形同虚设；`TestClient` 只导入一份，所以一直看不出来。

这里钉三件事：
  ① `server.ws_registry()` 在存在 `__main__.ws_clients` 时**返回它**（一处真相）；
  ② 两者是**同一个对象**（不是复制，否则广播还是送不到）；
  ③ 真进程（`-m` 起）里：连上 `/ws` 后发一条 `/api/command`，客户端**真收到** `task_created`
     —— 这条是行为级判据，也是最难糊弄的一条（用标准库 socket 手写 WS 握手，不引第三方）。
"""
from __future__ import annotations

import base64
import json
import os
import socket
import struct
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.append(str(_ROOT))


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def test_ws_registry_prefers_main_module(monkeypatch):
    """`__main__` 里那份才算数（服务真的跑在它上面）。"""
    import types

    from agent_community.platform import server as srv

    fake_main = types.SimpleNamespace(ws_clients=set(), hall_messages=[])
    monkeypatch.setitem(sys.modules, "__main__", fake_main)
    clients, hall = srv.ws_registry()
    assert clients is fake_main.ws_clients, "必须返回 __main__ 那份（同一个对象，不是复制）"
    assert hall is fake_main.hall_messages


def test_ws_registry_falls_back_to_module_scope(monkeypatch):
    """没有 `__main__.ws_clients` 时退回本模块（测试/被 import 的场景）。"""
    import types

    from agent_community.platform import server as srv

    monkeypatch.setitem(sys.modules, "__main__", types.SimpleNamespace())
    clients, hall = srv.ws_registry()
    assert clients is srv.ws_clients and hall is srv.hall_messages


# ── 行为级：真进程 + 手写 WebSocket 客户端（零第三方）─────────────────
def _ws_connect(host: str, port: int, path: str = "/ws", timeout: float = 10):
    s = socket.create_connection((host, port), timeout=timeout)
    key = base64.b64encode(os.urandom(16)).decode()
    req = ("GET %s HTTP/1.1\r\nHost: %s:%d\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
           "Sec-WebSocket-Key: %s\r\nSec-WebSocket-Version: 13\r\n\r\n" % (path, host, port, key))
    s.sendall(req.encode())
    head = b""
    while b"\r\n\r\n" not in head:
        chunk = s.recv(4096)
        if not chunk:
            raise AssertionError("WS 握手被断开：%r" % head[-200:])
        head += chunk
    assert b"101" in head.split(b"\r\n")[0], head[:200]
    return s


def _ws_read_text(s, timeout: float = 10) -> str:
    s.settimeout(timeout)
    hdr = b""
    while len(hdr) < 2:
        hdr += s.recv(2 - len(hdr))
    b1, b2 = hdr[0], hdr[1]
    opcode = b1 & 0x0F
    length = b2 & 0x7F
    if length == 126:
        length = struct.unpack(">H", s.recv(2))[0]
    elif length == 127:
        length = struct.unpack(">Q", s.recv(8))[0]
    data = b""
    while len(data) < length:
        chunk = s.recv(length - len(data))
        if not chunk:
            break
        data += chunk
    if opcode == 0x1:
        return data.decode("utf-8", "replace")
    return ""


def test_real_process_broadcast_reaches_external_ws_client(tmp_path):
    """★行为级：`-m` 起的真进程里，广播**真的**到得了外部 WS 连接（这是事故本体）。"""
    port = _free_port()
    env = dict(os.environ)
    env.update({"AC_PORT": str(port), "AC_DATA_DIR": str(tmp_path), "PYTHONIOENCODING": "utf-8"})
    proc = subprocess.Popen([sys.executable, "-m", "agent_community.platform.server"],
                            cwd=str(_ROOT), env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        up = False
        for _ in range(60):
            try:
                with urllib.request.urlopen("http://127.0.0.1:%d/api/status" % port,
                                            timeout=2) as r:
                    up = r.status == 200
            except OSError:
                up = False
            if up:
                break
            time.sleep(0.5)
        assert up, "临时平台没起来（端口 %d）" % port

        ws = _ws_connect("127.0.0.1", port)
        try:
            body = json.dumps({"input": "WS 广播守卫"}, ensure_ascii=False).encode("utf-8")
            req = urllib.request.Request("http://127.0.0.1:%d/api/command" % port, data=body,
                                         headers={"Content-Type": "application/json"},
                                         method="POST")
            with urllib.request.urlopen(req, timeout=30) as r:
                assert r.status == 200
            got = ""
            for _ in range(4):
                try:
                    got = _ws_read_text(ws, timeout=5)
                except (socket.timeout, TimeoutError):
                    continue
                if "task_created" in got:
                    break
            assert "task_created" in got, (
                "外部 WS 客户端没收到 task_created —— 多半又是两份 ws_clients"
                "（`/ws` 加进 `__main__` 那份、`bcast_to_clients` 遍历另一份）。收到的是：%r" % got[:200])
        finally:
            ws.close()
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
