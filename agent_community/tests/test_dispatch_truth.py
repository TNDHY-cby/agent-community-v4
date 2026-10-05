# -*- coding: utf-8 -*-
"""V-15 步骤2 测试：派发去假成功。

对应设计稿 §十四 步骤2 的验证要求「桥不在时返回 False 且不入队」，
以及 §十五 验证矩阵 #3「开工，桥不在场 → 返回 False，成员标 blocked，不产生假成功」。

三态语义（见 bridge_procs 模块说明）：
    alive   -> 入队，True
    dead    -> **不入队**，False          ← 本次修复的主体
    unknown -> 入队，True，但 note 标注「存活未知」

背景：改前队列分支**无条件** `return True, "已入 pending 队列（等桥领取）"`，
而 `_activate_single` 是**裸调用**、`_activate_leader_if_needed` **丢弃 _disp_ok** ——
假成功会从队列分支转移到调用方。本文件对这三处各锁一个用例。
"""
from __future__ import annotations

import asyncio

import pytest

from agent_community.platform import bridge_procs


class FakeProc:
    """桥进程替身：poll() 返回 None 表示活着。"""

    def __init__(self, alive=True, pid=4242):
        self.pid = pid
        self._alive = alive

    def poll(self):
        return None if self._alive else 0


@pytest.fixture(autouse=True)
def clean_bridge_procs():
    """每个用例前清空存活表（就地清空，不重新绑定）。"""
    bridge_procs._procs.clear()
    yield
    bridge_procs._procs.clear()


def run(coro):
    return asyncio.run(coro)


def _dispatch(harness_id, kind="task", payload=None):
    from agent_community.platform.server import _dispatch_to_harness
    p = payload or {"type": kind, "workshop_id": "w1", "member_id": "m1"}
    return _dispatch_to_harness(harness_id, p, kind=kind)


# ── 队列分支三态 ────────────────────────────────────────────────
class TestQueueBranchThreeStates:
    def test_dead_bridge_does_not_enqueue(self, tmp_path):
        """矩阵 #3 核心：桥进程已退出 → False **且不入队**。"""
        from agent_community.platform.state import pending_tasks, pending_activations
        bridge_procs.record("h1", FakeProc(alive=False))

        before = (len(pending_tasks.get("h1", [])), len(pending_activations.get("h1", [])))
        ok, note = _dispatch("h1", kind="task")

        assert ok is False, f"桥已死应返回 False，实得 {ok}"
        assert "未入队" in note or "已退出" in note, f"note 应说明未入队，实得 {note!r}"
        after = (len(pending_tasks.get("h1", [])), len(pending_activations.get("h1", [])))
        assert after == before, f"**不得入队**：{before} -> {after}"

    def test_alive_bridge_enqueues(self):
        """桥活着 → 正常入队 True。"""
        from agent_community.platform.state import pending_tasks
        bridge_procs.record("h1", FakeProc(alive=True))
        ok, note = _dispatch("h1", kind="task")
        assert ok is True
        assert "在跑" in note, f"note 应说明桥在跑，实得 {note!r}"
        assert pending_tasks.get("h1"), "应入队"

    def test_unknown_bridge_enqueues_with_honest_note(self):
        """从未登记（用户手动起的桥）→ 允许入队，但如实标注存活未知。"""
        ok, note = _dispatch("h1", kind="task")
        assert ok is True, "未知不该误杀（平台不能假装知道）"
        assert "存活未知" in note, f"note 必须标注未知，实得 {note!r}"

    def test_dead_bridge_activation_not_enqueued(self):
        """activation 类型同样受门禁。"""
        from agent_community.platform.state import pending_activations
        bridge_procs.record("h1", FakeProc(alive=False))
        ok, _ = _dispatch("h1", kind="activation")
        assert ok is False
        assert not pending_activations.get("h1"), "activation 也不得入队"

    def test_status_lifecycle(self):
        """活着 -> 退出 -> 状态翻转，且 forget 后回到 unknown。"""
        p = FakeProc(alive=True)
        bridge_procs.record("h1", p)
        assert bridge_procs.status("h1") == "alive"
        assert bridge_procs.is_alive("h1") is True

        p._alive = False                      # 进程退出
        assert bridge_procs.status("h1") == "dead"
        assert bridge_procs.is_alive("h1") is False

        bridge_procs.forget("h1")
        assert bridge_procs.status("h1") == "unknown"

    def test_record_requires_proc(self):
        assert "error" in bridge_procs.record("h1", None)
        assert "error" in bridge_procs.record("", FakeProc())

    def test_prune_removes_dead(self):
        dead = FakeProc(alive=False)
        bridge_procs.record("h_dead", dead)
        bridge_procs.record("h_alive", FakeProc(alive=True))
        removed = bridge_procs.prune()
        assert removed == ["h_dead"]
        assert bridge_procs.status("h_alive") == "alive"


# ── 调用方消费返回值（防止假成功转移）────────────────────────────
class TestCallersConsumeReturnValue:
    def _mk_member(self):
        from agent_community.platform.workshop import Workshop, WorkshopMember
        return Workshop, WorkshopMember

    def test_activate_single_marks_blocked_on_dead_bridge(self):
        """矩阵 #3：桥不在场 → 成员标 blocked（原先裸调用，永远停在 activating）。"""
        from agent_community.platform.server import _activate_single
        from agent_community.platform.workshop import Workshop, WorkshopMember

        Workshop, _ = self._mk_member()
        ws = Workshop(workshop_id="w1", name="t", workspace_dir="D:\\ws\\w1",
                      hall_content="hall")
        m = WorkshopMember(member_id="m1", role="员工", display_name="测试",
                           harness_ids=["h_dead"])
        ws.members.append(m)

        # 该 harness 走队列分支（wakeup 默认非 http/file），且桥已死
        bridge_procs.record("h_dead", FakeProc(alive=False))
        _activate_single(ws, m)

        assert m.status == "blocked", f"桥死应标 blocked，实得 {m.status!r}"

    def test_activate_single_keeps_activating_when_alive(self):
        """桥活着 → 不误标 blocked（保持原有 activating 语义，由回报推进）。"""
        from agent_community.platform.server import _activate_single
        from agent_community.platform.workshop import Workshop, WorkshopMember

        ws = Workshop(workshop_id="w2", name="t", workspace_dir="D:\\ws\\w2",
                      hall_content="hall")
        m = WorkshopMember(member_id="m2", role="员工", display_name="测试",
                           harness_ids=["h_alive"])
        ws.members.append(m)

        bridge_procs.record("h_alive", FakeProc(alive=True))
        _activate_single(ws, m)
        assert m.status != "blocked", f"桥活不该标 blocked，实得 {m.status!r}"

    def test_leader_dispatch_failure_blocks_leader(self):
        """组长激活派发失败 → 组长标 blocked，且 reply 里说明失败（不再谎报"已向组长派发"）。"""
        from agent_community.platform import server as srv
        from agent_community.platform.workshop import Workshop, WorkshopMember

        ws = Workshop(workshop_id="w3", name="t", workspace_dir="D:\\ws\\w3",
                      hall_content="hall")
        leader = WorkshopMember(member_id="m3", role="组长", display_name="组",
                                harness_ids=["h_leader_dead"])
        ws.members.append(leader)

        bridge_procs.record("h_leader_dead", FakeProc(alive=False))
        reply, ok, ld = srv._activate_leader_if_needed(ws)

        assert ld is leader
        assert leader.status == "blocked", f"组长应标 blocked，实得 {leader.status!r}"
        assert "失败" in reply, f"reply 必须如实说明失败，实得 {reply[:80]!r}"
        assert "已向组长" not in reply, "派发失败时不得谎报『已向组长派发』"


# ── auto-connect 把 proc 登记进存活表 ────────────────────────────
class TestAutoConnectRecords:
    def test_record_integration(self):
        """桥进程登记后 status 可查（非阻塞、零 I/O）。"""
        p = FakeProc(alive=True, pid=777)
        out = bridge_procs.record("h_auto", p, cmd="python bridge.py")
        assert out["pid"] == 777
        assert bridge_procs.status("h_auto") == "alive"

        d = bridge_procs.describe()
        assert d["count"] == 1
        assert d["bridges"]["h_auto"]["status"] == "alive"
