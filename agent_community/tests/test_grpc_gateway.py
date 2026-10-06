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
import time
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

# V18 §7.1/§7.3：让测试能把假 REST 调成任意状态码/任意慢响应（不改默认正常行为）
# status=None → 走正常按路径分支；否则一律按 SCENARIO 回
SCENARIO: dict = {"status": None, "body": None, "sleep": 0.0}


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
                      "auth": self.headers.get("Authorization", ""),
                      "agent_token": self.headers.get("x-agent-token", ""),
                      "body": body})

    def _maybe_scenario(self, body: dict | None) -> bool:
        """V18：测试把状态码/延迟调成任意值时按 SCENARIO 回（返回 True 表示已处理）。

        `body` 是 str 时按**纯文本**回（用来覆盖"上游返回非 JSON 体"的分支）。
        """
        if SCENARIO["status"] is None:
            return False
        if SCENARIO["sleep"]:
            time.sleep(SCENARIO["sleep"])
        payload = SCENARIO["body"]
        if isinstance(payload, str):
            raw = payload.encode("utf-8")
            self.send_response(int(SCENARIO["status"]))
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
        else:
            self._reply(payload if payload is not None else {"error": "scenario"},
                        int(SCENARIO["status"]))
        return True

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n).decode("utf-8") if n else ""
        body = json.loads(raw) if raw else None
        self._record(body)
        if self._maybe_scenario(body):
            return
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
        if self._maybe_scenario(None):
            return
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
    SCENARIO.update(status=None, body=None, sleep=0.0)
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



# ── 3. V18 §7.1：错误码映射（表驱动）───────────────────────────────
class TestErrorMapping:
    """上游 HTTP 状态 → gRPC 状态码的映射（设计稿 §5.1）。

    这组用例锁的是"失败有语义"：修前所有失败都塌成 UNKNOWN，客户端无法分支。
    """

    @pytest.mark.parametrize("upstream,expect", [
        (400, grpc.StatusCode.INVALID_ARGUMENT),
        (401, grpc.StatusCode.UNAUTHENTICATED),
        (403, grpc.StatusCode.PERMISSION_DENIED),
        (404, grpc.StatusCode.NOT_FOUND),
        (409, grpc.StatusCode.ALREADY_EXISTS),
        (429, grpc.StatusCode.RESOURCE_EXHAUSTED),
        (500, grpc.StatusCode.INTERNAL),
        (503, grpc.StatusCode.UNAVAILABLE),
        (418, grpc.StatusCode.INTERNAL),          # 未列出 → 兜底 INTERNAL
    ])
    def test_upstream_status_maps_to_grpc_code(self, stub, upstream, expect):
        SCENARIO.update(status=upstream, body={"error": f"上游理由<{upstream}>"},
                        sleep=0.0)
        with pytest.raises(grpc.RpcError) as ei:
            stub.GetTask(pb2.GetTaskRequest(task_id="t"))
        assert ei.value.code() == expect, f"{upstream} 应映射 {expect}"
        det = str(ei.value.details() or "")
        assert f"upstream={upstream}" in det, "details 必须带上游状态码便于对账"
        assert f"上游理由<{upstream}>" in det

    def test_not_found_details_carry_upstream_message(self, stub):
        """D1 的直接靶点：修前是 UNKNOWN + 'HTTP Error 404'。"""
        SCENARIO.update(status=404, body={"error": "任务不存在"}, sleep=0.0)
        with pytest.raises(grpc.RpcError) as ei:
            stub.GetTask(pb2.GetTaskRequest(task_id="__none__"))
        assert ei.value.code() == grpc.StatusCode.NOT_FOUND
        det = str(ei.value.details() or "")
        assert "任务不存在" in det and "upstream=404" in det

    def test_connection_refused_maps_unavailable(self, gateway, monkeypatch):
        """D3：平台不可达不得塌成 UNKNOWN。"""
        monkeypatch.setattr(gw, "BASE_URL", "http://127.0.0.1:1")
        ch = grpc.insecure_channel(f"127.0.0.1:{gateway}")
        try:
            grpc.channel_ready_future(ch).result(timeout=10)
            with pytest.raises(grpc.RpcError) as ei:
                pb2_grpc.AgentCommunityStub(ch).ListProtocols(pb2.ProtocolsRequest())
            assert ei.value.code() == grpc.StatusCode.UNAVAILABLE
        finally:
            ch.close()

    def test_invalid_payload_json_is_invalid_argument_and_not_forwarded(self, stub):
        SCENARIO.update(status=None)
        with pytest.raises(grpc.RpcError) as ei:
            stub.SendMessage(pb2.HarnessMessage(payload_json="{not json"))
        assert ei.value.code() == grpc.StatusCode.INVALID_ARGUMENT
        assert "payload_json" in str(ei.value.details())
        assert not CALLS, "坏 JSON 不得发往上游"


# ── 4. V18 §7.2：agent_token 转发（R1/R3/R4）──────────────────────
class TestTokenForwarding:
    def test_agent_token_forwarded_as_header(self, stub):
        """D2：proto 里的 agent_token 必须变成平台认的 x-agent-token 头。"""
        out = stub.SendMessage(pb2.HarnessMessage(payload_json='{"harness_id":"h1"}',
                                                 agent_token="tok-abc"))
        assert out.ok is True
        assert CALLS[-1]["agent_token"] == "tok-abc"

    def test_empty_token_omits_header(self, stub):
        """R1：为空**不注入** —— 继承平台"未签发放行"的向后兼容。"""
        stub.SendMessage(pb2.HarnessMessage(payload_json='{"harness_id":"h1"}', agent_token=""))
        assert CALLS[-1]["agent_token"] == ""

    def test_submit_task_forwards_token(self, stub):
        """拍板 ①：SubmitTask 虽然后端当前不校验 P1，也一并转发（一致性）。"""
        stub.SubmitTask(pb2.SubmitTaskRequest(payload_json='{"mode":"parallel"}',
                                             agent_token="tok-x"))
        assert CALLS[-1]["agent_token"] == "tok-x"

    def test_gateway_does_not_verify_locally(self, stub):
        """R3：网关**不本地校验** —— 假 token 照常转发，平台回 401 时才翻译成 UNAUTHENTICATED。

        这条同时守住 R4：401 不得被改写成别的码。
        """
        SCENARIO.update(status=401, body={"error": "缺少身份凭证头 x-agent-token"}, sleep=0.0)
        with pytest.raises(grpc.RpcError) as ei:
            stub.SendMessage(pb2.HarnessMessage(payload_json='{"harness_id":"h1"}',
                                                 agent_token="bogus"))
        assert CALLS and CALLS[-1]["agent_token"] == "bogus", "网关不得在本地把请求毙掉"
        assert ei.value.code() == grpc.StatusCode.UNAUTHENTICATED

    def test_list_protocols_and_gettask_never_inject_token(self, stub):
        """R2：不涉及该字段的 RPC 不得注入头。"""
        stub.ListProtocols(pb2.ProtocolsRequest())
        stub.GetTask(pb2.GetTaskRequest(task_id="t"))
        assert CALLS, "应有上游调用记录"
        for c in CALLS:
            assert c["agent_token"] == ""


# ── 5. V18 §7.3：deadline 与 details 卫生 ──────────────────────────
class TestDeadlineAndHygiene:
    def test_client_deadline_exceeded(self, stub):
        """客户端设 deadline 且上游慢 → DEADLINE_EXCEEDED（不是 UNKNOWN，也不是干等到默认超时）。"""
        SCENARIO.update(status=200, body={"status": "running"}, sleep=3.0)
        with pytest.raises(grpc.RpcError) as ei:
            stub.ListProtocols(pb2.ProtocolsRequest(), timeout=0.5)
        assert ei.value.code() == grpc.StatusCode.DEADLINE_EXCEEDED

    def test_details_hide_local_paths_and_tracebacks(self, stub):
        """卫生：details 里不得出现本地绝对路径与 Traceback（对外纪律）。"""
        bad = ('Traceback (most recent call last):\n'
               '  File "D:\\proj\\app.py", line 1, in <module>\n'
               'RuntimeError: boom at D:\\proj\\app.py')
        SCENARIO.update(status=500, body={"error": bad}, sleep=0.0)
        with pytest.raises(grpc.RpcError) as ei:
            stub.GetTask(pb2.GetTaskRequest(task_id="t"))
        det = str(ei.value.details() or "")
        assert ei.value.code() == grpc.StatusCode.INTERNAL
        assert "Traceback" not in det and "D:" not in det, det
        assert "upstream=500" in det
        assert len(det) <= 300, "details 必须截断"

    def test_upstream_raw_body_fallback_still_sanitized(self, stub):
        """上游返回**非 JSON** 体（raw 分支）也要走同一套卫生与映射。"""
        SCENARIO.update(status=502,
                        body="boom at D:\\x\\y Traceback (most recent call last)",
                        sleep=0.0)
        with pytest.raises(grpc.RpcError) as ei:
            stub.ListProtocols(pb2.ProtocolsRequest())
        assert ei.value.code() == grpc.StatusCode.UNAVAILABLE
        det = str(ei.value.details() or "")
        assert "Traceback" not in det and "D:" not in det, det
        assert "upstream=502" in det
