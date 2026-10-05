# -*- coding: utf-8 -*-
"""V-15 步骤 8 + 9 测试：会话可见性端点 + 三处关闭链路 + 前端「确认已进入」接线。

**这两步此前为零覆盖**（实现来自并行会话，2026-10-05 由 `7c99828` 代为提交，
交接说明书 §2 明确要求「接力者第一步：补步骤 8/9 单测」）。本文件即该补测。

覆盖矩阵（实现位置 → 断言）：

步骤 8 ── 会话可见性
    A. `GET /api/sessions`（`routers/sessions.py`）
       - 只返回**未关闭**会话（closed 必须被 `only_open` 排除）
       - `harness_id` / `workshop_id` 过滤生效、可叠加
       - 计数四件套与 `total_count` 自洽
       - 空过滤 = 全量活跃会话
       - `session.list` 审计（只读查询也要留痕）
       - **路由已挂进 app**（聚合器改了但没 include 是典型静默失败）
    B. 关闭链路①：注销 harness（`harness_register.unregister_harness`）
       → 该 harness 会话全关并移出注册表 + 响应带 `sessions_closed` + `session.close` 审计
    C. 关闭链路②：删除工作间（`workshops_lifecycle.delete_workshop`）
       → 该工作间会话转 `close_requested`（**不是** closed：等 harness 自述）+ 响应字段 + 审计
    D. 关闭链路③：stale 回收（`workshops_lifecycle.recycle_stale_workshops`）
       → 与②同语义，逐条挂 `sessions_close_requested`

步骤 9 ── 前端接线（静态断言，workshop.html）
    - `#sessionCountLine` 容器 + `GET /api/sessions?workshop_id=` 调用
    - 「确认已进入」按钮 → `POST /api/harness/activation-result`，`source=human`
    - `CONFIRMING` 全局登记表 + 模板渲染中间态（轮询防护，§4.3 教训）
    - 按钮态 `<button`/`</button>` 配平（模板字符串拼接最容易漏闭合）

数据隔离：本文件不碰真实 data/ —— 注册表实例全部落 tmp_path，
`state.workshops` / `harness_manager` 的改动由 conftest 的 `guard_real_data_dir` 兜底。
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from agent_community.platform import state as state_mod
from agent_community.platform.harness_adapter import harness_manager
from agent_community.platform.protocol import HarnessAI, HarnessInfo, WakeupMethod
from agent_community.platform.session_registry import (
    ST_ACTIVE,
    ST_CLOSE_REQ,
    ST_CLOSED,
    ST_IDLE,
    SessionRegistry,
)
from agent_community.platform.server import app

REPO_ROOT = Path(__file__).resolve().parents[2]


def run(coro):
    return asyncio.run(coro)


def unpack(resp):
    """端点可能返回 dict，也可能是 Utf8JSONResponse（错误分支）。"""
    if hasattr(resp, "status_code") and hasattr(resp, "body"):
        return resp.status_code, json.loads(resp.body)
    return 200, resp


# ══════════════════════════════════════════════════════════════════
# 现状守卫：确认「零覆盖」这件事在被测代码里仍有落点
# ══════════════════════════════════════════════════════════════════
def _mounted_paths():
    """列出 app 上**真实可达**的全部路径。

    ⚠️ 不能直接遍历 `app.routes` 取 `.path`：本项目实测 **fastapi 0.141.1 /
    starlette 1.7.0** 下，`app.include_router(r)` 往 `app.routes` 里放的是
    **`fastapi.routing._IncludedRouter`**（惰性对象，`.path is None`），
    真实路由挂在它的 `.original_router` 上 —— 只读 `.path` 会**漏掉全部
    include_router 挂载的端点**（`/api/sessions` `/api/policy` `/api/audit`
    `/api/protocols` `/api/workshops` … 统统看不到），而服务其实是好的。
    `router.routes.extend(sub.routes)` 这种**手动聚合器**才会直接出现在 `app.routes`。
    """
    paths = set()
    for r in app.routes:
        if type(r).__name__ == "_IncludedRouter":
            for sub in getattr(getattr(r, "original_router", None), "routes", []):
                paths.add(getattr(sub, "path", ""))
        else:
            paths.add(getattr(r, "path", ""))
    return paths


class TestWiringGuard:
    def test_sessions_endpoint_mounted_on_app(self):
        """`/api/sessions` 必须真挂进 app —— 写好了却没 include 是静默失败。"""
        paths = _mounted_paths()
        assert "/api/sessions" in paths, (
            "server.py 未 include _sessions_router —— 端点写好了但访问不到"
        )
        assert "/api/workshops" in paths, "同一机制下其它 router 也应在（防守卫本身失真）"

    def test_sessions_router_has_single_endpoint(self):
        from agent_community.platform.routers import sessions as s_mod

        assert {r.path for r in s_mod.router.routes} == {"/api/sessions"}


# ══════════════════════════════════════════════════════════════════
# 数据隔离夹具
# ══════════════════════════════════════════════════════════════════
@pytest.fixture()
def registry(tmp_path, monkeypatch):
    """全新的隔离注册表，并替换掉模块级单例（生产代码全都是 `from ..session_registry import session_registry`）。"""
    from agent_community.platform import session_registry as sr_mod

    reg = SessionRegistry(data_dir=tmp_path / "_sessions")
    monkeypatch.setattr(sr_mod, "session_registry", reg)
    return reg


@pytest.fixture()
def clean_harness_state(monkeypatch):
    """替换 harness_manager 的会话/桥表为空 dict + 清空 state.workshops。

    ⚠️ 必须 `monkeypatch.setattr` 换**新 dict**，不能就地 clear()：
    就地清空会把真实运行态（服务同机跑着）一起抹掉。
    """
    monkeypatch.setattr(harness_manager, "sessions", {}, raising=False)
    monkeypatch.setattr(harness_manager, "bridges", {}, raising=False)
    monkeypatch.setattr(harness_manager, "id_to_harness", {}, raising=False)
    saved = dict(state_mod.workshops)
    state_mod.workshops.clear()
    yield
    state_mod.workshops.clear()
    state_mod.workshops.update(saved)


def _seed(reg: SessionRegistry, **kw):
    base = dict(
        harness_id="h1", session_id="s1", workshop_id="w1", member_id="m1",
        source="acp", context_turns=1,
    )
    base.update(kw)
    return reg.register(**base)


# ══════════════════════════════════════════════════════════════════
# A. GET /api/sessions
# ══════════════════════════════════════════════════════════════════
class TestSessionsEndpoint:
    def test_empty_registry_returns_zeroed_envelope(self, registry):
        from agent_community.platform.routers import sessions as s_mod

        out = run(s_mod.list_sessions())
        assert out["success"] is True
        assert out["count"] == 0 and out["sessions"] == []
        assert out["active_count"] == 0 and out["total_count"] == 0

    def test_closed_session_excluded_from_list_but_counted_in_total(self, registry):
        """closed 必须被 only_open 挡掉；但 total_count 是**全量**（含 closed）。"""
        from agent_community.platform.routers import sessions as s_mod

        _seed(registry, session_id="s-open")
        _seed(registry, session_id="s-closed", member_id="m2")
        registry.set_status("h1", "s-closed", ST_CLOSED)

        out = run(s_mod.list_sessions())
        ids = [r["session_id"] for r in out["sessions"]]
        assert ids == ["s-open"], f"closed 不应出现在列表里，实得 {ids}"
        assert out["count"] == 1
        assert out["total_count"] == 2, "total_count 应含已关闭会话"
        assert out["active_count"] == 1

    def test_harness_filter(self, registry):
        from agent_community.platform.routers import sessions as s_mod

        _seed(registry, harness_id="h1", session_id="s-a")
        _seed(registry, harness_id="h2", session_id="s-b")

        out = run(s_mod.list_sessions(harness_id="h2"))
        assert [r["session_id"] for r in out["sessions"]] == ["s-b"]
        assert out["count"] == 1
        # 过滤只影响列表，**计数仍是全局的**（界面同时要显示全局态势）
        assert out["total_count"] == 2

    def test_workshop_filter(self, registry):
        from agent_community.platform.routers import sessions as s_mod

        _seed(registry, workshop_id="w1", session_id="s-w1")
        _seed(registry, workshop_id="w2", session_id="s-w2", member_id="m2")

        out = run(s_mod.list_sessions(workshop_id="w2"))
        assert [r["session_id"] for r in out["sessions"]] == ["s-w2"]

    def test_filters_are_intersecting(self, registry):
        from agent_community.platform.routers import sessions as s_mod

        _seed(registry, harness_id="h1", workshop_id="w1", session_id="hit")
        _seed(registry, harness_id="h1", workshop_id="w2", session_id="miss-ws",
              member_id="m2")
        _seed(registry, harness_id="h2", workshop_id="w1", session_id="miss-h",
              member_id="m3")

        out = run(s_mod.list_sessions(harness_id="h1", workshop_id="w1"))
        assert [r["session_id"] for r in out["sessions"]] == ["hit"]

    def test_status_counts_are_consistent(self, registry):
        """四件套计数与 total 自洽，且 idle 单独可见（拍板 #4「保留待复用」）。"""
        from agent_community.platform.routers import sessions as s_mod

        _seed(registry, session_id="a1")
        _seed(registry, session_id="a2", member_id="m2")
        _seed(registry, session_id="i1", member_id="m3", workshop_id="w2")
        _seed(registry, session_id="c1", member_id="m4", workshop_id="w3")
        registry.set_status("h1", "i1", ST_IDLE)
        registry.set_status("h1", "c1", ST_CLOSED)

        out = run(s_mod.list_sessions())
        assert out["active_count"] == 2
        assert out["idle_count"] == 1
        assert out["close_requested_count"] == 0
        assert out["total_count"] == 4
        assert out["count"] == 3, "idle 未关闭，仍在列表里"

    def test_rows_carry_contract_fields(self, registry):
        """前端卡片要用的字段必须在（session_id / 归属 / created_at / last_seen / status）。"""
        from agent_community.platform.routers import sessions as s_mod

        _seed(registry, session_id="s-1", workshop_id="w9", member_id="m9")
        row = run(s_mod.list_sessions())["sessions"][0]
        for f in ("session_id", "harness_id", "workshop_id", "member_id",
                  "created_at", "last_seen", "status", "source"):
            assert f in row, f"会话行缺字段 {f}"
        assert row["workshop_id"] == "w9" and row["member_id"] == "m9"

    def test_read_query_is_audited(self, registry):
        """只读查询也要留痕：session.list（设计稿 §12.2.1）。"""
        from agent_community.platform.audit import audit_log
        from agent_community.platform.routers import sessions as s_mod

        run(s_mod.list_sessions(harness_id="h-x", workshop_id="w-x"))
        evs = audit_log.query(limit=20, event="session.list")
        assert evs, "应记录 session.list 审计"
        assert "w-x" in str(evs[0].get("detail", ""))

    def test_endpoint_survives_empty_filters(self, registry):
        """显式传空串 = 全量（前端默认就是空串）。"""
        from agent_community.platform.routers import sessions as s_mod

        _seed(registry, session_id="s-1")
        assert run(s_mod.list_sessions("", ""))["count"] == 1


# ══════════════════════════════════════════════════════════════════
# B. 关闭链路①：注销 harness
# ══════════════════════════════════════════════════════════════════
class FakeRequest:
    def __init__(self, body=None, headers=None):
        self.headers = headers or {}
        self._body = body or {}

    async def json(self):
        return self._body


def _register_harness(hid="h1"):
    # ⚠️ HarnessAI.model_name 是**必填字段**（无默认值），漏填直接 ValidationError
    info = HarnessInfo(
        harness_id=hid, harness_name=hid,
        wakeup_method=WakeupMethod.CLIPBOARD,
        ai=HarnessAI(model_name="示例模型"),
    )
    harness_manager.register(info)


class TestHarnessUnregisterClosesSessions:
    def test_unregister_closes_all_sessions_of_harness(
        self, registry, clean_harness_state, monkeypatch
    ):
        from agent_community.platform.routers import harness_register as hr

        _register_harness("h-down")
        _seed(registry, harness_id="h-down", session_id="s1")
        _seed(registry, harness_id="h-down", session_id="s2", member_id="m2")
        _seed(registry, harness_id="h-keep", session_id="s3", member_id="m3")

        status, out = unpack(run(hr.unregister_harness("h-down")))
        assert status == 200, out
        assert out["sessions_closed"] == 2, "应报告关了 2 个会话"
        assert registry.get("h-down", "s1") is None, "宿主消失 → 会话应移出注册表"
        assert registry.get("h-keep", "s3") is not None, "别的 harness 不得被误伤"

    def test_unregister_zero_sessions_reports_zero(self, registry, clean_harness_state):
        from agent_community.platform.routers import harness_register as hr

        _register_harness("h-none")
        status, out = unpack(run(hr.unregister_harness("h-none")))
        assert status == 200
        assert out["sessions_closed"] == 0

    def test_unregister_unknown_harness_is_404(self, registry, clean_harness_state):
        from agent_community.platform.routers import harness_register as hr

        status, out = unpack(run(hr.unregister_harness("h-ghost")))
        assert status == 404

    def test_unregister_writes_session_close_audit(
        self, registry, clean_harness_state
    ):
        from agent_community.platform.audit import audit_log
        from agent_community.platform.routers import harness_register as hr

        _register_harness("h-audit")
        _seed(registry, harness_id="h-audit", session_id="s1")
        run(hr.unregister_harness("h-audit"))

        evs = audit_log.query(limit=20, event="session.close")
        assert evs, "注销联动关闭应写 session.close 审计"
        assert "h-audit" in str(evs[0].get("target", ""))

    def test_registry_failure_does_not_break_unregister(
        self, registry, clean_harness_state, monkeypatch
    ):
        """失败不阻塞铁律：注册表炸了，注销本身仍须成功。"""
        from agent_community.platform.routers import harness_register as hr

        _register_harness("h-boom")

        def boom(_hid):
            raise RuntimeError("注册表故障")

        monkeypatch.setattr(registry, "on_harness_unregistered", boom)
        status, out = unpack(run(hr.unregister_harness("h-boom")))
        assert status == 200, f"注册表异常不得阻断注销：{out}"
        assert out["sessions_closed"] == 0


# ══════════════════════════════════════════════════════════════════
# C/D. 关闭链路②③：删除工作间 + stale 回收
# ══════════════════════════════════════════════════════════════════
class TestWorkshopCloseChains:
    def _mk_ws(self, wid="w-del", name="测试间"):
        from agent_community.platform.workshop import Workshop, WorkshopMember

        ws = Workshop(workshop_id=wid, name=name, workspace_dir="",
                      hall_content="", status="running",
                      members=[WorkshopMember(member_id="m1", role="员工",
                                              display_name="示例Harness-A")])
        state_mod.workshops[wid] = ws
        return ws

    def test_delete_workshop_requests_close(self, registry, clean_harness_state):
        """删除 → close_requested（**不是** closed：等 harness 自述确认）。"""
        from agent_community.platform.routers import workshops_lifecycle as wl

        self._mk_ws("w-del")
        _seed(registry, workshop_id="w-del", session_id="s-d1")
        _seed(registry, workshop_id="w-del", session_id="s-d2", member_id="m2")
        _seed(registry, workshop_id="w-other", session_id="s-o", member_id="m3")

        status, out = unpack(run(wl.delete_workshop("w-del")))
        assert status == 200, out
        assert out["sessions_close_requested"] == 2

        for sid in ("s-d1", "s-d2"):
            rec = registry.get("h1", sid)
            assert rec is not None, "删除只发起 close，不立即移除记录"
            assert rec["status"] == ST_CLOSE_REQ, f"{sid} 状态应为 close_requested"
            assert rec.get("close_requested_at"), "应记录发起关闭的时间"
        # closed 会话不出现在活跃列表里，但这批是 close_requested（仍可见，等确认）
        assert registry.get("h1", "s-o")["status"] == ST_ACTIVE

    def test_delete_workshop_close_is_audited(self, registry, clean_harness_state):
        from agent_community.platform.audit import audit_log
        from agent_community.platform.routers import workshops_lifecycle as wl

        self._mk_ws("w-audit")
        _seed(registry, workshop_id="w-audit", session_id="s1")
        run(wl.delete_workshop("w-audit"))

        evs = audit_log.query(limit=20, event="session.close")
        assert evs, "工作间删除联动关闭应写 session.close 审计"
        assert "w-audit" in str(evs[0].get("target", ""))

    def test_delete_missing_workshop_is_404(self, registry, clean_harness_state):
        from agent_community.platform.routers import workshops_lifecycle as wl

        status, _out = unpack(run(wl.delete_workshop("w-ghost")))
        assert status == 404

    def test_no_sessions_means_zero_not_error(self, registry, clean_harness_state):
        from agent_community.platform.routers import workshops_lifecycle as wl

        self._mk_ws("w-empty")
        status, out = unpack(run(wl.delete_workshop("w-empty")))
        assert status == 200
        assert out["sessions_close_requested"] == 0

    def test_recycle_stale_uses_same_close_semantics(
        self, registry, clean_harness_state, monkeypatch
    ):
        """stale 回收与 DELETE 同语义（实现注释的原话），逐条挂计数。"""
        from agent_community.platform.routers import workshops_lifecycle as wl

        self._mk_ws("w-stale")
        _seed(registry, workshop_id="w-stale", session_id="s-st")
        # 只对 stale 判定/回收站打桩，其余回收逻辑全走真代码。
        # ⚠️ 这两个符号是**在函数体内 `from ..server import`** 的局部名，
        #    模块属性上并不存在 —— 只能打桩 `server` 模块上的原件。
        from agent_community.platform import server as srv_mod

        monkeypatch.setattr(srv_mod, "_is_stale_workshop",
                            lambda ws: {"stale": True, "reason": "unit-test"})
        monkeypatch.setattr(srv_mod, "_trash_bridge_dir",
                            lambda d: {"trashed": False, "reason": "unit-test"})

        # 端点要求显式指明目标：传 ws_ids 或 all=true，二者皆空返回 400
        status, out = unpack(run(wl.recycle_stale_workshops(
            FakeRequest({"ws_ids": ["w-stale"]}))))
        assert status == 200, out
        recycled = out["recycled"]
        assert len(recycled) == 1, f"应回收 1 个：{out}"
        assert recycled[0]["workshop_id"] == "w-stale"
        assert recycled[0]["sessions_close_requested"] == 1
        assert registry.get("h1", "s-st")["status"] == ST_CLOSE_REQ

    def test_close_requested_session_is_still_listed(self, registry):
        """发起关闭后仍要看得见（人要知道「哪些在等 harness 确认」）。"""
        from agent_community.platform.routers import sessions as s_mod

        _seed(registry, session_id="s1")
        registry.set_status("h1", "s1", ST_CLOSE_REQ)
        out = run(s_mod.list_sessions())
        assert out["count"] == 1
        assert out["close_requested_count"] == 1


# ══════════════════════════════════════════════════════════════════
# 步骤 9：前端「确认已进入」接线（静态断言）
# ══════════════════════════════════════════════════════════════════
@pytest.fixture(scope="class")
def workshop_html() -> str:
    p = REPO_ROOT / "agent_community" / "frontend" / "workshop.html"
    # ⚠️ 必须用 Python 读取并按 utf-8 解码：铁律④ —— PowerShell 显示的乱码是假象，
    #    但反过来，用错编码读文件会让中文断言全部"凭空失败"。这里显式 utf-8。
    return p.read_text(encoding="utf-8")


class TestFrontendConfirmButton:
    def test_session_count_line_exists(self, workshop_html):
        assert 'id="sessionCountLine"' in workshop_html, "缺会话计数容器（步骤8）"

    def test_calls_sessions_endpoint_with_workshop_filter(self, workshop_html):
        assert "/api/sessions?workshop_id=" in workshop_html, \
            "前端未按工作间拉取会话列表"

    def test_confirm_button_posts_human_source(self, workshop_html):
        """按钮必须走 activation-result 并且 **source=human**（不得冒充机器会话）。"""
        assert "confirmHumanEnter" in workshop_html
        idx = workshop_html.index("async function confirmHumanEnter")
        body = workshop_html[idx:idx + 1600]
        assert "/api/harness/activation-result" in body
        assert "'human'" in body, "人工确认必须以 source=human 上报"
        assert "'entered'" in body

    def test_confirming_set_guards_polling_rerender(self, workshop_html):
        """§4.3 教训：draw() 每 3s 重建 DOM，中间态必须靠全局 Set + 模板渲染保留。

        断言「全局登记表存在」按结构判定（`CONFIRMING` 被赋值成 `new Set()`），
        不假定写不写 `const`/`let` —— 否则等于把断言绑死在书写风格上。
        """
        import re

        assert re.search(r"\bCONFIRMING\s*=\s*new\s+Set\s*\(", workshop_html), \
            "缺 CONFIRMING 全局登记表（轮询会冲掉「确认中...」中间态）"
        idx = workshop_html.index("async function confirmHumanEnter")
        body = workshop_html[idx:idx + 2000]
        assert "CONFIRMING.add(" in body, "进入时必须登记，否则模板渲染不出「确认中...」"
        assert "CONFIRMING.delete(" in body, \
            "必须在 finally 里注销，否则失败后按钮永远停在 disabled 的「确认中...」"

    def test_confirming_state_rendered_in_draw_template(self, workshop_html):
        """模板里要按 CONFIRMING.has(mid) 渲染 disabled 中间态，否则重建就丢。"""
        assert "CONFIRMING.has(m.member_id)" in workshop_html

    def test_button_tags_balanced_in_draw_template(self, workshop_html):
        """模板字符串拼接最容易漏 </button> —— 直接配平计数。"""
        opens = workshop_html.count("<button")
        closes = workshop_html.count("</button>")
        assert opens == closes, f"<button> 与 </button> 不配平：{opens} vs {closes}"

    def test_confirm_error_restores_button(self, workshop_html):
        """确认失败要还原按钮态，否则用户被卡在 disabled 的「确认中...」。"""
        idx = workshop_html.index("async function confirmHumanEnter")
        body = workshop_html[idx:idx + 2000]
        assert "btn.disabled=false" in body.replace(" ", ""), \
            "失败分支必须还原按钮（撤 disabled）"
