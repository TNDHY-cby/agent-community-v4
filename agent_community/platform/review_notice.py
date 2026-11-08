# -*- coding: utf-8 -*-
"""给验收加**通知成员**（2026-11-08）。

## 为什么（真实现场）

`POST …/tasks/{seq}/review` 以前只改账本：组长点「**返工**」「**拒收**」，
**成员那边一个字都没有** —— 他不会知道要改，也不会再看账本 ⇒ 这条活儿就死在账本里。
这是典型的"失败不发声"：组长以为通知到了，其实没有。

## 做法（一处真相）

验收成功后统一走 `notify()`：
- **返工 / 拒收** ⇒ 写该成员 `_notice.md`（带任务号 + 组长原话 + 该怎么做）+ 通知账 `written`；
- **通过** ⇒ 也写一条短的（"这件结了"），成员才知道不用再改；
- 通知账如实记 `written`（**写了不等于看到了**），失败不吞：返回里带 `notify` 字段说清。
"""
from __future__ import annotations

#: 三种结论 → （通知标题，让他做什么）
TEMPLATES = {
    "返工": ("返工通知", "请按组长的意见改完再交一次（产物放进你工位的 `产物/`，或回一次 `task-result`）"),
    "拒收": ("拒收通知", "这条被拒收了：先看组长原话，再找组长确认要不要重派或者换人"),
    "通过": ("验收通过", "这件结了，不用再改"),
}


def text_for(result: str, task_id: str, note: str = "", who: str = "") -> str:
    title, todo = TEMPLATES.get(str(result or "").strip(), ("验收结果", ""))
    parts = ["【%s】%s" % (title, task_id or "")]
    if note.strip():
        parts.append("组长原话：%s" % note.strip())
    if todo:
        parts.append(todo)
    if who:
        parts.append("（验收人：%s）" % who)
    return " —— ".join(parts)


def notify(workshop_id: str, ws, rec: dict, *, result: str, note: str = "",
           who: str = "") -> dict:
    """给该任务的成员写一条验收通知 + 记通知账。**不抛异常**（失败如实返回）。"""
    from . import notice_log as _nl

    slot = str((rec or {}).get("slot_dir") or "").strip().strip("/")
    task_id = str((rec or {}).get("task_id") or "T-%03d" % int((rec or {}).get("task_seq") or 0))
    if not slot:
        return {"ok": False, "why": "这条任务没有 slot_dir，不知道通知谁", "written": False}
    wp = str(getattr(ws, "workspace_dir", "") or "")
    if not wp:
        return {"ok": False, "why": "工作间没有工作目录，写不了通知", "written": False}
    line = text_for(result, task_id, note, who)
    try:
        from .dispatch import append_notice as _append_notice

        r = _append_notice(wp, slot, line)
    except Exception as e:                                     # noqa: BLE001 — 写不进去要说清，不假装成功
        try:
            _nl.record(workshop_id, target="member:%s" % slot, slot=slot,
                       channel="notice_file", result="failed",
                       detail="验收通知（写入失败）：%s" % str(e)[:120],
                       ref="review:%s" % task_id)
        except Exception:                                      # noqa: BLE001 — 记账也失败就只能如实返回
            pass
        return {"ok": False, "why": "%s: %s" % (type(e).__name__, e), "written": False}
    try:
        _nl.record(workshop_id, target="member:%s" % slot, slot=slot, channel="notice_file",
                   result="written" if r.get("ok") else "failed",
                   detail="验收结果：%s" % str(result or ""), ref="review:%s" % task_id)
    except Exception as e:                                     # noqa: BLE001 — 通知已写，记账失败要说
        return {"ok": bool(r.get("ok")), "written": bool(r.get("ok")),
                "file": r.get("file") or "",
                "warning": "通知已写，但通知账没记成：%s: %s" % (type(e).__name__, e)}
    return {"ok": bool(r.get("ok")), "written": bool(r.get("ok")), "slot_dir": slot,
            "file": r.get("file") or "", "line": line,
            "note": "写进他的 `_notice.md`；`written` 表示**写了**，不等于他看了"}
