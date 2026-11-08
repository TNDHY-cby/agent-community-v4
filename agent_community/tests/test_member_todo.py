# -*- coding: utf-8 -*-
"""守卫（2026-11-08）：**成员自己该干什么**（`platform/member_todo`）。

判据：

  ① `issued/working` ⇒ `kind=do`，动作写"交回执"并给端点；
  ② `rework` ⇒ `kind=do`，动作写"改完再交"，且**优先于**没交的活（`next` 先挑它）；
  ③ `delivered` ⇒ `kind=wait`（别重复交）；`accepted` ⇒ `done`；`rejected` ⇒ `stop`；
  ④ 只算**我自己**的任务（别人的不算）；
  ⑤ 给我自己的通知才会出现（别人的不算）；
  ⑥ `next` 只挑一件、没有可做的就是 `None`；视图里**不排分**；
  ⑦ 不认识的状态**如实说不认识**（不假装有动作）。
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.append(str(_ROOT))

from agent_community.platform import member_todo as MT  # noqa: E402

ME = {"member_seq": 1, "slot_dir": "前端组/员工01"}


def _t(seq: int, state: str, member: int = 1, **kw) -> dict:
    d = {"task_seq": seq, "state": state, "member_seq": member, "task_id": "T-%03d" % seq}
    d.update(kw)
    return d


def test_01_issued_says_do_receipt():
    out = MT.build(ME, [_t(1, "issued")], [])
    row = out["tasks"][0]
    assert row["kind"] == "do" and "回执" in row["what"] and "task-result" in row["how"], row


def test_02_rework_is_do_and_picked_first():
    out = MT.build(ME, [_t(1, "issued"), _t(2, "rework")], [])
    assert out["next"]["task_id"] == "T-002" and "改完再交" in out["next"]["what"], out["next"]


def test_03_other_states_map_right():
    for st, kind in (("delivered", "wait"), ("accepted", "done"), ("rejected", "stop")):
        out = MT.build(ME, [_t(1, st)], [])
        assert out["tasks"][0]["kind"] == kind, (st, out["tasks"][0])


def test_04_only_my_tasks():
    out = MT.build(ME, [_t(1, "issued", member=1), _t(2, "issued", member=9)], [])
    assert [r["task_id"] for r in out["tasks"]] == ["T-001"], out["tasks"]


def test_05_only_my_notices():
    ns = [{"target": "member:前端组/员工01", "detail": "给我的"},
          {"target": "member:后端组/员工01", "detail": "给别人的"}]
    out = MT.build(ME, [], ns)
    assert len(out["notices"]) == 1 and out["notices"][0]["detail"] == "给我的", out["notices"]


def test_06_next_is_none_when_nothing_to_do():
    out = MT.build(ME, [_t(1, "delivered"), _t(2, "accepted")], [])
    assert out["next"] is None and out["counts"]["todo"] == 0, out
    assert "score" not in str(out).lower() and "rank" not in str(out).lower()


def test_07_unknown_state_is_honest():
    out = MT.build(ME, [_t(1, "something_weird")], [])
    row = out["tasks"][0]
    assert row["kind"] == "unknown" and "不认识" in row["what"], row
