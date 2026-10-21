# -*- coding: utf-8 -*-
"""守卫：`GET /api/events`（旧前端兼容）**必须扛得住真实的 Message 对象**。

## 这个 500 的来历（2026-10-21 真请求抓到的，**搬家前就有**）

`list_events` 原来写的是 `msg.created_at or t.created_at`，而 `Message` 模型**没有**
`created_at` 字段（它叫 `timestamp`）⇒ 只要 tasks 里有消息，整个端点就 500。
之前一直没炸，是因为列表空、循环没进去（"看起来能用"的典型）。

同一条链路还有第二个隐患：时间**可能是 datetime 也可能是 str**（新版用 datetime、
兜底用任务字符串）⇒ 直接 `sort()` 会在两者混排时抛 TypeError。

本守卫按真对象跑：造一个带消息的任务 → 调真 handler → 要求返回体里
`created_at` 是**字符串**、条数对得上、且**不抛异常**。
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.append(str(_ROOT))

from agent_community.tests.conftest import resolve_handler  # noqa: E402


def test_events_endpoint_survives_real_message_objects(monkeypatch):
    from agent_community.platform import server as srv
    from agent_community.platform.protocol import Message, MessageType, Task

    task = Task(id="evt-1", title="事件流守卫", description="d", created_by="user")
    task.messages.append(Message(type=MessageType.CHAT, from_agent="user",
                                 content="这条消息必须出现在事件流里", task_id="evt-1"))
    monkeypatch.setitem(srv.tasks, "evt-1", task)

    fn = resolve_handler("list_events")
    out = asyncio.run(fn(50))

    assert isinstance(out, dict), out
    assert out["total"] == len(out["events"]) >= 1, "事件流没带上刚造的那条消息：%r" % (out,)
    sample = out["events"][0]
    assert isinstance(sample.get("created_at"), str), \
        "created_at 必须是字符串（datetime 与 str 混排会让 sort 抛 TypeError）：%r" % (sample,)
    assert sample.get("content") and sample.get("type"), sample


def test_events_endpoint_handles_mixed_time_types(monkeypatch):
    """datetime 与 str 混在一起时也不许炸（这正是当初 500 的第二个成因）。"""
    from agent_community.platform import server as srv
    from agent_community.platform.protocol import Message, MessageType, Task

    t1 = Task(id="evt-a", title="A", description="d", created_by="user")
    t1.messages.append(Message(type=MessageType.CHAT, from_agent="user", content="aa",
                               task_id="evt-a"))            # Message.timestamp 是 datetime
    t2 = Task(id="evt-b", title="B", description="d", created_by="user")
    t2.created_at = "2026-10-01T00:00:00"                    # 人为塞成字符串（旧数据形态）
    t2.messages.append(Message(type=MessageType.CHAT, from_agent="user", content="bb",
                               task_id="evt-b"))
    monkeypatch.setitem(srv.tasks, "evt-a", t1)
    monkeypatch.setitem(srv.tasks, "evt-b", t2)

    out = asyncio.run(resolve_handler("list_events")(50))
    assert out["total"] >= 2, out
    assert all(isinstance(e.get("created_at"), str) for e in out["events"]), out["events"]
