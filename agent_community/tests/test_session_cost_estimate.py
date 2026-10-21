# -*- coding: utf-8 -*-
"""守卫：**会话级成本预估**（V75 P1-5）。

判据（真算）：
① 工作间 `origin_task_id` 指向的任务带用量 → 求和正确、`available: True`、`basis` 说明依据；
② 任务树里登记了第二个任务 → 也计入（多个来源相加）；
③ **归不到**（人工建的工作间、没任务 id）→ `available: False` + `why` + 指向 `/api/ai/usage`
   —— 宁可说"没有"，也不编一个 0 当结论。
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.append(str(_ROOT))

from agent_community.platform.protocol import Task, TaskStatus  # noqa: E402
from agent_community.platform.routers.sessions import _workshop_cost  # noqa: E402
from agent_community.platform.workshop import Workshop  # noqa: E402


def test_sums_origin_and_tree_tasks(monkeypatch):
    from agent_community.platform import server as srv

    t1 = Task(id="c-1", title="a", description="d", created_by="u", status=TaskStatus.COMPLETED)
    t1.usage = {"cost": 0.01, "total_tokens": 100, "cached_tokens": 40, "rounds": 2}
    t2 = Task(id="c-2", title="b", description="d", created_by="u", status=TaskStatus.COMPLETED)
    t2.usage = {"cost": 0.02, "total_tokens": 200, "cached_tokens": 10, "rounds": 1}
    monkeypatch.setitem(srv.tasks, "c-1", t1)
    monkeypatch.setitem(srv.tasks, "c-2", t2)

    ws = Workshop(workshop_id="w-1", name="w", workspace_dir="", hall_content="",
                  origin_task_id="c-1")
    ws.task_tree = [{"node_id": "n1", "task_id": "c-2"}]
    monkeypatch.setitem(srv.workshops, "w-1", ws)

    c = _workshop_cost("w-1")
    assert c["available"] is True, c
    assert abs(c["cost"] - 0.03) < 1e-9, c
    assert c["total_tokens"] == 300 and c["cached_tokens"] == 50 and c["rounds"] == 3, c
    assert c["tasks_counted"] == 2 and "累计用量" in c["basis"], c


def test_unlinked_session_says_not_available():
    c = _workshop_cost("人工建的工作间")
    assert c["available"] is False, c
    assert "不编" in c["why"] or "没有关联" in c["why"], c["why"]
    assert "/api/ai/usage" in c["how_to_get"], c
