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


def test_idempotent_advance(monkeypatch):
    monkeypatch.setenv("AC_WORKFLOW_BATCH", "1")   # V79：默认并行 3，这里只验"一次一步"
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
    first = we.on_fail(ws, ws.task_tree[0])
    assert first["state"] == "retry" and ws.task_tree[0]["retry_count"] == 1, (first, ws.task_tree[0])
    second = we.on_fail(ws, ws.task_tree[0])
    assert second["state"] == "blocked", (second, ws.task_tree[0])
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

# ── V79：并行与失败重路由 ──────────────────────────────────────

def test_parallel_batch_respects_limit(monkeypatch):
    """同层多个可跑节点：一次最多推 AC_WORKFLOW_BATCH 个，且**不跨父子**。"""
    monkeypatch.setenv("AC_WORKFLOW_BATCH", "2")
    ws = _ws("wf-7", [{"node_id": "p1", "label": "甲", "kind": "task", "status": "planned"},
                      {"node_id": "p2", "label": "乙", "kind": "task", "status": "planned"},
                      {"node_id": "p3", "label": "丙", "kind": "task", "status": "planned"},
                      {"node_id": "c1", "label": "子", "kind": "task", "status": "planned",
                       "parent_id": "p1"}])
    batch = we.next_nodes(ws)
    ids = [str(n["node_id"]) for n in batch]
    assert len(ids) == 2, ids
    assert "c1" not in ids, "父节点没跑完时子节点不能进同一批：%r" % ids

    r = C.post("/api/workshops/wf-7/workflow/run").json()
    assert r["advanced"] is True and len(r["advanced_nodes"]) == 2, r


def test_fallback_routing_on_failure():
    """失败两次后有备选节点 ⇒ 重路由到它，而不是整棵树 blocked。"""
    ws = _ws("wf-8", [{"node_id": "f1", "label": "主", "kind": "task", "status": "running",
                       "retry_count": 1, "fallback_to": "f2"},
                      {"node_id": "f2", "label": "备选", "kind": "task", "status": "planned"}])
    out = we.on_fail(ws, ws.task_tree[0])
    assert out["state"] == "routed" and out["routed_to"] == "f2", out
    assert ws.task_tree[0]["status"] == "failed", ws.task_tree[0]
    assert we.next_node(ws)["node_id"] == "f2", "重路由后下一个应能跑备选节点"


def test_no_fallback_blocks():
    ws = _ws("wf-9", [{"node_id": "g1", "label": "孤", "kind": "task", "status": "running",
                       "retry_count": 1}])
    out = we.on_fail(ws, ws.task_tree[0])
    assert out["state"] == "blocked" and out["routed_to"] == "", out
    assert we.status(ws)["state"] == "blocked", we.status(ws)


def test_failed_node_not_rerun():
    """被重路由后留下的 failed 节点不再被派（避免死循环）。"""
    ws = _ws("wf-10", [{"node_id": "h1", "label": "坏", "kind": "task", "status": "failed"},
                       {"node_id": "h2", "label": "备", "kind": "task", "status": "planned"}])
    assert we.next_node(ws)["node_id"] == "h2", we.next_node(ws)


def test_done_endpoint_reports_routed(monkeypatch):
    """端点：节点失败且带备选 ⇒ 返回 routed_to，并把备选放出来。"""
    ws = _ws("wf-11", [{"node_id": "i1", "label": "主", "kind": "task", "status": "running",
                        "retry_count": 1, "fallback_to": "i2"},
                       {"node_id": "i2", "label": "备", "kind": "task", "status": "planned"}])
    srv.workshops["wf-11"] = ws
    r = C.post("/api/tasks/i1/workflow-node/done", json={"ok": False}).json()
    assert r["state"] == "routed" and r["routed_to"] == "i2", r


def test_batch_env_default_is_three(monkeypatch):
    monkeypatch.delenv("AC_WORKFLOW_BATCH", raising=False)
    ws = _ws("wf-12", [{"node_id": "j%d" % i, "label": str(i), "kind": "task", "status": "planned"}
                       for i in range(5)])
    assert len(we.next_nodes(ws)) == 3, "默认并行上限应为 3"
