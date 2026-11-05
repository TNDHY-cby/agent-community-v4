# -*- coding: utf-8 -*-
"""`routers/p4_v5`：**目录校验 · 产物清单 · 验收 · 归档**（`V113` 第 7/11 步，P4 批）。

| 端点 | 干什么 |
|---|---|
| `GET  /api/workshop/{id}/verify-dirs` | `stat` 校验目录/分工/notice（缺什么报什么，**不代建**） |
| `POST /api/workshop/{id}/verify-artifacts` | 扫 `产物/` 出清单（**目录 diff，L0**），写 `_meta/artifacts.json` |
| `POST /api/workshop/{id}/tasks/{task_seq}/review` | 验收：通过/返工/拒收（**通过必须 ≥L1 证据**，L3 一律拒） |
| `POST /api/workshop/{id}/integrate-report` | 汇总写 `_meta/integrate.json`（**不搬产物**）<br>⚠️ 与 v4 的 `/integrate`（阶段整合、会派整合者）**不是同一件事** |
| `POST /api/workshop/{id}/close` | 归档：置 `closed` + 吊销全部 A 档 token |
| `GET  /api/workshop/{id}/tasks` | 任务账本（每条的状态/证据等级/验收备注） |
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from .. import artifacts as _arts
from .. import intake_token as _tok
from .. import receipt_store as _receipt
from .. import task_ledger as _ledger
from .. import v5_evidence_view as _ev
from .. import v5_roster as _roster
from .. import verify as _verify

router = APIRouter()


def _ws(workshop_id: str):
    from ..server import workshops

    return workshops.get(workshop_id)


def _no_ws(workshop_id: str):
    return JSONResponse({"ok": False, "code": "NO_SUCH_WORKSHOP",
                         "error": "没有这个工作间：%s" % workshop_id,
                         "next": "GET /api/workshops 看有哪些"}, status_code=404)


def _slots(workshop_id: str) -> list:
    r = _roster.roster(workshop_id)
    out = []
    for m in r["members"]:
        out.append({"slot_dir": m.get("slot_dir"), "harness_id": m.get("harness_id"),
                    "member_seq": m.get("member_seq")})
    return out


def _evidence_view(workshop_id: str, task_seq: int, *, given_level: str = "",
                   snapshot: dict | None = None) -> dict:
    """拼四步表（**只读**：不扫盘、不写 `_meta`、不改账本）。POST 失败态与 GET 视图共用这一份。"""
    ws = _ws(workshop_id)
    wp = str(getattr(ws, "workspace_dir", "") or "") if ws else ""
    rec = _ledger.get(workshop_id, task_seq)
    slot_dir = str((rec or {}).get("slot_dir") or "")
    if snapshot is None:
        snapshot = _arts.load_previous(wp) if (wp and slot_dir) else None
    receipts: list = []
    try:
        receipts = _receipt.recent(workshop_id, limit=5)
    except Exception as e:                                     # noqa: BLE001 — 读不到回执要说，但不该毁掉整个视图
        print("[p4] 回执流水读不出来（%s: %s）；四步表第 3 步按「没有回执」显示" % (type(e).__name__, e),
              flush=True)
    return _ev.build_view(task_seq=task_seq, rec=rec, slot_dir=slot_dir,
                          snapshot=snapshot, receipts=receipts, given_level=given_level)


@router.get("/api/workshop/{workshop_id}/verify-dirs")
async def verify_dirs(workshop_id: str):
    """只 `stat`：组目录 / `分工.md`（含版本行）/ `_notice.md` / 成员 `任务`·`产物`。"""
    ws = _ws(workshop_id)
    if ws is None:
        return _no_ws(workshop_id)
    wp = str(getattr(ws, "workspace_dir", "") or "")
    out = _verify.verify_dirs(wp, _slots(workshop_id))
    return out


@router.get("/api/workshop/{workshop_id}/artifacts")
async def artifacts_readonly(workshop_id: str):
    """**只读**上次扫出来的产物清单（`_meta/artifacts.json`）：不重扫、不写盘。

    为什么要它：外部协议（A2A 的 `artifacts`、MCP 工具）要能读到"平台认定过的产物"，
    而「扫」是有副作用的（会重写快照）。读取就该只读；没扫过就如实说空。
    """
    ws = _ws(workshop_id)
    if ws is None:
        return _no_ws(workshop_id)
    wp = str(getattr(ws, "workspace_dir", "") or "")
    prev = _arts.load_previous(wp) if wp else None
    if not prev:
        return {"ok": True, "scanned_at": None, "total": 0, "slots": {},
                "note": "还没扫过产物（先 POST …/verify-artifacts）；这里不代扫，避免读一次就改盘"}
    return {"ok": True, "scanned_at": prev.get("scanned_at"), "total": prev.get("total", 0),
            "slots": prev.get("slots") or {},
            "note": "只读上次快照；要最新就再扫一次（扫会写盘，所以不放 GET 里）"}


@router.post("/api/workshop/{workshop_id}/verify-artifacts")
async def verify_artifacts(workshop_id: str, request: Request):
    """扫 `产物/` 并与上次快照 diff（L0）。body 可选 `{slot_dirs: [...]}` 限定范围。"""
    ws = _ws(workshop_id)
    if ws is None:
        return _no_ws(workshop_id)
    try:
        body = await request.json()
    except Exception:                                          # noqa: BLE001 — 不传就全扫
        body = {}
    body = body if isinstance(body, dict) else {}
    wp = str(getattr(ws, "workspace_dir", "") or "")
    slots = [str(x) for x in (body.get("slot_dirs") or [])] or \
            [s["slot_dir"] for s in _slots(workshop_id) if s.get("slot_dir")]
    if not slots:
        return JSONResponse({"ok": False, "code": "NO_SLOTS",
                             "error": "这个工作间还没有 v5 工位（没有可扫的产物目录）",
                             "next": "先落名单（回执 类型: 名单）再扫"}, status_code=400)
    out = _arts.scan(wp, slots)
    out["ok"] = True
    return out


@router.get("/api/workshop/{workshop_id}/tasks/{task_seq}/evidence-view")
async def evidence_view(workshop_id: str, task_seq: int, level: str = ""):
    """**只读**「证据四步表」：用户点「通过」之前先看清缺什么、该谁补、补完点哪儿（`V121`）。

    只拿**上次产物快照** + 账本现状 + 回执流水；**不扫盘、不写 `_meta`、不改账本**
    （与 `GET …/artifacts` 同一原则：看的时候不许改盘）。

    `?level=L0|L1|L2|L4`：预演"我打算按这一档验收，会卡在哪"（不传就只看事实，`blocking` 按"还差人拍板"算）。
    """
    ws = _ws(workshop_id)
    if ws is None:
        return _no_ws(workshop_id)
    view = _evidence_view(workshop_id, task_seq, given_level=level)
    view["ok"] = True
    view["given_level"] = str(level or "").strip().upper()
    view["note"] = ("只读视图：不扫盘、不写 `_meta`、不改账本；要看最新产物先 `POST …/verify-artifacts`"
                    "（点「通过」时平台会自动扫一次）")
    return view


@router.post("/api/workshop/{workshop_id}/tasks/{task_seq}/review")
async def review(workshop_id: str, task_seq: int, request: Request):
    """验收。body：`{result: 通过|返工|拒收, evidence_level, evidence_ref, note, scan?}`。

    `evidence_ref`：`L1` 的**凭据**（哪个库/哪个会话/谁核的/用什么工具核的）——`L1` 不带凭据会被拒（`L1_NEEDS_PROOF`）。
    失败时响应里追加「证据四步表」（`steps/evidence_have/blocking/who_should_fix`），老的 `code/error/next` 一个都不删。
    """
    ws = _ws(workshop_id)
    if ws is None:
        return _no_ws(workshop_id)
    try:
        body = await request.json()
    except Exception:                                          # noqa: BLE001
        body = {}
    body = body if isinstance(body, dict) else {}
    result = str(body.get("result") or "").strip()
    if result not in ("通过", "返工", "拒收"):
        return JSONResponse({"ok": False, "code": "BAD_REQUEST",
                             "error": "result 只能是 通过 / 返工 / 拒收",
                             "next": "按实际结论填；拿不准先选「返工」并写备注"}, status_code=400)
    wp = str(getattr(ws, "workspace_dir", "") or "")
    rec = _ledger.get(workshop_id, task_seq)
    # ★V130 §4 规则二：`L1` 的凭据若**像本机路径**就真 stat 一次（读不到 ⇒ 不算 L1，按 L4 算）。
    #   不像路径的（"哪个库/哪个会话/谁核的"）不 stat —— 平台本来核对不了这类，如实标"人工凭据"。
    ref = str(body.get("evidence_ref") or "").strip()
    proof_exists = None
    if ref and _ev.proof_looks_like_path(ref):
        p = Path(ref)
        if not p.is_absolute() and wp:
            p = Path(wp) / ref
        try:
            proof_exists = p.exists()
        except OSError as e:                                   # noqa: BLE001 — 探不到 ≠ 不存在，如实留 None
            print("[p4] 凭据路径探测失败（%s: %s）；按「平台未核对」处理" % (type(e).__name__, e),
                  flush=True)
            proof_exists = None
    arts_summary = None
    scanned = None
    if rec:
        slot = str(rec.get("slot_dir") or "")
        if body.get("scan", True) and slot and wp:
            scanned = _arts.scan(wp, [slot])
            arts_summary = _arts.summary_for_task(scanned, slot, "T-%03d" % task_seq)
    out = _ledger.review(workshop_id, task_seq, result=result,
                         evidence_level=str(body.get("evidence_level") or ""),
                         evidence_ref=ref,
                         proof_exists=proof_exists,
                         note=str(body.get("note") or ""),
                         artifacts=arts_summary, who=str(body.get("who") or "lead"))
    view = _evidence_view(workshop_id, task_seq,
                          given_level=str(body.get("evidence_level") or ""),
                          snapshot=scanned if scanned is not None else None)
    if out.get("ok"):
        out["artifacts"] = arts_summary
        out["evidence_view"] = view
        return out
    merged = _ev.merge_into_failure(out, view)
    return JSONResponse(merged, status_code=409 if out.get("code") == "NO_SUCH_TASK" else 400)


@router.post("/api/workshop/{workshop_id}/integrate-report")
async def integrate_report(workshop_id: str, request: Request):
    """汇总写 `_meta/integrate.json`（清单+结论+未交付），**不动产物文件**。

    ⚠️ 与 v4 的 `POST …/integrate`（阶段整合：收产物 + 派整合者）**语义不同**，所以路径分开，
    不要合到一个路径上（2026-10-27 判据抓到过同路径两语义打架）。
    """
    ws = _ws(workshop_id)
    if ws is None:
        return _no_ws(workshop_id)
    try:
        body = await request.json()
    except Exception:                                          # noqa: BLE001
        body = {}
    body = body if isinstance(body, dict) else {}
    wp = str(getattr(ws, "workspace_dir", "") or "")
    if not wp:
        return JSONResponse({"ok": False, "code": "NO_WORKSHOP_PATH",
                             "error": "工作间没有路径", "next": "先设 workshop_path"}, status_code=400)
    out = _ledger.integrate(workshop_id, workshop_path=wp, note=str(body.get("note") or ""))
    return out


@router.post("/api/workshop/{workshop_id}/close")
async def close(workshop_id: str, request: Request):
    """归档：置 `closed` + **吊销全部 A 档 token**（关了就收不回来了）。"""
    ws = _ws(workshop_id)
    if ws is None:
        return _no_ws(workshop_id)
    from ..server import save_state

    revoked = []
    for m in _roster.roster(workshop_id)["members"]:
        r = _tok.revoke(workshop_id, str(m.get("slot_dir") or ""))
        revoked.append(r.get("revoked"))
    try:
        ws.status = "closed"
        save_state()
    except Exception as e:                                     # noqa: BLE001 — 置状态失败要说清
        return JSONResponse({"ok": False, "code": "CLOSE_FAILED",
                             "error": "吊销已完成，但工作间状态没写成 closed（%s: %s）"
                                      % (type(e).__name__, e),
                             "next": "查日志后重试 POST …/close"}, status_code=500)
    return {"ok": True, "status": "closed", "revoked": revoked,
            "hint": "工作区目录**原样保留**（归档不搬文件）；要收起来自己打包"}


@router.get("/api/workshop/{workshop_id}/tasks")
async def list_tasks(workshop_id: str):
    """任务账本：每条的状态 / 证据等级 / 验收备注 / 验收人。"""
    ws = _ws(workshop_id)
    if ws is None:
        return _no_ws(workshop_id)
    tasks = _ledger.list_tasks(workshop_id)
    return {"ok": True, "count": len(tasks), "tasks": tasks,
            "hint": "`state=accepted` 才算验收过；`rework` 要按 `review_note` 改完再交"}
