# -*- coding: utf-8 -*-
"""守卫：V84 五条。

① 默认不出现（清单里没有、单卡 404）；② 打开后出现且字段齐；
③ 卡片**不含**敏感串（成员真名 / 绝对路径 / token）；④ 关闭后立刻消失；⑤ 未知工作间 404 说清。
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.append(str(_ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from agent_community.platform import server as srv  # noqa: E402
from agent_community.platform.workshop import Workshop, WorkshopMember  # noqa: E402

C = TestClient(srv.app, client=("127.0.0.1", 54321))


def _ws(wid="dw-1", discoverable=False):
    ws = Workshop(workshop_id=wid, name="演示工作间", workspace_dir="/绝对/路径/不该出现",
                  hall_content="内部任务正文不该曝光")
    m = WorkshopMember(member_id="m1", role="工程师", display_name="张三真名")
    m.entry_state = "entered"
    ws.members = [m]
    ws.discoverable = discoverable
    ws.task_tree = [{"node_id": "n1", "label": "需求", "kind": "task", "status": "done"}]
    srv.workshops[wid] = ws
    return ws


def test_default_hidden():
    _ws("dw-a", discoverable=False)
    rows = C.get("/.well-known/workshops.json").json()["workshops"]
    assert all(r["workshop_id"] != "dw-a" for r in rows), rows
    r = C.get("/.well-known/workshop/dw-a.json")
    assert r.status_code == 404 and "默认关" in r.text, r.text


def test_enabled_appears_with_fields():
    ws = _ws("dw-b", discoverable=True)
    r = C.post("/api/workshops/dw-b/discoverable", json={"on": True})
    assert r.status_code == 200 and r.json()["discoverable"] is True, r.text
    rows = C.get("/.well-known/workshops.json").json()["workshops"]
    row = next(x for x in rows if x["workshop_id"] == "dw-b")
    for k in ("workshop_id", "name", "status", "members", "nodes", "gate", "trust"):
        assert k in row, (k, row)
    assert row["trust"] == "platform-self"
    assert ws.discoverable is True


def test_card_has_no_private_strings():
    _ws("dw-c", discoverable=True)
    C.post("/api/workshops/dw-c/discoverable", json={"on": True})
    raw = C.get("/.well-known/workshop/dw-c.json").text
    for bad in ("张三真名", "/绝对/路径", "内部任务正文", "token", "password"):
        assert bad not in raw, "卡片泄露了 %r：%s" % (bad, raw[:200])


def test_disable_disappears():
    _ws("dw-d", discoverable=True)
    C.post("/api/workshops/dw-d/discoverable", json={"on": True})
    assert C.get("/.well-known/workshop/dw-d.json").status_code == 200
    C.post("/api/workshops/dw-d/discoverable", json={"on": False})
    assert C.get("/.well-known/workshop/dw-d.json").status_code == 404
    rows = C.get("/.well-known/workshops.json").json()["workshops"]
    assert all(r["workshop_id"] != "dw-d" for r in rows), rows


def test_unknown_workshop_404():
    r = C.get("/.well-known/workshop/不存在.json")
    assert r.status_code == 404 and "没有这个工作间" in r.text, r.text
