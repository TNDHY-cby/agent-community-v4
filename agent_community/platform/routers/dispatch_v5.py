# -*- coding: utf-8 -*-
"""`routers/dispatch_v5`：**派活与激活结算**（设计 `V113` 第 10 步 / P3 批）。

| 端点 | 干什么 |
|---|---|
| `POST /api/workshop/{id}/dispatch` | 组长发起派活：写任务文件 → 追加 `_notice` → **平台 append `manifest.active_tasks`** → 三步激活 → 记账 |
| `POST /api/workshop/{id}/wakeup/poll` | B 档结算：标记被取走 ⇒ `ok`；超时 ⇒ `manual`（可配 `AC_WAKEUP_QUEUE_TIMEOUT_SEC`） |
| `GET  /api/workshop/{id}/dispatch/{task_seq}` | 看一条派活的账（文件在不在、notice 写了没、激活到哪一步） |

**为什么 manifest 由平台 append**（架构师 2026-10-27 排雷①）：不组长改 —— 它改坏了 G0 就整间停工。
平台 append 后立刻重算 hash，G0 保持绿通。
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from .. import dispatch as _dp
from .. import manifest as _mf
from .. import v5_gate as _gate
from .. import v5_roster as _roster

router = APIRouter()


def _ws(workshop_id: str):
    from ..server import workshops

    return workshops.get(workshop_id)


def _no_ws(workshop_id: str):
    return JSONResponse({"ok": False, "code": "NO_SUCH_WORKSHOP",
                         "error": "没有这个工作间：%s" % workshop_id,
                         "next": "GET /api/workshops 看有哪些"}, status_code=404)


def _dispatch_log_path() -> Path:
    from ..env_spec import data_dir as _data_dir

    return Path(_data_dir()) / "dispatch_log.jsonl"


def _record(entry: dict) -> None:
    p = _dispatch_log_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _log_recent(workshop_id: str, limit: int = 50) -> list:
    p = _dispatch_log_path()
    if not p.is_file():
        return []
    out = []
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            e = json.loads(line)
        except ValueError:                                     # 坏行跳过
            continue
        if e.get("workshop_id") == workshop_id:
            out.append(e)
    return out[-limit:]


def _steps_from_record(ws, rec: dict) -> dict:
    """从一条派活记录推**同一份**四步表（账本视图与派活返回同源，规则 20）。"""
    from pathlib import Path

    wp = str(getattr(ws, "workspace_dir", "") or "")
    rel = str(rec.get("file") or "")
    file_exists = bool(rel) and (Path(wp) / rel).is_file()
    res = str(rec.get("wakeup_result") or "")
    steps = [
        {"n": 1, "name": "任务文件", "ok": file_exists, "what": rel},
        {"n": 2, "name": "_notice 追加", "ok": bool(rec.get("notice")),
         "what": str(rec.get("notice") or "")},
        {"n": 3, "name": "manifest.active_tasks", "ok": bool(rec.get("manifest_ok")),
         "what": str(rec.get("manifest_hash") or "")},
        {"n": 4, "name": "触达", "ok": res in ("ok", "queued"),
         "what": str(rec.get("wakeup_detail") or ""), "result": res},
    ]
    bad = next((x for x in steps if not x["ok"] and x["n"] <= 3), None)
    manual_required = (res == "manual")
    stuck_at = bad["name"] if bad else ("触达" if res == "failed" else "")
    why, nxt = "", ""
    if stuck_at == "任务文件":
        why, nxt = "任务文件不在（可能被移走/工作区变了）", "重派一次，或把文件放回 `%s`" % rel
    elif stuck_at == "_notice 追加":
        why, nxt = "`_notice.md` 没写上", "看 `<组>/_notice.md` 是否可写"
    elif stuck_at == "manifest.active_tasks":
        why, nxt = "manifest 没登记这条任务（G0 会拦后续派活）", "`POST …/manifest` 修好再重派"
    elif stuck_at == "触达":
        why, nxt = "前三步已落盘，但**触达失败**", "看桥/服务是否在跑，修好后重派或手动贴"
    return {"steps": steps, "stuck_at": stuck_at, "why": why, "next": nxt,
            "manual_required": manual_required,
            "manual_hint": (str(rec.get("wakeup_detail") or "") if manual_required else "")}


def do_dispatch(workshop_id: str, body: dict, *, issued_by: str = "lead",
                source: str = "api") -> dict:
    """派活核心（HTTP 端点与"派活回执"共用同一条路 —— 一处真相）。"""
    ws = _ws(workshop_id)
    if ws is None:
        return {"ok": False, "code": "NO_SUCH_WORKSHOP", "error": "没有这个工作间",
                "next": "GET /api/workshops"}
    wp = str(getattr(ws, "workspace_dir", "") or "")
    if not wp:
        return {"ok": False, "code": "NO_WORKSHOP_PATH", "error": "这个工作间没有工作区目录",
                "next": "先设 workshop_path"}

    seq = int(body.get("member_seq") or 0)
    ok_gate, code, four = _gate.check(ws, "dispatch", {"member_seq": seq})
    if not ok_gate:
        return {"ok": False, "code": code, "error": "派活被闸门拦住", "four": four,
                "next": four.get("how", "")}

    # G0：manifest 必须合法（派活要先有入口地图）
    ok_mf, mfb, errs = _mf.read(wp)
    if not ok_mf:
        return {"ok": False, "code": "GATE_G0", "error": "manifest 读不出来（G0 拦）", "errors": errs,
                "next": "先 POST /api/workshop/{id}/manifest 写一份合法 manifest"}
    ok_v, code_v, errs_v, four_v = _mf.validate(mfb, workshop_path=wp)
    if not ok_v:
        return {"ok": False, "code": code_v, "error": "manifest 不合法（G0 拦）",
                "errors": errs_v, "four": four_v, "next": four_v.get("how", "")}

    m = _roster.find_member(workshop_id, seq)
    slot_dir = str((m or {}).get("slot_dir") or "")
    harness_id = str((m or {}).get("harness_id") or "")
    tier = str((m or {}).get("tier") or "c_visual")

    # ★2026-10-27：允许调用方指定任务号 —— 回执里 `任务: T-002` 就是指定；
    #   另外 `task_id` 形如 `T-002` 也要认（否则平台另发号 ⇒ 找不到组长已写好的正文）。
    _tid = str(body.get("task_id") or "").strip()
    _seq_from_id = 0
    if _tid.upper().startswith("T-"):
        try:
            _seq_from_id = int(_tid[2:])
        except ValueError:
            _seq_from_id = 0
    _explicit_seq = int(body.get("task_seq") or _seq_from_id or 0)
    _no_write = str(body.get("write_file", "1")) in ("0", "false", "False")
    if _explicit_seq:
        task_seq = _explicit_seq
    elif _no_write:
        # ★2026-10-27：正文不进球 ⇒ **号也不许猜**。原来"找任务目录里最大号"会把旧任务
        #   误当成你这条（判据抓到的真问题）。必须显式给 task_seq / task_id。
        return {"ok": False, "code": "TASK_SEQ_REQUIRED",
                "error": "你要求不代写正文，但没说是哪一条（缺 task_seq/task_id）",
                "next": "带上 `task_id: T-003`（或 `task_seq: 3`）再发；或去掉 write_file=0 让平台代写"}
    else:
        task_seq = _dp.next_task_seq(workshop_id)
    task_rel = _dp.task_relpath(slot_dir, task_seq)
    abs_task = Path(wp) / task_rel
    title = str(body.get("title") or "").strip() or ("任务 %d" % task_seq)
    goal = str(body.get("goal") or body.get("description") or "").strip() or title
    accept = str(body.get("accept") or "").strip() or "（未写验收要点 —— 由组长补充）"
    due = str(body.get("due") or "")
    deps = "、".join(str(x) for x in (body.get("deps") or [])) or "无"

    file_created = False
    if not abs_task.is_file():
        if _no_write:
            return {"ok": False, "code": "TASK_FILE_MISSING",
                    "error": "任务文件不存在，且你要求不代写：%s" % task_rel,
                    "next": "让组长先把正文写进这个文件，再发派活（正文不进球）"}
        abs_task.parent.mkdir(parents=True, exist_ok=True)
        abs_task.write_text(_dp.TASK_TEMPLATE.format(
            task_id="T-%03d" % task_seq, title=title, slot_dir=slot_dir,
            issued_by=issued_by, issued_at=_dp._now(), due=due or "（未设）", deps=deps,
            goal=goal, accept=accept), encoding="utf-8")
        file_created = True

    notice = _dp.append_notice(wp, slot_dir,
                               "任务/T-%03d.md | 新任务 | %s" % (task_seq, title[:40]))
    mfres = _dp.append_manifest_task(wp, task_rel)
    # ★2026-11-01（清单 B2）：给对端的**回报契约** —— 它干完自己 POST 回执也行，
    #   平台侧的 `a2a_result_poll` 会按 `tasks/get` 回查；两条路谁先到算谁。
    #   地址取平台运行时地址（不写死；取不到就不写这段，绝不硬编一个错的）。
    _report_hint = ""
    try:
        from ...platform_address import base_url as _rbu  # type: ignore

        _base = str(_rbu() or "").rstrip("/")
    except Exception:                                          # noqa: BLE001 — 拿不到地址就不写
        _base = ""
    if _base:
        _report_hint = (
            "【干完怎么回报】POST %s/api/harness/task-result ，body：\n"
            '{"workshop_id":"%s","member_id":"m%d","harness_id":"%s",'
            '"ok":true,"result":"你的结果或产物清单"}\n'
            "（不回执的话，平台只能按 `tasks/get` 回查你的状态，产物会被记成空。）"
            % (_base, workshop_id, int(seq or 0), harness_id or ""))
    # ★2026-11-01（老板：「注册后变为一个**具体模板**，工作间里**不用连接好的会话**，
    #   而是用**模板 + 工作间实时路径**重新拉人」）：
    #   派活参数一律**现算**（L3），不再用注册时那套固定值 —— 同一个 harness 进多个工作间时，
    #   任务路径 / 工位 / 会话号 / 回报 body 都必须是**这次**的。
    _ctx = ""
    _ctx_info = {}
    _is_a2a = False
    try:
        _is_a2a = _dp._harness_is_a2a(harness_id or "")
    except Exception:                                          # noqa: BLE001 — 判不出来就当非 A2A
        _is_a2a = False
    _l3 = None
    if _is_a2a:
        _explicit = str((body or {}).get("a2a_context_id") or "").strip()
        if _explicit:
            _ctx = _explicit
        else:
            try:
                from ..a2a_context import current_for as _ctx_current

                _ctx_info = _ctx_current(harness_id or "", workshop_id, slot_dir)
                _ctx = str(_ctx_info.get("context_id") or "")
            except Exception as _e:                            # noqa: BLE001 — 取不到就不带（退回每次新开）
                print("[dispatch] 取 A2A 当前会话失败（本条按不带 contextId 发）：%s: %s"
                      % (type(_e).__name__, _e), flush=True)
        # L3：L2 具体模板 + 这次的工作间实时路径 ⇒ 落地参数（端点/协议/会话/回报契约）
        try:
            from ..server import harness_manager as _hm
            from ..bridge_template import render_for_workshop as _render

            _sess = (_hm.sessions or {}).get(harness_id or "")
            if _sess is not None and getattr(_sess, "info", None) is not None:
                _l3 = _render(_sess.info, workshop_id=workshop_id, workshop_dir=str(wp),
                              slot_dir=slot_dir, member_seq=int(seq or 0),
                              task_seq=int(task_seq), task_rel=task_rel, context_id=_ctx)
                _ctx = str(_l3.get("context_id") or _ctx)
        except Exception as _e:                                # noqa: BLE001 — L3 失败就退回手拼
            print("[dispatch] L3 落地参数渲染失败（退回手拼回报契约）：%s: %s"
                  % (type(_e).__name__, _e), flush=True)
    if _l3 and isinstance(_l3.get("report"), dict):
        # 用模板给的回报契约（含本次 workshop_id / member_id），不再手拼
        _rp = _l3["report"]
        _report_hint = ("【干完怎么回报】POST %s ，body：\n%s\n"
                        "（不回执的话，平台只能按 `tasks/get` 回查你的状态，产物会被记成空。）"
                        % (_rp.get("url"), json.dumps(_rp.get("body") or {}, ensure_ascii=False)))
    elif not _report_hint and _base:
        _report_hint = (
            "【干完怎么回报】POST %s/api/harness/task-result ，body：\n"
            '{"workshop_id":"%s","member_id":"m%d","harness_id":"%s",'
            '"ok":true,"result":"你的结果或产物清单"}\n'
            "（不回执的话，平台只能按 `tasks/get` 回查你的状态，产物会被记成空。）"
            % (_base, workshop_id, int(seq or 0), harness_id or ""))
    if _is_a2a and _ctx:
        _report_hint += ("\n\n【如果你觉得上下文已经很长（压缩过几次）】"
                         "回复里带上 `[NEED_NEW_CONTEXT]`，平台下次会给你开一个新会话。")
    wake = _dp.activate(wp, slot_dir, harness_id=harness_id, tier=tier,
                        task_id="T-%03d" % task_seq, task_rel=task_rel,
                        report_hint=_report_hint, a2a_context_id=_ctx)
    # ★2026-11-02（老板：「工作过程中没有回复，让用户以为是掉线了」）：
    #   界面要按 a2a_task_id 去查进度，就得知道**问哪个 harness** —— 这里一并带出去，
    #   免得前端再翻名单自己去猜（那个坑已经踩过：members-status 是旧名单）。
    try:
        if isinstance(wake, dict):
            wake["harness_id"] = str(harness_id or "")
    except Exception:                                          # noqa: BLE001 — 只是补字段，不改结论
        pass
    # 派出去就记一笔用量（平台自己数，**不信对端自报**）—— 到阈值就换新会话
    if _is_a2a and _ctx and not (body or {}).get("a2a_context_id"):
        try:
            from ..a2a_context import note_used as _ctx_note

            _ctx_note(harness_id or "", workshop_id, slot_dir,
                      chars=len(_report_hint or "") + len(str(title or "")))
        except Exception as _e:                                # noqa: BLE001 — 记账失败不挡派活
            print("[dispatch] A2A 会话用量记账失败：%s: %s" % (type(_e).__name__, _e), flush=True)
    # ★2026-11-01（清单 B2）：A2A 派成功后**登记待回写**（后台轮询 GetTask ⇒ 写回账本）
    if str(wake.get("method") or "") == "a2a" and str(wake.get("result") or "") in ("ok", "queued"):
        try:
            from ..a2a_result_poll import add as _a2a_add

            _a2a_add(url=str(wake.get("url") or ""),
                     a2a_task_id=str(wake.get("a2a_task_id") or ""),
                     workshop_id=workshop_id, task_seq=int(task_seq), harness_id=harness_id or "",
                     slot_dir=slot_dir)
        except Exception as _e:                                # noqa: BLE001 — 登记失败要说，但不挡派活
            print("[dispatch] A2A 结果回写登记失败（派活已成功，回写会缺）：%s: %s"
                  % (type(_e).__name__, _e), flush=True)
    entry = {"at": _dp._now(), "workshop_id": workshop_id, "task_seq": task_seq,
             "task_id": "T-%03d" % task_seq, "slot_dir": slot_dir, "member_seq": seq,
             "title": title, "file": task_rel, "file_created": file_created,
             "notice": notice.get("file"), "tier": tier,
             "manifest_ok": bool(mfres.get("ok")), "manifest_hash": mfres.get("manifest_hash"),
             "manifest_already": mfres.get("already"),
             "wakeup_result": wake.get("result"), "wakeup_detail": wake.get("detail"),
             "activated_at": wake.get("at"), "issued_by": issued_by, "source": source}
    # ★P4：派活成功就登记进**任务账本**（一处真相：P3 记账 + P4 状态机同一条记录）
    ledger_rec = None
    if mfres.get("ok"):
        from .. import task_ledger as _ledger

        ledger_rec = _ledger.issue(workshop_id, task_seq, slot_dir=slot_dir,
                                   file=task_rel, member_seq=seq, title=title,
                                   wake=str(wake.get("result") or ""))
    _record(entry)
    # ★2026-10-31：把"三步激活"摊成**显式四步**，并指出卡在哪一步（失败要说话）
    _res = str(wake.get("result") or "")
    steps = [
        # ★2026-10-31：第 1 步看**文件在不在**（平台代写 or 组长已写好都算），不是只看自己写没写
        {"n": 1, "name": "任务文件", "ok": bool(file_created or abs_task.exists()),
         "what": task_rel, "created_by_platform": bool(file_created)},
        {"n": 2, "name": "_notice 追加", "ok": bool(notice.get("ok") or notice.get("file")),
         "what": str(notice.get("file") or notice.get("error") or "")},
        {"n": 3, "name": "manifest.active_tasks", "ok": bool(mfres.get("ok")),
         "what": str(mfres.get("manifest_hash") or mfres.get("error") or "")},
        {"n": 4, "name": "触达", "ok": _res in ("ok", "queued"),
         "what": "%s%s" % (wake.get("method") or "", (" " + str(wake.get("url") or wake.get("flag") or "")) if (wake.get("url") or wake.get("flag")) else ""),
         "result": _res},
    ]
    # ★2026-10-31：**manual ≠ 失败** —— C 档（视觉型）就是人工粘贴这条正常路径。
    #   只有"前三步没成"或"触达 failed（真发不出去）"才算卡住；
    _bad = next((x for x in steps if not x["ok"] and x["n"] <= 3), None)
    manual_required = (_res == "manual")
    stuck_at = _bad["name"] if _bad else ("触达" if _res == "failed" else "")
    if not stuck_at:
        why, nxt = "", ""
    elif stuck_at == "触达":
        why = "三步已落盘，但**触达失败**：%s" % (wake.get("detail") or wake.get("error") or _res)
        nxt = "看 `wakeup.url` 是否可达（桥/服务是否在跑）；修好后重派或手动贴"
    else:
        why = "第 %s 步没成：%s" % (_bad["n"], _bad["what"] or _bad["name"])
        nxt = {"任务文件": "看工作区是否可写（`verify-dirs`）",
               "_notice 追加": "看 `<组>/_notice.md` 是否能写",
               "manifest.active_tasks": "先修 manifest（G0）：`POST /api/workshop/{id}/manifest`"}.get(stuck_at, "")
    out = {"ok": bool(mfres.get("ok") and not _bad),
           "steps": steps, "stuck_at": stuck_at, "why": why, "next": nxt,
           "manual_required": manual_required,
           # manual 时也把"该怎么贴"写清（这是 C 档的正常终态，不是错误）
           "manual_hint": (str(wake.get("detail") or "") if manual_required else ""),
           "task_seq": task_seq, "task_id": entry["task_id"],
           "file": task_rel, "file_created": file_created, "notice": notice,
           "manifest": {k: mfres.get(k) for k in ("ok", "already", "manifest_hash")},
           "wakeup": wake, "activated": wake.get("result") in ("ok", "queued"),
           "ledger": {"state": (ledger_rec or {}).get("state", "")},
           "stage": "P3（写文件 + notice + manifest + 三步激活）"}
    if not mfres.get("ok"):
        out["code"] = "MANIFEST_APPEND_FAILED"
        out["error"] = mfres.get("error")
        out["next"] = "先修好 manifest（G0），再重新派活；文件与 notice 已写，别重复建"
        return out
    if wake.get("result") == "manual":
        out["needs_human"] = True
        out["how"] = wake.get("detail")
    if wake.get("result") == "failed":
        out["code"] = "WAKEUP_FAILED"
        out["next"] = "它没被叫醒（原因见 wakeup.detail）；可以人工去说一句，或修好唤醒地址后重试"
    return out


@router.post("/api/workshop/{workshop_id}/dispatch")
async def dispatch(workshop_id: str, request: Request):
    """组长发起派活。body：`{member_seq, title, goal?, accept?, due?, deps?, task_seq?}`。"""
    try:
        body = await request.json()
    except Exception:                                          # noqa: BLE001 — 空 body 会被下面拒
        body = {}
    body = body if isinstance(body, dict) else {}
    # ★2026-11-07（V138 §二 第二件）：可以**对一个**小队**派活** —— 平台展开成该**小队**每个工位各一条任务。
    #   ⚠️ 复用 `do_dispatch` 核心（闸门/任务号/正文全走同一条路），**不是**批量放行：
    #     每条任务各自记在 workshop_id+task_seq 上，验收仍逐条。
    squad_id = str(body.get("squad_id") or "").strip()
    if squad_id and not int(body.get("member_seq") or 0):
        from .. import squads as _cells_mod
        from .. import server as _srv

        ws = (_srv.workshops or {}).get(workshop_id)
        if ws is None:
            return JSONResponse({"ok": False, "code": "NO_SUCH_WORKSHOP",
                                 "error": "没有这个工作间"}, status_code=404)
        cl = list(getattr(ws, "squads", None) or [])
        if not _cells_mod.find(cl, squad_id):
            return JSONResponse({"ok": False, "code": "NO_SUCH_CELL",
                                 "error": "没有这个**小队**：%s" % squad_id,
                                 "next": "GET …/squads 看现有的"}, status_code=404)
        seqs = _cells_mod.squad_members_of(cl, squad_id)
        if not seqs:
            return JSONResponse({"ok": False, "code": "CELL_EMPTY",
                                 "error": "这个**小队**还没有工位（派不出去）",
                                 "next": "POST …/squads/%s {member_seqs: [...]} 先加人" % squad_id},
                                status_code=409)
        results = []
        for s0 in seqs:
            r = do_dispatch(workshop_id, {**body, "member_seq": s0},
                            issued_by=str(body.get("issued_by") or "lead"), source="api")
            results.append({"member_seq": s0, "ok": bool(r.get("ok")), "out": r})
        n_ok = len([r for r in results if r["ok"]])
        return {"ok": n_ok > 0, "squad_id": squad_id, "count": len(results), "ok_count": n_ok,
                "results": results,
                "note": ("按**小队**展开成 %d 条任务（每条各自记账）；验收仍**逐条**，"
                         "没有整**小队**一键通过" % len(results)),
                "next": "" if n_ok == len(results) else "看 results 里没派出去的那几条的 code/next"}
    if not int(body.get("member_seq") or 0):
        return JSONResponse({"ok": False, "code": "BAD_REQUEST",
                             "error": "要 member_seq（派给哪个工位）或 squad_id（派给整个 小队）",
                             "next": "GET /api/workshop/{id}/roster 取 member_seq；"
                                     "或 GET …/squads 取 squad_id"}, status_code=400)
    out = do_dispatch(workshop_id, body, issued_by=str(body.get("issued_by") or "lead"),
                      source="api")
    if out.get("ok"):
        return out
    st = 409 if str(out.get("code", "")).startswith("GATE_G") else 400
    return JSONResponse(out, status_code=st)


@router.post("/api/workshop/{workshop_id}/wakeup/poll")
async def wakeup_poll(workshop_id: str, request: Request):
    """B 档结算：标记被取走 ⇒ `ok`（记账）；还在且超时 ⇒ `manual`（需要人）。"""
    ws = _ws(workshop_id)
    if ws is None:
        return _no_ws(workshop_id)
    try:
        body = await request.json()
    except Exception:                                          # noqa: BLE001
        body = {}
    body = body if isinstance(body, dict) else {}
    wp = str(getattr(ws, "workspace_dir", "") or "")
    seq = int(body.get("member_seq") or 0)
    members = _roster.roster(workshop_id)["members"]
    targets = [m for m in members if (not seq or int(m["member_seq"]) == seq)]
    if not targets:
        return JSONResponse({"ok": False, "code": "NO_SUCH_MEMBER",
                             "error": "没有可结算的工位（member_seq=%s）" % seq,
                             "next": "GET …/roster 看有哪些"}, status_code=404)
    results = []
    for m in targets:
        r = _dp.poll_flags(wp, str(m.get("slot_dir") or ""))
        settled = ""
        if r["state"] == "taken":
            settled = "ok"
        elif r["state"] == "timeout":
            settled = "manual"
        results.append({"member_seq": m["member_seq"], "slot_dir": m.get("slot_dir"),
                        **r, "settled": settled})
        if settled:
            _record({"at": _dp._now(), "workshop_id": workshop_id, "kind": "wakeup_settle",
                     "member_seq": m["member_seq"], "wakeup_result": settled,
                     "detail": r.get("note")})
    return {"ok": True, "results": results,
            "hint": "只有 `settled=ok` 才算它真被叫醒；`manual` 要你去它的窗口说一句"}


@router.get("/api/workshop/{workshop_id}/dispatch/{task_seq}")
async def dispatch_detail(workshop_id: str, task_seq: int):
    """看一条派活的账（文件在不在 / notice 写了没 / 激活到哪一步）。"""
    ws = _ws(workshop_id)
    if ws is None:
        return _no_ws(workshop_id)
    entries = [e for e in _log_recent(workshop_id, 500)
               if int(e.get("task_seq") or 0) == int(task_seq) and e.get("kind") != "wakeup_settle"]
    if not entries:
        return JSONResponse({"ok": False, "code": "NO_SUCH_TASK",
                             "error": "没有 T-%03d 这条派活记录" % task_seq,
                             "next": "POST …/dispatch 派一条"}, status_code=404)
    e = entries[-1]
    wp = str(getattr(ws, "workspace_dir", "") or "")
    abs_task = Path(wp) / str(e.get("file") or "")
    ok_mf, mfb, _errs = _mf.read(wp)
    out = {"ok": True, "entry": e, "file_exists": abs_task.is_file(),
           "in_manifest": bool(ok_mf and e.get("file") in (mfb.get("active_tasks") or [])),
           "hint": "文件在 + 在 manifest.active_tasks 里 + activated_at 有值 ⇒ 这条派活是真落地的"}
    # ★2026-10-31：与派活返回**同一份**四步表（规则 20：一处真相，两处显示不许对不上）
    out.update(_steps_from_record(ws, e))
    return out
