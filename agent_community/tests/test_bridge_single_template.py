# -*- coding: utf-8 -*-
"""V41 裁定 1B：**一个 harness 只允许一条通用桥模板**（老板 2026-10-11 18:13 拍板）。

裁定原文：「1B；2A」——
- **1B** = 一个 harness **只允许一条**通用桥模板（问题 1 选 B）；
- **2A** = 工作间里换桥 = **停用旧通道 + 建新通道**，旧记录保留可回看（问题 2 选 A）。

本文件锁 1B 的落法，要点有三：
1. 建第二条 ⇒ 活动列表里**只剩本次那条**；
2. 被顶掉的那条**不删除**，移入 `superseded`（保留 name/kind/params/时间，可回看）——
   与项目一贯的「停用≠删除」一致；
3. **如实告知**（`note` + `superseded`），不许静默。

⚠️ 注意分层：`bridge_templates.put` 是**存储层**（"同名覆盖、可并存"有 17 条既有测试依赖，
   本文件**不动它**）；"只允许一条"是**产品裁定**，实现在 API 层（`_collapse_to_single`）。
"""
from __future__ import annotations

import asyncio

import pytest

from agent_community.platform import bridge_templates as bt
from agent_community.platform.routers import harness_bridge as hb


class _Info:
    def __init__(self):
        self.metadata = {}


class _Sess:
    def __init__(self):
        self.info = _Info()


class _Req:
    def __init__(self, body):
        self._body = body

    async def json(self):
        return self._body


def run(c):
    return asyncio.run(c)


@pytest.fixture()
def sess(monkeypatch):
    s = _Sess()
    monkeypatch.setattr(hb.harness_manager, "sessions", {"h1": s}, raising=False)
    monkeypatch.setattr(hb, "save_state", lambda: None, raising=False)
    import agent_community.platform.server as srv
    monkeypatch.setattr(srv, "save_state", lambda: None, raising=False)
    return s


def test_second_template_replaces_the_first(sess):
    """★核心：建第二条 ⇒ 活动列表只剩一条（本次那条）。"""
    run(hb.harness_bridge_template_put("h1", _Req({"name": "桥A", "kind": "file_poll",
                                                   "params": {"INBOX_DIR": r"D:\x"}})))
    run(hb.harness_bridge_template_put("h1", _Req({"name": "桥B", "kind": "cli_acp",
                                                   "params": {"ACP_COMMAND": "a.exe"}})))
    items = bt.list_all(sess.info)
    assert len(items) == 1, f"1B 要求只留一条，实得 {[t.get('name') for t in items]}"
    assert items[0]["name"] == "桥B", "留下的应是本次写入的那条"


def test_superseded_is_kept_not_deleted(sess):
    """★被顶掉的那条**不能静默删**：要留在 `superseded` 里可回看。"""
    run(hb.harness_bridge_template_put("h1", _Req({"name": "桥A", "kind": "file_poll",
                                                   "params": {"INBOX_DIR": r"D:\x"}})))
    res = run(hb.harness_bridge_template_put("h1", _Req({"name": "桥B", "kind": "cli_acp",
                                                         "params": {"ACP_COMMAND": "a.exe"}})))
    assert res.get("superseded") == ["桥A"], res.get("superseded")
    # 历史存在 harness.metadata 的独立键下（不能塞进模板 dict：`_normalize_one` 会丢未知键）
    hist = (sess.info.metadata or {}).get("bridge_template_history") or []
    assert len(hist) == 1
    assert hist[0]["name"] == "桥A"
    assert hist[0]["kind"] == "file_poll", "历史里要留住它当时的类型（可回看）"
    assert hist[0].get("superseded_at"), "要记什么时候被顶掉的"
    assert hist[0].get("why"), "要说清为什么被顶掉（V41 裁定 1B）"


def test_response_speaks_about_the_collapse(sess):
    """如实告知（准则⑤）：响应里要有 note + single_template，不许静默收敛。"""
    run(hb.harness_bridge_template_put("h1", _Req({"name": "桥A", "kind": "file_poll",
                                                   "params": {"INBOX_DIR": r"D:\x"}})))
    res = run(hb.harness_bridge_template_put("h1", _Req({"name": "桥B", "kind": "cli_acp",
                                                         "params": {"ACP_COMMAND": "a.exe"}})))
    assert res.get("single_template") is True
    assert "只保留一条" in (res.get("note") or ""), res.get("note")
    assert "桥A" in (res.get("note") or ""), "要点名被收敛掉的那条"


def test_first_template_reports_no_supersede(sess):
    res = run(hb.harness_bridge_template_put("h1", _Req({"name": "唯一桥", "kind": "file_poll",
                                                         "params": {"INBOX_DIR": r"D:\x"}})))
    assert res.get("superseded") == []
    assert "唯一的一条" in (res.get("note") or "")


def test_storage_layer_still_allows_many(sess):
    """分层守卫：**存储层语义不变**（`put` 仍可并存），"只允许一条"只在 API 层。"""
    bt.put(sess.info, {"name": "x1", "kind": "file_poll", "params": {}})
    bt.put(sess.info, {"name": "x2", "kind": "cli_acp", "params": {}})
    assert len(bt.list_all(sess.info)) == 2, \
        "存储层若被改成只留一条，会破坏既有 17 条 bridge_templates 测试的契约"
