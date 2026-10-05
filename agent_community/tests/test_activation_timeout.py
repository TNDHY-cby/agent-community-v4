# -*- coding: utf-8 -*-
"""V-15 步骤7 测试：激活超时分档（§10.2 + 验证矩阵 #6）。

验证矩阵 #6：「C 档未回报 | **90s 后 timeout -> needs_human**」
§10.2：「未回报 → 平台标为 needs_human，**不继续派活**」

原实现是**无上限重派**（每 90s 一次、无限）—— 外端真没接时一直循环，
用户完全看不出卡在哪一层。本文件把「有上限 + 能看见」锁住。

含一个**我自己引入又修掉**的顺序缺陷回归：
    `_handle_activation_timeout` 必须**在** `_activate_single` 之后写 entry_state，
    否则后者会把它覆盖成 dispatched 并清空 entry_reason —— 超时信息彻底丢失。
"""
from __future__ import annotations

import asyncio

import pytest

from agent_community.platform import entry_state as es
from agent_community.platform.workshop import Workshop, WorkshopMember


def _mk(wid="w1", mid="m1"):
    ws = Workshop(workshop_id=wid, name="t", workspace_dir=f"D:\\ws\\{wid}",
                  hall_content="hall")
    m = WorkshopMember(member_id=mid, role="员工", display_name="甲",
                       harness_ids=["h_none"])
    ws.members.append(m)
    return ws, m


def run(coro):
    return asyncio.run(coro)


class TestEscalationSteps:
    def test_first_timeout_retries_and_marks_timeout(self):
        """第 1 次超时 -> 重派，且 entry_state 落到 timeout（不是被覆盖成 dispatched）。"""
        from agent_community.platform.server import _handle_activation_timeout
        ws, m = _mk()
        out = _handle_activation_timeout(ws, m)
        assert out == "retry"
        assert m._activate_retries == 1
        assert m.entry_state == es.TIMEOUT, \
            f"超时必须可见，实得 {m.entry_state!r}（疑似被 _activate_single 覆盖）"
        assert "第 1/2" in m.entry_reason, m.entry_reason

    def test_second_timeout_also_retries(self):
        from agent_community.platform.server import _handle_activation_timeout
        ws, m = _mk()
        _handle_activation_timeout(ws, m)
        out = _handle_activation_timeout(ws, m)
        assert out == "retry"
        assert m._activate_retries == 2
        assert m.entry_state == es.TIMEOUT
        assert "第 2/2" in m.entry_reason, m.entry_reason

    def test_third_timeout_escalates_to_needs_human(self):
        """第 3 次 -> needs_human，**停止派活**（§10.2）。"""
        from agent_community.platform.server import _handle_activation_timeout
        ws, m = _mk()
        _handle_activation_timeout(ws, m)
        _handle_activation_timeout(ws, m)
        out = _handle_activation_timeout(ws, m)
        assert out == "needs_human"
        assert m.entry_state == es.NEEDS_HUMAN
        assert m.status == "blocked", \
            "必须退出 pending，否则恢复循环会继续命中 -> 违反「不继续派活」"
        assert "人工" in m.entry_reason

    def test_no_further_dispatch_after_escalation(self):
        """needs_human 之后：恢复循环的命中条件（status=='pending'）不再成立。"""
        from agent_community.platform.server import _handle_activation_timeout
        ws, m = _mk()
        for _ in range(3):
            _handle_activation_timeout(ws, m)
        assert m.status != "pending", "已停手，不该再被超时循环命中"
        # 第 4 次调用（若被误调）：计数已到顶，仍是 needs_human
        out = _handle_activation_timeout(ws, m)
        assert out == "needs_human"

    def test_status_blocked_wins_over_timeout(self):
        """重派本身失败（桥已死）时，blocked 比 timeout 更具体 —— 让 blocked 胜出。"""
        from agent_community.platform import bridge_procs
        from agent_community.platform.server import _handle_activation_timeout

        class _Dead:
            pid = 9

            def poll(self):
                return 1

        bridge_procs._procs.clear()
        bridge_procs.record("h_none", _Dead())
        try:
            ws, m = _mk()
            out = _handle_activation_timeout(ws, m)
            assert out == "retry"
            assert m.entry_state == es.BLOCKED, \
                f"派发失败应为 blocked，实得 {m.entry_state!r}"
            assert m.status == "blocked"
            assert m.entry_reason, "必须给出派发失败原因"
        finally:
            bridge_procs._procs.clear()


class TestRetryCounterReset:
    def test_entered_resets_counter(self):
        """成功回报即开新周期，计数归零 —— 否则上次攒的会让下次一超时就直接停手。"""
        from agent_community.platform.routers import harness_register as hr
        from agent_community.platform.state import workshops as _wsmap

        ws, m = _mk("wr", "mr")
        m._activate_retries = 2
        m.entry_state = es.TIMEOUT
        _wsmap[ws.workshop_id] = ws

        class R:
            headers = {}

            async def json(self):
                return {"workshop_id": "wr", "member_id": "mr", "status": "entered"}

        try:
            run(hr.harness_activation_result(R()))
            assert m._activate_retries == 0, "回报 entered 必须归零"
            assert m.entry_state == es.ENTERED
            assert m.entry_reason == ""
        finally:
            _wsmap.pop(ws.workshop_id, None)

    def test_blocked_report_does_not_reset(self):
        """blocked 回报不该清计数（失败还在累积中）。"""
        from agent_community.platform.routers import harness_register as hr
        from agent_community.platform.state import workshops as _wsmap

        ws, m = _mk("wb", "mb")
        m._activate_retries = 1
        _wsmap[ws.workshop_id] = ws

        class R:
            headers = {}

            async def json(self):
                return {"workshop_id": "wb", "member_id": "mb", "status": "blocked",
                        "reason": "连不上"}

        try:
            run(hr.harness_activation_result(R()))
            assert m._activate_retries == 1, "失败不该重置失败计数"
            assert m.entry_state == es.BLOCKED
        finally:
            _wsmap.pop(ws.workshop_id, None)


class TestRecoverLoopWiring:
    def test_loop_calls_escalator(self):
        """恢复循环必须接上调用器（源码断言，防止改回去）。"""
        import inspect
        from agent_community.platform import server as srv
        src = inspect.getsource(srv)
        assert "_handle_activation_timeout(ws, m)" in src, \
            "恢复循环未接入超时分档"
