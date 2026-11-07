# -*- coding: utf-8 -*-
"""`platform/squads`：**分队（模块化工作间）**（设计 `V138` §二 第二件，老板 2026-11-07）。

## 一句话

分队 = **工作间里的一个模块**（一组工位 + 一个方向 + 一份子目标）。

> **不是**子工作间。不要按"前端一个间、后端一个间"拆 —— 那会立刻付出四项代价
> （跨间通道本就没有 / 拍板权分裂 / 证据链跨间对齐 / 通知未读事件全复制，见 `V138` §〇）。

## 三条纪律

1. **不新建账本**：任务仍记在 `workshop_id + task_seq` 上，分队只是**分组**（`squad_id` 是标签）；
2. **验收仍逐条**：没有"整分队一键通过"这种东西（那是把责任链抹平）；
3. **拍板仍唯一**：分队**没有自己的组长**（组长是工作间级的，见 `V135` 主组长唯一）。

本模块是**纯函数**（无 IO），路由层只做编排 —— 与 `debate.py` / `inherit.py` 同一规矩。
"""
from __future__ import annotations

import re

#: 一个工作间最多几个分队（防止有人拿它当"拆间"用）
MAX_SQUADS = 12

#: 分队状态
SQUAD_STATES = ("active", "paused", "closed")

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")


def new_squad(squads: list, *, name: str, direction: str = "", goal: str = "",
             member_seqs: list | None = None, squad_id: str = "") -> tuple:
    """建一个**分队**。返回 `(新 squads 列表, 错误中文)`；**同 id 拒绝**（不静默覆盖）。"""
    squads = list(squads or [])
    nm = str(name or "").strip()
    if not nm:
        return squads, "分队要有名字（name）"
    if len(squads) >= MAX_SQUADS:
        return squads, "一个工作间最多 %d 个**分队** —— 再多就该**拆工作间**了（先看 V138 §〇 的三条件）" % MAX_SQUADS
    cid = str(squad_id or "").strip() or _auto_id(nm)
    if not _ID_RE.match(cid):
        return squads, "squad_id 只能是小写字母/数字/-/_（收到 %r）" % cid
    if any(str(c.get("squad_id")) == cid for c in squads):
        return squads, "squad_id 重复：%s（换一个，平台不静默覆盖）" % cid
    seqs = []
    for x in (member_seqs or []):
        try:
            n = int(x)
        except (TypeError, ValueError):
            return squads, "member_seqs 里有一项不是整数：%r" % (x,)
        if n and n not in seqs:
            seqs.append(n)
    squads.append({"squad_id": cid, "name": nm,
                  "direction": str(direction or "").strip(),
                  "goal": str(goal or "").strip(),
                  "member_seqs": seqs, "status": "active"})
    return squads, ""


def _auto_id(name: str) -> str:
    """从名字生成 id（中文名退化成 `squad-<序号>`，不猜拼音）。"""
    s = re.sub(r"[^a-z0-9]+", "-", str(name or "").lower()).strip("-")
    return s or ("squad-%d" % (abs(hash(name)) % 10000))


def update_squad(squads: list, squad_id: str, patch: dict) -> tuple:
    """改一个**分队**（成员/状态/目标/方向）。只认白名单字段。"""
    squads = [dict(c) for c in (squads or [])]
    hit = next((c for c in squads if str(c.get("squad_id")) == str(squad_id)), None)
    if hit is None:
        return squads, "没有这个**分队**：%s" % squad_id
    bad = sorted(k for k in (patch or {})
                 if k not in ("name", "direction", "goal", "member_seqs", "status", "squad_id"))
    if bad:
        return squads, "这些字段不能在**分队**上改：%s" % "、".join(bad)
    if "squad_id" in patch and str(patch["squad_id"]) != str(squad_id):
        return squads, "squad_id 不能改（要换身份请新建一个**分队**）"
    if "status" in patch and str(patch["status"]) not in SQUAD_STATES:
        return squads, "status 只能是 %s" % " / ".join(SQUAD_STATES)
    if "member_seqs" in patch:
        seqs = []
        for x in (patch.get("member_seqs") or []):
            try:
                n = int(x)
            except (TypeError, ValueError):
                return squads, "member_seqs 里有一项不是整数：%r" % (x,)
            if n and n not in seqs:
                seqs.append(n)
        hit["member_seqs"] = seqs
    for k in ("name", "direction", "goal", "status"):
        if k in patch:
            hit[k] = str(patch[k] or "").strip()
    return squads, ""


def find(squads: list, squad_id: str) -> dict:
    return next((dict(c) for c in (squads or []) if str(c.get("squad_id")) == str(squad_id)), {})


def squad_of(squads: list, member_seq: int) -> str:
    """这个工位属于哪个**分队**（空＝直属组长）。**一个工位只属于一个 Cell**。"""
    for c in (squads or []):
        if int(member_seq or 0) in [int(x) for x in (c.get("member_seqs") or [])]:
            return str(c.get("squad_id") or "")
    return ""


def squad_members_of(squads: list, squad_id: str) -> list:
    return [int(x) for x in ((find(squads, squad_id) or {}).get("member_seqs") or [])]


def view(squads: list, members: list, tasks: list) -> dict:
    """**三级视图**：工作间 → Cell → 工位（`V138` §二 第三件）。

    `tasks` 传任务账本里的条目（有 `member_seq` / `state`）；只为**计数**，不改账本。
    """
    by_seq = {int((m or {}).get("member_seq") or 0): (m or {}) for m in (members or [])}
    out_squads = []
    assigned = set()
    for c in (squads or []):
        seqs = [int(x) for x in (c.get("member_seqs") or [])]
        assigned.update(seqs)
        rows = []
        for s in seqs:
            m = by_seq.get(s) or {}
            ts = [t for t in (tasks or []) if int((t or {}).get("member_seq") or 0) == s]
            rows.append({"member_seq": s, "slot_dir": m.get("slot_dir") or "",
                         "harness_id": m.get("harness_id") or "",
                         "state": m.get("state") or "",
                         "tasks": len(ts),
                         "open_tasks": len([t for t in ts
                                            if str(t.get("state")) not in ("accepted", "closed",
                                                                          "refuted", "delivered")])})
        out_squads.append({"squad_id": c.get("squad_id"), "name": c.get("name"),
                          "direction": c.get("direction"), "goal": c.get("goal"),
                          "status": c.get("status") or "active",
                          "member_count": len(rows), "members": rows,
                          "tasks": sum(r["tasks"] for r in rows),
                          "open_tasks": sum(r["open_tasks"] for r in rows)})
    loose = [{"member_seq": int((m or {}).get("member_seq") or 0),
              "slot_dir": (m or {}).get("slot_dir") or ""}
             for m in (members or [])
             if int((m or {}).get("member_seq") or 0) not in assigned
             and str((m or {}).get("slot_dir") or "").strip().strip("/") != "_lead"]
    # `_lead` 是**工作间级**的拍板人，不属于任何**分队**，也不算"没分组"（不然每个视图都在提醒你给它分组）
    lead = next(({"member_seq": int((m or {}).get("member_seq") or 0),
                  "slot_dir": str((m or {}).get("slot_dir") or "")}
                 for m in (members or [])
                 if str((m or {}).get("slot_dir") or "").strip().strip("/") == "_lead"), {})
    return {"ok": True, "levels": ["workshop", "squad", "slot"],
            "workshop": {"cell_count": len(out_squads), "slot_count": len(by_seq or {}),
                         "lead": lead, "loose_slots": loose},
            "squads": out_squads,
            "note": ("三级：工作间 → **分队** → 成员（老板 2026-11-07 定词：叫**分队**、不叫分组）。"
                     "分队只是**编队**：账本仍按 `workshop_id+task_seq`，验收仍逐条，"
                     "**没有整分队一键通过**；**组长不进分队**（他负责拍板与验收）。")}


#: 拆分建议的时间线差异阈值（天）：两个 Cell 的工期差超过它，才谈得上"独立时间线"
SPLIT_DAY_GAP = 14


def split_advice(squads: list, members: list, tasks: list) -> dict:
    """**该不该拆成两个工作间**（`V138` §〇 的三条件）—— 只给事实与理由，**不推荐**。

    | 条件 | 怎么判（都可算） |
    |---|---|
    | 独立交付 | **分队**之间**没有跨 Cell 依赖**（任务 `deps` 指向别 Cell 的任务 ⇒ 不独立） |
    | 独立时间线 | 各 **分队**的工期跨度差异 > `SPLIT_DAY_GAP` 天 |
    | 独立责任 | 验收人**不止一个**（账本里出现 ≥2 个不同 `reviewed_by`，且都不是 `user`） |

    三条件**同时成立**才 `consider_split`；一条都不成立 ⇒ `keep`（现状就是"该放一个间里"）。
    """
    squads = list(squads or [])
    tasks = list(tasks or [])
    seq_cell = {}
    for c in squads:
        for s in (c.get("member_seqs") or []):
            seq_cell[int(s)] = str(c.get("squad_id") or "")
    task_cell = {str(t.get("task_id") or t.get("task_seq")): seq_cell.get(int(t.get("member_seq") or 0), "")
                 for t in tasks}

    # ① 独立交付：有跨 Cell 依赖 ⇒ 不独立
    cross = []
    for t in tasks:
        mine = task_cell.get(str(t.get("task_id") or t.get("task_seq")), "")
        for d in (t.get("deps") or []):
            other = task_cell.get(str(d), "")
            if mine and other and other != mine:
                cross.append({"task": t.get("task_id") or t.get("task_seq"),
                              "dep": d, "from_cell": mine, "to_cell": other})
    independent_delivery = (not cross) and len(squads) >= 2

    # ② 独立时间线：每 **分队**的工期跨度（用账本里的 created_at / closed_at，取不到就 0）
    def _span(cid: str) -> float:
        ts = [t for t in tasks if seq_cell.get(int(t.get("member_seq") or 0)) == cid]
        vals = []
        for t in ts:
            for k in ("created_at", "issued_at", "closed_at", "updated_at"):
                v = t.get(k)
                if isinstance(v, (int, float)) and v >= 0:
                    vals.append(float(v))
        return (max(vals) - min(vals)) if len(vals) >= 2 else 0.0

    spans = {str(c.get("squad_id")): _span(str(c.get("squad_id"))) for c in squads}
    gap_days = (max(spans.values()) - min(spans.values())) / 86400.0 if spans else 0.0
    independent_timeline = gap_days > SPLIT_DAY_GAP

    # ③ 独立责任：验收人不止一个
    reviewers = {str(t.get("reviewed_by") or "") for t in tasks
                 if str(t.get("reviewed_by") or "") not in ("", "user")}
    independent_owner = len(reviewers) >= 2

    conds = {"independent_delivery": independent_delivery,
             "independent_timeline": independent_timeline,
             "independent_owner": independent_owner}
    n = sum(1 for v in conds.values() if v)
    advice = "consider_split" if n == 3 else "keep"
    reasons = []
    if cross:
        reasons.append("**有跨分队依赖**：%d 条（例：%s 依赖 %s）⇒ 拆开会立刻产生跨间沟通"
                       % (len(cross), cross[0]["task"], cross[0]["dep"]))
    if not independent_timeline:
        reasons.append("工期差异只有 %.1f 天（阈值 %d 天）⇒ 一条时间线就够" % (gap_days, SPLIT_DAY_GAP))
    if not independent_owner:
        reasons.append("验收人只有一个（%s）⇒ 拍板权拆开后会分裂"
                       % ("、".join(sorted(reviewers)) or "还没出现第二个"))
    if advice == "keep":
        reasons.append("三条件要**同时**成立才谈得上拆；现在成立 %d 条 ⇒ **保持一个工作间，用 Cell 分组**" % n)
    return {"ok": True, "advice": advice, "conditions": conds, "satisfied": n,
            "cross_cell_deps": cross[:20],
            "span_days": {k: round(v / 86400.0, 1) for k, v in spans.items()},
            "gap_days": round(gap_days, 1), "reviewers": sorted(reviewers),
            "reasons": reasons,
            "note": ("平台**只给事实与理由，不替你决定**；真要拆，见 `V138` §〇 的三项代价"
                     "（跨间通道本就没有 / 拍板权分裂 / 证据链要跨间对齐）")}
