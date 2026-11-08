# -*- coding: utf-8 -*-
"""`platform/member_todo`：**成员自己该干什么**（"我该干什么"视图，只读）。

## 为什么（2026-11-08）

组长那边已经有 `lead-todo` / `lead-digest`；但**成员这一侧一直靠"自己去翻工位"**：
任务文件、`_notice.md`、账本状态 —— 一个 agent 想确认"我现在该做什么"要读一堆东西。

给它一个**一条查询就够**的视图：

| 它看到 | 依据 |
|---|---|
| 我手上的任务 + **每条我该做什么** | 任务账本里 `member_seq` 是我的那些 |
| 给我的通知（最近 N 条） | 通知账里 `target=member:<我的工位>` |
| 一句"当前最该做的 1 件事" | 按状态优先级挑一条（不排分，只挑） |

## 状态 → 我该做什么（一处真相，别处不要再写第二份）

| 状态 | 动作 |
|---|---|
| `issued` / `working` | 干完了就**交回执**（`POST /api/harness/task-result`） |
| `rework` | 按组长意见**改完再交**（产物放 `产物/`，平台扫到会自动推回 `delivered`） |
| `delivered` | **等组长验收**（别重复交；平台已经收到了） |
| `accepted` | 已通过，**不用再做** |
| `rejected` | 被拒收，**等组长重派**（别再往这条上改） |
"""
from __future__ import annotations

#: 状态 → （做不做、一句话、具体怎么做）
ACTIONS = {
    "issued": ("do", "干完了就交回执",
               "POST /api/harness/task-result（body: workshop_id/member_id/harness_id/ok/result）"),
    "working": ("do", "干完了就交回执",
                "POST /api/harness/task-result（body: workshop_id/member_id/harness_id/ok/result）"),
    "rework": ("do", "按组长意见改完再交",
               "改完把产物放进你工位的 `产物/`（平台扫到会自动推回 delivered），或回一次 task-result"),
    "delivered": ("wait", "等组长验收", "不用重复交；平台已经收到你的产物了"),
    "accepted": ("done", "已通过，不用再做", ""),
    "rejected": ("stop", "被拒收，等组长重派", "别再往这条上改；要重做等组长重新派活"),
}

#: 挑"当前最该做的一件"的顺序（先返工，再没交的，最后等验收的）
_PRIORITY = ("rework", "issued", "working", "delivered", "rejected", "accepted")


def build(member: dict, tasks: list, notices: list, workshop_id: str = "") -> dict:
    """算这个成员的「我该干什么」（只读）。"""
    seq = int((member or {}).get("member_seq") or 0)
    slot = str((member or {}).get("slot_dir") or "").strip().strip("/")
    mine = [t for t in (tasks or []) if int((t or {}).get("member_seq") or 0) == seq]
    rows = []
    for t in mine:
        st = str((t or {}).get("state") or "")
        kind, what, how = ACTIONS.get(st, ("unknown", "状态不认识（如实说不认识）", ""))
        rows.append({"task_seq": t.get("task_seq"),
                     "task_id": str(t.get("task_id") or "T-%03d" % int(t.get("task_seq") or 0)),
                     "title": str(t.get("title") or ""),
                     "state": st, "kind": kind, "what": what, "how": how,
                     "review_note": str(t.get("review_note") or ""),
                     "reviewed_by": str(t.get("reviewed_by") or ""),
                     "file": str(t.get("file") or "")})
    order = {s: i for i, s in enumerate(_PRIORITY)}
    rows.sort(key=lambda r: (order.get(r["state"], 99), -int(r.get("task_seq") or 0)))
    nxt = next((r for r in rows if r["kind"] == "do"), None)
    # 通知：只留"给我的"
    mine_notices = [n for n in (notices or [])
                    if str((n or {}).get("target") or "") in
                    ("member:%s" % slot, "member:%s" % slot.strip("/"), slot)]
    return {
        "ok": True, "workshop_id": workshop_id, "member_seq": seq, "slot_dir": slot,
        "counts": {"tasks": len(rows),
                   "todo": len([r for r in rows if r["kind"] == "do"]),
                   "waiting": len([r for r in rows if r["kind"] == "wait"]),
                   "notices": len(mine_notices)},
        "next": ({"task_id": nxt["task_id"], "state": nxt["state"],
                  "what": nxt["what"], "how": nxt["how"]} if nxt else None),
        "tasks": rows,
        "notices": mine_notices[-10:],
        "note": ("`kind`: `do`＝现在该你动 / `wait`＝等组长 / `done`＝已通过 / `stop`＝别再动。"
                 "`next` 只挑**一件**最该做的（先返工，再没交的，最后等验收的）；**不排分**。"
                 "通知来自通知账（`written` 只表示写进了你的通知文件，平台不知道你看没看）。"),
    }
