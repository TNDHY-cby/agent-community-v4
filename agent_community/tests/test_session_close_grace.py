# -*- coding: utf-8 -*-
"""V-17 测试：`close_requested` 宽限期惰性收敛（会话关闭兜底）。

设计稿：`design-docs/V17_会话关闭兜底设计.md`（§5 验证矩阵逐条对应）

**背景（有实测证据）**：V-15 的两段式关闭里，第二段依赖 harness 主动自述，
**没有兜底** → 实测 18 个会话中 17 个永久停在 `close_requested`。

本文件锁死的红线：
- **纯规则、事件触发**：绝不轮询、不走 LLM（省 token 红线）；
- **`idle` 不得被收敛**（V-15 拍板 #4「工作间结束保留待复用」）；
- 记录**只改状态、不删除**（晚到的自述仍能更新，审计轨迹保留）；
- 收敛逻辑**失败不阻断**可见性查询（它挂在只读链路上）。
"""
from __future__ import annotations

import asyncio

import pytest

from agent_community.platform import session_registry as sr_mod
from agent_community.platform.session_registry import (
    CLOSE_GRACE_SECONDS,
    CLOSE_REASON_GRACE,
    CLOSE_REASON_SELF,
    ST_ACTIVE,
    ST_CLOSE_REQ,
    ST_CLOSED,
    ST_IDLE,
    ST_STALE,
    SessionRegistry,
)


def run(coro):
    return asyncio.run(coro)


@pytest.fixture()
def reg(tmp_path, monkeypatch):
    """隔离注册表 + 替换模块级单例（sweep_and_audit 用的是模块全局）。"""
    r = SessionRegistry(data_dir=tmp_path / "_grace")
    monkeypatch.setattr(sr_mod, "session_registry", r)
    return r


def _seed_close_req(reg, **kw):
    """造一个停在 close_requested 的会话，返回 (harness_id, session_id, 起算时间)。"""
    base = dict(harness_id="h1", session_id="s1", workshop_id="w1",
                member_id="m1", source="acp")
    base.update(kw)
    reg.register(**base)
    reg.set_status(base["harness_id"], base["session_id"], ST_CLOSE_REQ)
    rec = reg.get(base["harness_id"], base["session_id"])
    return base["harness_id"], base["session_id"], rec["close_requested_at"]


# ══════════════════════════════════════════════════════════════════
# §5-1 / §5-2：宽限期内保留，超期收敛
# ══════════════════════════════════════════════════════════════════
class TestGraceWindow:
    def test_within_grace_stays_close_requested(self, reg):
        hid, sid, started = _seed_close_req(reg)
        out = reg.sweep_expired_close_requests(now=started + 100, grace_seconds=600)
        assert out == [], "宽限期内不得收敛"
        assert reg.get(hid, sid)["status"] == ST_CLOSE_REQ

    def test_exactly_at_grace_is_expired(self, reg):
        """边界：宽限期**走满即算到期**（`now - start >= grace`），不拖到下一秒。

        这也是收敛条件写成 `if now - started < grace: continue` 的含义 ——
        与设计稿 §3.2 的「超过宽限期」一致（期满即可关）。
        """
        hid, sid, started = _seed_close_req(reg)
        out = reg.sweep_expired_close_requests(now=started + 600, grace_seconds=600)
        assert len(out) == 1, "走满宽限期应收敛"
        assert reg.get(hid, sid)["status"] == ST_CLOSED

    def test_one_second_before_grace_stays(self, reg):
        hid, sid, started = _seed_close_req(reg)
        out = reg.sweep_expired_close_requests(now=started + 599, grace_seconds=600)
        assert out == []
        assert reg.get(hid, sid)["status"] == ST_CLOSE_REQ

    def test_beyond_grace_closes_with_reason(self, reg):
        hid, sid, started = _seed_close_req(reg)
        out = reg.sweep_expired_close_requests(now=started + 601, grace_seconds=600)
        assert len(out) == 1
        rec = reg.get(hid, sid)
        assert rec["status"] == ST_CLOSED
        assert rec["close_reason"] == CLOSE_REASON_GRACE, "必须标记是兜底关闭"
        assert rec["closed_at"] == started + 601, "应记录关闭时刻"

    def test_default_grace_constant_value(self):
        """默认宽限期 = 10 × 心跳超时窗（60s），改动需同步设计稿 §3.1。"""
        assert CLOSE_GRACE_SECONDS == 600.0


# ══════════════════════════════════════════════════════════════════
# §5-3：其它状态一律不碰（拍板 #4 回归）
# ══════════════════════════════════════════════════════════════════
class TestOtherStatusesUntouched:
    @pytest.mark.parametrize("status", [ST_ACTIVE, ST_IDLE, ST_STALE, ST_CLOSED])
    def test_non_close_requested_never_touched(self, reg, status):
        reg.register(harness_id="h1", session_id="s-x", workshop_id="w1", member_id="m1")
        if status != ST_ACTIVE:
            reg.set_status("h1", "s-x", status)
        # 把时间推到远超宽限期
        reg.sweep_expired_close_requests(now=1e12, grace_seconds=600)
        assert reg.get("h1", "s-x")["status"] == status, \
            f"{status} 不得被兜底收敛（idle 尤其 —— 拍板 #4 保留待复用）"

    def test_idle_survives_even_with_old_close_requested_at(self, reg):
        """防退化：万一 idle 记录上残留了 close_requested_at，也不能被收敛。"""
        reg.register(harness_id="h1", session_id="s-i", workshop_id="w1", member_id="m1")
        reg.set_status("h1", "s-i", ST_IDLE)
        reg._records[0]["close_requested_at"] = 1.0      # 极旧
        reg.sweep_expired_close_requests(now=1e12, grace_seconds=600)
        assert reg.get("h1", "s-i")["status"] == ST_IDLE


# ══════════════════════════════════════════════════════════════════
# §5-4：幂等（重复 sweep 不重复收敛、不重复记审计）
# ══════════════════════════════════════════════════════════════════
class TestIdempotence:
    def test_second_sweep_returns_nothing(self, reg):
        hid, sid, started = _seed_close_req(reg)
        first = reg.sweep_expired_close_requests(now=started + 1000, grace_seconds=600)
        second = reg.sweep_expired_close_requests(now=started + 2000, grace_seconds=600)
        assert len(first) == 1 and second == []
        assert reg.get(hid, sid)["status"] == ST_CLOSED


# ══════════════════════════════════════════════════════════════════
# §5-5：老记录无 close_requested_at → 补记起算，不立即关闭
# ══════════════════════════════════════════════════════════════════
class TestLegacyRecords:
    def test_legacy_record_backfilled_not_closed(self, reg):
        """V-17 上线瞬间不得把历史数据成批误清 —— 宽限期从此刻起算。"""
        reg.register(harness_id="h1", session_id="s-old", workshop_id="w1", member_id="m1")
        reg.set_status("h1", "s-old", ST_CLOSE_REQ)
        reg._records[0].pop("close_requested_at", None)     # 模拟 V-17 之前落盘

        out = reg.sweep_expired_close_requests(now=1e12, grace_seconds=600)
        assert out == [], "补记起算点不算关闭"
        rec = reg.get("h1", "s-old")
        assert rec["status"] == ST_CLOSE_REQ
        assert rec["close_requested_at"] == 1e12, "应补记当前时间"

    def test_legacy_then_expires_normally(self, reg):
        reg.register(harness_id="h1", session_id="s-old", workshop_id="w1", member_id="m1")
        reg.set_status("h1", "s-old", ST_CLOSE_REQ)
        reg._records[0].pop("close_requested_at", None)

        reg.sweep_expired_close_requests(now=1000.0, grace_seconds=600)
        out = reg.sweep_expired_close_requests(now=1000.0 + 601, grace_seconds=600)
        assert len(out) == 1, "补记之后应按正常宽限期收敛"

    def test_legacy_backfill_is_persisted(self, reg, tmp_path):
        """补记必须落盘，否则每次 sweep 都重新起算 → 永远收敛不了。"""
        reg.register(harness_id="h1", session_id="s-old", workshop_id="w1", member_id="m1")
        reg.set_status("h1", "s-old", ST_CLOSE_REQ)
        reg._records[0].pop("close_requested_at", None)
        reg.sweep_expired_close_requests(now=1000.0, grace_seconds=600)

        fresh = SessionRegistry(data_dir=tmp_path / "_grace")
        assert fresh.get("h1", "s-old")["close_requested_at"] == 1000.0


# ══════════════════════════════════════════════════════════════════
# §5-9：记录只改状态、不删除
# ══════════════════════════════════════════════════════════════════
class TestRecordKept:
    def test_closed_record_still_present(self, reg):
        hid, sid, started = _seed_close_req(reg)
        reg.sweep_expired_close_requests(now=started + 1000, grace_seconds=600)
        assert reg.get(hid, sid) is not None, "兜底关闭不得删除记录（晚到自述仍要能更新）"

    def test_late_self_report_can_still_update(self, reg):
        """晚到的 harness 自述应能把 close_reason 改回「自述」，语义以对面为准。"""
        hid, sid, started = _seed_close_req(reg)
        reg.sweep_expired_close_requests(now=started + 1000, grace_seconds=600)
        assert reg.get(hid, sid)["close_reason"] == CLOSE_REASON_GRACE

        reg.set_status(hid, sid, ST_CLOSED, note="对面补报")
        rec = reg.get(hid, sid)
        assert rec["close_reason"] == CLOSE_REASON_SELF
        assert rec["note"] == "对面补报"


# ══════════════════════════════════════════════════════════════════
# §5-6 / §5-7：端点接线与失败不阻断
# ══════════════════════════════════════════════════════════════════
class TestEndpointWiring:
    def test_listing_sessions_sweeps_first(self, reg):
        """触发时机①：人来看的那一刻必须是真相（计数应已收敛）。"""
        from agent_community.platform.routers import sessions as s_mod

        hid, sid, started = _seed_close_req(reg)
        reg._records[0]["close_requested_at"] = started - 100000   # 早已超期

        out = run(s_mod.list_sessions())
        assert out["close_requested_count"] == 0, "列表前应先收敛"
        assert out["count"] == 0, "已关闭会话不再出现在活跃列表"
        assert out["total_count"] == 1, "total_count 仍是全量（记录没被删）"

    def test_within_grace_still_listed(self, reg):
        from agent_community.platform.routers import sessions as s_mod

        _seed_close_req(reg)
        out = run(s_mod.list_sessions())
        assert out["close_requested_count"] == 1
        assert out["count"] == 1, "宽限期内仍要看得见（等待态必须可见）"

    def test_sweep_failure_does_not_break_endpoint(self, reg, monkeypatch):
        """兜底逻辑炸了，可见性查询仍须 200（它挂在只读链路上）。"""
        from agent_community.platform.routers import sessions as s_mod

        def boom(*_a, **_k):
            raise RuntimeError("收敛炸了")

        monkeypatch.setattr(reg, "sweep_expired_close_requests", boom)
        out = run(s_mod.list_sessions())
        assert out["success"] is True, "兜底失败不得影响列表返回"

    def test_sweep_and_audit_never_raises(self, reg, monkeypatch):
        monkeypatch.setattr(reg, "sweep_expired_close_requests",
                            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
        assert sr_mod.sweep_and_audit() == []


# ══════════════════════════════════════════════════════════════════
# §5-8：审计可区分「兜底关闭」与「自述关闭」
# ══════════════════════════════════════════════════════════════════
class TestAudit:
    def test_grace_close_is_audited_with_reason(self, reg, tmp_path):
        from agent_community.platform.audit import audit_log

        hid, sid, started = _seed_close_req(reg)
        reg._records[0]["close_requested_at"] = started - 100000
        closed = sr_mod.sweep_and_audit(grace_seconds=600)
        assert len(closed) == 1

        evs = audit_log.query(limit=20, event="session.close")
        assert evs, "兜底关闭必须留审计"
        e = evs[0]
        assert e.get("actor") == "system", "兜底是平台行为，actor 应为 system"
        assert "宽限期到期" in str(e.get("detail", ""))
        assert sid in str(e.get("detail", "")), "detail 应含 session_id 便于溯源"

    def test_no_audit_when_nothing_expires(self, reg):
        from agent_community.platform.audit import audit_log

        _seed_close_req(reg)
        assert sr_mod.sweep_and_audit() == []
        assert audit_log.query(limit=20, event="session.close") == [], \
            "没收敛就不该有审计噪音"


# ══════════════════════════════════════════════════════════════════
# 配置读取与回退
# ══════════════════════════════════════════════════════════════════
class TestGraceConfig:
    def test_config_overrides_default(self, monkeypatch):
        monkeypatch.setattr(sr_mod, "load_config", None, raising=False)
        import agent_community.config as cfg_mod
        monkeypatch.setattr(cfg_mod, "load_config",
                            lambda: {"session_close_grace_seconds": 42}, raising=True)
        assert sr_mod._close_grace_seconds() == 42

    def test_invalid_config_falls_back(self, monkeypatch):
        import agent_community.config as cfg_mod

        for bad in (None, "", "abc", 0, -5):
            monkeypatch.setattr(cfg_mod, "load_config",
                                lambda b=bad: {"session_close_grace_seconds": b}, raising=True)
            assert sr_mod._close_grace_seconds() == CLOSE_GRACE_SECONDS, \
                f"非法值 {bad!r} 应回退默认常量"

    def test_missing_key_falls_back(self, monkeypatch):
        import agent_community.config as cfg_mod
        monkeypatch.setattr(cfg_mod, "load_config", lambda: {}, raising=True)
        assert sr_mod._close_grace_seconds() == CLOSE_GRACE_SECONDS

    def test_config_key_exists_in_defaults(self):
        from agent_community.config import DEFAULT_CONFIG
        assert "session_close_grace_seconds" in DEFAULT_CONFIG


# ══════════════════════════════════════════════════════════════════
# 其余两个触发时机（工作间删除 / harness 注销）也已接线
# ══════════════════════════════════════════════════════════════════
class TestOtherTriggerPoints:
    def test_delete_workshop_sweeps_before_closing(self, reg, monkeypatch):
        """触发时机②：删除工作间时先收敛上一批到期遗留。"""
        from agent_community.platform import state as state_mod
        from agent_community.platform.routers import workshops_lifecycle as wl
        from agent_community.platform.workshop import Workshop

        called = []
        real = sr_mod.sweep_and_audit
        monkeypatch.setattr(sr_mod, "sweep_and_audit",
                            lambda *a, **k: called.append(1) or real(*a, **k))
        state_mod.workshops["w-del"] = Workshop(
            workshop_id="w-del", name="t", workspace_dir="", hall_content="")
        try:
            run(wl.delete_workshop("w-del"))
        finally:
            state_mod.workshops.pop("w-del", None)
        assert called, "删除工作间路径必须调用兜底收敛"

    def test_unregister_harness_sweeps_before_removing(self, reg, monkeypatch):
        """触发时机③：宿主消失前先标掉到期项。"""
        from agent_community.platform.harness_adapter import harness_manager
        from agent_community.platform.protocol import HarnessAI, HarnessInfo, WakeupMethod
        from agent_community.platform.routers import harness_register as hr

        monkeypatch.setattr(harness_manager, "sessions", {}, raising=False)
        monkeypatch.setattr(harness_manager, "bridges", {}, raising=False)
        monkeypatch.setattr(harness_manager, "id_to_harness", {}, raising=False)

        called = []
        real = sr_mod.sweep_and_audit
        monkeypatch.setattr(sr_mod, "sweep_and_audit",
                            lambda *a, **k: called.append(1) or real(*a, **k))

        info = HarnessInfo(harness_id="h-doom", harness_name="h-doom",
                           wakeup_method=WakeupMethod.CLIPBOARD,
                           ai=HarnessAI(model_name="示例模型"))
        harness_manager.register(info)
        run(hr.unregister_harness("h-doom"))
        assert called, "注销路径必须调用兜底收敛"
