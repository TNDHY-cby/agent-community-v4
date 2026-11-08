# -*- coding: utf-8 -*-
"""守卫（`V134` ⑧ 延伸，2026-11-07）：**催办** —— "做了没回执 / 交了没验收"要主动说话。

判据都是纯函数真结果：

  ① `issued` 超阈值 ⇒ 催成员回执；`delivered` 超阈值 ⇒ 催组长验收；
  ② 没超阈值的不催；
  ③ **没有时间戳的不催**（判不了时长，不假装知道）；
  ④ 已了结（`accepted/closed/refuted/reviewed`）不催；
  ⑤ 同一条任务同一天只催一次（`ref_of` 稳定，换个 `day` 就是新的一条）；
  ⑥ 阈值可配（`issued_min` / `delivered_min` 直传优先于默认）。
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.append(str(_ROOT))

from agent_community.platform import nudge as N  # noqa: E402

NOW = 1_800_000_000.0


def _t(seq: int, state: str, *, age_min: float, slot="前端组/员工01", **kw) -> dict:
    d = {"task_seq": seq, "state": state, "slot_dir": slot, "member_seq": 1,
         "updated_at": NOW - age_min * 60.0}
    d.update(kw)
    return d


def test_01_issued_over_threshold_nudges_member():
    out = N.plan([_t(1, "issued", age_min=45)], now=NOW)
    assert out["count"] == 1 and out["items"][0]["kind"] == "member_no_receipt"
    assert "task-result" in out["items"][0]["ask"]


def test_02_delivered_over_threshold_nudges_lead():
    out = N.plan([_t(2, "delivered", age_min=90)], now=NOW)
    assert out["count"] == 1 and out["items"][0]["kind"] == "lead_no_review"
    assert "review" in out["items"][0]["ask"]


def test_03_under_threshold_is_quiet():
    out = N.plan([_t(1, "issued", age_min=5), _t(2, "delivered", age_min=5)], now=NOW)
    assert out["count"] == 0, out


def test_04_no_timestamp_is_not_guessed():
    task = {"task_seq": 9, "state": "issued", "slot_dir": "x"}
    assert N.plan([task], now=NOW)["count"] == 0, "没有时间戳就判不了，不许瞎催"


def test_05_done_states_are_skipped():
    tasks = [_t(1, "accepted", age_min=999), _t(2, "closed", age_min=999),
             _t(3, "refuted", age_min=999), _t(4, "reviewed", age_min=999)]
    assert N.plan(tasks, now=NOW)["count"] == 0


def test_06_ref_is_stable_and_day_scoped():
    item = {"kind": "member_no_receipt", "task_seq": 3}
    a = N.ref_of(item, "2026-11-07")
    b = N.ref_of(item, "2026-11-07")
    c = N.ref_of(item, "2026-11-08")
    assert a == b and a != c and a.startswith("nudge:")


def test_07_thresholds_are_configurable():
    tasks = [_t(1, "issued", age_min=45)]
    assert N.plan(tasks, now=NOW, issued_min=60)["count"] == 0
    assert N.plan(tasks, now=NOW, issued_min=10)["count"] == 1


def test_08_summarize_is_short_per_slot():
    tasks = [_t(i, "issued", age_min=45) for i in range(1, 6)]
    out = N.plan(tasks, now=NOW)
    lines = N.summarize(out["items"])
    assert len(lines) == 1 and "催办" in lines[0], lines
    assert "T-001" in lines[0]

def test_09_rework_over_threshold_nudges_redelivery():
    """被返工了但没重交 ⇒ 也要催（以前这一支谁都不管，任务永久卡在 rework）。"""
    out = N.plan([_t(3, "rework", age_min=45)], now=NOW)
    assert out["count"] == 1 and out["items"][0]["kind"] == "member_no_rework_delivery"
    assert "改完再交" in out["items"][0]["ask"]
    assert out["counts"]["member_no_rework_delivery"] == 1


def test_10_rework_under_threshold_is_quiet():
    assert N.plan([_t(3, "rework", age_min=5)], now=NOW)["count"] == 0
