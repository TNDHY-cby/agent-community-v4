# -*- coding: utf-8 -*-
"""`platform/bridge_selfcheck.py`：**"你的 agent 到底通没通"**（V38 §3 第 3 步）。

老板 2026-10-11 的真事：接了 agent，唤不醒、不回话，却不知道卡在哪。
自检就是把这个"不知道"变成**一句能判定的话**。

本文件锁四态 + 两条纪律：
- 四态：`ok` / `no_reply`（桥取了、agent 没回话）/ `not_picked`（桥没在跑）/ `not_set_up`（还没架桥）；
- ★**不猜**：没证据时**不许**说"通了"；
- ★**每态都要给"怎么办"**（准则⑤ 失败要说话）。
"""
from __future__ import annotations

import json
import os

from agent_community.platform import bridge_selfcheck as sc


def _mk_inbox(tmp_path, *, delivered=False, reply=False, sent=False, failed=False):
    inbox = tmp_path / "inbox"
    inbox.mkdir(parents=True)
    return str(inbox)


def test_send_probe_writes_instructions_and_reply_name(tmp_path):
    inbox = _mk_inbox(tmp_path)
    r = sc.send_probe(inbox, harness_id="示例Harness-A", workshop_id="w1",
                      member_id="m0", role="码农")
    assert r["ok"] is True
    assert os.path.isfile(r["path"])
    payload = json.loads(open(r["path"], encoding="utf-8").read())
    # 载荷里必须写清"怎么回话"（用户/agent 读到就知道做什么）
    assert payload["probe_id"] == r["probe_id"]
    assert payload["reply_file"].startswith("reply_")
    assert "replies/" in payload["message"]
    assert r["probe_id"] in payload["message"], "回话文件名要带 probe_id，平台才好精确认领"


def test_not_picked_when_bridge_not_running(tmp_path):
    """★老板那次的情形：消息还在邮箱里、没人取 ⇒ 必须直说"桥没在运行"。"""
    inbox = _mk_inbox(tmp_path)
    r = sc.send_probe(inbox, harness_id="h", workshop_id="w1", member_id="m0")
    st = sc.inspect_probe(inbox, r["probe_id"])
    assert st["picked_up"] is False
    v = sc.verdict(st)
    assert v["state"] == "not_picked" and v["ok"] is False
    assert "没有在运行" in v["reason"]
    assert "bridge.py" in v["advice"]


def test_no_reply_when_picked_up_but_agent_silent(tmp_path):
    """桥取走了、agent 没回话 ⇒ 说清"卡在 agent 侧"，并给两处自查。"""
    inbox = _mk_inbox(tmp_path)
    r = sc.send_probe(inbox, harness_id="h", workshop_id="w1", member_id="m0")
    d = os.path.join(inbox, "delivered")
    os.makedirs(d, exist_ok=True)
    os.rename(r["path"], os.path.join(d, os.path.basename(r["path"])))
    st = sc.inspect_probe(inbox, r["probe_id"])
    assert st["picked_up"] is True and st["answered"] is False
    v = sc.verdict(st)
    assert v["state"] == "no_reply" and v["ok"] is False
    assert "没等到你的 agent 回话" in v["reason"]
    assert "叫醒命令" in v["advice"], "要指向最常见的原因（TRIGGER_CMD 没填）"


def test_ok_when_agent_replied(tmp_path):
    """收到回话 ⇒ ok，且说明回话在哪（sent/ 还是等转发）。"""
    inbox = _mk_inbox(tmp_path)
    r = sc.send_probe(inbox, harness_id="h", workshop_id="w1", member_id="m0")
    replies = os.path.join(inbox, "replies")
    os.makedirs(os.path.join(replies, "sent"), exist_ok=True)
    open(os.path.join(replies, "sent", r["payload"]["reply_file"]), "w", encoding="utf-8").write("{}")
    st = sc.inspect_probe(inbox, r["probe_id"])
    v = sc.verdict(st)
    assert v["state"] == "ok" and v["ok"] is True
    assert v["detail"]["reply_location"].startswith("replies/sent")


def test_failed_reply_is_reported_not_silently_ok(tmp_path):
    """回话被平台拒收（replies/failed/）⇒ **不能说"通了"**，要给去哪看。"""
    inbox = _mk_inbox(tmp_path)
    r = sc.send_probe(inbox, harness_id="h", workshop_id="w1", member_id="m0")
    os.makedirs(os.path.join(inbox, "replies", "failed"), exist_ok=True)
    open(os.path.join(inbox, "replies", "failed", r["payload"]["reply_file"]), "w",
         encoding="utf-8").write("{}")
    st = sc.inspect_probe(inbox, r["probe_id"])
    v = sc.verdict(st)
    assert v["ok"] is False, "平台没收下 ≠ 通了"
    assert "平台没收下" in v["reason"]
    assert "failed" in v["advice"]


def test_not_set_up_when_no_inbox():
    """还没架桥（没邮箱）⇒ 归 not_set_up，并给"先架桥"。"""
    v = sc.verdict({"picked_up": False, "answered": False}, inbox_exists=False)
    assert v["state"] == "not_set_up"
    assert "架桥" in v["advice"]


def test_selfcheck_end_to_end_returns_three_state(tmp_path):
    """一步到位：没桥在跑时，`selfcheck()` 应回 not_picked 且**写下了**测试文件（可复盘）。"""
    inbox = _mk_inbox(tmp_path)
    out = sc.selfcheck(inbox, harness_id="h", workshop_id="w1", member_id="m0", wait=0)
    assert out["state"] == "not_picked"
    assert out["success"] is False
    assert out["detail"]["probe_id"].startswith(sc.PROBE_PREFIX)
    # 测试文件真的写下去了（不是空跑）
    got = [n for n in os.listdir(inbox) if "task_" in n]
    assert got, "自检必须真的发一条测试消息，否则不是自检"


def test_selfcheck_on_missing_inbox_is_not_set_up(tmp_path):
    out = sc.selfcheck(str(tmp_path / "没有这个邮箱"), harness_id="h",
                       workshop_id="w1", member_id="m0", wait=0)
    assert out["state"] == "not_set_up"
    assert out["success"] is False
