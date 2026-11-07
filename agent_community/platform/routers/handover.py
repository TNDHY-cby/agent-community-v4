# -*- coding: utf-8 -*-
"""platform/routers/handover：交接体系的 HTTP 面（2026-10-07）。

对应《防幻觉纪律与修复方案_20261007.md》§四 的两件配套物：
  ① 证据栏（无证据不回执） ② 送达回执协议（无对方可读回复不算送达）

三个只读/校验端点：
  GET  /api/handover/template        取任务书/回执模板（带证据栏）
  POST /api/handover/receipt         提交回执——**无证据一律 400 并逐条报错**
  GET  /api/handover/receipts        查已受理的回执
"""
from __future__ import annotations

from fastapi import APIRouter, Request

from ..handover import (
    CHECKPOINT_REQUIRED,
    aging_tracker,
    build_resume_prompt,
    handover_ledger,
    template_receipt,
    template_task_order,
    validate_checkpoint,
    validate_receipt,
)

router = APIRouter()


def _json_err(msg, code=400):
    from ..server import Utf8JSONResponse
    return Utf8JSONResponse({"error": msg}, status_code=code)


@router.get("/api/handover/template")
async def handover_template(kind: str = "task_order", task_id: str = "", title: str = ""):
    """取模板。`kind=task_order`（任务书）或 `kind=receipt`（回执）。

    模板里的每一项都带**空的证据栏**（commit/file/test），领取方必须逐项补齐才能回执。
    """
    kind = (kind or "task_order").strip().lower()
    if kind == "receipt":
        # ⚠️ 2026-10-07 示例Harness-F 实测报回：这里原来没把 title 传下去，于是
        # `?kind=receipt&title=X` **忽略 title**（回显空串），而 kind=task_order 正常。
        return {"success": True, "template": template_receipt(task_id=task_id, who="", title=title)}
    return {"success": True, "template": template_task_order(task_id=task_id, title=title)}


@router.post("/api/handover/receipt")
async def handover_receipt(request: Request):
    """提交回执。**没有证据就拒收**（这正是「无证据不回执」的落点）。

    body:
      {
        "task_id": "...", "who": "示例Harness-E",
        "items": [{"claim": "做了什么", "evidence": {"commit": "76964df", "file": "", "test": "752 passed"}}],
        "delivery": {"delivered": true, "reply_ref": "对方可读回复的凭据", "note": ""}
      }
    通过 → 200 + 受理的回执；不通过 → 400 + 逐条错误（errors 数组）。
    """
    try:
        body = await request.json()
    except Exception:
        return _json_err("body 必须为 JSON")
    ok, errors, normalized = validate_receipt(body)
    if not ok:
        return _err_with(errors)
    rec = handover_ledger.add(normalized)
    return {"success": True, "accepted": True, "receipt": rec,
            "note": "已受理。送达状态由 delivery.delivered + reply_ref 决定。"}


def _err_with(errors: list):
    from ..server import Utf8JSONResponse
    return Utf8JSONResponse(
        {"error": "回执不合规", "errors": errors,
         "hint": "每项完成声明至少带一种证据（commit/file/test）；delivered=True 必须给 reply_ref。"},
        status_code=400,
    )


@router.get("/api/handover/receipts")
async def handover_receipts(limit: str = "100", task_id: str = ""):
    """查已受理的回执（倒序）。"""
    try:
        _lim = max(1, min(500, int(limit)))
    except (TypeError, ValueError):
        _lim = 100
    return {"receipts": handover_ledger.query(limit=_lim, task_id=task_id or None)}


# ── V-24 上下文老化 + 交接单（D7）──────────────────────────────────
# 借 OpenAI Agents SDK 的 memory consolidation turns / durable checkpointing、
# Google ADK 的 resume 重跑语义、CrewAI 的 RuntimeState 序列化口径。


@router.post("/api/handover/compaction")
async def handover_compaction(request: Request):
    """记一次上下文压缩。到阈值时 `needs_checkpoint=True`——此时**不该开新会话**。"""
    try:
        body = await request.json()
    except Exception:
        return _json_err("body 必须为 JSON")
    out = aging_tracker.note_compaction(str(body.get("session_key") or ""))
    if not out.get("ok"):
        return _json_err(out.get("error") or "参数错误")
    return {"success": True, **out}


@router.get("/api/handover/aging")
async def handover_aging(session_key: str = ""):
    """查某会话的老化状态与「能不能开新会话」。"""
    ok, reason, rec = aging_tracker.can_open_new_session(session_key)
    return {"success": True, "session_key": session_key, "can_open_new_session": ok,
            "reason": reason, "compactions": rec.get("compactions", 0),
            "checkpoints": rec.get("checkpoints", 0), "threshold": aging_tracker.threshold}


@router.post("/api/handover/checkpoint")
async def handover_checkpoint(request: Request):
    """交一份**交接单**（checkpoint）。校验不过 400 并逐条报错；过了则计数归零。

    body: {agent_id, wake_word, workspace_dir, plan, resume_semantics,
           done: [...], todo: [...], session_key?}
    """
    try:
        body = await request.json()
    except Exception:
        return _json_err("body 必须为 JSON")
    ok, errors, norm = validate_checkpoint(body)
    if not ok:
        from ..server import Utf8JSONResponse
        return Utf8JSONResponse(
            {"error": "交接单不合规", "errors": errors,
             "hint": "必填：" + "/".join(CHECKPOINT_REQUIRED) + "；done 与 todo 不能同时为空。"},
            status_code=400,
        )
    rec = handover_ledger.add({"kind": "checkpoint", **norm})
    ag = aging_tracker.note_checkpoint(norm.get("session_key") or norm.get("agent_id") or "")
    return {"success": True, "accepted": True, "checkpoint": rec, "aging": ag,
            "note": "交接完成，已允许开新会话。"}


@router.get("/api/handover/checkpoints")
async def handover_checkpoints(limit: str = "20", agent_id: str = ""):
    """查最近的交接单。"""
    try:
        _lim = max(1, min(200, int(limit)))
    except (TypeError, ValueError):
        _lim = 20
    rows = [r for r in handover_ledger.query(limit=500) if r.get("kind") == "checkpoint"]
    if agent_id:
        rows = [r for r in rows if r.get("agent_id") == agent_id]
    return {"checkpoints": rows[:_lim]}


@router.post("/api/handover/activate")
async def handover_activate(request: Request):
    """**交接单驱动的新会话拉起**（D7 执行端）。

    body: `{"harness_id": "...", "session_key": "..."}`（session_key 省略则用 harness_id）

    两道闸门，顺序固定（这就是「压缩到阈值 → 必须先交交接单 → 才准开新会话」的落点）：
      1. 老化闸门：`can_open_new_session(session_key)` 不通 → **409**，要求先交交接单；
      2. 交接单闸门：该 agent 名下**没有任何交接单** → **400**，同样要求先交。
    通过后：用交接单拼续作提示词 → 走既有 `_dispatch_to_harness` 派发 → 记审计 → 返回派发结果。
    """
    try:
        body = await request.json()
    except Exception:
        return _json_err("body 必须为 JSON")
    hid = str(body.get("harness_id") or "").strip()
    if not hid:
        return _json_err("缺少 harness_id")
    session_key = str(body.get("session_key") or hid).strip()

    ok_gate, reason, rec = aging_tracker.can_open_new_session(session_key)
    if not ok_gate:
        from ..server import Utf8JSONResponse
        return Utf8JSONResponse(
            {"error": "老化闸门未放行", "reason": reason,
             "compactions": rec.get("compactions", 0), "threshold": aging_tracker.threshold,
             "hint": "先 POST /api/handover/checkpoint 交一份交接单"},
            status_code=409,
        )

    cps = [r for r in handover_ledger.query(limit=500)
           if r.get("kind") == "checkpoint" and r.get("agent_id") == hid]
    if not cps:
        return _json_err(f"agent {hid} 名下没有交接单：新会话必须带交接单才能拉起")
    cp = cps[0]

    prompt = build_resume_prompt(cp)
    payload = {
        "type": "activate",
        "kind": "resume",
        "harness_id": hid,
        "wake_word": cp.get("wake_word", ""),
        "workspace_dir": cp.get("workspace_dir", ""),
        "checkpoint_ts": cp.get("ts"),
        "activation_prompt": prompt,
        "message": prompt,
    }
    try:
        from ..server import _dispatch_to_harness
        # ⚠️ `_dispatch_to_harness` 是**同步**函数（server.py:5344，返回 (ok, note) 元组），
        #    全仓其余 20 处调用点都没有 await。第一版我写成 `await` => 线上 500
        #    "object tuple can't be used in 'await' expression"（隔离实例实测抓到）。
        #    当时单测没抓到，因为**我的假函数写成了 async**——假函数的形状错了，
        #    测试就会替真代码背书。已把测试的假函数一并改成同步。
        disp_ok, note = _dispatch_to_harness(hid, payload, kind="activation")
    except Exception as e:  # noqa: BLE001
        return _json_err(f"派发异常: {e}", 500)

    try:
        from ..audit import audit_log
        audit_log.record(
            "handover.activated", actor="system", target=hid,
            detail=f"wake_word={cp.get('wake_word','')} dispatched={bool(disp_ok)}",
            extra={"harness_id": hid, "session_key": session_key,
                   "dispatched": bool(disp_ok), "wake_word": cp.get("wake_word", "")},
        )
    except Exception:  # noqa: BLE001
        pass

    return {"success": bool(disp_ok), "dispatched": bool(disp_ok), "harness_id": hid,
            "wake_word": cp.get("wake_word", ""), "note": note,
            "prompt_preview": prompt[:300]}


# ── ★2026-11-07（V138 §三）**跨间交接单**：A 间 → B 间，带凭据移交，不做跨间自由聊天 ──────────
def _now_iso():
    from datetime import datetime

    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _append(path, line: str):
    from pathlib import Path

    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    head = "" if p.is_file() else "# 跨间交接（只追加）\n\n"
    with p.open("a", encoding="utf-8") as fh:
        fh.write(head + line + "\n")


def _read_lines(path, limit: int = 200):
    from pathlib import Path

    p = Path(path)
    if not p.is_file():
        return []
    try:
        return [x for x in p.read_text(encoding="utf-8").splitlines() if x.startswith("- ")]
    except OSError:
        return []


@router.post("/api/workshop/{ws_id}/handover-to-workshop")
async def handover_to_workshop(ws_id: str, request: Request):
    """**跨间交接单**：把一件事从本工作间移交给另一个工作间的**组长**。

    body：`{target_workshop_id, title, goal?, accept?, due?, note?, proof?}`

    为什么只做"交接单"、不做跨间聊天（`V138` §三）：跨间自由对话会立刻把责任链打散
    （谁答应的、谁认的），与 `V134` ① 的"署名可兑现"冲突。

    **平台只做三件事**：① 校验两间都存在且不是同一个；② 在**两边**各写一条只追加的留痕
    （来源间 `_handover_out.md`、目标间 `_handover_in.md`）并给目标间组长 `_notice.md` 加一行；
    ③ 返回交接单号（可双向查）。**不替目标间决定派给谁**（那是目标间组长的事）。
    """
    from .. import server as _srv

    try:
        body = await request.json()
    except Exception:                                          # noqa: BLE001 — 空 body 会被下面拒
        body = {}
    body = body if isinstance(body, dict) else {}
    target = str(body.get("target_workshop_id") or "").strip()
    title = str(body.get("title") or "").strip()
    if not target or not title:
        return _json_err("要 target_workshop_id 和 title（交接什么）", 400)
    if target == ws_id:
        return _json_err("目标工作间不能是自己（同间协作直接用讨论区/派活，不用交接单）", 400)
    src = (_srv.workshops or {}).get(ws_id)
    dst = (_srv.workshops or {}).get(target)
    if src is None:
        return _json_err("没有这个工作间（来源）：%s" % ws_id, 404)
    if dst is None:
        return _json_err("没有这个工作间（目标）：%s" % target, 404)
    src_dir = str(getattr(src, "workspace_dir", "") or "")
    dst_dir = str(getattr(dst, "workspace_dir", "") or "")
    if not src_dir or not dst_dir:
        return _json_err("工作区目录缺失（来源或目标没设 workshop_path）", 409)
    hid = "H-%s" % (abs(hash((ws_id, target, title, _now_iso()))) % 1000000)
    line = ("- %s | %s → %s | %s | goal=%s | accept=%s | due=%s | proof=%s | note=%s"
            % (hid, ws_id, target, title,
               str(body.get("goal") or ""), str(body.get("accept") or ""),
               str(body.get("due") or ""), str(body.get("proof") or ""),
               str(body.get("note") or "")))
    _append("%s/_handover_out.md" % src_dir, line)
    _append("%s/_handover_in.md" % dst_dir, line)
    from ..dispatch import append_notice as _append_notice

    notified = {}
    try:
        notified = dict(_append_notice(dst_dir, "_lead", "[跨间交接 %s] 来自「%s」：%s"
                                       % (hid, ws_id, title)) or {})
    except Exception as e:                                     # noqa: BLE001 — 通知失败不影响交接单本身
        notified = {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}
    try:
        from ..audit import record as _audit_record

        _audit_record.record("workshop.handover_cross", actor="user", target=ws_id,
                             detail="%s → %s：%s" % (ws_id, target, title),
                             extra={"handover_id": hid, "target": target})
    except Exception:                                          # noqa: BLE001 — 审计写不进去也要把交接单交出去
        pass
    return {"ok": True, "handover_id": hid, "from_workshop": ws_id, "to_workshop": target,
            "title": title, "notified": notified,
            "note": ("两边都留了只追加的痕（来源 `_handover_out.md` / 目标 `_handover_in.md`）；"
                     "目标间组长已收到通知。**平台没有替目标间决定派给谁**。"),
            "next": "目标间组长：GET /api/workshop/%s/handovers 看进来的交接单，再自己派活" % target}


@router.get("/api/workshop/{ws_id}/handovers")
async def list_handovers(ws_id: str, limit: int = 100):
    """双向查：本间**交出去**的（out）与**收进来**的（in）。只读。"""
    from .. import server as _srv

    ws = (_srv.workshops or {}).get(ws_id)
    if ws is None:
        return _json_err("没有这个工作间：%s" % ws_id, 404)
    d = str(getattr(ws, "workspace_dir", "") or "")
    out = _read_lines("%s/_handover_out.md" % d, limit)
    inn = _read_lines("%s/_handover_in.md" % d, limit)
    return {"ok": True, "workshop_id": ws_id,
            "out": out[-int(limit or 100):], "in": inn[-int(limit or 100):],
            "count": {"out": len(out), "in": len(inn)},
            "note": ("跨间只走交接单（带内容/验收要点/凭据），**不走自由聊天** —— "
                     "跨间闲聊会把责任链打散（V138 §三）")}
