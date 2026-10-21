# -*- coding: utf-8 -*-
"""守卫：**人工审批闸门**（V76 §4 前半六格，真跑 HTTP + 真跑状态）。

① 默认关：没设策略 ⇒ 不进 waiting_approval（旧行为不变）
② 开了闸门：回执 ok 后进 waiting_approval，GET 给出"待批什么"
③ approve ⇒ 回到 resume_to 记录的状态
④ reject 无 note ⇒ 400；带 note ⇒ 回 delegating
⑤ 非待批任务上调用 ⇒ 400 + 当前状态原话
⑥ cancel ⇒ failed（终止）+ 审计有记录
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.append(str(_ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from agent_community.platform import approval as ap  # noqa: E402
from agent_community.platform import server as srv  # noqa: E402
from agent_community.platform.harness_adapter import harness_manager  # noqa: E402
from agent_community.platform.protocol import HarnessInfo, Task, TaskStatus  # noqa: E402
from agent_community.platform.workshop import Workshop  # noqa: E402

C = TestClient(srv.app, client=("127.0.0.1", 54321))


def _mk(task_id: str, wid: str, required: bool, status=TaskStatus.DELEGATING):
    task = Task(id=task_id, title="审批", description="d", created_by="user", status=status)
    srv.tasks[task_id] = task
    ws = Workshop(workshop_id=wid, name=wid, workspace_dir="", hall_content="",
                  origin_task_id=task_id)
    ws.approval_policy = {"approval_required": required}
    srv.workshops[wid] = ws
    info = HarnessInfo(harness_id="h-" + task_id, harness_name="h", harness_type="custom",
                       ai={"model_name": "m", "provider": "p"})

    class _S:
        def __init__(self, i):
            self.info = i
            self.metadata = {}

    harness_manager.sessions["h-" + task_id] = _S(info)
    return task


def _reply(task_id: str, ok=True):
    from agent_community.tests.conftest import resolve_handler

    fn = resolve_handler("harness_task_result")

    class _R:
        headers = {}

        async def json(self):
            return {"harness_id": "h-" + task_id, "task_id": task_id, "ok": ok, "result": "done"}

    return asyncio.run(fn(_R()))


def test_default_off_does_not_gate():
    task = _mk("ap-1", "w-ap-1", required=False)
    _reply("ap-1")
    assert str(task.status.value) != "waiting_approval", "默认关时不许改变旧行为"
    assert not (task.approval or {}).get("waiting")


def test_gate_opens_and_reports_what_is_pending():
    task = _mk("ap-2", "w-ap-2", required=True)
    _reply("ap-2")
    assert str(task.status.value) == "waiting_approval", task.status
    got = C.get("/api/tasks/ap-2/approval").json()
    assert got["state"] == "waiting" and got["reason"], got
    assert got["resume_to"], "要记住批完回到哪个状态：%r" % got


def test_approve_resumes():
    task = _mk("ap-3", "w-ap-3", required=True)
    _reply("ap-3")
    resume = task.approval["resume_to"]
    r = C.post("/api/tasks/ap-3/approval", json={"decision": "approve", "note": "可以"})
    assert r.status_code == 200, r.text
    assert str(task.status.value) == resume, (task.status, resume)


def test_reject_requires_note():
    task = _mk("ap-4", "w-ap-4", required=True)
    _reply("ap-4")
    r = C.post("/api/tasks/ap-4/approval", json={"decision": "reject", "note": ""})
    assert r.status_code == 400 and "理由" in r.text, r.text
    ok = C.post("/api/tasks/ap-4/approval", json={"decision": "reject", "note": "结果不对，重做"})
    assert ok.status_code == 200, ok.text
    assert str(task.status.value) == "delegating", task.status


def test_non_waiting_task_refused_with_status():
    task = _mk("ap-5", "w-ap-5", required=False, status=TaskStatus.EXECUTING)
    r = C.post("/api/tasks/ap-5/approval", json={"decision": "approve"})
    assert r.status_code == 400, r.text
    assert "executing" in r.text, "要说清它现在是什么状态：%r" % r.text


def test_cancel_terminates_and_audits():
    task = _mk("ap-6", "w-ap-6", required=True)
    _reply("ap-6")
    r = C.post("/api/tasks/ap-6/approval", json={"decision": "cancel", "note": "不做了"})
    assert r.status_code == 200, r.text
    assert str(task.status.value) == "failed", task.status
    assert (task.approval or {}).get("decision") == "cancel", task.approval
