# -*- coding: utf-8 -*-
"""守卫：**对外任务回执自动收回**（V63 §6/§7）。

判据（按真行为）：
  ① **默认关**：不设 `AC_PEER_POLL_ENABLED` 时 `is_enabled()` 为 False（自动对外发请求要用户点头）；
  ② 设 `1` 才开；间隔/每轮条数可配且有上下限；
  ③ **开启后真能自动推进**：一轮之后本地记录从 `sent` 变 `working`，时间线留下 `check` 事件，
     并且审计里是 `peer.task.auto`（**与人工点刷新区分开**）；
  ④ **失败不吞**：对端不可达 ⇒ 标 `unreachable`（不留在 working），并记下退避时间。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.append(str(_ROOT))


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("AC_DATA_DIR", str(tmp_path / "data"))
    from agent_community.platform import peer_poll, peer_tasks, peers
    peer_poll.stop()
    peer_poll._LAST.clear()
    yield peer_poll, peer_tasks, peers
    peer_poll.stop()
    peer_poll._LAST.clear()


def test_disabled_by_default(env):
    poll, _t, _p = env
    assert poll.is_enabled() is False, "自动对外请求必须默认关"
    assert poll.start() is False, "默认配置下不许真的起线程"
    st = poll.status()
    assert st["enabled"] is False and st["running"] is False


def test_enable_switch_and_limits(env, monkeypatch):
    poll, _t, _p = env
    monkeypatch.setenv("AC_PEER_POLL_ENABLED", "1")
    monkeypatch.setenv("AC_PEER_POLL_INTERVAL", "5")          # 下限 10
    monkeypatch.setenv("AC_PEER_POLL_MAX", "99")              # 上限 20
    assert poll.is_enabled() is True
    assert poll.interval() == 10, "间隔必须钳到 >=10s，别把对端打爆"
    assert poll.max_per_round() == 20


def test_tick_advances_status_and_records_timeline(env, monkeypatch):
    poll, tasks, peers = env
    peers.upsert_peer({"peer_id": "co-b", "display_name": "公司 B",
                       "base_url": "http://127.0.0.1:9", "token": "your-frp-token"})
    rec = tasks.create("co-b", "公司 B", "派一趟活", "remote-1")
    assert tasks.get(rec["pt_id"])["status"] == "sent"

    # mock 网络层：对端说在做
    import agent_community.a2a_client as client
    monkeypatch.setattr(client, "get_task",
                        lambda *a, **k: {"id": "remote-1", "status": "working"}, raising=False)
    out = poll.tick(now=1e9)
    assert out["checked"] >= 1
    after = tasks.get(rec["pt_id"])
    assert after["status"] == "working", "自动收回没把状态推进"
    assert any(x["event"] == "check" for x in after["timeline"]), "时间线没留痕"


def test_tick_marks_unreachable_on_failure_and_backs_off(env, monkeypatch):
    poll, tasks, peers = env
    peers.upsert_peer({"peer_id": "co-c", "display_name": "公司 C",
                       "base_url": "http://127.0.0.1:9", "token": "your-frp-token"})
    rec = tasks.create("co-c", "公司 C", "断线测试", "remote-2")

    import agent_community.a2a_client as client

    def boom(*a, **k):
        raise ConnectionRefusedError("refused")

    monkeypatch.setattr(client, "get_task", boom, raising=False)
    out = poll.tick(now=1e9)
    after = tasks.get(rec["pt_id"])
    assert after["status"] == "unreachable", "对端断了却还显示 %s" % after["status"]
    assert "refused" in after["last_check"]["detail"]
    assert poll._LAST.get(rec["pt_id"], {}).get("next", 0) > 1e9, "失败后必须退避，别每轮都撞"
