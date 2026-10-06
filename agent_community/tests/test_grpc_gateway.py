# -*- coding: utf-8 -*-
"""P2 gRPC 网关冒烟测试（`agent_community/grpc_gateway.py`）。

**方法论**（按 17 号任务派发给的参考）：
  - 同进程 `ThreadingHTTPServer` 线程当"平台 REST"（**不用 Popen 子进程** —— 会被安全机制挂起）；
  - 真起 gRPC server（`build_server(0)` 拿系统分配端口）→ 用**真 gRPC 客户端**打它；
  - 断言的是**信封式转发**：gRPC 请求体 `payload_json` 原样成为 REST 请求体，
    REST 响应再包回 `payload_json`/`ok`/`error` —— 即"与 HTTP 接口零协议漂移"这条设计契约。

不碰真实平台：BASE_URL 指向本测试起的假 REST。
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

grpc = pytest.importorskip("grpc", reason="grpcio 未安装（可选依赖）")

from agent_community import grpc_gateway as gw  # noqa: E402
from agent_community.proto import (  # noqa: E402
    agent_community_pb2 as pb2,
    agent_community_pb2_grpc as pb2_grpc,
)

# ── 假平台 REST：记录收到的请求，返回可断言的响应 ──────────────────
CALLS: list[dict] = []


class _H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _reply(self, obj: dict, code: int = 200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _record(self, body: dict | None):
        CALLS.append({"method": self.command, "path": self.path,
                      "auth": self.headers.get("Authorization", ""), "body": body})

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n).decode("utf-8") if n else ""
        body = json.loads(raw) if raw else None
        self._record(body)
        if self.path == "/api/harness/register":
            self._reply({"success": True, "agent_token": "tok-xyz", "echo": body})
        elif self.path == "/api/harness/message":
            self._reply({"success": True})
        elif self.path == "/api/workshop":
            self._reply({"success": True, "workshop_id": "ws_grpc_1"})
        elif self.path == "/api/harness/task-result":
            self._reply({"success": False, "error": "缺少 member_id"})
        else:
            self._reply({"success": True})

    def do_GET(self):
        self._record(None)
        if self.path.startswith("/api/task/"):
            self._reply({"task_id": self.path.rsplit("/", 1)[-1], "status": "working"})
        elif self.path == "/api/protocols":
            self._reply({"protocols": [{"id": "grpc", "status": "available"}]})
        else:
            self._reply({"ok": True})

    def log_message(self, *a):
        pass


@pytest.fixture
def fake_rest():
    """同进程起的假平台 REST（线程版），返回其基址。"""
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _H)
    srv.daemon_threads = True
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    yield base
    srv.shutdown()
    srv.server_close()


@pytest.fixture
def gateway(fake_rest, monkeypatch):
    """把网关的 BASE_URL/TOKEN 指向假 REST，并起一个真 gRPC server。"""
    CALLS.clear()
    monkeypatch.setattr(gw, "BASE_URL", fake_rest)
    monkeypatch.setattr(gw, "TOKEN", "test-token")
    server, bound = gw.build_server(0)
    yield bound
    server.stop(0)


@pytest.fixture
def stub(gateway):
    ch = grpc.insecure_channel(f"127.0.0.1:{gateway}")
    grpc.channel_ready_future(ch).result(timeout=10)
    yield pb2_grpc.AgentCommunityStub(ch)
    ch.close()


# ── 1. 六条 RPC 全部可达，且信封原样转发 ──────────────────────────
class TestEnvelopeForwarding:
    def test_register_harness_envelope_roundtrip(self, stub):
        req = {"harness_id": "h-grpc", "name": "示例Harness-A", "nested": {"a": [1, 2]}}
        out = stub.RegisterHarness(pb2.RegisterRequest(payload_json=json.dumps(req)))
        payload = json.loads(out.payload_json)
        assert payload["success"] is True
        assert payload["agent_token"] == "tok-xyz"
        # 关键：REST 收到的 body 与 gRPC 里的 payload_json 完全一致（零漂移）
        assert payload["echo"] == req
        assert CALLS[-1]["path"] == "/api/harness/register"
        assert CALLS[-1]["body"] == req
        assert CALLS[-1]["auth"] == "Bearer test-token"

    def test_send_message_returns_ok(self, stub):
        out = stub.SendMessage(pb2.HarnessMessage(payload_json='{"text":"hi"}',
                                                 agent_token="agent-tok"))
        assert out.ok is True and out.error == ""
        assert CALLS[-1]["body"] == {"text": "hi"}

    def test_submit_task_maps_workshop_id_to_task_id(self, stub):
        out = stub.SubmitTask(pb2.SubmitTaskRequest(task_id="t1",
                                                   payload_json='{"title":"x"}'))
        assert out.ok is True and out.task_id == "ws_grpc_1"

    def test_get_task_returns_status(self, stub):
        out = stub.GetTask(pb2.GetTaskRequest(task_id="task-42"))
        assert out.task_id == "task-42"
        assert out.status == "working"
        assert json.loads(out.payload_json)["status"] == "working"

    def test_report_result_propagates_error_verbatim(self, stub):
        """REST 的失败必须如实回传（含 error 文本），不得吞掉或假报成功。"""
        out = stub.ReportResult(pb2.TaskResult(task_id="t1", payload_json="{}"))
        assert out.ok is False
        assert out.error == "缺少 member_id"

    def test_list_protocols_envelope(self, stub):
        out = stub.ListProtocols(pb2.ProtocolsRequest())
        payload = json.loads(out.payload_json)
        assert payload["protocols"][0]["id"] == "grpc"


# ── 2. 边界与诚实性 ──────────────────────────────────────────────
class TestEdges:
    def test_empty_payload_json_treated_as_empty_object(self, stub):
        out = stub.SendMessage(pb2.HarnessMessage(payload_json=""))
        assert out.ok is True
        assert CALLS[-1]["body"] == {}

    def test_no_token_means_no_auth_header(self, gateway, monkeypatch):
        monkeypatch.setattr(gw, "TOKEN", "")
        ch = grpc.insecure_channel(f"127.0.0.1:{gateway}")
        try:
            grpc.channel_ready_future(ch).result(timeout=10)
            pb2_grpc.AgentCommunityStub(ch).SendMessage(pb2.HarnessMessage(payload_json="{}"))
            assert CALLS[-1]["auth"] == ""
        finally:
            ch.close()

    def test_rest_down_raises_grpc_error_not_silent_success(self, stub, monkeypatch):
        """平台 REST 挂了：gRPC 层必须**报错**，绝不能返回一个"成功"的信封。"""
        monkeypatch.setattr(gw, "BASE_URL", "http://127.0.0.1:1")   # 必然连不上
        with pytest.raises(grpc.RpcError):
            stub.SendMessage(pb2.HarnessMessage(payload_json="{}"))

    def test_build_server_returns_bound_port(self):
        server, bound = gw.build_server(0)
        try:
            assert isinstance(bound, int) and bound > 0
        finally:
            server.stop(0)
