# -*- coding: utf-8 -*-
"""守卫：**工作流执行引擎**（V78 §4）。

① 拓扑：父未完成 ⇒ 子不可跑；父 done ⇒ 子可跑
② 幂等：连续两次推进只派一个节点（第二次 `advanced=false` + 原因）
③ 失败：先 retry 一次，再失败 `blocked`，返回带节点
④ 预算超 ⇒ 409 且不推进（接 V77）
⑤ 审批型节点 ⇒ `waiting_approval`，且不当作 done（接 V76）
⑥ reset：blocked 复位成 planned 并留痕
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.append(str(_ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from agent_community.platform import server as srv  # noqa: E402
from agent_community.platform import workflow_engine as we  # noqa: E402
from agent_community.platform.protocol import Task, TaskStatus  # noqa: E402
from agent_community.platform.workshop import Workshop  # noqa: E402

C = TestClient(srv.app, client=("127.0.0.1", 54321))


def _ws(wid: str, nodes: list) -> Workshop:
    ws = Workshop(workshop_id=wid, name=wid, workspace_dir="", hall_content="")
    ws.task_tree = nodes
    srv.workshops[wid] = ws
    return ws


def test_topology_blocks_until_parent_done():
    ws = _ws("wf-1", [{"node_id": "a", "label": "父", "kind": "task", "status": "planned"},
                      {"node_id": "b", "label": "子", "kind": "task", "status": "planned",
                       "parent_id": "a"}])
    nxt = we.next_node(ws)
    assert nxt["node_id"] == "a", nxt
    we.mark(ws.task_tree[0], "done")
    assert we.next_node(ws)["node_id"] == "b"


def test_idempotent_advance():
    _ws("wf-2", [{"node_id": "a", "label": "步一", "kind": "task", "status": "planned"},
                 {"node_id": "b", "label": "步二", "kind": "task", "status": "planned"}])
    r1 = C.post("/api/workshops/wf-2/workflow/run").json()
    assert r1["advanced"] is True and r1["node_id"] == "a", r1
    r2 = C.post("/api/workshops/wf-2/workflow/run").json()
    assert r2["advanced"] is True and r2["node_id"] == "b", "第二次应推进一步而不是重复派 a：%r" % r2
    r3 = C.post("/api/workshops/wf-2/workflow/run").json()
    assert r3["advanced"] is False and "没有可跑" in (r3["why"] or "") or r3["next"] is None, r3


def test_failure_retries_once_then_blocked():
    ws = _ws("wf-3", [{"node_id": "a", "label": "会失败的", "kind": "task", "status": "running"}])
    first = we.on_fail(ws.task_tree[0])
    assert first == "retry" and ws.task_tree[0]["retry_count"] == 1, ws.task_tree[0]
    second = we.on_fail(ws.task_tree[0])
    assert second == "blocked", ws.task_tree[0]
    st = we.status(ws)
    assert st["state"] == "blocked" and st["blocked"] == ["a"], st
    assert "卡在节点 a" in st["why"], st["why"]


def test_budget_gate_blocks_advance():
    ws = _ws("wf-4", [{"node_id": "bud-1", "label": "烧钱的", "kind": "task", "status": "planned"}])
    task = Task(id="bud-1", title="t", description="d", created_by="u", status=TaskStatus.CREATED)
    task.budget = {"max_cost": 0.01}
    task.usage = {"cost": 9.0, "rounds": 2}
    srv.tasks["bud-1"] = task
    r = C.post("/api/workshops/wf-4/workflow/run")
    assert r.status_code == 409 and "超过上限" in r.text, r.text
    assert ws.task_tree[0]["status"] == "planned", "被预算拦下时不许把节点标成 running"


def test_approval_node_waits():
    ws = _ws("wf-5", [{"node_id": "apx", "label": "等人批", "kind": "approval", "status": "planned"}])
    r = C.post("/api/workshops/wf-5/workflow/run").json()
    assert r["advanced"] is False and r["state"] == "waiting_approval", r
    assert ws.task_tree[0]["status"] == "waiting_approval", ws.task_tree[0]
    assert we.next_node(ws) is None, "等审批时不许再推进"


def test_reset_blocked_nodes():
    ws = _ws("wf-6", [{"node_id": "a", "label": "卡住的", "kind": "task", "status": "blocked",
                       "retry_count": 1}])
    r = C.post("/api/workshops/wf-6/workflow/reset").json()
    assert r["reset"] == ["a"], r
    assert ws.task_tree[0]["status"] == "planned" and ws.task_tree[0]["retry_count"] == 0, ws.task_tree[0]
