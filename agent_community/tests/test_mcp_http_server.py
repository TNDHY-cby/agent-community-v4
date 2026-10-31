# -*- coding: utf-8 -*-
"""守卫（`V104` §7）：**平台当 MCP 服务端（HTTP）** —— 它真来调我们，才算"给到它"。

判据（全部真跑，不查源码文本）：
  ① `POST /mcp` 的 `initialize` 回 `serverInfo` 并给 `Mcp-Session-Id`；
  ② `tools/list` 回的**就是** `agent_community/mcp_server.py` 那份工具表（一处真相，14 个）；
  ③ `tools/call` 真能调用（`status` 拿到平台真实状态），且**不会把自己锁死**
     （工具内部要回头调本平台 REST —— 必须在线程里跑，2026-10-25 实测过 `TimeoutError`）；
  ④ 每次调用都进留痕：`GET /api/mcp/calls` 能看到**客户端名字**与工具名、成败；
  ⑤ 没人来过时留痕里**没有** `tools/call`（空 = 它一次都没来，这正是老板要的那个判据）。
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.append(str(_ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from agent_community.platform import server as srv  # noqa: E402
from agent_community.platform.routers import mcp_http  # noqa: E402

C = TestClient(srv.app, client=("127.0.0.1", 54321))


def _rpc(method: str, params: dict | None = None, *, sid: str = "", rid: int = 1):
    headers = {"Content-Type": "application/json"}
    if sid:
        headers["Mcp-Session-Id"] = sid
    return C.post("/mcp", json={"jsonrpc": "2.0", "id": rid, "method": method,
                                "params": params or {}}, headers=headers)


def _init(name: str = "guard-client") -> str:
    r = _rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                           "clientInfo": {"name": name, "version": "0"}})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["result"]["serverInfo"]["name"], body
    sid = r.headers.get("Mcp-Session-Id") or r.headers.get("mcp-session-id")
    assert sid, "initialize 必须给 Mcp-Session-Id（否则后续调用认不出是谁）"
    return sid


def test_initialize_and_tools_list_share_one_registry():
    sid = _init("guard-client-A")
    r = _rpc("tools/list", {}, sid=sid, rid=2)
    names = [t["name"] for t in r.json()["result"]["tools"]]
    from agent_community import mcp_server as _srv

    assert names == [t["name"] for t in _srv.TOOLS], "HTTP 版必须与 stdio 版**同一份工具表**"
    assert len(names) == 14, names
    for must in ("status", "submit_task_result"):
        assert must in names


def test_tools_call_really_works_and_does_not_deadlock(monkeypatch):
    """`status` 工具内部要回头调本平台 REST —— 在事件循环里同步跑会把自己锁死（实测过）。

    ⚠️ 2026-10-25 双平台判据抓到：这个用例原来会**真去打 127.0.0.1:18920**，
    于是"本机有服务在跑"时才绿（Windows 侧侥幸）、Linux 侧必红 —— 那是**假绿**。
    现在把工具内部的 HTTP 调用改成走**本进程的 TestClient**：既不自锁、也不依赖任何外部服务，
    两边平台跑出来的结论才一致。
    """
    from agent_community import mcp_server as _srv

    def _inproc_call(method, path, payload=None, timeout=30):
        r = C.request(method, path, json=payload)
        try:
            return r.json()
        except ValueError:                                     # 非 JSON 也如实返回
            return {"raw": r.text[:400]}

    monkeypatch.setattr(_srv, "_call", _inproc_call, raising=True)

    sid = _init("guard-client-B")
    r = _rpc("tools/call", {"name": "status", "arguments": {}}, sid=sid, rid=3)
    assert r.status_code == 200, r.text
    res = r.json()["result"]
    assert res.get("isError") is False, res["content"][0]["text"][:300]
    text = res["content"][0]["text"]
    assert "version" in text or "status" in text, text[:200]


def test_evidence_records_who_came_and_what_they_called():
    sid = _init("guard-client-C")
    _rpc("tools/call", {"name": "list_tasks", "arguments": {}}, sid=sid, rid=4)
    r = C.get("/api/mcp/calls", params={"limit": 50})
    data = r.json()
    kinds = [(c["kind"], c.get("tool")) for c in data["calls"]]
    assert ("tools/call", "list_tasks") in kinds, kinds
    named = [c for c in data["calls"] if c["kind"] == "tools/call" and c.get("client")]
    assert named, "留痕要记下**调用方是谁**（clientInfo），否则等于没记"
    assert any(c.get("client") in ("guard-client-C", "") for c in named)


def test_session_header_is_read_case_insensitively():
    """★2026-10-25 真机踩到：有的客户端发 `Mcp-Session-Id`、有的发小写 ——
    头名大小写对不上时，留痕里"是谁来的"会变成空。这里两种写法都要认。"""
    mcp_http.reset_for_tests()
    r = C.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize",
                             "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                                        "clientInfo": {"name": "casE-client", "version": "0"}}})
    sid = r.headers.get("Mcp-Session-Id") or r.headers.get("mcp-session-id")
    assert sid
    for hdr in ("Mcp-Session-Id", "mcp-session-id"):
        r2 = C.post("/mcp", json={"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                                  "params": {"name": "list_agents", "arguments": {}}},
                    headers={hdr: sid})
        assert r2.status_code == 200
    calls = C.get("/api/mcp/calls").json()["calls"]
    hit = [c for c in calls if c["kind"] == "tools/call"]
    assert hit and all(c.get("client") == "casE-client" for c in hit), \
        "两种头名写法都要认出是同一个人（实际：%s）" % [c.get("client") for c in hit]


def test_clear_evidence_means_it_never_came():
    """留痕里没有 `tools/call` 时，界面就必须说"它一次都没来过"，不能拿别的当证据。"""
    with mcp_http._LOCK:
        mcp_http._CALLS.clear()
        mcp_http._SESSIONS.clear()
    data = C.get("/api/mcp/calls").json()
    assert data["count"] == 0 and data["calls"] == []
    assert "没来过" in data["hint"] or "空" in data["hint"]


if __name__ == "__main__":                                        # pragma: no cover
    import pytest

    raise SystemExit(pytest.main([__file__, "-v"]))
