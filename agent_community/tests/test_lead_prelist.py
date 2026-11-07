# -*- coding: utf-8 -*-
"""守卫（`V137`，老板 2026-11-07）：**组长预列表** —— 按方向给「单 / 团」两套配置。

判据都是纯函数真结果（不查源码文本）：

  ① 有方向的候选进对应方向组；
  ② 没报方向的进 `no_direction`，且**不进任何方向组**（平台不替它编）；
  ③ 方向里 solo 可行 ⇒ `solo.feasible=true` 且 lead 对；
  ④ 方向里 solo 不行 ⇒ 如实 `feasible=false` + `reason`，且 `team` 给配置；
  ⑤ 方向里补不齐 ⇒ `blocked.missing` + `next`；
  ⑥ `by_mode` 三组键齐（可为空数组）；
  ⑦ `?task=` 命中方向（包含匹配）；
  ⑧ `?task=` 未命中 ⇒ 说清 + 列出实际方向（**不退化**）；
  ⑨ 助手跨方向时标 `from_direction`；
  ⑩ 与算法同源（同一方向候选集下的结论与 `lead_algorithm.plan` 一致）；
  ⑪ 稳定可复现；
  ⑫ 空候选 ⇒ 空结构 + 说明（不崩、不编）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.append(str(_ROOT))

from agent_community.platform import lead_algorithm as alg  # noqa: E402
from agent_community.platform import lead_prelist as pre  # noqa: E402


def _e(hid: str, dirs: list, *, fit="high", pc=2, read=True, write=True,
       dispatch=True, recv="yes", wake=True, elig=True) -> dict:
    caps = {"lead_eligible": elig,
            "protocol_fit": {"level": fit, "reasons": [] if fit == "high" else ["缺读文件"]},
            "protocol_count": pc, "can_read_files": read, "can_write_files": write,
            "can_dispatch": dispatch, "can_receive_result": recv,
            "can_wake_automatically": wake}
    return {"harness_id": hid, "harness_name": hid, "directions": dirs, "capabilities": caps}


def test_01_direction_group_contains_its_candidates():
    out = pre.build_prelist([_e("示例-前端全能", ["前端"]), _e("示例-后端全能", ["后端"])])
    dirs = {g["direction"]: g for g in out["directions"]}
    assert set(dirs) == {"前端", "后端"}
    assert [c["harness_id"] for c in dirs["前端"]["candidates"]] == ["示例-前端全能"]


def test_02_no_direction_is_not_guessed():
    out = pre.build_prelist([_e("示例-没报方向", []), _e("示例-前端", ["前端"])])
    assert [x["harness_id"] for x in out["no_direction"]] == ["示例-没报方向"]
    for g in out["directions"]:
        assert "示例-没报方向" not in [c["harness_id"] for c in g["candidates"]]
    assert "不替它编" in out["no_direction_note"]


def test_03_solo_feasible_for_full_capability():
    out = pre.build_prelist([_e("示例-全能", ["前端"])])
    g = out["directions"][0]
    assert g["solo"]["feasible"] is True and g["solo"]["lead"]["harness_id"] == "示例-全能"


def test_04_solo_infeasible_falls_back_to_team():
    out = pre.build_prelist([
        _e("示例-少协议", ["前端"], fit="medium", read=False),
        _e("示例-能读", ["后端"], fit="low", read=True, write=False, dispatch=False,
           recv="no", wake=False, elig=False),
    ])
    g = next(x for x in out["directions"] if x["direction"] == "前端")
    assert g["solo"]["feasible"] is False and g["solo"]["reason"]
    assert g["team"]["feasible"] is True, g["team"]


def test_05_blocked_says_what_is_missing():
    out = pre.build_prelist([_e("示例-只写", ["前端"], fit="medium", read=False)])
    g = out["directions"][0]
    assert g["blocked"]["missing"] and g["blocked"]["next"]


def test_06_by_mode_has_three_keys():
    out = pre.build_prelist([_e("示例-全能", ["前端"])])
    assert set(out["by_mode"]) == {"solo", "team", "blocked"}
    assert out["by_mode"]["solo"] and out["by_mode"]["solo"][0]["direction"] == "前端"


def test_07_task_keyword_matches():
    out = pre.build_prelist([_e("示例-前端", ["前端"])], task="前端页面开发")
    assert out["matched_directions"] == ["前端"]


def test_08_task_keyword_miss_is_honest():
    out = pre.build_prelist([_e("示例-前端", ["前端"])], task="运维")
    assert out["matched_directions"] == []
    assert "没匹配到" in out["match_note"] and "前端" in out["match_note"]


def test_09_helper_marks_from_direction():
    out = pre.build_prelist([
        _e("示例-少协议", ["前端"], fit="medium", read=False),
        _e("示例-能读", ["后端"], fit="low", read=True, write=False, dispatch=False,
           recv="no", wake=False, elig=False),
    ])
    g = next(x for x in out["directions"] if x["direction"] == "前端")
    hs = g["team"]["helpers"]
    assert hs and all(h.get("from_direction") for h in hs), hs


def test_10_same_source_as_algorithm():
    entries = [_e("示例-少协议", ["前端"], fit="medium", read=False),
               _e("示例-能读", ["前端"], fit="low", read=True, write=False,
                  dispatch=False, recv="no", wake=False, elig=False)]
    out = pre.build_prelist(entries)
    g = out["directions"][0]
    direct = alg.plan([{"harness_id": e["harness_id"], "harness_name": e["harness_name"],
                        "capabilities": e["capabilities"]} for e in entries])
    assert g["team"]["lead"]["harness_id"] == direct["lead"]["harness_id"]
    assert [h["harness_id"] for h in g["team"]["helpers"]] == \
        [h["harness_id"] for h in direct["helpers"]]


def test_11_stable():
    pool = [_e("示例-B", ["前端"]), _e("示例-A", ["前端"])]
    a = pre.build_prelist(pool)
    b = pre.build_prelist(pool)
    assert json.dumps(a, ensure_ascii=False, sort_keys=True) == \
        json.dumps(b, ensure_ascii=False, sort_keys=True)


def test_12_empty_pool_is_explained():
    out = pre.build_prelist([])
    assert out["ok"] is True and out["directions"] == [] and out["no_direction"] == []
    assert out["by_mode"] == {"solo": [], "team": [], "blocked": []}
    assert out["sort_note"], "空池也要给口径说明"


# ── ★V137 优化：一行结论 / 梯队 / 任务卡 / 引导 ──────────────────────
def test_13_headline_is_one_human_sentence():
    out = pre.build_prelist([
        _e("示例-前端全能", ["前端"]),
        _e("示例-后端少协议", ["后端"], fit="medium", read=False),
        _e("示例-能读", ["测试"], fit="low", read=True, write=False, dispatch=False,
           recv="no", wake=False, elig=False),
        _e("示例-只写", ["运维"], fit="medium", read=False),
    ])
    hs = {g["direction"]: g["headline"] for g in out["directions"]}
    assert "一个人就够" in hs["前端"] and "前端" in hs["前端"]
    assert "得搭团" in hs["后端"] and "补" in hs["后端"]
    assert "没人能接" in hs["运维"] or "没人能接" in hs["测试"], hs


def test_14_alternatives_lists_other_solo_candidates():
    out = pre.build_prelist([_e("示例-A", ["前端"], pc=1), _e("示例-B", ["前端"], pc=2)])
    g = out["directions"][0]
    assert g["solo"]["lead"]["harness_id"] == "示例-B", "协议多的当 lead"
    assert [x["harness_id"] for x in g["solo"]["alternatives"]] == ["示例-A"]


def test_15_task_card_hit_and_miss():
    hit = pre.build_prelist([_e("示例-前端", ["前端"])], task="前端页面开发")
    assert hit["task_card"]["status"] == "hit"
    assert hit["task_card"]["matched_directions"] == ["前端"]
    miss = pre.build_prelist([_e("示例-前端", ["前端"])], task="运维")
    assert miss["task_card"]["status"] == "miss"
    assert miss["task_card"]["available_directions"] == ["前端"]
    assert "换个说法" in miss["task_card"]["next"]


def test_16_guidance_has_three_situations():
    out = pre.build_prelist([_e("示例-前端", ["前端"])])
    g = out["guidance"]
    assert set(("if_empty", "if_all_blocked", "if_partial")) <= set(g)
    assert all(str(v).strip() for v in g.values())
