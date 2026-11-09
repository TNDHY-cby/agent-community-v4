# -*- coding: utf-8 -*-
"""`platform/org_chart.py`：**组织关系指针化**（借鉴竞品 A1）—— 把"谁向谁汇报"从命名约定变成数据。

设计稿：`design-docs/V139_组织关系指针化设计.md`（老板 2026-11-09「1可以」）。

**说清边界（不许夸大）**：我方现在只有**两层**（组长 + 成员），分队是**分组不是层级**
（`squads.py` 里没有任何队长概念，实测确认）。所以 `reports_to` 今天恒为 `_lead`。
它的价值是①把"`_lead` 这个字面量约定"变成**显式数据**（前端/agent 不用再比字符串）；
②为将来"分队长"那一层**留位**（那时只需让 `reports_to` 能取分队长的槽位，结构不改）。

**不做**：不发明分队长角色、不排分、不改派活闸门/lead-todo/presence 的任何判断。
"""
from __future__ import annotations

import os

LEAD_SLOT = "_lead"


def enabled() -> bool:
    """`AC_ORG_CHART=0` 关掉（回滚开关，默认开）。**唯一开关点**。"""
    return str(os.environ.get("AC_ORG_CHART") or "1").strip() not in ("0", "false", "False", "off")


def build(members, squads=None, *, workshop_id: str = "") -> dict:
    """由名单 + 分队算出组织关系（只读，纯函数）。

    `members`：`[{slot_dir, member_seq, tier, state, ...}]`（`v5_roster.roster()['members']`）
    `squads`：`[{squad_id, name, members:[{member_seq, slot_dir}]}]`（可空）
    """
    from . import squads as _sq

    squads = squads or []
    # 分队记录里成员可能是 `members: [{member_seq, slot_dir}]`，也可能只存了 `member_seqs: [1,2]`
    # —— 两种都要认（`squads.py` 建的时候收 `member_seqs`，存的形态按版本不同）。
    by_seq = {}
    for sq in squads:
        raw = list((sq or {}).get("members") or []) or list((sq or {}).get("member_seqs") or [])
        for m in raw:
            try:
                seq = int((m or {}).get("member_seq") if isinstance(m, dict) else m)
            except (TypeError, ValueError):
                continue
            if seq:
                by_seq[seq] = sq

    nodes, edges, unassigned = [], [], []
    has_lead = any(str((m or {}).get("slot_dir") or "") == LEAD_SLOT for m in (members or []))
    if has_lead:
        nodes.append({"slot_dir": LEAD_SLOT, "member_seq": 0, "kind": "lead", "level": 0,
                      "reports_to": None, "squad_id": None, "squad_name": None})
    for m in (members or []):
        slot = str((m or {}).get("slot_dir") or "")
        if not slot or slot == LEAD_SLOT:
            continue
        seq = int((m or {}).get("member_seq") or 0)
        sq = by_seq.get(seq)
        node = {"slot_dir": slot, "member_seq": seq, "kind": "member", "level": 1,
                "reports_to": LEAD_SLOT if has_lead else None,
                "squad_id": (sq or {}).get("squad_id"), "squad_name": (sq or {}).get("name"),
                "tier": str((m or {}).get("tier") or ""),
                "state": str((m or {}).get("state") or "")}
        nodes.append(node)
        if node["reports_to"]:
            edges.append({"from": slot, "to": node["reports_to"], "kind": "reports_to"})
        if sq is None:
            unassigned.append({"slot_dir": slot, "member_seq": seq})
    return {
        "ok": True, "enabled": True, "workshop_id": workshop_id,
        "nodes": nodes, "edges": edges, "unassigned": unassigned,
        "counts": {"members": len([n for n in nodes if n["kind"] == "member"]),
                   "lead": 1 if has_lead else 0,
                   "squads": len(squads),
                   "unassigned": len(unassigned),
                   "edges": len(edges)},
        "note": ("`reports_to` 是**汇报对象**（槽位名），`squad_id` 只是**分组**，不是上级。"
                 "现在只有两层：组长（level 0）+ 成员（level 1）。"
                 "没有组长工位时成员的 reports_to 是 null（平台如实给 null，不编一个上级）。"),
        "no_ranking": "这里没有分数/排名：只描述谁向谁汇报，不评价谁更该当组长。",
    }


def annotate_roster(roster_out: dict, squads=None, *, workshop_id: str = "") -> dict:
    """给 `GET …/roster` 的每个成员补 `reports_to` / `level`（**只加字段，不改既有字段**）。"""
    if not enabled() or not isinstance(roster_out, dict):
        return roster_out
    chart = build(roster_out.get("members") or [], squads, workshop_id=workshop_id)
    rel = {n["slot_dir"]: n for n in chart["nodes"]}
    for m in (roster_out.get("members") or []):
        n = rel.get(str((m or {}).get("slot_dir") or ""))
        if n:
            m["reports_to"] = n["reports_to"]
            m["level"] = n["level"]
            m["squad_id"] = n.get("squad_id")
    return roster_out
