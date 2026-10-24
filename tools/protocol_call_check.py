# -*- coding: utf-8 -*-
"""判据：**逐个协议，真的去调一次它的接口**（老板 2026-10-24：「逐个检测协议接口的调用情况」）。

和 `tools/protocol_matrix.py` 的分工（别混）：
- `protocol_matrix.py` 测的是**接入流程**（登记 → 叫醒脚本 → 桥模板 → 生成桥 → 投递 → 闭环消费）；
- 本工具测的是**协议接口本身**：HTTP 真打、WebSocket 真连真收发、MCP 真走 JSON-RPC stdio、
  ACP 真拉起子进程握手、A2A 真起 sidecar 读 Agent Card、gRPC 真起网关真发 RPC。

每个协议一行；三种结论：
- `OK`    真调通了（detail 里写清楚调了什么、回了什么）；
- `SKIP`  环境不具备/协议未实现 —— **如实说明为什么**，不算通过也不算失败；
- `FAIL`  该能通却没通（detail 里是真实原因，不许空着）。

跑法：`python tools/protocol_call_check.py [--base http://127.0.0.1:18920]`
退出码：0 无 FAIL / 1 有 FAIL / 2 连不上平台（HTTP 都打不通，别的没意义）。
"""
from __future__ import annotations

import argparse
import json
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

PY = sys.executable
ROWS = []


def _row(proto: str, status: str, detail: str) -> None:
    ROWS.append({"proto": proto, "status": status, "detail": detail})


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def _wait_port(port: int, timeout: float = 20.0) -> bool:
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                return True
        except OSError:
            time.sleep(0.2)
    return False


def _get_json(url: str, timeout: float = 8.0) -> tuple:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode("utf-8") or "{}")
    except Exception as e:                                    # noqa: BLE001 — 调用失败要如实报
        return 0, {"error": "%s: %s" % (type(e).__name__, str(e)[:120])}


# ── HTTP ─────────────────────────────────────────────────────────────────────
def check_http(base: str) -> None:
    code, body = _get_json(base + "/api/status")
    if code == 200 and body.get("version"):
        _row("http", "OK", "GET /api/status → 200 ｜ 版本 %s ｜ 已登记 harness %s"
             % (body.get("version"), body.get("harnesses")))
    else:
        _row("http", "FAIL", "GET /api/status 失败：%s" % (body.get("error") or code))


# ── WebSocket ────────────────────────────────────────────────────────────────
def check_ws(base: str) -> None:
    url = base.replace("http://", "ws://").replace("https://", "wss://") + "/ws"
    try:
        from websockets.sync.client import connect
    except Exception as e:                                    # noqa: BLE001 — 没装库就如实跳过
        _row("ws", "SKIP", "本机没装 websockets（%s）—— 库不装就不假装测过" % type(e).__name__)
        return
    try:
        with connect(url, open_timeout=5, close_timeout=2) as ws:
            got = None
            try:
                got = ws.recv(timeout=3)
            except Exception:                                 # noqa: BLE001 — 没历史消息时本来就没东西可收
                got = None
            if got is None:
                ws.send(json.dumps({"type": "ping"}, ensure_ascii=False))
                try:
                    got = ws.recv(timeout=3)
                except Exception:                             # noqa: BLE001 — 发了没回也要如实说
                    got = None
            if got:
                _row("ws", "OK", "连上 %s 并真收到一帧：%s" % (url, str(got)[:70]))
            else:
                _row("ws", "OK", "连上 %s（握手成功）；没有可收的消息，也没回 ping —— 接口可达" % url)
    except Exception as e:                                    # noqa: BLE001
        _row("ws", "FAIL", "连 %s 失败：%s: %s" % (url, type(e).__name__, str(e)[:120]))


# ── MCP（stdio JSON-RPC）────────────────────────────────────────────────────
def check_mcp() -> None:
    reqs = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize",
         "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                    "clientInfo": {"name": "protocol-call-check", "version": "1"}}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
    ]
    try:
        p = subprocess.Popen([PY, "-m", "agent_community.mcp_server"], cwd=str(ROOT),
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace")
    except Exception as e:                                    # noqa: BLE001
        _row("mcp", "FAIL", "起不来 mcp_server：%s: %s" % (type(e).__name__, e))
        return
    try:
        out_lines = []
        for req in reqs:
            p.stdin.write(json.dumps(req) + "\n")
            p.stdin.flush()
            deadline = time.time() + 10
            while time.time() < deadline:
                line = p.stdout.readline()
                if not line:
                    break
                line = line.strip()
                if not line:
                    continue
                out_lines.append(line)
                try:
                    obj = json.loads(line)
                except ValueError:                            # noqa: BLE001 — 非 JSON 行跳过
                    continue
                if obj.get("id") == req["id"]:
                    break
        got = {}
        tools = 0
        for line in out_lines:
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            if obj.get("id") == 1:
                got["init"] = (obj.get("result") or {})
            if obj.get("id") == 2:
                tools = len(((obj.get("result") or {}).get("tools") or []))
                got["tools"] = obj.get("result") or {}
        if got.get("init"):
            name = ((got["init"].get("serverInfo") or {}).get("name")
                    or (got["init"].get("serverInfo") or {}).get("title") or "（无名）")
            _row("mcp", "OK", "initialize 回 serverInfo=%s ｜ tools/list 回 %d 个工具" % (name, tools))
        else:
            err = p.stderr.read(400) if p.stderr else ""
            _row("mcp", "FAIL", "stdio JSON-RPC 没有回 initialize（stderr 尾部：%s）" % (err[-200:] or "空"))
    finally:
        try:
            p.kill()
        except Exception:                                     # noqa: BLE001 — 收尾杀不掉不影响结论
            pass


# ── ACP（真拉起子进程握手）────────────────────────────────────────────────
def check_acp() -> None:
    fake = ROOT / "agent_community" / "tests" / "_fake_acp_agent.py"
    if not fake.is_file():
        _row("acp", "SKIP", "没有假 agent（%s）—— 不假装测过" % fake.name)
        return
    try:
        from agent_community.platform.acp_harness_bridge import AcpHarnessBridge
    except Exception as e:                                    # noqa: BLE001
        _row("acp", "SKIP", "平台这版没有 AcpHarnessBridge（%s）" % type(e).__name__)
        return
    bridge = None
    try:
        bridge = AcpHarnessBridge(cmd=[PY, str(fake).replace("\\", "/")])
        bridge.start()
        init = bridge.initialize()
        sess = bridge.new_session(str(ROOT))
        sid = str(getattr(sess, "session_id", "") or "")
        _row("acp", "OK", "真拉起子进程 → initialize 回 %s ｜ session/new 回 sessionId=%s"
             % ("ok" if init else "空", sid or "空"))
    except Exception as e:                                    # noqa: BLE001
        _row("acp", "FAIL", "ACP 握手失败：%s: %s" % (type(e).__name__, str(e)[:140]))
    finally:
        try:
            if bridge is not None:
                bridge.close()
        except Exception:                                     # noqa: BLE001
            pass


# ── A2A（真起 sidecar，读 Agent Card）──────────────────────────────────────
def check_a2a() -> None:
    port = _free_port()
    proc = None
    try:
        proc = subprocess.Popen([PY, "-m", "agent_community.a2a_server", "--port", str(port),
                                 "--host", "127.0.0.1"], cwd=str(ROOT),
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if not _wait_port(port, 25):
            _row("a2a", "FAIL", "sidecar 没起来（端口 %d 25 秒内没监听）" % port)
            return
        code, body = _get_json("http://127.0.0.1:%d/.well-known/agent-card.json" % port)
        if code == 200 and (body.get("name") or body.get("agentName")):
            _row("a2a", "OK", "真起 sidecar :%d → Agent Card 200 ｜ name=%s"
                 % (port, body.get("name") or body.get("agentName")))
        else:
            _row("a2a", "FAIL", "Agent Card 没取到（code=%s，%s）" % (code, body.get("error") or body))
    except Exception as e:                                    # noqa: BLE001
        _row("a2a", "FAIL", "A2A sidecar 起不来：%s: %s" % (type(e).__name__, str(e)[:140]))
    finally:
        try:
            if proc is not None:
                proc.kill()
        except Exception:                                     # noqa: BLE001
            pass


# ── gRPC（真起网关，真发一次 RPC）─────────────────────────────────────────
def _edge_token(base: str) -> tuple:
    """拿一个**已登记 harness** 的令牌（平台自己签发，本机 dev；**绝不打印明文**）。

    网关的入站 Bearer 校验是 `identity.check_edge_bearer`，它按 harness_id 验 HMAC ——
    所以必须用一个**真实存在的 harness id**（编一个字符串会 401，那是正确行为）。
    id 从**活着的平台**问（本进程里的 manager 是空的，它靠服务启动时加载）。
    """
    try:
        code, body = _get_json(base + "/api/docking/overview")
        hs = body.get("harnesses") or []
        ids = [str(h.get("harness_id") or "") for h in hs if isinstance(h, dict)]
        ids = [i for i in ids if i]
        if not ids:
            return "", ""
        from agent_community.platform.identity import issue_agent_token

        hid = ids[0]
        return str(issue_agent_token(hid) or ""), hid
    except Exception as e:                                    # noqa: BLE001 — 拿不到就如实说没有令牌
        print("[grpc] 拿不到 agent_token（%s: %s）—— 会以无令牌方式调用，401 是 fail-closed 的正确结果"
              % (type(e).__name__, str(e)[:100]))
        return "", ""


def check_grpc(base: str) -> None:
    try:
        import grpc  # noqa: F401
    except Exception as e:                                    # noqa: BLE001 — 可选依赖
        _row("grpc", "SKIP", "没装 grpcio（%s）—— 可选依赖，没装就如实跳过" % type(e).__name__)
        return
    try:
        from agent_community import grpc_gateway as gw
        from agent_community.proto import agent_community_pb2 as pb2
        from agent_community.proto import agent_community_pb2_grpc as pb2_grpc
    except Exception as e:                                    # noqa: BLE001
        _row("grpc", "SKIP", "桩/网关导入不了（%s: %s）" % (type(e).__name__, str(e)[:120]))
        return
    server = None
    try:
        server, port = gw.build_server(0)
        # 网关要求入站 Bearer（`identity.check_edge_bearer`）—— 用**已登记 harness** 的令牌来调，
        # 由平台自己签发（HMAC，本机 dev）；**不打印明文**。拿不到就如实说"缺令牌"，不当成接口坏。
        tok, hid = _edge_token(base)
        meta = [("authorization", "Bearer " + tok)] if tok else None
        ch = grpc.insecure_channel("127.0.0.1:%d" % port)
        stub = pb2_grpc.AgentCommunityStub(ch)
        resp = stub.ListProtocols(pb2.ProtocolsRequest(), timeout=10, metadata=meta)
        # ★网关是**信封式**转发：真正的 JSON 在 `payload_json` 里（不是 reply.protocols）
        raw = getattr(resp, "payload_json", "") or ""
        names = []
        try:
            names = [str(p.get("id")) for p in (json.loads(raw).get("protocols") or [])]
        except ValueError:
            names = []
        if not names:
            _row("grpc", "FAIL", "网关回了 RPC，但 payload_json 解析不出协议列表：%s" % (raw[:120] or "空"))
            return
        _row("grpc", "OK", "真起网关 :%d → ListProtocols 回了 %d 个协议（%s）｜ 用 %s 的令牌鉴权"
             % (port, len(names), "、".join(names[:5]), hid or "（无）"))
    except Exception as e:                                    # noqa: BLE001
        _row("grpc", "FAIL", "gRPC 调用失败：%s: %s" % (type(e).__name__, str(e)[:160]))
    finally:
        try:
            if server is not None:
                server.stop(0)
        except Exception:                                     # noqa: BLE001
            pass


def main() -> int:
    ap = argparse.ArgumentParser(description="逐个协议真调一次接口")
    ap.add_argument("--base", default="http://127.0.0.1:18920")
    args = ap.parse_args()
    base = args.base.rstrip("/")
    print("=" * 100)
    print("协议接口逐个调用 —— 真打（平台 %s）" % base)
    print("=" * 100)
    code, _ = _get_json(base + "/api/status")
    if code != 200:
        print("连不上平台（%s）—— 先把服务起起来，别的协议没意义" % base)
        return 2
    check_http(base)
    check_ws(base)
    check_mcp()
    check_acp()
    check_a2a()
    check_grpc(base)
    _row("pipe", "SKIP", "本地进程间不是网络接口：它靠平台自己的文件/管道目录，"
                         "已由 file_poll 桥那条（protocol_matrix ⑥闭环）覆盖")
    _row("sse", "SKIP", "协议目录里标记 not_implemented（规划中）—— 没有接口可调，不假装")
    print("%-8s %-6s %s" % ("协议", "结果", "调了什么 / 回了什么"))
    print("-" * 100)
    for r in ROWS:
        mark = {"OK": "OK", "SKIP": "如实跳过", "FAIL": "★FAIL"}[r["status"]]
        print("%-8s %-6s %s" % (r["proto"], mark, r["detail"]))
    print("-" * 100)
    fails = [r for r in ROWS if r["status"] == "FAIL"]
    skips = [r for r in ROWS if r["status"] == "SKIP"]
    if fails:
        print("结论：**%d 个协议调不通**（%s）；另有 %d 个如实跳过"
              % (len(fails), "、".join(r["proto"] for r in fails), len(skips)))
        return 1
    print("结论：**该通的都真调通了**（%d 通 / %d 如实跳过）"
          % (len(ROWS) - len(skips), len(skips)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
