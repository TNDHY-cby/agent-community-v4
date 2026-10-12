# -*- coding: utf-8 -*-
"""★"运行中改名单不该把已有成员的**桥 / 会话绑定**弄断"（2026-10-12 修复）。

**病灶（读源码发现的真缺陷）**：`POST /api/workshop/{ws}/members` 在 `running` 状态下
按 `harness_id` 做增删 diff，但落库时是**整表替换**：

    new_members = [WorkshopMember(member_id=f"m{i}", ...) for i, m in enumerate(members_raw)]
    ...
    elif hid in old_hid2m:
        m.status = old_hid2m[hid].status      # 只搬了 status
        m.session = old_hid2m[hid].session    # 和 session
    ws.members = new_members

⇒ 一旦"运行中"编辑名单，成员的 `member_id` 会被**按序号重排**，而
    `bridge_ref`（**这条桥 ↔ 这个会话**的绑定 —— 平台的核心模型）、
    `session_ref` / `session_aliases`、`channel_seq` / `channel_state`
**全部丢失**：改一次名单 = 已有成员与桥/会话断链。

**修法**：绑定同一个 harness 的成员，**直接继承旧成员对象**，只覆盖名单里可编辑的字段
（role / display_name / harness_ids）。

本文件锁住这个行为，避免以后"重构名单同步"时又退回整表替换。
"""
from __future__ import annotations

import asyncio
import json

import pytest


def _mk_ws(hid: str = "h1", status: str = "running"):
    from agent_community.platform.workshop import Workshop, WorkshopMember

    m = WorkshopMember(member_id="m0", role="码农", display_name="老张", harness_ids=[hid])
    # 模拟"已经架好桥、绑好会话"的状态
    m.bridge_ref = {
        "template_name": "某桥", "bridge_path": r"D:\x\bridge.py",
        "session_ref": {"id": "sess-1"}, "session_name": "ws-项目-码农-m0",
        "state": "attached", "verified": True, "verified_at": 123.0,
        "channel_seq": 3, "artifact_kind": "file_poll",
    }
    m.session_ref = {"id": "sess-1"}
    m.session_aliases = ["ws-项目-码农-m0", "旧名"]
    m.channel_seq = 3
    m.channel_state = "open"
    m.session = "ws-项目-码农-m0"
    ws = Workshop(workshop_id="ws1", name="测试间", workspace_dir="", hall_content="",
                  members=[m])
    ws.status = status
    return ws, m


class _Req:
    def __init__(self, body):
        self._body = body
        self.headers = {}
        self.query_params = {}

    async def json(self):
        return self._body


def test_editing_roster_keeps_bridge_and_session_bindings(monkeypatch):
    """★核心：同一个 harness、只改了角色的成员，必须**保住桥/会话绑定**。"""
    from agent_community.platform import state as st
    from agent_community.platform.routers import workshops_assign as wa

    ws, old_m = _mk_ws("h1")
    st.workshops["ws1"] = ws
    monkeypatch.setattr(wa, "save_state", lambda: None, raising=False)
    # 别真的去激活/清扫
    import agent_community.platform.server as _srv
    monkeypatch.setattr(_srv, "_activate_single", lambda *a, **k: None, raising=False)

    body = {"members": [{"role": "测试", "display_name": "老张", "harness_ids": ["h1"]}]}
    try:
        out = asyncio.run(wa.workshop_save_members("ws1", _Req(body)))
    finally:
        st.workshops.pop("ws1", None)

    assert isinstance(out, dict) and out.get("success") is True, out
    m = ws.members[0]
    assert m.role == "测试", "名单里可编辑的字段要生效"
    assert m.bridge_ref and m.bridge_ref.get("session_name") == "ws-项目-码农-m0", \
        "★桥绑定被整表替换弄丢了（member_id 被重排 ⇒ bridge_ref 丢失）"
    assert m.bridge_ref.get("verified") is True, "验证状态也要保住"
    assert m.session_ref == {"id": "sess-1"}, "会话引用丢了"
    assert m.session_aliases == ["ws-项目-码农-m0", "旧名"], "会话别名丢了"
    assert m.channel_seq == 3 and m.channel_state == "open", "通道序号/状态丢了"
    assert m.member_id == "m0", "同一成员的 member_id 不该被重排"


def test_new_member_still_gets_activated(monkeypatch):
    """反向：**新增**成员这条路不能被改坏（仍要激活）。"""
    from agent_community.platform import state as st
    from agent_community.platform.routers import workshops_assign as wa

    ws, _ = _mk_ws("h1")
    st.workshops["ws1"] = ws
    monkeypatch.setattr(wa, "save_state", lambda: None, raising=False)
    # ⚠️ _activate_single 是在函数体内 rom ..server import ... 进来的
    #   ⇒ 补 router 模块没用，必须补 **server 模块的属性**（我第一版补错了，
    #     于是真的去跑激活、AI 未配置 ⇒ 静默 blocked、activated 为空）。
    import agent_community.platform.server as srv
    activated = []
    monkeypatch.setattr(srv, "_activate_single",
                        lambda w, m: activated.append(m.member_id), raising=False)

    body = {"members": [
        {"role": "码农", "display_name": "老张", "harness_ids": ["h1"]},
        {"role": "测试", "display_name": "小李", "harness_ids": ["h2"]},
    ]}
    try:
        out = asyncio.run(wa.workshop_save_members("ws1", _Req(body)))
    finally:
        st.workshops.pop("ws1", None)

    assert out.get("success") is True, out
    assert len(activated) == 1, "新增的那个成员应被激活一次：%r" % activated
    assert len(ws.members) == 2
    kinds = {m.role for m in ws.members}
    assert kinds == {"码农", "测试"}, kinds
