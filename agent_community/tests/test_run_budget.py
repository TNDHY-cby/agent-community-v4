# -*- coding: utf-8 -*-
"""守卫：**执行预算与超时闸**（V77 第一半）。

① 没预算 → `ok`（默认不限，零影响）；
② 超过金额上限 → `over_budget` + 人话原因 + 两条出路；
③ 超过轮次上限 → `over_budget`；
④ 超过时限 → `over_time`（用注入的 `now` 算，不靠等）；
⑤ 任务没设时**继承工作间默认**；
⑥ `set_budget`：非法值 400 语义（抛 ValueError）/ 传 0 取消该项限制。
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.append(str(_ROOT))

import pytest  # noqa: E402

from agent_community.platform import run_budget as rb  # noqa: E402
from agent_community.platform.protocol import Task, TaskStatus  # noqa: E402
from agent_community.platform.workshop import Workshop  # noqa: E402


def _t(**kw):
    t = Task(id="b-1", title="预算", description="d", created_by="u", status=TaskStatus.EXECUTING)
    for k, v in kw.items():
        setattr(t, k, v)
    return t


def test_no_budget_is_ok():
    res = rb.check(_t())
    assert res["state"] == "ok" and res["why"] == "", res


def test_over_cost():
    t = _t(budget={"max_cost": 0.01}, usage={"cost": 0.02, "rounds": 1})
    res = rb.check(t)
    assert res["state"] == "over_budget" and "超过上限" in res["why"], res
    assert "调高预算" in res["how_to_fix"], res


def test_over_rounds():
    t = _t(budget={"max_rounds": 2}, usage={"cost": 0.0, "rounds": 3})
    res = rb.check(t)
    assert res["state"] == "over_budget" and "轮" in res["why"], res


def test_over_time_with_injected_now():
    started = (datetime.now() - timedelta(minutes=30)).isoformat(timespec="seconds")
    t = _t(budget={"max_minutes": 10}, created_at=started)
    res = rb.check(t)
    assert res["state"] == "over_time" and "分钟" in res["why"], res
    assert "snapshots" in res["how_to_fix"], "要给一条排查出路：%r" % res["how_to_fix"]


def test_inherit_workshop_default():
    ws = Workshop(workshop_id="w-b", name="w", workspace_dir="", hall_content="")
    ws.budget_default = {"max_cost": 1.0}
    t = _t(usage={"cost": 5.0, "rounds": 1})
    assert rb.check(t)["state"] == "ok", "没有工作间时不该超"
    assert rb.check(t, ws)["state"] == "over_budget", "应继承工作间默认预算"


def test_set_budget_validation_and_clear():
    t = _t()
    assert rb.set_budget(t, {"max_cost": 2})["max_cost"] == 2
    with pytest.raises(ValueError):
        rb.set_budget(t, {"max_cost": "不是数字"})
    with pytest.raises(ValueError):
        rb.set_budget(t, {"max_rounds": -1})
    assert "max_cost" not in rb.set_budget(t, {"max_cost": 0}), "传 0 应当取消该项限制"

def test_start_blocked_by_budget_and_endpoints():
    """启动闸 + 停止/重跑（真跑 HTTP）：超预算 409，停止留痕，重跑在预算内才允许。"""
    from fastapi.testclient import TestClient

    from agent_community.platform import server as srv

    c = TestClient(srv.app, client=("127.0.0.1", 54321))
    task = Task(id="b-9", title="闸", description="d", created_by="u", status=TaskStatus.CREATED)
    task.budget = {"max_cost": 0.01}
    task.usage = {"cost": 0.05, "rounds": 3}
    srv.tasks["b-9"] = task

    r = c.post("/api/task/b-9/start")
    assert r.status_code == 409 and "超过上限" in r.text, r.text

    s = c.post("/api/tasks/b-9/stop", json={"why": "超预算了"})
    assert s.status_code == 200 and s.json()["task_status"] == "failed", s.text
    assert any(x["kind"] == "fail" for x in getattr(task, "snapshots", [])), task.snapshots

    still_over = c.post("/api/tasks/b-9/restart")
    assert still_over.status_code == 409, "预算没调高之前不许重跑：%s" % still_over.text

    c.post("/api/tasks/b-9/budget", json={"max_cost": 1.0})
    ok = c.post("/api/tasks/b-9/restart")
    assert ok.status_code == 200 and ok.json()["task_status"] == "created", ok.text
    assert ok.json()["usage_kept"]["cost"] == 0.05, "重跑要保留历史用量（不抹账）"
