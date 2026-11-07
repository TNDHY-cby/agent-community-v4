# -*- coding: utf-8 -*-
"""守卫（`V136`，老板 2026-11-07）：**组长算法两条路** —— 全能单组长 / 少协议组长 + 助手互补。

判据都是纯函数真结果（不查源码文本）：

  ① 有 `high` ⇒ `solo`（不要助手，四件事全覆盖）；
  ② 多个 `high` ⇒ 选协议数最多的，其余列进 `others_solo`；
  ③ 无 `high` 但能补 ⇒ `team`，每个助手带 `adds`（补了哪一项）；
  ④ 读不了文件的主组长 ⇒ 助手必须能读（`read_artifacts` 只认"能读"，不认"能写"）；
  ⑤ 补不齐 ⇒ `blocked` + `next` 三条路，且**不给假配置**；
  ⑥ `needs` 可缩小（只要派活）；
  ⑦ 稳定可复现（两次结果逐字相同）；
  ⑧ 空候选 ⇒ `blocked` + 先去注册。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.append(str(_ROOT))

from agent_community.platform import lead_algorithm as alg  # noqa: E402


def _c(hid: str, *, fit="high", pc=1, read=True, write=True, dispatch=True,
       recv="yes", wake=True, elig=True, reasons=None) -> dict:
    caps = {"lead_eligible": elig,
            "protocol_fit": {"level": fit, "reasons": list(reasons or [])},
            "protocol_count": pc, "can_read_files": read, "can_write_files": write,
            "can_dispatch": dispatch, "can_receive_result": recv,
            "can_wake_automatically": wake}
    return {"harness_id": hid, "harness_name": hid, "capabilities": caps}


def test_01_high_goes_solo_without_helpers():
    p = alg.plan([_c("示例-A2A全能", pc=3)])
    assert p["mode"] == "solo" and p["helpers"] == [], p
    assert all(v.get("covered_by") for v in p["coverage"].values()), p["coverage"]


def test_02_multiple_high_picks_most_protocols():
    p = alg.plan([_c("示例-单协议", pc=1), _c("示例-多协议", pc=4)])
    assert p["mode"] == "solo"
    assert p["lead"]["harness_id"] == "示例-多协议", p["lead"]
    assert [x["harness_id"] for x in p["others_solo"]] == ["示例-单协议"]


def test_03_team_fills_gap_and_says_what_it_adds():
    # medium 的定义就是"读不了文件"（fit 与能力必须一致；不一致的输入不在算法保证范围内）
    lead = _c("示例-桥组长", fit="medium", pc=1, read=False)
    reader = _c("示例-能读", fit="low", pc=1, read=True, write=False,
                dispatch=False, recv="no", wake=False, elig=False)
    p = alg.plan([lead, reader])
    assert p["mode"] == "team", p
    assert p["lead"]["harness_id"] == "示例-桥组长"
    assert any(h["harness_id"] == "示例-能读" for h in p["helpers"]), p["helpers"]
    assert all(h.get("adds") for h in p["helpers"])


def test_04_read_is_not_covered_by_writing():
    """能写 ≠ 能看：主组长读不了 ⇒ 必须配一个能读的助手（首版把'能写'当'能读'，实测抓到）。"""
    lead = _c("示例-只写", fit="medium", read=False, write=True)
    p = alg.plan([lead])
    assert p["mode"] == "blocked", p
    assert "read_artifacts" in (p["blocked"]["missing"] or []), p["blocked"]


def test_05_blocked_gives_three_ways_forward():
    p = alg.plan([_c("示例-只写", fit="medium", read=False, write=True)])
    assert p["mode"] == "blocked"
    assert p["blocked"]["next"] and ("①" in p["blocked"]["next"])
    assert not p.get("lead") and not p.get("helpers"), "不许给假配置"


def test_06_needs_can_shrink():
    lead = _c("示例-只写", fit="medium", read=False, write=True)
    p = alg.plan([lead], needs=["dispatch"])
    assert p["mode"] in ("solo", "team"), p
    assert p["needs"] == ["dispatch"]


def test_07_stable_and_repeatable():
    pool = [_c("示例-B", pc=2), _c("示例-A", pc=2)]
    a = alg.plan(pool)
    b = alg.plan(pool)
    assert json.dumps(a, ensure_ascii=False, sort_keys=True) == \
        json.dumps(b, ensure_ascii=False, sort_keys=True)
    # 同分按 harness_id 字典序（可复现，不掺偏好）
    assert a["lead"]["harness_id"] == "示例-A", a["lead"]


def test_08_empty_pool_is_blocked_with_next():
    p = alg.plan([])
    assert p["mode"] == "blocked" and p["blocked"]["next"]
    assert p["why"], "空池也要说一句为什么"
