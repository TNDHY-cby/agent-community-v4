# -*- coding: utf-8 -*-
"""`platform/lead_todo`：**组长今天该管谁**（把催办与失联合成一张待办）。

## 为什么要有它（2026-11-08）

`nudge` 会说"哪条任务停了"，`presence` 会说"哪个人不动了" —— 但组长不会分别去看两张表：
他要的是**一张"我现在该做什么"**的清单。

## 三类事，两种紧急度（不排分，只用"要先处理 / 可以等等"两档）

| kind | 来自 | 人话 | 紧急度 |
|---|---|---|---|
| `silent` | `presence` | 这个人有在途任务但已经不动了 | **先处理**（有人在等、又没动静） |
| `no_review` | `nudge` | 产物到了，等你验收放行 | 超阈值 **2 倍**算先处理，否则可以等等 |
| `no_receipt` | `nudge` | 任务派出去了，他没交回执 | 可以等等（催一次即可） |

**纪律**：只读；每条都带**具体怎么做**（调哪个端点/什么动作）；没有活要管时给空清单 + 一句说明。
"""
from __future__ import annotations

#: 紧急度两档（**不排分**）
URGENT = "now"
LATER = "later"

KIND_LABELS = {
    "silent": "人不动了",
    "no_review": "产物待验收",
    "no_receipt": "任务没回执",
    "awaiting_rework": "返工待重交",
}


def build(members: list, tasks: list, events: list, workshop_id: str = "",
          *, now: float | None = None) -> dict:
    """合成"组长该管谁"的清单（只读）。"""
    from . import nudge as _nd
    from . import presence as _pr

    pr = _pr.plan(members, tasks, events, now=now)
    nd = _nd.plan(tasks, members=members, now=now)
    items = []
    # ① 失联（有在途任务但不动）—— 先处理
    for r in pr.get("members") or []:
        if not r.get("silent"):
            continue
        items.append({
            "kind": "silent", "priority": URGENT,
            "label": KIND_LABELS["silent"],
            "member_seq": r.get("member_seq"), "slot_dir": r.get("slot_dir"),
            "task_id": "、".join(r.get("open_task_ids") or []) or "",
            "task_seq": (r.get("open_task_ids") or [""])[0],
            "age_min": r.get("idle_min"), "threshold_min": pr.get("threshold_min"),
            "why": r.get("why") or "",
            "ask": "问问他在哪一步；或者换人/拆细一点",
            "how": ("`POST /api/workshop/%s/nudge` 会给他写催办；"
                    "要换人就用 `POST …/dispatch` 派给别人" % workshop_id),
        })
    # ② 催办（待验收 / 没回执 / 返工待重交）
    for it in nd.get("items") or []:
        _k = str(it.get("kind") or "")
        if _k == "lead_no_review":
            kind = "no_review"
        elif _k == "member_no_rework_delivery":
            kind = "awaiting_rework"
        else:
            kind = "no_receipt"
        thr = int(it.get("threshold_min") or 0)
        age = float(it.get("age_min") or 0)
        prio = URGENT if (kind == "awaiting_rework"
                          or (kind == "no_review" and thr and age >= thr * 2)) else LATER
        items.append({
            "kind": kind, "priority": prio, "label": KIND_LABELS[kind],
            "member_seq": it.get("member_seq"), "slot_dir": it.get("slot_dir"),
            "task_id": it.get("task_id"), "task_seq": it.get("task_seq"),
            "age_min": it.get("age_min"), "threshold_min": thr,
            "why": "已超 %s 分钟（阈值 %s）" % (int(age), thr),
            "ask": ("验收放行" if kind == "no_review" else
                    ("按意见改完再交" if kind == "awaiting_rework" else "让他交回执")),
            "how": ("`POST /api/workshop/%s/tasks/%s/review`（result 只能是 通过/返工/拒收）"
                    % (workshop_id, it.get("task_seq"))
                    if kind == "no_review" else
                    "他调 `POST /api/harness/task-result`；你也可 `POST …/nudge` 催一次"),
        })
    items.sort(key=lambda x: (0 if x["priority"] == URGENT else 1, -(x.get("age_min") or 0)))
    n_urgent = len([x for x in items if x["priority"] == URGENT])
    return {
        "ok": True, "workshop_id": workshop_id,
        "counts": {"total": len(items), "urgent": n_urgent, "later": len(items) - n_urgent,
                   "silent": len([x for x in items if x["kind"] == "silent"]),
                   "no_review": len([x for x in items if x["kind"] == "no_review"]),
                   "no_receipt": len([x for x in items if x["kind"] == "no_receipt"]),
                   "awaiting_rework": len([x for x in items if x["kind"] == "awaiting_rework"])},
        "urgent": [x for x in items if x["priority"] == URGENT],
        "later": [x for x in items if x["priority"] != URGENT],
        "items": items,
        "thresholds": {"presence_silent_min": pr.get("threshold_min"),
                       "nudge": nd.get("thresholds")},
        "note": ("只有**现在有事可做**的才进来（没有活就是空清单）。**不排分**：只分"
                 "「先处理 / 可以等等」两档；每条都带具体怎么做。"
                 "`silent` 表示有人有在途任务却不动了；`no_review` 表示产物到了等你放行；"
                 "`no_receipt` 表示任务派出去了没交回执。"),
    }

def collect(workshop_id: str) -> dict:
    """拉算待办要用的三份真实数据（任务账本 / 名单 / 收件事件）。**只读**。"""
    from . import artifact_inbox as _inbox
    from . import task_ledger as _tl
    from . import v5_roster as _roster

    tasks = list(_tl.list_tasks(workshop_id) or [])
    try:
        members = list((_roster.roster(workshop_id) or {}).get("members") or [])
    except Exception:                                          # noqa: BLE001 — 名单读不出来就空着（不猜）
        members = []
    try:
        events = [e for e in _inbox.events(limit=500)
                  if str((e or {}).get("workshop_id") or "") == workshop_id]
    except Exception:                                          # noqa: BLE001 — 事件读不出来只影响"产物痕迹"
        events = []
    return {"tasks": tasks, "members": members, "events": events}

def build_all(entries: list, *, now: float | None = None) -> dict:
    """**跨工作间汇总**（大厅首页要的那一口："我今天总共该管什么"）。

    `entries` = `[{"workshop_id","name","status","members","tasks","events"}]`（调用方把数据取好）；
    本函数**只算不写**，可直接单测。返回：

    ```
    {"ok":True,"totals":{"workshops":N,"total":X,"urgent":Y,"later":Z,...},
     "workshops":[{"workshop_id","name","status","counts","urgent":[],"later":[]}],  # 有事的在前
     "empty_workshops":m,   # 一个事都没有的工作间数（不列出来，只给数字）
     "note":"…"}
    ```

    **口径与单间一致**：只分「先处理 / 可以等等」两档、**不排分**；
    排序只用"有没有先处理的事 + 件数"，不是推荐。
    """
    rows, empty_n = [], 0
    tot = {"total": 0, "urgent": 0, "later": 0, "silent": 0,
           "no_review": 0, "no_receipt": 0, "awaiting_rework": 0}
    for e in (entries or []):
        one = build(e.get("members") or [], e.get("tasks") or [],
                    e.get("events") or [], str(e.get("workshop_id") or ""), now=now)
        c = one.get("counts") or {}
        for k in tot:
            tot[k] += int(c.get(k) or 0)
        if int(c.get("total") or 0) == 0:
            empty_n += 1
            continue
        rows.append({"workshop_id": str(e.get("workshop_id") or ""),
                     "name": str(e.get("name") or ""),
                     "status": str(e.get("status") or ""),
                     "counts": c,
                     "urgent": one.get("urgent") or [],
                     "later": one.get("later") or []})
    rows.sort(key=lambda r: (-int((r["counts"] or {}).get("urgent") or 0),
                             -int((r["counts"] or {}).get("total") or 0),
                             r["workshop_id"]))
    return {"ok": True,
            "totals": dict(tot, workshops=len(entries or []),
                           workshops_with_todo=len(rows), empty_workshops=empty_n),
            "workshops": rows,
            "note": ("按工作间汇总「今天该管谁」：**只分先处理/可以等等两档，不排分**；"
                     "排序只按『有没有先处理的事 + 件数』。一个事都没有的工作间只计入 "
                     "`empty_workshops`，不列出来（不刷屏）。口径与单间 `…/lead-todo` 完全一致。")}
