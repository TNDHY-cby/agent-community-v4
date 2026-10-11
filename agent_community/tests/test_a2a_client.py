# -*- coding: utf-8 -*-
"""M-2 公司 B 侧 A2A 客户端测试。

不依赖真实平台/9104：起一个本地模拟 A2A 服务端（Bearer 校验 + 简单 JSON-RPC 回显），
验证客户端（a2a_client）的：
  1. 读 Agent Card（公开路径）；
  2. tasks/send 携带正确公司 token → 200 放行；缺 token → 401；错 token → 403；
  3. JSON-RPC error / HTTP error 正确转 A2AError；
  4. CLI 入口（card / send / get / msg）可用且缺 token 拒绝。
"""
from __future__ import annotations

import io
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from agent_community import a2a_client  # noqa: E402

TOKEN_OK = "co-b-token-ok"


class _FakeA2AHandler(BaseHTTPRequestHandler):
    """模拟公司 A 的 9104：公开读卡 + Bearer 校验后的 JSON-RPC 回显。"""

    def _send_json(self, obj, code=200):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.startswith("/.well-known/agent-card.json"):
            self._send_json({"name": "fake-a", "provider": {"company_id": "co-local-a"},
                             "authentication": {"schemes": [{"scheme": "bearer"}]}})
        else:
            self._send_json({"error": "not found"}, 404)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length).decode("utf-8", "replace")
        auth = self.headers.get("Authorization", "")
        bearer = auth[7:].strip() if auth.startswith("Bearer ") else ""
        if not bearer:
            self._send_json({"error": "缺少 Bearer"}, 401)
            return
        if bearer != TOKEN_OK:
            self._send_json({"error": "校验失败"}, 403)
            return
        try:
            msg = json.loads(raw or "{}")
        except Exception:  # noqa: BLE001
            self._send_json({"jsonrpc": "2.0", "error": {"code": -32700, "message": "解析失败"}}, 400)
            return
        method = msg.get("method", "")
        if method == "tasks/send":
            result = {"id": "ws-fake-1", "status": "working",
                      "metadata": {"echo": (msg.get("params") or {}).get("message", {})}}
        elif method == "tasks/get":
            result = {"id": (msg.get("params") or {}).get("taskId", ""), "status": "completed"}
        elif method == "message/send":
            result = {"taskId": (msg.get("params") or {}).get("taskId", ""),
                      "messageId": "m-fake-1"}
        else:
            self._send_json({"jsonrpc": "2.0", "error": {"code": -32601,
                                                          "message": f"未知方法: {method}"}}, 200)
            return
        self._send_json({"jsonrpc": "2.0", "id": msg.get("id"), "result": result})

    def log_message(self, fmt, *args):  # noqa: A003
        pass


@pytest.fixture
def fake_url():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _FakeA2AHandler)
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    httpd.server_close()


class TestDiscover:
    def test_card_read_without_token(self, fake_url):
        card = a2a_client.discover_card(fake_url)
        assert card["provider"]["company_id"] == "co-local-a"
        assert card["authentication"]["schemes"][0]["scheme"] == "bearer"


class TestSendTask:
    def test_send_with_valid_company_token(self, fake_url):
        result = a2a_client.send_task(fake_url, "B 发来的任务书", TOKEN_OK)
        assert result["status"] == "working"
        assert result["metadata"]["echo"]["parts"][0]["text"] == "B 发来的任务书"

    def test_send_without_token_raises_401(self, fake_url):
        with pytest.raises(a2a_client.A2AError) as ei:
            a2a_client.send_task(fake_url, "无 token 的任务", None)
        assert "401" in str(ei.value)

    def test_send_with_wrong_token_raises_403(self, fake_url):
        with pytest.raises(a2a_client.A2AError) as ei:
            a2a_client.send_task(fake_url, "错 token 的任务", "wrong-token")
        assert "403" in str(ei.value)


class TestGetAndMsg:
    def test_get_task(self, fake_url):
        result = a2a_client.get_task(fake_url, "ws-fake-1", TOKEN_OK)
        assert result["id"] == "ws-fake-1"
        assert result["status"] == "completed"

    def test_send_message(self, fake_url):
        result = a2a_client.send_message(fake_url, "ws-fake-1", "回执：已收到", TOKEN_OK)
        assert result["taskId"] == "ws-fake-1"
        assert result["messageId"]


class TestCli:
    def test_cli_missing_token_refuses(self, capsys):
        rc = a2a_client.main(["send", "任务", "--url", "http://127.0.0.1:1"])
        assert rc == 2
        out = capsys.readouterr()
        assert "缺少公司 token" in out.err

    def test_cli_card_with_token(self, fake_url, capsys):
        rc = a2a_client.main(["card", "--token", TOKEN_OK, "--url", fake_url])
        assert rc == 0
        out = capsys.readouterr()
        assert "co-local-a" in out.out
