# -*- coding: utf-8 -*-
"""`platform/routers/workshops_lifecycle.py` 的 `POST /api/workshop`：建间后的初始状态。

**为什么锁这条**（2026-10-11 缺陷 + 修复）：

老板口径（2026-10-10 确认）：
    大厅发任务 → 平台自动建好工作间 → **进去就能直接开始聊**（一级讨论，初步定任务方向）
    → 再点「选定员工」，平台按任务方向自动选成员。

而实测发现：大厅「发布」走的 `POST /api/workshop`（`frontend/index.html:670`，
body 只有 `{hall_content}`）**构造时不设 status** ⇒ 落默认 `draft`；
`POST …/discuss` 里那句 `if ws.status not in ("division","review","running"): ws.status = "discussing"`
**只会在用户发出第一条消息时**才把状态掰过来 ⇒ 表现为"进去先是草稿态、发了消息才是讨论中"。

修复：**有任务内容就在建间时进 `discussing`**（与另一条自动建间入口 `server.py:1365` 一致）；
**没有任务内容仍为 `draft`**（纯手工建的空间不假装在讨论）。

状态机权威：`workshop.py:80` `draft → discussing(一级讨论) → selecting → division → running → review → done`。
"""
from __future__ import annotations

import asyncio
import json

import pytest

from agent_community.platform import state as state_mod
from agent_community.platform.routers import workshops_lifecycle as wl
from agent_community.platform.workshop import Workshop


def run(coro):
    return asyncio.run(coro)


def unpack(resp):
    if hasattr(resp, "status_code") and hasattr(resp, "body"):
        return resp.status_code, json.loads(resp.body)
    return 200, resp


class FakeRequest:
    def __init__(self, body=None):
        self.headers = {}
        self._body = body if body is not None else {}

    async def json(self):
        return self._body


@pytest.fixture(autouse=True)
def _clean_workshops():
    """建间端点会往全局 `workshops` 里塞对象 ⇒ 用例前后清干净，别污染其它测试。"""
    before = set(state_mod.workshops.keys())
    try:
        yield
    finally:
        for k in list(state_mod.workshops.keys()):
            if k not in before:
                state_mod.workshops.pop(k, None)


def _create(body):
    status, data = unpack(run(wl.create_workshop(FakeRequest(body))))
    return status, data


def _ws_of(data):
    return state_mod.workshops.get(data["workshop_id"])


def test_hall_task_starts_in_discussing():
    """★核心：大厅发任务建出来的间，**建好即处于一级讨论**（老板要的"进去就能直接开始聊"）。"""
    status, data = _create({"hall_content": "做一个看板", "name": "看板项目"})
    assert status == 200 and data["success"] is True
    ws = _ws_of(data)
    assert ws is not None
    assert ws.status == "discussing", "有任务内容时建间就应进入一级讨论，而不是停在 draft"


def test_hall_task_without_name_still_discussing():
    """没传 name 时用任务内容命名（既有行为），状态同样要进讨论。"""
    status, data = _create({"hall_content": "  做个用户管理后台  "})
    assert status == 200
    ws = _ws_of(data)
    assert ws.status == "discussing"
    assert ws.name.strip() != "" and ws.name != "未命名工作间"


def test_blank_hall_stays_draft():
    """没有任务内容 ⇒ 仍是草稿（纯手工建的空间，不假装在讨论）。"""
    status, data = _create({"name": "手工空间"})
    assert status == 200
    ws = _ws_of(data)
    assert ws.status == "draft"


def test_whitespace_only_hall_stays_draft():
    """空白任务内容按"没有任务"处理（`hall.strip()` 为空的边界）。"""
    status, data = _create({"hall_content": "   \n\t  "})
    assert status == 200
    ws = _ws_of(data)
    assert ws.status == "draft"


def test_discussing_workshop_accepts_first_message():
    """端到端语义：建出来的间**不用先"自愈"就能进讨论**（状态本来就对）。

    这里只断言状态前置条件 —— 讨论端点自身的行为由 `test_workshops_discuss.py` 覆盖。
    修复的意义在于：用户进去之前状态就是 `discussing`，与界面显示一致
    （不再"显示草稿/发消息才变讨论中"的割裂）。
    """
    status, data = _create({"hall_content": "分析竞品", "name": "竞品分析"})
    ws = _ws_of(data)
    # 讨论端点里那句自愈兜底的**触发条件**（status not in (...)）在建间后就已不成立
    assert ws.status not in ("draft",), "建间后不应再落在 draft"
    assert ws.status == "discussing"
