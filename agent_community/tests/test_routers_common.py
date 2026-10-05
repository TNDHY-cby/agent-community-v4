# -*- coding: utf-8 -*-
"""基座测试：两个 router `*_common` 共享辅助模块（V-13 拆分的底座）。

**为什么算"基层"**：V-13 把 `workshops.py` / `harness.py` 拆成子模块后，
四个 workshops 子模块、三个 harness 子模块**彼此不互相调用**，全部只依赖
各自 `*_common`。也就是说这两个 `_common` 是**七个业务模块的唯一共享面** ——
它们坏了，整组端点一起坏。此前零测试。

本文件锁死：
- `identity` 准入闸门在 harness 侧的接线（401/403 与"未知 harness 放行"的区别）
- MCP 工具动态拉取的**失败不阻断**语义
- 组长派工 payload 的结构与回报要求（外端按这个文本工作）
"""
from __future__ import annotations

import asyncio

import pytest

from agent_community.platform.harness_adapter import harness_manager
from agent_community.platform.protocol import HarnessAI, HarnessInfo, HarnessTool, WakeupMethod
from agent_community.platform.workshop import Workshop, WorkshopMember


def run(coro):
    return asyncio.run(coro)


class Headers(dict):
    def get(self, key, default=None):
        want = str(key).lower()
        for k, v in self.items():
            if str(k).lower() == want:
                return v
        return default


def _ws(wid="w1"):
    return Workshop(workshop_id=wid, name="测试间", workspace_dir=r"D:\ws\ws_w1",
                    hall_content="把首页做出来")


# ══════════════════════════════════════════════════════════════════
# A. workshops_common
# ══════════════════════════════════════════════════════════════════
class TestWorkshopsCommon:
    def test_pick_member_empty_id_returns_none(self):
        from agent_community.platform.routers import workshops_common as wc

        ws = _ws()
        ws.members = [WorkshopMember(member_id="m1", role="员工", display_name="A")]
        assert wc._leader_pick_member(ws, "") is None

    def test_pick_member_found(self):
        from agent_community.platform.routers import workshops_common as wc

        ws = _ws()
        m = WorkshopMember(member_id="m2", role="组长", display_name="B")
        ws.members = [WorkshopMember(member_id="m1", role="员工", display_name="A"), m]
        assert wc._leader_pick_member(ws, "m2") is m

    def test_pick_member_missing_returns_none(self):
        from agent_community.platform.routers import workshops_common as wc

        ws = _ws()
        ws.members = [WorkshopMember(member_id="m1", role="员工", display_name="A")]
        assert wc._leader_pick_member(ws, "nope") is None

    def test_member_hid_first_or_empty(self):
        from agent_community.platform.routers import workshops_common as wc

        assert wc._leader_member_hid(
            WorkshopMember(member_id="m", role="r", display_name="d",
                           harness_ids=["示例Harness-A", "示例Harness-B"])
        ) == "示例Harness-A", "应取首个 harness_id"
        assert wc._leader_member_hid(
            WorkshopMember(member_id="m", role="r", display_name="d")) == "", \
            "未绑定 harness 应返回空串而非 None（拼进 JSON 不能是 null）"

    @pytest.mark.parametrize("kind,expected_type", [
        ("assign", "leader_assign"),
        ("redirect", "leader_redirect"),
    ])
    def test_assign_payload_type_mapping(self, kind, expected_type):
        from agent_community.platform.routers import workshops_common as wc

        ws = _ws()
        m = WorkshopMember(member_id="m1", role="员工", display_name="A",
                           harness_ids=["示例Harness-A"])
        p = wc._leader_build_assign_payload(ws, m, "做首页", "", kind)
        assert p["type"] == expected_type
        assert p["kind"] == kind

    def test_assign_payload_carries_work_context(self):
        """外端按这个 payload 干活：指令 / 工作区 / 大厅原文 缺一不可。"""
        from agent_community.platform.routers import workshops_common as wc

        ws = _ws()
        m = WorkshopMember(member_id="m7", role="码农", display_name="A",
                           harness_ids=["示例Harness-A"])
        p = wc._leader_build_assign_payload(ws, m, "实现登录页", "注意鉴权", "assign")

        assert p["workshop_id"] == ws.workshop_id
        assert p["member_id"] == "m7" and p["role"] == "码农"
        assert p["workspace_dir"] == ws.workspace_dir
        assert p["instruction"] == "实现登录页"
        assert p["report_endpoint"] == "/api/harness/task-result"
        for needle in ("实现登录页", "注意鉴权", ws.workspace_dir, ws.hall_content):
            assert needle in p["message"], f"message 缺关键内容：{needle}"

    def test_assign_payload_omits_empty_note(self):
        from agent_community.platform.routers import workshops_common as wc

        ws = _ws()
        m = WorkshopMember(member_id="m1", role="员工", display_name="A")
        p = wc._leader_build_assign_payload(ws, m, "干活", "", "assign")
        assert "【组长备注】" not in p["message"], "空备注不该留空标签"

    def test_assign_payload_requires_report(self):
        """回报要求必须写进文本：外端不回，用户就看不到结果（V-15 反复强调）。"""
        from agent_community.platform.routers import workshops_common as wc

        ws = _ws()
        m = WorkshopMember(member_id="m1", role="员工", display_name="A",
                           harness_ids=["示例Harness-A"])
        p = wc._leader_build_assign_payload(ws, m, "干活", "", "assign")
        assert "task-result" in p["message"]
        assert "result" in p["message"], "必须说明把回复放进 result 字段"
        assert "示例Harness-A" in p["message"], "回报体里应带 harness_id"

    def test_platform_base_url_safe_returns_string(self):
        """取不到活跃 server 时必须回退默认地址，不得抛异常。"""
        from agent_community.platform.routers import workshops_common as wc

        val = wc._platform_base_url_safe()
        assert isinstance(val, str) and val.startswith("http")


# ══════════════════════════════════════════════════════════════════
# B. harness_common —— 准入闸门
# ══════════════════════════════════════════════════════════════════
def _register(hid, metadata=None):
    info = HarnessInfo(harness_id=hid, harness_name=hid,
                       wakeup_method=WakeupMethod.CLIPBOARD,
                       ai=HarnessAI(model_name="示例模型"),
                       metadata=metadata or {})
    return harness_manager.register(info)[0]


@pytest.fixture()
def clean_sessions(monkeypatch):
    monkeypatch.setattr(harness_manager, "sessions", {}, raising=False)
    monkeypatch.setattr(harness_manager, "bridges", {}, raising=False)
    monkeypatch.setattr(harness_manager, "id_to_harness", {}, raising=False)
    yield


class TestHarnessIdentityGate:
    def test_empty_harness_id_passes(self, clean_sessions):
        from agent_community.platform.routers import harness_common as hc

        assert hc._harness_identity_check("", Headers({})) is None

    def test_unknown_harness_passes_to_legacy_logic(self, clean_sessions):
        """未知 harness 不在这里拦 —— 交由既有 404 逻辑处理，避免行为漂移。"""
        from agent_community.platform.routers import harness_common as hc

        assert hc._harness_identity_check("h-ghost", Headers({})) is None

    def test_harness_without_token_passes(self, clean_sessions):
        from agent_community.platform.routers import harness_common as hc

        _register("h-legacy", metadata={})
        assert hc._harness_identity_check("h-legacy", Headers({})) is None

    def test_missing_header_returns_401(self, clean_sessions):
        from agent_community.platform.routers import harness_common as hc

        _register("h-sec", metadata={"agent_token": "issued"})
        resp = hc._harness_identity_check("h-sec", Headers({}))
        assert resp is not None and resp.status_code == 401

    def test_wrong_token_returns_403(self, clean_sessions):
        from agent_community.platform.routers import harness_common as hc

        _register("h-sec", metadata={"agent_token": "issued"})
        resp = hc._harness_identity_check("h-sec", Headers({"X-Agent-Token": "0" * 64}))
        assert resp is not None and resp.status_code == 403

    def test_correct_token_passes(self, clean_sessions):
        from agent_community.platform.identity import issue_agent_token
        from agent_community.platform.routers import harness_common as hc

        _register("h-sec", metadata={"agent_token": "issued"})
        tok = issue_agent_token("h-sec")
        assert hc._harness_identity_check(
            "h-sec", Headers({"X-Agent-Token": tok})) is None

    def test_tolerates_none_metadata(self, clean_sessions):
        """metadata 为 None 不得抛 —— 旧存量条目可能是空的。"""
        from agent_community.platform.routers import harness_common as hc

        sess = _register("h-nometa")
        sess.info.metadata = None
        assert hc._harness_identity_check("h-nometa", Headers({})) is None


# ══════════════════════════════════════════════════════════════════
# C. harness_common —— MCP 工具动态拉取
# ══════════════════════════════════════════════════════════════════
class TestMaybePullMcpTools:
    def _info(self, **kw):
        base = dict(harness_id="h1", harness_name="h1",
                    wakeup_method=WakeupMethod.MCP, api_base_url="http://127.0.0.1:9/mcp",
                    ai=HarnessAI(model_name="示例模型"))
        base.update(kw)
        return HarnessInfo(**base)

    def test_non_mcp_untouched(self):
        from agent_community.platform.routers import harness_common as hc

        info = self._info(wakeup_method=WakeupMethod.CLIPBOARD)
        out = run(hc._maybe_pull_mcp_tools(info))
        assert out is info and out.tools == []

    def test_mcp_without_base_url_untouched(self):
        from agent_community.platform.routers import harness_common as hc

        info = self._info(api_base_url="   ")
        assert run(hc._maybe_pull_mcp_tools(info)).tools == []

    def test_already_has_tools_skips_fetch(self, monkeypatch):
        """已声明工具就不再拉 —— 否则注册被远端拖慢。"""
        from agent_community.platform.routers import harness_common as hc
        from agent_community.platform import mcp_client

        called = []
        monkeypatch.setattr(mcp_client, "mcp_list_tools",
                            lambda url: called.append(url) or [], raising=True)
        info = self._info(tools=[HarnessTool(name="t1")])
        run(hc._maybe_pull_mcp_tools(info))
        assert called == [], "已有 tools 时不应触发远端调用"

    def test_fetch_populates_tools(self, monkeypatch):
        from agent_community.platform import mcp_client
        from agent_community.platform.routers import harness_common as hc

        async def fake(url):
            return [
                {"name": "search", "description": "检索",
                 "inputSchema": {"type": "object"}, "capability_tag": "分析"},
                {"name": "write_file", "description": "写文件"},
            ]

        monkeypatch.setattr(mcp_client, "mcp_list_tools", fake, raising=True)
        info = self._info()
        out = run(hc._maybe_pull_mcp_tools(info))
        assert [t.name for t in out.tools] == ["search", "write_file"]
        assert out.tools[0].parameters == {"type": "object"}
        assert out.tools[0].capability_tag == "分析"
        assert out.tools[1].parameters == {}, "缺 inputSchema 应回落空 dict 而非 None"

    def test_fetch_failure_does_not_block(self, monkeypatch):
        """远端挂了不得阻断注册（设计明写：失败按无工具外端对待）。"""
        from agent_community.platform import mcp_client
        from agent_community.platform.routers import harness_common as hc

        async def boom(url):
            raise RuntimeError("connection refused")

        monkeypatch.setattr(mcp_client, "mcp_list_tools", boom, raising=True)
        info = self._info()
        out = run(hc._maybe_pull_mcp_tools(info))
        assert out is info and out.tools == []

    def test_garbage_entries_skipped(self, monkeypatch):
        """脏数据（非 dict / 无 name）必须跳过，不能造出 name=None 的工具。"""
        from agent_community.platform import mcp_client
        from agent_community.platform.routers import harness_common as hc

        async def fake(url):
            return ["not-a-dict", {"description": "无名字"}, {"name": "ok"}]

        monkeypatch.setattr(mcp_client, "mcp_list_tools", fake, raising=True)
        out = run(hc._maybe_pull_mcp_tools(self._info()))
        assert [t.name for t in out.tools] == ["ok"]
