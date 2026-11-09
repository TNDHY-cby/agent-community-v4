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

from .. import error_semantics as _esem   # 错误码语义（旧 A4）


def _is_lead_member(workshop_id: str, member: str) -> bool:
    """`member` 是不是这个工作间的**组长**（名单里 `slot_dir == "_lead"`）。"""
    want = str(member or "").strip().strip("/")
    if not want:
        return False
    try:
        for m in (_roster.effective_members(workshop_id).get("members") or []):
            if str((m or {}).get("slot_dir") or "").strip().strip("/") == want:
                return want == "_lead"
    except Exception as e:                                     # noqa: BLE001 — 名单读不到就按"不是组长"（fail-closed）
        print("[p4] 读名单失败（%s: %s）；本次按'不是组长'处理" % (type(e).__name__, e), flush=True)
    return False


def _resolve_reviewer(workshop_id: str, body: dict, request) -> tuple:
    """**验收署名必须能兑现**（`V134` ①）：返回 `(who, who_source, err)`。

    三种来源，各自留证（`V134` §① 原话：不许靠请求体默认值冒充组长）：

    | `who_source` | 要求 | 记成 |
    |---|---|---|
    | 不填 / `user` | 无 | `who="user"`（界面/人点的，**不冒充组长**） |
    | `platform` | 无（平台自动出结论，人复核） | `who="platform-review"` |
    | `token` | 必须带组长 A 档令牌（`Authorization: Bearer` 或 `X-Agent-Token`），且令牌属于本工作间的 `_lead` | `who=<令牌里的成员>`、`who_source="token"` |

    声称自己是某个具名身份（如 `who="lead"`）却拿不出令牌 ⇒ **403 `WHO_NOT_PROVEN`**，不落账。
    """
    who_raw = str(body.get("who") or "").strip()
    src = str(body.get("who_source") or "").strip().lower()
    # ★先分清"来源"，再看"名字"：来源没写就当 `user`（人点的）；声称具名身份则必须给凭据。
    #   （首版把"没写 who"也当 user，于是 `who_source=token` 被吞掉、令牌白给 —— 实测抓到的 bug）
    if src in ("", "user"):
        if who_raw and who_raw != "user":
            return "", "", {
                "ok": False, "code": "WHO_NOT_PROVEN",
                "error": ("验收署名写成了「%s」，但没给凭据 —— 平台不替你记成某个身份"
                          "（`V134` ①：验收责任链必须是可兑现的）" % who_raw),
                "next": ("人点的验收就别填 who（默认 user）；要让组长自己验收，"
                         "给它签一份 A 档令牌，并在请求里带 `Authorization: Bearer <token>` + `who_source: token`"),
            }
        return "user", "user", None
    if src == "platform":
        return "platform-review", "platform", None
    if src != "token":
        return "", "", {
            "ok": False, "code": "BAD_WHO_SOURCE",
            "error": "不认识的验收署名来源：%r" % src,
            "next": "who_source 只认 user / token / platform；不填就当 user（人点的）",
        }
    # 走到这里：who_source=token —— 必须能用令牌证明"我是这个工作间的组长"
    auth = str(request.headers.get("authorization") or "")
    bearer = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
    token = bearer or str(request.headers.get("x-agent-token") or body.get("token") or "").strip()
    pair, why = _tok.verify(token)
    if pair is None:
        return "", "", {
            "ok": False, "code": why or "TOKEN_BAD",
            "error": "用 `who_source=token` 验收，但令牌校验没过（%s）" % (why or "TOKEN_BAD"),
            "next": "用对接单里那份组长令牌；吊销过/换过工作间都会失效",
        }
    wid_tok, member = pair
    if str(wid_tok) != str(workshop_id):
        return "", "", {
            "ok": False, "code": "TOKEN_SCOPE",
            "error": "这份令牌不属于这个工作间（它属于 %s）" % wid_tok,
            "next": "用本工作间组长的那份令牌",
        }
    if not _is_lead_member(workshop_id, member):
        return "", "", {
            "ok": False, "code": "NOT_LEAD",
            "error": "令牌有效，但 %r 不是这个工作间的组长（名单里没有 `_lead` 工位对得上）" % member,
            "next": "组长验收要用 `_lead` 工位那份令牌；成员验收不算组长的结论",
        }
    return str(member or who_raw), "token", None


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
    # ★2026-11-07（V134 ①）：验收署名必须能兑现 —— 不再默认 "lead"（那是"谁点一下都算组长"）
    who, who_source, _who_err = _resolve_reviewer(workshop_id, body, request)
    if _who_err is not None:
        return JSONResponse(_who_err, status_code=403)
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
                         artifacts=arts_summary, who=who, who_source=who_source)
    out["reviewed_by"] = who
    out["reviewed_by_source"] = who_source
    view = _evidence_view(workshop_id, task_seq,
                          given_level=str(body.get("evidence_level") or ""),
                          snapshot=scanned if scanned is not None else None)
    if out.get("ok"):
        out["artifacts"] = arts_summary
        out["evidence_view"] = view
        # ★2026-11-08：验收结果**必须通知到他** —— 以前只改账本，成员一个字都收不到
        #   （组长点了"返工"，他还以为没事）。写他的 `_notice.md` + 记通知账；失败如实带回。
        try:
            from .. import review_notice as _rn

            out["notify"] = _rn.notify(workshop_id, ws, dict(rec or {},
                                                             task_seq=task_seq,
                                                             task_id="T-%03d" % task_seq),
                                       result=result, note=str(body.get("note") or ""), who=who)
        except Exception as e:                                 # noqa: BLE001 — 通知失败不能让验收结果丢
            out["notify"] = {"ok": False, "written": False,
                             "why": "通知成员时出错：%s: %s" % (type(e).__name__, e)}
        return out
    merged = _ev.merge_into_failure(out, view)
    return JSONResponse(_esem.annotate(merged),
                        status_code=409 if out.get("code") == "NO_SUCH_TASK" else 400)


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

@router.get("/api/error-semantics")
async def error_semantics_table():
    """**错误码语义表**（借鉴竞品 A4）：哪些该重试、哪些该停手、哪些是幂等命中。

    调用方拿到错误先看 `retryable`：`true` ⇒ 稍后重试；`false` ⇒ 停手，按 `next` 那句人话改。
    **不要对 409 一律重试**（会形成重试风暴）。
    """
    return _esem.table()
