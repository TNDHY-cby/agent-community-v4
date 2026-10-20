# -*- coding: utf-8 -*-
"""终端侧中转客户端（V66 M1）—— **主动出站**连 Hub，收/发跨端消息。

设计要点（都在 `V66` 设计稿里）：

- **只出站**：本模块从不监听任何端口；平台不需要公网 IP、不需要隧道；
- **长轮询**：一个后台线程按 Hub 给的 `poll_max_s` 挂着等消息，收到就落进本地收件箱；
- **本地收件箱**：`<AC_DATA_DIR>/relay_inbox.json`（有上限、会过期，**不落业务数据**）；
- **失败要说话**：连不上 Hub 就如实把 `last_error` 记下来并保留最后成功时间，不假装在线。
"""
from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional

_LOCK = threading.Lock()
_THREAD: Optional[threading.Thread] = None
_STOP = threading.Event()

#: 运行态（只读给界面看）
STATE: dict = {
    "configured": False, "hub_url": "", "terminal_id": "", "connected": False,
    "last_ok": 0.0, "last_error": "", "cursor": 0, "received": 0, "sent": 0,
}


def _env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or "").strip() or default


def hub_url() -> str:
    return _env("AC_HUB_URL").rstrip("/")


def terminal_id() -> str:
    return _env("AC_HUB_TERMINAL_ID")


def hub_token() -> str:
    return _env("AC_HUB_TOKEN")


def is_configured() -> bool:
    return bool(hub_url() and terminal_id())


def inbox_file() -> Path:
    from .identity import _default_data_root
    return Path(_default_data_root()) / "relay_inbox.json"


def _inbox_max() -> int:
    v = _env("AC_HUB_INBOX_MAX", "200")
    try:
        return max(10, int(v))
    except Exception:                                        # noqa: BLE001
        return 200


def load_inbox() -> dict:
    p = inbox_file()
    if not p.is_file():
        return {"messages": [], "cursor": 0}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("messages"), list):
            return data
    except Exception:                                        # noqa: BLE001
        pass
    return {"messages": [], "cursor": 0}


def save_inbox(data: dict) -> None:
    p = inbox_file()
    p.parent.mkdir(parents=True, exist_ok=True)
    msgs = list(data.get("messages") or [])[-_inbox_max():]
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps({"messages": msgs, "cursor": int(data.get("cursor") or 0)},
                              ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, p)


def _post(path: str, body: dict, timeout: float = 30.0) -> tuple[int, dict]:
    """向 Hub 发一个请求（**出站**）。返回 `(状态码, JSON)`；网络错误时返回 `(0, {...})`。"""
    base = hub_url()
    if not base:
        return 0, {"error": "没配置 AC_HUB_URL（中转服务器地址）"}
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    h = {"Content-Type": "application/json"}
    tok = hub_token()
    if tok:
        h["Authorization"] = "Bearer " + tok
    r = urllib.request.Request(base + path, data=data, method="POST", headers=h)
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(raw)
        except Exception:                                    # noqa: BLE001
            return e.code, {"error": raw[:200]}
    except Exception as e:                                   # noqa: BLE001
        return 0, {"error": "%s: %s" % (type(e).__name__, str(e)[:160])}


def register() -> tuple[int, dict]:
    """登记到 Hub（**每次重连都调**；Hub 重启后靠这个自动回来）。"""
    st, body = _post("/hub/register", {"terminal_id": terminal_id(),
                                      "name": _env("AC_HUB_TERMINAL_NAME", terminal_id())})
    with _LOCK:
        STATE["configured"] = is_configured()
        STATE["hub_url"] = hub_url()
        STATE["terminal_id"] = terminal_id()
        if st == 200:
            STATE["connected"] = True
            STATE["last_ok"] = time.time()
            STATE["last_error"] = ""
        else:
            STATE["connected"] = False
            STATE["last_error"] = str((body or {}).get("error") or "登记失败（HTTP %s）" % st)
    return st, body


def send(to_terminal: str, payload, kind: str = "text") -> tuple[int, dict]:
    """把一条内容发给另一个终端（经 Hub）。"""
    st, body = _post("/hub/send", {"from": terminal_id(), "to": to_terminal,
                                   "kind": kind, "payload": payload})
    if st == 200:
        with _LOCK:
            STATE["sent"] = int(STATE.get("sent") or 0) + 1
            STATE["last_ok"] = time.time()
    return st, body


def poll_once(wait: float = 0.0) -> dict:
    """拉一次（`wait>0` 就是长轮询）。收到的消息追加进本地收件箱。"""
    data = load_inbox()
    since = int(data.get("cursor") or 0)
    st, body = _post("/hub/heartbeat", {"terminal_id": terminal_id(), "since": since,
                                        "wait": wait}, timeout=max(15.0, wait + 15.0))
    with _LOCK:
        STATE["configured"] = is_configured()
        STATE["hub_url"] = hub_url()
        STATE["terminal_id"] = terminal_id()
        if st == 200:
            STATE["connected"] = True
            STATE["last_ok"] = time.time()
            STATE["last_error"] = ""
        else:
            STATE["connected"] = False
            STATE["last_error"] = str((body or {}).get("error") or "取消息失败（HTTP %s）" % st)
    msgs = (body or {}).get("messages") or []
    if msgs:
        data.setdefault("messages", []).extend(msgs)
        data["cursor"] = int((body or {}).get("cursor") or since)
        save_inbox(data)
        with _LOCK:
            STATE["cursor"] = data["cursor"]
            STATE["received"] = int(STATE.get("received") or 0) + len(msgs)
    return {"status": st, "received": len(msgs), "cursor": int(data.get("cursor") or 0)}


def _loop() -> None:
    """后台线程：登记 → 长轮询 → 出错退避重连（**不打扰主流程**）。"""
    backoff = 2.0
    while not _STOP.is_set():
        try:
            st, body = register()
            if st != 200:
                _STOP.wait(backoff)
                backoff = min(60.0, backoff * 2)
                continue
            backoff = 2.0
            wait = float((body or {}).get("poll_max_s") or 25)
            while not _STOP.is_set():
                r = poll_once(wait=wait)
                if r["status"] != 200:
                    break                                  # 出去重连
        except Exception as e:                               # noqa: BLE001
            with _LOCK:
                STATE["connected"] = False
                STATE["last_error"] = "%s: %s" % (type(e).__name__, str(e)[:120])
            _STOP.wait(backoff)
            backoff = min(60.0, backoff * 2)


def start() -> bool:
    """按配置启动后台线程。**没配 `AC_HUB_URL`/`AC_HUB_TERMINAL_ID` 就不启动**（返回 False）。"""
    global _THREAD
    if not is_configured():
        with _LOCK:
            STATE["configured"] = False
        return False
    if _THREAD and _THREAD.is_alive():
        return True
    _STOP.clear()
    _THREAD = threading.Thread(target=_loop, name="relay-client", daemon=True)
    _THREAD.start()
    print("[relay] 已连中转：%s（终端 %s）" % (hub_url(), terminal_id()), flush=True)
    return True


def stop() -> None:
    global _THREAD
    _STOP.set()
    _THREAD = None


def status() -> dict:
    with _LOCK:
        out = dict(STATE)
    out["running"] = bool(_THREAD and _THREAD.is_alive())
    out["inbox_count"] = len((load_inbox().get("messages") or []))
    out["note"] = ("没配置中转（AC_HUB_URL / AC_HUB_TERMINAL_ID 为空）——本机一切照常，跨端不可用"
                   if not is_configured() else
                   ("已连接中转" if out.get("connected") else
                    "中转不可达：%s" % (out.get("last_error") or "未知原因")))
    return out
