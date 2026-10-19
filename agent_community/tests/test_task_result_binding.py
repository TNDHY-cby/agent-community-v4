# -*- coding: utf-8 -*-
"""守卫：**多成员并发**的两条硬约束（2026-10-19 由 `tools/task_parallel_check.py` 抓到）。

判据（全部按真行为，不看源码文本）：

  ① **冒名回报必须拦住**：回报的 `harness_id` 不是该成员绑定的那条 ⇒ 403，且**不落进度**
     （此前只校验 token 属于 body.harness_id，于是 A 能替 B 交活：串件 + 假完成，界面看着正常）；
  ② **旧桥不被误伤**：回报不带 `harness_id`（或该成员未绑 harness）⇒ 照旧放行；
  ③ **建间的默认值不许是假数据**：不传 `display_name` 时取该成员绑定 harness 的注册名
     （**不再全员同名"示例Harness-E"**）；不传 `harness_ids` 时就是**空**（不假装绑了一个不存在的 harness）。
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


def _register(client, hid: str, name: str, inbox: Path) -> str:
    """注册一只 harness，返回它的 agent_token（回报必须带，否则 401 —— 这本身是对的）。"""
    r = client.post("/api/harness/register", json={
        "harness_id": hid, "harness_name": name, "harness_type": "desktop-agent",
        "wakeup_method": "file_poll", "wakeup_dir": str(inbox),
        "ai": {"model_name": "示例模型"},
    })
    assert r.status_code == 200, r.text[:300]
    return str((r.json() or {}).get("agent_token") or "")


def _workshop(client, members):
    r = client.post("/api/workshop", json={
        "name": "并发守卫", "hall_content": "两人各写一块", "members": members,
    })
    assert r.status_code == 200, r.text[:300]
    return r.json()["workshop_id"]


# ── ③ 默认值不许是假数据 ────────────────────────────────────────────────
def test_create_workshop_derives_names_and_never_fakes_binding(client, tmp_path):
    _register(client, "bind-a", "示例Harness-BindA", tmp_path / "ia")
    _register(client, "bind-b", "示例Harness-BindB", tmp_path / "ib")
    ws_id = _workshop(client, [{"role": "码农", "harness_ids": ["bind-a"]},
                               {"role": "码农", "harness_ids": ["bind-b"]}])
    body = client.get("/api/workshop/%s" % ws_id).json()
    names = [m["display_name"] for m in body["members"]]
    assert names == ["示例Harness-BindA", "示例Harness-BindB"], \
        "不传 display_name 时应取绑定 harness 的注册名：%r" % names
    assert names[0] != names[1], "两个成员撞同一个名 ⇒ 界面上分不清谁在干活"

    ws2 = _workshop(client, [{"role": "码农"}])
    m = client.get("/api/workshop/%s" % ws2).json()["members"][0]
    assert m["harness_ids"] == [], "不传 harness_ids 时不许绑一个写死的示例 harness（开箱假数据）：%r" % m


# ── ①② 回报的归属校验 ──────────────────────────────────────────────────
def test_report_from_wrong_harness_is_rejected(client, tmp_path):
    tok_a = _register(client, "bind-a", "示例Harness-BindA", tmp_path / "ia")
    _register(client, "bind-b", "示例Harness-BindB", tmp_path / "ib")
    ws_id = _workshop(client, [{"role": "码农", "harness_ids": ["bind-a"]},
                               {"role": "码农", "harness_ids": ["bind-b"]}])
    before = len(client.get("/api/workshop/%s" % ws_id).json().get("discussion") or [])
    r = client.post("/api/harness/task-result", json={
        "workshop_id": ws_id, "member_id": "m1", "harness_id": "bind-a",
        "ok": True, "result": "冒名：拿 A 的身份替 B 交活",
    }, headers={"X-Agent-Token": tok_a})
    assert r.status_code == 403, "A 的 harness 不许替 m1（绑的是 B）交活：%s" % r.text[:200]
    body = r.json()
    assert "绑定不一致" in body["error"] and body["member_bound_harness"] == ["bind-b"]
    after = len(client.get("/api/workshop/%s" % ws_id).json().get("discussion") or [])
    assert after == before, "被拒的冒名回报不许落任何进度（讨论区不许出现）"


def test_report_from_bound_harness_is_accepted(client, tmp_path):
    tok_a = _register(client, "bind-a", "示例Harness-BindA", tmp_path / "ia")
    _register(client, "bind-b", "示例Harness-BindB", tmp_path / "ib")
    ws_id = _workshop(client, [{"role": "码农", "harness_ids": ["bind-a"]},
                               {"role": "码农", "harness_ids": ["bind-b"]}])
    r = client.post("/api/harness/task-result", json={
        "workshop_id": ws_id, "member_id": "m0", "harness_id": "bind-a",
        "ok": True, "result": "正常的本人回报",
    }, headers={"X-Agent-Token": tok_a})
    assert r.status_code == 200, r.text[:200]
    disc = client.get("/api/workshop/%s" % ws_id).json().get("discussion") or []
    assert any("正常的本人回报" in str(m.get("content") or "") and
               str(m.get("from_member") or "") == "m0" for m in disc), \
        "本人回报必须挂在自己名下（from_member=m0）"


def test_report_without_harness_id_still_accepted_for_old_bridges(client):
    """旧桥只带 workshop_id/member_id（文档格式本来就是这样）⇒ 不许被新校验误伤。"""
    ws_id = _workshop(client, [{"role": "码农", "harness_ids": ["bind-a"]}])
    r = client.post("/api/harness/task-result", json={
        "workshop_id": ws_id, "member_id": "m0", "ok": True, "result": "旧桥回报：不带 harness_id",
    })
    assert r.status_code == 200, r.text[:200]
