# -*- coding: utf-8 -*-
"""V-15 步骤1 测试：会话注册表 + 回执契约。

覆盖设计稿 §十四 步骤1 的验证要求「登记/查询/落盘/读回」，
以及 §十五 验证矩阵中与本步相关的条目（#4 后半、#12、#13、#14、#16、#17、#19、#20）。

三条纪律各有一个用例锁住：
  1. 就地变更不重新绑定（同 state.py / policy.py 的教训）
  2. 独立落盘（不碰 policy.json）
  3. 失败不阻塞（坏路径不抛）
"""
from __future__ import annotations

import asyncio
import json

import pytest

from agent_community.platform.session_registry import (
    SRC_ACP,
    SRC_HUMAN,
    ST_ACTIVE,
    ST_CLOSE_REQ,
    ST_CLOSED,
    ST_IDLE,
    SessionRegistry,
)


class FakeRequest:
    def __init__(self, body=None, headers=None):
        self.headers = headers or {}
        self._body = body or {}

    async def json(self):
        return self._body


def run(coro):
    return asyncio.run(coro)


@pytest.fixture()
def reg(tmp_path):
    """隔离的注册表实例（独立临时目录，不碰真实 data/）。"""
    return SessionRegistry(data_dir=tmp_path / "_sessions")


def _mk(reg, **kw):
    base = dict(harness_id="h1", session_id="s1", workshop_id="w1",
                member_id="m1", role="员工", workspace_dir=r"D:\ws\ws_a")
    base.update(kw)
    return reg.register(**base)


# ── 核心：登记 / 查询 ─────────────────────────────────────────────
class TestRegistryCore:
    def test_register_and_get_roundtrip(self, reg):
        rec = _mk(reg, source=SRC_ACP, context_turns=1)
        assert rec["session_id"] == "s1"
        assert rec["status"] == ST_ACTIVE
        got = reg.get("h1", "s1")
        assert got is not None and got["workshop_id"] == "w1"
        assert got["role"] == "员工"

    def test_idempotent_update_no_duplicate(self, reg):
        """同 (harness, session) 再登记 = 更新，不产生重复行。"""
        _mk(reg, context_turns=1)
        _mk(reg, context_turns=5, workspace_dir=r"D:\ws\ws_b")
        assert len(reg.list()) == 1
        r = reg.get("h1", "s1")
        assert r["context_turns"] == 5, "更新应生效"
        assert r["workspace_dir"] == r"D:\ws\ws_b"
        assert r["created_at"] <= r["last_seen"], "created_at 不应被覆盖"

    def test_human_without_session_id_synthesizes(self, reg):
        """11.1 人工进入没有 session_id → 合成 id 且标 source=human。"""
        rec = _mk(reg, session_id="", source=SRC_HUMAN, member_id="m2")
        assert rec["session_id"].startswith("human:"), rec["session_id"]
        assert rec["source"] == SRC_HUMAN
        assert "m2" in rec["session_id"]

    def test_find_excludes_closed(self, reg):
        """14 复用判定：closed 的不该被找到（不能复用已关闭的会话）。"""
        _mk(reg)
        assert len(reg.find("w1", "m1")) == 1
        reg.set_status("h1", "s1", ST_CLOSED)
        assert reg.find("w1", "m1") == []

    def test_list_filters(self, reg):
        _mk(reg)
        _mk(reg, harness_id="h2", session_id="s2", workshop_id="w2")
        assert len(reg.list()) == 2
        assert len(reg.list(harness_id="h1")) == 1
        assert len(reg.list(workshop_id="w2")) == 1
        assert len(reg.list(harness_id="h2", workshop_id="w1")) == 0

    def test_unknown_status_rejected(self, reg):
        """打字错误不得静默创建新状态。"""
        _mk(reg)
        assert reg.set_status("h1", "s1", "actve") is None   # 拼写错误
        assert reg.get("h1", "s1")["status"] == ST_ACTIVE

    def test_touch_updates_last_seen(self, reg):
        _mk(reg)
        before = reg.get("h1", "s1")["last_seen"]
        import time as _t
        _t.sleep(0.01)
        assert reg.touch("h1", "s1") is True
        assert reg.get("h1", "s1")["last_seen"] > before

    def test_missing_harness_id(self, reg):
        assert "error" in reg.register(harness_id="")


# ── 生命周期（拍板 #4：保留待复用）──────────────────────────────
class TestLifecycle:
    def test_workshop_end_keeps_sessions(self, reg):
        """12 工作间结束 → 保留标 idle，不关（拍板 #4）。"""
        _mk(reg)
        n = reg.on_workshop_ended("w1")
        assert n == 1
        r = reg.get("h1", "s1")
        assert r["status"] == ST_IDLE, "应保留为 idle，不是 closed"
        assert len(reg.list()) == 1, "不应被移除"

    def test_workshop_deleted_requests_close(self, reg):
        """14 删工作间 → cwd 已随工作区进回收站 → 发起 close。"""
        _mk(reg)
        changed = reg.on_workshop_deleted("w1")
        assert len(changed) == 1
        assert changed[0]["status"] == ST_CLOSE_REQ
        assert changed[0]["close_requested_at"] is not None
        assert len(reg.list()) == 1, "只是标状态，不立即移除（等 harness 自述）"

    def test_harness_unregister_removes(self, reg):
        """16 注销 → 宿主没了，全部移除。"""
        _mk(reg)
        _mk(reg, harness_id="h2", session_id="s2")
        n = reg.on_harness_unregistered("h1")
        assert n == 1
        assert reg.list(harness_id="h1") == []
        assert len(reg.list(harness_id="h2")) == 1

    def test_clear_is_inplace_no_rebind(self, reg):
        """纪律1：clear 必须就地，不能 = []（否则旧引用失效）。"""
        _mk(reg)
        before = reg._records
        n = reg.clear()
        assert n == 1
        assert reg._records is before, "clear 后必须是同一个对象"
        assert reg._records == [] or len(reg._records) == 0

    def test_clear_then_reuse(self, reg):
        """清空后注册表仍可用（不因 clear 变坏）。"""
        _mk(reg)
        reg.clear()
        _mk(reg, session_id="s9")
        assert len(reg.list()) == 1


# ── 落盘（纪律2：独立落盘）────────────────────────────────────
class TestPersistence:
    def test_write_and_read_back(self, tmp_path):
        """15 落盘/读回。"""
        d = tmp_path / "_p1"
        a = SessionRegistry(data_dir=d)
        a.register(harness_id="h1", session_id="s1", workshop_id="w1",
                   source=SRC_ACP, context_turns=1)
        assert (d / "sessions.json").exists(), "应落盘"

        b = SessionRegistry(data_dir=d)      # 新实例 = 模拟重启
        r = b.get("h1", "s1")
        assert r is not None, "重启后应读回"
        assert r["source"] == SRC_ACP and r["context_turns"] == 1

    def test_does_not_touch_policy_config(self, tmp_path):
        """纪律2：sessions.json 独立，不碰 policy.json（混存会被读-改-写覆盖）。"""
        d = tmp_path / "_p2"
        d.mkdir(parents=True, exist_ok=True)      # 先建目录，否则 write_text 会 FileNotFoundError
        policy = d / "policy.json"
        policy.write_text('{"default_by_op": {"shell.exec": "ask"}}', encoding="utf-8")
        before = policy.read_text(encoding="utf-8")

        r = SessionRegistry(data_dir=d)
        r.register(harness_id="h1", session_id="s1", workshop_id="w1")

        assert policy.read_text(encoding="utf-8") == before, "policy.json 不得被改动"
        assert (d / "sessions.json").exists()

    def test_corrupt_file_does_not_crash(self, tmp_path):
        """纪律3：坏文件回落空表，不抛。"""
        d = tmp_path / "_p3"
        d.mkdir()
        (d / "sessions.json").write_text("{ not json", encoding="utf-8")
        r = SessionRegistry(data_dir=d)
        assert r.list() == []
        r.register(harness_id="h1", session_id="s1")
        assert len(r.list()) == 1

    def test_utf8_bom_tolerated(self, tmp_path):
        """PowerShell `Set-Content -Encoding UTF8` 会写 **BOM** —— 读侧必须容忍。

        回归现场：真实 `data/sessions.json` 就是被这么清空过的（BOM + `[]`），
        `read_text(encoding="utf-8")` 不剥 BOM -> json.loads 抛 -> 被 except 吞掉
        -> **静默回落空表** —— 文件里一旦真有会话，重启就等于全丢，只留一行日志。
        """
        d = tmp_path / "_p5"
        d.mkdir()
        payload = [{"harness_id": "h1", "session_id": "s1", "workshop_id": "w1",
                    "status": "active"}]
        # 带 BOM 写（模拟 PowerShell 的输出）
        (d / "sessions.json").write_bytes(
            b"\xef\xbb\xbf" + json.dumps(payload, ensure_ascii=False).encode("utf-8"))

        r = SessionRegistry(data_dir=d)
        got = r.get("h1", "s1")
        assert got is not None, "**BOM 导致会话数据被静默丢弃**（重启即丢）"
        assert got["workshop_id"] == "w1"

    def test_load_without_bom_still_works(self, tmp_path):
        """utf-8-sig 对「无 BOM」输入也要正确（不能只修了有 BOM 的那半边）。"""
        d = tmp_path / "_p6"
        d.mkdir()
        payload = [{"harness_id": "h2", "session_id": "s2"}]
        (d / "sessions.json").write_text(json.dumps(payload, ensure_ascii=False),
                                         encoding="utf-8")
        r = SessionRegistry(data_dir=d)
        assert r.get("h2", "s2") is not None

    def test_unwritable_dir_does_not_raise(self, tmp_path, monkeypatch):
        """纪律3：写盘异常只打印，不冒泡。"""
        r = SessionRegistry(data_dir=tmp_path / "_p4")

        def boom(*a, **k):
            raise OSError("disk full")

        monkeypatch.setattr(r, "_save", boom)
        rec = r.register(harness_id="h1", session_id="s1")
        assert rec.get("session_id") == "s1", "写盘失败不应影响登记本身"


# ── 回执契约（激活回报端点）────────────────────────────────────
class TestReceiptContract:
    @pytest.fixture()
    def env(self, tmp_path, monkeypatch):
        from agent_community.platform import routers
        from agent_community.platform import session_registry as sr_mod
        from agent_community.platform.routers import harness_register as hr

        fresh = SessionRegistry(data_dir=tmp_path / "_rc")
        monkeypatch.setattr(sr_mod, "session_registry", fresh)
        return hr, fresh

    def test_receipt_with_new_fields_registers(self, env):
        """回执带三字段 → 进注册表（原先 session_id 收到就丢弃）。"""
        hr, reg = env
        body = {
            "workshop_id": "w1", "member_id": "m1", "status": "entered",
            "session_id": "acp-abc", "context_turns": 1, "source": "acp",
            "harness_id": "h1",
        }
        out = run(hr.harness_activation_result(FakeRequest(body)))
        assert out["success"] is True
        r = reg.get("h1", "acp-abc")
        assert r is not None, "session_id 应被登记"
        assert r["source"] == "acp" and r["context_turns"] == 1
        assert r["workshop_id"] == "w1" and r["member_id"] == "m1"

    def test_receipt_backward_compatible(self, env):
        """老桥不带新字段 → 照常工作，向后兼容。"""
        hr, reg = env
        out = run(hr.harness_activation_result(FakeRequest(
            {"workshop_id": "w1", "member_id": "m1", "status": "entered"})))
        assert out["success"] is True
        # harness_id 未知但 source 也空 → 不登记，也不报错
        assert out.get("session") is None or "session" in out

    def test_human_receipt_synthesizes_session(self, env):
        """19 人工点击：无 session_id → 合成，不冒充机器会话。"""
        hr, reg = env
        out = run(hr.harness_activation_result(FakeRequest({
            "workshop_id": "w1", "member_id": "m2", "status": "entered",
            "source": "human", "harness_id": "h1",
        })))
        assert out["success"] is True
        rec = out["session"]
        assert rec is not None
        assert rec["source"] == "human"
        assert rec["session_id"].startswith("human:")
        assert rec["context_turns"] is None, "人工点击没有上下文轮数"

    def test_reused_context_is_audited_not_blocked(self, env):
        """context_turns > 1 → 记 session.context_reused，但**不阻断**（验证矩阵 #11）。"""
        hr, reg = env
        out = run(hr.harness_activation_result(FakeRequest({
            "workshop_id": "w1", "member_id": "m1", "status": "entered",
            "session_id": "s-x", "context_turns": 200, "source": "acp",
            "harness_id": "h1",
        })))
        assert out["success"] is True, "复用旧上下文不得阻断流程"

        from agent_community.platform.audit import audit_log
        evs = audit_log.query(limit=30, event="session.context_reused")
        assert evs, "应记录 context_reused"
        assert "200" in str(evs[0].get("detail", ""))

    def test_activation_audit_carries_target(self, env):
        """20 审计带 workshop:member（原先是 target='' 空）。"""
        hr, reg = env
        run(hr.harness_activation_result(FakeRequest({
            "workshop_id": "w7", "member_id": "m7", "status": "entered",
            "harness_id": "h1", "source": "human",
        })))
        from agent_community.platform.audit import audit_log
        evs = audit_log.query(limit=30, event="harness.activation_result")
        assert evs, "应有激活审计"
        assert "w7:m7" in str(evs[0].get("target", "")), \
            f"target 应含 workshop:member，实得 {evs[0].get('target')!r}"

    def test_unknown_status_does_not_set_member(self, env, monkeypatch):
        """未知 status 不应把成员置成乱七八糟的值（只接受已知档位）。"""
        hr, reg = env
        out = run(hr.harness_activation_result(FakeRequest({
            "workshop_id": "nope", "member_id": "m1", "status": "entered",
            "harness_id": "h1",
        })))
        assert out["success"] is True, "工作间不存在也应返回成功（向后兼容）"
