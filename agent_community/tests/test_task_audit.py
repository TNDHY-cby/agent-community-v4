# -*- coding: utf-8 -*-
"""V-20 工单化审计埋点测试 —— 47 号任务书验收项 #1。

要求：一次完整任务生命周期（创建→执行→完成）在**审计落盘**里留下 ≥3 条迁移记录。

覆盖：
  1. 完整生命周期 ≥3 条 `task.transition`，且真的落盘（读回 audit.json 逐条核对）
  2. actor 取值优先级：member_id > by > system
  3. 构造注入 auditor 时走注入出口、**不碰**真实 audit_log
  4. 审计出口抛异常不影响状态迁移（不阻塞主流程）
  5. remove 也留痕；重复 remove 不重复留痕
  6. audit_log.query 支持按 target（工单号）过滤
  7. GET /api/audit 的 target 参数接通

注：用 `asyncio.run()` 与本项目其余测试同范式。
"""
from __future__ import annotations

import asyncio
import json
import re

import pytest

from agent_community.platform.audit import audit_log
from agent_community.platform.task_state_machine import (
    ASSIGNED,
    CREATED,
    DONE,
    EV_COMPLETE,
    EV_DROP,
    EV_REPORT,
    EV_STUCK,
    EXECUTING,
    WAITING_REPLY,
    TaskStateMachine,
)


def run(coro):
    return asyncio.run(coro)


# ══════════════════════════════════════════════════════════════
# 一、主验收：完整生命周期留下可追溯台账
# ══════════════════════════════════════════════════════════════

class TestLifecycleAudit:
    def test_full_lifecycle_records_at_least_three_transitions(self):
        """★ 47 号验收项 #1：created→assigned→executing→done 全程可查。"""
        ws = "ws-47-lifecycle"
        sm = TaskStateMachine()
        sm.ensure(ws, initial=CREATED)
        sm.set_state(ws, ASSIGNED, stage="division")
        sm.set_state(ws, EXECUTING, stage="running")
        sm.on_event(ws, EV_REPORT, {"member_id": "worker-1"})
        sm.on_event(ws, EV_COMPLETE, {"by": "user"})

        evs = audit_log.query(limit=50, event="task.transition", target=ws)
        assert len(evs) >= 3, f"应 ≥3 条迁移审计，实际 {len(evs)} 条"

        # 时间倒序：最新一条是 complete，最老一条是最初那次 set_state
        details = [e["detail"] for e in evs]
        assert any("created→assigned" in d for d in details)
        assert any("executing→waiting_reply" in d for d in details)
        assert any("→done" in d for d in details)
        assert all(e["actor"] for e in evs), "每条都要有 actor"

    def test_transitions_are_really_persisted_to_disk(self):
        """审计不能只停在内存 —— 读回落盘文件逐条核对（这是「落盘」二字的证据）。"""
        ws = "ws-47-persist"
        sm = TaskStateMachine()
        sm.set_state(ws, ASSIGNED)
        sm.set_state(ws, EXECUTING)
        sm.set_state(ws, DONE)

        path = audit_log._path
        assert path.exists(), "audit.json 未落盘"
        on_disk = json.loads(path.read_text(encoding="utf-8"))
        hits = [e for e in on_disk
                if e.get("event") == "task.transition" and e.get("target") == ws]
        assert len(hits) >= 3, f"落盘文件里只有 {len(hits)} 条"

    def test_remove_leaves_a_trace_and_does_not_repeat(self):
        seen: list[list[str]] = []
        sm = TaskStateMachine(auditor=lambda a, t, d, e: seen.append([a, t, d]))
        sm.set_state("ws-rm", EXECUTING)
        assert sm.remove("ws-rm") is True
        assert seen[-1][1] == "ws-rm" and "removed" in seen[-1][2]
        assert sm.remove("ws-rm") is False
        assert len(seen) == 2, "第二次 remove 未发生迁移，不该再留痕"


# ══════════════════════════════════════════════════════════════
# 二、actor 口径
# ══════════════════════════════════════════════════════════════

class TestActorResolution:
    def test_actor_priority_member_id_then_by_then_system(self):
        seen: list[str] = []
        sm = TaskStateMachine(auditor=lambda a, t, d, e: seen.append(a))
        sm.on_event("ws-1", EV_STUCK, {"member_id": "m-9", "by": "leader"})
        sm.on_event("ws-2", EV_STUCK, {"by": "leader"})
        sm.on_event("ws-3", EV_STUCK)
        assert seen == ["m-9", "leader", "system"]


# ══════════════════════════════════════════════════════════════
# 三、不阻塞 / 可注入（本模块「不 import 主模块全局单例」原则的守卫）
# ══════════════════════════════════════════════════════════════

class TestSinkInjectionAndSafety:
    def test_injected_auditor_skips_real_audit_log(self):
        before = len(audit_log.events)
        seen: list[tuple] = []
        sm = TaskStateMachine(auditor=lambda a, t, d, e: seen.append((a, t, d, e)))
        sm.set_state("ws-inj", ASSIGNED)
        assert len(audit_log.events) == before, "注入 auditor 时不该再写真实 audit_log"
        assert seen and seen[0][1] == "ws-inj"

    def test_module_level_sink_is_used_when_no_constructor_auditor(self):
        from agent_community.platform import task_state_machine as tsm

        seen: list[str] = []
        tsm.set_audit_sink(lambda a, t, d, e: seen.append(t))
        try:
            tsm.TaskStateMachine().set_state("ws-global", ASSIGNED)
        finally:
            tsm.set_audit_sink(None)
        assert seen == ["ws-global"]

    def test_auditor_exception_never_breaks_the_transition(self):
        """审计炸了，状态迁移必须照常完成（失败不炸原则）。"""
        def boom(actor, target, detail, extra):
            raise RuntimeError("审计出口故意炸")

        sm = TaskStateMachine(auditor=boom)
        out = sm.set_state("ws-safe", EXECUTING)
        assert out["state"] == EXECUTING
        assert sm.get_state("ws-safe") == EXECUTING
        assert sm.on_event("ws-safe", EV_COMPLETE, {"by": "user"})["state"] == DONE

    def test_on_event_still_returns_when_audit_sink_is_missing(self):
        """sink 解析不出来也不影响 on_event 的返回值契约。"""
        from agent_community.platform import task_state_machine as tsm

        tsm.set_audit_sink(None)
        sm = TaskStateMachine()
        sm.set_state("ws-nosink", EXECUTING)
        assert sm.on_event("ws-nosink", EV_REPORT, {})["state"] == WAITING_REPLY


# ══════════════════════════════════════════════════════════════
# 四、工单化查询：按工单号检索
# ══════════════════════════════════════════════════════════════

class TestTaskScopedQuery:
    def test_query_filters_by_target(self):
        sm = TaskStateMachine()
        sm.set_state("ws-A", ASSIGNED)
        sm.set_state("ws-B", ASSIGNED)
        hits = audit_log.query(limit=50, event="task.transition", target="ws-A")
        assert hits, "按 target 应能查到 ws-A 的迁移"
        assert all(e["target"] == "ws-A" for e in hits)

    def test_query_without_target_keeps_old_behaviour(self):
        sm = TaskStateMachine()
        sm.set_state("ws-C", ASSIGNED)
        hits = audit_log.query(limit=50, event="task.transition")
        assert any(e["target"] == "ws-C" for e in hits)

    def test_audit_router_accepts_target_param(self):
        from agent_community.platform.routers.audit import audit_query

        sm = TaskStateMachine()
        sm.set_state("ws-R", ASSIGNED)
        out = run(audit_query(limit="50", event="task.transition", target="ws-R"))
        assert out["events"] and all(e["target"] == "ws-R" for e in out["events"])


# ══════════════════════════════════════════════════════════════
# 五、V-20.1：结构化明细字段 + total（FE-01 接口变更申请 #1 / #2）
# ══════════════════════════════════════════════════════════════

RE_DETAIL = re.compile(r"^(\S+): (\S+)→(\S+) action=(\S+) note=(.*)$")


def _parse_detail(detail: str) -> dict:
    """冻结正则的 Python 版（与前端任务书 §4 同一格式），用于交叉校验结构化字段。"""
    m = RE_DETAIL.match(detail)
    assert m, f"detail 不符合冻结格式: {detail!r}"
    return {"event_name": m.group(1), "from": m.group(2), "to": m.group(3),
            "action": m.group(4), "note": m.group(5)}


class TestStructuredDetail:
    def test_transition_event_carries_structured_fields(self):
        sm = TaskStateMachine()
        sm.set_state("ws-s1", ASSIGNED)
        ev = audit_log.query(limit=5, event="task.transition", target="ws-s1")[0]
        assert ev["from"] == "created" and ev["to"] == "assigned"
        assert ev["action"] == "noop" and ev["event_name"] == "set_state"
        assert ev["note"] == ""

    def test_structured_fields_match_frozen_detail_format(self):
        """**交叉校验**：结构化字段必须与 detail 用冻结正则解析出的结果逐字一致。"""
        sm = TaskStateMachine()
        ws = "ws-47-x"
        sm.set_state(ws, ASSIGNED)          # set_state 样本
        sm.on_event(ws, EV_DROP)            # drop 样本
        sm.remove(ws)                       # remove 样本
        evs = audit_log.query(limit=10, event="task.transition", target=ws)
        assert len(evs) == 3, f"应恰好 3 条，实得 {len(evs)}"
        for ev in evs:
            parsed = _parse_detail(ev["detail"])
            for k, v in parsed.items():
                assert ev[k] == v, f"{k}: 结构化={ev[k]!r} 与 detail 解析={v!r} 不一致"

    def test_auditor_receives_structured_extra_as_fourth_arg(self):
        seen: list[dict] = []
        sm = TaskStateMachine(auditor=lambda a, t, d, e: seen.append(e))
        sm.set_state("ws-x", EXECUTING)
        assert seen and isinstance(seen[0], dict)
        assert seen[0] == {"event_name": "set_state", "from": "created",
                           "to": "executing", "action": "noop", "note": ""}

    def test_extra_cannot_overwrite_identity_fields(self):
        ev = audit_log.record(
            "task.transition", actor="real", target="real-t", detail="real-d",
            extra={"ts": "HACK", "event": "HACK", "actor": "HACK", "target": "HACK",
                   "detail": "HACK", "custom": 1},
        )
        assert ev["actor"] == "real" and ev["target"] == "real-t" and ev["detail"] == "real-d"
        assert ev["event"] == "task.transition" and ev["ts"] != "HACK"
        assert ev["custom"] == 1, "非身份字段应当被合入"

    def test_record_without_extra_is_byte_identical_to_before(self):
        """向后兼容硬断言：不传 extra 时，事件**只有原来的 5 个键**。"""
        ev = audit_log.record("workshop.create", actor="user", target="w1", detail="d")
        assert list(ev.keys()) == ["ts", "event", "actor", "target", "detail"]

    def test_old_style_records_still_queryable(self):
        """老记录没有结构化字段 —— 前端必须能同时处理两种形态，后端保证老记录仍可查。"""
        audit_log.events.append({"ts": "2026-10-07T00:00:00", "event": "task.transition",
                                 "actor": "user", "target": "ws-old", "detail": "drop: created→dropped action=drop note="})
        hits = audit_log.query(limit=5, event="task.transition", target="ws-old")
        assert len(hits) == 1
        assert "from" not in hits[0], "老记录不该凭空长出结构化字段"


class TestAuditTotal:
    def test_query_page_total_is_pre_truncation(self):
        sm = TaskStateMachine()
        for i in range(5):
            sm.set_state(f"ws-n{i}", ASSIGNED)
        page, total = audit_log.query_page(limit=2, event="task.transition")
        assert len(page) == 2, "当页应受 limit 限制"
        assert total == 5, f"total 必须是截断前的命中数，实得 {total}"

    def test_total_respects_filters(self):
        sm = TaskStateMachine()
        sm.set_state("ws-f1", ASSIGNED)
        sm.set_state("ws-f2", ASSIGNED)
        _, t_all = audit_log.query_page(limit=100, event="task.transition")
        _, t_one = audit_log.query_page(limit=100, event="task.transition", target="ws-f1")
        assert t_all >= 2 and t_one == 1

    def test_router_returns_total_alongside_events(self):
        from agent_community.platform.routers.audit import audit_query

        sm = TaskStateMachine()
        for i in range(3):
            sm.set_state(f"ws-t{i}", ASSIGNED)
        out = run(audit_query(limit="2", event="task.transition"))
        assert set(out.keys()) == {"events", "total"}, "顶层键应为 events + total"
        assert len(out["events"]) == 2 and out["total"] == 3

    def test_query_and_query_page_agree_on_events(self):
        """两者口径必须一致（_filtered 共用），否则前端拿 total 与列表对不上。"""
        sm = TaskStateMachine()
        sm.set_state("ws-agg", ASSIGNED)
        a = audit_log.query(limit=5, event="task.transition", target="ws-agg")
        b, _ = audit_log.query_page(limit=5, event="task.transition", target="ws-agg")
        assert a == b
