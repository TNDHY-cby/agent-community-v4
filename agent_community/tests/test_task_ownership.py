# -*- coding: utf-8 -*-
"""V-20 原子所有权（防抢单）测试 —— 47 号任务书验收项 #2。

覆盖：
  1. **并发 10 次领取同一任务 → 恰好 1 次成功**（47 号验收原文）
  2. 不同交付单元互不阻塞（层内并行不被串行化）
  3. 同 owner 重入 → 续租，token 不变
  4. release 后他人可领取
  5. owner / token 不符不可释放
  6. TTL 过期后可被他人领取（stranded 回收的钩子）
  7. reap_expired 返回并清除过期 key
  8. 竞争路径跨事件循环不炸（回归守卫，见下）
  9. execute_layers 同交付单元被**两个不同 owner** 双份派发 → 只真实执行一次

注：用 `asyncio.run()` 而非 @pytest.mark.asyncio —— 与本项目其余测试同范式。
"""
from __future__ import annotations

import asyncio

import pytest

from agent_community.platform.protocol import (
    DelegationRequest,
    ExecutionResult,
    ReviewResponse,
)
from agent_community.platform.task_ownership import (
    OwnershipRegistry,
    delegation_key,
    task_key,
)


def run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _clean_singleton():
    """state 里的模块级 ownership 单例是跨用例共享的，每个用例前后清干净。"""
    from agent_community.platform import state as _state

    _state.ownership.reset()
    yield
    _state.ownership.reset()


# ══════════════════════════════════════════════════════════════
# 一、登记表本体
# ══════════════════════════════════════════════════════════════

class TestAtomicClaim:
    def test_ten_concurrent_claims_only_one_wins(self):
        """★ 47 号验收项 #2：并发 10 次领取同一任务，仅 1 次成功。"""
        reg = OwnershipRegistry(default_ttl=60.0)
        key = task_key("t-47")

        async def main():
            return await asyncio.gather(*[reg.acquire(key, f"agent-{i}") for i in range(10)])

        leases = run(main())
        winners = [l for l in leases if l is not None]
        assert len(winners) == 1, f"应恰好 1 个 owner 领到，实际 {len(winners)} 个"
        assert reg.owner_of(key) == winners[0].owner
        assert reg.snapshot()["denials"] == 9

    def test_contended_acquire_survives_a_second_event_loop(self):
        """回归守卫：模块级单例若用 `asyncio.Lock`，**竞争路径**会调 `_get_loop()`
        把自己绑死到首个 loop，第二个 loop 再竞争即
        `RuntimeError: ... is bound to a different event loop`
        （2026-10-07 实测复现；pytest 逐用例 asyncio.run 正是该场景）。
        V-20 临界区改用 threading.Lock，本用例锁死该行为不复发。
        """
        reg = OwnershipRegistry(default_ttl=60.0)
        key = task_key("t-loop")

        async def main():
            return await asyncio.gather(*[reg.acquire(key, f"a{i}") for i in range(10)])

        first = run(main())
        second = run(main())          # 换一个事件循环，继续竞争同一 key
        first_winners = [x for x in first if x]
        second_winners = [x for x in second if x]
        assert len(first_winners) == 1
        # 关键：第二个循环里**不能抛** RuntimeError；且只有原 owner 能续租，其余全被拒
        assert [w.owner for w in second_winners] == [first_winners[0].owner]

    def test_different_units_do_not_block_each_other(self):
        """★ 层内并行保障：同一 owner 领**不同**交付单元应全部成功。"""
        reg = OwnershipRegistry()
        async def main():
            return await asyncio.gather(
                reg.acquire(delegation_key("d1"), "task-1"),
                reg.acquire(delegation_key("d2"), "task-1"),
                reg.acquire(delegation_key("d3"), "task-1"),
            )
        leases = run(main())
        assert all(l is not None for l in leases), "不同交付单元不该互相阻塞"

    def test_same_owner_reacquire_renews_same_token(self):
        """同 owner 重入 → 续租（容忍审查回退重试与兜底层二次 acquire），token 不变。"""
        reg = OwnershipRegistry(default_ttl=10.0)
        async def main():
            l1 = await reg.acquire(delegation_key("d1"), "task-1")
            l2 = await reg.acquire(delegation_key("d1"), "task-1")
            return l1, l2
        l1, l2 = run(main())
        assert l1 is not None and l2 is not None
        assert l2.token == l1.token, "续租不应换 token，否则外层旧票据将无法释放"
        assert reg.snapshot()["renewals"] == 1

    def test_invalid_args_do_not_raise(self):
        reg = OwnershipRegistry()
        async def main():
            return (await reg.acquire("", "a"), await reg.acquire("k", ""),
                    await reg.release("", "a"), await reg.release("k", ""))
        assert run(main()) == (None, None, False, False)


class TestRelease:
    def test_release_then_next_owner_can_claim(self):
        reg = OwnershipRegistry()
        async def main():
            l = await reg.acquire(delegation_key("d1"), "task-1")
            ok = await reg.release(delegation_key("d1"), "task-1", l.token)
            l2 = await reg.acquire(delegation_key("d1"), "task-2")
            return ok, l2
        ok, l2 = run(main())
        assert ok is True
        assert l2 is not None and l2.owner == "task-2"

    def test_release_requires_matching_owner_and_token(self):
        reg = OwnershipRegistry()
        async def main():
            l = await reg.acquire(delegation_key("d1"), "task-1")
            wrong_owner = await reg.release(delegation_key("d1"), "task-2", l.token)
            wrong_token = await reg.release(delegation_key("d1"), "task-1", "deadbeef")
            good = await reg.release(delegation_key("d1"), "task-1", l.token)
            return wrong_owner, wrong_token, good
        assert run(main()) == (False, False, True)


class TestTtlAndReap:
    def test_expired_lease_can_be_claimed_by_another_owner(self):
        reg = OwnershipRegistry(default_ttl=0.05)
        async def main():
            l1 = await reg.acquire(task_key("t"), "a")
            await asyncio.sleep(0.12)
            l2 = await reg.acquire(task_key("t"), "b")
            return l1, l2
        l1, l2 = run(main())
        assert l1 is not None and l2 is not None and l2.owner == "b"
        assert l2.token != l1.token, "过期重发必须换新 token，让旧票据彻底作废"

    def test_owner_of_returns_none_after_expiry(self):
        reg = OwnershipRegistry(default_ttl=0.03)
        async def main():
            await reg.acquire(task_key("t"), "a")
            await asyncio.sleep(0.08)
            return reg.owner_of(task_key("t"))
        assert run(main()) is None

    def test_reap_expired_returns_and_clears_keys(self):
        reg = OwnershipRegistry(default_ttl=0.05)
        async def main():
            await reg.acquire(task_key("t1"), "a")
            await reg.acquire(task_key("t2"), "a")
            await asyncio.sleep(0.12)
            gone = await reg.reap_expired()
            return sorted(gone), reg.snapshot()
        gone, snap = run(main())
        assert gone == sorted([task_key("t1"), task_key("t2")])
        assert snap["held"] == 0 and snap["reaps"] == 2


# ══════════════════════════════════════════════════════════════
# 二、接入点：execute_layers（防同一子任务双份执行）
# ══════════════════════════════════════════════════════════════

def _delegation(did: str, task_id: str = "task-47") -> DelegationRequest:
    return DelegationRequest(
        id=did, from_agent="leader", to_agent="worker-1",
        capability_required="code", task_id=task_id,
        title="子任务", description="做事",
    )


def _run_once(*, owners: tuple[str, ...]):
    """让 N 个不同 owner 并发对**同一交付单元**跑 execute_layers。

    返回 (results_per_call, 真实执行次数)。
    """
    from agent_community.platform.discussion_engine import DiscussionEngine

    engine = DiscussionEngine()
    d = _delegation("d-1")
    calls: list[str] = []

    async def execute_fn(exec_msg):
        calls.append(exec_msg.delegation.id)
        await asyncio.sleep(0.05)      # 拉开窗口，保证两次派发真的重叠
        return ExecutionResult(
            delegation_id=exec_msg.delegation.id,
            from_agent=exec_msg.delegation.from_agent,
            to_agent=exec_msg.delegation.to_agent,
            ok=True, content="done",
        )

    async def review_fn(req):
        return ReviewResponse(request_id=req.request_id, from_agent="reviewer",
                              verdict="pass", feedback="ok", score=1.0)

    async def main():
        return await asyncio.gather(*[
            engine.execute_layers(
                task_id="task-47", delegations=[d], dependencies=[],
                execute_fn=execute_fn, review_fn=review_fn, owner=o,
            )
            for o in owners
        ])

    return run(main()), calls


class TestExecuteLayersOwnership:
    def test_two_different_owners_racing_the_same_unit_execute_once(self):
        """★ 两个不同 owner 抢同一交付单元 → 只真实执行 1 次，另一份被拒。"""
        results, calls = _run_once(owners=("leader-A", "leader-B"))
        assert len(calls) == 1, f"应只真实执行 1 次，实际 {len(calls)} 次"
        outs = [r["d-1"] for r in results]
        assert sum(1 for o in outs if o.ok) == 1
        rejected = [o for o in outs if not o.ok]
        assert len(rejected) == 1 and "已被" in (rejected[0].error or "")

    def test_same_owner_redispatch_renews_instead_of_rejecting(self):
        """同 owner 重入按设计**续租放行**（容忍同一调度方的重试）。

        这是设计稿明确的语义选择；本用例把它钉住，防止将来被误改成硬拒。
        """
        results, calls = _run_once(owners=("leader-A", "leader-A"))
        assert len(calls) == 2, "同 owner 重入应放行（续租语义）"

    def test_locks_are_released_after_run(self):
        """跑完必须归还锁，否则后续派发被永久挡住。"""
        from agent_community.platform import state as _state
        from agent_community.platform.task_ownership import delegation_key as dk

        _run_once(owners=("leader-A",))
        assert _state.ownership.owner_of(dk("d-1")) is None, "交付单元锁未归还"
        assert _state.ownership.snapshot()["held"] == 0

    def test_locks_released_even_when_execution_raises(self):
        """执行抛异常时 finally 仍须归还锁。"""
        from agent_community.platform import state as _state
        from agent_community.platform.discussion_engine import DiscussionEngine
        from agent_community.platform.task_ownership import delegation_key as dk

        engine = DiscussionEngine()
        d = _delegation("d-boom")

        async def boom_execute(exec_msg):
            raise RuntimeError("模拟执行崩")

        async def review_fn(req):
            return ReviewResponse(request_id=req.request_id, from_agent="r",
                                  verdict="pass", feedback="ok", score=1.0)

        async def main():
            return await engine.execute_layers(
                task_id="task-47", delegations=[d], dependencies=[],
                execute_fn=boom_execute, review_fn=review_fn, owner="leader-A",
            )

        out = run(main())
        assert out["d-boom"].ok is False          # gather(return_exceptions=True) 已兜住
        assert _state.ownership.owner_of(dk("d-boom")) is None, "异常路径未归还锁"
