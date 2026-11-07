# -*- coding: utf-8 -*-
"""`platform/notice_log`：**通知投递记录**（设计 `V134` ⑧，2026-11-07）。

## 为什么要它

现在"通知"只有两种动作：往 `_lead/_notice.md` 追加一行、或视觉投递；**没有任何记录**。
于是"组长不看文件"＝没通知，而界面仍显示"已通知" ⇒ **失败被吞**（正是准则⑤要防的）。

## 三类结果必须分开（不许把 written 说成 delivered）

| result | 意思 | 能不能说"已送达" |
|---|---|---|
| `written` | 写进了通知文件（对方看没看**不知道**） | ❌ 只能说"已写到 XX 文件" |
| `delivered` | 真投递到对方（视觉/协议），且我们核过 | ✅ |
| `failed` | 试了但没成（带真实原因） | ❌ 必须说清为什么 |

落盘：`<data_dir>/notice_log.json`，按工作间保留最近 `MAX_PER_WS` 条（防无限增长）。
"""
from __future__ import annotations

import json
import time
from pathlib import Path

#: 每个工作间保留多少条（滚动）
MAX_PER_WS = 500

#: 三类结果（**不许自己造第四种**）
RESULTS = ("written", "delivered", "failed")

RESULT_LABELS = {
    "written": "已写进通知文件（对方看没看，平台不知道）",
    "delivered": "已真投递到对方（平台核过）",
    "failed": "没成（原因见 detail）",
}


def _path() -> Path:
    from .env_spec import data_dir

    return Path(data_dir()) / "notice_log.json"


def _load() -> dict:
    p = _path()
    if not p.is_file():
        return {}
    try:
        d = json.loads(p.read_text(encoding="utf-8") or "{}")
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(d: dict) -> None:
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    tmp.replace(p)


def record(workshop_id: str, *, target: str, slot: str = "", channel: str = "",
           result: str = "written", detail: str = "", ref: str = "") -> dict:
    """记一条通知。`result` 只能是 `written` / `delivered` / `failed`（**别的值直接拒**）。"""
    if str(result) not in RESULTS:
        return {"ok": False, "code": "BAD_RESULT",
                "error": "result 只能是 %s（收到 %r）" % (" / ".join(RESULTS), result)}
    wid = str(workshop_id or "")
    if not wid:
        return {"ok": False, "code": "NO_WORKSHOP", "error": "要 workshop_id"}
    d = _load()
    rows = list(d.get(wid) or [])
    row = {"ts": round(time.time(), 3), "target": str(target or ""), "slot": str(slot or ""),
           "channel": str(channel or ""), "result": str(result),
           "detail": str(detail or "")[:400], "ref": str(ref or "")}
    rows.append(row)
    d[wid] = rows[-MAX_PER_WS:]
    try:
        _save(d)
    except OSError as e:                                       # noqa: BLE001 — 记录写不进去不能把通知本身弄挂
        return {"ok": False, "code": "LOG_WRITE_FAILED",
                "error": "通知记录没写进去：%s: %s" % (type(e).__name__, e),
                "note": "通知本身可能已经发出去了；但这条账没记上"}
    return {"ok": True, "row": row}


def log(workshop_id: str, limit: int = 100) -> dict:
    """读通知账（**按时间正序**，只给最近 `limit` 条）+ 三类计数 + 一句话口径。"""
    wid = str(workshop_id or "")
    rows = list((_load().get(wid) or []))
    n = max(1, int(limit or 100))
    counts = {k: 0 for k in RESULTS}
    for r in rows:
        k = str(r.get("result") or "")
        if k in counts:
            counts[k] += 1
    return {"ok": True, "workshop_id": wid, "count": len(rows),
            "counts": counts, "items": rows[-n:],
            "labels": dict(RESULT_LABELS),
            "note": ("`written` **不等于**已送达：平台只知道写进了通知文件，"
                     "不知道对方看没看；只有 `delivered` 才是真投递。")}
