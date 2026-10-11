# -*- coding: utf-8 -*-
"""★回归守卫：**注册过的 harness 必须活过重启**。

**为什么写这条**（2026-10-11 实际发生）：
老板接入的 agent 跑得好好的（工作间成员、隔间、桥、审计都有它），可是
`agent_community/data/harnesses.json` 里**只有另一个 harness** —— **它不在里面**。
平台重启（watchdog 重生 / 我改代码重启）后 `load_state()` 按 `harnesses.json` 恢复，
**它就"未注册"了**：工作间里的成员还在，但一切依赖它的动作全 404。

⇒ 性质：**注册成功的 harness 必须写进 `harnesses.json`**，且 `load_state()` 能把它恢复回来。
本测试用**真端点 + 真 `save_state` + 真 `load_state`**，并把数据目录隔离到 tmp_path。

⚠️ 若哪天这条红了：**不要改断言**，去查"注册后到底写没写盘 / 写盘失败有没有说话"。
"""
from __future__ import annotations

import asyncio
import json

import pytest

from agent_community.platform import server as srv
from agent_community.platform import state as state_mod
from agent_community.platform.harness_adapter import harness_manager
from agent_community.platform.routers import harness_register as hr


class FakeRequest:
    def __init__(self, body=None):
        self.headers = {}
        self._body = body if body is not None else {}

    async def json(self):
        return self._body


def run(coro):
    return asyncio.run(coro)


def _reg_body(hid: str) -> dict:
    return {
        "harness_id": hid,
        "harness_name": hid + "-显示名",
        "harness_type": "desktop-agent",
        "wakeup_method": "file_poll",
        "acp_command": "示例执行文件.exe --noop",
        "ai": {"model_name": "示例模型"},
    }


@pytest.fixture()
def iso(tmp_path, monkeypatch):
    """把平台的数据目录隔离到 tmp_path，并保证用例后清掉注册的 harness。"""
    monkeypatch.setattr(srv, "DATA_DIR", tmp_path, raising=False)
    before = set(harness_manager.sessions.keys())
    yield tmp_path
    for k in list(harness_manager.sessions.keys()):
        if k not in before:
            try:
                harness_manager.unregister(k)
            except Exception:
                pass


def test_registered_harness_is_written_to_disk(iso):
    """★核心：注册成功 ⇒ `harnesses.json` 里必须能查到它。"""
    hid = "回归探针-落盘"
    res = run(hr.register_harness(FakeRequest(_reg_body(hid))))
    assert isinstance(res, dict) and res.get("success") is True, res

    hf = iso / "harnesses.json"
    assert hf.exists(), "注册成功后 harnesses.json 没生成 —— 重启后这个 harness 会消失"
    data = json.loads(hf.read_text(encoding="utf-8"))
    ids = list(data.keys()) if isinstance(data, dict) else [x.get("harness_id") for x in data]
    assert hid in ids, f"注册的 harness 不在落盘文件里：{ids}"


def test_load_state_restores_harness_from_disk(iso):
    """★另一半：`load_state()` 必须能把文件里的 harness 恢复进内存（这才叫"活过重启"）。"""
    hid = "回归探针-重启恢复"
    one = _reg_body(hid)
    (iso / "harnesses.json").write_text(
        json.dumps({hid: one}, ensure_ascii=False), encoding="utf-8")

    harness_manager.sessions.pop(hid, None)
    assert hid not in harness_manager.sessions

    srv.load_state()

    assert hid in harness_manager.sessions, \
        "load_state 没能从 harnesses.json 恢复该 harness —— 重启后它会变成『未注册』"
    got = harness_manager.sessions[hid].info
    assert got.harness_id == hid


def test_workshop_member_keeps_pointing_at_harness_after_restore(iso):
    """端到端语义：重启恢复后，工作间里的成员**仍能找到它的 harness**。

    老板那次的现象是"工作间成员还在、但 harness 未注册"——这条把它钉住：
    只要 harness 在盘上，`load_state()` 之后就两边都在。
    """
    from agent_community.platform.state import workshops
    from agent_community.platform.workshop import Workshop, WorkshopMember

    hid = "回归探针-成员指向"
    (iso / "harnesses.json").write_text(
        json.dumps({hid: _reg_body(hid)}, ensure_ascii=False), encoding="utf-8")
    w = Workshop(workshop_id="w-restart", name="重启恢复测试", workspace_dir=str(iso / "ws"),
                 hall_content="任务", members=[WorkshopMember(member_id="m0", role="组长",
                                                             display_name=hid,
                                                             harness_ids=[hid])])
    (iso / "workshops.json").write_text(
        json.dumps({w.workshop_id: w.to_dict()}, ensure_ascii=False), encoding="utf-8")

    harness_manager.sessions.pop(hid, None)
    workshops.pop(w.workshop_id, None)

    srv.load_state()

    assert hid in harness_manager.sessions
    assert w.workshop_id in workshops
    m = workshops[w.workshop_id].members[0]
    assert m.harness_ids == [hid], "成员的 harness 指向必须原样恢复（否则派发找不到它）"
