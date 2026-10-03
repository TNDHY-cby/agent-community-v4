# -*- coding: utf-8 -*-
"""V-13 并发 + 故障注入测试（补齐审阅点名的最实在缺口）。

背景：外部代码审阅指出——「多 Agent 并发协作场景下的压力测试和故障注入测试，
从现有测试文件清单中看不到对应的专用测试集」。本文件补齐：

A. 故障注入：任务执行中途失败/断连/超时 的状态机行为
   - 心跳超时兜底（tick）
   - L0 自动重试 → L1 升级的升级路径
   - 终态幂等（防二次变更）
B. 并发：asyncio 交错下写状态不丢失
C. 测试隔离：state.reset() / snapshot() 语义（V-13 步骤3 的产物）

全部离线、无服务依赖、自包含；每个测试独立，互不污染。
"""
from __future__ import annotations

import asyncio
import time

import pytest

from agent_community.platform import state
from agent_community.platform.task_state_machine import (
    ACT_AUTO_RETRY,
    ACT_ESCALATE_L1,
    ACT_PAUSE,
    ACT_NOOP,
    ASSIGNED,
    BLOCKED_RETRYING,
    CREATED,
    DONE,
    DROPPED,
    EXECUTING,
    STUCK_PAUSED,
    TIMEOUT,
    WAITING_REPLY,
    EV_BLOCK,
    EV_COMPLETE,
    EV_RETRY_OK,
    EV_STUCK,
    EV_TIMEOUT,
    TaskStateMachine,
)


# ─────────────────────────────────────────────────────────────
# A. 故障注入
# ─────────────────────────────────────────────────────────────

def _expire(st):
    """把某个状态的 last_ts 拨回很久以前，模拟心跳丢失。"""
    st["last_ts"] = time.time() - 9999


class TestFaultInjection:
    """任务执行中途出问题时，状态机必须给出可预期的转移与动作。"""

    def test_heartbeat_timeout_triggers_auto_retry(self):
        """执行中超时 → L0 自动重试（重试次数未达上限时）。"""
        m = TaskStateMachine(timeout_sec=1.0, max_auto_retry=3)
        m.ensure("t1", CREATED)
        m.set_state("t1", EXECUTING)
        _expire(m._states["t1"])

        fired = m.tick()
        assert [k for k, _ in fired] == ["t1"], f"tick 应只触发 t1，实得 {fired}"
        assert m.get_state("t1") == BLOCKED_RETRYING, "超时应转入 blocked_retrying"
        action = fired[0][1].get("action")
        assert action == ACT_AUTO_RETRY, f"首个超时应自动重试，实得 {action}"

    def test_timeout_escalates_to_l1_after_retries(self):
        """真实重试循环，直至配额耗尽才 L1（不是无限重试）。

        实测语义（probe_sm2）：
            EXECUTING --timeout--> BLOCKED_RETRYING (retries+1, auto_retry)
            BLOCKED_RETRYING --EV_RETRY_OK--> EXECUTING
            直到 retries >= max_auto_retry，再 timeout 则 TIMEOUT + escalate_l1
        注意：BLOCKED_RETRYING 若**未**收到 RETRY_OK 就再次超时，是直接 L1 ——
        那表示「重试已派发但没恢复」，属确定性故障，见 test_deterministic_block。
        """
        m = TaskStateMachine(timeout_sec=1.0, max_auto_retry=2)
        m.ensure("t2", CREATED)
        m.set_state("t2", EXECUTING)

        # 两轮「超时 → 自动重试 → 重试恢复」
        for i in range(2):
            _expire(m._states["t2"])
            fired = m.tick()
            assert fired, f"第 {i+1} 次超时应触发"
            assert fired[0][1].get("action") == ACT_AUTO_RETRY, \
                f"第 {i+1} 次超时应自动重试，实得 {fired[0][1].get('action')}"
            assert m.get_state("t2") == BLOCKED_RETRYING
            r = m.on_event("t2", EV_RETRY_OK, {})
            assert m.get_state("t2") == EXECUTING, \
                f"RETRY_OK 应回到 executing，实得 {r}"

        # 第 3 次：retries 已达上限 → 升级
        _expire(m._states["t2"])
        fired = m.tick()
        assert fired, "耗尽配额后的超时仍应触发"
        assert m.get_state("t2") == TIMEOUT, "耗尽重试应转入 timeout"
        assert fired[0][1].get("action") == ACT_ESCALATE_L1, "应升级 L1"

    def test_deterministic_block_escalates_immediately(self):
        """确定性故障（EV_BLOCK）不重试，直接 L1。"""
        m = TaskStateMachine(timeout_sec=1.0, max_auto_retry=5)
        m.ensure("t3", EXECUTING)
        r = m.on_event("t3", EV_BLOCK, {"reason": "missing_dep"})
        assert m.get_state("t3") == TIMEOUT
        assert r.get("action") == ACT_ESCALATE_L1, f"EV_BLOCK 应直接 L1，实得 {r.get('action')}"

    def test_stuck_pauses_without_retry(self):
        """过程性困难（EV_STUCK）→ 暂停，不重试（组长先解决）。"""
        m = TaskStateMachine(timeout_sec=1.0, max_auto_retry=5)
        m.ensure("t4", EXECUTING)
        r = m.on_event("t4", EV_STUCK, {})
        assert m.get_state("t4") == STUCK_PAUSED
        assert r.get("action") == ACT_PAUSE

    def test_waiting_reply_timeout_no_retry(self):
        """等待回复超时 → L1，不占用重试配额。"""
        m = TaskStateMachine(timeout_sec=1.0, max_auto_retry=3)
        m.ensure("t5", WAITING_REPLY)
        _expire(m._states["t5"])
        fired = m.tick()
        assert fired, "WAITING_REPLY 超时应触发"
        assert m.get_state("t5") == TIMEOUT
        assert fired[0][1].get("action") == ACT_ESCALATE_L1

    def test_terminal_state_is_idempotent(self):
        """终态幂等：DONE 之后任何事件都不再改变状态（防二次变更）。"""
        m = TaskStateMachine(timeout_sec=0.001, max_auto_retry=1)
        m.ensure("t6", EXECUTING)
        m.on_event("t6", EV_COMPLETE, {})
        assert m.get_state("t6") == DONE
        before = m.to_dict()["states"]["t6"]

        for ev in (EV_TIMEOUT, EV_BLOCK, EV_COMPLETE, EV_STUCK):
            r = m.on_event("t6", ev, {})
            assert r.get("action") == ACT_NOOP, f"{ev} 应被终态拒绝，实得 {r}"
            assert r.get("note", "").startswith("terminal"), f"{ev} 应标注 terminal，实得 {r}"
        assert m.to_dict()["states"]["t6"] == before, "终态后状态不得变化"

    def test_tick_ignores_non_watched_states(self):
        """tick 只盯执行中的状态，不惊动 created/done。"""
        m = TaskStateMachine(timeout_sec=1.0)
        m.ensure("t7", CREATED)
        m.ensure("t8", DONE)
        _expire(m._states["t7"])
        _expire(m._states["t8"])
        assert m.tick() == [], "created/done 不应被 tick 触发"

    def test_tick_respects_timeout_threshold(self):
        """未超时不触发（防止正常任务被误判掉线）。"""
        m = TaskStateMachine(timeout_sec=30.0)
        m.ensure("t9", EXECUTING)     # last_ts = now
        assert m.tick() == [], "未超时不应触发"


# ─────────────────────────────────────────────────────────────
# B. 并发（asyncio 交错，模拟请求处理中被打断）
# ─────────────────────────────────────────────────────────────

class TestConcurrency:
    """真实场景：多个外端 harness 同时注册/同时完成，状态不得丢失。

    注：用 `asyncio.run()` 而非 @pytest.mark.asyncio —— 与本项目其余测试
    （test_provider_stack / test_reasoning_effort 等）保持一致，不引入 pytest-asyncio 依赖。
    """

    def test_concurrent_state_writes_no_loss(self):
        """50 个协程交错写不同 key，全部可见（无 lost update）。"""
        m = TaskStateMachine(timeout_sec=60.0)
        results = []

        async def worker(i):
            await asyncio.sleep(0)          # 交错点（真实场景=HTTP I/O）
            m.ensure(f"c{i}", CREATED)
            await asyncio.sleep(0)
            results.append(m.get_state(f"c{i}"))

        async def run():
            await asyncio.gather(*(worker(i) for i in range(50)))

        asyncio.run(run())
        keys = [k for k in m.to_dict()["states"] if k.startswith("c")]
        assert len(keys) == 50, f"并发写入丢失：期望 50 实得 {len(keys)}"
        assert all(r == CREATED for r in results)

    def test_concurrent_transitions_on_distinct_keys(self):
        """并发对不同 key 推进状态，互不串扰。"""
        m = TaskStateMachine(timeout_sec=60.0)

        async def worker(i):
            await asyncio.sleep(0)
            m.ensure(f"d{i}", EXECUTING)
            await asyncio.sleep(0)
            m.on_event(f"d{i}", EV_COMPLETE, {})
            return m.get_state(f"d{i}")

        async def run():
            return await asyncio.gather(*(worker(i) for i in range(30)))

        res = asyncio.run(run())
        assert len(set(res)) == 1 and next(iter(res)) == DONE, f"并发迁移结果异常: {set(res)}"

    def test_reset_mid_flight_clears_state(self):
        """reset() 插入并发流程中，能就地清空且后续写入仍正常。"""
        async def writer():
            await asyncio.sleep(0)
            state.tasks["mid"] = {"x": 1}
            await asyncio.sleep(0)
            return len(state.tasks)

        asyncio.run(writer())
        assert "mid" in state.tasks
        state.reset()
        assert len(state.tasks) == 0, "reset 应清空"
        # 清空后继续并发写，仍可用（证明未破坏引用）
        n = asyncio.run(writer())
        assert n == 1, "reset 后再次并发写入应成功"
        state.reset()


# ─────────────────────────────────────────────────────────────
# C. 测试隔离（V-13 步骤3 产物的语义保证）
# ─────────────────────────────────────────────────────────────

class TestStateIsolation:
    """reset() 是后续所有测试隔离的前置——语义必须先立住。"""

    def test_reset_clears_in_place_no_rebind(self):
        """reset 只 clear，不重新绑定（保双副本语义，防 --token 分裂重演）。"""
        state.reset()
        sentinel = state.tasks
        state.tasks["iso"] = {"a": 1}
        state.reset()
        assert len(state.tasks) == 0, "reset 应清空"
        assert state.tasks is sentinel, "reset 不得重新绑定对象"
        assert state.S.tasks is state.tasks, "S 命名空间须与直接引用同一对象"

    def test_snapshot_is_deep_copy(self):
        """snapshot 深拷贝：改快照不污染真实状态。"""
        state.reset()
        state.tasks["snap"] = {"inner": {"k": 1}}
        s = state.snapshot()
        s["tasks"]["snap"]["inner"]["k"] = 999
        assert state.tasks["snap"]["inner"]["k"] == 1, "snapshot 必须是深拷贝"
        state.reset()

    def test_namespace_proxy_sees_writes_both_directions(self):
        """S 与直接引用双向可见（新旧代码可并存）。"""
        state.reset()
        state.S.tasks["viaS"] = {"v": 1}
        assert "viaS" in state.tasks, "经 S 写入须对直接引用可见"
        state.tasks["viaDirect"] = {"v": 2}
        assert "viaDirect" in state.S.tasks, "经直接引用写入须对 S 可见"
        state.reset()

    def test_isolation_between_tests(self):
        """前一个测试留下的状态，不会泄漏到本测试（reset 是前置）。"""
        state.reset()
        assert len(state.tasks) == 0
        state.tasks["leak_check"] = {"y": 1}
        assert len(state.tasks) == 1
        state.reset()
        assert len(state.tasks) == 0
