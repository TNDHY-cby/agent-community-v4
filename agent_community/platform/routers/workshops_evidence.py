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
