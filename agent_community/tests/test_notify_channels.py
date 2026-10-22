# -*- coding: utf-8 -*-
"""守卫：**通知渠道**（V80 §B.3 四格）。

① 没配时说清"没配"（不是 500）；② webhook 用本地临时 HTTP 服务真发一次并收到；
③ SMTP 没配 → 明确报缺哪几项；④ 响应里**不出现**凭据明文。
"""
from __future__ import annotations

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.append(str(_ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from agent_community.platform import server as srv  # noqa: E402

C = TestClient(srv.app, client=("127.0.0.1", 54321))


def test_unconfigured_says_so(monkeypatch):
    for k in ("AC_WEBHOOK_URL", "AC_SMTP_HOST", "AC_SMTP_FROM", "AC_SMTP_TO"):
        monkeypatch.delenv(k, raising=False)
    d = C.get("/api/notify/channels").json()
    assert d["channels"]["webhook"]["configured"] is False
    assert "AC_WEBHOOK_URL" in d["channels"]["webhook"]["why"]
    r = C.post("/api/notify/test", json={"channel": "webhook"})
    assert r.status_code == 200 and r.json()["ok"] is False, r.text
    assert "AC_WEBHOOK_URL" in r.text


def test_webhook_really_posts(monkeypatch):
    got = {}

    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            n = int(self.headers.get("Content-Length") or 0)
            got["body"] = self.rfile.read(n).decode("utf-8", "replace")
            self.send_response(204)
            self.end_headers()

        def log_message(self, *a):
            pass

    srv_ = HTTPServer(("127.0.0.1", 0), H)
    port = srv_.server_address[1]
    th = threading.Thread(target=srv_.serve_forever, daemon=True)
    th.start()
    try:
        monkeypatch.setenv("AC_WEBHOOK_URL", "http://127.0.0.1:%d/hook" % port)
        r = C.post("/api/notify/test", json={"channel": "webhook", "text": "测试内容"}).json()
        assert r["ok"] is True, r
        assert "测试内容" in json.loads(got["body"])["text"], got
    finally:
        srv_.shutdown()


def test_smtp_missing_fields(monkeypatch):
    monkeypatch.setenv("AC_SMTP_HOST", "smtp.example.invalid")
    monkeypatch.delenv("AC_SMTP_FROM", raising=False)
    monkeypatch.delenv("AC_SMTP_TO", raising=False)
    r = C.post("/api/notify/test", json={"channel": "smtp"}).json()
    assert r["ok"] is False and "AC_SMTP_FROM" in r["why"] and "AC_SMTP_TO" in r["why"], r


def test_no_secret_leak(monkeypatch):
    monkeypatch.setenv("AC_WEBHOOK_URL", "http://127.0.0.1:1/hook")
    monkeypatch.setenv("AC_WEBHOOK_HEADERS", json.dumps({"Authorization": "Bearer 超级机密令牌"}))
    monkeypatch.setenv("AC_SMTP_PASSWORD", "邮件密码机密")
    d = C.get("/api/notify/channels")
    r = C.post("/api/notify/test", json={"channel": "webhook"})
    for blob in (d.text, r.text):
        assert "超级机密令牌" not in blob and "邮件密码机密" not in blob, blob[:200]
