# -*- coding: utf-8 -*-
"""中转服务器（Hub）—— **本地优先架构里的"服务器"那一半**（V66）。

```bash
# 在"大家都能连到"的那台机器上跑（云主机/内网服务器都行）
set AC_HUB_TOKEN=your-hub-token        # PowerShell: $env:AC_HUB_TOKEN="your-hub-token"
python tools/relay_hub.py --port 9400
```

## 它只做三件事（再多就不该放在这里）

1. **终端登记与在线状态**：谁在线、最后什么时候来过；
2. **消息转发**：按 `to_terminal` 投进对方队列（终端**主动来取**，Hub 从不主动连终端）；
3. **有限暂存**：待取消息有**条数上限**（`AC_HUB_QUEUE_MAX`）与**过期时间**（`AC_HUB_TTL`），过期即丢。

## 它**不**做什么（这条决定架构干净不干净）

- **不存业务数据**（工作间/审计/成员都在终端自己机器上）；
- **不主动连终端**（那等于在 Hub 上开出站口，问题只是搬了个地方）；
- **不代理管理面**（终端的管理端点永不出网）；
- 不绑任何服务商——这就是一份能跑在任何人机器上的标准库程序。

## 环境变量（全部可选，除 token）

| 变量 | 默认 | 说明 |
|---|---|---|
| `AC_HUB_TOKEN` | 空 | 共享密钥；**空则不校验**（只适合本机自测，启动时会有提示） |
| `AC_HUB_PORT` | `9400` | 监听端口（命令行 `--port` 优先） |
| `AC_HUB_HOST` | `0.0.0.0` | 监听地址 |
| `AC_HUB_QUEUE_MAX` | `200` | 每个终端队列上限（超了按最旧丢） |
| `AC_HUB_TTL` | `86400` | 消息存活秒数（过期即丢） |
| `AC_HUB_POLL_MAX` | `25` | 长轮询最长挂起秒数 |
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

_LOCK = threading.Condition()
#: terminal_id -> {"name": str, "last_seen": float}
TERMINALS: dict[str, dict] = {}
#: terminal_id -> [{"seq","from","kind","payload","at"}]（有上限、会过期）
QUEUES: dict[str, list] = {}
_SEQ = 0


def _env_int(name: str, default: int, lo: int = 1) -> int:
    v = (os.environ.get(name) or "").strip()
    try:
        n = int(v) if v else default
    except Exception:                                        # noqa: BLE001
        n = default
    return max(lo, n)


def queue_max() -> int:
    return _env_int("AC_HUB_QUEUE_MAX", 200)


def ttl() -> int:
    return _env_int("AC_HUB_TTL", 86400, lo=1)


def poll_max() -> int:
    return _env_int("AC_HUB_POLL_MAX", 25, lo=1)


def hub_token() -> str:
    return (os.environ.get("AC_HUB_TOKEN") or "").strip()


def hub_shared() -> bool:
    """共享形态（V68）：一台 Hub 服务多家公司 ⇒ 按公司隔离。默认**私有**（所有终端互通）。"""
    return (os.environ.get("AC_HUB_SHARED") or "").strip().lower() in ("1", "true", "yes", "on")


def allow_cross() -> bool:
    """共享形态下是否**允许**跨公司（默认不允许；允许时每条消息还要显式带 `allow_cross`）。"""
    return (os.environ.get("AC_HUB_ALLOW_CROSS") or "").strip().lower() in ("1", "true", "yes", "on")


def company_of(tid: str) -> str:
    rec = TERMINALS.get(tid) or {}
    return str(rec.get("company") or "default")


def _now() -> float:
    return time.time()


def _expire() -> None:
    """清掉过期消息（**必须调用；否则暂存会无限长**）。"""
    horizon = _now() - ttl()
    for tid in list(QUEUES):
        q = [m for m in QUEUES[tid] if float(m.get("at") or 0) >= horizon]
        if q:
            QUEUES[tid] = q
        else:
            QUEUES.pop(tid, None)


def put_message(to: str, frm: str, kind: str, payload) -> dict:
    """投递一条（**不推进=不算送达**：只进队列，由对方来取）。"""
    global _SEQ
    with _LOCK:
        _SEQ += 1
        msg = {"seq": _SEQ, "from": frm, "kind": kind, "payload": payload, "at": _now()}
        q = QUEUES.setdefault(to, [])
        q.append(msg)
        over = len(q) - queue_max()
        dropped = 0
        if over > 0:                                         # 超上限按最旧丢（并如实告诉调用方）
            dropped = over
            del q[:over]
        _LOCK.notify_all()
        return {"queued": True, "seq": msg["seq"], "dropped": dropped,
                "online": bool(TERMINALS.get(to)),
                "queue_len": len(q)}


def take_messages(tid: str, since: int, wait: float) -> list:
    """取这个终端的消息（长轮询：没新消息就最多挂 `wait` 秒）。"""
    deadline = _now() + max(0.0, wait)
    with _LOCK:
        while True:
            _expire()
            q = QUEUES.get(tid) or []
            out = [m for m in q if int(m.get("seq") or 0) > int(since or 0)]
            if out:
                return out
            left = deadline - _now()
            if left <= 0:
                return []
            _LOCK.wait(min(left, 1.0))


class Handler(BaseHTTPRequestHandler):
    server_version = "ACRelayHub/1.0"

    def log_message(self, fmt, *args):                       # 静默：只打自己的日志
        return

    # ── 工具 ────────────────────────────────────────────────
    def _json(self, code: int, obj: dict) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except Exception:                                    # noqa: BLE001
            pass

    def _auth(self) -> bool:
        want = hub_token()
        if not want:
            return True                                      # 未设 token：放行（启动时已提示）
        got = (self.headers.get("Authorization") or "")
        got = got[7:].strip() if got.startswith("Bearer ") else got.strip()
        if not got:
            got = (self.headers.get("X-Hub-Token") or "").strip()
        return got == want

    def _body(self) -> dict:
        try:
            n = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(n) if n else b"{}"
            data = json.loads(raw.decode("utf-8", "replace") or "{}")
            return data if isinstance(data, dict) else {}
        except Exception:                                    # noqa: BLE001
            return {}

    # ── 路由 ────────────────────────────────────────────────
    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/hub/health":
            return self._json(200, {"ok": True, "terminals": len(TERMINALS),
                                    "queue_max": queue_max(), "ttl_s": ttl(),
                                    "shared": hub_shared(), "allow_cross": allow_cross()})
        if path == "/hub/terminals":
            if not self._auth():
                return self._json(401, {"error": "需要 Hub 令牌"})
            with _LOCK:
                _expire()
                items = [{"terminal_id": tid, "name": (rec.get("name") or ""),
                          "company": (rec.get("company") or "default"),
                          "last_seen": float(rec.get("last_seen") or 0),
                          "age_s": round(_now() - float(rec.get("last_seen") or 0), 1),
                          "queue_len": len(QUEUES.get(tid) or [])}
                         for tid, rec in sorted(TERMINALS.items())]
                return self._json(200, {"terminals": items, "total": len(items),
                                        "shared": hub_shared()})
        return self._json(404, {"error": "未知路径：%s" % path})

    def do_POST(self):
        path = self.path.split("?")[0]
        body = self._body()
        if not self._auth():
            return self._json(401, {"error": "需要 Hub 令牌", "how_to_fix": "请求带 Authorization: Bearer <AC_HUB_TOKEN>"})

        if path == "/hub/register":
            tid = str(body.get("terminal_id") or "").strip()
            if not tid:
                return self._json(400, {"error": "terminal_id 必填（自己起个名字，如 home-desktop）"})
            company = str(body.get("company_id") or "").strip() or "default"
            with _LOCK:
                old = TERMINALS.get(tid) or {}
                if hub_shared() and old and str(old.get("company") or "default") != company:
                    # 同名终端换公司 = 要么是打错了，要么想蹭别人的名字 ⇒ 拒绝并说清
                    return self._json(409, {
                        "error": "这个 terminal_id 已经被别的公司登记过",
                        "terminal_id": tid,
                        "registered_company": str(old.get("company") or "default"),
                        "how_to_fix": "换一个 terminal_id（每个公司的终端名不要重名）",
                    })
                TERMINALS[tid] = {"name": str(body.get("name") or tid)[:80],
                                  "company": company, "last_seen": _now()}
                _LOCK.notify_all()
            print("[hub] 登记终端 %s（%s ｜ 公司 %s）" % (tid, TERMINALS[tid]["name"], company), flush=True)
            return self._json(200, {"ok": True, "terminal_id": tid, "company": company,
                                    "poll_max_s": poll_max(), "queue_max": queue_max(),
                                    "ttl_s": ttl(), "shared": hub_shared()})

        if path == "/hub/heartbeat":
            tid = str(body.get("terminal_id") or "").strip()
            if not tid:
                return self._json(400, {"error": "terminal_id 必填"})
            since = int(body.get("since") or 0)
            wait = float(body.get("wait") or 0)
            wait = max(0.0, min(wait, float(poll_max())))
            with _LOCK:
                TERMINALS.setdefault(tid, {"name": tid, "last_seen": _now()})
                TERMINALS[tid]["last_seen"] = _now()
            msgs = take_messages(tid, since, wait)
            return self._json(200, {"messages": msgs, "now": _now(),
                                    "cursor": (msgs[-1]["seq"] if msgs else since)})

        if path == "/hub/send":
            to = str(body.get("to") or "").strip()
            frm = str(body.get("from") or "").strip()
            if not to:
                return self._json(400, {"error": "to 必填（对方 terminal_id）"})
            if not frm:
                return self._json(400, {"error": "from 必填（你是谁）"})
            # ── V68 公司隔离（只在**共享形态**下生效；私有形态保持 M1/M2 的老行为）──
            if hub_shared():
                with _LOCK:
                    known = (frm in TERMINALS) and (to in TERMINALS)
                if not known:
                    missing = frm if frm not in TERMINALS else to
                    return self._json(403, {
                        "error": "有一方还没在中转上登记（不许给看不见的终端发）",
                        "missing": missing,
                        "how_to_fix": "让对方先启动平台（它会自动登记）；或者你打错了 terminal_id",
                    })
                c_from, c_to = company_of(frm), company_of(to)
                if c_from != c_to:
                    if not (allow_cross() and bool(body.get("allow_cross"))):
                        return self._json(403, {
                            "error": "跨公司发送被拒绝",
                            "from_company": c_from, "to_company": c_to,
                            "allow_cross_enabled": allow_cross(),
                            "how_to_fix": ("这是共享中转，默认只允许公司内部发送。"
                                           "若要跨公司合作：中转侧设 AC_HUB_ALLOW_CROSS=1，"
                                           "且每条消息显式带 allow_cross=true"),
                        })
                    print("[hub] 跨公司放行：%s(%s) → %s(%s)" % (frm, c_from, to, c_to), flush=True)
            kind = str(body.get("kind") or "text")[:40]
            info = put_message(to, frm, kind, body.get("payload"))
            print("[hub] %s → %s（%s，seq=%s，对方在线=%s）" %
                  (frm, to, kind, info["seq"], info["online"]), flush=True)
            return self._json(200, {"ok": True, **info,
                                    "note": ("已投进对方队列，等它来取" if info["online"]
                                             else "对方现在不在线，已暂存（有上限、会过期）")})
        return self._json(404, {"error": "未知路径：%s" % path})


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="中转服务器（V66）")
    ap.add_argument("--host", default=(os.environ.get("AC_HUB_HOST") or "0.0.0.0"),
                    help="监听地址（默认 0.0.0.0；只本机测就填 127.0.0.1）")
    ap.add_argument("--port", type=int, default=_env_int("AC_HUB_PORT", 9400),
                    help="监听端口（默认取 AC_HUB_PORT 或 9400）")
    ap.add_argument("--certfile", default=(os.environ.get("AC_HUB_TLS_CERT") or ""),
                    help="TLS 证书（PEM）；给了就启用 HTTPS，**跨公网必须开**（否则令牌明文传输）")
    ap.add_argument("--keyfile", default=(os.environ.get("AC_HUB_TLS_KEY") or ""),
                    help="TLS 私钥（PEM）")
    a = ap.parse_args(argv)
    if not hub_token():
        print("[hub] ⚠ 未设 AC_HUB_TOKEN：**谁都能往队列里塞消息**，只适合本机自测。"
              "正式用请设 AC_HUB_TOKEN 并让终端带同一个值。", flush=True)
    srv = ThreadingHTTPServer((a.host, a.port), Handler)
    scheme = "http"
    if a.certfile and a.keyfile:
        # ★跨公网必须 TLS：HTTP 明文传 `Authorization: Bearer` 等于把钥匙贴在网上。
        import ssl
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(a.certfile, a.keyfile)
        srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
        scheme = "https"
        print("[hub] TLS 已启用（证书 %s）" % Path(a.certfile).name, flush=True)
    elif not a.certfile:
        print("[hub] ⚠ 当前是 **明文 HTTP**：只适合内网/本机自测。"
              "跨公网请用 --certfile/--keyfile 开 HTTPS（或在前面放一层带证书的反向代理）。", flush=True)
    print("[hub] 监听 %s://%s:%d ｜ 队列上限 %d 条 / 存活 %ds / 长轮询最长 %ds" %
          (scheme, a.host, a.port, queue_max(), ttl(), poll_max()), flush=True)
    print("[hub] 终端用出站连接过来登记（Hub 不会主动连终端）", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
