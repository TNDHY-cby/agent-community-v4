"""老板口述②「员工掉线」抢救链测试（V23 §2）。

链条：员工掉线 → 通知【组长】 → 组长去检查 → 能调起就用【鼠标控制调起】
→ **多次仍调不起/无法调起** → 通知【用户】。

本套测试钉死四件事：
  1. **先就地抢救、失败才上报人**（不是一掉线就叫人，也不是"只标记"）；
  2. **组长只通知一次**、**用户只在上限后通知一次**（不刷屏）；
  3. **"多次"有明确阈值**，到顶就 `needs_human` 且**不再无限重试**；
  4. **不许假报警**：桥进程 `unknown`（多数 harness 的桥不是平台拉的）与心跳无数据**都不算掉线**。
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from agent_community.platform import member_offline as mo  # noqa: E402
from agent_community.platform.workshop import Workshop, WorkshopMember  # noqa: E402


def run(coro):
    return asyncio.run(coro)


def _mkws(tmp_path, members=None):
    """默认成员是**有活**的（`entry_state=dispatched`）——
    因为"没活"现在会被判定为 `idle` 而**不救**（老板 2026-10-08），
    抢救链测试必须建立在"它该干活"的前提上。"""
    return Workshop(workshop_id="w1", name="项目", workspace_dir=str(tmp_path / "ws"),
                    hall_content="x", created_at="now",
                    members=members or [WorkshopMember(member_id="m1", role="码农",
                                                       display_name="小码", harness_ids=["h1"],
                                                       entry_state="dispatched"),
                                        WorkshopMember(member_id="m0", role="组长",
                                                       display_name="老组", harness_ids=["hL"],
                                                       entry_state="dispatched")])


class TestPendingWorkGate:
    """★老板 2026-10-08：「这个不够智能，**有时候对方并没有工作需要进行**」。

    ⇒ 救之前必须先问"它现在该干活吗"：**没活就不救、不叫组长、不叫用户**。
    这是"别打扰用户"与"别白折腾"的直接落点。
    """

    class _WS:
        def __init__(self, members):
            self.members = members
            self.assignments = {}

    def _idle_member(self, **kw):
        """造一个"空闲"成员；kw 里的字段**覆盖**默认值（不能直接传同名字段，会重复关键字）。"""
        m = WorkshopMember(member_id="m1", role="码农", display_name="小码",
                           harness_ids=["h1"], status="idle", entry_state="pending")
        for k, v in kw.items():
            setattr(m, k, v)
        return m

    def test_idle_member_is_never_rescued(self, tmp_path):
        """**空闲 + 心跳失联** ⇒ 不救、不通知任何人（这就是老板要的"聪明"）。"""
        ws = self._WS([self._idle_member()])
        rec = _Rec()
        out = mo.handle_offline(ws, ws.members[0], notify_leader=rec.nl,
                                attempt_revive=lambda: {"ok": True, "verified": False},
                                notify_user=rec.nu,
                                heartbeat_status=lambda h: {"stale": True}, bridge_alive=lambda h: None,
                                now=1000.0)
        assert out["action"] == "idle", out
        assert out["verdict"] == "idle"
        assert rec.leader == [] and rec.user == [], "没活就不许打扰任何人"
        assert "空闲" in out["pending_work_why"] or "没有在进行" in out["pending_work_why"]

    def test_dead_bridge_but_idle_is_still_not_rescued(self, tmp_path):
        """桥确实死了、但它没活 ⇒ 仍然不救（不可用 ≠ 要去救）。"""
        ws = self._WS([self._idle_member()])
        g = mo.rescue_needed(ws, ws.members[0], heartbeat_status=lambda h: None,
                             bridge_alive=lambda h: False)
        assert g["verdict"] == "idle" and g["needed"] is False
        assert g["signal_reasons"], "信号仍要如实报出（它确实不可用），只是不去救"

    def test_working_member_with_dead_heartbeat_is_rescued(self, tmp_path):
        ws = self._WS([self._idle_member(status="working")])
        g = mo.rescue_needed(ws, ws.members[0], heartbeat_status=lambda h: {"stale": True},
                             bridge_alive=lambda h: None)
        assert g["verdict"] == "rescue" and g["needed"] is True

    def test_pending_work_signals(self, tmp_path):
        """四种"有活"的依据都要认（entry_state / status / 派发观察 / 已派发委派）。"""
        m1 = WorkshopMember(member_id="m1", role="员工", display_name="甲", harness_ids=["h1"],
                            entry_state="acked")
        assert mo.has_pending_work(self._WS([m1]), m1)[0] is True
        m2 = WorkshopMember(member_id="m2", role="员工", display_name="乙", harness_ids=["h1"],
                            status="activating")
        assert mo.has_pending_work(self._WS([m2]), m2)[0] is True
        m3 = WorkshopMember(member_id="m3", role="员工", display_name="丙", harness_ids=["h1"])
        m3._sim_watch = {"done": False}
        assert mo.has_pending_work(self._WS([m3]), m3)[0] is True
        m4 = WorkshopMember(member_id="m4", role="员工", display_name="丁", harness_ids=["h1"])
        ws = self._WS([m4])
        ws.assignments = {"m4": [{"dispatched": True, "note": "做X"}]}
        assert mo.has_pending_work(ws, m4)[0] is True
        # 结案的不算
        ws.assignments = {"m4": [{"dispatched": True, "done": True}]}
        assert mo.has_pending_work(ws, m4)[0] is False

    def test_pending_work_but_healthy_is_none(self, tmp_path):
        ws = self._WS([self._idle_member(status="working")])
        rec = _Rec()
        out = mo.handle_offline(ws, ws.members[0], notify_leader=rec.nl,
                                attempt_revive=lambda: {"ok": True, "verified": True},
                                notify_user=rec.nu, heartbeat_status=lambda h: {"stale": False},
                                bridge_alive=lambda h: None, now=1000.0)
        assert out["action"] == "none" and rec.leader == [] and rec.user == []

    def test_heartbeat_stale_is_soft_not_unavailable(self, tmp_path):
        """★审 FE-09 时发现的口径问题：**心跳失联是软信号**（本项目心跳可选、很多 harness 从不报），
        ⇒ 不许据此把成员显示成"联系不上"；但它仍算"有活却不对劲"，**该救还是要救**。"""
        m = WorkshopMember(member_id="m1", role="码农", display_name="小码", harness_ids=["h1"],
                           status="working")
        ws = self._WS([m])
        r = mo.offline_report(ws, heartbeat_status=lambda h: {"stale": True},
                              bridge_alive=lambda h: None, now=1000.0)[0]
        assert r["available"] is True, "只看硬信号：心跳失联 ≠ 收不到活"
        assert r["hard_reasons"] == []
        assert any("心跳" in x for x in r["soft_reasons"]), "软信号仍要如实带出"
        assert r["verdict"] == "rescue", "有活 + 有信号 ⇒ 仍要救"

    def test_dead_bridge_is_hard_unavailable(self, tmp_path):
        """桥确认退出才是**硬**信号（没人取邮箱 ⇒ 确实收不到活）。"""
        m = WorkshopMember(member_id="m1", role="码农", display_name="小码", harness_ids=["h1"],
                           status="working")
        ws = self._WS([m])
        r = mo.offline_report(ws, heartbeat_status=lambda h: None,
                              bridge_alive=lambda h: False, now=1000.0)[0]
        assert r["available"] is False
        assert r["hard_reasons"] and "桥进程" in r["hard_reasons"][0]

    def test_report_marks_three_verdicts(self, tmp_path):
        idle = WorkshopMember(member_id="m1", role="员工", display_name="甲", harness_ids=["h1"],
                              status="idle")
        busy = WorkshopMember(member_id="m2", role="员工", display_name="乙", harness_ids=["h2"],
                              status="working")
        ok_m = WorkshopMember(member_id="m3", role="员工", display_name="丙", harness_ids=["h3"],
                              status="working")
        ws = self._WS([idle, busy, ok_m])

        def hb(h):
            return {"stale": True} if h == "h2" else {"stale": False}
        rows = {r["member_id"]: r for r in mo.offline_report(ws, heartbeat_status=hb,
                                                            bridge_alive=lambda h: None, now=1000.0)}
        assert rows["m1"]["verdict"] == "idle" and rows["m1"]["needs_rescue"] is False
        assert rows["m2"]["verdict"] == "rescue" and rows["m2"]["needs_rescue"] is True
        assert rows["m3"]["verdict"] == "healthy" and rows["m3"]["available"] is True


class _Rec:
    """记录回调调用，便于断言"通知了几次"。（定义在用例之后无妨：调用时才解析名字。）"""

    def __init__(self):
        self.leader: list = []
        self.user: list = []

    def nl(self, text):
        self.leader.append(text)

    def nu(self, text):
        self.user.append(text)


def _revive(fail=True, detail="未命中窗口"):
    return lambda: {"ok": True, "verified": not fail, "detail": detail, "how": "重派+窗口激活"}


class TestHeartbeatIntervalIsEffective:
    """顺带修的真 bug：`POST /api/heartbeat` 的 `interval` 之前**解析了却从未使用**
    （`beat()` 不接收 interval，只有 `register()` 写进记录）⇒ 文档承诺的 `interval?: 秒` 无效，
    调用方无法缩短失联判定窗口。实测踩到：想用 interval=1 造失联来验掉线链，4 秒后仍判在线。
    """

    def _stub_request(self, body):
        async def _json():
            return body
        return type("R", (), {"json": staticmethod(_json)})()

    def _call(self, tmp_path, monkeypatch, body):
        from agent_community.platform import server as srv
        from agent_community.platform.heartbeat import heartbeat_registry
        class _Info:
            harness_name = "示例Harness-Off"
            def model_dump(self):
                return {}
        sess = type("S", (), {"info": _Info(), "metadata": {}})()
        monkeypatch.setattr(srv.harness_manager, "sessions", {"off-man": sess}, raising=False)
        try:
            heartbeat_registry.unregister("off-man")
        except Exception:  # noqa: BLE001
            pass
        import asyncio as _a
        out = _a.run(srv.api_heartbeat(self._stub_request(body)))
        rec = heartbeat_registry.status("off-man") or {}
        return out, rec

    def test_interval_is_written_into_the_record(self, tmp_path, monkeypatch):
        out, rec = self._call(tmp_path, monkeypatch,
                              {"harness_id": "off-man", "interval": 1})
        assert out["success"] is True
        assert rec.get("interval") == 1.0, f"interval 必须真的写进记录，实得 {rec.get('interval')}"

    def test_shorter_interval_makes_stale_sooner(self, tmp_path, monkeypatch):
        out, rec = self._call(tmp_path, monkeypatch,
                              {"harness_id": "off-man", "interval": 1})
        # stale 判定 = now - last_seen > interval * timeout_scale(2.0) ⇒ interval=1 时 2 秒即失联
        from agent_community.platform.heartbeat import heartbeat_registry
        rec2 = dict(rec)
        rec2["last_seen"] = rec["last_seen"] - 3.0
        assert heartbeat_registry._is_stale(rec2) is True, "interval=1 时 3 秒前的心跳应判失联"

    def test_default_interval_when_not_provided(self, tmp_path, monkeypatch):
        out, rec = self._call(tmp_path, monkeypatch, {"harness_id": "off-man"})
        assert rec.get("interval") == 30.0, "不传 interval 时维持既有默认 30s（行为不变）"


class TestOfflineDetection:
    """掉线判据：哪些算、哪些**不许**算（防假报警）。"""

    def test_healthy_member_not_offline(self, tmp_path):
        ws = _mkws(tmp_path)
        assert mo.offline_reasons(ws, ws.members[0], heartbeat_status=lambda h: {"stale": False},
                                  bridge_alive=lambda h: None) == []

    def test_stale_heartbeat_is_offline(self, tmp_path):
        ws = _mkws(tmp_path)
        r = mo.offline_reasons(ws, ws.members[0], heartbeat_status=lambda h: {"stale": True},
                               bridge_alive=lambda h: None)
        assert r and "心跳" in r[0]

    def test_dead_bridge_is_offline(self, tmp_path):
        ws = _mkws(tmp_path)
        r = mo.offline_reasons(ws, ws.members[0], heartbeat_status=lambda h: None,
                               bridge_alive=lambda h: False)
        assert r and "桥进程" in r[0]

    def test_unknown_bridge_is_not_offline(self, tmp_path):
        """**防假报警**：桥 status=unknown（平台没登记过）不许判掉线，否则给全部成员报警。"""
        ws = _mkws(tmp_path)
        assert mo.offline_reasons(ws, ws.members[0], heartbeat_status=lambda h: None,
                                  bridge_alive=lambda h: None) == []

    def test_no_harness_is_reported(self, tmp_path):
        ws = _mkws(tmp_path, [WorkshopMember(member_id="m9", role="员工", display_name="甲")])
        r = mo.offline_reasons(ws, ws.members[0], heartbeat_status=lambda h: None,
                               bridge_alive=lambda h: None)
        assert r and "未绑定 harness" in r[0]

    def test_blocked_entry_state_counts(self, tmp_path):
        m = WorkshopMember(member_id="m1", role="码农", display_name="小码", harness_ids=["h1"],
                           entry_state="blocked", entry_reason="桥没起")
        ws = _mkws(tmp_path, [m])
        r = mo.offline_reasons(ws, m, heartbeat_status=lambda h: None, bridge_alive=lambda h: None)
        assert any("blocked" in x for x in r)


class TestRecoveryChain:
    def _call(self, ws, m, rec, *, now, revive=None, stale=True):
        return mo.handle_offline(
            ws, m, notify_leader=rec.nl, attempt_revive=revive or _revive(), notify_user=rec.nu,
            heartbeat_status=lambda h: {"stale": stale}, bridge_alive=lambda h: None, now=now)

    def test_first_round_notifies_leader_then_retries(self, tmp_path):
        ws = _mkws(tmp_path)
        m, rec = ws.members[0], _Rec()
        out = self._call(ws, m, rec, now=1000.0)
        assert out["action"] == "retry" and out["attempts"] == 1 and out["notified_leader"] is True
        assert len(rec.leader) == 1 and "掉线" in rec.leader[0]
        assert rec.user == [], "首轮**不许**上报用户（老板：先就地抢救）"
        assert m.entry_state != "needs_human"

    def test_cooldown_blocks_hammering(self, tmp_path):
        ws = _mkws(tmp_path)
        m, rec = ws.members[0], _Rec()
        self._call(ws, m, rec, now=1000.0)
        out = self._call(ws, m, rec, now=1000.0 + 10)     # 冷却内
        assert out["action"] == "cooldown" and out["attempts"] == 1
        assert len(rec.leader) == 1, "冷却期不该重复通知组长"

    def test_escalates_to_user_only_at_max_attempts(self, tmp_path):
        ws = _mkws(tmp_path)
        m, rec = ws.members[0], _Rec()
        t = 1000.0
        for i in range(1, mo.MAX_ATTEMPTS + 1):
            out = self._call(ws, m, rec, now=t)
            t += mo.COOLDOWN_SECONDS + 1
            assert out["action"] == ("gave_up" if i == mo.MAX_ATTEMPTS else "retry"), out
        assert len(rec.user) == 1, "只在上限后通知用户一次"
        assert "需要你处理" in rec.user[0]
        assert m.entry_state == "needs_human" and "调起" in m.entry_reason
        assert len(rec.leader) == 1, "组长始终只通知一次"

    def test_after_escalation_no_more_retries(self, tmp_path):
        ws = _mkws(tmp_path)
        m, rec = ws.members[0], _Rec()
        t = 1000.0
        for _ in range(mo.MAX_ATTEMPTS):
            self._call(ws, m, rec, now=t)
            t += mo.COOLDOWN_SECONDS + 1
        out = self._call(ws, m, rec, now=t + 200)
        assert out["action"] == "escalated", "上报人后不再自动重试（不刷屏、不空转）"

    def test_verified_revive_clears_attempts(self, tmp_path):
        ws = _mkws(tmp_path)
        m, rec = ws.members[0], _Rec()
        self._call(ws, m, rec, now=1000.0)                        # 先失败一次
        out = self._call(ws, m, rec, now=1000.0 + 61, revive=_revive(fail=False))
        assert out["action"] == "revived"
        assert m.offline_attempts == 0 and m.offline_last_at == 0, "结案要清零"

    def test_recovered_member_resets_without_notice(self, tmp_path):
        ws = _mkws(tmp_path)
        m, rec = ws.members[0], _Rec()
        m.offline_attempts = 2
        out = self._call(ws, m, rec, now=1000.0, stale=False)
        assert out["action"] == "none" and m.offline_attempts == 0
        assert rec.leader == [] and rec.user == []

    def test_leader_notification_failure_does_not_abort_chain(self, tmp_path):
        """通知组长失败也必须继续尝试调起（不能因为通知失败就把抢救链断掉）。"""
        ws = _mkws(tmp_path)
        m = ws.members[0]

        def boom(_):
            raise RuntimeError("讨论区写失败")
        out = mo.handle_offline(ws, m, notify_leader=boom, attempt_revive=_revive(),
                                notify_user=lambda t: None,
                                heartbeat_status=lambda h: {"stale": True}, bridge_alive=lambda h: None,
                                now=1000.0)
        assert out["action"] == "retry" and out["attempts"] == 1

    def test_revive_exception_is_unverified_not_success(self, tmp_path):
        ws = _mkws(tmp_path)
        m, rec = ws.members[0], _Rec()

        def boom():
            raise RuntimeError("窗口激活炸了")
        out = mo.handle_offline(ws, m, notify_leader=rec.nl, attempt_revive=boom, notify_user=rec.nu,
                                heartbeat_status=lambda h: {"stale": True}, bridge_alive=lambda h: None,
                                now=1000.0)
        assert out["action"] == "retry", "调起异常绝不算成功"

    def test_report_shape(self, tmp_path):
        ws = _mkws(tmp_path)
        m, rec = ws.members[0], _Rec()
        self._call(ws, m, rec, now=1000.0)
        rows = mo.offline_report(ws, heartbeat_status=lambda h: {"stale": True},
                                 bridge_alive=lambda h: None, now=1000.0 + 5)
        r = rows[0]
        assert r["offline"] is True and r["attempts"] == 1 and r["max_attempts"] == mo.MAX_ATTEMPTS
        assert r["cooldown_left"] > 0 and r["escalated"] is False
