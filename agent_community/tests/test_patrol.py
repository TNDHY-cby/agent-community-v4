# -*- coding: utf-8 -*-
"""V56 守卫：巡逻员（派人到现场 / 三类动作 / 三态 / 平台复核 / 链条插入顺序）。

设计稿：`design-docs/V56_巡逻员现场查看与维修设计.md`（老板 2026-10-17「按你的来」拍板）。

判据（全按真结果）：
  ① 巡逻员的挑选：角色含「巡逻」**且**绑了 harness；没绑不算可用（不假装派了人）；
  ② 回报硬约束：`checked` 不能空；`tried[].action` 只许三类；`fixed=None` 必须写 `why_no_proof`；
     `fixed=False` 必须写 `still_broken_reason`；`need_human` 必须写 `user_action`；
  ③ **"它说修好了"不算修好**：`fixed=True` + 掉线判据仍真 ⇒ `claimed_but_not_verified`；
  ④ 指令是纯文本（无 `**`），含检查单与三类动作与复验要求；
  ⑤ **链条顺序**：平台自己调起没成才派；只派一次；派出后宽限期内**先别叫用户**（`awaiting_patrol`）；
     没有巡逻员 ⇒ 跳过并**如实说明**；
  ⑥ 端点：没巡逻员 409 说清怎么配；派发成功 200；回报不合规 422；合规回报走复核并给出人话。
"""
from __future__ import annotations

import asyncio
import json
import os
import sys

import pytest

_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
if _ROOT not in sys.path:
    sys.path.append(_ROOT)

from agent_community.platform import member_offline as mo                  # noqa: E402
from agent_community.platform import patrol as pt                         # noqa: E402
from agent_community.platform import state as _state                      # noqa: E402
from agent_community.platform.routers import workshops_patrol as wp       # noqa: E402
from agent_community.platform.workshop import Workshop, WorkshopMember    # noqa: E402


def _member(mid="m1", role="码农", name="老王", hids=("hid_1",), **kw):
    m = WorkshopMember(member_id=mid, role=role, display_name=name, harness_ids=list(hids))
    for k, v in kw.items():
        setattr(m, k, v)
    return m


# ── ① 挑选巡逻员 ────────────────────────────────────────────────────────
def test_pick_patrol_requires_role_and_harness():
    ws = type("W", (), {})()
    ws.members = [_member("m1", "码农"), _member("m2", "巡逻员", hids=()),
                  _member("m3", "副巡逻员", hids=("hid_3",))]
    picked = pt.pick_patrol_member(ws)
    assert picked is not None and picked.member_id == "m3", "没绑 harness 的巡逻员不算可用"
    assert pt.pick_patrol_member(ws, exclude=["m3"]) is None


# ── ② 回报硬约束 ────────────────────────────────────────────────────────
def _report(**kw):
    base = {"checked": [{"what": "窗口", "how": "list_windows", "result": "没找到"}],
            "found": "窗口没开，进程也不在",
            "tried": [{"action": "relaunch_window", "result": "ok", "verified": True}],
            "fixed": True, "evidence": {"after": {"window": "找到 1 个"}}}
    base.update(kw)
    return base


def test_report_accepts_good_one():
    ok, probs, norm = pt.validate_report(_report())
    assert ok and probs == []
    assert norm["actions"] == ["relaunch_window"]


@pytest.mark.parametrize("mutate,expect", [
    (lambda r: r.__setitem__("checked", []), "checked 不能为空"),
    (lambda r: r["tried"][0].__setitem__("action", "edit_code"), "不在允许的三类里"),
    (lambda r: r.__setitem__("fixed", None), "why_no_proof"),
    (lambda r: r.__setitem__("fixed", False), "still_broken_reason"),
    (lambda r: r.__setitem__("need_human", True), "user_action"),
    (lambda r: r.__setitem__("fixed", "yes"), "true / false / null"),
])
def test_report_rejects_and_says_why(mutate, expect):
    r = _report()
    mutate(r)
    ok, probs, _ = pt.validate_report(r)
    assert not ok, "该拒收：%s" % expect
    assert any(expect in p for p in probs), probs


# ── ③ 平台复核：不采信"它说修好了" ──────────────────────────────────────
def test_claimed_fixed_but_still_offline_is_not_verified():
    ok, _, norm = pt.validate_report(_report())
    assert ok
    v = pt.verdict(norm, offline_still=True)
    assert v["status"] == "claimed_but_not_verified" and v["fixed"] is False
    line = pt.human_line(norm, v)
    assert "没通过" in line


def test_fixed_when_offline_cleared():
    ok, _, norm = pt.validate_report(_report())
    v = pt.verdict(norm, offline_still=False)
    assert v["status"] == "fixed" and v["fixed"] is True


def test_unverified_when_no_proof():
    r = _report(fixed=None, why_no_proof="目标没有标准文本框，读不回", tried=[])
    ok, _, norm = pt.validate_report(r)
    assert ok
    v = pt.verdict(norm, offline_still=True)
    assert v["status"] == "unverified" and v["fixed"] is False


def test_human_line_mentions_user_action_when_needed():
    r = _report(fixed=False, still_broken_reason="桥端口被占用", need_human=True,
                user_action="关掉占用 9104 的程序再试")
    ok, _, norm = pt.validate_report(r)
    v = pt.verdict(norm, offline_still=True)
    line = pt.human_line(norm, v)
    assert "没修好" in line and "桥端口被占用" in line and "关掉占用" in line


# ── ④ 指令：纯文本、含检查单/三类动作/复验 ─────────────────────────────
def test_instruction_is_plain_and_complete():
    inst = pt.instruction(pt.symptom_of(_member("m9", "码农", "老九")), patrol_id="p1",
                          workshop_id="ws1", base_url="http://127.0.0.1:18920")
    assert "**" not in inst
    for kw in ("现场检查单", "relaunch_window", "restart_bridge", "reactivate",
               "不许改代码", "复验", "fixed=null", "/patrol/p1/result", "claimed_but_not_verified"):
        assert kw in inst, "指令缺：%s" % kw


# ── ⑤ 链条插入：顺序 / 只派一次 / 宽限期 / 无巡逻员如实跳过 ──────────────
def _ws_with(members):
    w = Workshop(workshop_id="ws_p", name="巡逻间", workspace_dir=".", hall_content="h",
                 members=members)
    return w


def test_chain_dispatches_patrol_only_after_failed_revive():
    member = _member("m1", "码农")
    calls = []

    def revive():
        return {"ok": False, "verified": False, "detail": "没窗口"}

    out = mo.handle_offline(_ws_with([member]), member,
                            notify_leader=lambda t: None, attempt_revive=revive,
                            notify_user=lambda t: None,
                            heartbeat_status=lambda h: {"stale": True, "age": 999},
                            bridge_alive=lambda h: False,
                            pending_work=lambda w, m: (True, "有活在等"),
                            dispatch_patrol=lambda sym: (calls.append(sym) or
                                                         {"dispatched": True, "patrol_id": "p1"}),
                            now=1000.0)
    assert out["action"] in ("retry", "awaiting_patrol")
    assert len(calls) == 1 and out["patrol"]["dispatched"] is True


def test_chain_does_not_dispatch_when_revive_works():
    member = _member("m1", "码农")
    calls = []
    out = mo.handle_offline(_ws_with([member]), member,
                            notify_leader=lambda t: None,
                            attempt_revive=lambda: {"ok": True, "verified": True, "detail": "起来了"},
                            notify_user=lambda t: None,
                            heartbeat_status=lambda h: {"stale": True, "age": 999},
                            bridge_alive=lambda h: False,
                            pending_work=lambda w, m: (True, "有活在等"),
                            dispatch_patrol=lambda sym: (calls.append(sym) or {"dispatched": True}),
                            now=1000.0)
    assert out["action"] == "revived" and calls == [], "调起成功就不该派人"


def test_chain_dispatches_at_most_once():
    member = _member("m1", "码农")
    calls = []
    ws = _ws_with([member])
    for i in range(3):
        member.offline_last_at = 0                      # 绕过冷却
        out = mo.handle_offline(ws, member,
                                notify_leader=lambda t: None,
                                attempt_revive=lambda: {"ok": False, "verified": False, "detail": "x"},
                                notify_user=lambda t: None,
                                heartbeat_status=lambda h: {"stale": True, "age": 999},
                                bridge_alive=lambda h: False,
                                pending_work=lambda w, m: (True, "有活"),
                                dispatch_patrol=lambda sym: (calls.append(sym) or
                                                             {"dispatched": True, "patrol_id": "p"}),
                                now=1000.0 + i * 120)
        if out["action"] == "escalated":
            break
    assert len(calls) == 1, "巡逻员只该派一次，实际派了 %d 次" % len(calls)


def test_chain_waits_for_patrol_before_calling_user():
    member = _member("m1", "码农")
    member.offline_attempts = 2                       # 下一轮就到上限
    member.patrol_dispatched_at = 1000.0
    out = mo.handle_offline(_ws_with([member]), member,
                            notify_leader=lambda t: None,
                            attempt_revive=lambda: {"ok": False, "verified": False, "detail": "x"},
                            notify_user=lambda t: (_ for _ in ()).throw(AssertionError("不该叫用户")),
                            heartbeat_status=lambda h: {"stale": True, "age": 999},
                            bridge_alive=lambda h: False,
                            pending_work=lambda w, m: (True, "有活"),
                            dispatch_patrol=lambda sym: {"dispatched": True},
                            now=1000.0 + 60)              # 冷却已过、宽限期内
    assert out["action"] == "awaiting_patrol" and out["patrol_left"] > 0


def test_chain_without_patrol_member_says_so_honestly():
    member = _member("m1", "码农")
    out = mo.handle_offline(_ws_with([member]), member,
                            notify_leader=lambda t: None,
                            attempt_revive=lambda: {"ok": False, "verified": False, "detail": "x"},
                            notify_user=lambda t: None,
                            heartbeat_status=lambda h: {"stale": True, "age": 999},
                            bridge_alive=lambda h: False,
                            pending_work=lambda w, m: (True, "有活"),
                            dispatch_patrol=None,
                            now=1000.0)
    assert out["patrol"]["skipped"] is True and "没有可用巡逻员" in out["patrol"]["why"]


# ── ⑥ 端点层 ───────────────────────────────────────────────────────────
class _Req:
    def __init__(self, body=None):
        self._body = body if body is not None else {}
        self.base_url = "http://127.0.0.1:18920/"

    async def json(self):
        return self._body


def _resp(resp):
    if isinstance(resp, dict):
        return 200, resp
    body = getattr(resp, "body", b"")
    try:
        return getattr(resp, "status_code", 200), json.loads(body.decode("utf-8"))
    except Exception:                                    # noqa: BLE001
        return getattr(resp, "status_code", 200), {}


@pytest.fixture()
def two(tmp_path, monkeypatch):
    from agent_community.platform import server as _srv
    w = Workshop(workshop_id="ws_pt", name="巡逻间", workspace_dir=str(tmp_path / "ws_pt"),
                 hall_content="h",
                 members=[_member("m1", "码农", "掉线的"), _member("m0", "巡逻员", "巡的")])
    (tmp_path / "ws_pt").mkdir(parents=True, exist_ok=True)
    _state.workshops["ws_pt"] = w
    said = []
    monkeypatch.setattr(_srv, "save_state", lambda *a, **k: None, raising=False)
    monkeypatch.setattr(_srv, "_append_msg",
                        lambda _ws, kind, text, **kw: said.append(text), raising=False)
    monkeypatch.setattr(_srv, "_dispatch_to_harness",
                        lambda hid, payload, kind="task": (True, "ok"), raising=False)
    yield {"ws": w, "said": said}
    _state.workshops.pop("ws_pt", None)


def test_dispatch_ok_returns_checklist_and_instruction(two):
    code, body = _resp(asyncio.run(wp.dispatch_patrol("ws_pt", _Req({"target_member_id": "m1"}))))
    assert code == 200 and body["dispatched"] is True and body["patrol_member_id"] == "m0"
    assert body["checklist"] and body["allowed_actions"] == list(pt.ALLOWED_ACTIONS)
    assert "/patrol/" in body["instruction"] and "**" not in body["instruction"]
    assert two["ws"].patrols and two["ws"].patrols[0]["status"] == "dispatched"


def test_dispatch_without_patrol_member_is_409_with_fix(two):
    two["ws"].members[1].role = "码农"                   # 唯一"巡逻员"改名 ⇒ 没有可用的
    code, body = _resp(asyncio.run(wp.dispatch_patrol("ws_pt", _Req({"target_member_id": "m1"}))))
    assert code == 409 and "没有可用的巡逻员" in body["error"] and body["how_to_fix"]


def test_dispatch_rejects_non_patrol_member(two):
    code, body = _resp(asyncio.run(wp.dispatch_patrol(
        "ws_pt", _Req({"target_member_id": "m1", "patrol_member_id": "m1"}))))
    assert code == 400 and "不是巡逻员" in body["error"]


def test_result_rejects_bad_report(two):
    asyncio.run(wp.dispatch_patrol("ws_pt", _Req({"target_member_id": "m1"})))
    pid = two["ws"].patrols[0]["patrol_id"]
    code, body = _resp(asyncio.run(wp.submit_patrol_result(
        "ws_pt", pid, _Req({"checked": [], "fixed": True}))))
    assert code == 422 and body["problems"]


def test_result_applies_platform_verdict(two):
    asyncio.run(wp.dispatch_patrol("ws_pt", _Req({"target_member_id": "m1"})))
    pid = two["ws"].patrols[0]["patrol_id"]
    code, body = _resp(asyncio.run(wp.submit_patrol_result("ws_pt", pid, _Req(_report()))))
    assert code == 200
    # 复核用的 offline_report 在测试环境拿不到心跳 ⇒ 按"仍掉线"处理 ⇒ 不许判成 fixed
    assert body["verdict"]["status"] in ("claimed_but_not_verified", "unverified", "not_fixed")
    assert body["verdict"]["fixed"] is False
    assert "巡逻" in body["human_line"]
    assert two["ws"].members[0].entry_state == "needs_human"


def test_list_patrols_filters_by_target(two):
    asyncio.run(wp.dispatch_patrol("ws_pt", _Req({"target_member_id": "m1"})))
    code, body = _resp(asyncio.run(wp.list_patrols("ws_pt", target_member_id="m1")))
    assert code == 200 and body["count"] == 1
    code2, body2 = _resp(asyncio.run(wp.list_patrols("ws_pt", target_member_id="mX")))
    assert body2["count"] == 0
