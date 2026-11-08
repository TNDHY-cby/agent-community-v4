# -*- coding: utf-8 -*-
"""`platform/lead_digest`：**给组长的一张日报**（把"该管谁"压成一段人话，写进他的通知文件）。

## 为什么要有它（2026-11-08）

`lead-todo` 是一张表，但**表得有人去看**。真实现场就是这么过的：
成员停了 / 人不动了 / 产物等人验收 —— 只要组长不点界面，这些事**永远没人知道**。
所以给组长一条**主动送达**的摘要（写进 `_lead/_notice.md`，进通知账）。

## 三条纪律

1. **一条摘要，不刷屏**：只算"先处理"的项；超过 8 条就折成"……另有 N 条"；
2. **同一天只送一次**（去重键 `digest:<日期>`，看通知账）—— 不重复打扰；
3. **只读 + 一次写**：`build()` 只算不写；`send()` 才写文件 + 记 `written`（**写了不等于看了**）。
"""
from __future__ import annotations

#: 摘要里最多列几条（其余折成一句）
MAX_LINES = 8


def build(workshop_id: str, todo: dict) -> dict:
    """把 `lead_todo.build()` 的结果压成一段给人看的话（**只算不写**）。"""
    urgent = list((todo or {}).get("urgent") or [])
    later = list((todo or {}).get("later") or [])
    total = int(((todo or {}).get("counts") or {}).get("total") or 0)
    if not total:
        return {"ok": True, "empty": True, "text": "", "counts": (todo or {}).get("counts") or {},
                "note": "现在没有要你管的事 —— 不发日报（不打扰）"}
    lines = []
    for x in urgent[:MAX_LINES]:
        who = x.get("slot_dir") or "?"
        tid = x.get("task_id") or ""
        lines.append("- **%s**：%s%s —— %s"
                     % (x.get("label") or x.get("kind"), who,
                        ("（%s）" % tid) if tid else "", x.get("ask") or ""))
    if len(urgent) > MAX_LINES:
        lines.append("- ……另有 %d 条先处理的，看 `GET …/lead-todo`" % (len(urgent) - MAX_LINES))
    tail = ""
    if later:
        tail = "\n另有 %d 条「可以等等」（催回执一类），`GET …/lead-todo` 看全量。" % len(later)
    text = ("【组长日报】现在有 **%d** 件事等你（先处理 %d / 可以等等 %d）：\n%s%s"
            % (total, len(urgent), len(later), "\n".join(lines), tail))
    return {"ok": True, "empty": False, "text": text,
            "counts": (todo or {}).get("counts") or {},
            "shown": min(len(urgent), MAX_LINES),
            "truncated": max(0, len(urgent) - MAX_LINES)}

def send(workshop_id: str, ws, data: dict, *, dry_run: bool = False) -> dict:
    """**真发**日报（端点与后台循环共用这一条路）。`dry_run` 只回文本不写。"""
    import time as _t

    from . import lead_todo as _lt
    from . import notice_log as _nl

    todo = _lt.build(data.get("members") or [], data.get("tasks") or [],
                     data.get("events") or [], workshop_id)
    out = build(workshop_id, todo)
    if out.get("empty"):
        return {"ok": True, "sent": False, "why": out.get("note"),
                "note": "没有要管的事就不发日报（不打扰）"}
    if dry_run:
        return {"ok": True, "dry_run": True, "sent": False, "text": out.get("text"),
                "note": "dry_run：只算了这段话，一个字没写"}
    ref = "digest:%s" % _t.strftime("%Y-%m-%d")
    try:
        seen = {str(x.get("ref") or "") for x in (_nl.log(workshop_id, limit=500).get("items") or [])}
    except Exception:                                          # noqa: BLE001 — 通知账读不出来就按"没发过"处理
        seen = set()
    if ref in seen:
        return {"ok": True, "sent": False, "skipped": True, "ref": ref,
                "why": "今天的日报已经发过（同一天只发一次，不重复打扰）",
                "text": out.get("text")}
    wp = str(getattr(ws, "workspace_dir", "") or "")
    if not wp:
        return {"ok": False, "code": "NO_WORKSPACE_DIR",
                "error": "这个工作间没有工作目录，写不了日报"}
    from .dispatch import append_notice as _append_notice

    r = _append_notice(wp, "_lead", out.get("text") or "")
    try:
        _nl.record(workshop_id, target="lead:_lead", slot="_lead", channel="notice_file",
                   result="written" if r.get("ok") else "failed",
                   detail="组长日报（该管谁）", ref=ref)
    except Exception as e:                                     # noqa: BLE001 — 通知已写，记账失败要说
        return {"ok": bool(r.get("ok")), "sent": bool(r.get("ok")), "ref": ref,
                "file": r.get("file") or "", "text": out.get("text"),
                "warning": "日报已写，但通知账没记成：%s: %s" % (type(e).__name__, e)}
    return {"ok": bool(r.get("ok")), "sent": bool(r.get("ok")), "ref": ref,
            "file": r.get("file") or "", "text": out.get("text"),
            "note": ("已写进组长 `_lead/_notice.md`，通知账记 `written` —— **写了不等于他看了**；"
                     "同一天再发会被跳过")}
