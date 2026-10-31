# -*- coding: utf-8 -*-
"""`routers/p6_v5`：**结构化讨论区**（AILP 2.0），`V117` §7-5 / P6 批。

| 端点 | 干什么 |
|---|---|
| `GET  /api/ailp/types` | 四类消息的字段表（前端/agent 都照它写） |
| `POST /api/workshop/{id}/ailp` | 发一条结构化消息（**自由聊天一律 422**）<br>⚠️ 与 v4 的 `/discuss`（三级讨论区、收 `{text}`）**不是同一件事** |
| `GET  /api/workshop/{id}/ailp?limit=` | 读讨论（结构化，按时间正序） |

**平台只做三件事**：校验强类型、`manifest_ref` 对账、记账。
**不生成任何内容**、不代替谁说话、不做摘要（那是审忆员的事，且要人拍板）。
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from .. import ailp as _ailp
from .. import manifest as _mf

router = APIRouter()


def _ws(workshop_id: str):
    from ..server import workshops

    return workshops.get(workshop_id)


def _no_ws(workshop_id: str):
    return JSONResponse({"ok": False, "code": "NO_SUCH_WORKSHOP",
                         "error": "没有这个工作间：%s" % workshop_id,
                         "next": "GET /api/workshops 看有哪些"}, status_code=404)


def _log_path(workshop_id: str) -> Path:
    from ..env_spec import data_dir

    safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in workshop_id)[:60]
    return Path(data_dir()) / "discuss" / (safe + ".jsonl")


def _read(workshop_id: str, limit: int = 50) -> list:
    p = _log_path(workshop_id)
    if not p.is_file():
        return []
    out = []
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except ValueError:                                     # 坏行跳过，不影响别的行
            continue
    return out[-max(1, int(limit or 50)):]


@router.get("/api/ailp/types")
async def ailp_types():
    """四类消息的字段表（**唯一真源**：前端渲染卡片、agent 组包都照它）。"""
    cat = _ailp.types_catalog()
    return {"ok": True, **cat,
            "hint": "平台只校验与记账，不生成内容；界面要把这四类渲染成卡片，别直接甩 JSON"}


@router.post("/api/workshop/{workshop_id}/ailp")
async def discuss(workshop_id: str, request: Request):
    """发一条 AILP 2.0 消息。body 就是信封本身（见 `/api/ailp/types`）。"""
    ws = _ws(workshop_id)
    if ws is None:
        return _no_ws(workshop_id)
    try:
        body = await request.json()
    except Exception:                                          # noqa: BLE001 — 不是 JSON 就直接拒
        return JSONResponse({"ok": False, "code": "A001",
                             "error": "讨论区只收 JSON 信封（自由聊天不收）",
                             "next": "GET /api/ailp/types 看四类消息怎么组"}, status_code=422)
    wp = str(getattr(ws, "workspace_dir", "") or "")
    ok_m, mf_body, _errs = _mf.read(wp) if wp else (False, None, [])
    allowed = _ailp.allowed_refs(mf_body) if ok_m else []
    if not ok_m:
        return JSONResponse({"ok": False, "code": "GATE_G0",
                             "error": "这个工作间还没有合法 manifest，讨论区不能对账",
                             "next": "先 POST /api/workshop/{id}/manifest 写一份"}, status_code=409)
    ok, errors, norm = _ailp.validate(body, allowed=allowed, workshop_id=workshop_id)
    if not ok:
        return JSONResponse({"ok": False, "code": "AILP_INVALID",
                             "error": "消息不合格（一条都没收）", "errors": errors,
                             "next": errors[0]["fix"] if errors else "看 /api/ailp/types"},
                            status_code=422)
    p = _log_path(workshop_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(norm, ensure_ascii=False) + "\n")
    return {"ok": True, "msg_id": norm["msg_id"], "msg_type": norm["msg_type"],
            "manifest_ref": norm["manifest_ref"], "recorded_at": norm["received_at"],
            "note": "平台只记了这条结构化消息，没有替任何人说话",
            "next": "同一条重复发按 msg_id 判重（POST …/ailp/replay）"}


@router.get("/api/workshop/{workshop_id}/ailp")
async def discuss_list(workshop_id: str, limit: int = 50):
    """读讨论：按时间正序给最近若干条（结构化，前端按 `msg_type` 渲染卡片）。"""
    ws = _ws(workshop_id)
    if ws is None:
        return _no_ws(workshop_id)
    items = _read(workshop_id, limit)
    counts = {}
    for it in items:
        counts[it.get("msg_type", "?")] = counts.get(it.get("msg_type", "?"), 0) + 1
    return {"ok": True, "count": len(items), "counts": counts, "items": items,
            "hint": "这里是**结构化消息**（四类），不是聊天记录；"
                    "平台没有生成任何内容，也没做摘要"}


@router.post("/api/workshop/{workshop_id}/ailp/replay")
async def discuss_replay(workshop_id: str, request: Request):
    """把一条旧消息按原样再交一次：**fingerprint 相同 ⇒ 幂等**，不重复入账。"""
    ws = _ws(workshop_id)
    if ws is None:
        return _no_ws(workshop_id)
    try:
        body = await request.json()
    except Exception:                                          # noqa: BLE001
        body = {}
    body = body if isinstance(body, dict) else {}
    msg_id = str(body.get("msg_id") or "")
    for it in _read(workshop_id, 2000):
        if it.get("msg_id") == msg_id:
            return {"ok": True, "duplicate": True, "msg_id": msg_id,
                    "note": "这条已经收过了（按 msg_id 判重）"}
    return JSONResponse({"ok": False, "code": "NO_SUCH_MESSAGE",
                         "error": "没有这条消息：%s" % msg_id,
                         "next": "GET /api/workshop/{id}/ailp 看现存消息"}, status_code=404)
