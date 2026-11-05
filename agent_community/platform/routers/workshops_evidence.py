"""platform/routers/workshops_evidence：**信息搜索者与防信息污染**（V55，2026-10-17 落地）。

设计稿：`design-docs/V55_信息搜索者与防信息污染设计.md`（老板「最优解」拍板）。

三个端点（都挂 `workshops*` 组，聚合器只做 `router.routes.extend`）：

    POST /api/workshop/{ws_id}/search-request   委派检索（派给某个成员；带 question/must_cover/时限）
    POST /api/workshop/{ws_id}/evidence         交**信息卡**（硬约束校验 → 分档 → 归并 → 限量广播）
    GET  /api/workshop/{ws_id}/evidence         取卡片（默认只给摘要；`?full=1` 才带摘录）

**平台不搜**：搜索由 agent 做；平台只做校验、收敛、限量分发、留痕。四件标注都不改写内容。
"""
from __future__ import annotations

from uuid import uuid4

from fastapi import APIRouter, Request

from .. import evidence as ev
from ..audit import audit_log as _audit_log
from ..state import workshops
from ..workshop import write_resources_manifest
from .workshops_common import _leader_member_hid, _leader_pick_member

router = APIRouter()

EVIDENCE_KIND = "evidence"


@router.post("/api/workshop/{ws_id}/search-request")
async def post_search_request(ws_id: str, request: Request):
    """把"去搜一下"委派给某个成员（搜索者）。

    body: `{member_id, question, must_cover?: [], deadline_minutes?, node_id?}`
    ⇒ 平台拼**给 agent 看的话术**（`evidence.search_instruction`，一处真相）＋派发任务；
    派不出去就如实报（成员未绑 harness 这种情况最常见）。
    """
    from ..server import Utf8JSONResponse, _append_msg, _dispatch_to_harness, now_iso, save_state
    ws = workshops.get(ws_id)
    if not ws:
        return Utf8JSONResponse({"error": "工作间不存在"}, status_code=404)
    body = await request.json()
    member_id = (body.get("member_id") or "").strip()
    question = (body.get("question") or "").strip()
    if not member_id or not question:
        return Utf8JSONResponse({"error": "member_id 与 question 不能为空"}, status_code=400)
    member = _leader_pick_member(ws, member_id)
    if not member:
        return Utf8JSONResponse({"error": f"成员不存在: {member_id}"}, status_code=404)
    hid = _leader_member_hid(member)
    if not hid:
        return Utf8JSONResponse({"error": f"成员 {member.display_name} 未绑定 harness，无法委派检索"},
                                status_code=400)
    must_cover = [str(x).strip() for x in (body.get("must_cover") or []) if str(x).strip()]
    deadline = int(body.get("deadline_minutes") or 0)
    node_id = (body.get("node_id") or "").strip()
    instruction = ev.search_instruction(question, must_cover, deadline,
                                        base_url=str(request.base_url).rstrip("/"))
    payload = {
        "type": "search_request",
        "kind": "search_request",
        "workshop_id": ws.workshop_id,
        "member_id": member.member_id,
        "role": member.role,
        "question": question,
        "must_cover": must_cover,
        "deadline_minutes": deadline,
        "output": "evidence",
        "workspace_dir": ws.workspace_dir,
        "report_endpoint": "/api/harness/task-result",
        "message": instruction,
    }
    if node_id:
        payload["node_id"] = node_id
    ok, note = _dispatch_to_harness(hid, payload, kind="task")
    ws.assignments.setdefault(member_id, []).append({
        "kind": "search_request", "question": question, "must_cover": must_cover,
        "deadline_minutes": deadline, "at": now_iso(), "by": "平台", "dispatched": ok,
    })
    _append_msg(ws, "leader",
                "【委派检索】请 %s（%s）去查：%s%s\n（派发：%s）"
                % (member.role, member.display_name, question,
                   ("\n必须覆盖：" + "、".join(must_cover)) if must_cover else "",
                   "成功" if ok else "失败"),
                zone=2, display_name="组长")
    _audit_log.record("workshop.search_request", actor="user", target=ws_id,
                      detail="委派检索：%s → %s" % (question[:60], member_id))
    save_state()
    return {"success": True, "member_id": member_id, "dispatched": ok, "dispatch_note": note,
            "instruction": instruction}


@router.post("/api/workshop/{ws_id}/evidence")
async def post_evidence(ws_id: str, request: Request):
    """交**信息卡**：校验硬约束（不过就 422 说清怎么改）→ 分档 → 归并 → **限量广播**。

    body: `{name, note, uploader, node_id?, contradicts?: [rid], evidence: {as_of, confidence,
           limits, sources: [{url,title,publisher,quoted_at,quote,primary,paywall}]}}`
    """
    from ..server import Utf8JSONResponse, _append_msg, _tree_find, now_iso, save_state
    ws = workshops.get(ws_id)
    if not ws:
        return Utf8JSONResponse({"error": "工作间不存在"}, status_code=404)
    body = await request.json()
    ok, errs, card = ev.validate(body)
    if not ok:
        # 失败要说话：一次把所有不合规项都说清（别让人一轮一轮试）
        return Utf8JSONResponse({"error": "信息卡不合规", "problems": errs,
                                 "how_to_fix": "逐条改；硬约束见 design-docs/V55 §3"}, status_code=422)

    graded = ev.grade_sources(card["evidence"]["sources"])
    conf = ev.effective_confidence(card["evidence"]["confidence"], graded["cap"])
    groups = ev.merge_duplicates(graded["sources"])

    rid = uuid4().hex[:8]
    res = {
        "rid": rid,
        "name": card["name"],
        "kind": EVIDENCE_KIND,
        "path": "",
        "note": card["note"],
        "uploader": (body.get("uploader") or "").strip(),
        "task_node": (body.get("node_id") or "").strip(),
        "created_at": now_iso(),
        "evidence": {
            **card["evidence"],
            "confidence": conf["effective"],
            "confidence_reported": conf["reported"],
            "confidence_capped": conf["capped"],
            "confidence_why": conf["why"],
            "source_groups": len(groups),
            "grades": sorted({s["grade"] for s in graded["sources"]}),
            "disputed": False,
            # ★2026-10-19（证据链端到端判据抓到）**同一件事原有两处口径**：
            #   `evidence.validate()` 会把 `contradicts` 归一化进 `card["evidence"]["contradicts"]`，
            #   而这里只读**body 顶层**的 `contradicts` ⇒ 按校验器格式交的卡，冲突**永远不生效**
            #   （接口回 200、卡也进了，只是两张都没标争议 —— 又是"看起来成功"）。
            #   现在两处都认（顶层优先，兼容既有调用方），归一到同一个字段。
            "contradicts": [str(x).strip()
                            for x in ((body.get("contradicts")
                                       or card["evidence"].get("contradicts") or []))
                            if str(x).strip()],

        },
    }

    node_id = res["task_node"]
    if node_id and _tree_find(ws, node_id) is None:
        return Utf8JSONResponse({"error": f"任务树节点不存在：{node_id}"}, status_code=400)

    # 冲突标记：两卡互链，都打 disputed（平台不判谁对）
    touched = []
    for other in ws.resources:
        if other.get("rid") in res["evidence"]["contradicts"]:
            other.setdefault("evidence", {}).setdefault("contradicts", [])
            if rid not in other["evidence"]["contradicts"]:
                other["evidence"]["contradicts"].append(rid)
            other["evidence"]["disputed"] = True
            res["evidence"]["disputed"] = True
            touched.append(other.get("rid"))

    ws.resources.append(res)
    if node_id:
        node = _tree_find(ws, node_id)
        if node is not None:
            node.setdefault("resources", []).append(rid)
    try:
        write_resources_manifest(ws)
    except Exception as _e:                     # 落盘失败要说话，但要写进日志而不是吞掉
        print(f"[evidence] 资源清单落盘失败: {_e}", flush=True)

    summary = ev.render_summary(res, rid=rid)
    _append_msg(ws, "notice", summary, zone=3)   # ★只广播摘要：**不含摘录原文**
    _audit_log.record("workshop.evidence.add", actor="agent", target=ws_id,
                      detail="信息卡「%s」rid=%s 来源组=%d 置信=%s"
                             % (res["name"][:30], rid, len(groups), conf["effective"]))
    save_state()
    return {
        "success": True,
        "resource": res,
        "source_groups": len(groups),
        "confidence": conf,
        "marked_disputed_with": touched,
        "summary": summary,
    }


@router.get("/api/workshop/{ws_id}/evidence")
async def list_evidence(ws_id: str, node_id: str = "", full: int = 0):
    """取信息卡。**默认只给摘要**（断言 + 来源标题 + 档位），`full=1` 才带摘录原文。

    限量再分发：默认路径不返回 `quote`，避免把大段原文灌进别人的上下文。
    """
    from ..server import Utf8JSONResponse
    ws = workshops.get(ws_id)
    if not ws:
        return Utf8JSONResponse({"error": "工作间不存在"}, status_code=404)
    items = []
    for r in ws.resources:
        if r.get("kind") != EVIDENCE_KIND:
            continue
        if node_id and r.get("task_node") != node_id:
            continue
        e = r.get("evidence") or {}
        item = {
            "rid": r.get("rid"), "name": r.get("name"), "note": r.get("note"),
            "uploader": r.get("uploader"), "task_node": r.get("task_node"),
            "created_at": r.get("created_at"),
            "as_of": e.get("as_of"), "confidence": e.get("confidence"),
            "confidence_reported": e.get("confidence_reported"),
            "confidence_capped": e.get("confidence_capped"),
            "limits": e.get("limits"), "grades": e.get("grades"),
            "source_groups": e.get("source_groups"),
            "disputed": e.get("disputed", False), "contradicts": e.get("contradicts", []),
            "sources": [{"url": s.get("url"), "title": s.get("title"),
                         "publisher": s.get("publisher"), "grade": s.get("grade"),
                         "primary": s.get("primary")}
                        for s in (e.get("sources") or [])],
        }
        if full:
            item["quotes"] = [{"url": s.get("url"), "quote": s.get("quote")}
                              for s in (e.get("sources") or [])]
        items.append(item)
    return {"success": True, "workshop_id": ws_id, "count": len(items),
            "full": bool(full), "items": items}


# ═══════════════════════════════════════════════════════════════════════
# ★V130：**跨组织证据采信**（2026-11-04 老板拍板；`design-docs/V130`）
#
# 三个端点，回答"复核算不算数 / 谁说了算 / 两方不一致怎么办"：
#   POST …/tasks/{seq}/peer-review   接收方**独立重算**（喂进对端 sha256 清单，本侧真扫盘比对）
#   POST …/tasks/{seq}/dispute       挂争议 / 判证伪（写 dispute 记录，从可验收里摘出）
#   GET  …/tasks/{seq}/peer-review   **只读**看采信态 + 差异 + 争议
#
# 硬规则（`V130` §6）：**没有 `accepted_peer` 的跨组织结果，不算验收通过**。
# 平台不替人判"谁在撒谎"：它只做可自动做的那部分（重算 hash、查凭据是否存在），
# 主张冲突就如实留痕并冻结待裁。
# ═══════════════════════════════════════════════════════════════════════

def _peer_files_from_body(body: dict) -> tuple[list, str]:
    """取对端给的产物清单：显式 `peer_files` 优先，否则从外部任务记录 `pt_id` 的最近回执里取。"""
    items = body.get("peer_files") or []
    if isinstance(items, list) and items:
        return [x for x in items if isinstance(x, dict)], "peer_files"
    pt_id = str(body.get("pt_id") or "").strip()
    if pt_id:
        from .. import peer_tasks as _pt
        rec = _pt.get(pt_id) or {}
        arts = rec.get("last_artifacts") or []
        if arts:
            return [x for x in arts if isinstance(x, dict)], "pt_id:%s 的最近回执" % pt_id
    return [], ""


@router.post("/api/workshop/{ws_id}/tasks/{task_seq}/peer-delivery")
async def post_peer_delivery(ws_id: str, task_seq: int, request: Request):
    """**登记对端交付**（`V130` §4 规则一）：交付方给产物清单 + `sha256` + 它自己的说法。

    为什么要有这个入口：A2A / MCP 的自动回写走的是同一个 `note_external_result`，
    但"对方把结果发在群里/邮件里、由人录进来"也是真实场景 —— 没有它，跨组织任务只能
    靠后台轮询，人连"复核"的起点都没有。

    ⚠️ 这一步只是**交付方自证**：采信态默认 `accepted_self`，**不许**当验收通过。
    body：`{source?, external_task_id?, external_state?, result?, artifacts?: [{name, sha256, bytes}]}`
    """
    from ..server import Utf8JSONResponse
    from .. import task_ledger as _ledger
    ws = workshops.get(ws_id)
    if not ws:
        return Utf8JSONResponse({"error": "工作间不存在"}, status_code=404)
    try:
        body = await request.json()
    except Exception:                                          # noqa: BLE001
        body = {}
    body = body if isinstance(body, dict) else {}
    arts = [a for a in (body.get("artifacts") or []) if isinstance(a, dict)][:50]
    rec = _ledger.note_external_result(ws_id, task_seq,
                                       source=str(body.get("source") or "manual"),
                                       external_task_id=str(body.get("external_task_id") or ""),
                                       external_state=str(body.get("external_state") or ""),
                                       result=str(body.get("result") or ""),
                                       artifacts=arts)
    if rec is None:
        return Utf8JSONResponse({"ok": False, "code": "NO_SUCH_TASK",
                                 "error": "账本里没有 T-%03d" % int(task_seq),
                                 "next": "先派活（POST …/dispatch）；账本只记真实派出过的任务"},
                                status_code=404)
    view = _ledger.peer_view(ws_id, task_seq)
    _audit_log.record("workshop.peer_delivery", actor="user", target=ws_id,
                      detail="T-%03d 登记对端交付：产物 %d 条（来源 %s）"
                             % (int(task_seq), len(arts), body.get("source") or "manual"))
    return {"ok": True, "task_seq": int(task_seq), "artifacts": len(arts),
            "accept_state": view.get("accept_state"),
            "note": "这是**交付方自证**，采信态 accepted_self；要算数得做本侧复核"
                    "（POST …/peer-review）",
            "view": view}


@router.post("/api/workshop/{ws_id}/tasks/{task_seq}/peer-review")
async def post_peer_review(ws_id: str, task_seq: int, request: Request):
    """**接收方独立复核**：本侧真扫产物、重算 `sha256`、逐文件比对对端清单（`V130` §4 规则二）。

    body：`{peer_files?: [{name, sha256, bytes, slot_dir?}], pt_id?: str,
            checked_by?, note?, slot_dir?}`
    · 不给 `peer_files` 就试 `pt_id`（刷新回执拿到的 `last_artifacts`，含对端 `sha256`）；
    · 全部一致 ⇒ 采信态 `accepted_peer`（账本记 `peer_checked_at`）；否则**停在 `accepted_self`** + `peer_diff`。
    """
    from ..server import Utf8JSONResponse
    from .. import artifacts as _arts
    from .. import task_ledger as _ledger
    ws = workshops.get(ws_id)
    if not ws:
        return Utf8JSONResponse({"error": "工作间不存在"}, status_code=404)
    try:
        body = await request.json()
    except Exception:                                          # noqa: BLE001 — 不传就当空
        body = {}
    body = body if isinstance(body, dict) else {}
    rec = _ledger.get(ws_id, task_seq)
    if rec is None:
        return Utf8JSONResponse({"ok": False, "code": "NO_SUCH_TASK",
                                 "error": "账本里没有 T-%03d" % int(task_seq),
                                 "next": "先派活（POST …/dispatch）；账本只记真实派出过的任务"},
                                status_code=404)
    peer_files, src = _peer_files_from_body(body)
    if not peer_files:
        return Utf8JSONResponse({
            "ok": False, "code": "PEER_FILES_REQUIRED",
            "error": "没拿到对端的产物清单（既没传 peer_files，也没能从 pt_id 的回执里取到）",
            "next": ("先 POST /api/peer-tasks/{pt_id}/refresh 让本侧拿到对端产物清单（含 sha256），"
                     "或在 body 里直接传 peer_files: [{name, sha256, bytes}]"),
        }, status_code=400)
    wp = str(getattr(ws, "workspace_dir", "") or "")
    slot = str(rec.get("slot_dir") or body.get("slot_dir") or "")
    if not wp or not slot:
        return Utf8JSONResponse({"ok": False, "code": "NO_ARTIFACT_DIR",
                                 "error": "这条任务没有工位目录（slot_dir）或工作间没有路径，无法重算产物",
                                 "next": "确认账本里这条任务的 slot_dir；或 body 传 slot_dir"}, status_code=400)
    snap = _arts.snapshot(wp, slot)          # 真扫盘（只读：不写 _meta，不改文件）
    out = _ledger.record_peer_review(ws_id, task_seq, peer_files=peer_files,
                                     local_files=snap.get("files") or {},
                                     local_slot_dir=slot,
                                     checked_by=str(body.get("checked_by") or "user"),
                                     note=str(body.get("note") or ""))
    out["scan"] = {"slot_dir": slot, "exists": bool(snap.get("exists")),
                   "files": len(snap.get("files") or {}), "source": src}
    out["view"] = _ledger.peer_view(ws_id, task_seq)
    _audit_log.record("workshop.peer_review", actor="user", target=ws_id,
                      detail="T-%03d 复核：%s（一致 %d / 不一致 %d）"
                             % (int(task_seq), out.get("accept_state"),
                                out.get("diff", {}).get("matched", 0),
                                out.get("diff", {}).get("mismatched", 0)))
    return out


@router.post("/api/workshop/{ws_id}/tasks/{task_seq}/dispute")
async def post_dispute(ws_id: str, task_seq: int, request: Request):
    """**挂争议 / 判证伪**（`V130` §4 规则三）：双方主张原样留痕，这条从可验收里摘出（不删数据）。

    body：`{raised_by, claims: [{by, claim}] | [str], adjudicator?, why?, outcome?: disputed|refuted}`
    """
    from ..server import Utf8JSONResponse
    from .. import task_ledger as _ledger
    ws = workshops.get(ws_id)
    if not ws:
        return Utf8JSONResponse({"error": "工作间不存在"}, status_code=404)
    try:
        body = await request.json()
    except Exception:                                          # noqa: BLE001
        body = {}
    body = body if isinstance(body, dict) else {}
    out = _ledger.mark_dispute(ws_id, task_seq,
                               raised_by=str(body.get("raised_by") or "user"),
                               claims=body.get("claims") or [],
                               adjudicator=str(body.get("adjudicator") or ""),
                               why=str(body.get("why") or ""),
                               outcome=str(body.get("outcome") or "disputed"))
    if not out.get("ok"):
        code = out.get("code") or ""
        return Utf8JSONResponse(out, status_code=404 if code == "NO_SUCH_TASK" else 400)
    out["view"] = _ledger.peer_view(ws_id, task_seq)
    _audit_log.record("workshop.dispute", actor="user", target=ws_id,
                      detail="T-%03d %s：发起=%s 待裁=%s"
                             % (int(task_seq), out.get("accept_state"),
                                out.get("dispute", {}).get("raised_by"),
                                out.get("dispute", {}).get("adjudicator") or "未指定"))
    return out


@router.get("/api/workshop/{ws_id}/tasks/{task_seq}/peer-review")
async def get_peer_review(ws_id: str, task_seq: int):
    """**只读**跨组织复核视图：采信态 / 复核时间 / 逐文件差异 / 争议记录。不扫盘、不写盘。"""
    from ..server import Utf8JSONResponse
    from .. import task_ledger as _ledger
    ws = workshops.get(ws_id)
    if not ws:
        return Utf8JSONResponse({"error": "工作间不存在"}, status_code=404)
    view = _ledger.peer_view(ws_id, task_seq)
    if not view.get("ok"):
        return Utf8JSONResponse(view, status_code=404)
    return view
