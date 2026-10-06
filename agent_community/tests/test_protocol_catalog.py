# -*- coding: utf-8 -*-
"""基座测试：`platform/protocol_catalog.py` + `routers/protocols.py`（V-11 协议目录）。

**为什么算"基层"**：这是平台对外协议描述的**单一事实源** ——
`GET /api/protocols`（前端渲染 + 外端自选）、A2A `supportedInterfaces[]`、
内部 `select_endpoint` 的"已实现"判断，全部派生自这一处。
设计原则原文：**"不撒谎"** —— 未实现的协议必须标 `not_implemented`
并**从选择池剔除**。一个手滑把 sse 标成 available，外端就会去连一个不存在的端点。
此前零测试。
"""
from __future__ import annotations

import asyncio

import pytest

from agent_community.platform import protocol_catalog as pc


def run(coro):
    return asyncio.run(coro)


class FakeRequest:
    def __init__(self, headers=None):
        self.headers = headers or {}


REQUIRED_KEYS = {"id", "name", "direction", "kind", "status",
                 "best_for", "example", "requires", "endpoint"}


# ══════════════════════════════════════════════════════════════════
# A. 目录结构
# ══════════════════════════════════════════════════════════════════
class TestCatalogShape:
    def test_all_entries_have_required_keys(self):
        for p in pc.get_protocol_catalog():
            missing = REQUIRED_KEYS - set(p)
            assert not missing, f"协议 {p.get('id')} 缺字段 {missing}"

    def test_ids_unique(self):
        ids = [p["id"] for p in pc.get_protocol_catalog()]
        assert len(ids) == len(set(ids)), f"协议 id 重复：{ids}"

    def test_expected_protocols_present(self):
        ids = {p["id"] for p in pc.get_protocol_catalog()}
        assert {"http", "ws", "mcp", "a2a", "acp", "pipe", "sse", "grpc"} <= ids

    def test_direction_values_valid(self):
        for p in pc.get_protocol_catalog():
            assert p["direction"] in ("inbound", "outbound", "bidirectional"), \
                f"{p['id']} direction 非法：{p['direction']}"

    def test_status_values_valid(self):
        allowed = {"available", "partial", "not_implemented", "disabled"}
        for p in pc.get_protocol_catalog():
            assert p["status"] in allowed, f"{p['id']} status 非法：{p['status']}"


# ══════════════════════════════════════════════════════════════════
# B. 运行时端点填充
# ══════════════════════════════════════════════════════════════════
class TestEndpointFilling:
    def test_host_and_port_are_used(self):
        cat = {p["id"]: p for p in pc.get_protocol_catalog(host="10.0.0.7", http_port=1234)}
        assert cat["http"]["endpoint"] == "http://10.0.0.7:1234/api"
        assert cat["ws"]["endpoint"] == "ws://10.0.0.7:1234/ws"

    def test_a2a_uses_fixed_port(self):
        cat = {p["id"]: p for p in pc.get_protocol_catalog(host="h", http_port=1)}
        assert "9104" in cat["a2a"]["endpoint"], "A2A 端口约定 9104"

    def test_mcp_is_stdio(self):
        cat = {p["id"]: p for p in pc.get_protocol_catalog()}
        assert cat["mcp"]["endpoint"].startswith("stdio:")

    def test_other_protocols_have_empty_endpoint(self):
        cat = {p["id"]: p for p in pc.get_protocol_catalog()}
        # ⚠️ 2026-10-06：`grpc` 移出本列表 —— 它现在**真有**网关（9105），
        #    端点不再属于"凭空造"；其端点是否给出由**可用性**决定
        #    （不可用时必须为空），断言见 TestRuntimeStatusIsDynamic。
        for pid in ("acp", "pipe", "sse"):
            assert cat[pid]["endpoint"] == "", f"{pid} 不应凭空造端点"

    def test_default_host_port(self):
        cat = {p["id"]: p for p in pc.get_protocol_catalog()}
        assert "127.0.0.1:18920" in cat["http"]["endpoint"]


# ══════════════════════════════════════════════════════════════════
# C. 「不撒谎」：可用性判定 + 选择池
# ══════════════════════════════════════════════════════════════════
class TestAvailabilityHonesty:
    def test_not_implemented_excluded_from_pool(self):
        """SSE 未实现 —— 绝不能出现在可选择的协议池里，否则外端会连空端点。"""
        ids = pc.get_available_protocol_ids()
        assert "sse" not in ids, "未实现的 SSE 混进了选择池（违反『不撒谎』）"

    def test_partial_excluded_from_pool(self):
        """ws 是半实现（status=partial），同样不算 available。"""
        assert "ws" not in pc.get_available_protocol_ids()

    def test_core_protocols_are_selectable(self):
        ids = set(pc.get_available_protocol_ids())
        assert {"http", "mcp", "a2a", "acp", "pipe"} <= ids

    def test_grpc_status_tracks_dependency(self):
        """gRPC 状态必须**跟随环境**：装了 grpcio 才 available，否则 disabled。"""
        cat = {p["id"]: p for p in pc.get_protocol_catalog()}
        if pc._grpc_available():
            assert cat["grpc"]["status"] == "available"
            assert "grpc" in pc.get_available_protocol_ids()
        else:
            assert cat["grpc"]["status"] == "disabled"
            assert "grpc" not in pc.get_available_protocol_ids()

    def test_pool_ids_all_available(self):
        """选择池里的每个 id，其目录状态都必须是 available（防两处漂移）。"""
        cat = {p["id"]: p for p in pc.get_protocol_catalog()}
        for pid in pc.get_available_protocol_ids():
            assert cat[pid]["status"] == "available", \
                f"{pid} 在池里但状态是 {cat[pid]['status']}"


# ══════════════════════════════════════════════════════════════════
# D. 不得被调用方污染（每次返回副本）
# ══════════════════════════════════════════════════════════════════
class TestNoCallerMutation:
    def test_returned_entries_do_not_alias_module_state(self):
        """调用方改了返回值，第二次调用必须还是干净的 —— 否则是进程级串味。"""
        first = pc.get_protocol_catalog()
        first[0]["id"] = "TAMPERED"
        first[0]["status"] = "broken"
        second = pc.get_protocol_catalog()
        assert second[0]["id"] == "http"
        assert second[0]["status"] == "available"

    def test_repeated_calls_are_stable(self):
        a = pc.get_protocol_catalog()
        b = pc.get_protocol_catalog()
        assert [p["id"] for p in a] == [p["id"] for p in b]


# ══════════════════════════════════════════════════════════════════
# E. 端点路由
# ══════════════════════════════════════════════════════════════════
class TestProtocolsRoute:
    def test_route_path_registered(self):
        from agent_community.platform.routers import protocols as mod

        assert {r.path for r in mod.router.routes} == {"/api/protocols"}

    def test_returns_total_matching_len(self):
        from agent_community.platform.routers import protocols as mod

        out = run(mod.list_protocols(FakeRequest({"host": "127.0.0.1:18920"})))
        assert out["total"] == len(out["protocols"]) == len(pc.get_protocol_catalog())

    def test_host_header_strips_port(self):
        from agent_community.platform.routers import protocols as mod

        out = run(mod.list_protocols(FakeRequest({"host": "192.168.1.5:18920"})))
        http = {p["id"]: p for p in out["protocols"]}["http"]
        assert http["endpoint"].startswith("http://192.168.1.5:"), \
            "host 头里的端口应被剥离（端口由平台自己填）"

    def test_survives_missing_host_header(self):
        from agent_community.platform.routers import protocols as mod

        out = run(mod.list_protocols(FakeRequest({})))
        assert out["total"] > 0


# ── 运行时状态契约（2026-10-06 加：装完 grpcio 却不重启也要能反映）────────
class TestRuntimeStatusIsDynamic:
    """`_PROTOCOLS` 里的 status 是**导入时**快照；凡"随环境变化"的状态必须调用时算。

    事故：装好 grpcio 后，**运行中的**平台仍一直报 gRPC `disabled`（要重启才变），
    与 `_grpc_available()` docstring 承诺的"随环境变化"不符。
    """

    def test_grpc_status_follows_availability_without_reload(self, monkeypatch):
        monkeypatch.setattr(pc, "_grpc_available", lambda: False)
        row = [p for p in pc.get_protocol_catalog() if p["id"] == "grpc"][0]
        assert row["status"] == "disabled"
        assert "grpc" not in pc.get_available_protocol_ids()

        monkeypatch.setattr(pc, "_grpc_available", lambda: True)
        row = [p for p in pc.get_protocol_catalog() if p["id"] == "grpc"][0]
        assert row["status"] == "available"
        assert "grpc" in pc.get_available_protocol_ids()

    def test_grpc_endpoint_filled(self):
        row = [p for p in pc.get_protocol_catalog(host="127.0.0.1") if p["id"] == "grpc"][0]
        # 端点与可用性一致：可用 -> 给 9105；不可用 -> 必须为空（不凭空造端点）
        if row["status"] == "available":
            assert row["endpoint"] == "127.0.0.1:9105", "gRPC 网关默认端口 9105 必须出现在端点里"
        else:
            assert row["endpoint"] == "", "gRPC 不可用时不得给出端点"

    def test_other_protocols_stay_static(self, monkeypatch):
        """只有 grpc 是运行时状态；别人的静态判定不得被这条改动带偏。"""
        monkeypatch.setattr(pc, "_grpc_available", lambda: True)
        got = {p["id"]: p["status"] for p in pc.get_protocol_catalog()}
        assert got["http"] == "available"
        assert got["sse"] == "not_implemented", "未实现的 SSE 不能因为 grpc 可用就被算成可用"
