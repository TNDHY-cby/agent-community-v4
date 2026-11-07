# -*- coding: utf-8 -*-
"""`routers/artifact_inbox`：**收件目录的发现与上报**（设计 `V133`，老板 2026-11-07）。

三个只读/半只读入口（都不改成员文件）：

| 入口 | 干什么 |
|---|---|
| `POST /api/workshop/{id}/scan-inbox` | **手动扫一次**（立即返回本轮 `new/changed/baseline/errors`） |
| `GET  /api/workshop/{id}/inbox-events?limit=` | 只读事件流（倒序，最新在前） |
| `GET  /api/workshop/{id}/inbox-status` | 监视器状态（是否在跑、上次扫、上次错误） |
| `GET  /api/artifact-inbox/status` | 实例级监视器状态（不依赖工作间 id） |

算法全在 `platform/artifact_inbox.py`（可单测）；这里只做 HTTP 包装与"说清失败"。
"""
from __future__ import annotations

from fastapi import APIRouter, Request

from .. import artifact_inbox as inbox

router = APIRouter(tags=["artifact-inbox"])


def _ws(ws_id: str):
    # ⚠️ 惰性导入：`server.py` 在本模块的导入链上（它先 include 本模块）——
    #   模块级 `from .. import server` 会造成循环导入，服务直接起不来（实测踩过）。
    from .. import server as _srv

    return (_srv.workshops or {}).get(ws_id)


@router.post("/api/workshop/{ws_id}/scan-inbox")
async def scan_inbox(ws_id: str, request: Request):
    """手动扫一次这个工作间的收件目录。

    body（可选）：`{"dry_run": true}` —— 只算不落盘、不通知组长（给界面"先看看"用）。
    失败语义：工作间不存在 ⇒ 404；目录不存在/不可读 ⇒ 200 但在 `errors` 里逐条说清
    （**不把它当"没有新文件"**）。
    """
    from fastapi.responses import JSONResponse

    ws = _ws(ws_id)
    if ws is None:
        return JSONResponse({"ok": False, "code": "NO_SUCH_WORKSHOP",
                             "error": "没有这个工作间：%s" % ws_id,
                             "next": "先 GET /api/workshops 看有哪些"}, status_code=404)
    try:
        body = await request.json()
    except Exception:                                          # noqa: BLE001 — 无 body 就是正常扫
        body = {}
    body = body if isinstance(body, dict) else {}
    out = inbox.scan_workshop(ws_id, dry_run=bool(body.get("dry_run")))
    if not out.get("ok") and out.get("code") == "NO_SUCH_WORKSHOP":
        return JSONResponse(out, status_code=404)
    return out


@router.get("/api/workshop/{ws_id}/inbox-events")
async def inbox_events(ws_id: str, limit: int = 50):
    """只读：最近的事件（倒序）。事件是**平台自己扫盘得到的**（`source=platform_observed`）。"""
    from fastapi.responses import JSONResponse

    if _ws(ws_id) is None:
        return JSONResponse({"ok": False, "code": "NO_SUCH_WORKSHOP",
                             "error": "没有这个工作间：%s" % ws_id}, status_code=404)
    items = [x for x in inbox.events(limit=limit) if str(x.get("workshop_id") or "") == ws_id]
    return {"ok": True, "workshop_id": ws_id, "count": len(items), "events": items,
            "unread": inbox.unread_of(ws_id),
            "how_to_read": ("`source=platform_observed` 是平台扫盘得到的（文件与 sha256 都是真的）；"
                            "**这不等于验收通过** —— 验收看证据档与采信态。"
                            "`needs_review=true` 的事件已经绑到任务上、等人验收；"
                            "看完用 `POST …/inbox-events/read` 推进已读水位（与大厅未读同一口径）")}


@router.post("/api/workshop/{ws_id}/inbox-events/read")
async def inbox_events_read(ws_id: str, request: Request):
    """推进这个工作间的**事件已读水位**（`V134` ④；与大厅未读同一套 `read_marks`）。

    body（可选）：`{"seq": N}`；不传 ⇒ 推到当前最大事件 seq。只前进不后退（与 `read_marks` 同规则）。
    """
    from fastapi.responses import JSONResponse

    if _ws(ws_id) is None:
        return JSONResponse({"ok": False, "code": "NO_SUCH_WORKSHOP",
                             "error": "没有这个工作间：%s" % ws_id}, status_code=404)
    try:
        body = await request.json()
    except Exception:                                          # noqa: BLE001 — 无 body = 推到最新
        body = {}
    body = body if isinstance(body, dict) else {}
    seq = body.get("seq")
    try:
        seq_i = None if seq is None else int(seq)
    except (TypeError, ValueError):
        return JSONResponse({"ok": False, "code": "BAD_SEQ",
                             "error": "seq 要是整数，收到：%r" % (seq,),
                             "next": "不传 seq 就推到最新；或传一个整数"}, status_code=400)
    return inbox.mark_read(ws_id, seq_i)


@router.get("/api/workshop/{ws_id}/inbox-status")
async def inbox_status(ws_id: str):
    """监视器状态 + 这个工作间各工位的收件目录（**先看清平台在盯哪儿**）。"""
    from fastapi.responses import JSONResponse

    ws = _ws(ws_id)
    if ws is None:
        return JSONResponse({"ok": False, "code": "NO_SUCH_WORKSHOP",
                             "error": "没有这个工作间：%s" % ws_id}, status_code=404)
    from .. import v5_roster as _roster

    dirs = []
    for m in (_roster.effective_members(ws_id).get("members") or []):
        base, label, err = inbox.inbox_of(ws, m)
        dirs.append({"slot_dir": str((m or {}).get("slot_dir") or ""),
                     "harness": str((m or {}).get("harness") or ""),
                     "dir": base.as_posix() if str(base) else "",
                     "source": label, "exists": bool(str(base) and base.is_dir()),
                     "error": err})
    st = inbox.status()
    st["ok"] = True
    st["workshop_id"] = ws_id
    st["inbox_dirs"] = dirs
    return st


@router.get("/api/artifact-inbox/status")
async def inbox_status_global():
    """实例级监视器状态（不带工作间 id；给设置页/自检用）。"""
    st = inbox.status()
    st["ok"] = True
    return st
