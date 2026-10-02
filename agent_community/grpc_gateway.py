# -*- coding: utf-8 -*-
"""agent_community/grpc_gateway — A2A gRPC 绑定网关（独立进程，端口 9105）。

⚠️ 可选依赖：需要 grpcio + grpcio-tools。未安装时本模块优雅降级：
  - import grpc 失败 → 打印安装提示并退出
  - 协议目录里 gRPC 自动标 disabled

信封式设计：payload_json 承载复杂业务结构，与 HTTP 接口零协议漂移。
内部转发到平台 REST API（默认 127.0.0.1:18920），P1 校验 / P2 审计天然生效。

启动：
    python -m agent_community.grpc_gateway --port 9105

生成桩（首次/更新 proto 后）：
    python -m grpc_tools.protoc -I agent_community/proto \
        --python_out=agent_community/proto \
        --grpc_python_out=agent_community/proto \
        agent_community/proto/agent_community.proto
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request

# ── 可选依赖检查 ──────────────────────────────────────────────
try:
    import grpc
    from concurrent import futures
    HAS_GRPC = True
except ImportError:
    HAS_GRPC = False

BASE_URL = os.environ.get("AC4_BASE_URL", "http://127.0.0.1:18920").rstrip("/")
TOKEN = os.environ.get("AC4_TOKEN", "")


def _headers() -> dict:
    h = {"Content-Type": "application/json", "Accept": "application/json"}
    if TOKEN:
        h["Authorization"] = f"Bearer {TOKEN}"
    return h


def _call(method: str, path: str, payload: dict | None = None, timeout: int = 30):
    url = BASE_URL + path
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, headers=_headers(), method=method)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8", "replace")
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"raw": raw}


def _post(path: str, payload: dict, timeout: int = 60):
    return _call("POST", path, payload, timeout=timeout)


# ── gRPC Servicer（需 grpcio）─────────────────────────────────
if HAS_GRPC:
    # 尝试导入生成的桩；未生成则提示
    try:
        from .proto import agent_community_pb2, agent_community_pb2_grpc

        class AgentCommunityServicer(agent_community_pb2_grpc.AgentCommunityServicer):
            """把 gRPC 调用转发到平台 REST API（信封式，零协议漂移）。"""

            def RegisterHarness(self, request, context):
                payload = json.loads(request.payload_json or "{}")
                resp = _post("/api/harness/register", payload)
                return agent_community_pb2.RegisterReply(
                    payload_json=json.dumps(resp, ensure_ascii=False)
                )

            def SendMessage(self, request, context):
                payload = json.loads(request.payload_json or "{}")
                # P1 身份校验由平台 REST 层处理
                resp = _post("/api/harness/message", payload)
                ok = resp.get("success", False)
                return agent_community_pb2.MessageAck(ok=ok, error=str(resp.get("error", "")))

            def SubmitTask(self, request, context):
                payload = json.loads(request.payload_json or "{}")
                resp = _post("/api/workshop", payload)
                ok = bool(resp.get("success") or resp.get("workshop_id"))
                return agent_community_pb2.TaskAck(
                    ok=ok,
                    task_id=str(resp.get("workshop_id", "")),
                    error=str(resp.get("error", "")),
                )

            def GetTask(self, request, context):
                resp = _call("GET", f"/api/task/{request.task_id}")
                return agent_community_pb2.TaskStatus(
                    task_id=request.task_id,
                    status=str(resp.get("status", "")),
                    payload_json=json.dumps(resp, ensure_ascii=False),
                )

            def ReportResult(self, request, context):
                payload = json.loads(request.payload_json or "{}")
                resp = _post("/api/harness/task-result", payload)
                ok = resp.get("success", False)
                return agent_community_pb2.ResultAck(ok=ok, error=str(resp.get("error", "")))

            def ListProtocols(self, request, context):
                resp = _call("GET", "/api/protocols")
                return agent_community_pb2.ProtocolsReply(
                    payload_json=json.dumps(resp, ensure_ascii=False)
                )

        def serve(port: int):
            server = grpc.server(futures.ThreadPoolExecutor(max_workers=8))
            agent_community_pb2_grpc.add_AgentCommunityServicer_to_server(
                AgentCommunityServicer(), server
            )
            server.add_insecure_port(f"[::]:{port}")
            server.start()
            print(f"[grpc] gRPC gateway listening on :{port} (forwarding to {BASE_URL})")
            server.wait_for_termination()

    except ImportError as e:
        HAS_GRPC = False
        _STUB_ERROR = str(e)

        def serve(port: int):
            print(f"[grpc] 桩代码未生成（{e}）")
            print("[grpc] 请先运行：python -m grpc_tools.protoc ...（见模块文档）")
            sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser(description="Agent Community gRPC Gateway")
    parser.add_argument("--port", type=int, default=9105, help="gRPC 端口（默认 9105）")
    parser.add_argument("--api", default=None, help="平台 REST 基址（默认 AC4_BASE_URL 或 127.0.0.1:18920）")
    args = parser.parse_args()

    if args.api:
        global BASE_URL
        BASE_URL = args.api.rstrip("/")

    if not HAS_GRPC:
        print("[grpc] 未安装 grpcio —— gRPC 网关不可用。")
        print("[grpc] 启用方式：pip install grpcio grpcio-tools")
        print("[grpc] 然后生成桩：python -m grpc_tools.protoc ...")
        sys.exit(1)

    serve(args.port)


if __name__ == "__main__":
    main()
