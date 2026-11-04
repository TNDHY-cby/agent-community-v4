# -*- coding: utf-8 -*-
"""守卫（`V127`）：**平台当 MCP 服务端 —— 只读资源面**。

判据（都跑真函数/真路由，不查源码文本）：
  ① `initialize` 声明 `resources` 且 `subscribe=false`（不支持就不许声明 true）；
  ② `resources/list` / `resources/templates/list` 回的**就是** `mcp_server.py` 那两份表（一处真相）；
  ③ `resources/read` 读得到真数据，且**内部字段不外泄**（`_list_key` / `_offset` 不许出现在正文里）；
  ④ **脱敏真生效**：平台 `/api/status` 里有 `pipe_dir`（真实绝对路径）和可能存在的令牌，
     资源正文里必须一个都没有；
  ⑤ 平台说"没有"（404/409）时资源面**如实报 -32002**，不许当成"内容"发回去（假成功）；
  ⑥ 清单超限时**先翻页**：给 `next`，且两页不重叠（`AC_MCP_RESOURCE_MAX_BYTES` 决定上限）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.append(str(_ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from agent_community import mcp_server as ms  # noqa: E402
from agent_community.platform import server as srv  # noqa: E402

C = TestClient(srv.app, client=("127.0.0.1", 54322))


def _rpc(method: str, params: dict | None = None, *, sid: str = "", rid: int = 1):
    headers = {"Content-Type": "application/json"}
    if sid:
        headers["Mcp-Session-Id"] = sid
    return C.post("/mcp", json={"jsonrpc": "2.0", "id": rid, "method": method,
                                "params": params or {}}, headers=headers)


def _read(uri: str) -> tuple[int, dict]:
    r = _rpc("resources/read", {"uri": uri}, rid=9)
    body = r.json()
    if body.get("error"):
        return int(body["error"].get("code")), body["error"]
    return 0, (body["result"]["contents"] or [{}])[0]


def _inproc(method, path, payload=None, timeout=30):
    """让 mcp_server 的平台调用**走本进程**：既不自锁，也不依赖 18920 有没有在跑。"""
    r = C.request(method, path, json=payload)
    try:
        return r.json()
    except ValueError:
        return {"raw": r.text[:400]}


#: 稳定的假平台（**刻意不用真端点**）：全量回归时前面的用例会改鉴权/只读档/数据目录，
#: 真端点在这里会随顺序变绿变红（实测：单跑绿、全量跑 `code=-32002`）。
#: 「真端点也能读」这件事由 `tools/mcp_resources_check.py` 在**独立临时实例**上判（12 格），
#: 单测只负责资源面自己的逻辑：投影、脱敏、序列化、内部字段不外泄。
_FAKE_PLATFORM = {
    "/api/status": {"status": "running", "version": "9.9.9", "agents_registered": 0,
                    "agents_online": 0, "tasks_total": 0, "tasks_active": 0, "harnesses": 0,
                    "pipe_dir": "D:\\some\\where\\pipes",
                    "data_health": {"empty": False, "has_history": True, "detail": "/home/someone/x/y"}},
    "/api/harness/list": {"harnesses": [{"harness_id": "h1", "name": "示例Harness-A",
                                         "status": "online", "kind": "mcp",
                                         "metadata": {"agent_token": "a" * 64,
                                                      "api_base_url": "http://127.0.0.1:9/mcp"}}]},
    "/api/agents": {"agents": [{"agent_id": "a1", "name": "员工01"}]},
    "/api/workshops": {"workshops": [{"workshop_id": "ws-1", "name": "演示间",
                                      "status": "running", "members": [{"member_id": "m0"}]}]},
    "/api/tasks": {"tasks": [{"task_id": "T-1", "title": "演示任务", "status": "running"}]},
    "/api/protocols": {"protocols": [{"id": "mcp", "name": "MCP", "layer": 3,
                                      "semantics": "工具面", "status": "available"}]},
}


def _fake_platform(method, path, payload=None, timeout=30):
    if path in _FAKE_PLATFORM:
        return _FAKE_PLATFORM[path]
    return {"error": "没有这个端点：%s" % path, "http_status": 404}


def test_initialize_declares_readonly_resources():
    r = _rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                            "clientInfo": {"name": "res-guard", "version": "0"}})
    caps = r.json()["result"]["capabilities"]
    assert "resources" in caps, caps
    assert caps["resources"]["subscribe"] is False, "我们没有事件总线，不许假声明 subscribe=true"
    assert caps["resources"]["listChanged"] is False


def test_lists_come_from_the_one_registry(monkeypatch):
    monkeypatch.setattr(ms, "_call", _inproc, raising=True)
    # 一处真相：HTTP 入口回的必须与 mcp_server 的表一致（加/删资源不用改这里）
    got = _rpc("resources/list", {}, rid=2).json()["result"]["resources"]
    assert [x["uri"] for x in got] == [x["uri"] for x in ms.RESOURCES]
    tpl = _rpc("resources/templates/list", {}, rid=3).json()["result"]["resourceTemplates"]
    assert [x["uriTemplate"] for x in tpl] == [x["uriTemplate"] for x in ms.RESOURCE_TEMPLATES]
    for x in got + tpl:
        assert x.get("name") and x.get("mimeType") == "application/json"


def test_read_returns_json_and_hides_internal_fields(monkeypatch):
    monkeypatch.setattr(ms, "_call", _fake_platform, raising=True)
    for uri in ("ac://status", "ac://harnesses", "ac://agents",
                "ac://workshops", "ac://tasks", "ac://protocols"):
        code, got = _read(uri)
        assert code == 0, "%s → %s" % (uri, got)
        text = got["text"]
        json.loads(text)                                     # 必须是合法 JSON
        assert "_list_key" not in text and '"_offset"' not in text, \
            "%s 把内部字段漏出去了：%s" % (uri, text[:200])


def test_secrets_and_local_paths_never_leak(monkeypatch):
    """`/api/status` 真的带 `pipe_dir`（本机绝对路径）——这是脱敏必须兜住的真实字段。"""
    monkeypatch.setattr(ms, "_call", _fake_platform, raising=True)
    code, got = _read("ac://status")
    assert code == 0, got
    text = got["text"]
    assert "D:\\some\\where" not in text, text
    assert "/home/someone" not in text, text
    assert "pipe_dir" not in text, "敏感键名要先白名单投影掉：%s" % text[:200]
    code, got = _read("ac://harnesses")
    assert code == 0, got
    assert "a" * 64 not in got["text"], "令牌明文不许进资源"


def test_platform_404_becomes_resource_not_found(monkeypatch):
    def _fake(method, path, payload=None, timeout=30):
        return {"error": "工作间不存在", "http_status": 404}

    monkeypatch.setattr(ms, "_call", _fake, raising=True)
    code, err = _read("ac://workshop/no-such-one")
    assert code == -32002, err
    assert "no-such-one" in err.get("message", "")


def test_bad_uri_is_invalid_params():
    code, err = _read("http://example.com/x")
    assert code == -32602, err


def test_big_list_pages_instead_of_truncating(monkeypatch):
    """上限压到 1 KB ⇒ 清单必须翻页（给 next），两页不重叠。"""
    monkeypatch.setattr(ms, "_call", _inproc, raising=True)
    monkeypatch.setenv("AC_MCP_RESOURCE_MAX_BYTES", "1024")

    def _fake_tasks(cursor: str = ""):
        return ms._page({"tasks": [{"task_id": "T-%03d" % i, "title": "巡检任务%03d" % i}
                                   for i in range(40)], "total": 40})

    monkeypatch.setattr(ms, "_res_tasks", _fake_tasks, raising=True)
    code, page1 = _read("ac://tasks")
    assert code == 0, page1
    body1 = json.loads(page1["text"])
    assert body1.get("next"), "超过上限必须给 next，而不是悄悄截断：%s" % page1["text"][:200]
    code, page2 = _read(body1["next"])
    assert code == 0, page2
    body2 = json.loads(page2["text"])
    ids1 = {t["task_id"] for t in body1.get("tasks") or []}
    ids2 = {t["task_id"] for t in body2.get("tasks") or []}
    assert ids1 and ids2, (ids1, ids2)
    assert not (ids1 & ids2), "翻页出现重复：%s" % (ids1 & ids2)
    assert body1.get("total") == body2.get("total") == 40


if __name__ == "__main__":                                        # pragma: no cover
    import pytest

    raise SystemExit(pytest.main([__file__, "-v"]))
