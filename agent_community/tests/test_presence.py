# -*- coding: utf-8 -*-
"""守卫（`V131` 痕迹可见性的"人"维度，2026-11-08）：**成员在不在动**。

判据都是纯函数真结果：

  ① 有在途任务 + 超阈值 ⇒ `silent=True`，且 `why` 说清"最后痕迹在什么时候 / 从没痕迹"；
  ② 有在途任务 + 没超阈值 ⇒ 不算失联（是"在干、只是慢"）；
  ③ **没有在途任务**的空闲成员 ⇒ 永远不算失联（不制造噪音）；
  ④ 组长（`_lead`）不进这张表（他拍板，不按"在不在干"衡量）；
  ⑤ 最后痕迹来自**产物事件**时，`last_seen_channel=artifact`；来自任务账本时是 `task`；
  ⑥ 阈值可配（`silent_min` 直传优先）。
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.append(str(_ROOT))

from agent_community.platform import presence as P  # noqa: E402

NOW = 1_800_000_000.0


def _m(seq: int, slot: str, **kw) -> dict:
    d = {"member_seq": seq, "slot_dir": slot, "harness": "h%d" % seq, "state": "active", "tier": "a_protocol"}
    d.update(kw)
    return d


def _t(seq: int, state: str, *, age_min: float, tid: int = 1) -> dict:
    return {"member_seq": seq, "state": state, "task_seq": tid,
            "updated_at": NOW - age_min * 60.0}


def test_01_silent_when_open_task_and_over_threshold():
    out = P.plan([_m(1, "前端组/员工01")], [_t(1, "working", age_min=300)], [], now=NOW)
    row = out["members"][0]
    assert row["silent"] is True and "最后痕迹" in row["why"], row


def test_02_not_silent_under_threshold():
    out = P.plan([_m(1, "前端组/员工01")], [_t(1, "working", age_min=10)], [], now=NOW)
    assert out["members"][0]["silent"] is False


def test_03_idle_member_is_never_silent():
    out = P.plan([_m(2, "后端组/员工01")], [], [], now=NOW)
    row = out["members"][0]
    assert row["silent"] is False and row["open_tasks"] == 0


def test_04_lead_is_not_in_the_table():
    out = P.plan([_m(9, "_lead"), _m(1, "前端组/员工01")], [], [], now=NOW)
    slots = [r["slot_dir"] for r in out["members"]]
    assert "_lead" not in slots and "前端组/员工01" in slots


def test_05_artifact_event_counts_as_trace():
    ev = {"member_seq": 1, "slot_dir": "前端组/员工01", "ts": NOW - 60, "file": "a.md"}
    out = P.plan([_m(1, "前端组/员工01")], [_t(1, "working", age_min=300)], [ev], now=NOW)
    row = out["members"][0]
    assert row["last_seen_channel"] == "artifact" and row["silent"] is False, row


def test_06_threshold_is_configurable():
    tasks = [_t(1, "working", age_min=300)]
    assert P.plan([_m(1, "a")], tasks, [], now=NOW, silent_min=600)["members"][0]["silent"] is False
    assert P.plan([_m(1, "a")], tasks, [], now=NOW, silent_min=30)["members"][0]["silent"] is True


def test_07_no_trace_at_all_is_silent_with_clear_why():
    task = {"member_seq": 1, "state": "issued", "task_seq": 1, "task_id": "T-001"}  # 没有任何时间戳
    out = P.plan([_m(1, "a")], [task], [], now=NOW)
    row = out["members"][0]
    assert row["silent"] is True and "没有任何痕迹" in row["why"], row


def test_08_delivered_is_not_open():
    out = P.plan([_m(1, "a")], [_t(1, "delivered", age_min=999)], [], now=NOW)
    assert out["members"][0]["open_tasks"] == 0 and out["members"][0]["silent"] is False

def test_09_rework_counts_as_open_task():
    """被返工等他重交 ⇒ 算在途（否则"被返工的人不动"永远看不出来）。"""
    out = P.plan([_m(1, "a")], [_t(1, "rework", age_min=600)], [], now=NOW)
    row = out["members"][0]
    assert row["open_tasks"] == 1 and row["silent"] is True, row
