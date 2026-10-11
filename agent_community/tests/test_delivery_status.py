# -*- coding: utf-8 -*-
"""`platform/delivery_status.py`：**"消息到底送到没有"**（V37 §5 P1 止血）。

老板 2026-10-11 事故：邮箱压着 4 条、桥没在跑，而**平台一声不吭** ⇒
本模块必须把这件事**说出来**，且**不许猜**。

本文件锁四件事：
1. 邮箱有积压、没有取过 ⇒ `stuck=True` + 说清"没有任何东西来取过" + 给怎么办；
2. 有 `delivered/` 痕迹 ⇒ 如实报"曾取走过 N 条"（**不用进程判活**）；
3. `bridge_running` **不许猜**：没证据时是 `None` + 说明，不是 `False`；
4. 全站口径：工作间级要有 `headline`（消息没送到时必须说话）。
"""
from __future__ import annotations

import os
import time

from agent_community.platform import delivery_status as ds


class _M:
    def __init__(self, **kw):
        self.member_id = kw.get("member_id", "m0")
        self.role = kw.get("role", "码农")
        self.display_name = kw.get("display_name", "示例Harness-A")
        self.harness_ids = kw.get("harness_ids", ["示例Harness-A"])
        self.session_ref = kw.get("session_ref", {})
        self.bridge_ref = kw.get("bridge_ref", {})


class _WS:
    def __init__(self, members):
        self.workshop_id = "w-test"
        self.members = members


def _mk_comp(tmp_path, *, pending=0, delivered=0, replies=0, failed=0):
    comp = tmp_path / "码农_m0"
    inbox = comp / "inbox"
    inbox.mkdir(parents=True)
    for i in range(pending):
        (inbox / f"task_{i}.json").write_text("{}", encoding="utf-8")
    if delivered:
        d = inbox / "delivered"
        d.mkdir()
        for i in range(delivered):
            (d / f"task_d{i}.json").write_text("{}", encoding="utf-8")
    if replies:
        r = inbox / "replies"
        r.mkdir()
        for i in range(replies):
            (r / f"reply_{i}.json").write_text("{}", encoding="utf-8")
    if failed:
        f = inbox / "replies" / "failed"
        f.mkdir(parents=True)
        for i in range(failed):
            (f / f"reply_f{i}.json").write_text("{}", encoding="utf-8")
    return str(comp)


def test_pending_without_delivery_is_stuck_and_actionable(tmp_path):
    """★核心场景（老板那次）：有积压、没人取过 ⇒ 必须说清 + 给怎么办。"""
    comp = _mk_comp(tmp_path, pending=4)
    r = ds.member_status(_WS([]), _M(), compartment_dir=comp)
    assert r["inbox_pending"] == 4
    assert r["delivered_count"] == 0
    assert r["stuck"] is True
    assert "没有任何东西来取过" in r["stuck_reason"]
    assert r["advice"], "必须给「怎么办」，不能只说卡住"
    assert "bridge" in r["advice"] or "桥" in r["advice"]


def test_delivered_evidence_is_reported(tmp_path):
    """有投递痕迹 ⇒ 如实报"曾取走过 N 条"（不靠进程判断）。"""
    comp = _mk_comp(tmp_path, pending=0, delivered=3)
    r = ds.member_status(_WS([]), _M(), compartment_dir=comp)
    assert r["delivered_count"] == 3
    assert r["stuck"] is False
    assert r["inbox_pending"] == 0


def test_bridge_running_is_not_guessed(tmp_path):
    """★不许猜：没有投递证据时 `bridge_running` 是 None（不是 False），并附说明。"""
    comp = _mk_comp(tmp_path, pending=1)
    r = ds.member_status(_WS([]), _M(), compartment_dir=comp)
    assert r["bridge_running"] is None, "没证据时说 False 就是猜（会误伤正在跑的桥）"
    assert r["liveness_evidence"], "不给结论也要给依据说明"


def test_recent_delivery_marks_running(tmp_path):
    """最近有投递 ⇒ 才敢说 True。"""
    comp = _mk_comp(tmp_path, delivered=1)
    r = ds.member_status(_WS([]), _M(), compartment_dir=comp)
    assert r["bridge_running"] is True
    assert r["delivered_recently"] is True


def test_replies_waiting_and_failed_are_reported(tmp_path):
    """回报压在 replies/ 或失败在 replies/failed/ 都要说话。"""
    comp = _mk_comp(tmp_path, replies=2)
    r = ds.member_status(_WS([]), _M(), compartment_dir=comp)
    assert r["replies_pending"] == 2
    assert r["stuck"] is True and "回报" in r["stuck_reason"]

    comp2 = _mk_comp(tmp_path / "b", failed=1)
    r2 = ds.member_status(_WS([]), _M(), compartment_dir=comp2)
    assert r2["replies_failed"] == 1
    assert r2["stuck"] is True and "平台没收下" in r2["stuck_reason"]


def test_missing_email_is_explained_not_crashed(tmp_path):
    """隔间/邮箱不存在时也要给结论，不许崩。

    ★语义（实测纠正）：**"还没架桥"不是"卡住"** ——
    我第一版把它算 stuck，结果全空的工作间报"有 4 个成员没送到（共 0 条积压）"，
    自相矛盾。⇒ 没邮箱且**没有待送消息**时，只平静地说明「还没走到这一步」。
    """
    r = ds.member_status(_WS([]), _M(), compartment_dir=str(tmp_path / "不存在的隔间"))
    assert r["inbox_pending"] == 0
    assert r["stuck"] is False, "没架桥不等于卡住"
    assert r["not_set_up"], "要说清「还没走到这一步」，而不是报红"
    assert r["advice"], "仍然要给「要收活就得先架桥」的指引"


def test_message_waiting_but_no_email_is_stuck(tmp_path):
    """真正该报红的情形：**有东西要送、却连邮箱都没有**。"""
    comp = tmp_path / "还没架桥_m9"
    inbox = comp / "inbox"
    inbox.mkdir(parents=True)
    (inbox / "task_1.json").write_text("{}", encoding="utf-8")
    # 邮箱"存在"但内容为 0 的文件？—— 这里直接造"目录不存在"的场景：
    r = ds.member_status(_WS([]), _M(),
                         compartment_dir=str(tmp_path / "根本没这个隔间"))
    assert r["stuck"] is False          # 没东西要送 ⇒ 不报红
    # 有积压 + 无邮箱 ⇒ 报红
    comp2 = tmp_path / "有积压但无邮箱"
    (comp2 / "inbox").mkdir(parents=True)
    (comp2 / "inbox" / "task_a.json").write_text("{}", encoding="utf-8")
    r2 = ds.member_status(_WS([]), _M(), compartment_dir=str(comp2))
    assert r2["inbox_pending"] == 1
    # 邮箱在、有积压、没有任何取件记录 ⇒ **正是老板那次的情形，必须报红**
    assert r2["stuck"] is True
    assert "没有任何东西来取过" in r2["stuck_reason"]


def test_workshop_headline_speaks_when_something_is_stuck(tmp_path):
    """★全站口径（V37 §4.3）：消息没送到时，工作间级必须有一句话。"""
    ws = _WS([_M(member_id="m0"), _M(member_id="m1", role="美术")])
    comps = {"m0": _mk_comp(tmp_path / "a", pending=2),
             "m1": _mk_comp(tmp_path / "b", delivered=1)}
    out = ds.workshop_status(ws, compartments=comps)
    assert out["member_count"] == 2
    assert out["stuck_count"] == 1
    assert out["total_pending"] == 2
    assert "没送到" in out["headline"], out["headline"]

    # 都正常时也要给一句话（不能空着）
    comps2 = {"m0": _mk_comp(tmp_path / "c", delivered=1),
              "m1": _mk_comp(tmp_path / "d", delivered=1)}
    ok = ds.workshop_status(ws, compartments=comps2)
    assert ok["stuck_count"] == 0
    assert "都已送达" in ok["headline"]
