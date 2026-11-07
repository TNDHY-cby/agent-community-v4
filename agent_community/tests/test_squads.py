# -*- coding: utf-8 -*-
"""守卫（`V138` §二 第二件）：**小队（模块化工作间）** —— 一间多 Cell，不拆间。

判据都是纯函数真结果：

  ① 建 Cell：`squads[]` 有它，成员 `member_seq` 对上；
  ② 同 id 拒绝（**不静默覆盖**）；
  ③ 超过上限 ⇒ 明说"该拆工作间了"（不是继续加）；
  ④ 一个工位只属于一个 Cell（`squad_of` 返回唯一）；
  ⑤ 三级视图：工作间 → Cell → 工位（含每工位任务计数），未分组工位进 `loose_slots`；
  ⑥ Cell **没有自己的组长**（数据结构里没有 lead 字段）；
  ⑦ 空 Cell 派活要拦住（`CELL_EMPTY`，在端点层判，这里验 `squad_members_of` 为空）。
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.append(str(_ROOT))

from agent_community.platform import squads as C  # noqa: E402


def test_01_create_and_members():
    cs, err = C.new_squad([], name="前端", direction="前端", goal="做页面",
                         member_seqs=[1, 2])
    assert err == "" and len(cs) == 1, err
    assert cs[0]["squad_id"] == "frontend" or cs[0]["squad_id"].startswith("cell-") or cs[0]["squad_id"]
    assert cs[0]["member_seqs"] == [1, 2]
    assert cs[0]["status"] == "active"


def test_02_duplicate_id_is_rejected():
    cs, _ = C.new_squad([], name="前端", squad_id="fe", member_seqs=[1])
    cs2, err = C.new_squad(cs, name="前端二", squad_id="fe", member_seqs=[2])
    assert "重复" in err and len(cs2) == 1
    assert cs2[0]["member_seqs"] == [1], "不许静默覆盖已有 Cell"


def test_03_over_limit_says_split_workshop():
    cs: list = []
    for i in range(C.MAX_SQUADS):
        cs, err = C.new_squad(cs, name="模块%d" % i, squad_id="m%d" % i)
        assert err == "", err
    cs2, err2 = C.new_squad(cs, name="再加一个", squad_id="extra")
    assert "拆工作间" in err2 and len(cs2) == C.MAX_SQUADS


def test_04_one_slot_belongs_to_one_cell():
    cs, _ = C.new_squad([], name="前端", squad_id="fe", member_seqs=[1, 2])
    cs, _ = C.new_squad(cs, name="后端", squad_id="be", member_seqs=[3])
    assert C.squad_of(cs, 1) == "fe"
    assert C.squad_of(cs, 3) == "be"
    assert C.squad_of(cs, 9) == ""


def test_05_view_is_three_levels_with_counts():
    cs, _ = C.new_squad([], name="前端", squad_id="fe", member_seqs=[1, 2])
    members = [{"member_seq": 1, "slot_dir": "前端组/员工01"},
               {"member_seq": 2, "slot_dir": "前端组/员工02"},
               {"member_seq": 3, "slot_dir": "后端组/员工01"}]
    tasks = [{"member_seq": 1, "task_seq": 1, "state": "issued"},
             {"member_seq": 2, "task_seq": 2, "state": "accepted"},
             {"member_seq": 3, "task_seq": 3, "state": "issued"}]
    v = C.view(cs, members, tasks)
    assert v["levels"] == ["workshop", "squad", "slot"]
    fe = v["squads"][0]
    assert fe["squad_id"] == "fe" and fe["member_count"] == 2
    assert fe["tasks"] == 2 and fe["open_tasks"] == 1
    assert [x["member_seq"] for x in v["workshop"]["loose_slots"]] == [3], "没进 Cell 的工位要列出来"


def test_06_cell_has_no_lead_field():
    cs, _ = C.new_squad([], name="前端", squad_id="fe")
    assert not any(k in cs[0] for k in ("lead", "lead_harness_id", "reviewer")), cs[0]
    assert "组长不进分队" in C.view(cs, [], [])["note"]


def test_07_empty_cell_has_no_members():
    cs, _ = C.new_squad([], name="空模块", squad_id="empty")
    assert C.squad_members_of(cs, "empty") == []


def test_08_update_members_and_status():
    cs, _ = C.new_squad([], name="前端", squad_id="fe", member_seqs=[1])
    cs2, err = C.update_squad(cs, "fe", {"member_seqs": [2, 3], "status": "paused"})
    assert err == "" and C.squad_members_of(cs2, "fe") == [2, 3] and cs2[0]["status"] == "paused"
    _cs3, err2 = C.update_squad(cs2, "fe", {"squad_id": "x"})
    assert "不能改" in err2
    _cs4, err3 = C.update_squad(cs2, "nope", {"status": "paused"})
    assert "没有这个" in err3 and "分队" in err3


# ── 拆分建议（V138 §〇 三条件）──────────────────────────────────────
def test_09_split_advice_keep_when_deps_cross_squads():
    cs, _ = C.new_squad([], name="前端", squad_id="fe", member_seqs=[1])
    cs, _ = C.new_squad(cs, name="后端", squad_id="be", member_seqs=[2])
    tasks = [{"task_id": "T-001", "member_seq": 1, "deps": ["T-002"]},
             {"task_id": "T-002", "member_seq": 2, "deps": []}]
    out = C.split_advice(cs, [], tasks)
    assert out["advice"] == "keep"
    assert out["cross_cell_deps"], "跨 Cell 依赖必须被点出来"


def test_10_split_advice_keep_when_same_owner_and_timeline():
    cs, _ = C.new_squad([], name="前端", squad_id="fe", member_seqs=[1])
    cs, _ = C.new_squad(cs, name="后端", squad_id="be", member_seqs=[2])
    tasks = [{"task_id": "T-001", "member_seq": 1, "reviewed_by": "_lead"},
             {"task_id": "T-002", "member_seq": 2, "reviewed_by": "_lead"}]
    out = C.split_advice(cs, [], tasks)
    assert out["advice"] == "keep" and out["satisfied"] < 3
    assert any("拍板权" in r for r in out["reasons"])


def test_11_split_advice_consider_when_three_conditions_hold():
    cs, _ = C.new_squad([], name="前端", squad_id="fe", member_seqs=[1])
    cs, _ = C.new_squad(cs, name="后端", squad_id="be", member_seqs=[2])
    day = 86400
    tasks = [
        {"task_id": "T-001", "member_seq": 1, "reviewed_by": "_lead",
         "created_at": 0, "closed_at": 2 * day},
        {"task_id": "T-002", "member_seq": 2, "reviewed_by": "审忆员甲",
         "created_at": 0, "closed_at": 30 * day},
    ]
    out = C.split_advice(cs, [], tasks)
    assert out["advice"] == "consider_split", out
    assert all(out["conditions"].values()), out["conditions"]
