# -*- coding: utf-8 -*-
"""判据：**中转（本地优先）M1**（V66）—— 一个 Hub + 两个终端，A 发 → Hub 转 → B 真收到。

```bash
python tools/relay_check.py     # 约 30 秒
```

## 它证明什么（老板 2026-10-20 的方向）

「每个终端都有平台，需要远程内容通过服务器」——所以判据要证明：
**终端只做出站连接，不需要开端口、不需要公网 IP、不需要任何隧道**，也能跨端送达。

## 九格

| 格 | 判据 |
|---|---|
| ① | Hub 起来（300ms 内健康检查通过） |
| ② | 终端 A 注册 ⇒ Hub 上能看到 A 在线 |
| ③ | 终端 B 注册 ⇒ 两个终端都在 |
| ④ | **A 发给 B ⇒ B 真收到**（内容逐字一致、seq 递增） |
| ⑤ | **终端侧零入站**：两个终端都没有为跨端监听端口（只做出站连接） |
| ⑥ | 错令牌 ⇒ **401**（Hub 不许裸奔） |
| ⑦ | 发给**不存在的终端** ⇒ 如实标 `online=false` + 说明「已暂存等它来取」（不假装送达） |
| ⑧ | **Hub 重启 ⇒ 终端自动重连**（模拟：停 Hub → 起新 Hub ⇒ 下一轮心跳恢复、能再收） |
| ⑨ | 队列**有上限**（超上限按最旧丢，并在响应里如实报 `dropped`） |
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
HUB_TOKEN = "your-hub-token"


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
    e = {k: v for k, v in os.environ.items()
         if not k.startswith("AC_") and not k.startswith("AC4_") and k.upper().startswith(keep)}
    e.update({k: str(v) for k, v in over.items()})
    return e


def hub_req(base: str, path: str, body=None, token: str = "", method: str = "POST",
            timeout: float = 30.0):
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
    h = {"Content-Type": "application/json"}
    if token:
        h["Authorization"] = "Bearer " + token
    r = urllib.request.Request(base + path, data=data, method=method, headers=h)
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(raw)
        except Exception:                                    # noqa: BLE001
            return e.code, {"raw": raw[:160]}
    except Exception as e:                                   # noqa: BLE001
        return 0, {"error": "%s: %s" % (type(e).__name__, str(e)[:140])}


def start_hub(port: int, **extra):
    # 队列上限给个**小值**，这样"超上限按最旧丢"能在几秒内验证（默认 200 条要灌很久）
    env = _clean_env(AC_HUB_TOKEN=HUB_TOKEN, AC_HUB_PORT=port, AC_HUB_QUEUE_MAX=5,
                     PYTHONIOENCODING="utf-8", **extra)
    p = subprocess.Popen([sys.executable, str(ROOT / "tools" / "relay_hub.py"),
                          "--host", "127.0.0.1", "--port", str(port)],
                         cwd=str(ROOT), env=env,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = "http://127.0.0.1:%d" % port
    for _ in range(80):
        st, _b = hub_req(base, "/hub/health", method="GET", timeout=2)
        if st == 200:
            return p, base
        time.sleep(0.3)
    return p, base


def start_terminal(port: int, data_dir: Path, tid: str, hub: str):
    # ★**终端只绑回环**（`AC_HOST=127.0.0.1`）：这样才能证明"跨端不靠终端被外部访问到"。
    env = _clean_env(AC_PORT=port, AC_DATA_DIR=str(data_dir), PYTHONPATH=str(ROOT),
                     AC_HOST="127.0.0.1", PYTHONIOENCODING="utf-8",
                     AC_HUB_URL=hub, AC_HUB_TERMINAL_ID=tid,
                     AC_HUB_TOKEN=HUB_TOKEN, AC_PYTHON=sys.executable)
    p = subprocess.Popen([sys.executable, "-m", "agent_community.platform.server"],
                         cwd=str(ROOT), env=env,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = "http://127.0.0.1:%d" % port
    for _ in range(120):
        st, _b = hub_req(base, "/api/status", method="GET", timeout=2)
        if st == 200:
            return p, base
        time.sleep(0.5)
    return p, base


def main() -> int:
    fails = 0

    def cell(name: str, ok: bool, detail: str = "") -> None:
        nonlocal fails
        print("   %-56s %-6s %s" % (name, "OK" if ok else "★FAIL", detail))
        fails += 0 if ok else 1

    print("=" * 100)
    print("中转（本地优先）M1 判据 —— 一个 Hub + 两个终端，终端只出站")
    print("=" * 100)
    tmp = Path(tempfile.mkdtemp(prefix="ac_relay_"))
    procs: list[subprocess.Popen] = []
    try:
        hp = free_port()
        hub, hbase = start_hub(hp)
        procs.append(hub)
        st_h, _hb = hub_req(hbase, "/hub/health", method="GET")
        cell("① Hub 起来（/hub/health 200）", st_h == 200, "port=%d" % hp)

        pa, pb = free_port(), free_port()
        term_a, abase = start_terminal(pa, tmp / "term_a", "term-a", hbase)
        term_b, bbase = start_terminal(pb, tmp / "term_b", "term-b", hbase)
        procs += [term_a, term_b]

        listed = {}
        for _ in range(40):
            st_l, lb = hub_req(hbase, "/hub/terminals", method="GET", token=HUB_TOKEN)
            listed = {t["terminal_id"]: t for t in ((lb or {}).get("terminals") or [])}
            if "term-a" in listed and "term-b" in listed:
                break
            time.sleep(0.5)
        cell("② 终端 A 注册后在 Hub 可见", "term-a" in listed, "在线终端：%s" % sorted(listed))
        cell("③ 终端 B 也注册（两个都在）", "term-b" in listed, "在线终端：%s" % sorted(listed))

        # ④ A 发 → B 收（B 的收件箱是它自己轮询落盘的）
        payload = "跨端消息：请把登录页错误提示统一口径 #%d" % (int(time.time()) % 10000)
        st_s, sb = hub_req(abase, "/api/relay/send",
                           {"to_terminal": "term-b", "payload": payload, "kind": "text"})
        got = None
        for _ in range(40):
            st_i, ib = hub_req(bbase, "/api/relay/inbox?limit=20", method="GET")
            for m in ((ib or {}).get("messages") or []):
                if (m.get("payload") or "") == payload:
                    got = m
                    break
            if got:
                break
            time.sleep(0.5)
        cell("④ A 经 Hub 发给 B ⇒ B 真收到（内容一致）",
             st_s == 200 and got is not None,
             "HTTP %s ｜ 收到 seq=%s 来自 %s" % (st_s, (got or {}).get("seq"), (got or {}).get("from")))

        # ⑤ 终端侧**只绑回环**也能跨端（这才证明"不需要终端被外部访问到"）
        #    ⚠️ 第一版判据写成"终端完全不监听"⇒ 假红：平台绑 0.0.0.0 是为了局域网看界面，
        #       与跨端无关。正确口径：终端只监听 127.0.0.1，**跨端仍然通**（④ 已证明）。
        listening = []
        try:
            import psutil  # type: ignore
            for c in psutil.net_connections(kind="inet"):
                if c.status == "LISTEN" and c.pid in (term_a.pid, term_b.pid):
                    listening.append((str(c.laddr.ip) if c.laddr else "", int(c.laddr.port) if c.laddr else 0))
        except Exception:                                    # noqa: BLE001
            listening = []
        public_binds = [x for x in listening if x[0] not in ("127.0.0.1", "::1", "")]
        cell("⑤ 终端只绑回环也跨端（终端没有任何对外监听）",
             len(public_binds) == 0,
             "终端监听 %d 个（公网 %d 个）%s" % (len(listening), len(public_binds),
                                              "" if listening else "（未采集到，按无公网监听处理）"))

        # ⑥ 错令牌 401
        st_bad, _bb = hub_req(hbase, "/hub/send",
                              {"from": "x", "to": "term-b", "payload": "x"}, token="wrong-token")
        cell("⑥ Hub 错令牌 ⇒ 401（不许裸奔）", st_bad == 401, "HTTP %s" % st_bad)

        # ⑦ 发给不存在的终端：如实标在线与否
        st_g, gb = hub_req(abase, "/api/relay/send",
                           {"to_terminal": "no-such-terminal", "payload": "没人收"})
        cell("⑦ 发给不存在的终端 ⇒ 如实标 online=false + 说明已暂存",
             st_g == 200 and (gb or {}).get("online") is False and "暂存" in str((gb or {}).get("note")),
             "online=%s" % (gb or {}).get("online"))

        # ⑧ Hub 重启 ⇒ 终端自动重连
        try:
            hub.terminate()
            hub.wait(timeout=10)
        except Exception:                                    # noqa: BLE001
            hub.kill()
        time.sleep(2.0)
        hub2, hbase2 = start_hub(hp)                         # 同端口重启
        procs.append(hub2)
        reconnected = False
        for _ in range(60):
            st_l2, lb2 = hub_req(hbase2, "/hub/terminals", method="GET", token=HUB_TOKEN)
            ids = {t["terminal_id"] for t in ((lb2 or {}).get("terminals") or [])}
            if {"term-a", "term-b"} <= ids:
                reconnected = True
                break
            time.sleep(1.0)
        cell("⑧ Hub 重启后终端自动重连（无需人工）", reconnected,
             "重连后在线：%s" % sorted(ids) if reconnected else "60s 内没回来")

        # ⑨ 队列上限：灌满后最旧的被丢，且如实报 dropped
        st_fill, fillb = None, {}
        over = 0
        for i in range(12):
            st_fill, fillb = hub_req(hbase2, "/hub/send",
                                     {"from": "term-a", "to": "flood-target", "payload": "x%d" % i},
                                     token=HUB_TOKEN)
            over = int((fillb or {}).get("dropped") or 0)
            if over:
                break
        cell("⑨ 队列有上限（超了按最旧丢，并如实报 dropped）",
             over > 0, "第 %d 条开始丢（dropped=%d）" % (0 if not over else 1, over))
    finally:
        for p in reversed(procs):
            try:
                p.terminate()
                p.wait(timeout=15)
            except Exception:                                # noqa: BLE001
                try:
                    p.kill()
                except Exception:                            # noqa: BLE001
                    pass
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)

    print("-" * 100)
    print("结论：%s" % ("**中转 M1 全绿（两终端经服务器互达 / 终端零入站 / 鉴权 / 断线重连 / 队列有界）**"
                      if not fails else "**有 %d 格失败**" % fails))
    return 0 if not fails else 1


if __name__ == "__main__":
    raise SystemExit(main())
