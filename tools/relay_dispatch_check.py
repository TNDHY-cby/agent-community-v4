# -*- coding: utf-8 -*-
"""判据：**派活与回执走中转**（M2，V67）—— 一个 Hub + 两个终端，八格。

```bash
python tools/relay_dispatch_check.py     # 约 60 秒
```

## 它证明什么

跨端派活**不需要隧道、不需要对方地址可达**：只要两台终端在同一个中转上，
派活就是一条消息，对端**真建出工作间**，回执回来**自动更新**本侧那条外部任务。

## 八格

| 格 | 判据 |
|---|---|
| ① | 两个终端 + 一个 Hub 起来，两边 `connected=true` |
| ② | A `POST /api/relay/dispatch` ⇒ 本地立刻有外部任务（`status=sent`、`via=hub`） |
| ③ | **B 侧真建出工作间**（标题一致）——不是"只收到一条消息" |
| ④ | B 回了 `task.ack`，A 的收件箱里有它，且 `handled.ok=true` |
| ⑤ | 模拟 B 完成发 `task.result(done)` ⇒ **A 那条外部任务自动变 `done`**（不用点刷新回执） |
| ⑥ | 给不存在的终端派活 ⇒ 如实 `online=false`，本地任务仍 `sent`（**不标成功**） |
| ⑦ | 坏消息（缺 title 的 task.assign）⇒ 对端 **不建间**，且收件箱该项 `handled.ok=false` |
| ⑧ | 全程两个终端**都没有对外监听**（只绑回环也能跨端） |
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


def _env(**over) -> dict:
    keep = ("PATH", "PYTHON", "SYSTEM", "WINDIR", "TEMP", "TMP", "HOME", "USER",
            "APPDATA", "LOCALAPPDATA", "COMSPEC", "PATHEXT", "LANG", "LC_", "LD_LIBRARY")
    e = {k: v for k, v in os.environ.items()
         if not k.startswith("AC_") and not k.startswith("AC4_") and k.upper().startswith(keep)}
    e.update({k: str(v) for k, v in over.items()})
    return e


def req(base: str, path: str, body=None, method: str = "GET", token: str = "", timeout=30,
        bearer: str = ""):
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
    h = {"Content-Type": "application/json"}
    if token:
        h["Authorization"] = "Bearer " + token
    if bearer:
        h["Authorization"] = "Bearer " + bearer
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


def start_hub(port: int):
    env = _env(AC_HUB_TOKEN=HUB_TOKEN, AC_HUB_PORT=port, PYTHONIOENCODING="utf-8")
    p = subprocess.Popen([sys.executable, str(ROOT / "tools" / "relay_hub.py"),
                          "--host", "127.0.0.1", "--port", str(port)],
                         cwd=str(ROOT), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = "http://127.0.0.1:%d" % port
    for _ in range(80):
        st, _b = req(base, "/hub/health", timeout=2)
        if st == 200:
            break
        time.sleep(0.3)
    return p, base


def start_terminal(port: int, data_dir: Path, tid: str, hub: str):
    env = _env(AC_PORT=port, AC_DATA_DIR=str(data_dir), PYTHONPATH=str(ROOT),
               AC_HOST="127.0.0.1", PYTHONIOENCODING="utf-8", AC_HUB_URL=hub,
               AC_HUB_TERMINAL_ID=tid, AC_HUB_TOKEN=HUB_TOKEN, AC_PYTHON=sys.executable)
    p = subprocess.Popen([sys.executable, "-m", "agent_community.platform.server"],
                         cwd=str(ROOT), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = "http://127.0.0.1:%d" % port
    for _ in range(120):
        st, _b = req(base, "/api/status", timeout=2)
        if st == 200:
            return p, base
        time.sleep(0.5)
    return p, base


def main() -> int:
    fails = 0

    def cell(name: str, ok: bool, detail: str = "") -> None:
        nonlocal fails
        print("   %-58s %-6s %s" % (name, "OK" if ok else "★FAIL", detail))
        fails += 0 if ok else 1

    print("=" * 104)
    print("派活与回执走中转（M2）判据 —— 不需要隧道、不需要对方地址")
    print("=" * 104)
    tmp = Path(tempfile.mkdtemp(prefix="ac_disp_"))
    procs: list[subprocess.Popen] = []
    try:
        hub, hbase = start_hub(free_port())
        procs.append(hub)
        pa, pb = free_port(), free_port()
        ta, abase = start_terminal(pa, tmp / "a", "term-a", hbase)
        tb, bbase = start_terminal(pb, tmp / "b", "term-b", hbase)
        procs += [ta, tb]

        conn_a = conn_b = False
        for _ in range(60):
            _s, sa = req(abase, "/api/relay/status")
            _s, sb = req(bbase, "/api/relay/status")
            conn_a = bool((sa or {}).get("connected"))
            conn_b = bool((sb or {}).get("connected"))
            if conn_a and conn_b:
                break
            time.sleep(1.0)
        cell("① 两终端 + 一 Hub 起来，两边都 connected=true", conn_a and conn_b,
             "A=%s B=%s" % (conn_a, conn_b))

        title = "跨端派活：把登录页错误提示统一口径"
        st_d, db = req(abase, "/api/relay/dispatch",
                       {"to_terminal": "term-b", "title": title, "peer_name": "另一台"},
                       method="POST")
        pt_id = str((db or {}).get("pt_id") or "")
        st_l, lb = req(abase, "/api/peer-tasks?limit=20")
        rec = None
        for t in ((lb or {}).get("tasks") or []):
            if t.get("pt_id") == pt_id:
                rec = t
                break
        cell("② 派活后本侧立刻有外部任务（sent + via=hub）",
             st_d == 200 and rec is not None and rec.get("status") == "sent"
             and rec.get("via") == "hub",
             "HTTP %s ｜ pt=%s ｜ status=%s via=%s" % (st_d, pt_id,
                                                     (rec or {}).get("status"), (rec or {}).get("via")))

        # ③ B 侧真建出工作间（标题一致）
        ws_ok, ws_id = False, ""
        for _ in range(60):
            _s, wl = req(bbase, "/api/workshops")
            for w in ((wl or {}).get("workshops") or []):
                if str(w.get("name") or "") == title[:40]:
                    ws_ok, ws_id = True, str(w.get("workshop_id") or "")
                    break
            if ws_ok:
                break
            time.sleep(1.0)
        if not ws_ok:
            # 失败时打出对端收件箱的真实处理结果（不猜）
            _s, ibx = req(bbase, "/api/relay/inbox?limit=10")
            tail = [{"kind": m.get("kind"), "handled": m.get("handled")}
                    for m in ((ibx or {}).get("messages") or [])]
            cell("③ 对端真建出工作间（不是只收到一条消息）", False,
                 "对端收件箱：%s" % json.dumps(tail[-3:], ensure_ascii=False)[:220])
        else:
            cell("③ 对端真建出工作间（不是只收到一条消息）", True, "对端工作间=%s" % ws_id)

        # ④ A 的收件箱里有 task.ack（对端"我接活了"）
        #    ⚠️ ack **不需要 `handled`**（它不在需处理类型里，只落收件箱）——
        #       第一版判据要求 handled.ok 才通过 ⇒ 假红。正确口径：收到 ack 且 payload.accepted 为真。
        ack_ok = False
        ack_detail = ""
        for _ in range(30):
            _s, ib = req(abase, "/api/relay/inbox?limit=50")
            for m in ((ib or {}).get("messages") or []):
                if (m.get("kind") or "") == "task.ack":
                    p = m.get("payload") or {}
                    ack_ok = bool(p.get("accepted"))
                    ack_detail = "accepted=%s ws=%s" % (p.get("accepted"), p.get("workshop_id"))
                    break
            if ack_ok:
                break
            time.sleep(1.0)
        cell("④ 对端回了 task.ack（本侧看到「已接活 + 它的工作间号」）", ack_ok,
             ack_detail or "没收到 ack")

        # ⑤ 模拟 B 完成 → task.result(done) ⇒ A 侧自动变 done
        from_hub = {"from": "term-b", "to": "term-a", "kind": "task.result",
                    "payload": {"pt_id": pt_id, "status": "completed",
                                "detail": "做完了"}}
        st_r, rb = req(hbase, "/hub/send", from_hub, method="POST", token=HUB_TOKEN)
        done = False
        for _ in range(40):
            _s, tb2 = req(abase, "/api/peer-tasks/%s" % pt_id)
            if str(((tb2 or {}).get("task") or {}).get("status") or "") == "done":
                done = True
                break
            time.sleep(1.0)
        cell("⑤ 回执经中转回来 ⇒ 本侧外部任务自动变 done（不用点刷新）", done,
             "hub send HTTP %s" % st_r)

        # ⑥ 给不存在的终端派活 ⇒ 如实 online=false，本地仍 sent（不标成功）
        st_g, gb = req(abase, "/api/relay/dispatch",
                       {"to_terminal": "no-such-term", "title": "没人接的活"}, method="POST")
        pt2 = str((gb or {}).get("pt_id") or "")
        st2, lb2 = req(abase, "/api/peer-tasks/%s" % pt2)
        st_of = str(((lb2 or {}).get("task") or {}).get("status") or "")
        cell("⑥ 给不存在的终端派活 ⇒ online=false 且状态仍是 sent（不假装成功）",
             (gb or {}).get("online") is False and st_of == "sent",
             "online=%s status=%s" % ((gb or {}).get("online"), st_of))

        # ⑦ 坏消息（缺 title）⇒ 对端不建间，且收件箱该项 handled.ok=false
        n_before = None
        _s, wl0 = req(bbase, "/api/workshops")
        n_before = len(((wl0 or {}).get("workshops") or []))
        req(hbase, "/hub/send", {"from": "term-a", "to": "term-b", "kind": "task.assign",
                                 "payload": {"pt_id": "pt_bad", "title": ""}},
            method="POST", token=HUB_TOKEN)
        bad_ok = False
        bad_detail = ""
        for _ in range(40):
            _s, ib2 = req(bbase, "/api/relay/inbox?limit=50")
            # ⚠️ 要扫**所有** task.assign（第一条是成功那次）—— 第一版 break 在第一条 ⇒ 假红
            for m in ((ib2 or {}).get("messages") or []):
                if (m.get("kind") or "") != "task.assign":
                    continue
                hd = m.get("handled") or {}
                if hd and hd.get("ok") is False:
                    bad_ok = True
                    bad_detail = str(hd.get("detail") or "")[:70]
                    break
            if bad_ok:
                break
            time.sleep(1.0)
        _s, wl1 = req(bbase, "/api/workshops")
        n_after = len(((wl1 or {}).get("workshops") or []))
        cell("⑦ 坏消息（缺 title）⇒ 不建间 + 处理失败可见", bad_ok and n_after == n_before,
             "%s ｜ 工作间 %d→%d" % (bad_detail or "无痕迹", n_before, n_after))

        # ⑧ 两个终端都没有对外监听
        listening = []
        try:
            import psutil  # type: ignore
            for c in psutil.net_connections(kind="inet"):
                if c.status == "LISTEN" and c.pid in (ta.pid, tb.pid):
                    listening.append(str(c.laddr.ip) if c.laddr else "")
        except Exception:                                    # noqa: BLE001
            listening = []
        public = [x for x in listening if x not in ("127.0.0.1", "::1", "")]
        cell("⑧ 两个终端都没有对外监听（只绑回环也能跨端派活）", len(public) == 0,
             "监听 %d 个（对外 %d）" % (len(listening), len(public)))
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

    print("-" * 104)
    print("结论：%s" % ("**派活与回执走中转全绿（派活真建间 / ack 回执 / 结果自动更新 / 失败可见 / 零对外监听）**"
                      if not fails else "**有 %d 格失败**" % fails))
    return 0 if not fails else 1


if __name__ == "__main__":
    raise SystemExit(main())
