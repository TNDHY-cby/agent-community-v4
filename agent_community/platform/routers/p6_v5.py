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


def _ctx_for_validate(workshop_id: str) -> dict:
    """给 `ailp.validate` 准备**真实上下文**（`V122` §4.1 定义 1）：已有消息 / 任务号 / 名单成员。

    为什么要它：没有上下文的"引用存在性校验"只能退化成"非空校验"（老板点破的坑）。
    这三样拿不到时**如实降级**（对应项传 `None` = 该项不校验），并在响应里说明，不假装校验过。
    """
    msgs = _read(workshop_id, 100000)
    msg_ids = {str(m.get("msg_id") or "") for m in msgs if m.get("msg_id")}
    task_ids: set = set()
    members: set = set()
    try:
        from .. import task_ledger as _ledger

        for t in _ledger.list_tasks(workshop_id):
            seq = t.get("task_seq")
            if seq is None:
                continue
            task_ids.add("T-%03d" % int(seq))
            task_ids.add(str(int(seq)))
    except Exception as e:                                     # noqa: BLE001 — 账本读不到就不校验任务号
        print("[ailp] 任务账本读不出来（%s: %s）⇒ ACT 的任务号本次不校验" % (type(e).__name__, e),
              flush=True)
        task_ids = None                                         # type: ignore[assignment]
    try:
        from .. import v5_roster as _roster

        for m in _roster.roster(workshop_id).get("members") or []:
            for k in ("slot_dir", "session_label"):
                v = str(m.get(k) or "").strip()
                if v:
                    members.add(v)
            for part in str(m.get("slot_dir") or "").split("/"):
                if part.strip():
                    members.add(part.strip())
    except Exception as e:                                     # noqa: BLE001 — 名单读不到就不校验 owner
        print("[ailp] 名单读不出来（%s: %s）⇒ ACT 的 owner 本次不校验" % (type(e).__name__, e),
              flush=True)
        members = None                                          # type: ignore[assignment]
    else:
        if not members:
            # 名单**还没落** ≠ 名单里没这个人：这时候强校验会把所有 ACT 都拒掉。
            # 如实降级（不校验），别假装校验过 —— 与"失败要说话"同一原则。
            print("[ailp] 本工作间名单还是空的 ⇒ ACT 的 owner 本次不校验（等名单落库后再验）",
                  flush=True)
            members = None                                      # type: ignore[assignment]
    return {"msg_ids": msg_ids, "task_ids": task_ids, "members": members}


def _find_duplicate(workshop_id: str, norm: dict) -> dict | None:
    """查重（`V122` §4.1 定义 2）：`sender + msg_type + target + fingerprint` 四者同 ⇒ 判重。

    ⚠️ **只比 fingerprint 会误杀**（相似但合法的重复发言），所以四元组一起比。
    命中时**返回已有的 msg_id**（幂等），不报错 —— 客户端重试才是安全的。
    """
    for it in _read(workshop_id, 100000):
        if (str(it.get("sender")) == norm["sender"]
                and str(it.get("msg_type")) == norm["msg_type"]
                and str(it.get("target")) == norm["target"]
                and str(it.get("fingerprint")) == norm["fingerprint"]):
            return it
    return None


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
    ok, errors, norm = _ailp.validate(body, allowed=allowed, workshop_id=workshop_id,
                                      **_ctx_for_validate(workshop_id))
    if not ok:
        return JSONResponse({"ok": False, "code": "AILP_INVALID",
                             "error": "消息不合格（一条都没收）", "errors": errors,
                             "next": errors[0]["fix"] if errors else "看 /api/ailp/types"},
                            status_code=422)
    # 查重（`V122` §4.1 定义 2）：同 sender+type+target+fingerprint ⇒ 幂等返回已有 msg_id，**不报错**
    dup = _find_duplicate(workshop_id, norm)
    if dup is not None:
        return {"ok": True, "duplicate": True, "msg_id": dup.get("msg_id"),
                "msg_type": dup.get("msg_type"), "manifest_ref": dup.get("manifest_ref"),
                "recorded_at": dup.get("received_at"),
                "note": "这条已经收过了（按 sender+类型+target+fingerprint 判重，账本没有重复入账）"}
    p = _log_path(workshop_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(norm, ensure_ascii=False) + "\n")
    # ★2026-11-07（V138 §二 第一件）：点了名的消息要**真通知到**（不是只存进流水）
    notified = {}
    to_whom = str(norm.get("to") or "")
    ws_path = str(getattr(ws, "workspace_dir", "") or "")
    if to_whom and ws_path:
        from ..dispatch import append_notice as _append_notice

        slot = "_lead" if to_whom in ("lead", "all") else to_whom.split(":", 1)[1]
        line = "[AILP %s] %s → %s：%s" % (norm.get("msg_type"), norm.get("sender"),
                                          to_whom, str(norm.get("manifest_ref") or ""))
        try:
            notified = dict(_append_notice(ws_path, slot, line) or {})
            notified["slot"] = slot
            try:
                from .. import notice_log as _nl

                _nl.record(workshop_id, target=to_whom, slot=slot, channel="notice_file",
                           result="written" if notified.get("ok") else "failed",
                           detail="AILP %s" % norm.get("msg_type"),
                           ref=norm.get("msg_id") or "")
            except Exception as _e:                            # noqa: BLE001 — 记账失败不影响消息
                print("[notice] 记通知账失败（不影响消息）: %s" % _e, flush=True)
        except Exception as e:                                 # noqa: BLE001 — 通知写不进去不能把消息吞了
            notified = {"ok": False, "error": "通知没写进去：%s: %s" % (type(e).__name__, e),
                        "slot": slot,
                        "note": "消息本身**已经记下了**（这条不会丢），只是对方没被点名提醒"}
    return {"ok": True, "msg_id": norm["msg_id"], "msg_type": norm["msg_type"],
            "manifest_ref": norm["manifest_ref"], "recorded_at": norm["received_at"],
            "to": norm.get("to") or "", "thread": norm.get("thread") or "",
            "notified": notified,
            "note": "平台只记了这条结构化消息，没有替任何人说话",
            "next": "同一条重复发按 sender+类型+target+fingerprint 判重（幂等返回已有 msg_id）"}


@router.get("/api/workshop/{workshop_id}/ailp")
async def discuss_list(workshop_id: str, limit: int = 50):
    """读讨论：按时间正序给最近若干条（结构化，前端按 `msg_type` 渲染卡片）。

    另给 `unread`（`V138`：与大厅同一套 `read_marks`，键 `ailp:<ws>`，只前进不后退）。
    """
    ws = _ws(workshop_id)
    if ws is None:
        return _no_ws(workshop_id)
    items = _read(workshop_id, limit)
    counts = {}
    for it in items:
        counts[it.get("msg_type", "?")] = counts.get(it.get("msg_type", "?"), 0) + 1
    from .. import read_marks as _rm

    total = len(_read(workshop_id, 100000))
    rid = "ailp:%s" % workshop_id
    seq = int((_rm.get(rid) or {}).get("seq") or 0)
    unread = max(0, total - max(0, seq))
    return {"ok": True, "count": len(items), "counts": counts, "items": items,
            "unread": {"total": total, "read": max(0, seq), "unread": unread},
            "hint": "这里是**结构化消息**（四类），不是聊天记录；"
                    "平台没有生成任何内容，也没做摘要"}


@router.post("/api/workshop/{workshop_id}/ailp/read")
async def discuss_read(workshop_id: str, request: Request):
    """**把讨论区标为已读**（`V138`）：水位与大厅同一套（`read_marks`，只前进不后退）。

    body 可给 `{seq: N}`（用户看到第 N 条）；不给就推进到"当前总条数"。
    往回设会被拒并说明原因（`moved=false`），不报错 —— 与大厅未读口径一致。
    """
    ws = _ws(workshop_id)
    if ws is None:
        return _no_ws(workshop_id)
    try:
        body = await request.json()
    except Exception:                                          # noqa: BLE001 — 空 body 就按"推到最新"
        body = {}
    body = body if isinstance(body, dict) else {}
    from .. import read_marks as _rm

    total = len(_read(workshop_id, 100000))
    rid = "ailp:%s" % workshop_id
    want = body.get("seq")
    res = _rm.mark(rid, total if want is None else want, only_forward=True)
    seq = int((res or {}).get("seq") or 0)
    return {"ok": True, "workshop_id": workshop_id, "moved": bool((res or {}).get("moved")),
            "why": (res or {}).get("why") or "",
            "unread": {"total": total, "read": max(0, seq), "unread": max(0, total - max(0, seq))}}


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
