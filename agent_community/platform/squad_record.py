# -*- coding: utf-8 -*-
"""`platform/squad_record`：**分队履历**与**分队模板化**（`V140` §2.1 / §2.2，队列 47-1 / 47-2）。

## 口径纪律（三条，都来自设计稿，不是我自己想的）

1. **只算终态**（`accepted` / `rejected`）：进行中的不进分母 —— 否则**越忙的队通过率越难看**；
2. **不给分数、不排名**：`accept_rate` 是**比例事实**（12 条里 9 条通过），不是评分；
3. **样本不足要说**：`total < 3` 附 `insufficient: true` + 一句"样本太少，别据此判断"。

## clone 的克制（`V140` §2.2）

| 复制 | **不复制** |
|---|---|
| `direction` / `goal` / 成员构成 | **任务历史**（新队从零记履历） |
| `copy_experience=true` 时：**经验索引指针**（memories 的路径） | **经验正文**（避免把上一件事的来龙去脉灌进新任务） |

老板要的"清上下文影响"就是这一点：新队拿到的是"我们以前踩过这些坑（路径在这）"，
而不是"上一个任务的全文"。要看自己去读（按索引按需读）。
"""
from __future__ import annotations

import os

OPEN_STATES = ("issued", "working", "rework", "delivered")
TERMINAL_STATES = ("accepted", "rejected")


def history_enabled() -> bool:
    """`AC_SQUAD_HISTORY=0` 关履历（回滚开关，默认开）。"""
    return str(os.environ.get("AC_SQUAD_HISTORY") or "1").strip() not in ("0", "false", "False", "off")


def template_enabled() -> bool:
    """`AC_SQUAD_TEMPLATE=0` 关模板化（回滚开关，默认开）。"""
    return str(os.environ.get("AC_SQUAD_TEMPLATE") or "1").strip() not in ("0", "false", "False", "off")


def _rework_times(rec: dict) -> int:
    """这条任务历史上被返工过几次（数 log 里 state == rework 的次数）。"""
    n = 0
    for it in (rec.get("log") or []):
        if str((it or {}).get("state") or "") == "rework":
            n += 1
    return n


def build(squad: dict, members: list, tasks: list, *, workshop_id: str = "") -> dict:
    """算一个分队的**履历**（只读，纯函数）。

    `squad`：`{squad_id, name, direction, goal, member_seqs:[...], status}`
    `members`：名单里的成员（用来把 member_seq 翻成 slot_dir）
    `tasks`：`task_ledger.list_tasks(workshop_id)` 的结果
    """
    if not history_enabled():
        return {"ok": True, "enabled": False, "squad_id": (squad or {}).get("squad_id"),
                "name": (squad or {}).get("name"),
                "note": "分队履历已关闭（AC_SQUAD_HISTORY=0）；不是没有数据，是开关关了"}
    seqs = [int(x) for x in ((squad or {}).get("member_seqs") or [])]
    by_seq = {int((m or {}).get("member_seq") or 0): (m or {}) for m in (members or [])}
    mine = [t for t in (tasks or []) if int((t or {}).get("member_seq") or 0) in seqs]
    accepted = [t for t in mine if str((t or {}).get("state")) == "accepted"]
    rejected = [t for t in mine if str((t or {}).get("state")) == "rejected"]
    open_now = [t for t in mine if str((t or {}).get("state")) in OPEN_STATES]
    rework_now = [t for t in mine if str((t or {}).get("state")) == "rework"]
    exhausted = [t for t in mine if (t or {}).get("redispatch_budget_exhausted")]

    total = len(accepted) + len(rejected)
    rw_times = [_rework_times(t) for t in (accepted + rejected)]
    quality = {
        "accept_rate": round(len(accepted) / total, 3) if total else None,
        "avg_rework": round(sum(rw_times) / len(rw_times), 2) if rw_times else 0,
        "note": "accept_rate 是**比例**（通过条数/已结条数），不是评分；不排名",
    }

    # 最近 5 条终态：按 created_at 倒序（没有就按任务号）
    def _key(t: dict):
        return (str((t or {}).get("created_at") or ""), int((t or {}).get("task_seq") or 0))

    recent = []
    for t in sorted(accepted + rejected, key=_key, reverse=True)[:5]:
        st = str(t.get("state"))
        word = "通过" if st == "accepted" else ("拒收" if st == "rejected" else st)
        rw = _rework_times(t)
        tail = ""
        if st == "rejected" and (t.get("redispatch_budget_exhausted")):
            tail = "（重派到顶·烂尾）"
        elif rw:
            tail = " · 返工 %d 次" % rw
        recent.append("%s %s%s" % (str(t.get("task_id") or ("T-%03d" % int(t.get("task_seq") or 0))),
                                   word, tail))

    insufficient = total < 3
    return {
        "ok": True, "enabled": True, "workshop_id": workshop_id,
        "squad_id": (squad or {}).get("squad_id"),
        "name": (squad or {}).get("name"),
        "direction": (squad or {}).get("direction"),
        "goal": (squad or {}).get("goal"),
        "members": len(seqs),
        "member_slots": [str((by_seq.get(s) or {}).get("slot_dir") or "") for s in seqs],
        "tasks": {"total": total, "accepted": len(accepted), "rejected": len(rejected),
                  "open": len(open_now), "rework_now": len(rework_now),
                  "exhausted": len(exhausted)},
        "quality": quality,
        "recent": recent,
        "insufficient": insufficient,
        "note": ("只统计**已结束**的任务（通过/拒收）；进行中的 %d 条**不进通过率**"
                 "（否则越忙的队越难看）。" % len(open_now))
                + ("　⚠️ **样本太少（%d 条 < 3），别据此判断这个队行不行**。" % total
                   if insufficient else ""),
        "no_ranking": "这里没有分数、没有排名：只给事实（几条通过、几条返工、最近做了啥）。",
    }


def experience_index(mind_root: str, *, limit: int = 5) -> dict:
    """**只给经验指针、不给正文**（`V140` §2.2）。

    从心智根下找 `memories/**/*.md`，返回**相对路径**列表（最多 `limit` 条，按修改时间倒序）。
    目录不存在/空 ⇒ 空列表 + 说明（不编造路径）。
    """
    from pathlib import Path

    root = Path(str(mind_root or ""))
    if not root or not root.is_dir():
        return {"paths": [], "why": "心智根不在（%s）⇒ 没有经验可指" % (root or "未配置")}
    hits = [p for p in root.rglob("memories/**/*.md") if p.is_file()]
    if not hits:
        return {"paths": [], "why": "心智根里还没有 memories/ 归档 ⇒ 没有经验可指"}
    hits.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return {"paths": [str(p.relative_to(root)).replace("\\", "/") for p in hits[:limit]],
            "why": "只给**路径指针**，不给正文 —— 新队要看自己去读（避免把上一件事的上下文灌进来）"}


def clone_payload(src: dict, *, name: str = "", direction: str = "", goal: str = "",
                  member_seqs=None) -> dict:
    """生成**新分队**的记录（复制方向/目标/成员构成；**绝不带任务历史**）。

    返回的是"要写进 `ws.squads` 的一条新记录"，由调用方落盘。
    """
    return {
        "name": str(name or ((src.get("name") or "") + "（副本）")).strip(),
        "direction": str(direction if direction != "" else (src.get("direction") or "")),
        "goal": str(goal if goal != "" else (src.get("goal") or "")),
        "status": "active",
        "member_seqs": [int(x) for x in (member_seqs
                                        if member_seqs is not None
                                        else (src.get("member_seqs") or []))],
        # 明确**不**带这些（写出来是为了让人一眼看到"故意没有"）
        "copied_from": str(src.get("squad_id") or ""),
        "tasks": [],
    }
