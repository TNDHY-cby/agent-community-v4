# -*- coding: utf-8 -*-
"""守卫（2026-11-06 老板拍板）：**MCP 只支持到 `2025-06-18`** —— 而且这个口径不许只活在文档里。

老板原话（选的是"不跟进"那项）：MCP `2026-07-28` **不跟进**，口径如实写"支持到 2025-06-18"。

为什么要有守卫：版本上限原先散在四处（stdio 服务端 / 平台客户端 / 协议目录 / 文档），
改一处漏三处很常见；更糟的是"两处都写 2025-06-18 但协商行为不是那样"——那种只有真跑能抓。

判据（全部真结果，不查源码文本）：
  ① 一处真相：`mcp_server.SUPPORTED_PROTOCOL_VERSIONS` 与 `mcp_client` 声明的版本都取自 `spec_versions`；
  ② 协议目录（真函数）的 `mcp` 项带 `spec_version=2025-06-18`，并把 `2026-07-28` 登记为**未跟进**；
  ③ `GET /api/protocols`（真端点）里能看到同一个版本口径；
  ④ stdio 侧真协商：请求 `2026-07-28` ⇒ 回 `2025-06-18`（不假装支持）；
  ⑤ HTTP 侧真协商：`POST /mcp` 的 `initialize` 同样回 `2025-06-18`，响应里不出现 `2026-07-28`；
  ⑥ 支持列表里的版本照原样回（不是"一律回最高"）。
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.append(str(_ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from agent_community import mcp_server as stdio  # noqa: E402
from agent_community import spec_versions as sv  # noqa: E402
from agent_community.platform import mcp_client as mcli  # noqa: E402
from agent_community.platform import protocol_catalog as cat  # noqa: E402
from agent_community.platform import server as srv  # noqa: E402

C = TestClient(srv.app, client=("127.0.0.1", 54321))


def _mcp_entry() -> dict:
    return next(p for p in cat.get_protocol_catalog() if p["id"] == "mcp")


def _http_init(version: str):
    return C.post("/mcp", json={
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": version, "capabilities": {},
                   "clientInfo": {"name": "版本口径守卫", "version": "0"}},
    }, headers={"Content-Type": "application/json"})


def test_one_source_of_truth():
    """① 版本号只有一个出处。"""
    assert stdio.SUPPORTED_PROTOCOL_VERSIONS == sv.MCP_SUPPORTED
    assert stdio.PROTOCOL_VERSION == sv.MCP_SUPPORTED_MAX
    assert mcli.MCP_PROTOCOL_VERSION == sv.MCP_SUPPORTED_MAX
    assert sv.MCP_SUPPORTED_MAX == "2025-06-18"


def test_catalog_declares_max_and_what_is_not_followed():
    """② 协议目录如实写"支持到哪一版 + 哪一版没跟进"。"""
    e = _mcp_entry()
    assert e.get("spec_version") == "2025-06-18", e.get("spec_version")
    assert "2026-07-28" in (e.get("spec_not_followed") or []), e.get("spec_not_followed")
    note = str(e.get("spec_note") or "")
    assert "2026-07-28" in note and "未跟进" in note, note
    assert "2025-06-18" in note, note


def test_protocols_endpoint_carries_same_version():
    """③ 真端点上也是同一口径（前端/外端 agent 读到的是这个）。"""
    r = C.get("/api/protocols")
    assert r.status_code == 200, r.text
    body = r.json()
    mcp = next(p for p in body["protocols"] if p["id"] == "mcp")  # type: ignore[index]
    assert mcp.get("spec_version") == "2025-06-18"
    assert "2026-07-28" in (mcp.get("spec_not_followed") or [])


def test_stdio_negotiates_down_for_newer_version():
    """④ stdio 侧：要求 2026-07-28 ⇒ 协商回 2025-06-18（不假装支持）。"""
    out = stdio._handle({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                         "params": {"protocolVersion": "2026-07-28"}})
    assert out is not None
    ver = out["result"]["protocolVersion"]
    assert ver == "2025-06-18", ver
    assert "2026-07-28" not in str(out), "响应里不许出现未支持的版本号，否则客户端会以为支持了"


def test_stdio_echoes_supported_version():
    """⑥ 支持列表里的旧版照原样回（不是一律回最高）。"""
    out = stdio._handle({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                         "params": {"protocolVersion": "2025-03-26"}})
    assert out is not None
    assert out["result"]["protocolVersion"] == "2025-03-26"


def test_http_negotiates_down_for_newer_version():
    """⑤ HTTP 侧：同一行为（两个入口不许两套口径）。"""
    r = _http_init("2026-07-28")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["result"]["protocolVersion"] == "2025-06-18", body
    assert r.headers.get("Mcp-Session-Id") or r.headers.get("mcp-session-id")
    assert "2026-07-28" not in r.text, "响应里不许出现未支持的版本号"
