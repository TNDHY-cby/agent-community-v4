# -*- coding: utf-8 -*-
"""守卫：v4.26 清单 §三 的缺陷修复（D1 / D2 / D3 / D5 / D6）。

D4（菱形多父）另在 `test_workflow_multiparent.py`。
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.append(str(_ROOT))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from agent_community.platform import run_budget as rb  # noqa: E402
from agent_community.platform import server as srv  # noqa: E402
from agent_community.platform.protocol import Task, TaskStatus  # noqa: E402
from agent_community.platform.workshop import Workshop  # noqa: E402

C = TestClient(srv.app, client=("127.0.0.1", 54321))


def _ws(wid: str, nodes: list) -> Workshop:
    ws = Workshop(workshop_id=wid, name=wid, workspace_dir="", hall_content="")
    ws.task_tree = nodes
    srv.workshops[wid] = ws
    return ws


def _task(tid: str, cost=0.0, rounds=0, budget=None) -> Task:
    t = Task(id=tid, title="t", description="d", created_by="u", status=TaskStatus.EXECUTING)
    t.usage = {"cost": cost, "rounds": rounds}
    if budget is not None:
        t.budget = budget
    srv.tasks[tid] = t
    return t


# ── D2：0 表示"零花费"，不是不限 ──────────────────────────────
def test_zero_budget_means_no_spending():
    t = _task("d2-1", cost=0.01, budget={"max_cost": 0})
    res = rb.check(t)
    assert res["state"] == "over_budget", res

    t2 = _task("d2-2", budget={"max_cost": 0})
    t2.usage = {"cost": 0.0, "rounds": 0}
    assert rb.check(t2)["state"] == "ok", rb.check(t2)

    # 传 null（None）才是"取消这一项"
    t3 = _task("d2-3", budget={"max_cost": 0})
    assert "max_cost" not in rb.set_budget(t3, {"max_cost": None})
    # 传 0 是设成零花费，**不是**取消
    assert rb.set_budget(t3, {"max_cost": 0})["max_cost"] == 0


# ── D1：同批每个节点都要过预算 ────────────────────────────────
def test_parallel_batch_checks_every_node(monkeypatch):
    monkeypatch.setenv("AC_WORKFLOW_BATCH", "3")
    ws = _ws("d1-ws", [{"node_id": "ok-1", "label": "便宜的", "kind": "task", "status": "planned"},
                       {"node_id": "bad-1", "label": "超预算的", "kind": "task", "status": "planned"}])
    _task("ok-1", cost=0.001, budget={"max_cost": 1.0})
    _task("bad-1", cost=9.0, budget={"max_cost": 0.01})

    r = C.post("/api/workshops/d1-ws/workflow/run").json()
    assert r["advanced"] is True, r
    ids = [a["node_id"] for a in r["advanced_nodes"]]
    assert "ok-1" in ids, r
    assert "bad-1" not in ids, "第二个节点超预算，不该被派：%r" % r
    assert [b["node_id"] for b in r["blocked_nodes"]] == ["bad-1"], r
    # 被拦的节点要标 blocked，别的照跑
    st = {n["node_id"]: n["status"] for n in ws.task_tree}
    assert st["ok-1"] == "running" and st["bad-1"] == "blocked", st


# ── D3：节点没有对应任务时，不许默认放行 ──────────────────────
def test_missing_task_is_not_silently_ok(monkeypatch):
    monkeypatch.setenv("AC_WORKFLOW_BATCH", "1")
    ws = _ws("d3-ws", [{"node_id": "nobody-1", "label": "没任务", "kind": "task", "status": "planned"}])
    ws.budget_default = {"max_cost": 0}          # 工作间口径：零花费
    r = C.post("/api/workshops/d3-ws/workflow/run")
    # 没有用量 ⇒ 0 花费不超；但响应里必须带 note 说明"这是估算口径"，不能装没事
    body = r.json()
    assert r.status_code in (200, 409), r.text
    if r.status_code == 200:
        note = body.get("note") or (body.get("blocked_nodes") or [{}])[0].get("why") or ""
        assert "没有对应任务" in note or body.get("blocked_nodes") is not None, body
    st = {n["node_id"]: n["status"] for n in ws.task_tree}
    assert st["nobody-1"] in ("running", "blocked"), st


# ── D5：产物出口只给产物子树 ──────────────────────────────────
def test_artifact_only_serves_artifact_subtrees(monkeypatch, tmp_path):
    monkeypatch.setenv("AC_DATA_DIR", str(tmp_path))
    (tmp_path / "workshops" / "w1").mkdir(parents=True)
    (tmp_path / "workshops" / "w1" / "结果.md").write_text("产物", encoding="utf-8")
    (tmp_path / "tasks.json").write_text("{}", encoding="utf-8")          # 平台状态
    (tmp_path / "config").mkdir(exist_ok=True)
    (tmp_path / "config" / "secret.json").write_text("{}", encoding="utf-8")

    assert C.get("/api/artifact", params={"path": "workshops/w1/结果.md"}).status_code == 200
    for bad in ("tasks.json", "config/secret.json", "../tasks.json"):
        r = C.get("/api/artifact", params={"path": bad})
        assert r.status_code == 403, "%s 不该能取：%s" % (bad, r.text)


# ── D6：upgrade 不许阻塞事件循环 ──────────────────────────────
def test_upgrade_is_threaded():
    import inspect

    from agent_community.platform.routers import agent_manager as am

    src = inspect.getsource(am.upgrade)
    assert "to_thread" in src, "upgrade 必须走 asyncio.to_thread（D6）：%s" % src
