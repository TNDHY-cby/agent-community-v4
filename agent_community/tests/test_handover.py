# -*- coding: utf-8 -*-
"""V-23 交接体系测试：证据栏闸门 + 送达回执协议（2026-10-07）。

设计来源 = 项目自有文档《防幻觉纪律与修复方案_20261007.md》§四：
  ① 任务书/回执模板加「证据栏」：每项完成声明必须附 commit hash / 文件路径 / 测试输出，无证据不回执；
  ③ 送达回执协议：消息必须等对方可读回复才可宣称送达。

覆盖：
  1. 模板：任务书/回执模板每项都带**空的证据栏**（commit/file/test）
  2. 无证据拒收（①）：缺证据 → 400 + 逐条 errors
  3. 送达协议（③）：delivered=True 无 reply_ref → 拒收；有 → 受理
  4. 合法回执受理并落台账，可查回
  5. 空 items / 缺 claim / 缺 task_id 的边界
  6. 台账 ring buffer 上限
  7. HTTP 面：三个端点的真实往返
"""
from __future__ import annotations

import asyncio
import json

import pytest

from agent_community.platform.handover import (
    CHECKPOINT_REQUIRED,
    COMPACT_THRESHOLD,
    EVIDENCE_KEYS,
    MAX_RECEIPTS,
    AgingTracker,
    HandoverLedger,
    aging_tracker,
    handover_ledger,
    template_receipt,
    template_task_order,
    validate_checkpoint,
    validate_receipt,
)


def run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _clean_ledger():
    handover_ledger.reset()
    aging_tracker.reset()
    yield
    handover_ledger.reset()
    aging_tracker.reset()


def _ok_receipt(**over):
    base = {
        "task_id": "t-1", "who": "DSH",
        "items": [{"claim": "做了 A", "evidence": {"commit": "76964df"}}],
        "delivery": {"delivered": False, "reply_ref": ""},
    }
    base.update(over)
    return base


class TestTemplates:
    def test_task_order_has_empty_evidence_column(self):
        t = template_task_order(task_id="t-1", title="测试", items=[{"claim": "x"}])
        assert t["kind"] == "task_order" and t["task_id"] == "t-1"
        ev = t["items"][0]["evidence"]
        assert set(ev.keys()) == set(EVIDENCE_KEYS), "每项都必须带完整的证据栏"
        assert all(v == "" for v in ev.values()), "模板里的证据栏必须是空的（由领取方填）"
        assert "evidence_hint" in t and "rule" in t

    def test_receipt_template_has_delivery_section(self):
        t = template_receipt(task_id="t-1", who="DSH")
        assert t["kind"] == "receipt"
        assert t["delivery"] == {"delivered": False, "reply_ref": "", "note": ""}
        assert "delivery_rule" in t


class TestEvidenceGate:
    def test_no_evidence_is_rejected(self):
        """① 的核心：**无证据不回执**。"""
        ok, errors, _ = validate_receipt(_ok_receipt(
            items=[{"claim": "我做了 A", "evidence": {"commit": "", "file": "", "test": ""}}]))
        assert not ok
        assert any("无证据" in e for e in errors), errors

    def test_each_of_the_three_evidence_kinds_passes(self):
        for k in EVIDENCE_KEYS:
            ok, errors, _ = validate_receipt(_ok_receipt(
                items=[{"claim": "做了 A", "evidence": {k: "something"}}]))
            assert ok, f"{k} 应当被接受，实得 {errors}"

    def test_partial_evidence_is_enough(self):
        ok, _, norm = validate_receipt(_ok_receipt(
            items=[{"claim": "做了 A", "evidence": {"file": "agent_community/platform/handover.py"}}]))
        assert ok and norm["items"][0]["evidence"]["file"].endswith("handover.py")

    def test_error_message_names_the_offending_item(self):
        ok, errors, _ = validate_receipt(_ok_receipt(items=[
            {"claim": "A", "evidence": {"commit": "abc"}},
            {"claim": "B", "evidence": {}},
        ]))
        assert not ok
        assert any("第 2 项" in e for e in errors), errors
        assert not any("第 1 项" in e for e in errors)


class TestBoundaries:
    def test_empty_items_rejected(self):
        ok, errors, _ = validate_receipt(_ok_receipt(items=[]))
        assert not ok and any("items" in e for e in errors)

    def test_missing_claim_rejected(self):
        ok, errors, _ = validate_receipt(_ok_receipt(
            items=[{"claim": "  ", "evidence": {"commit": "abc"}}]))
        assert not ok and any("claim" in e for e in errors)

    def test_missing_task_id_rejected(self):
        ok, errors, _ = validate_receipt(_ok_receipt(task_id=""))
        assert not ok and any("task_id" in e for e in errors)

    def test_garbage_payload_does_not_raise(self):
        for bad in (None, [], "x", 42, {}):
            ok, errors, _ = validate_receipt(bad)
            assert not ok and errors, f"{bad!r} 应当被拒但不抛异常"


class TestDeliveryProtocol:
    def test_delivered_without_reply_ref_is_rejected(self):
        """③ 的核心：**没有对方可读回复就不算送达**。"""
        ok, errors, _ = validate_receipt(_ok_receipt(delivery={"delivered": True, "reply_ref": ""}))
        assert not ok
        assert any("reply_ref" in e for e in errors), errors

    def test_delivered_with_reply_ref_passes(self):
        ok, _, norm = validate_receipt(_ok_receipt(
            delivery={"delivered": True, "reply_ref": "A2A_inbox/a095bec3.md"}))
        assert ok and norm["delivered"] is True and norm["reply_ref"].endswith("a095bec3.md")

    def test_not_delivered_needs_no_reply_ref(self):
        ok, _, norm = validate_receipt(_ok_receipt(delivery={"delivered": False}))
        assert ok and norm["delivered"] is False


class TestLedger:
    def test_accepted_receipt_is_queryable(self):
        ok, _, norm = validate_receipt(_ok_receipt())
        assert ok
        rec = handover_ledger.add(norm)
        assert rec["accepted"] is True and rec["task_id"] == "t-1"
        got = handover_ledger.query(limit=10, task_id="t-1")
        assert len(got) == 1 and got[0]["items"][0]["claim"] == "做了 A"

    def test_query_filters_by_task_id(self):
        for tid in ("a", "b"):
            _, _, n = validate_receipt(_ok_receipt(task_id=tid))
            handover_ledger.add(n)
        assert len(handover_ledger.query(task_id="a")) == 1
        assert len(handover_ledger.query()) == 2

    def test_ring_buffer_cap(self, tmp_path):
        # ⚠️ 必须显式传 data_dir=tmp_path：局部构造的台账不会走 conftest 对**单例**的隔离，
        #    用默认目录会直接把测试数据写进真实 data/，触发会话级指纹守卫报红（2026-10-07 实测）。
        led = HandoverLedger(data_dir=tmp_path, max_records=3)
        for i in range(5):
            _, _, n = validate_receipt(_ok_receipt(task_id=f"t{i}"))
            led.add(n)
        assert len(led.records) == 3
        assert [r["task_id"] for r in led.records] == ["t2", "t3", "t4"]
        assert (tmp_path / "handover_receipts.json").exists(), "应落在传入的目录里"


class TestRouter:
    def test_template_endpoint(self):
        from agent_community.platform.routers.handover import handover_template
        out = run(handover_template(kind="task_order", task_id="ws-1", title="T"))
        assert out["success"] and out["template"]["kind"] == "task_order"
        out2 = run(handover_template(kind="receipt", task_id="ws-1"))
        assert out2["template"]["kind"] == "receipt"

    def test_receipt_endpoint_rejects_without_evidence(self):
        from agent_community.platform.routers.handover import handover_receipt

        class _Req:
            def __init__(self, body): self._b = body
            async def json(self): return self._b

        resp = run(handover_receipt(_Req(_ok_receipt(items=[{"claim": "x", "evidence": {}}]))))
        assert resp.status_code == 400
        payload = json.loads(resp.body.decode("utf-8"))
        assert payload["error"] == "回执不合规" and payload["errors"]

    def test_receipt_endpoint_accepts_valid(self):
        from agent_community.platform.routers.handover import handover_receipt, handover_receipts

        class _Req:
            def __init__(self, body): self._b = body
            async def json(self): return self._b

        out = run(handover_receipt(_Req(_ok_receipt())))
        assert out["success"] and out["accepted"] is True
        got = run(handover_receipts(limit="5", task_id="t-1"))
        assert got["receipts"] and got["receipts"][0]["task_id"] == "t-1"


# ══════════════════════════════════════════════════════════════
# V-24 上下文老化 + 交接单（D7）
# 借鉴：OpenAI Agents SDK 的 memory consolidation turns / durable checkpointing、
#       Google ADK 的 resume 重跑语义、CrewAI 的 RuntimeState 序列化口径。
# ══════════════════════════════════════════════════════════════

class TestTemplateRoute:
    """MiMo 2026-10-07 实测报回的两条，这里钉死防回归。"""

    def test_receipt_template_honours_title(self):
        from agent_community.platform.routers.handover import handover_template
        out = run(handover_template(kind="receipt", task_id="t-1", title="交付回执台"))
        assert out["template"]["title"] == "交付回执台", "kind=receipt 曾漏传 title（回显空串）"

    def test_task_order_template_honours_title(self):
        from agent_community.platform.routers.handover import handover_template
        out = run(handover_template(kind="task_order", task_id="t-1", title="X"))
        assert out["template"]["title"] == "X"

    def test_receipt_record_carries_both_shapes(self):
        """提交体是嵌套 delivery；列表曾只有拍平字段 —— 现在两条形状都给。"""
        ok, _, norm = validate_receipt(_ok_receipt(
            delivery={"delivered": True, "reply_ref": "chan/6.md", "note": "n"}))
        assert ok
        assert norm["delivered"] is True and norm["reply_ref"] == "chan/6.md"      # 拍平（旧）
        assert norm["delivery"] == {"delivered": True, "reply_ref": "chan/6.md",    # 嵌套（新）
                                    "note": "n"}
class TestAgingGate:
    def test_below_threshold_still_allowed(self, tmp_path):
        t = AgingTracker(data_dir=tmp_path)
        for _ in range(COMPACT_THRESHOLD - 1):
            t.note_compaction("a")
        ok, reason, _ = t.can_open_new_session("a")
        assert ok and "未到阈值" in reason

    def test_at_threshold_blocks_new_session(self, tmp_path):
        t = AgingTracker(data_dir=tmp_path)
        for _ in range(COMPACT_THRESHOLD):
            r = t.note_compaction("a")
        assert r["needs_checkpoint"] is True
        ok, reason, _ = t.can_open_new_session("a")
        assert not ok and "未交交接单" in reason

    def test_checkpoint_opens_the_gate_and_resets(self, tmp_path):
        t = AgingTracker(data_dir=tmp_path)
        for _ in range(COMPACT_THRESHOLD + 1):
            t.note_compaction("a")
        assert t.can_open_new_session("a")[0] is False
        t.note_checkpoint("a")
        ok, reason, rec = t.can_open_new_session("a")
        assert ok and rec["compactions"] == 0, "交完交接单应归零、开启新一轮"

    def test_compaction_needs_session_key(self, tmp_path):
        t = AgingTracker(data_dir=tmp_path)
        assert t.note_compaction("")["ok"] is False

    def test_sessions_are_independent(self, tmp_path):
        t = AgingTracker(data_dir=tmp_path)
        for _ in range(COMPACT_THRESHOLD):
            t.note_compaction("a")
        assert t.can_open_new_session("a")[0] is False
        assert t.can_open_new_session("b")[0] is True, "别的会话不该被牵连"


class TestCheckpoint:
    def _good(self, **over):
        d = {"agent_id": "harness-x", "wake_word": "续作-合作社",
             "workspace_dir": r"D:\ws\ws_1", "plan": "做 A/B",
             "resume_semantics": "A 已完成不重跑；B 必须重跑",
             "done": ["A"], "todo": ["B"], "session_key": "harness-x"}
        d.update(over)
        return d

    def test_valid_checkpoint_passes(self):
        ok, errors, norm = validate_checkpoint(self._good())
        assert ok and not errors
        assert norm["wake_word"] and norm["done"] == ["A"] and norm["todo"] == ["B"]

    def test_all_required_fields_reported_when_missing(self):
        ok, errors, _ = validate_checkpoint({})
        assert not ok
        for k in CHECKPOINT_REQUIRED:
            assert any(k in e for e in errors), f"{k} 缺失未被报出: {errors}"

    def test_resume_semantics_is_required(self):
        """借 Google ADK 的教训：恢复语义不写清，恢复就不可靠。"""
        ok, errors, _ = validate_checkpoint(self._good(resume_semantics="  "))
        assert not ok and any("resume_semantics" in e for e in errors)

    def test_done_and_todo_cannot_both_be_empty(self):
        ok, errors, _ = validate_checkpoint(self._good(done=[], todo=[]))
        assert not ok and any("done" in e and "todo" in e for e in errors)

    def test_garbage_does_not_raise(self):
        for bad in (None, [], "x", 7):
            ok, errors, _ = validate_checkpoint(bad)
            assert not ok and errors


class TestCheckpointEndpoints:
    def test_compaction_then_blocked_then_checkpoint_unblocks(self):
        from agent_community.platform.routers.handover import (
            handover_aging, handover_checkpoint, handover_compaction,
        )

        class _Req:
            def __init__(self, body): self._b = body
            async def json(self): return self._b

        for _ in range(COMPACT_THRESHOLD):
            out = run(handover_compaction(_Req({"session_key": "s1"})))
            assert out["success"]
        assert out["needs_checkpoint"] is True
        ag = run(handover_aging(session_key="s1"))
        assert ag["can_open_new_session"] is False
        good = {"agent_id": "a", "wake_word": "w", "workspace_dir": r"D:\x",
                "plan": "p", "resume_semantics": "r", "done": ["d"], "todo": [],
                "session_key": "s1"}
        cp = run(handover_checkpoint(_Req(good)))
        assert cp["success"] and cp["accepted"] is True
        assert run(handover_aging(session_key="s1"))["can_open_new_session"] is True

    def test_checkpoint_endpoint_rejects_incomplete(self):
        from agent_community.platform.routers.handover import handover_checkpoint

        class _Req:
            def __init__(self, body): self._b = body
            async def json(self): return self._b

        resp = run(handover_checkpoint(_Req({"agent_id": "a"})))
        assert resp.status_code == 400
        payload = json.loads(resp.body.decode("utf-8"))
        assert payload["error"] == "交接单不合规" and payload["errors"]


# ══════════════════════════════════════════════════════════════
# V-24 执行端：交接单驱动的新会话拉起（两道闸门互锁）
# ══════════════════════════════════════════════════════════════

CP = {"agent_id": "harness-x", "wake_word": "续作-合作社",
      "workspace_dir": r"D:\ws\ws_1", "plan": "做 A/B",
      "resume_semantics": "A 已完成不重跑；B 必须重跑",
      "done": ["A"], "todo": ["B"], "session_key": "harness-x"}


class _Req:
    def __init__(self, body): self._b = body
    async def json(self): return self._b


class TestResumePrompt:
    def test_prompt_carries_every_checkpoint_field(self):
        from agent_community.platform.handover import build_resume_prompt
        p = build_resume_prompt(CP)
        for needle in ("续作-合作社", r"D:\ws\ws_1", "做 A/B", "A", "B", "A 已完成不重跑"):
            assert needle in p, f"续作提示词缺少 {needle!r}"
        assert "不要重跑" in p and "待办" in p

    def test_prompt_tolerates_empty_fields(self):
        from agent_community.platform.handover import build_resume_prompt
        p = build_resume_prompt({})
        assert "（未提供）" in p and "（无）" in p, "空字段必须有明确的占位，不能让 agent 看到空洞"


class TestActivateGate:
    def _patch_dispatch(self, monkeypatch, ok=True):
        """假函数必须与真函数**同形**：`_dispatch_to_harness` 是同步的、返回 (ok, note)。

        ⚠️ 第一版我把它写成 `async def`，于是单测全绿而线上 500
        （`object tuple can't be used in 'await' expression`）。**假函数的形状错误会让测试
        替真代码背书**——这是本项目"看起来通过"的典型来源，记在这里当反例。
        """
        calls = []
        def _fake(hid, payload, kind=""):
            calls.append({"hid": hid, "payload": payload, "kind": kind})
            return ok, "mocked dispatch"
        import agent_community.platform.server as srv
        monkeypatch.setattr(srv, "_dispatch_to_harness", _fake, raising=False)
        return calls

    def test_missing_harness_id(self):
        from agent_community.platform.routers.handover import handover_activate
        resp = run(handover_activate(_Req({})))
        assert resp.status_code == 400

    def test_gate_closed_without_checkpoint_returns_409(self, tmp_path):
        """核心互锁：压缩到阈值却**没交交接单** → 不许开新会话。"""
        from agent_community.platform.routers.handover import handover_activate
        for _ in range(COMPACT_THRESHOLD):
            aging_tracker.note_compaction("harness-x")
        resp = run(handover_activate(_Req({"harness_id": "harness-x"})))
        assert resp.status_code == 409
        payload = json.loads(resp.body.decode("utf-8"))
        assert payload["error"] == "老化闸门未放行" and "未交交接单" in payload["reason"]

    def test_no_checkpoint_but_gate_open_returns_400(self):
        from agent_community.platform.routers.handover import handover_activate
        resp = run(handover_activate(_Req({"harness_id": "harness-y"})))
        assert resp.status_code == 400
        payload = json.loads(resp.body.decode("utf-8"))
        assert "没有交接单" in payload["error"]

    def test_dispatch_happens_after_checkpoint(self, monkeypatch):
        from agent_community.platform.routers.handover import handover_activate
        calls = self._patch_dispatch(monkeypatch)
        handover_ledger.add({"kind": "checkpoint", **validate_checkpoint(CP)[2]})
        out = run(handover_activate(_Req({"harness_id": "harness-x"})))
        assert out["success"] and out["dispatched"] is True
        assert out["wake_word"] == "续作-合作社"
        assert len(calls) == 1 and calls[0]["hid"] == "harness-x"
        assert calls[0]["kind"] == "activation"
        assert "续作" in calls[0]["payload"]["activation_prompt"]
        assert calls[0]["payload"]["wake_word"] == "续作-合作社"

    def test_dispatch_failure_is_reported_not_raised(self, monkeypatch):
        from agent_community.platform.routers.handover import handover_activate
        self._patch_dispatch(monkeypatch, ok=False)
        handover_ledger.add({"kind": "checkpoint", **validate_checkpoint(CP)[2]})
        out = run(handover_activate(_Req({"harness_id": "harness-x"})))
        assert out["success"] is False and out["dispatched"] is False and out["note"]

    def test_activation_is_audited(self, monkeypatch):
        from agent_community.platform.audit import audit_log
        from agent_community.platform.routers.handover import handover_activate
        self._patch_dispatch(monkeypatch)
        handover_ledger.add({"kind": "checkpoint", **validate_checkpoint(CP)[2]})
        run(handover_activate(_Req({"harness_id": "harness-x"})))
        evs = audit_log.query(limit=20, event="handover.activated", target="harness-x")
        assert evs and evs[0]["wake_word"] == "续作-合作社"
