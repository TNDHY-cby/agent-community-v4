# -*- coding: utf-8 -*-
"""`platform/session_notes`：**"让它自己发"**的载体（老板 2026-10-25 口径）。

## 一句话

要把一句话放进外端 agent 自己的会话里，**最好的方式是让它自己发** ——
平台**不代写**它的会话、不改它的库，只做两件事：

1. **放一句待办**（平台/界面写：「请你在自己的会话里说这句」）；
2. **收它的自报**（它调 `report_session_sent` 说"我发了"，平台记下**时间 + 是哪个客户端来的**）。

## 为什么不代写

2026-10-25 的两次教训（`V101`/`V103`）：平台调第三方插件往它库里插行，
**看着"发进去了"，其实它本人从没参与**（应用关着也照样"成功"）。
⇒ 现行口径：**内容是平台给的，发送动作是它的**。平台只记录"它说它发了"，
并在界面/接口上**明确标注这是它自报**（自报不是硬证据，这是 `V101` 的规矩）。

## 落在哪

`AC_DATA_DIR/session_notes.json`（不进仓库、可换目录）；最多保留 200 条。
"""
from __future__ import annotations

import json
import time
import uuid

MAX_NOTES = 200


def _path():
    from .server import DATA_DIR

    return DATA_DIR / "session_notes.json"


def _load() -> list:
    try:
        p = _path()
        if not p.is_file():
            return []
        data = json.loads(p.read_text(encoding="utf-8") or "[]")
        return data if isinstance(data, list) else []
    except Exception as e:                                   # noqa: BLE001 — 读坏了要说清，不静默
        print("[session_notes] 读不出来（%s: %s）；本次按空处理" % (type(e).__name__, e), flush=True)
        return []


def _save(items: list) -> None:
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(items[-MAX_NOTES:], ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(p)


def add(text: str, *, harness_id: str = "", note: str = "") -> dict:
    """放一句「请它自己在会话里发」的话。**平台不代发**。"""
    body = str(text or "").strip()
    if not body:
        return {"ok": False, "error": "text 不能为空"}
    items = _load()
    item = {"id": "note_" + uuid.uuid4().hex[:10], "text": body,
            "harness_id": str(harness_id or ""), "note": str(note or ""),
            "state": "pending", "created_at": int(time.time() * 1000),
            "sent_at": 0, "said": "", "client": "", "sent_by": ""}
    items.append(item)
    _save(items)
    return {"ok": True, "note": item}


def pending(*, harness_id: str = "", limit: int = 10) -> list:
    items = [i for i in _load() if i.get("state") == "pending"]
    if harness_id:
        items = [i for i in items if not i.get("harness_id") or i["harness_id"] == harness_id]
    return items[:max(1, int(limit or 10))]


def mark_sent(note_id: str, *, said: str = "", client: str = "",
              harness_id: str = "") -> dict:
    """它回报"我已经在自己的会话里发了"。

    ⚠️ 口径：这是**它的自报**（`V101`：自报不算硬证据）。平台记下来，
    但界面/接口上必须写成"它说它发了"，不许写成"平台发出去了"。
    """
    items = _load()
    for i in items:
        if i.get("id") == str(note_id or ""):
            i["state"] = "sent"
            i["sent_at"] = int(time.time() * 1000)
            i["said"] = str(said or i.get("text") or "")
            i["client"] = str(client or "")
            i["sent_by"] = str(harness_id or "")
            _save(items)
            return {"ok": True, "note": i,
                    "scope": "这是它自己的回报（自报），平台没有代它写任何东西"}
    return {"ok": False, "error": "没有这条待办：%s" % note_id,
            "next": "先 GET /api/mcp/session-notes?pending=1 看有哪些"}


def recent(limit: int = 20) -> list:
    items = _load()
    return list(reversed(items[-max(1, int(limit or 20)):]))


def counts() -> dict:
    items = _load()
    return {"total": len(items),
            "pending": sum(1 for i in items if i.get("state") == "pending"),
            "sent": sum(1 for i in items if i.get("state") == "sent")}
