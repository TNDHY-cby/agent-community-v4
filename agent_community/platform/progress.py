# -*- coding: utf-8 -*-
"""`platform/progress`：**工作过程上报** —— 让用户"最直观地看到它在干活"（老板 2026-10-26）。

## 一句话

外端 agent 在**它自己的回合里**干活时，每完成一步就调一次 `report_progress(text)`；
平台把这条记下来（时间 + 谁报的 + 属于哪条任务），界面立刻能按时间列出「工作过程」。
**如果它自己还有"往自己会话里写一句"的能力（`session_post` 那类），引导里要求它顺手也写一份** ——
这样用户在它自己的会话里也能看到过程（那部分由**它**发，平台不代写）。

## 三条口径（与 `V101`/`V105` 一致）

1. 平台只记录**它自己报的**进度（这是"它说的话"，不是平台替它演的过程）；
2. 上报永不阻塞它的干活：接口永远 200，除非参数真的缺（那要 400 说清）；
3. 落点 `AC_DATA_DIR/progress.jsonl`（**追加写**、有上限），换目录就跟着走，不进仓库。
"""
from __future__ import annotations

import json
import time
import uuid

MAX_ITEMS = 500


def _path():
    from .server import DATA_DIR

    return DATA_DIR / "progress.jsonl"


def _read_all() -> list:
    p = _path()
    if not p.is_file():
        return []
    out = []
    try:
        for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except ValueError:                                 # 坏行跳过（不影响其它行）
                continue
            if isinstance(obj, dict):
                out.append(obj)
    except Exception as e:                                     # noqa: BLE001 — 读不出来要说，不静默
        print("[progress] 读不出历史（%s: %s），本次按空处理" % (type(e).__name__, e), flush=True)
        return []
    return out


def _rewrite(items: list) -> None:
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".jsonl.tmp")
    tmp.write_text("\n".join(json.dumps(i, ensure_ascii=False) for i in items[-MAX_ITEMS:]) + "\n",
                   encoding="utf-8")
    tmp.replace(p)


def add(text: str, *, harness_id: str = "", task_id: str = "", kind: str = "step") -> dict:
    """记一条工作过程。`kind`：step（一步）/ note（备注）/ block（卡住了）/ done（收尾）。"""
    body = str(text or "").strip()
    if not body:
        return {"ok": False, "error": "text 不能为空（一句话说明你这步干了什么）"}
    item = {"id": "pg_" + uuid.uuid4().hex[:10], "text": body[:500], "kind": str(kind or "step")[:16],
            "harness_id": str(harness_id or ""), "task_id": str(task_id or ""),
            "at_ms": int(time.time() * 1000),
            "at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    items = _read_all()
    items.append(item)
    _rewrite(items)
    return {"ok": True, "item": item, "count": len(items),
            "hint": "这只是**你报的进度**；如果平台给你的能力清单里有 session_post 那类工具，"
                    "请顺手也写一份到你自己的会话里，用户在那里能直接看到过程"}


def recent(*, harness_id: str = "", task_id: str = "", limit: int = 30) -> list:
    items = _read_all()
    if harness_id:
        items = [i for i in items if i.get("harness_id") == harness_id]
    if task_id:
        items = [i for i in items if i.get("task_id") == task_id]
    return items[-max(1, min(int(limit or 30), 200)):]


def counts() -> dict:
    items = _read_all()
    return {"total": len(items),
            "harnesses": len({i.get("harness_id") for i in items if i.get("harness_id")}),
            "tasks": len({i.get("task_id") for i in items if i.get("task_id")})}
