# -*- coding: utf-8 -*-
"""守卫（2026-11-08）：**组长日报**（`platform/lead_digest`）—— 把"该管谁"压成一段人话。

判据：

  ① 没有要管的事 ⇒ **不发**（`empty=True`、`text=""`，不打扰）；
  ② 有事 ⇒ 一段话里有「先处理 N / 可以等等 M」的计数，且列出具体的谁、哪条任务；
  ③ 超过 8 条**折成一句**（不刷屏），并给出被折掉的条数；
  ④ 每条摘要都带**该做什么**（来自 `lead_todo` 的 `ask`），不是只报问题；
  ⑤ 文本里**不排分**（没有 score/rank/优先级数字）。
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.append(str(_ROOT))

from agent_community.platform import lead_digest as LD  # noqa: E402
from agent_community.platform import lead_todo as LT  # noqa: E402

NOW = 1_800_000_000.0


def _m(seq: int, slot: str) -> dict:
    return {"member_seq": seq, "slot_dir": slot, "harness": "h%d" % seq, "state": "active"}


def _t(seq: int, state: str, *, age_min: float, member: int = 1, tid: int = 1) -> dict:
    return {"member_seq": member, "state": state, "task_seq": tid,
            "task_id": "T-%03d" % tid, "updated_at": NOW - age_min * 60.0}


def test_01_nothing_to_do_sends_nothing():
    todo = LT.build([_m(1, "a")], [], [], "ws", now=NOW)
    out = LD.build("ws", todo)
    assert out["empty"] is True and out["text"] == ""


def test_02_text_has_counts_and_who():
    todo = LT.build([_m(1, "前端组/员工01")], [_t(1, "working", age_min=600)], [], "ws", now=NOW)
    out = LD.build("ws", todo)
    txt = out["text"]
    assert "先处理" in txt and "前端组/员工01" in txt and "T-001" in txt, txt


def test_03_long_list_is_truncated():
    members = [_m(i, "员工%02d" % i) for i in range(1, 13)]
    tasks = [_t(1, "working", age_min=600, member=i, tid=i) for i in range(1, 13)]
    todo = LT.build(members, tasks, [], "ws", now=NOW)
    out = LD.build("ws", todo)
    assert out["shown"] == LD.MAX_LINES and out["truncated"] >= 4, out
    assert "另有" in out["text"]


def test_04_each_line_says_what_to_do():
    todo = LT.build([_m(1, "a")], [_t(1, "delivered", age_min=300)], [], "ws", now=NOW)
    out = LD.build("ws", todo)
    assert "验收" in out["text"] or "回执" in out["text"], out["text"]


def test_05_no_scores_in_text():
    todo = LT.build([_m(1, "a")], [_t(1, "working", age_min=600)], [], "ws", now=NOW)
    txt = LD.build("ws", todo)["text"].lower()
    assert "score" not in txt and "rank" not in txt
