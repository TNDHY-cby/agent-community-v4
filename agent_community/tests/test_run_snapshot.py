# -*- coding: utf-8 -*-
"""守卫：**运行快照回放 + 流程导入导出**（V76 §4 后半五格）。

① 回执/审批各写一条快照，`kind` 正确、顺序正序；
② 只追加：同一任务再写一条 → 条数 +1，旧记录还在（不覆盖）；
③ 超上限 → 丢最旧并**留一条 truncated 记录**（不静默）；
④ 导出 workflow 的 nodes/edges 与 task_tree 一致；
⑤ 导入：新增成功 / 已存在的跳过 / 未知字段与未知 kind → 400 并列出支持值；单条快照 404 说清。
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.append(str(_ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from agent_community.platform import run_snapshot as rs  # noqa: E402
from agent_community.platform import server as srv  # noqa: E402
from agent_community.platform.protocol import Task, TaskStatus  # noqa: E402
from agent_community.platform.workshop import Workshop  # noqa: E402

C = TestClient(srv.app, client=("127.0.0.1", 54321))


def _task(tid="sn-1"):
    t = Task(id=tid, title="快照", description="d", created_by="u", status=TaskStatus.EXECUTING)
    srv.tasks[tid] = t
    return t


def test_append_is_append_only_and_ordered():
    t = _task("sn-1")
    a = rs.append(t, "dispatch", summary="派活")
    b = rs.append(t, "reply", summary="回执")
    rows = rs.list_of(t, 10)
    assert [r["snap_id"] for r in rows] == [a["snap_id"], b["snap_id"]]
    assert [r["kind"] for r in rows] == ["dispatch", "reply"]
    assert len(t.snapshots) == 2, "只追加，不许覆盖"


def test_cap_truncates_with_marker(monkeypatch):
    monkeypatch.setenv("AC_SNAPSHOT_MAX", "10")
    t = _task("sn-2")
    for i in range(15):
        rs.append(t, "reply", summary="第 %d 条" % i)
    kinds = [r["kind"] for r in t.snapshots]
    assert kinds[0] == "truncated", "超上限要留截断记录：%r" % kinds[:3]
    assert t.snapshots[0]["detail"]["dropped"] == 5, t.snapshots[0]
    # 上限只数真实记录（10 条）+ 1 条截断标记
    assert len(t.snapshots) == 11, [r["kind"] for r in t.snapshots[:3]]
    assert len([r for r in t.snapshots if r["kind"] != "truncated"]) == 10


def test_export_matches_task_tree():
    ws = Workshop(workshop_id="w-sn", name="间", workspace_dir="", hall_content="")
    ws.task_tree = [{"node_id": "n1", "label": "根", "kind": "task", "parent_id": ""},
                    {"node_id": "n2", "label": "子", "kind": "subtask", "parent_id": "n1"}]
    srv.workshops["w-sn"] = ws
    got = C.get("/api/workshops/w-sn/workflow").json()
    assert [n["node_id"] for n in got["nodes"]] == ["n1", "n2"], got
    assert got["edges"] == [{"from": "n1", "to": "n2", "kind": "parent"}], got


def test_import_only_adds_and_validates():
    ws = Workshop(workshop_id="w-imp", name="间", workspace_dir="", hall_content="")
    ws.task_tree = [{"node_id": "n1", "label": "根", "kind": "task", "parent_id": ""}]
    srv.workshops["w-imp"] = ws

    ok = C.post("/api/workshops/w-imp/workflow",
                json={"nodes": [{"node_id": "n1", "label": "改名", "kind": "task"},
                                {"node_id": "n2", "label": "新", "kind": "review"}]})
    assert ok.status_code == 200, ok.text
    assert ok.json()["added"] == ["n2"] and ok.json()["skipped_existing"] == ["n1"], ok.json()
    assert [n["node_id"] for n in ws.task_tree] == ["n1", "n2"]
    assert ws.task_tree[0]["label"] == "根", "已存在的节点不许被改（防误毁）"

    bad_field = C.post("/api/workshops/w-imp/workflow", json={"nodes": [{"node_id": "n3", "未知": 1}]})
    assert bad_field.status_code == 400 and "不认识的字段" in bad_field.text, bad_field.text
    bad_kind = C.post("/api/workshops/w-imp/workflow", json={"nodes": [{"node_id": "n4", "kind": "画布"}]})
    assert bad_kind.status_code == 400 and "supported" in bad_kind.text, bad_kind.text


def test_snapshot_endpoints_read_only():
    t = _task("sn-3")
    rec = rs.append(t, "approval", summary="审批：approve", detail={"decision": "approve"})
    lst = C.get("/api/tasks/sn-3/snapshots", params={"limit": 1}).json()
    assert lst["count"] == 1 and lst["total"] == 1 and lst["cap"] >= 10, lst
    one = C.get("/api/tasks/sn-3/snapshot", params={"snap_id": rec["snap_id"]})
    assert one.status_code == 200 and one.json()["kind"] == "approval", one.text
    missing = C.get("/api/tasks/sn-3/snapshot", params={"snap_id": "sn-nope"})
    assert missing.status_code == 404 and "没有这条快照" in missing.text, missing.text
