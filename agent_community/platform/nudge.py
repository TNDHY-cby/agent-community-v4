# -*- coding: utf-8 -*-
"""`platform/nudge`：**催办**（"做了没回执 / 交了没验收"主动提醒）。

## 为什么要有它（`V134` ⑧ 的延伸，2026-11-07 真事）

真实现场：工作间「E2E-A2A模板验证」里 `T-001` 停在 `issued`（派出去了没回应）、
`T-002` 停在 `delivered`（产物到了但没回执/没验收）。**平台什么都不说** ——
于是"它做了但没交回来"和"它压根没动"在界面上长得一样。

## 判据（什么算"该催"）

| 情形 | 阈值（可被 env 覆盖） | 催谁 | 催什么 |
|---|---|---|---|
| 任务派出去了，迟迟没有回执/产物 | `AC_NUDGE_ISSUED_MIN`（默认 30 分钟） | 接活的那个成员 | 回执（`task-result`） |
| 产物到了，但组长迟迟没验收 | `AC_NUDGE_DELIVERED_MIN`（默认 60 分钟） | 组长 | 验收（`review`） |

## 三条纪律

1. **只读清单与真发分离**：`plan()` 只算不写；`send()` 才写 `_notice.md` + 通知账；
2. **同一条任务、同一天只催一次**（记在通知账的 `ref=nudge:<task>:<日期>`，据此去重）——
   不许变成刷屏；
3. **催了也记 `written`**（不是"已送达"）：平台只知道写进了通知文件（`V134` ⑧ 同一口径）。
"""
from __future__ import annotations

import time

#: 默认阈值（分钟）
ISSUED_MIN = 30
DELIVERED_MIN = 60

#: 这些状态算"已经了结"，不催
DONE_STATES = ("accepted", "closed", "refuted", "reviewed")


def _cfg(name: str, default: int) -> int:
    try:
        import os

        v = os.environ.get(name)
        return int(v) if v not in (None, "") else default
    except (TypeError, ValueError):
        return default


def _ts_of(task: dict) -> float:
    """取任务"最后一次动"的时间（尽量用真实时间戳，取不到给 0 = 判不了，不瞎催）。"""
    vals = []
    for k in ("updated_at", "delivered_at", "issued_at", "created_at"):
        v = (task or {}).get(k)
        if isinstance(v, (int, float)) and v > 0:
            vals.append(float(v))
        elif isinstance(v, str) and v.strip():
            try:
                from datetime import datetime

                vals.append(datetime.fromisoformat(v.replace("Z", "+00:00")).timestamp())
            except (ValueError, TypeError):
                continue
    return max(vals) if vals else 0.0


def plan(tasks: list, *, members: list | None = None, now: float | None = None,
         issued_min: int | None = None, delivered_min: int | None = None) -> dict:
    """算出"该催谁、催什么"（**只读**）。返回 `{ok, items[], counts, thresholds, note}`。"""
    now = float(now if now is not None else time.time())
    i_min = int(issued_min if issued_min is not None else _cfg("AC_NUDGE_ISSUED_MIN", ISSUED_MIN))
    d_min = int(delivered_min if delivered_min is not None else _cfg("AC_NUDGE_DELIVERED_MIN", DELIVERED_MIN))
    by_seq = {int((m or {}).get("member_seq") or 0): (m or {}) for m in (members or [])}
    items = []
    for t in (tasks or []):
        st = str((t or {}).get("state") or "")
        if st in DONE_STATES:
            continue
        ts = _ts_of(t)
        if not ts:                                  # 没有时间戳 ⇒ 判不了，不催（不假装知道多久没动）
            continue
        age_min = (now - ts) / 60.0
        seq = int((t or {}).get("member_seq") or 0)
        slot = str((t or {}).get("slot_dir") or "") or str((by_seq.get(seq) or {}).get("slot_dir") or "")
        if st in ("issued", "working") and age_min >= i_min:
            items.append({"kind": "member_no_receipt", "task_seq": t.get("task_seq"),
                          "task_id": t.get("task_id") or ("T-%03d" % int(t.get("task_seq") or 0)),
                          "slot_dir": slot, "member_seq": seq, "state": st,
                          "age_min": round(age_min, 1), "threshold_min": i_min,
                          "ask": "回执（POST /api/harness/task-result：做了什么/产物路径/怎么验证）"})
        elif st == "delivered" and age_min >= d_min:
            items.append({"kind": "lead_no_review", "task_seq": t.get("task_seq"),
                          "task_id": t.get("task_id") or ("T-%03d" % int(t.get("task_seq") or 0)),
                          "slot_dir": slot, "member_seq": seq, "state": st,
                          "age_min": round(age_min, 1), "threshold_min": d_min,
                          "ask": "验收（POST …/tasks/{seq}/review；产物已到，等你放行）"})
    return {"ok": True, "items": items, "count": len(items),
            "counts": {"member_no_receipt": len([x for x in items if x["kind"] == "member_no_receipt"]),
                       "lead_no_review": len([x for x in items if x["kind"] == "lead_no_review"])},
            "thresholds": {"issued_min": i_min, "delivered_min": d_min},
            "note": ("`issued/working` 超阈值 ⇒ 催成员回执；`delivered` 超阈值 ⇒ 催组长验收。"
                     "**没有时间戳的任务不催**（判不了时长，不假装知道）。")}


def ref_of(item: dict, day: str = "") -> str:
    """催办去重键：同一条任务、同一天只催一次。"""
    d = str(day or time.strftime("%Y-%m-%d"))
    return "nudge:%s:%s:%s" % (item.get("kind") or "", item.get("task_seq") or 0, d)


def summarize(items: list, limit: int = 8) -> list:
    """把催办清单压成给 `_notice.md` 的短句（每人最多一条，不刷屏）。"""
    by_slot: dict = {}
    for it in (items or []):
        key = (it.get("slot_dir") or "", it.get("kind") or "")
        by_slot.setdefault(key, []).append(it)
    lines = []
    for (slot, kind), its in list(by_slot.items())[:limit]:
        ids = "、".join(str(x.get("task_id")) for x in its[:6])
        if kind == "member_no_receipt":
            lines.append("【催办】%s：%s 还没回执（已超 %s 分钟）—— 请调 task-result 交回执"
                         % (slot or "?", ids, int(its[0].get("threshold_min") or 0)))
        else:
            lines.append("【催办】组长：%s 的产物已到，等你验收（已超 %s 分钟）"
                         % (ids, int(its[0].get("threshold_min") or 0)))
    return lines

def apply(workshop_id: str, ws, *, dry_run: bool = False, now: float | None = None) -> dict:
    """**真催**（端点与后台循环共用这一条路）。

    `dry_run=True` 只算不写。返回 `{ok, count, sent[], failed[], skipped[], note}`。
    去重看通知账里的 `ref=nudge:…:<日期>`（同一条任务同一天只催一次）。
    """
    from . import notice_log as _nl
    from . import task_ledger as _tl
    from . import v5_roster as _roster

    tasks = list(_tl.list_tasks(workshop_id) or [])
    try:
        members = list((_roster.roster(workshop_id) or {}).get("members") or [])
    except Exception:                                          # noqa: BLE001 — 名单读不出来只影响 slot 名
        members = []
    p = plan(tasks, members=members, now=now)
    items = p["items"]
    if dry_run:
        return {"ok": True, "dry_run": True, "count": len(items), "items": items,
                "would_write": summarize(items),
                "note": "dry_run：只算了该催谁，一个字都没写"}
    wp = str(getattr(ws, "workspace_dir", "") or "")
    if not wp:
        return {"ok": False, "code": "NO_WORKSPACE_DIR",
                "error": "这个工作间没有工作目录，写不了催办通知"}
    seen = {str(x.get("ref") or "") for x in (_nl.log(workshop_id, limit=500).get("items") or [])}
    fresh, skipped = [], []
    for it in items:
        ref = ref_of(it)
        if ref in seen:
            skipped.append({"task_id": it.get("task_id"), "ref": ref, "why": "今天已经催过"})
            continue
        fresh.append(dict(it, ref=ref))
    from .dispatch import append_notice as _append_notice

    sent, failed = [], []
    for it in fresh:
        slot = str(it.get("slot_dir") or "") or ("_lead" if it["kind"] == "lead_no_review" else "")
        if not slot:
            failed.append({"task_id": it.get("task_id"), "why": "不知道催给谁（任务没有 slot_dir）"})
            continue
        line = ("【催办】%s：%s —— 已超 %s 分钟"
                % (it.get("task_id"), it.get("ask"), int(it.get("threshold_min") or 0)))
        try:
            r = _append_notice(wp, slot, line)
            sent.append({"task_id": it.get("task_id"), "slot": slot,
                         "ok": bool(r.get("ok")), "file": r.get("file") or ""})
            _nl.record(workshop_id,
                       target=("%s:%s" % ("lead" if it["kind"] == "lead_no_review" else "member", slot)),
                       slot=slot, channel="notice_file",
                       result="written" if r.get("ok") else "failed",
                       detail="催办：%s" % it.get("ask"), ref=it.get("ref") or "")
        except Exception as e:                                 # noqa: BLE001 — 单条失败不影响其它
            failed.append({"task_id": it.get("task_id"), "why": "%s: %s" % (type(e).__name__, e)})
    return {"ok": True, "dry_run": False, "count": len(items),
            "sent": [x for x in sent if x.get("ok")], "failed": failed, "skipped": skipped,
            "note": ("催了 **%d** 条（写进各自 `_notice.md`，通知账记 `written` —— **写了不等于看到了**）；"
                     "今天已催过的 %d 条跳过，不刷屏"
                     % (len([x for x in sent if x.get("ok")]), len(skipped)))}
