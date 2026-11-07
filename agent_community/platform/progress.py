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

## ★2026-11-06（`V131` §5/§6.3/§7 落地，三处补强）

- **落盘前脱敏**（§6.3）：`text` 是**对方写的自由文本**，可能带真实路径/密钥 ⇒ 先过
  `agent_community/redact.scrub_text()`（与 MCP 资源面**同一份**正则，一处真相）；
- **按天保留**（§5）：原来只按条数滚动（500 条），一天灌满就把早先历史挤光 ⇒ 再加
  `AC_PROGRESS_KEEP_DAYS`（默认 30 天）；
- **失败要说话**（§7 第 5 条）：落盘失败（磁盘满/没权限）不再把裸异常冒成 500，
  而是回 `{ok: False, error: "<人话>（<真实原因>）"}`，让人知道"这条没记上"。
"""
from __future__ import annotations

import json
import os
import time
import uuid

MAX_ITEMS = 500
#: 默认保留多少天（`AC_PROGRESS_KEEP_DAYS`；<=0 表示只按条数裁）
DEFAULT_KEEP_DAYS = 30


def _path():
    from .server import DATA_DIR

    return DATA_DIR / "progress.jsonl"


def _keep_days() -> int:
    raw = (os.environ.get("AC_PROGRESS_KEEP_DAYS") or "").strip()
    if not raw:
        return DEFAULT_KEEP_DAYS
    try:
        return int(raw)
    except ValueError:
        return DEFAULT_KEEP_DAYS


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
            except ValueError:
                continue
            if isinstance(obj, dict):
                out.append(obj)
    except Exception as e:                                     # noqa: BLE001 — 读不出来要说，不静默
        print("[progress] 读不出历史（%s: %s），本次按空处理" % (type(e).__name__, e), flush=True)
        return []
    return out


def _trim(items: list) -> list:
    """先按天裁、再按条数裁（两条上限都生效）。"""
    days = _keep_days()
    if days > 0:
        cutoff_ms = (time.time() - days * 86400) * 1000
        try:
            items = [i for i in items if float(i.get("at_ms") or 0) >= cutoff_ms]
        except (TypeError, ValueError):                        # noqa: BLE001 — 有意静默：坏时间戳的条目
            pass                                              #   不因为过滤出错就整批丢掉（宁可多留几条）
    return items[-MAX_ITEMS:]


def _rewrite(items: list) -> None:
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".jsonl.tmp")
    tmp.write_text("\n".join(json.dumps(i, ensure_ascii=False) for i in _trim(items)) + "\n",
                   encoding="utf-8")
    tmp.replace(p)


def add(text: str, *, harness_id: str = "", task_id: str = "", kind: str = "step") -> dict:
    """记一条工作过程。`kind`：step（一步）/ note（备注）/ block（卡住了）/ done（收尾）。

    `text` **落盘前会脱敏**（路径/密钥 → `<path>`/`<hidden>`）；返回里的 `text` 就是**实际存下来的**那份，
    调用方据此知道"你写的原话被洗过"。
    """
    from ..redact import scrub_text

    body = scrub_text(str(text or "").strip())
    if not body:
        return {"ok": False, "error": "text 不能为空（一句话说明你这步干了什么）"}
    item = {"id": "pg_" + uuid.uuid4().hex[:10], "text": body[:500], "kind": str(kind or "step")[:16],
            "harness_id": str(harness_id or ""), "task_id": str(task_id or ""),
            "at_ms": int(time.time() * 1000),
            "at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    items = _read_all()
    items.append(item)
    try:
        _rewrite(items)
    except Exception as e:                                     # noqa: BLE001 — 落盘失败要说话（V131 §7）
        why = "%s: %s" % (type(e).__name__, str(e)[:120])
        print("[progress] 落盘失败（这条没记上）：%s" % why, flush=True)
        return {"ok": False, "code": "PROGRESS_WRITE_FAILED",
                "error": "过程没记上：写不进 %s（常见原因：磁盘满 / 没权限 / 目录只读）—— %s"
                         % (_path().name, why),
                "next": "把数据目录（AC_DATA_DIR）指到可写位置，或清一下磁盘再报一次"}
    return {"ok": True, "item": item, "count": len(items), "text": item["text"],
            "redacted": item["text"] != str(text or "").strip(),
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
