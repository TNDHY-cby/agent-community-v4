# -*- coding: utf-8 -*-
"""工作间可发现（V84）：卡片只放**公开信息**，不放名字/路径/token。"""
from __future__ import annotations

from datetime import datetime


def _gate(ws) -> dict:
    try:
        from . import workshop_gate as g

        info = g.info(ws)
        return {"ok": info.get("ok"), "state": info.get("state"),
                "members_count": info.get("members_count"),
                "verified_count": info.get("verified_count")}
    except Exception:                                        # noqa: BLE001 — 有意静默：卡片缺 gate 不影响发现（宁可少字段也不报错）
        return {}


def card(ws) -> dict:
    """单卡：**只有可公开的字段**。"""
    nodes = [n for n in (getattr(ws, "task_tree", None) or []) if isinstance(n, dict)]
    done = sum(1 for n in nodes if str(n.get("status") or "") == "done")
    return {
        "workshop_id": str(getattr(ws, "workshop_id", "")),
        "name": str(getattr(ws, "name", "") or ""),
        "status": str(getattr(ws, "status", "") or ""),
        "discoverable": bool(getattr(ws, "discoverable", False)),
        "members": len(getattr(ws, "members", None) or []),
        "nodes": {"total": len(nodes), "done": done},
        "next_node": ((nodes[0].get("node_id") if nodes else "") or ""),
        "gate": _gate(ws),
        "updated_at": str(getattr(ws, "created_at", "") or ""),
        "checked_at": datetime.now().isoformat(timespec="seconds"),
        "trust": "platform-self",
        "note": "这是平台自己的工作间卡片；不含成员姓名、工作目录、令牌与任务正文",
    }


def listing(workshops) -> dict:
    rows = [card(ws) for ws in (workshops or [])
            if bool(getattr(ws, "discoverable", False))]
    rows.sort(key=lambda r: r["workshop_id"])
    return {"count": len(rows), "checked_at": datetime.now().isoformat(timespec="seconds"),
            "trust": "platform-self", "workshops": rows}
