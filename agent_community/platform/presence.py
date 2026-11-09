# -*- coding: utf-8 -*-
"""`platform/presence`：**成员在不在动**（痕迹可见性的"人"维度）。

## 为什么要它（`V131` 痕迹可见性的延伸，2026-11-08）

`nudge`（催办）是按**任务**看的：某条任务停了就催。
但老板真正会问的另一件事是：「**这个人还在不在干活？**」
—— 一个成员被派了活，之后任务停在 `issued/working`、也没交任何产物 ⇒
界面上和"他正在做、只是慢"长得一样。

## 判定口径（都可算，不猜）

对每个成员，取**最后痕迹**（取最大时间戳）：

| 痕迹来源 | 用哪个字段 |
|---|---|
| 任务账本 | 他那条任务的 `updated_at` / `delivered_at` / `issued_at` |
| 收件事件 | 他工位的产物事件时间 `ts` |

- **只有"有在途任务"（`issued` / `working`）的成员才可能被判失联** ——
  空闲成员（没派活）不制造噪音；
- 最后痕迹缺失（从来没有过）且**有在途任务** ⇒ 也算失联，`why` 写"从派活到现在没有任何痕迹"；
- 超过阈值（`AC_PRESENCE_SILENT_MIN`，默认 **120 分钟**）⇒ `silent=True`。

**不做的**：不猜"他是不是在思考"、不看窗口画面（那是监控室的事）、不把"没派活"当失联。
"""
from __future__ import annotations

#: 默认阈值（分钟）：有在途任务、这么久没有任何痕迹 ⇒ 判失联
SILENT_MIN = 120

#: 算"还在干"的在途状态（`delivered` 不算 —— 那已经是交出来了，等验收是组长的事；
#: ★2026-11-08：`rework`（被返工等他重交）**必须算在途**，否则"被返工的人不动"永远看不出来）
OPEN_STATES = ("issued", "working", "rework")


def _cfg(name: str, default: int) -> int:
    try:
        import os

        v = os.environ.get(name)
        return int(v) if v not in (None, "") else default
    except (TypeError, ValueError):
        return default


# 事件里的时间字段叫 **`at`**（ISO 串），任务账本叫 `updated_at`/`delivered_at`…；
#   两处都要认 —— 第一版漏了 `at` ⇒ "最后痕迹来自产物"永远判不出来（判据③抓到）。
def _ts_of(obj: dict, keys=("updated_at", "delivered_at", "issued_at", "created_at",
                            "at", "ts")) -> float:
    """对象上最后一个可用时间戳（数字或 ISO 字符串都认；拿不到给 0）。"""
    vals = []
    for k in keys:
        v = (obj or {}).get(k)
        if isinstance(v, (int, float)) and v > 0:
            vals.append(float(v))
        elif isinstance(v, str) and v.strip():
            try:
                from datetime import datetime

                vals.append(datetime.fromisoformat(v.replace("Z", "+00:00")).timestamp())
            except (ValueError, TypeError):
                continue
    return max(vals) if vals else 0.0


def _fmt(ts: float) -> str:
    if not ts:
        return ""
    import time as _t

    return _t.strftime("%Y-%m-%d %H:%M:%S", _t.localtime(ts))


def plan(members: list, tasks: list, events: list, *, now: float | None = None,
         silent_min: int | None = None) -> dict:
    """算每个成员的"最后痕迹"与是否失联。**只读**。"""
    import time as _t

    now = float(now if now is not None else _t.time())
    mins = int(silent_min if silent_min is not None else _cfg("AC_PRESENCE_SILENT_MIN", SILENT_MIN))
    by_seq_tasks: dict = {}
    for t in (tasks or []):
        seq = int((t or {}).get("member_seq") or 0)
        if seq:
            by_seq_tasks.setdefault(seq, []).append(t)
    by_slot_events: dict = {}
    for e in (events or []):
        slot = str((e or {}).get("slot_dir") or "").strip().strip("/")
        if slot:
            by_slot_events.setdefault(slot, []).append(e)

    rows = []
    for m in (members or []):
        seq = int((m or {}).get("member_seq") or 0)
        slot = str((m or {}).get("slot_dir") or "").strip().strip("/")
        if not seq or slot == "_lead":          # 组长不进这张表（他拍板，不按"在不在干"衡量）
            continue
        its = by_seq_tasks.get(seq) or []
        open_its = [t for t in its if str((t or {}).get("state") or "") in OPEN_STATES]
        task_ts = max([_ts_of(t) for t in its] or [0.0])
        ev_ts = max([_ts_of(e) for e in (by_slot_events.get(slot) or [])] or [0.0])
        last = max(task_ts, ev_ts)
        channel = ("artifact" if ev_ts and ev_ts >= task_ts else
                   ("task" if task_ts else "none"))
        idle_min = round((now - last) / 60.0, 1) if last else None
        silent = bool(open_its) and (last == 0 or (idle_min is not None and idle_min >= mins))
        # 人话状态（别让前端从 state 自己猜）：
        #   pending ⇒ 还没接入成功；active + silent ⇒ 不动了；active + 有活 ⇒ 在干；没活 ⇒ 空闲
        mstate = str((m or {}).get("state") or "")
        if mstate == "pending":
            state_reason = "还没接入成功（在名单里，但成员没连上平台）"
        elif silent:
            state_reason = "有在途任务但没有任何新痕迹（该问问他）"
        elif open_its:
            state_reason = "在干（有在途任务，最近有痕迹）"
        else:
            state_reason = "空闲（没有在途任务）"
        why = ""
        if silent:
            why = ("有 %d 条在途任务（%s），但从派活到现在**没有任何痕迹**"
                   % (len(open_its), "、".join(str((t or {}).get("task_id") or "T-%03d"
                                                   % int((t or {}).get("task_seq") or 0))
                                              for t in open_its[:4]))
                   if last == 0 else
                   "有 %d 条在途任务，最后痕迹在 %s（已 %.0f 分钟，阈值 %d 分钟）"
                   % (len(open_its), _fmt(last), idle_min or 0, mins))
        rows.append({"member_seq": seq, "slot_dir": slot,
                     "harness_id": str((m or {}).get("harness") or ""),
                     "state": str((m or {}).get("state") or ""),
                     "tier": str((m or {}).get("tier") or ""),
                     "open_tasks": len(open_its),
                     "open_task_ids": [str((t or {}).get("task_id")
                                           or "T-%03d" % int((t or {}).get("task_seq") or 0))
                                       for t in open_its[:6]],
                     "last_seen_ts": round(last, 3),
                     "last_seen_at": _fmt(last),
                     "last_seen_channel": channel,
                     "state_reason": state_reason,
                     "idle_min": idle_min,
                     "silent": silent, "why": why})
    rows.sort(key=lambda r: (not r["silent"], r["slot_dir"] or ""))
    return {"ok": True, "now": round(now, 3), "threshold_min": mins,
            "members": rows,
            "counts": {"total": len(rows),
                       "silent": len([r for r in rows if r["silent"]]),
                       "working": len([r for r in rows if r["open_tasks"] and not r["silent"]])},
            "note": ("只有**有在途任务**（issued/working）的成员才可能被判失联；"
                     "空闲成员不算（不制造噪音）；**不看窗口画面**（那是监控室）。"
                     "`last_seen_channel` 说明最后痕迹来自任务账本还是产物事件。")}
