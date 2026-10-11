"""P0 fixes: begin-selecting really advances the stage; no-agent selecting explains itself.

These lock the two defects found by the V40 walkthrough (a real user clicking through):
  1. endDiscuss() only flipped a browser variable -> backend status stayed 'discussing'
     -> the stage bar kept saying "一级讨论". Fix: POST …/begin-selecting really advances.
  2. With ZERO registered agents, "让平台 AI 选定员工" asked the AI anyway, got an empty
     list, and blamed "harness 与任务需求不匹配" -- misleading -- while the UI swallowed it.
     Fix: refuse early with a truthful reason + how to fix.
"""
from __future__ import annotations

import asyncio
import json

import pytest

from agent_community.platform import state as state_mod
from agent_community.platform.routers import workshops_assign as wa
from agent_community.platform.workshop import Workshop, WorkshopMember


def run(coro):
    return asyncio.run(coro)


@pytest.fixture()
def ws():
    w = Workshop(workshop_id="w-p0", name="P0 回归", workspace_dir="",
                 hall_content="做一个本地记账小工具", members=[])
    w.status = "discussing"
    state_mod.workshops[w.workshop_id] = w
    yield w
    state_mod.workshops.pop(w.workshop_id, None)


def test_begin_selecting_really_advances_backend_status(ws):
    """★核心：后端状态必须真的从 discussing 走到 selecting（修前它纹丝不动）。"""
    res = run(wa.workshop_begin_selecting(ws.workshop_id))
    assert res.get("success") is True
    assert res.get("changed") is True
    assert state_mod.workshops[ws.workshop_id].status == "selecting", \
        "点了『结束讨论』后端状态却没动 —— 阶段指示器会一直显示『一级讨论』"


def test_begin_selecting_is_idempotent(ws):
    """再点一次（或已经走到更后面）不能把状态往回拖。"""
    run(wa.workshop_begin_selecting(ws.workshop_id))
    again = run(wa.workshop_begin_selecting(ws.workshop_id))
    assert again.get("changed") is False
    assert state_mod.workshops[ws.workshop_id].status == "selecting"

    ws.status = "division"
    later = run(wa.workshop_begin_selecting(ws.workshop_id))
    assert later.get("changed") is False
    assert state_mod.workshops[ws.workshop_id].status == "division", "不许把后续阶段拖回来"


def test_begin_selecting_unknown_workshop_is_404():
    from agent_community.platform.server import Utf8JSONResponse
    res = run(wa.workshop_begin_selecting("根本没有这个工作间"))
    assert isinstance(res, Utf8JSONResponse)
    assert res.status_code == 404


def test_select_members_with_no_agents_explains_truthfully(ws, monkeypatch):
    """★核心：0 个 agent 时必须**说真话**（不是"需求不匹配"），并给"去哪儿接"。"""
    monkeypatch.setattr(wa, "harness_manager", type("H", (), {
        "list_sessions": staticmethod(lambda: []),
        "sessions": {},
    })())

    class _AI:  # 有 AI provider，否则会先被"未配置"拦掉
        def chat(self, system, user):  # pragma: no cover - 不该被调用
            raise AssertionError("0 个 agent 时不该去问 AI")

    monkeypatch.setattr(wa, "_sv", lambda: type("S", (), {"ai_provider": _AI()})())

    res = run(wa.workshop_select_members(ws.workshop_id))
    assert res.get("success") is False
    assert res.get("error_code") == "no_harness"
    assert "没有接入任何" in res.get("reason", ""), res
    assert res.get("advice"), "必须给怎么办"
    assert res.get("register_url"), "必须给去哪儿接"
