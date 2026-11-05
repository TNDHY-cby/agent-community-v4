# -*- coding: utf-8 -*-
"""守卫：**工作间开工闸 + 占位配置提醒**（外部测试问题 EXT-20261022-01 的 B/C/A 三条）。

判据（真跑 HTTP）：
① 0 成员工作间发讨论 → **409** + `error` + `next_step`（不再静默空转，状态不改）；
② 成员全是 pending（未接上）→ 409，原因里说得清"桥没取走唤醒消息/agent 没醒"；
③ 有一个成员已接上（entry_state=entered）→ 放行（不因为别人没验证就整个不让干）；
④ 工作间序列化里带 `gate`（界面能显示横幅，含 members_count / verified_count）；
⑤ 保存 `https://api.example.com` + `example-model-flash` → 返回 `placeholder_warning`（黄条），**不阻断**。
"""
from __future__ import annotations

import pytest

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.append(str(_ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from agent_community.platform import server as srv  # noqa: E402
from agent_community.platform.workshop import Workshop, WorkshopMember  # noqa: E402

C = TestClient(srv.app, client=("127.0.0.1", 54321))


def _ws(wid: str, members=()) -> Workshop:
    ws = Workshop(workshop_id=wid, name=wid, workspace_dir="", hall_content="做个记账工具")
    ws.members = list(members)
    srv.workshops[wid] = ws
    return ws


def _m(mid: str, state="pending", status="pending") -> WorkshopMember:
    m = WorkshopMember(member_id=mid, role="工程师", display_name=mid)
    m.entry_state = state
    m.status = status
    return m


def test_empty_workshop_discuss_is_refused():
    ws = _ws("g-1")
    r = C.post("/api/workshop/g-1/discuss", json={"message": "开始吧"})
    assert r.status_code == 409, r.text
    body = r.json()
    assert "还没有成员" in body["error"] and body["next_step"], body
    assert ws.status != "discussing", "被拦下时不许把状态改成 discussing：%r" % ws.status
    assert not ws.discussion, "被拦下时不该往讨论区写消息"


def test_all_members_pending_refused():
    _ws("g-2", [_m("m1"), _m("m2")])
    r = C.post("/api/workshop/g-2/discuss", json={"message": "开始吧"})
    assert r.status_code == 409, r.text
    assert "没接上" in r.json()["error"], r.text
    assert r.json()["gate"] == "no_verified_member", r.text


def test_one_verified_member_passes(monkeypatch):
    ws = _ws("g-3", [_m("m1"), _m("m2", state="entered", status="entered")])

    class _S:
        ai_provider = None

    from agent_community.platform.routers import workshops_discuss as _wd

    monkeypatch.setattr(_wd, "_sv", lambda: _S())     # 只验闸门放行，AI 桩成未配置
    r = C.post("/api/workshop/g-3/discuss", json={"message": "先说说你怎么理解这个需求"})
    assert r.status_code != 409, "有一个成员接上了就应当放行：%s" % r.text
    assert r.status_code == 400 and "AI Provider" in r.text, r.text
    assert ws.status == "discussing", ws.status


def test_gate_in_serialized_workshop():
    ws = _ws("g-4", [_m("m1")])
    d = ws.to_dict()
    assert "gate" in d and d["gate"]["state"] == "no_verified_member", d.get("gate")
    assert d["gate"]["members_count"] == 1 and d["gate"]["verified_count"] == 0, d["gate"]


def test_placeholder_config_warns_but_saves():
    # ★2026-11-05：不传 provider 会被 400 挡（正确：不能存一个没 provider 的配置）。
    #   本条测的是"占位符要警告但能保存"，与 provider 无关 ⇒ 补上 openai 保持原意。
    r = C.post("/api/config", json={"ai_provider": "openai", "ai_mode": "remote",
                                    "ai_base_url": "https://api.example.com",
                                    "ai_model": "example-model-flash", "api_key": "sk-example"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("placeholder_warning"), "占位符必须回 warning：%r" % body
    assert "示例占位" in body["placeholder_warning"][0], body

@pytest.fixture(autouse=True)
def _legacy_discuss_on(monkeypatch):
    """v4 自由发言讨论区已退役（默认 410）；本文件测闸门，故打开兼容开关。"""
    monkeypatch.setenv("AC_ALLOW_LEGACY_DISCUSS", "1")
