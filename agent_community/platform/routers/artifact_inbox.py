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
    # ★2026-11-08（V134 ⑩ 的最后一块）：**把本轮新事件登记进资源库**，组长才有"全貌"看
    #   （不然只能一个个工位翻）。`dry_run` 时不登记（连算都不算副作用）。
    if bool(body.get("register")) and not bool(body.get("dry_run")):
        try:
            out["registered"] = _register_events(ws, out.get("new") or [])
        except Exception as e:                                 # noqa: BLE001 — 登记失败不能让扫描失败
            out["registered"] = {"ok": False,
                                 "error": "登记进资源库失败：%s: %s" % (type(e).__name__, e),
                                 "note": "扫描结果仍然有效；只是资源库里少了这几条"}
    return out


def _register_events(ws, new_items: list) -> dict:
    """把新事件登记进 `ws.resources`（幂等：同 path+sha256 不重复登记）。

    为什么要它：成员交的文件散在各工位 `产物/`，组长要看全貌只能一个个翻；
    登记进资源库后一处可见，且 `exists` 会做真 stat（V134 ⑩ 的另一半）。
    """
    from ..workshop import write_resources_manifest

    have = {(str((r or {}).get("path") or ""), str((r or {}).get("sha256") or ""))
            for r in (getattr(ws, "resources", None) or [])}
    added, skipped = [], 0
    import uuid as _uuid

    from pathlib import Path as _P

    for it in (new_items or []):
        # ⚠️ 事件里的 `file` 是**收件目录内**的相对路径，`dir` 是绝对目录 ⇒ 登记时给**绝对路径**，
        #   否则资源库的 `exists` 会把它拼到 workspace_dir 下（假红）。这是"一处真相"：路径就在事件里。
        _dir = str((it or {}).get("dir") or "").strip()
        _file = str((it or {}).get("file") or "").strip()
        sha = str((it or {}).get("sha256") or "")
        slot = str((it or {}).get("slot_dir") or "")
        if not _file:
            continue
        full = str(_P(_dir) / _file) if _dir else _file
        key = (full, sha)
        if key in have:
            skipped += 1
            continue
        ws.resources.append({
            "rid": _uuid.uuid4().hex[:8],
            "name": _file.split("/")[-1] or _file,
            "kind": "file",
            "path": full,
            "note": "收件目录发现（%s，%s 字节）" % (slot, (it or {}).get("bytes") or 0),
            "uploader": slot,
            "source": "inbox",
            "sha256": sha,
            "slot_dir": slot,
            "found_at": str((it or {}).get("ts") or ""),
        })
        have.add(key)
        added.append(full)
    if added:
        try:
            write_resources_manifest(ws)
        except Exception as e:                                 # noqa: BLE001 — 清单写不进去要说，但资源已进内存
            return {"ok": bool(added), "added": added, "skipped": skipped,
                    "warning": "资源已登记，但清单文件没写成：%s: %s" % (type(e).__name__, e)}
    return {"ok": True, "added": added, "skipped": skipped,
            "note": ("登记进资源库（`source=inbox`，带 sha256）；同 path+sha256 不重复登记。"
                     "`GET …/resources` 每条都有 `exists` 真核对")}


@router.get("/api/workshop/{ws_id}/inbox-events")
async def inbox_events(ws_id: str, limit: int = 50):
    """只读：最近的事件（倒序）。事件是**平台自己扫盘得到的**（`source=platform_observed`）。"""
    from fastapi.responses import JSONResponse

    if _ws(ws_id) is None:
        return JSONResponse({"ok": False, "code": "NO_SUCH_WORKSHOP",
                             "error": "没有这个工作间：%s" % ws_id}, status_code=404)
    items = [dict(x) for x in inbox.events(limit=limit)
             if str(x.get("workshop_id") or "") == ws_id]
    # ★2026-11-08（V134 ⑤）：收件事件与"任务 → 验收"原来是两套口径（事件里只有 sha256，
    #   看不到这条进没进任务、那条任务验收了没）。这里给每条事件**补上账本口径**（只读、不改账本），
    #   于是一处就能看到：文件 → 哪条任务 → 谁验收/什么状态。
    try:
        from .. import task_ledger as _tl

        _by_seq = {int(t.get("task_seq") or 0): t for t in (_tl.list_tasks(ws_id) or [])}
    except Exception:                                          # noqa: BLE001 — 账本读不出来只影响这几列
        _by_seq = {}
    for it in items:
        tid = str(it.get("task_id") or "")
        seq = 0
        if tid.startswith("T-"):
            try:
                seq = int(tid[2:])
            except ValueError:
                seq = 0
        rec = _by_seq.get(seq) or {}
        it["task_seq"] = seq or None
        it["task_state"] = str(rec.get("state") or "")
        it["task_reviewed_by"] = str(rec.get("reviewed_by") or "")
        it["task_reviewed_by_source"] = str(rec.get("reviewed_by_source") or "")
        it["task_reviewed_at"] = str(rec.get("reviewed_at") or "")
        it["task_tier"] = ("已进任务账本" if seq and rec else
                           ("事件带 task_id 但账本里没有（对不上）" if tid and not rec else "未绑任务"))
    return {"ok": True, "workshop_id": ws_id, "count": len(items), "events": items,
            "unread": inbox.unread_of(ws_id),
            "task_fields_note": ("每条事件新增 `task_seq/task_state/task_reviewed_by/..."
                                 "`：**这条文件进了哪条任务、那条验收了没**（只读，账本为准）"),
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
