# -*- coding: utf-8 -*-
"""守卫：**V28 §2 规则 5「停用通道后不得再投消息」在派发总口把关**（2026-10-19）。

背景（`tools/channel_chain_check.py` 抓到）：这条规则原先只接在 `_activate_single` 一条路上，
于是把某成员的通道停用之后，`leader/assign` 照样把活写进它的 inbox（`dispatched=True`）——
用户看到的是"说了停用、消息照收"的假状态。

判据（按真行为，不看源码文本）：
  ① 通道 `retired` ⇒ 派活返回 `dispatched=false`，且**inbox 里没有新文件**；
  ② 通道 `pending` / `active` ⇒ 照旧派发（**不误伤**未接入/正常成员）。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.append(str(_ROOT))


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("AC_DATA_DIR", str(tmp_path / "data"))
    from fastapi.testclient import TestClient
    from agent_community.platform.server import app
    yield TestClient(app, client=("127.0.0.1", 54321))


def _setup(client, tmp_path):
    inbox = tmp_path / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    r = client.post("/api/harness/register", json={
        "harness_id": "gate-h", "harness_name": "示例Harness-Gate",
        "harness_type": "desktop-agent", "wakeup_method": "file_poll",
        "wakeup_dir": str(inbox), "ai": {"model_name": "示例模型"},
    })
    assert r.status_code == 200, r.text[:200]
    r = client.post("/api/workshop", json={
        "name": "闸门走查", "hall_content": "x",
        "members": [{"role": "码农", "harness_ids": ["gate-h"]}],
    })
    assert r.status_code == 200, r.text[:200]
    ws_id = r.json()["workshop_id"]
    from agent_community.platform.state import workshops
    return ws_id, workshops[ws_id].members[0], inbox


def _assign(client, ws_id: str, text: str):
    return client.post("/api/workshop/%s/leader/assign" % ws_id,
                       json={"member_id": "m0", "instruction": text})


def test_retired_channel_blocks_dispatch(client, tmp_path):
    ws_id, m, inbox = _setup(client, tmp_path)
    m.channel_state = "retired"
    before = sorted(p.name for p in inbox.glob("*"))
    r = _assign(client, ws_id, "停用后还想派活")
    assert r.status_code == 200, r.text[:200]
    body = r.json()
    assert body["dispatched"] is False, "通道已停用还派成功＝假状态：%s" % body
    assert "已停用" in str(body.get("dispatch_note") or ""), "被拒也要说清为什么：%s" % body
    after = sorted(p.name for p in inbox.glob("*"))
    assert after == before, "被拒的派发不许在 inbox 里落件：%s" % after


@pytest.mark.parametrize("state", ["pending", "active"])
def test_non_retired_channel_still_dispatches(client, tmp_path, state):
    ws_id, m, inbox = _setup(client, tmp_path)
    m.channel_state = state
    r = _assign(client, ws_id, "正常派活")
    assert r.status_code == 200, r.text[:200]
    assert r.json()["dispatched"] is True, "未接入/正常成员不许被这层闸门误伤：%s" % r.json()
    assert list(inbox.glob("*")), "派发成功就应该真落件（inbox 里要有文件）"
