# -*- coding: utf-8 -*-
"""agent_community/grpc_gateway — A2A gRPC 绑定网关（独立进程，端口 9105）。

⚠️ 可选依赖：需要 grpcio + grpcio-tools。未安装时本模块优雅降级：
  - import grpc 失败 → 打印安装提示并退出
  - 协议目录里 gRPC 自动标 disabled

信封式设计：payload_json 承载复杂业务结构，与 HTTP 接口零协议漂移。
内部转发到平台 REST API（默认 127.0.0.1:18920），P1 校验 / P2 审计天然生效。

## V18（2026-10-06 拍板实施）：鉴权与错误码语义化

设计稿：`design-docs/V18_gRPC鉴权与错误码语义化设计.md`（三条子选项按建议拍板）。

1. **错误码语义化**：上游 HTTP 状态与网关自身失败一律翻译成有语义的 gRPC 状态码
   （见 `_UPSTREAM_STATUS_MAP` 与各 `_abort_*`），不再塌成 `UNKNOWN`。
2. **agent_token 只透传、不二次校验**：三条带该字段的 RPC 把它注入 `x-agent-token` 头，
   校验**仍唯一发生在平台** `identity.check_request_token`（R3：网关不算 HMAC，
   避免"两处真源必然漂移"）；字段为空 → **不注入头**（继承平台的向后兼容放行）。
3. **details 卫生**：取上游 JSON 的 `error`/`detail` 文本，剔除本地绝对路径与 Traceback 行，
   截断 256 字符，并带上 `upstream=<status>` 便于对账。
4. **deadline 贯通**：客户端设了 deadline → 用作上游超时；超时后按"是否设过 deadline"
   区分 `DEADLINE_EXCEEDED` 与 `UNAVAILABLE`。

启动：
    python -m agent_community.grpc_gateway --port 9105

生成桩（首次/更新 proto 后）：
    python -m grpc_tools.protoc -I agent_community/proto \\
        --python_out=agent_community/proto \\
        --grpc_python_out=agent_community/proto \\
        agent_community/proto/agent_community.proto
    ⚠️ 生成后必须再跑 `temp/fix_grpc_stubs.py`（补 proto/__init__.py + 把 pb2_grpc
       的绝对导入改相对导入），否则包内导入会 ModuleNotFoundError。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import socket
import sys
import urllib.error
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

# 超时（秒）：与既有 `_call`/`_post` 的默认值一致；客户端设了更短 deadline 则以更短者为准
READ_TIMEOUT = 30.0
WRITE_TIMEOUT = 60.0
MAX_DETAILS_LEN = 256
AGENT_TOKEN_HEADER = "x-agent-token"   # 与 platform/identity.py 的约定一致

# ── V18 §5.1：上游 HTTP 状态 → gRPC 状态码（纯函数，表驱动，可变异验证）──
if HAS_GRPC:
    _UPSTREAM_STATUS_MAP = {
        400: grpc.StatusCode.INVALID_ARGUMENT,
        401: grpc.StatusCode.UNAUTHENTICATED,
        403: grpc.StatusCode.PERMISSION_DENIED,
        404: grpc.StatusCode.NOT_FOUND,
        405: grpc.StatusCode.UNIMPLEMENTED,
        409: grpc.StatusCode.ALREADY_EXISTS,
        429: grpc.StatusCode.RESOURCE_EXHAUSTED,
        500: grpc.StatusCode.INTERNAL,
        501: grpc.StatusCode.UNIMPLEMENTED,
        502: grpc.StatusCode.UNAVAILABLE,
        503: grpc.StatusCode.UNAVAILABLE,
        504: grpc.StatusCode.UNAVAILABLE,
    }


def _grpc_status_from_upstream(status: int):
    """上游 HTTP 状态 → gRPC 状态码；未列出的归为 INTERNAL（并保留 upstream= 供对账）。"""
    return _UPSTREAM_STATUS_MAP.get(int(status), grpc.StatusCode.INTERNAL)


# ── V18 §5.4：details 卫生 ─────────────────────────────────────
_ABS_PATH_RE = re.compile(r"[A-Za-z]:[\\/][^\s'\"`)\]]{1,120}")
_TRACEBACK_LINE_RE = re.compile(r"^\s*(Traceback \(most recent call last\):|File \")", re.M)
# 行首规则漏掉"Traceback 出现在行中"的形态（实测：纯文本上游体是一整行，
# 消息里夹带 Traceback 标记），故再按字面清一次
_TRACEBACK_MARK = "Traceback (most recent call last)"


def _strip_unsafe(text: str) -> str:
    """剔除本地绝对路径与 Traceback（对外 details 不得泄漏本机布局/调用栈）。"""
    if not text:
        return ""
    lines = []
    for ln in str(text).splitlines():
        if _TRACEBACK_LINE_RE.search(ln):
            continue
        ln = _ABS_PATH_RE.sub("<path>", ln)
        ln = ln.replace(_TRACEBACK_MARK, "").replace("  File \"<path>\"", "")
        if ln.strip():
            lines.append(ln)
    return "\n".join(lines).strip()


def _details_from_body(status: int, body) -> str:
    """从上游响应体取可读错误文本，做卫生处理后附 `upstream=<status>`。"""
    raw = ""
    if isinstance(body, dict):
        for key in ("error", "detail", "message", "raw"):
            v = body.get(key)
            if v:
                raw = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)
                break
    elif isinstance(body, str):
        raw = body
    raw = _strip_unsafe(raw)
    if len(raw) > MAX_DETAILS_LEN:
        raw = raw[:MAX_DETAILS_LEN] + "…"
    suffix = f"upstream={int(status)}"
    return f"{raw}（{suffix}）" if raw else suffix


def _headers(extra: dict | None = None) -> dict:
    h = {"Content-Type": "application/json", "Accept": "application/json"}
    if TOKEN:
        h["Authorization"] = f"Bearer {TOKEN}"
    if extra:
        h.update({k: v for k, v in extra.items() if v})   # 空值不注入
    return h


def _timeout_for(context, default: float) -> tuple[float, bool]:
    """按客户端 deadline 决定上游超时，返回 (timeout, had_deadline)。"""
    try:
        remain = context.time() if context is not None else None
    except Exception:  # noqa: BLE001
        remain = None
    if remain is None:
        return default, False
    return max(0.001, min(float(remain), default)), True


def _request(method: str, path: str, payload=None, *, timeout: float = 30.0,
             extra_headers: dict | None = None) -> tuple[int, object]:
    """统一出口：把成功与非 2xx 都收敛成 `(status, body)`，**不因 HTTP 状态抛异常**。

    只有连接层失败（URLError / 超时 / 连接被拒）才向调用方抛出，由上层翻译
    `UNAVAILABLE` / `DEADLINE_EXCEEDED`（V18 §5.2）。
    """
    url = BASE_URL + path
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, headers=_headers(extra_headers), method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
            status = getattr(resp, "status", 200) or 200
    except urllib.error.HTTPError as e:
        status = int(e.code)
        try:
            raw = e.read().decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            raw = ""
    try:
        return status, (json.loads(raw) if raw else {})
    except json.JSONDecodeError:
        return status, {"raw": raw}


def _parse_payload(payload_json: str) -> dict:
    """信封 JSON 解析：空串视作 `{}`（沿用既有契约），非空但非法 → 抛 ValueError。"""
    if not payload_json:
        return {}
    try:
        parsed = json.loads(payload_json)
    except json.JSONDecodeError as e:
        raise ValueError(f"payload_json 解析失败: {e}") from None
    if not isinstance(parsed, dict):
        raise ValueError("payload_json 必须是 JSON 对象")
    return parsed


def _resolve_token(token: str | None) -> dict | None:
    """R1：字段非空 → 注入 x-agent-token；为空 → None（不注入，继承平台兼容放行）"""
    tok = (token or "").strip()
    return {AGENT_TOKEN_HEADER: tok} if tok else None


# ── gRPC Servicer（需 grpcio）─────────────────────────────────
if HAS_GRPC:
    # 尝试导入生成的桩；未生成则提示
    try:
        from .proto import agent_community_pb2, agent_community_pb2_grpc

        def _upstream_or_abort(context, method, path, payload=None, *,
                               write: bool = False, extra_headers: dict | None = None):
            """一次上游调用 + 全部错误翻译；非 2xx 直接 `context.abort`（V18 §5.1/§5.2）。"""
            timeout, had_deadline = _timeout_for(context, WRITE_TIMEOUT if write else READ_TIMEOUT)
            try:
                status, body = _request(method, path, payload, timeout=timeout,
                                        extra_headers=extra_headers)
            except (socket.timeout, TimeoutError):
                # 客户端设了 deadline 却超时 → DEADLINE_EXCEEDED；否则视为服务不可达
                code = (grpc.StatusCode.DEADLINE_EXCEEDED if had_deadline
                        else grpc.StatusCode.UNAVAILABLE)
                context.abort(code, "上游调用超时" + ("（客户端 deadline 已用尽）" if had_deadline else ""))
            except urllib.error.URLError as e:
                reason_obj = getattr(e, "reason", e)
                # 连接阶段的超时会被 urllib 包成 URLError(reason=timeout) —— 同样要按
                # "客户端是否设过 deadline" 区分 DEADLINE_EXCEEDED / UNAVAILABLE
                if isinstance(reason_obj, (socket.timeout, TimeoutError)) and had_deadline:
                    context.abort(grpc.StatusCode.DEADLINE_EXCEEDED,
                                  "上游调用超时（客户端 deadline 已用尽）")
                reason = _strip_unsafe(str(reason_obj))
                context.abort(grpc.StatusCode.UNAVAILABLE, f"网关无法连接平台 API：{reason}")
            except OSError as e:
                context.abort(grpc.StatusCode.UNAVAILABLE, f"网关与平台之间网络错误：{_strip_unsafe(str(e))}")
            if int(status) != 200:
                context.abort(_grpc_status_from_upstream(status), _details_from_body(status, body))
            return body if isinstance(body, dict) else {}

        class AgentCommunityServicer(agent_community_pb2_grpc.AgentCommunityServicer):
            """把 gRPC 调用转发到平台 REST API（信封式，零协议漂移）。

            R3：网关**只透传**身份凭证，不在此处做任何 HMAC/白名单校验 ——
            校验唯一来源是平台 `identity.check_request_token`。
            """

            def RegisterHarness(self, request, context):
                try:
                    payload = _parse_payload(request.payload_json)
                except ValueError as e:
                    context.abort(grpc.StatusCode.INVALID_ARGUMENT, str(e))
                resp = _upstream_or_abort(context, "POST", "/api/harness/register", payload, write=True)
                return agent_community_pb2.RegisterReply(
                    payload_json=json.dumps(resp, ensure_ascii=False)
                )

            def SendMessage(self, request, context):
                try:
                    payload = _parse_payload(request.payload_json)
                except ValueError as e:
                    context.abort(grpc.StatusCode.INVALID_ARGUMENT, str(e))
                resp = _upstream_or_abort(
                    context, "POST", "/api/harness/message", payload, write=True,
                    extra_headers=_resolve_token(getattr(request, "agent_token", "")))
                ok = bool(resp.get("success", False))
                return agent_community_pb2.MessageAck(
                    ok=ok, error=str(resp.get("error", "") or ""))

            def SubmitTask(self, request, context):
                try:
                    payload = _parse_payload(request.payload_json)
                except ValueError as e:
                    context.abort(grpc.StatusCode.INVALID_ARGUMENT, str(e))
                # 拍板 ①：SubmitTask 虽然后端当前不校验 P1，也一并转发（一致性）
                resp = _upstream_or_abort(
                    context, "POST", "/api/workshop", payload, write=True,
                    extra_headers=_resolve_token(getattr(request, "agent_token", "")))
                ok = bool(resp.get("success") or resp.get("workshop_id"))
                return agent_community_pb2.TaskAck(
                    ok=ok,
                    task_id=str(resp.get("workshop_id", "")),
                    error=str(resp.get("error", "") or ""),
                )

            def GetTask(self, request, context):
                resp = _upstream_or_abort(context, "GET", f"/api/task/{request.task_id}")
                return agent_community_pb2.TaskStatus(
                    task_id=request.task_id,
                    status=str(resp.get("status", "")),
                    payload_json=json.dumps(resp, ensure_ascii=False),
                )

            def ReportResult(self, request, context):
                try:
                    payload = _parse_payload(request.payload_json)
                except ValueError as e:
                    context.abort(grpc.StatusCode.INVALID_ARGUMENT, str(e))
                resp = _upstream_or_abort(
                    context, "POST", "/api/harness/task-result", payload, write=True,
                    extra_headers=_resolve_token(getattr(request, "agent_token", "")))
                ok = bool(resp.get("success", False))
                return agent_community_pb2.ResultAck(
                    ok=ok, error=str(resp.get("error", "") or ""))

            def ListProtocols(self, request, context):
                resp = _upstream_or_abort(context, "GET", "/api/protocols")
                return agent_community_pb2.ProtocolsReply(
                    payload_json=json.dumps(resp, ensure_ascii=False))

        def build_server(port: int = 9105):
            """构建并**启动** gRPC server（不阻塞），返回 `(server, bound_port)`。

            拆出这一步是为了**可测**：冒烟测试要在同进程里起 server、再用真 gRPC 客户端打它，
            而 `serve()` 内部会 `wait_for_termination()` 永久阻塞。
            `port=0` 时由系统分配空闲端口，`bound_port` 返回实际绑定值（测试用它最省心）。
            """
            server = grpc.server(futures.ThreadPoolExecutor(max_workers=8))
            agent_community_pb2_grpc.add_AgentCommunityServicer_to_server(
                AgentCommunityServicer(), server
            )
            bound = server.add_insecure_port(f"[::]:{port}")
            server.start()
            return server, bound

        def serve(port: int):
            server, bound = build_server(port)
            print(f"[grpc] gRPC gateway listening on :{bound or port} (forwarding to {BASE_URL})")
            server.wait_for_termination()

    except ImportError as e:
        HAS_GRPC = False
        _STUB_ERROR = str(e)

        def build_server(port: int = 9105):  # noqa: ARG001
            raise RuntimeError(f"gRPC 桩/依赖不可用：{e}")

        def serve(port: int):
            print(f"[grpc] 桩代码未生成（{e}）")
            print("[grpc] 请先运行：python -m grpc_tools.protoc ...（见模块文档）")
            sys.exit(1)
else:
    def build_server(port: int = 9105):  # noqa: ARG001
        raise RuntimeError("grpcio 未安装")

    def serve(port: int):
        print("[grpc] 未安装 grpcio —— gRPC 网关不可用。")
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
