# -*- coding: utf-8 -*-
"""守卫：**每轮用量上屏的后端供数**（V75 P1-1）。

判据（真跑）：
① `extract_usage` 认多种别名、没有就 `{}`（**不编数**）、能补 `total_tokens`；
② 真发一次 `/api/harness/task-result`（带 usage）→ 该任务的最后一条消息 `payload["usage"]` 数值对得上；
③ 回报里**不给** usage ⇒ 消息里必须是 `{}`（不许凭空造）；
④ `GET /api/ai/usage` 200 且结构齐（拿不到就 `available:false` + 原因）。
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.append(str(_ROOT))

from agent_community.platform.ai_usage import extract_usage  # noqa: E402


def test_extract_usage_aliases_and_empty():
    assert extract_usage({}) == {}
    assert extract_usage({"usage": {}}) == {}
    assert extract_usage({"result": "ok"}) == {}

    u1 = extract_usage({"usage": {"input_tokens": 100, "output_tokens": 20}})
    assert u1["prompt_tokens"] == 100 and u1["completion_tokens"] == 20
    assert u1["total_tokens"] == 120, "没给 total 时要能算出来：%r" % u1

    u2 = extract_usage({"promptTokenCount": 5, "candidatesTokenCount": 7, "cachedTokenCount": 3,
                        "model": "示例模型"})
    assert u2["total_tokens"] == 12 and u2["cached_tokens"] == 3 and u2["model"] == "示例模型"

    u3 = extract_usage({"usage": {"prompt_tokens": "一坨不是数字"}})
    assert "prompt_tokens" not in u3, "非数字应当丢掉而不是留着：%r" % u3


def test_task_result_carries_usage_into_message(monkeypatch):
    import asyncio

    from fastapi import APIRouter

    from agent_community.platform import server as srv
    from agent_community.platform.harness_adapter import harness_manager
    from agent_community.platform.protocol import HarnessInfo, Task, TaskStatus

    # 一条真任务 + 一个已登记 harness
    task = Task(id="u-1", title="用量守卫", description="d", created_by="user",
                status=TaskStatus.DELEGATING)
    monkeypatch.setitem(srv.tasks, "u-1", task)
    info = HarnessInfo(harness_id="u-h", harness_name="u-h", harness_type="custom",
                       ai={"model_name": "m", "provider": "p"})

    class _Sess:
        def __init__(self, i):
            self.info = i
            self.metadata = {}

    monkeypatch.setitem(harness_manager.sessions, "u-h", _Sess(info))

    from agent_community.tests.conftest import resolve_handler

    fn = resolve_handler("harness_task_result")

    class _Req:
        headers = {}

        async def json(self):
            return {"harness_id": "u-h", "task_id": "u-1", "ok": True, "result": "做完了",
                    "usage": {"prompt_tokens": 300, "completion_tokens": 50, "cached_tokens": 200,
                              "cost": 0.003, "elapsed_ms": 1200}}

    out = asyncio.run(fn(_Req()))
    assert not hasattr(out, "status_code") or getattr(out, "status_code", 200) < 400, out

    u = dict(getattr(task, "usage", None) or {})
    assert u, "回报带用量，任务上却没记下来：%r" % (u,)
    assert u.get("prompt_tokens") == 300 and u.get("total_tokens") == 350, u
    assert u.get("cached_tokens") == 200 and u.get("cost") == 0.003, u
    assert u.get("rounds") == 1 and u.get("elapsed_ms") == 1200, u


def test_task_result_without_usage_stays_empty(monkeypatch):
    import asyncio

    from agent_community.platform import server as srv
    from agent_community.platform.harness_adapter import harness_manager
    from agent_community.platform.protocol import HarnessInfo, Task, TaskStatus

    task = Task(id="u-2", title="无用量", description="d", created_by="user",
                status=TaskStatus.DELEGATING)
    monkeypatch.setitem(srv.tasks, "u-2", task)
    info = HarnessInfo(harness_id="u-h2", harness_name="u-h2", harness_type="custom",
                       ai={"model_name": "m", "provider": "p"})

    class _Sess:
        def __init__(self, i):
            self.info = i
            self.metadata = {}

    monkeypatch.setitem(harness_manager.sessions, "u-h2", _Sess(info))

    from agent_community.tests.conftest import resolve_handler

    fn = resolve_handler("harness_task_result")

    class _Req:
        headers = {}

        async def json(self):
            return {"harness_id": "u-h2", "task_id": "u-2", "ok": True, "result": "做完了"}

    asyncio.run(fn(_Req()))
    assert not (getattr(task, "usage", None) or {}), \
        "没给 usage 时任务上不许编数：%r" % (getattr(task, "usage", None),)


def test_ai_usage_endpoint_shape():
    from fastapi.testclient import TestClient

    from agent_community.platform.server import app

    c = TestClient(app, client=("127.0.0.1", 54321))
    r = c.get("/api/ai/usage")
    assert r.status_code == 200, r.text
    body = r.json()
    assert "available" in body, body
    if body["available"]:
        for k in ("today", "month", "price_per_1k"):
            assert k in body, body
        assert "cost" in body["today"] and "budget" in body["today"], body
    else:
        assert body.get("why") and body.get("how_to_fix"), "拿不到也要说清为什么/怎么办：%r" % body

def test_usage_accumulates_over_rounds(monkeypatch):
    """多轮要**累加**（前端显示"这个任务一共花了多少"），不是每轮覆盖。"""
    import asyncio

    from agent_community.platform import server as srv
    from agent_community.platform.harness_adapter import harness_manager
    from agent_community.platform.protocol import HarnessInfo, Task, TaskStatus
    from agent_community.tests.conftest import resolve_handler

    task = Task(id="u-3", title="两轮", description="d", created_by="user",
                status=TaskStatus.DELEGATING)
    monkeypatch.setitem(srv.tasks, "u-3", task)
    info = HarnessInfo(harness_id="u-h3", harness_name="u-h3", harness_type="custom",
                       ai={"model_name": "m", "provider": "p"})

    class _Sess:
        def __init__(self, i):
            self.info = i
            self.metadata = {}

    monkeypatch.setitem(harness_manager.sessions, "u-h3", _Sess(info))
    fn = resolve_handler("harness_task_result")

    def _req(usage):
        class _R:
            headers = {}

            async def json(self):
                return {"harness_id": "u-h3", "task_id": "u-3", "ok": True,
                        "result": "ok", "usage": usage}
        return _R()

    asyncio.run(fn(_req({"prompt_tokens": 10, "completion_tokens": 5, "cost": 0.001})))
    asyncio.run(fn(_req({"prompt_tokens": 20, "completion_tokens": 5, "cost": 0.002})))
    u = dict(getattr(task, "usage", None) or {})
    assert u.get("rounds") == 2, u
    assert u.get("prompt_tokens") == 30 and u.get("total_tokens") == 40, u
    assert abs(float(u.get("cost", 0)) - 0.003) < 1e-9, u
