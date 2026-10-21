# -*- coding: utf-8 -*-
"""运行快照（V76 §3）：按任务**冻结**的执行记录，只追加，用于回放。

为什么单独一层：回放要可信 ⇒ 只追加、不改写、超上限**留一条截断记录**（不静默丢）。
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime

KINDS = ("dispatch", "reply", "approval", "complete", "fail", "truncated")


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def max_snapshots() -> int:
    try:
        return max(10, int(os.environ.get("AC_SNAPSHOT_MAX", "200")))
    except Exception:                                        # noqa: BLE001
        return 200


def append(task, kind: str, summary: str = "", detail=None, actor: str = "user",
           evidence_ref: str = "") -> dict:
    """追加一条快照（**只追加**：永远新 snap_id，不覆盖旧记录）。"""
    rec = {"snap_id": "sn-" + uuid.uuid4().hex[:10], "at": _now(),
           "kind": str(kind or "reply"), "task_id": str(getattr(task, "id", "") or ""),
           "actor": str(actor or "user")[:60], "summary": str(summary or "")[:200],
           "detail": detail if isinstance(detail, dict) else {},
           "evidence_ref": str(evidence_ref or "")[:200]}
    snaps = list(getattr(task, "snapshots", None) or [])
    # 上限**只数真实记录**；截断标记单独一条（累计 dropped），不参与计数也不被顶掉
    marker = next((r for r in snaps if r.get("kind") == "truncated"), None)
    real = [r for r in snaps if r.get("kind") != "truncated"]
    real.append(rec)
    cap = max_snapshots()
    dropped_total = int(((marker or {}).get("detail") or {}).get("dropped", 0) or 0)
    if len(real) > cap:
        dropped_total += len(real) - cap
        real = real[-cap:]
        marker = {"snap_id": "sn-trunc-" + uuid.uuid4().hex[:6], "at": _now(),
                  "kind": "truncated", "task_id": rec["task_id"], "actor": "platform",
                  "summary": "快照超过上限（%d 条），累计已丢弃更早的 %d 条" % (cap, dropped_total),
                  "detail": {"dropped": dropped_total, "cap": cap}, "evidence_ref": ""}
    snaps = ([marker] if marker else []) + real
    task.snapshots = snaps
    return rec


def list_of(task, limit: int = 50) -> list:
    """正序回放（老的在前）；`limit` 取**最近的 N 条**但仍按时间正序返回。"""
    snaps = list(getattr(task, "snapshots", None) or [])
    try:
        n = max(1, int(limit))
    except (TypeError, ValueError):
        n = 50
    return snaps[-n:]


def find(task, snap_id: str):
    for rec in list(getattr(task, "snapshots", None) or []):
        if rec.get("snap_id") == snap_id:
            return rec
    return None
