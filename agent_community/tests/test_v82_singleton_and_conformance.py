# -*- coding: utf-8 -*-
"""守卫：V82 三条。

① 单实例：第二个持有者拿不到锁（真 OS 锁）；`release()` 之后能再拿；
   `degraded` 策略下能"起"但标记降级；`/api/status` 带 `singleton`；
② 卡片静态一致性：字段/枚举/scheme/长度逐条报错，**passed 与 failed 分明**；
   `capability` 里 `card_conformance` 与 `agent_can_reply` **互不覆盖**；
③ 名册生命周期口径：7 天隐藏 / 90 天清明细 / 48 周期死号（纯函数）。
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.append(str(_ROOT))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from agent_community.platform import card_conformance as cc  # noqa: E402
from agent_community.platform import harness_capability as hc  # noqa: E402
from agent_community.platform import registry_lifecycle as rl  # noqa: E402
from agent_community.platform import server as srv  # noqa: E402
from agent_community.platform import singleton  # noqa: E402

C = TestClient(srv.app, client=("127.0.0.1", 54321))


# ── ① 单实例 ──────────────────────────────────────────────────
def test_second_holder_is_refused(monkeypatch, tmp_path):
    monkeypatch.setenv("AC_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("AC_SINGLETON_UNAVAILABLE", "fail_closed")
    lease1 = singleton.acquire()
    try:
        with pytest.raises(singleton.SingletonBusy):
            singleton.acquire()
    finally:
        lease1.release()
    # 释放后可以再拿
    lease2 = singleton.acquire()
    lease2.release()


def test_degraded_policy_starts_but_flags(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("AC_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("AC_SINGLETON_UNAVAILABLE", "degraded")
    lease1 = singleton.acquire()
    try:
        lease2 = singleton.acquire()
        assert lease2.degraded is True, "第二实例应当是降级启动"
        out = capsys.readouterr().out
        assert "降级启动" in out, out
        lease2.release()
    finally:
        lease1.release()


def test_status_exposes_singleton(monkeypatch, tmp_path):
    monkeypatch.setenv("AC_DATA_DIR", str(tmp_path))     # 读状态也不许碰真实数据目录
    d = C.get("/api/status").json()
    assert "singleton" in d and "policy" in d["singleton"], d.get("singleton")


# ── ② 卡片静态一致性 ──────────────────────────────────────────
def _info(**kw):
    base = {"harness_id": "示例Harness-A", "harness_name": "示例Harness-A",
            "harness_type": "custom", "wakeup_method": "file_poll", "wakeup_dir": "D:/示例/inbox",
            "capabilities": ["coding"], "ai": {"capabilities": ["coding"]}}
    base.update(kw)
    return base


def test_card_passes_when_fields_ok():
    r = cc.check_card(_info())
    assert r["conformance"] == "passed" and not r["conformance_errors"], r


def test_card_reports_each_field():
    r = cc.check_card(_info(harness_name="", wakeup_method="乱写",
                            wakeup_dir="ftp://x/y", capabilities=[1]))
    fields = {e["field"] for e in r["conformance_errors"]}
    assert r["conformance"] == "failed"
    assert {"harness_name", "wakeup_method", "wakeup_dir", "capabilities"} <= fields, fields


def test_capability_segments_do_not_overwrite_each_other():
    prov = hc.build_provenance(_info())
    segs = prov["segments"]
    assert "card_conformance" in segs and segs["card_conformance"]["state"] == "passed", segs
    # 没有探针记录 ⇒ 回话段必须仍是 unverified（不被"卡片合规"顶成 ok）
    assert segs["agent_can_reply"]["state"] == "unverified", segs["agent_can_reply"]


# ── ③ 名册生命周期 ────────────────────────────────────────────
def test_lifecycle_defaults():
    assert rl.should_hide(7) is True and rl.should_hide(6) is False
    assert rl.should_purge_detail(91) is True and rl.should_purge_detail(90) is False
    assert rl.should_check_dead(48) is True and rl.should_check_dead(47) is False
    p = rl.policy()
    assert p["hide_after_days_all_failing"] == 7 and p["detail_retention_days"] == 90
