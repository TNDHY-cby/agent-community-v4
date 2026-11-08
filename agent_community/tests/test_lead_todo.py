# -*- coding: utf-8 -*-
"""守卫（2026-11-08）：**组长今天该管谁**（`platform/lead_todo`，催办 + 失联的合成视图）。

判据都是纯函数真结果：

  ① 失联的人在 `urgent` 里（有人在等却不动 ⇒ 先处理）；
  ② 产物待验收超阈值 2 倍 ⇒ `urgent`；没到 2 倍 ⇒ `later`；
  ③ 没回执的 ⇒ `later`（催一次即可，不算紧急）；
  ④ 每条都带**怎么做**（`how` 里有具体端点名），不是只报问题；
  ⑤ 没活可管 ⇒ 空清单 + 计数全 0（不硬凑）；
  ⑥ **不排分**：只有 `now/later` 两档，没有任何数值分数字段。
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.append(str(_ROOT))

from agent_community.platform import lead_todo as LT  # noqa: E402

NOW = 1_800_000_000.0


def _m(seq: int, slot: str) -> dict:
    return {"member_seq": seq, "slot_dir": slot, "harness": "h%d" % seq, "state": "active"}


def _t(seq: int, state: str, *, age_min: float, member: int = 1, tid: int = 1) -> dict:
    return {"member_seq": member, "state": state, "task_seq": tid,
            "task_id": "T-%03d" % tid, "updated_at": NOW - age_min * 60.0}


def test_01_silent_goes_urgent():
    out = LT.build([_m(1, "前端组/员工01")], [_t(1, "working", age_min=600)], [], "ws", now=NOW)
    assert out["counts"]["silent"] == 1
    assert any(x["kind"] == "silent" and x["priority"] == "now" for x in out["urgent"]), out


def test_02_no_review_urgent_only_when_double_threshold():
    # 阈值默认 60 分钟；120 分钟=2 倍 ⇒ urgent
    hi = LT.build([_m(1, "a")], [_t(1, "delivered", age_min=130)], [], "ws", now=NOW)
    lo = LT.build([_m(1, "a")], [_t(1, "delivered", age_min=70)], [], "ws", now=NOW)
    assert any(x["kind"] == "no_review" for x in hi["urgent"]), hi["urgent"]
    assert any(x["kind"] == "no_review" for x in lo["later"]), lo["later"]


def test_03_no_receipt_is_later():
    out = LT.build([_m(1, "a")], [_t(1, "issued", age_min=45)], [], "ws", now=NOW)
    assert out["counts"]["no_receipt"] == 1
    assert all(x["priority"] == "later" for x in out["later"] if x["kind"] == "no_receipt")
    assert not out["urgent"] or all(x["kind"] != "no_receipt" for x in out["urgent"])


def test_04_every_item_says_how():
    out = LT.build([_m(1, "a")], [_t(1, "working", age_min=600), _t(2, "delivered", age_min=200)],
                    [], "ws", now=NOW)
    assert out["items"]
    for x in out["items"]:
        assert x.get("how") and x.get("ask"), x


def test_05_nothing_to_do_is_empty():
    out = LT.build([_m(1, "a")], [], [], "ws", now=NOW)
    assert out["counts"]["total"] == 0 and out["items"] == []


def test_06_no_score_field():
    out = LT.build([_m(1, "a")], [_t(1, "working", age_min=600)], [], "ws", now=NOW)
    blob = str(out).lower()
    assert "score" not in blob and "rank" not in blob
    assert {x["priority"] for x in out["items"]} <= {"now", "later"}

def test_07_rework_goes_urgent():
    """返工待重交 = 先处理（被打回又不动，最容易烂）。"""
    out = LT.build([_m(1, "a")], [_t(1, "rework", age_min=45)], [], "ws", now=NOW)
    assert out["counts"]["awaiting_rework"] == 1, out["counts"]
    assert any(x["kind"] == "awaiting_rework" and x["priority"] == "now" for x in out["urgent"]), out
    assert any("改完再交" in str(x.get("ask")) for x in out["items"])

# ── 跨工作间汇总（`build_all`）────────────────────────────────────────
def test_08_build_all_sums_and_sorts():
    """多个工作间：总量相加；有事的排在前面（空间只给数字，不列出）。"""
    entries = [
        {"workshop_id": "ws-a", "name": "甲", "status": "active", "members": [_m(1, "a")],
         "tasks": [_t(1, "working", age_min=600)], "events": []},
        {"workshop_id": "ws-b", "name": "乙", "status": "active", "members": [_m(1, "b")],
         "tasks": [_t(1, "issued", age_min=45)], "events": []},
        {"workshop_id": "ws-c", "name": "丙", "status": "active", "members": [], "tasks": [],
         "events": []},
    ]
    out = LT.build_all(entries, now=NOW)
    assert out["totals"]["workshops"] == 3
    assert out["totals"]["workshops_with_todo"] == 2
    assert out["totals"]["empty_workshops"] == 1
    assert [r["workshop_id"] for r in out["workshops"]] == ["ws-a", "ws-b"], out["workshops"]
    assert out["totals"]["urgent"] == 1 and out["totals"]["later"] == 2  # ws-a 也有没回执
    assert out["totals"]["total"] == 3


def test_09_build_all_is_empty_when_nothing_to_do():
    out = LT.build_all([{"workshop_id": "ws-x", "name": "空", "members": [], "tasks": [],
                         "events": []}], now=NOW)
    assert out["workshops"] == [] and out["totals"]["total"] == 0
    assert out["totals"]["empty_workshops"] == 1


def test_10_build_all_has_no_scores():
    entries = [{"workshop_id": "ws-a", "name": "甲", "members": [_m(1, "a")],
                "tasks": [_t(1, "working", age_min=600)], "events": []}]
    blob = str(LT.build_all(entries, now=NOW)).lower()
    assert "score" not in blob and "rank" not in blob
