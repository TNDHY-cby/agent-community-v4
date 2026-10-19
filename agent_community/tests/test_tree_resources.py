# -*- coding: utf-8 -*-
"""守卫：**任务树 + 资源库**的三条硬规矩（2026-10-19 端到端判据补齐时立的）。

判据（按真行为）：
  ① **删登记不删文件**：`DELETE /resources/{rid}` 只解除共享，**磁盘上的实体文件必须还在**
     （这是"移除资源"最容易误解成"删用户文件"的地方）；
  ② **资源与节点双向关联**：`attach` 之后，节点上有这条资源、资源上回填 `task_node`；
  ③ **非法状态被拒且状态不变**：节点状态只认 `active/done/bypassed/dropped`。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.append(str(_ROOT))


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("AC_DATA_DIR", str(tmp_path / "data"))
    from fastapi.testclient import TestClient
    from agent_community.platform.server import app
    return TestClient(app, client=("127.0.0.1", 54321))


def _ws(c) -> str:
    r = c.post("/api/workshop", json={"name": "树守卫", "hall_content": "x",
                                      "members": [{"role": "码农", "harness_ids": []}]})
    assert r.status_code == 200, r.text[:200]
    return r.json()["workshop_id"]


def _node(c, ws: str, label: str, **kw) -> str:
    r = c.post("/api/workshop/%s/tree/node" % ws, json={"label": label, **kw})
    assert r.status_code == 200, r.text[:200]
    return r.json()["node"]["node_id"]


def test_removing_resource_keeps_file_on_disk(client, tmp_path):
    ws = _ws(client)
    f = tmp_path / "产物.md"
    f.write_text("真产物", encoding="utf-8")
    r = client.post("/api/workshop/%s/resources" % ws, json={
        "name": "产物", "path": str(f), "kind": "file"})
    rid = r.json()["resource"]["rid"]
    assert client.delete("/api/workshop/%s/resources/%s" % (ws, rid)).status_code == 200
    listed = c_rids = [x["rid"] for x in client.get("/api/workshop/%s/resources" % ws)
                       .json()["resources"]]
    assert rid not in listed, "移除后不该还在资源库：%s" % listed
    assert f.is_file() and f.read_text(encoding="utf-8") == "真产物", "移除登记不许删实体文件"


def _rids(items) -> set:
    """节点上的资源集合：`attach` 返回的是 **rid 字符串列表**，`GET /tree` 返回的是**资源对象列表**。

    ⚠️ 两种形状都存在（同一个字段、两处出口）——第一版守卫只按对象读，
    在 `attach` 的返回上就 `'str' object has no attribute 'get'`（守卫自己写错，不是产品错）。
    """
    out = set()
    for r in items or []:
        if isinstance(r, dict):
            if r.get("rid"):
                out.add(str(r["rid"]))
        elif str(r or "").strip():
            out.add(str(r))
    return out


def test_attach_links_both_sides(client, tmp_path):
    ws = _ws(client)
    nid = _node(client, ws, "写模块")
    f = tmp_path / "产物.md"
    f.write_text("x", encoding="utf-8")
    rid = client.post("/api/workshop/%s/resources" % ws, json={
        "name": "产物", "path": str(f)}).json()["resource"]["rid"]
    body = client.post("/api/workshop/%s/tree/node/%s/attach" % (ws, nid),
                       json={"rid": rid}).json()
    assert rid in _rids(body["node"]["resources"]), \
        "节点上应有这条资源：%s" % body["node"]["resources"]
    assert body["resource"]["task_node"] == nid, "资源上应回填 node_id"


def test_illegal_status_rejected_and_unchanged(client):
    ws = _ws(client)
    nid = _node(client, ws, "节点")
    assert client.post("/api/workshop/%s/tree/node/%s/status" % (ws, nid),
                       json={"status": "done"}).status_code == 200
    bad = client.post("/api/workshop/%s/tree/node/%s/status" % (ws, nid),
                      json={"status": "whatever"})
    assert bad.status_code == 400
    tree = client.get("/api/workshop/%s/tree" % ws).json()["tree"]

    def find(nodes, want):
        for n in nodes or []:
            if n.get("node_id") == want:
                return n
            hit = find(n.get("children") or [], want)
            if hit:
                return hit
        return None

    assert (find(tree, nid) or {}).get("status") == "done", "非法状态不该改动现值"
