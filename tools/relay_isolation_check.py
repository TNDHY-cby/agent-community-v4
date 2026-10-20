# -*- coding: utf-8 -*-
"""判据：**一个 Hub 服务多家公司（公司隔离，M3）**（V68）。

```bash
python tools/relay_isolation_check.py     # 约 40 秒
```

## 六格

| 格 | 判据 |
|---|---|
| ① | **私有形态**（`AC_HUB_SHARED=0`，默认）：跨公司名字**也能通**（保证 M1/M2 不退化） |
| ② | **共享形态**（`AC_HUB_SHARED=1`）：**同公司通** |
| ③ | 共享形态：**跨公司被拒 403**，且响应带 `from_company`/`to_company`/`how_to_fix` |
| ④ | 共享形态 + `AC_HUB_ALLOW_CROSS=1`：**带 `allow_cross=true` 才通**，不带仍被拒 |
| ⑤ | `GET /hub/terminals` 每个终端都带 `company` |
| ⑥ | 发给**未登记**的终端 ⇒ 403（不许给看不见的公司发） |

> ⚠️ 判据自己也要防"假绿"：③④⑥ 必须验证**拒绝的原因**（不能只看 HTTP 码）。
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


def post(base: str, path: str, body: dict, token: str = "", timeout: float = 20.0):
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    h = {"Content-Type": "application/json"}
    if token:
        h["Authorization"] = "Bearer " + token
    r = urllib.request.Request(base + path, data=data, method="POST", headers=h)
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


def get(base: str, path: str, token: str = ""):
    h = {"Content-Type": "application/json"}
    if token:
        h["Authorization"] = "Bearer " + token
    r = urllib.request.Request(base + path, method="GET", headers=h)
    try:
        with urllib.request.urlopen(r, timeout=15) as resp:
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
    env = _env(AC_HUB_TOKEN=HUB_TOKEN, AC_HUB_PORT=port, PYTHONIOENCODING="utf-8", **extra)
    p = subprocess.Popen([sys.executable, str(ROOT / "tools" / "relay_hub.py"),
                          "--host", "127.0.0.1", "--port", str(port)],
                         cwd=str(ROOT), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = "http://127.0.0.1:%d" % port
    for _ in range(80):
        st, _b = get(base, "/hub/health")
        if st == 200:
            break
        time.sleep(0.3)
    return p, base


def main() -> int:
    fails = 0

    def cell(name: str, ok: bool, detail: str = "") -> None:
        nonlocal fails
        print("   %-56s %-6s %s" % (name, "OK" if ok else "★FAIL", detail))
        fails += 0 if ok else 1

    print("=" * 104)
    print("一个 Hub 服务多家公司（公司隔离 M3）判据 —— 私有形态不退化 / 共享形态真隔离")
    print("=" * 104)
    procs: list[subprocess.Popen] = []
    try:
        # ① 私有形态：不隔离（跨公司名字也能通）
        p1, b1 = start_hub(free_port())          # AC_HUB_SHARED 默认 0
        procs.append(p1)
        for tid, co in (("a1", "co-a"), ("b1", "co-b")):
            post(b1, "/hub/register", {"terminal_id": tid, "company_id": co}, HUB_TOKEN)
        st, body = post(b1, "/hub/send", {"from": "a1", "to": "b1", "payload": "私有形态跨公司"},
                        HUB_TOKEN)
        cell("① 私有形态（默认）：跨公司名字也能通（M1/M2 不退化）", st == 200,
             "HTTP %s" % st)

        # ②③ 共享形态
        p2, b2 = start_hub(free_port(), AC_HUB_SHARED="1")
        procs.append(p2)
        post(b2, "/hub/register", {"terminal_id": "a1", "company_id": "co-a"}, HUB_TOKEN)
        post(b2, "/hub/register", {"terminal_id": "a2", "company_id": "co-a"}, HUB_TOKEN)
        post(b2, "/hub/register", {"terminal_id": "b1", "company_id": "co-b"}, HUB_TOKEN)
        st_same, _bs = post(b2, "/hub/send", {"from": "a1", "to": "a2", "payload": "同公司"},
                           HUB_TOKEN)
        cell("② 共享形态：同公司发送 ⇒ 通", st_same == 200, "HTTP %s" % st_same)

        st_cross, bcross = post(b2, "/hub/send",
                               {"from": "a1", "to": "b1", "payload": "跨公司"}, HUB_TOKEN)
        cell("③ 共享形态：跨公司 ⇒ 403 且说清两家公司",
             st_cross == 403 and (bcross or {}).get("from_company") == "co-a"
             and (bcross or {}).get("to_company") == "co-b"
             and bool((bcross or {}).get("how_to_fix")),
             "HTTP %s ｜ %s→%s" % (st_cross, (bcross or {}).get("from_company"),
                                  (bcross or {}).get("to_company")))

        # ④ 共享 + 允许跨公司：要显式 allow_cross
        p3, b3 = start_hub(free_port(), AC_HUB_SHARED="1", AC_HUB_ALLOW_CROSS="1")
        procs.append(p3)
        post(b3, "/hub/register", {"terminal_id": "a1", "company_id": "co-a"}, HUB_TOKEN)
        post(b3, "/hub/register", {"terminal_id": "b1", "company_id": "co-b"}, HUB_TOKEN)
        st_no, _bn = post(b3, "/hub/send", {"from": "a1", "to": "b1", "payload": "不显式"}, HUB_TOKEN)
        st_yes, _by = post(b3, "/hub/send", {"from": "a1", "to": "b1", "payload": "显式允许",
                                            "allow_cross": True}, HUB_TOKEN)
        cell("④ 允许跨公司时：不显式 ⇒ 仍拒；带 allow_cross=true ⇒ 通",
             st_no == 403 and st_yes == 200, "不显式=%s 显式=%s" % (st_no, st_yes))

        # ⑤ terminals 带 company
        st_t, tb = get(b2, "/hub/terminals", HUB_TOKEN)
        rows = (tb or {}).get("terminals") or []
        has_co = bool(rows) and all("company" in r for r in rows)
        cell("⑤ /hub/terminals 每个终端都带 company", st_t == 200 and has_co,
             "%d 个终端：%s" % (len(rows), [(r.get("terminal_id"), r.get("company")) for r in rows][:3]))

        # ⑥ 给未登记的终端发 ⇒ 403
        st_u, bu = post(b2, "/hub/send", {"from": "a1", "to": "ghost", "payload": "x"}, HUB_TOKEN)
        cell("⑥ 发给未登记的终端 ⇒ 403（不给看不见的终端发）",
             st_u == 403 and (bu or {}).get("missing") == "ghost",
             "HTTP %s ｜ missing=%s" % (st_u, (bu or {}).get("missing")))
    finally:
        for p in reversed(procs):
            try:
                p.terminate()
                p.wait(timeout=10)
            except Exception:                                # noqa: BLE001
                try:
                    p.kill()
                except Exception:                            # noqa: BLE001
                    pass

    print("-" * 104)
    print("结论：%s" % ("**公司隔离全绿（私有形态不退化 / 共享形态真隔离 / 跨公司要两道许可）**"
                      if not fails else "**有 %d 格失败**" % fails))
    return 0 if not fails else 1


if __name__ == "__main__":
    raise SystemExit(main())
