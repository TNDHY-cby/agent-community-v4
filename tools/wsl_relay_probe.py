# -*- coding: utf-8 -*-
"""在 WSL 里跑的中转探针（跨网络验证用；由 `relay_crossnet_check.py` 调起）。

用法（Windows 侧）：
    wsl python3 /mnt/d/Temp/ac_wsl_probe.py <hub_base_url> <token> <terminal_id>

它做三件事并把结果打成一行 JSON：
  1. `/hub/register` 注册自己（证明**跨网络栈**能连到 Windows 上的 Hub）
  2. `/hub/send` 给指定终端发一条（证明能往对侧投递）
  3. `/hub/heartbeat` 取自己的消息（证明对侧能投回给它）
"""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request


def post(base: str, path: str, body: dict, token: str, timeout: float = 20.0):
    data = json.dumps(body).encode("utf-8")
    h = {"Content-Type": "application/json"}
    if token:
        h["Authorization"] = "Bearer " + token
    r = urllib.request.Request(base + path, data=data, method="POST", headers=h)
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        return e.code, {"raw": e.read().decode("utf-8", "replace")[:120]}
    except Exception as e:                                   # noqa: BLE001
        return 0, {"error": "%s: %s" % (type(e).__name__, str(e)[:120])}


def main() -> int:
    if len(sys.argv) < 4:
        print(json.dumps({"error": "需要 <hub_base> <token> <terminal_id>"}))
        return 2
    base, token, tid = sys.argv[1].rstrip("/"), sys.argv[2], sys.argv[3]
    out: dict = {"terminal_id": tid}
    st, body = post(base, "/hub/register", {"terminal_id": tid, "name": "wsl-probe"}, token)
    out["register"] = st
    st2, body2 = post(base, "/hub/heartbeat", {"terminal_id": tid, "since": 0, "wait": 8}, token,
                      timeout=30)
    out["heartbeat"] = st2
    out["messages"] = len((body2 or {}).get("messages") or [])
    out["payloads"] = [(m or {}).get("payload") for m in ((body2 or {}).get("messages") or [])][:5]
    print(json.dumps(out, ensure_ascii=False))
    return 0 if st == 200 else 1


if __name__ == "__main__":
    raise SystemExit(main())
