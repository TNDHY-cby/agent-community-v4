# -*- coding: utf-8 -*-
"""工作间「组长与成员编排」端点单测：`routers/workshops_assign.py`（8 个路由）。

覆盖三类，全部对着**源码里真实存在的分支**写：

1. 校验/错误分支 —— 8 个端点的工作间不存在（404）、必填缺失（400）、
   非法值（转向对象=原成员 400、成员未绑 harness 400、AI Provider 未配置 400）、
   类型不对（confirm-members 的 members 非对象数组 400）。
2. 业务不变量 ——
   - 派发失败也必须留痕（assignments 落 `dispatched=False` + 讨论区写明「派发：失败」），
     不得因为派发失败就丢掉记录；
   - 转向的目标成员派发**先于**原成员移交通知，且原成员未绑 harness 时**不阻断**主流程；
   - running 态名单增删以 **harness 绑定**为准：新增即激活（status=activating），
     移除即终止接入（status=removed + 释放 session + 清掉**本工作间该成员**的激活队列，
     不得误伤其它成员/其它工作间）；
   - 确认名单失败（400）**不得**改动名单与状态；成功则状态机必须落到 discussing/division；
   - select-members 会把未注册 harness 剔除、非法 role 归一为「员工」。
3. 正常路径返回结构 —— assignments 记录字段、`items[-10:]` 截断、成员按位置重编号 m0..、
   工作台四件套（paused / idea_bag / pending_reviews / state）。

数据隔离：本文件不碰真实 `data/` —— 夹具把 `server.save_state` 打桩成「记录调用」，
`_dispatch_to_harness` / `_activate_single` / `_activate_leader_if_needed` 也全部打桩，
不在测试里发任何真实网络请求；`state.workshops`、状态机、插话库、pending_activations
的改动一律在 finally 里清理，不留给其它测试。

────────────────────────────────────────────────────────────────────
⚠️ 已知生产代码缺陷（本文件不改源码，只如实记录）
────────────────────────────────────────────────────────────────────
`workshops_assign.py` **只导入了 `_sv`**（第 15 行 `from .workshops_common import _sv`），
却在函数体里使用 `_leader_pick_member` / `_leader_member_hid` / `_leader_build_assign_payload`
（L49/L52/L55/L91/L92/L95/L99/L102/L105/L139）—— 这三个符号从未导入，调用即
`NameError: name '_leader_pick_member' is not defined` → HTTP 500。已对运行中的
18920 服务实测复现（缺成员的 assign 请求 500，而同一端点的参数校验分支正常回 400）。

因此凡是会走到 `_leader_*` 的用例，断言**按修复后的正确契约写满**，但暂时挂
`xfail`（见 `DEFECT_REASON`）；`TestProductionDefectGuard` 把缺陷本身钉住。
**修复方式**：给第 15 行补上三个符号，然后删除全部 `xfail` 标记与该守卫类。
"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest

from agent_community.platform import server as server_mod
from agent_community.platform import state as state_mod
from agent_community.platform.routers import workshops_assign as wa
from agent_community.platform.task_state_machine import DISCUSSING
from agent_community.platform.workshop import Workshop, WorkshopMember


def run(coro):
    return asyncio.run(coro)


DEFECT_REASON = (
    "生产代码缺陷：workshops_assign.py 未从 workshops_common 导入 "
    "_leader_pick_member/_leader_member_hid/_leader_build_assign_payload（第 15 行只导入了 _sv），"
    "调用即 NameError→500。断言按修复后的正确契约写满，修复后请删除本 xfail 标记。"
)


def unpack(resp):
    if hasattr(resp, "status_code") and hasattr(resp, "body"):
        return resp.status_code, json.loads(resp.body)
    return 200, resp


class FakeRequest:
    def __init__(self, body=None, headers=None):
        self.headers = headers or {}
        self._body = body or {}

    async def json(self):
        return self._body


# ── 夹具 ─────────────────────────────────────────────────────────────
@pytest.fixture()
def env(monkeypatch):
    """造一个隔离工作间放进 state.workshops，并把全部外呼打桩；结束时彻底清理。"""
    wid = "ws_assign_" + uuid4().hex[:8]
    ws = Workshop(
        workshop_id=wid,
        name="编排单测工作间",
        workspace_dir="D:\\ws\\" + wid,
        hall_content="任务：给 FastAPI 项目补单元测试",
    )
    ws.members = [
        WorkshopMember(member_id="m0", role="组长", display_name="组长甲",
                       harness_ids=["h_leader"]),
        WorkshopMember(member_id="m1", role="码农", display_name="码农乙",
                       harness_ids=["h_coder"], status="working"),
    ]
    state_mod.workshops[wid] = ws

    calls = {"dispatch": [], "activate": [], "save": 0}

    def fake_dispatch(hid, payload, kind="task"):
        calls["dispatch"].append((hid, payload, kind))
        return True, "已入 pending 队列（测试桩）"

    monkeypatch.setattr(server_mod, "_dispatch_to_harness", fake_dispatch)

    def fake_activate(_ws, m):
        calls["activate"].append((_ws.workshop_id, m.member_id))

    monkeypatch.setattr(server_mod, "_activate_single", fake_activate)

    def fake_save():
        calls["save"] += 1
        return True

    monkeypatch.setattr(server_mod, "save_state", fake_save)

    out = SimpleNamespace(ws=ws, wid=wid, calls=calls)
    try:
        yield out
    finally:
        state_mod.workshops.pop(wid, None)
        state_mod.task_state_machine.remove(wid)
        state_mod.interject_store.remove_workshop(wid)
        for hid in list(state_mod.pending_activations):
            kept = [a for a in state_mod.pending_activations[hid]
                    if a.get("workshop_id") != wid]
            if kept:
                state_mod.pending_activations[hid] = kept
            else:
                state_mod.pending_activations.pop(hid, None)


def _leader_back_on(monkeypatch, reply="", ok=True):
    """打桩 _activate_leader_if_needed（confirm-members 会经 server 属性调用）。"""
    monkeypatch.setattr(server_mod, "_activate_leader_if_needed",
                        lambda _ws: (reply, ok, None))


# ══════════════════════════════════════════════════════════════════
# 0. 生产代码缺陷守卫（修好第 15 行的导入后，本类连同全部 xfail 标记一起删掉）
# ══════════════════════════════════════════════════════════════════
class TestProductionDefectGuard:
    def test_leader_helpers_are_not_imported_everywhere_they_are_used(self):
        """钉住缺陷：`_leader_*` 被使用却未导入 —— 修好本用例会变红，提示删除标记。"""
        missing = [name for name in (
            "_leader_pick_member", "_leader_member_hid", "_leader_build_assign_payload")
            if not hasattr(wa, name)]
        assert missing == [
            "_leader_pick_member", "_leader_member_hid", "_leader_build_assign_payload",
        ], ("缺陷似乎已修复：请删除本守卫用例，并去掉 TestLeaderAssign/"
            "TestLeaderRedirect/TestLeaderAssignments 里的 xfail 标记，让断言正式生效")


# ══════════════════════════════════════════════════════════════════
# 1. 工作间不存在 → 8 个端点全覆盖 404
# ══════════════════════════════════════════════════════════════════
def test_every_endpoint_guards_missing_workshop_with_404():
    """任何一个端点丢掉 404 守卫，本用例都会失败并点名是哪一个。"""
    ghost = "ws_ghost_" + uuid4().hex[:8]
    assert ghost not in state_mod.workshops
    req = FakeRequest(body={})
    endpoints = [
        ("POST leader-status", lambda: wa.workshop_leader_status(ghost)),
        ("POST leader/assign", lambda: wa.workshop_leader_assign(ghost, req)),
        ("POST leader/redirect", lambda: wa.workshop_leader_redirect(ghost, req)),
        ("GET leader/assignments", lambda: wa.workshop_leader_assignments(ghost)),
        ("POST select-members", lambda: wa.workshop_select_members(ghost)),
        ("POST members", lambda: wa.workshop_save_members(ghost, req)),
        ("POST confirm-members", lambda: wa.workshop_confirm_members(ghost, req)),
        ("GET leader-workbench", lambda: wa.leader_workbench(ghost)),
    ]
    for label, call in endpoints:
        status, payload = unpack(run(call()))
        assert status == 404, f"{label}: 缺工作间不存在守卫，实得 {status} / {payload}"
        assert payload.get("error") == "工作间不存在", \
            f"{label}: 错误信息应说明工作间不存在，实得 {payload}"


# ══════════════════════════════════════════════════════════════════
# 2. leader-status
# ══════════════════════════════════════════════════════════════════
def test_leader_status_reports_roster_and_handles_empty(env):
    status, payload = unpack(run(wa.workshop_leader_status(env.wid)))
    assert status == 200 and payload["success"] is True
    assert payload["members"] == [
        {"role": "组长", "display_name": "组长甲", "status": "pending"},
        {"role": "码农", "display_name": "码农乙", "status": "working"},
    ], "必须按名单顺序逐成员如实汇报 role/display_name/status"

    env.ws.members.clear()
    _, empty = unpack(run(wa.workshop_leader_status(env.wid)))
    assert empty == {"success": True, "members": []}, "空名单不得报错或返回脏数据"


# ══════════════════════════════════════════════════════════════════
# 3. leader/assign
# ══════════════════════════════════════════════════════════════════
class TestLeaderAssign:
    def test_blank_member_or_instruction_is_400(self, env):
        """member_id / instruction 任一为空（含纯空白）都必须 400，且不得落盘。"""
        bad_bodies = [
            {"member_id": "", "instruction": "写测试"},
            {"member_id": "m1"},
            {"member_id": "m1", "instruction": "   "},
        ]
        for body in bad_bodies:
            status, payload = unpack(run(wa.workshop_leader_assign(env.wid, FakeRequest(body))))
            assert status == 400, f"{body} 应 400，实得 {status}"
            assert "member_id" in payload["error"] and "instruction" in payload["error"]
        assert env.ws.assignments == {}, "校验失败不得写入 assignments"
        assert env.calls["save"] == 0, "校验失败不得落盘"

    @pytest.mark.xfail(reason=DEFECT_REASON)
    def test_unknown_member_is_404(self, env):
        status, payload = unpack(run(wa.workshop_leader_assign(
            env.wid, FakeRequest({"member_id": "m404", "instruction": "写测试"}))))
        assert status == 404
        assert "m404" in payload["error"]
        assert env.calls["dispatch"] == [], "成员不存在时不得派发"

    @pytest.mark.xfail(reason=DEFECT_REASON)
    def test_member_without_harness_is_400(self, env):
        env.ws.members.append(WorkshopMember(
            member_id="m9", role="画师", display_name="画师丙", harness_ids=[]))
        status, payload = unpack(run(wa.workshop_leader_assign(
            env.wid, FakeRequest({"member_id": "m9", "instruction": "画图"}))))
        assert status == 400
        assert "未绑定 harness" in payload["error"]
        assert "画师丙" in payload["error"], "报错要点名是哪位成员"
        assert env.calls["dispatch"] == []

    @pytest.mark.xfail(reason=DEFECT_REASON)
    def test_success_persists_record_and_writes_discussion(self, env):
        status, payload = unpack(run(wa.workshop_leader_assign(
            env.wid, FakeRequest({"member_id": "m1", "instruction": "写 20 条测试",
                                  "note": "今晚前"}))))
        assert status == 200
        assert payload["success"] is True and payload["member_id"] == "m1"
        assert payload["dispatched"] is True
        assert payload["dispatch_note"] == "已入 pending 队列（测试桩）"

        recs = env.ws.assignments["m1"]
        assert len(recs) == 1, "每次派发都必须留一条 assignment 记录"
        rec = recs[0]
        assert rec["kind"] == "assign"
        assert rec["instruction"] == "写 20 条测试" and rec["note"] == "今晚前"
        assert rec["by"] == "组长" and rec["dispatched"] is True and rec["at"]

        hid, dispatched_payload, kind = env.calls["dispatch"][0]
        assert hid == "h_coder", "必须派给该成员绑定的 harness"
        assert kind == "task"
        assert dispatched_payload["type"] == "leader_assign"
        assert dispatched_payload["member_id"] == "m1"
        assert "写 20 条测试" in dispatched_payload["message"]

        msg = env.ws.discussion[-1]
        assert msg["role"] == "leader" and msg["zone"] == 2
        assert msg["display_name"] == "组长"
        assert "【组长委派细化】" in msg["content"] and "写 20 条测试" in msg["content"]
        assert "备注：今晚前" in msg["content"]
        assert "派发：成功" in msg["content"]
        assert env.calls["save"] == 1, "派发成功必须落盘"

    @pytest.mark.xfail(reason=DEFECT_REASON)
    def test_dispatch_failure_still_recorded_and_visible(self, env, monkeypatch):
        """派发失败（桥已退出等）也必须留痕：记录 dispatched=False，讨论区写明失败。"""
        monkeypatch.setattr(server_mod, "_dispatch_to_harness",
                            lambda *a, **k: (False, "桥进程已退出，**本条未入队**"))
        status, payload = unpack(run(wa.workshop_leader_assign(
            env.wid, FakeRequest({"member_id": "m1", "instruction": "补测试"}))))
        assert status == 200 and payload["success"] is True
        assert payload["dispatched"] is False
        assert payload["dispatch_note"].startswith("桥进程已退出")
        assert env.ws.assignments["m1"][0]["dispatched"] is False, \
            "失败必须如实记在 assignments 里，不得谎报成功"
        assert "派发：失败" in env.ws.discussion[-1]["content"]
        assert env.calls["save"] == 1


# ══════════════════════════════════════════════════════════════════
# 4. leader/redirect
# ══════════════════════════════════════════════════════════════════
class TestLeaderRedirect:
    def test_missing_ids_or_same_member_is_400(self, env):
        cases = [
            ({"instruction": "改由你做"}, "from_member_id", "不能为空"),
            ({"from_member_id": "m0"}, "to_member_id", "不能为空"),
            ({"from_member_id": "m0", "to_member_id": "m0"}, "", "不能与原成员相同"),
        ]
        for body, field, hint in cases:
            status, payload = unpack(run(wa.workshop_leader_redirect(env.wid, FakeRequest(body))))
            assert status == 400, f"{body} 应 400，实得 {status}"
            if field:
                assert field in payload["error"], payload["error"]
            if hint:
                assert hint in payload["error"], payload["error"]
        assert env.calls["dispatch"] == [], "非法请求不得派发"
        assert env.ws.assignments == {}

    @pytest.mark.xfail(reason=DEFECT_REASON)
    def test_unknown_member_is_404_and_names_it(self, env):
        for frm, to in (("m0", "m404"), ("m404", "m1")):
            status, payload = unpack(run(wa.workshop_leader_redirect(
                env.wid, FakeRequest({"from_member_id": frm, "to_member_id": to}))))
            assert status == 404
            assert "m404" in payload["error"], f"必须点名缺失成员，实得 {payload['error']}"
        assert env.calls["dispatch"] == []

    @pytest.mark.xfail(reason=DEFECT_REASON)
    def test_target_without_harness_is_400(self, env):
        env.ws.members.append(WorkshopMember(
            member_id="m9", role="画师", display_name="画师丙", harness_ids=[]))
        status, payload = unpack(run(wa.workshop_leader_redirect(
            env.wid, FakeRequest({"from_member_id": "m1", "to_member_id": "m9",
                                  "instruction": "转给你"}))))
        assert status == 400
        assert "无法承接" in payload["error"] and "画师丙" in payload["error"]
        assert env.calls["dispatch"] == [], "目标承接不了就不该动任何派发"

    @pytest.mark.xfail(reason=DEFECT_REASON)
    def test_success_dispatches_target_first_then_source(self, env):
        status, payload = unpack(run(wa.workshop_leader_redirect(
            env.wid, FakeRequest({"from_member_id": "m0", "to_member_id": "m1",
                                  "instruction": "改由码农乙推进", "note": "组长决定"}))))
        assert status == 200
        assert payload["success"] is True
        assert payload["from_member_id"] == "m0" and payload["to_member_id"] == "m1"
        assert payload["dispatched"] is True and payload["src_notified"] is True

        assert [(c[0], c[1]["type"]) for c in env.calls["dispatch"]] == [
            ("h_coder", "leader_redirect"),   # 先给目标派承接
            ("h_leader", "leader_assign"),    # 再通知原成员移交
        ]
        assert "停止当前工作" in env.calls["dispatch"][1][1]["message"]

        rec = env.ws.assignments["m1"][-1]
        assert rec["kind"] == "redirect" and rec["from_member_id"] == "m0"
        assert rec["instruction"] == "改由码农乙推进" and rec["dispatched"] is True
        assert "m0" not in env.ws.assignments, "转向记录记在目标成员名下"

        msg = env.ws.discussion[-1]
        assert "【组长任务转向】" in msg["content"]
        assert "组长甲" in msg["content"] and "码农乙" in msg["content"]
        assert env.calls["save"] == 1

    @pytest.mark.xfail(reason=DEFECT_REASON)
    def test_source_without_harness_does_not_block_redirect(self, env):
        """原成员未绑 harness：跳过移交通知但主流程照常成功（不阻断）。"""
        env.ws.members[0].harness_ids = []
        status, payload = unpack(run(wa.workshop_leader_redirect(
            env.wid, FakeRequest({"from_member_id": "m0", "to_member_id": "m1"}))))
        assert status == 200 and payload["success"] is True
        assert payload["dispatched"] is True
        assert payload["src_notified"] is False
        assert len(env.calls["dispatch"]) == 1, "无 harness 就不该有移交通知"
        assert env.calls["dispatch"][0][0] == "h_coder"
        # instruction 缺省时用兜底文案，且记录里保留原值（空串）
        assert env.calls["dispatch"][0][1]["instruction"] == "请承接并推进该任务。"
        assert "承接并推进该任务。" in env.ws.discussion[-1]["content"]


# ══════════════════════════════════════════════════════════════════
# 5. GET leader/assignments
# ══════════════════════════════════════════════════════════════════
class TestLeaderAssignments:
    def test_empty_when_no_record(self, env):
        status, payload = unpack(run(wa.workshop_leader_assignments(env.wid)))
        assert status == 200 and payload["success"] is True
        assert payload["assignments"] == []

    @pytest.mark.xfail(reason=DEFECT_REASON)
    def test_resolves_member_fields_and_falls_back(self, env):
        env.ws.assignments = {
            "m1": [{"kind": "assign", "instruction": "甲"}],
            "m404": [{"kind": "redirect", "instruction": "乙"}],
        }
        _, payload = unpack(run(wa.workshop_leader_assignments(env.wid)))
        recs = {r["member_id"]: r for r in payload["assignments"]}
        assert set(recs) == {"m1", "m404"}
        assert recs["m1"]["role"] == "码农" and recs["m1"]["display_name"] == "码农乙"
        assert recs["m1"]["items"] == [{"kind": "assign", "instruction": "甲"}]
        assert recs["m404"]["role"] == "" and recs["m404"]["display_name"] == "m404", \
            "成员已不在名单时不得崩，display_name 回退为成员 id"

    @pytest.mark.xfail(reason=DEFECT_REASON)
    def test_items_truncated_to_last_10(self, env):
        env.ws.assignments = {"m1": [{"kind": "assign", "instruction": str(i)}
                                     for i in range(12)]}
        _, payload = unpack(run(wa.workshop_leader_assignments(env.wid)))
        items = payload["assignments"][0]["items"]
        assert len(items) == 10, "前端只吃最近 10 条，超出必须截断"
        assert items[0]["instruction"] == "2" and items[-1]["instruction"] == "11"


# ══════════════════════════════════════════════════════════════════
# 6. POST select-members
# ══════════════════════════════════════════════════════════════════
HARNESSES = [
    {"harness_id": "h_a", "harness_name": "示例Harness-A",
     "ai": {"model_name": "示例模型", "capabilities": ["code"]}},
    {"harness_id": "h_b", "harness_name": "示例Harness-B",
     "ai": {"model_name": "示例模型", "capabilities": ["draw"]}},
]


class _FakeProvider:
    def __init__(self):
        self.calls = []

    def chat(self, system, user):
        self.calls.append((system, user))
        return {"stub": True}


def _wire_select(monkeypatch, *, provider=True, reply="", error=None, harnesses=None):
    prov = _FakeProvider()
    monkeypatch.setattr(wa, "_sv", lambda: SimpleNamespace(
        ai_provider=prov if provider else None))
    monkeypatch.setattr(wa, "harness_manager", SimpleNamespace(
        sessions={}, list_sessions=lambda: list(harnesses or [])))

    async def _fake_run(_chat, label=""):
        if error is not None:
            raise error
        return reply

    monkeypatch.setattr(wa, "ai_external_run_ai_call", _fake_run)
    return prov


class TestSelectMembers:
    def test_without_ai_provider_is_400(self, env, monkeypatch):
        _wire_select(monkeypatch, provider=False)
        status, payload = unpack(run(wa.workshop_select_members(env.wid)))
        assert status == 400
        assert payload["error"] == "AI Provider 未配置"
        assert env.ws.status != "selecting", "未配置 Provider 不得推进工作间状态"

    def test_cleans_ai_output_and_drops_unregistered_harness(self, env, monkeypatch):
        reply = (
            '选定如下：{"members":['
            '{"role":"??","display_name":"示例Harness-A","harness_ids":["h_a","h_ghost","??"]},'
            '{"role":"码农","display_name":"","harness_ids":["h_b"]},'
            '"不是对象，必须丢弃",'
            '{"role":"搜索者","display_name":"无此人","harness_ids":[]}'
            ']} 以上。'
        )
        prov = _wire_select(monkeypatch, reply=reply, harnesses=HARNESSES)
        status, payload = unpack(run(wa.workshop_select_members(env.wid)))
        assert status == 200 and payload["success"] is True
        assert payload["members"] == [
            {"member_id": "m0", "role": "员工", "display_name": "示例Harness-A",
             "harness_ids": ["h_a"]},
            {"member_id": "m1", "role": "码农", "display_name": "", "harness_ids": ["h_b"]},
            {"member_id": "m2", "role": "搜索者", "display_name": "无此人",
             "harness_ids": []},
        ], "非法 role 要归一为「员工」、未注册 harness 要剔除、非对象项要跳过"
        assert "reason" not in payload, "有结果就不该带兜底 reason"
        assert env.ws.status == "selecting"
        assert [(m.member_id, m.role, m.harness_ids) for m in env.ws.members] == [
            ("m0", "员工", ["h_a"]),
            ("m1", "码农", ["h_b"]),
            ("m2", "搜索者", []),
        ], "名单必须整体替换为清洗后的 m0.. 序列"
        assert prov.calls and "示例Harness-A" in prov.calls[0][1], \
            "已注册 harness 必须真的喂给 AI 作为候选"

    def test_unparsable_reply_yields_empty_roster_with_reason(self, env, monkeypatch):
        _wire_select(monkeypatch, reply="抱歉，没有合适的员工。", harnesses=HARNESSES)
        status, payload = unpack(run(wa.workshop_select_members(env.wid)))
        assert status == 200 and payload["success"] is True
        assert payload["members"] == []
        assert "reason" in payload and "人工补充名单" in payload["reason"]
        assert env.ws.members == [], "AI 选不出人时名单被清空（由 reason 提示人工补）"
        assert env.ws.status == "selecting"

    def test_ai_exception_reported_without_wiping_roster(self, env, monkeypatch):
        _wire_select(monkeypatch, error=RuntimeError("模型超时"), harnesses=HARNESSES)
        status, payload = unpack(run(wa.workshop_select_members(env.wid)))
        assert status == 200
        assert payload["success"] is False
        assert "模型超时" in payload["error"] and "选定失败" in payload["error"]
        assert [m.member_id for m in env.ws.members] == ["m0", "m1"], \
            "AI 调用炸了不得把已有名单抹掉"


# ══════════════════════════════════════════════════════════════════
# 7. POST members（running 态增量同步）
# ══════════════════════════════════════════════════════════════════
class TestSaveMembers:
    def test_non_running_only_replaces_roster(self, env):
        env.ws.status = "discussing"
        body = {"members": [
            {"role": "码农", "display_name": "码农乙", "harness_ids": ["h_coder"]},
            {"role": "组长", "display_name": "组长甲", "harness_ids": ["h_leader"]},
        ]}
        status, payload = unpack(run(wa.workshop_save_members(env.wid, FakeRequest(body))))
        assert status == 200 and payload["success"] is True
        assert payload["members"] == ["m0", "m1"], "member_id 按名单位置重编号"
        assert payload["added"] == [] and payload["removed"] == []
        assert env.ws.status == "discussing", "讨论阶段改名单不得推进状态"
        assert env.ws.members[0].harness_ids == ["h_coder"], "顺序被改后按新顺序落位"
        assert env.calls["activate"] == [], "非 running 态不得激活任何人"
        assert env.calls["save"] == 1

    def test_running_add_activates_new_harness(self, env):
        env.ws.status = "running"
        body = {"members": [
            {"role": "组长", "display_name": "组长甲", "harness_ids": ["h_leader"]},
            {"role": "码农", "display_name": "码农乙", "harness_ids": ["h_coder"]},
            {"role": "搜索者", "display_name": "搜索丁", "harness_ids": ["h_new"]},
        ]}
        status, payload = unpack(run(wa.workshop_save_members(env.wid, FakeRequest(body))))
        assert status == 200
        assert payload["added"] == [{"member_id": "m2", "display_name": "搜索丁",
                                     "role": "搜索者", "harness_ids": ["h_new"]}]
        assert payload["removed"] == []
        assert env.ws.members[2].status == "activating", "新增成员必须立即进入激活"
        assert env.calls["activate"] == [(env.wid, "m2")], "必须真的调 _activate_single"

    def test_running_remove_terminates_member_and_clears_queue(self, env):
        env.ws.status = "running"
        old = env.ws.members[1]
        old.status = "working"
        old.session = "SESS-1"
        state_mod.pending_activations["h_coder"] = [
            {"workshop_id": env.wid, "member_id": "m1"},      # 该被清掉
            {"workshop_id": env.wid, "member_id": "m0"},      # 别的成员，保留
            {"workshop_id": "ws_other", "member_id": "m1"},   # 别的工作间，保留
        ]
        body = {"members": [
            {"role": "组长", "display_name": "组长甲", "harness_ids": ["h_leader"]},
        ]}
        status, payload = unpack(run(wa.workshop_save_members(env.wid, FakeRequest(body))))
        assert status == 200
        assert payload["removed"] == [{"member_id": "m1", "display_name": "码农乙",
                                       "role": "码农", "harness_ids": ["h_coder"]}]
        assert payload["added"] == []
        assert old.status == "removed", "被移除成员必须标记 removed（终止接入）"
        assert old.session is None, "必须释放平台侧会话引用"
        assert state_mod.pending_activations["h_coder"] == [
            {"workshop_id": env.wid, "member_id": "m0"},
            {"workshop_id": "ws_other", "member_id": "m1"},
        ], "只清「本工作间 × 本成员」的激活队列，不得误伤"
        assert env.calls["activate"] == []

    def test_running_retains_status_and_session_of_kept_members(self, env):
        env.ws.status = "running"
        old = env.ws.members[1]
        old.status = "working"
        old.session = "SESS-9"
        body = {"members": [
            {"role": "组长", "display_name": "组长甲", "harness_ids": ["h_leader"]},
            {"role": "码农", "display_name": "码农乙", "harness_ids": ["h_coder"]},
        ]}
        status, payload = unpack(run(wa.workshop_save_members(env.wid, FakeRequest(body))))
        assert status == 200
        assert payload["added"] == [] and payload["removed"] == []
        new = env.ws.members[1]
        assert new is not old, "成员对象按新名单重建"
        assert new.status == "working", "保留成员必须继承原生命周期状态"
        assert new.session == "SESS-9", "保留成员必须继承原会话引用"
        assert env.calls["activate"] == [], "保留成员不得被重复激活"


# ══════════════════════════════════════════════════════════════════
# 8. POST confirm-members
# ══════════════════════════════════════════════════════════════════
class TestConfirmMembers:
    def test_bad_members_type_is_400_without_side_effect(self, env, monkeypatch):
        _leader_back_on(monkeypatch)
        env.ws.status = "selecting"
        for bad in ("abc", [1, 2], [{"role": "组长"}, "x"]):
            status, payload = unpack(run(wa.workshop_confirm_members(
                env.wid, FakeRequest({"members": bad}))))
            assert status == 400, f"members={bad!r} 应 400，实得 {status}"
            assert "members 需为对象数组" in payload["error"]
        assert env.ws.status == "selecting", "校验失败不得推进状态"
        assert [m.member_id for m in env.ws.members] == ["m0", "m1"], "校验失败不得改名单"
        assert env.calls["save"] == 0

    def test_without_members_keeps_roster_and_enters_division(self, env, monkeypatch):
        _leader_back_on(monkeypatch, reply="", ok=True)
        status, payload = unpack(run(wa.workshop_confirm_members(env.wid, FakeRequest({}))))
        assert status == 200
        assert payload == {"success": True, "status": "division", "leader_activated": True}
        assert env.ws.status == "division"
        assert [m.member_id for m in env.ws.members] == ["m0", "m1"], \
            "不带 members 时保持原名单，不得清空"
        assert state_mod.task_state_machine.get_state(env.wid) == DISCUSSING, \
            "进入二级讨论必须把自治状态机置为 discussing"
        assert state_mod.task_state_machine._states[env.wid]["ctx"]["stage"] == "division"
        assert any("【二级讨论】" in m["content"] for m in env.ws.discussion), \
            "确认名单必须在讨论区留下二级讨论公告"
        assert env.calls["save"] == 1

    def test_with_members_replaces_roster(self, env, monkeypatch):
        _leader_back_on(monkeypatch)
        env.ws.status = "selecting"
        body = {"members": [{"role": "画师", "display_name": "画师丙",
                             "harness_ids": ["h_draw"]}]}
        status, payload = unpack(run(wa.workshop_confirm_members(env.wid, FakeRequest(body))))
        assert status == 200 and payload["status"] == "division"
        assert [(m.member_id, m.role, m.display_name, m.harness_ids)
                for m in env.ws.members] == [("m0", "画师", "画师丙", ["h_draw"])]

    def test_leader_activation_failure_surfaces_as_notice(self, env, monkeypatch):
        """组长激活不了时：leader_activated=False，且原因以 notice 形式进讨论区。"""
        _leader_back_on(monkeypatch, reply="（二级讨论：名单为空，无法激活组长。请先确认员工名单。）",
                        ok=False)
        status, payload = unpack(run(wa.workshop_confirm_members(env.wid, FakeRequest({}))))
        assert status == 200
        assert payload["status"] == "division" and payload["leader_activated"] is False
        last = env.ws.discussion[-1]
        assert last["role"] == "notice" and "无法激活组长" in last["content"]
        assert env.ws.status == "division", "组长没激活也要进入分工讨论（由用户决定下一步）"


# ══════════════════════════════════════════════════════════════════
# 9. GET leader-workbench
# ══════════════════════════════════════════════════════════════════
class TestLeaderWorkbench:
    def test_paused_list_and_state_machine_state(self, env):
        env.ws.members[1].status = "blocked"
        env.ws.members.append(WorkshopMember(
            member_id="m2", role="画师", display_name="画师丙",
            harness_ids=["h_draw"], status="stuck"))
        env.ws.members.append(WorkshopMember(
            member_id="m3", role="搜索者", display_name="搜索丁",
            harness_ids=["h_s"], status="working"))
        state_mod.task_state_machine.set_state(env.wid, "executing")

        status, payload = unpack(run(wa.leader_workbench(env.wid)))
        assert status == 200 and payload["success"] is True
        assert payload["paused"] == [
            {"member_id": "m1", "display_name": "码农乙", "status": "blocked", "role": "码农"},
            {"member_id": "m2", "display_name": "画师丙", "status": "stuck", "role": "画师"},
        ], "暂停列表只含 stuck/blocked/failed，且按名单顺序"
        assert payload["state"] == "executing"
        assert payload["idea_bag"] == []
        assert payload["pending_reviews"] is None, "无异常态且无待处理插话时不得报待裁决"

    def test_idea_bag_contains_only_pending_inspirations(self, env):
        from agent_community.platform.state import interject_store

        it_insp = interject_store.submit(env.wid, "有个点子", priority="灵感")
        it_gen = interject_store.submit(env.wid, "一般插话", priority="一般")
        it_done = interject_store.submit(env.wid, "已处理的点子", priority="灵感")
        interject_store.mark(env.wid, it_done["id"], "inserted")

        _, payload = unpack(run(wa.leader_workbench(env.wid)))
        ids = [it["id"] for it in payload["idea_bag"]]
        assert ids == [it_insp["id"]], \
            f"点子袋只收 pending 的「灵感」，实得 {ids}（一般插话={it_gen['id']}）"

    def test_pending_reviews_triggered_by_state_or_interjects(self, env):
        from agent_community.platform.state import interject_store

        state_mod.task_state_machine.set_state(env.wid, "executing")
        _, payload = unpack(run(wa.leader_workbench(env.wid)))
        assert payload["pending_reviews"] is None

        state_mod.task_state_machine.set_state(env.wid, "waiting_reply")
        _, payload = unpack(run(wa.leader_workbench(env.wid)))
        assert payload["pending_reviews"] is not None
        assert payload["pending_reviews"]["state"] == "waiting_reply"
        assert payload["pending_reviews"]["pending_interjects"] == []

        state_mod.task_state_machine.set_state(env.wid, "executing")
        it = interject_store.submit(env.wid, "要裁决", priority="紧急")
        _, payload = unpack(run(wa.leader_workbench(env.wid)))
        assert payload["pending_reviews"] is not None, "有待处理插话就必须进待裁决队列"
        assert [x["id"] for x in payload["pending_reviews"]["pending_interjects"]] == [it["id"]]
