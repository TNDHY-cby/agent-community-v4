# -*- coding: utf-8 -*-
"""`routers/activity`：**一个接入成员最近在干什么**（只读聚合，V131 §4 的最小痕迹链）。

## 为什么单独做一条端点

现在这些事实**散在五个地方**，用户要自己拼：

| 事实 | 在哪 |
|---|---|
| 它自己报的"我改完 X 了" | `progress.jsonl`（`/api/progress`） |
| **我们调它**的工具、结果怎么了 | `mcp_outcalls.json`（`/api/docking/harness/{id}/mcp-calls`） |
| **它调我们**的工具 | `mcp_calls.json`（`/api/mcp/calls`，**只记客户端名，关联不到 harness**） |
| 派给它的任务状态 | 任务账本（需要工作间上下文） |
| 它的会话内容 | 只能**调它的** `session_messages` 类工具读 |

本端点把它们合成**一条按时间排的痕迹链**，并把"能看见"与"可信"分开标（V131 §4.2）：

- `source`：`self_reported`（它自己说的）/ `platform_observed`（平台自己做的）
- `judge`：`unverified`（未复核）/ `verified`（平台可核）/ `n/a`

## 三条纪律

1. **只读**：不写任何状态、不改账本、不替它记账；
2. **读不到就说清**：会话读不到给 `capable=false` + `why` + 引导，**不显示空表**（空 ≠ 没发生）；
3. **关联不确定就标注**：`mcp_calls` 只记客户端名，**不硬绑 harness**，只在能对上时才列。
"""
from __future__ import annotations

import time

from fastapi import APIRouter

router = APIRouter(tags=["activity"])

#: 单次读会话最多返回几条（别把人家的会话一次搬空）
MAX_SESSION_ITEMS = 20
#: 调它读会话的超时（秒）——短，读不到就如实说
SESSION_TIMEOUT = 8.0


def _ts(epoch) -> str:
    try:
        return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(float(epoch)))
    except Exception:                                          # noqa: BLE001 — 坏时间不拖垮整表
        return ""


def _sess_and_info(harness_id: str):
    from ..harness_adapter import harness_manager as _hm

    sess = _hm.sessions.get(str(harness_id or ""))
    if sess is None:
        return None, None
    return sess, getattr(sess, "info", None)


def _tool_names(info) -> list:
    names = []
    for x in (getattr(info, "tools", None) or []):
        n = str(getattr(x, "name", "") or (x if isinstance(x, str) else ""))
        if n:
            names.append(n)
    return names


def _collect_work_trace(harness_id: str, limit: int) -> tuple[list, dict, list]:
    """把能按 harness 精确关联的痕迹合成一条时间线（正序）。返回 (rows, counts, caveats)。"""
    rows: list = []
    counts = {"progress": 0, "outbound": 0, "platform_tool_calls": 0}
    caveats: list = []

    # ① 它自己报的进度（source=self_reported，未复核）
    try:
        from .. import progress as _pg

        items = _pg.recent(harness_id=harness_id, limit=limit) or []
        counts["progress"] = len(items)
        for it in items:
            rows.append({
                "at": _ts(it.get("at_ms", 0) / 1000.0 if it.get("at_ms") else it.get("at", 0)),
                "_sort": float(it.get("at_ms", 0) or 0) or -1,
                "kind": "progress",
                "source": "self_reported",
                "judge": "unverified",
                "text": str(it.get("text") or "")[:400],
                "extra": {"step_kind": it.get("kind") or "step",
                          "task_id": it.get("task_id") or ""},
                "note": "这是**它自己报的**（平台没替它验）；要核实请看证据档与采信态",
            })
    except Exception as e:                                     # noqa: BLE001 — 没进度就是没进度，但要说明
        caveats.append("进度读不出来：%s: %s" % (type(e).__name__, str(e)[:80]))

    # ② 我们调它的工具（source=platform_observed，可核）
    try:
        from .. import mcp_result_poll as _mr

        outs = _mr.list_outcalls(harness_id=harness_id, limit=limit) or []
        counts["outbound"] = len(outs)
        for it in outs:
            st = str(it.get("state") or "")
            rows.append({
                "at": _ts(it.get("added_at", 0)),
                "_sort": float(it.get("added_at", 0) or 0) * 1000,
                "kind": "outbound_call",
                "source": "platform_observed",
                "judge": "verified" if st in ("done", "failed", "cancelled") else "unverified",
                "text": ("我们调了它的 `%s`，状态 %s" % (it.get("tool") or "?", st)),
                "extra": {"tool": it.get("tool") or "", "state": st,
                          "handle": str(it.get("handle") or "")[:40],
                          "why": str(it.get("why") or "")[:200],
                          "error": str(it.get("error") or "")[:200]},
                "note": "平台自己发起的调用；`unsupported` 表示它不支持异步约定（查不了）",
            })
    except Exception as e:                                     # noqa: BLE001
        caveats.append("出站调用读不出来：%s: %s" % (type(e).__name__, str(e)[:80]))

    # ③ 它调我们的工具：**只能按客户端名模糊关联**，不硬绑
    try:
        from . import mcp_http as _mh

        calls = _mh.recent_calls(limit=max(limit, 50)) or []
        mine = [c for c in calls
                if str(harness_id or "").lower() in str(c.get("client") or "").lower()]
        counts["platform_tool_calls"] = len(mine)
        for c in mine[:limit]:
            rows.append({
                "at": str(c.get("at") or ""),
                "_sort": float(c.get("at_ms", 0) or 0),
                "kind": "inbound_tool_call",
                "source": "platform_observed",
                "judge": "verified",
                "text": "它调了平台的 `%s`（%s）" % (c.get("tool") or c.get("kind") or "?",
                                                     "成功" if c.get("ok") else "失败"),
                "extra": {"tool": c.get("tool") or "", "ok": bool(c.get("ok")),
                          "client": c.get("client") or ""},
                "note": "按客户端名匹配（MCP 留痕只记 clientInfo）；对不上就可能漏",
            })
        if not mine:
            caveats.append("它调过平台的工具这件事**关联不到**（MCP 调用留痕只记客户端名）："
                           "去 `/api/mcp/calls` 看全量，别把这里的空当成『它没来过』")
    except Exception as e:                                     # noqa: BLE001
        caveats.append("平台侧调用留痕读不出来：%s: %s" % (type(e).__name__, str(e)[:80]))

    rows.sort(key=lambda r: (r.get("_sort") or 0, r.get("at") or ""))
    for r in rows:
        r.pop("_sort", None)
    return rows[-limit:], counts, caveats


async def _read_session(harness_id: str, info, tool_names: list) -> dict:
    """读它的会话 —— 实现已抽到 `platform/activity_read.py`（与**监控室会话观察窗**一处真相）。"""
    from .. import activity_read as _ar

    return await _ar.read_session(harness_id, info, tool_names)


@router.get("/api/harness/{harness_id}/activity")
async def harness_activity(harness_id: str, limit: int = 30, read_session: int = 1):
    """**这个成员最近在干什么**（只读）：痕迹时间线 + 它的会话（若有读会话工具）。

    用途（老板 2026-11-04）：「MCP 协议成员要有用户可以直观看见的工作痕迹和会话」。
    """
    from fastapi.responses import JSONResponse

    sess, info = _sess_and_info(harness_id)
    if sess is None:
        return JSONResponse({"ok": False, "code": "NO_SUCH_HARNESS",
                             "error": "没有这个接入：%s" % harness_id,
                             "next": "去「对接中心」确认它还在"}, status_code=404)

    names = _tool_names(info)
    rows, counts, caveats = _collect_work_trace(harness_id, max(1, min(int(limit or 30), 100)))

    session = {"capable": False, "why": "未读取（read_session=0）", "tool": "",
               "items": [], "read_at": "", "note": ""}
    if int(read_session or 0):
        session = await _read_session(harness_id, info, names)

    return {
        "ok": True,
        "harness_id": harness_id,
        "at": _ts(time.time()),
        "identity": {
            "kind": str(getattr(info, "wakeup_method", "") or ""),
            "name": str(getattr(info, "harness_name", "") or ""),
            "status": str(getattr(sess, "status", "") or ""),
            "tools": names[:40],
        },
        "work_trace": rows,
        "counts": counts,
        "session": session,
        "caveats": caveats,
        "how_to_read": ("`source=self_reported` 是它说的（未复核）；`platform_observed` 是平台做的；"
                        "`judge` 说明能不能当证据用。三件事别混：在干活 / 干完了 / 干得对。"),
    }
