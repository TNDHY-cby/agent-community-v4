# -*- coding: utf-8 -*-
"""V-15 步骤3 测试：成员进入状态机 + 按唤醒方式的最高可达。

设计稿 §十四 步骤3 的验证要求是「**单测四档各一**」—— 本文件据此分两组：
  A. 四档最高可达（ACP/HTTP_API/FILE_POLL/CLIPBOARD）+ 保守未知档
  B. 状态转移合法性（主路径逐级 / 阻塞可回 / 已进入不回退）
  C. 接线（写进 member 并出现在 API 返回体里）

背景教训：CLIPBOARD 原来只写到 pending，而派活要求全部 entered ——
**该类型整条链路走不通**。所以「四档各一」不是走过场，是验证每档能跑到最终动作。
"""
from __future__ import annotations

import asyncio
import json

import pytest

from agent_community.platform import entry_state as es
from agent_community.platform.workshop import Workshop, WorkshopMember


# ── A. 四档最高可达 ──────────────────────────────────────────────
class TestFourTiersMaxReachable:
    """§11 核心表：每档声明可达的最高状态。"""

    def test_acp_reaches_entered(self):
        assert es.max_reachable("acp") == es.ENTERED

    def test_http_api_reaches_acked_only(self):
        """HTTP 拿到 200 只能到 acked —— entered 要靠 harness 自述。"""
        assert es.max_reachable("http_api") == es.ACKED
        assert es.can_reach("http_api", es.ACKED) is True
        assert es.can_reach("http_api", es.ENTERED) is False, \
            "http_api 不该被宣告能到 entered（它自己不认就不算）"

    def test_file_poll_reaches_dispatched_only(self):
        """文件写成功即到头 —— 后面全靠 harness 侧。"""
        assert es.max_reachable("file_poll") == es.DISPATCHED

    def test_clipboard_reaches_entered_via_human(self):
        """CLIPBOARD 可达 entered，**靠人工点击**（§11.1）—— 不是停在 pending。"""
        assert es.max_reachable("clipboard") == es.ENTERED, \
            "CLIPBOARD 必须可达 entered，否则整条链路走不通（原稿的缺陷）"

    def test_unknown_type_is_conservative(self):
        """未知类型保守到 dispatched，绝不谎报 entered。"""
        assert es.max_reachable("whatever") == es.DISPATCHED
        assert es.max_reachable("") == es.PENDING

    def test_http_alias(self):
        """http 与 http_api 同档。"""
        assert es.max_reachable("http") == es.ACKED

    def test_describe_table_complete(self):
        d = es.describe()
        for m in ("acp", "http_api", "http", "file_poll", "clipboard", "unknown"):
            assert m in d["max_reachable"]
        assert list(es.STATUSES) == d["statuses"], "describe() 应返回与 STATUSES 一致的列表"


# ── B. 状态转移合法性 ────────────────────────────────────────────
class TestTransitions:
    def test_main_path_steps_forward(self):
        for a, b in zip(es.MAIN_PATH, es.MAIN_PATH[1:]):
            ok, why = es.can_transition(a, b)
            assert ok, f"主路径 {a}->{b} 应合法，实得 {why}"

    def test_main_path_cannot_skip(self):
        ok, why = es.can_transition(es.PENDING, es.ENTERED)
        assert ok is False, "不得跳级"
        assert "逐级" in why or "主路径" in why

    def test_blocked_from_any_stage(self):
        for s in es.MAIN_PATH:
            ok, _ = es.can_transition(s, es.BLOCKED)
            assert ok, f"{s} -> blocked 应合法（任何阶段都可能失败）"

    def test_no_retreat_after_entered(self):
        ok, why = es.can_transition(es.ENTERED, es.DISPATCHED)
        assert ok is False
        assert "不得回退" in why

    def test_blocked_can_resume_to_pending(self):
        ok, _ = es.can_transition(es.BLOCKED, es.PENDING)
        assert ok, "人工恢复后应回到 pending"

    def test_timeout_to_needs_human(self):
        ok, _ = es.can_transition(es.TIMEOUT, es.NEEDS_HUMAN)
        assert ok

    def test_unknown_state_rejected(self):
        ok, why = es.can_transition("actve", es.ENTERED)   # 拼写错误
        assert ok is False
        assert "未知状态" in why

    def test_advance_respects_max_reachable(self):
        """advance 不得越过该档最高可达。"""
        assert es.advance(es.PENDING, "file_poll") == es.DISPATCHED
        # file_poll 到 dispatched 后不再前进
        assert es.advance(es.DISPATCHED, "file_poll") == es.DISPATCHED
        # acp 可继续
        assert es.advance(es.DISPATCHED, "acp") == es.ACKED
        assert es.advance(es.ACKED, "acp") == es.ENTERED

    def test_advance_from_blocked_is_noop(self):
        """阻塞态不自动推进（需人工/重派）。"""
        assert es.advance(es.BLOCKED, "acp") == es.BLOCKED

    def test_labels_cover_all(self):
        """前端文案：每个状态都要有中文标签，否则显示成英文原值。"""
        for s in es.STATUSES:
            lab = es.entry_label(s)
            assert lab and lab != s, f"{s} 缺中文标签"


# ── C. 接线：写进 member 并出现在 API 返回体 ────────────────────
def _mk_member(mid="m1", hids=("h_dead",)):
    return WorkshopMember(member_id=mid, role="员工", display_name="测试",
                          harness_ids=list(hids))


class TestWiring:
    def test_activate_single_marks_blocked_with_reason(self):
        """桥死 -> entry_state=blocked 且带**具体原因**（不是笼统的"失败"）。"""
        from agent_community.platform import bridge_procs, server as srv
        bridge_procs._procs.clear()
        bridge_procs.record("h_dead", _DeadProc())

        ws = Workshop(workshop_id="w1", name="t", workspace_dir="D:\\ws\\w1",
                      hall_content="hall")
        m = _mk_member()
        ws.members.append(m)
        srv._activate_single(ws, m)

        assert m.status == "blocked"
        assert m.entry_state == es.BLOCKED
        assert m.entry_reason, "受阻必须给出原因（前端要显示）"
        assert "已退出" in m.entry_reason or "桥" in m.entry_reason

    def test_activate_single_dispatched_when_alive(self):
        """桥活 -> entry_state 推进到 dispatched（不是停在 pending）。"""
        from agent_community.platform import bridge_procs, server as srv
        bridge_procs._procs.clear()
        bridge_procs.record("h_alive", _AliveProc())

        ws = Workshop(workshop_id="w2", name="t", workspace_dir="D:\\ws\\w2",
                      hall_content="hall")
        m = _mk_member(mid="m2", hids=("h_alive",))
        ws.members.append(m)
        srv._activate_single(ws, m)

        assert m.entry_state == es.DISPATCHED, \
            f"派发成功应推进到 dispatched，实得 {m.entry_state}"
        assert m.entry_reason == ""

    def test_no_harness_blocked(self):
        """未指定 harness -> blocked + 原因。"""
        from agent_community.platform import server as srv
        ws = Workshop(workshop_id="w3", name="t", workspace_dir="D:\\ws\\w3",
                      hall_content="hall")
        m = WorkshopMember(member_id="m3", role="员工", display_name="x", harness_ids=[])
        ws.members.append(m)
        srv._activate_single(ws, m)
        assert m.entry_state == es.BLOCKED
        assert m.entry_reason == es.REASON_NO_HARNESS


class _DeadProc:
    pid = 1

    def poll(self):
        return 1


class _AliveProc:
    pid = 2

    def poll(self):
        return None


# ── D. 回执推进到 entered + API 带出字段 ─────────────────────────
class TestReceiptAndApi:
    def _fake_request(self, body):
        class R:
            headers = {}

            async def json(self):
                return body
        return R()

    def test_activation_result_entered(self):
        """回报 entered -> entry_state=entered（终点）。"""
        from agent_community.platform.routers import harness_register as hr
        from agent_community.platform.state import workshops as _ws

        ws = Workshop(workshop_id="w_rx", name="t", workspace_dir="D:\\ws\\rx",
                      hall_content="hall")
        m = _mk_member(mid="m_rx")
        m.entry_state = es.ACKED
        ws.members.append(m)
        _ws[ws.workshop_id] = ws
        try:
            out = asyncio.run(hr.harness_activation_result(self._fake_request({
                "workshop_id": "w_rx", "member_id": "m_rx", "status": "entered",
            })))
            assert out["success"] is True
            assert m.entry_state == es.ENTERED, f"实得 {m.entry_state}"
            assert m.entry_reason == ""
        finally:
            _ws.pop(ws.workshop_id, None)

    def test_activation_result_blocked_with_reason(self):
        """回报 blocked + reason -> 记进 entry_reason。"""
        from agent_community.platform.routers import harness_register as hr
        from agent_community.platform.state import workshops as _ws

        ws = Workshop(workshop_id="w_rb", name="t", workspace_dir="D:\\ws\\rb",
                      hall_content="hall")
        m = _mk_member(mid="m_rb")
        m.entry_state = es.DISPATCHED
        ws.members.append(m)
        _ws[ws.workshop_id] = ws
        try:
            asyncio.run(hr.harness_activation_result(self._fake_request({
                "workshop_id": "w_rb", "member_id": "m_rb", "status": "blocked",
                "reason": "会话创建失败",
            })))
            assert m.entry_state == es.BLOCKED
            assert "会话创建失败" in m.entry_reason
        finally:
            _ws.pop(ws.workshop_id, None)

    def test_api_returns_new_fields(self):
        """GET /api/workshop/{id} 必须**显式带出** entry_state/entry_reason/wakeup_method。"""
        from agent_community.platform.routers import workshops_lifecycle as wl
        from agent_community.platform.state import workshops as _ws

        ws = Workshop(workshop_id="w_api", name="t", workspace_dir="D:\\ws\\api",
                      hall_content="hall")
        m = _mk_member(mid="m_api")
        m.entry_state = es.BLOCKED
        m.entry_reason = es.REASON_NO_BRIDGE
        ws.members.append(m)
        _ws[ws.workshop_id] = ws
        try:
            out = asyncio.run(wl.get_workshop(ws.workshop_id))
            members = out["members"]
            assert len(members) == 1
            mm = members[0]
            assert mm.get("entry_state") == es.BLOCKED, \
                f"API 未带出 entry_state，实得 {mm}"
            assert mm.get("entry_reason") == es.REASON_NO_BRIDGE
            assert "wakeup_method" in mm, "前端要按档显示最高可达，必须带唤醒方式"
        finally:
            _ws.pop(ws.workshop_id, None)
