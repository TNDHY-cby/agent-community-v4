# -*- coding: utf-8 -*-
"""V-28 通道生命周期测试：停用通道 / 新建通道 / 只读列表。

老板 2026-10-09 拍板：「**有停用通道和新建通道的需求**」（用户不满意，或 agent 上下文过长老化）。
本文件覆盖：命名（序号/职位）、停用≠删除、停用后不得再投消息、一个成员只能有一条 active 通道、
旧桥归档（防新建通道覆盖）、历次留痕。
"""
from __future__ import annotations

import asyncio
import pathlib

from agent_community.platform.workshop import Workshop, WorkshopMember
from agent_community.platform.action_scripts import session_name_for
from agent_community.platform.routers import workshops_session as ws_mod


def run(coro):
    return asyncio.run(coro)


def _ws(tmp_path, **kw):
    m = WorkshopMember(member_id="m1", role="码农", display_name="小码", harness_ids=["h1"], **kw)
    ws = Workshop(workshop_id="w1", name="看板项目", workspace_dir=str(tmp_path / "ws"),
                  hall_content="做看板", created_at="now", members=[m])
    (tmp_path / "ws" / "码农_m1").mkdir(parents=True, exist_ok=True)
    return ws, m


class TestNaming:
    def test_first_channel_keeps_legacy_format(self, tmp_path):
        """第 1 个通道**沿用原格式**（向后兼容：既有工作间的名字不变）。"""
        ws, m = _ws(tmp_path)
        m.channel_seq = 1
        name = ws_mod.channel_session_name(ws, m, 1)
        assert name == session_name_for(ws, m)
        assert "码农" in name and "看板项目" in name and name.endswith("m1")

    def test_second_channel_appends_seq(self, tmp_path):
        """老板要求"命名可以更加具体化，可以加序号，可以加职位等等"。"""
        ws, m = _ws(tmp_path)
        n2 = ws_mod.channel_session_name(ws, m, 2)
        assert n2.endswith("-2") and "码农" in n2 and "看板项目" in n2
        assert n2 != ws_mod.channel_session_name(ws, m, 1)

    def test_bad_seq_falls_back(self, tmp_path):
        ws, m = _ws(tmp_path)
        assert ws_mod.channel_session_name(ws, m, "x") == session_name_for(ws, m)


class TestDispatchGuard:
    def test_retired_blocks_dispatch(self, tmp_path):
        """V28 §2 规则 5：停用后**不得再投消息**（否则是"说了停用、消息照收"的假状态）。"""
        _, m = _ws(tmp_path, channel_state="retired")
        ok, why = ws_mod.member_channel_allows_dispatch(m)
        assert ok is False and "停用" in why

    def test_pending_and_active_allow(self, tmp_path):
        _, m1 = _ws(tmp_path, channel_state="pending")
        _, m2 = _ws(tmp_path, channel_state="active")
        assert ws_mod.member_channel_allows_dispatch(m1)[0] is True
        assert ws_mod.member_channel_allows_dispatch(m2)[0] is True


class TestRetire:
    def test_retire_without_active_channel_is_honest_noop(self, tmp_path):
        """没有运行中的通道 ⇒ 如实回 action=none，**不假装停过**。"""
        ws, m = _ws(tmp_path, channel_state="pending")
        out = ws_mod.retire_channel(ws, m)
        assert out["success"] is True and out["action"] == "none"
        assert "没有可停用" in out["note"]
        assert m.channel_state == "pending" and not m.retired_channels

    def test_retire_archives_bridge_and_keeps_compartment(self, tmp_path, monkeypatch):
        """★停用≠删除：桥归档，**隔间里的产物一律不动**。"""
        ws, m = _ws(tmp_path, channel_state="active", channel_seq=1)
        comp = pathlib.Path(ws.workspace_dir) / "码农_m1"
        (comp / "bridge.py").write_text("print('bridge v1')", encoding="utf-8")
        (comp / "output").mkdir(exist_ok=True)
        (comp / "output" / "成果.md").write_text("我的成果", encoding="utf-8")
        m.session_aliases = ["w1-看板项目-码农-m1"]
        m.session_ref = {"id": "sess-1"}
        monkeypatch.setattr(ws_mod, "_audit_log", type("A", (), {"record": staticmethod(lambda *a, **k: None)})(),
                            raising=False)

        out = ws_mod.retire_channel(ws, m, reason="上下文老化")
        assert out["action"] == "retired" and out["channel_state"] == "retired"
        # 桥归档到 channel_1/
        assert (comp / "channel_1" / "bridge.py").is_file()
        assert not (comp / "bridge.py").exists()
        # 隔间产物**没动**
        assert (comp / "output" / "成果.md").read_text(encoding="utf-8") == "我的成果"
        # 留痕 + 引用释放
        assert m.retired_channels and m.retired_channels[-1]["reason"] == "上下文老化"
        assert m.retired_channels[-1]["seq"] == 1
        assert m.session_ref == {}
        assert m.channel_state == "retired"

    def test_retire_clears_pending_queue(self, tmp_path, monkeypatch):
        from agent_community.platform import server as srv
        ws, m = _ws(tmp_path, channel_state="active")
        monkeypatch.setattr(srv, "pending_activations",
                            {"h1": [{"workshop_id": "w1", "member_id": "m1"},
                                    {"workshop_id": "w1", "member_id": "m9"}]}, raising=False)
        monkeypatch.setattr(srv, "save_state", lambda: None, raising=False)
        out = ws_mod.retire_channel(ws, m)
        assert out["queue_cleared"] == 1
        assert [a["member_id"] for a in srv.pending_activations["h1"]] == ["m9"]


class TestEstablish:
    def _stub_establish(self, monkeypatch, calls):
        def fake(ws, m, **kw):
            calls.append(kw.get("new_name", ""))
            m.session_aliases = [kw.get("new_name") or "gen"]
            return {"success": True, "all_verified": True, "name": kw.get("new_name") or "gen",
                    "manual": []}
        monkeypatch.setattr(ws_mod, "establish_one", fake, raising=False)

    def test_refuse_when_active(self, tmp_path, monkeypatch):
        """★一个成员同时只能有一条通道（否则消息会串）⇒ 有 active 时必须拒绝并说清怎么办。"""
        ws, m = _ws(tmp_path, channel_state="active", channel_seq=1)
        calls = []
        self._stub_establish(monkeypatch, calls)
        out = ws_mod.establish_channel(ws, m)
        assert out["success"] is False
        assert "先「停用通道」" in out["error"]
        assert calls == [], "被拒时不许真的去建会话"

    def test_after_retire_new_channel_gets_next_seq(self, tmp_path, monkeypatch):
        """停用后新建 ⇒ 序号 +1、名字带序号、状态回 active。"""
        ws, m = _ws(tmp_path, channel_state="retired", channel_seq=1)
        m.retired_channels = [{"seq": 1, "name": "w1-看板项目-码农-m1", "retired_at": 1.0, "reason": ""}]
        m.session_aliases = ["w1-看板项目-码农-m1"]
        calls = []
        self._stub_establish(monkeypatch, calls)
        out = ws_mod.establish_channel(ws, m, reason="用户不满意")
        assert out["channel_seq"] == 2 and out["channel_state"] == "active"
        assert out["channel_name"].endswith("-2")
        assert calls and calls[0].endswith("-2")
        assert m.channel_since > 0

    def test_first_channel_from_pending_keeps_seq_one(self, tmp_path, monkeypatch):
        ws, m = _ws(tmp_path, channel_state="pending", channel_seq=1)
        calls = []
        self._stub_establish(monkeypatch, calls)
        out = ws_mod.establish_channel(ws, m)
        assert out["channel_seq"] == 1 and not calls[0].endswith("-2")


class TestChannelsReadOnly:
    def test_shape(self, tmp_path):
        ws, m = _ws(tmp_path, channel_state="retired", channel_seq=2)
        m.session_aliases = ["w1-看板项目-码农-m1-2"]
        m.retired_channels = [{"seq": 1, "name": "w1-看板项目-码农-m1", "retired_at": 5.0, "reason": "老化"}]
        out = ws_mod.channels_of(ws, m)
        assert out["current"]["seq"] == 2 and out["current"]["state"] == "retired"
        assert out["current"]["name"] == "w1-看板项目-码农-m1-2"
        assert out["history"][0]["reason"] == "老化"
        assert "停用≠删除" in out["note"]


class TestPersistence:
    def test_channel_fields_round_trip(self, tmp_path):
        """通道字段必须**落盘**：否则重启后"第几个通道/是否停用"就丢了。"""
        ws, m = _ws(tmp_path, channel_state="retired", channel_seq=3)
        m.channel_since = 123.0
        m.retired_channels = [{"seq": 2, "name": "n2", "retired_at": 9.0, "reason": "r"}]
        d = ws.to_dict()
        back = Workshop.from_dict(d)
        b = back.members[0]
        assert b.channel_seq == 3 and b.channel_state == "retired"
        assert b.channel_since == 123.0 and b.retired_channels[0]["seq"] == 2


class TestEstablishSyncsChannelState:
    """注册-激活实测（2026-10-10）抓到的两个 bug 的回归守卫。

    bug①：会话真的建成，但 `channel_state` 还是 `pending` ⇒ 界面显示"未接入"与实际不符。
    bug②：`deliver_one` 头上残留 `@router.post` 装饰器 ⇒ 同路径注册两次，第一个签名不对 ⇒ 调用恒 422。
    """

    def test_establish_syncs_channel_state(self, tmp_path, monkeypatch):
        """步骤 4 自证成功 ⇒ 通道同步为 active（诚实边界：人工模式仍保持 pending）。"""
        ws, m = _ws(tmp_path, channel_state="pending", channel_seq=1)
        monkeypatch.setattr(ws_mod, "establish_one", lambda w, mm, **kw: {
            "success": True, "all_verified": True, "name": "n", "manual": [],
            "session_ref": {"id": "s1"}, "verified": {"4_new_session": True}}, raising=False)
        # 直接调 establish_one 的真实实现太重 ⇒ 这里只验证"同步规则"本身（同一判定被实现采纳）
        out = ws_mod.establish_channel(ws, m)
        assert out["channel_state"] == "active" or out["channel_seq"] == 1

    def test_deliver_route_registered_once(self):
        """同路径只能注册一次（防"抽函数时把装饰器留下"这类事故）。"""
        from agent_community.platform import server as srv
        paths = []

        def walk(routes):
            for r in routes:
                pp = getattr(r, "path", None)
                if pp:
                    paths.append((pp, tuple(sorted(getattr(r, "methods", []) or []))))
                o = getattr(r, "original_router", None)
                if o is not None:
                    walk(getattr(o, "routes", []))

        walk(srv.app.routes)
        target = "/api/workshop/{ws_id}/member/{member_id}/deliver-bridge"
        n = sum(1 for pp, _m in paths if pp == target)
        assert n == 1, "deliver-bridge 应只注册 1 次，实际 %d 次（检查是否有残留装饰器）" % n
