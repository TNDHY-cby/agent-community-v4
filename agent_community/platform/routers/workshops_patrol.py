"""platform/routers/workshops_patrol：**巡逻员**（V56，2026-10-17 落地）。

设计稿：`design-docs/V56_巡逻员现场查看与维修设计.md`（老板「按你的来」拍板）。

三个端点（挂 `workshops*` 组）：

    POST /api/workshop/{ws_id}/patrol                      派巡逻员到现场（带目标成员与症状）
    POST /api/workshop/{ws_id}/patrol/{patrol_id}/result   巡逻员回报（校验三类动作 + 三态 + 复验）
    GET  /api/workshop/{ws_id}/patrols?target_member_id=    巡逻历史（每条带结论与证据）

⭐ 关键口径（与"调起成功不采信我发出去了"同一标准）：
   **"它说修好了"不算修好** —— 回报 `fixed=true` 时，平台拿**自己的掉线判据**复核；
   复核不过 ⇒ 记 `claimed_but_not_verified`，照旧升级给用户。
"""
from __future__ import annotations

from uuid import uuid4

from fastapi import APIRouter, Request

from .. import patrol as pt
from ..audit import audit_log as _audit_log
from ..state import workshops

router = APIRouter()


def _find_member(ws, member_id: str):
    return next((m for m in (ws.members or []) if m.member_id == member_id), None)


def _patrol_log(ws) -> list:
    """巡逻历史存在工作间上（`ws.patrols`），不需要新表。"""
    log = getattr(ws, "patrols", None)
    if log is None:
        log = []
        try:
            ws.patrols = log
        except Exception:                      # 老对象/只读替身：退回旁挂
            pass
    return log


@router.post("/api/workshop/{ws_id}/patrol")
async def dispatch_patrol(ws_id: str, request: Request):
    """派巡逻员到现场。

    body: `{target_member_id, symptom?: {…}, patrol_member_id?}`
    ⇒ 没巡逻员/巡逻员没绑 harness/目标是巡逻员自己 ⇒ **如实 400**（不假装派了人）。
    """
    from ..server import Utf8JSONResponse, _append_msg, _dispatch_to_harness, now_iso, save_state
    ws = workshops.get(ws_id)
    if not ws:
        return Utf8JSONResponse({"error": "工作间不存在"}, status_code=404)
    body = await request.json()
    target_id = (body.get("target_member_id") or "").strip()
    if not target_id:
        return Utf8JSONResponse({"error": "target_member_id 不能为空（要去看谁）"}, status_code=400)
    target = _find_member(ws, target_id)
    if not target:
        return Utf8JSONResponse({"error": f"目标成员不存在: {target_id}"}, status_code=404)

    want = (body.get("patrol_member_id") or "").strip()
    if want:
        patrol_member = _find_member(ws, want)
        if not patrol_member:
            return Utf8JSONResponse({"error": f"巡逻员不存在: {want}"}, status_code=404)
        if not pt.role_is_patrol(patrol_member):
            return Utf8JSONResponse(
                {"error": f"成员 {want} 的角色是「{patrol_member.role}」，不是巡逻员",
                 "how_to_fix": "把它的角色改成含「巡逻」二字，或让平台自动挑一个"}, status_code=400)
        if not any((h or "").strip() for h in (patrol_member.harness_ids or [])):
            return Utf8JSONResponse(
                {"error": f"巡逻员 {want} 未绑定 harness，派不出去",
                 "how_to_fix": "给它绑定一个 harness（否则平台发不出去）"}, status_code=400)
    else:
        patrol_member = pt.pick_patrol_member(ws, exclude=[target_id])
        if patrol_member is None:
            return Utf8JSONResponse(
                {"error": "这个工作间里没有可用的巡逻员",
                 "why": "巡逻员的要求：角色含「巡逻」且**绑了 harness**",
                 "how_to_fix": "加一个角色为「巡逻员」的成员并绑定 harness；"
                               "或者平台会照旧走原链条（通知组长 → 试调起 → 通知用户）"},
                status_code=409)

    patrol_id = uuid4().hex[:8]
    symptom = body.get("symptom") or pt.symptom_of(target)
    payload = pt.dispatch_payload(ws, patrol_member, target, symptom=symptom,
                                  base_url=str(request.base_url).rstrip("/"), patrol_id=patrol_id)
    ok, note = _dispatch_to_harness(patrol_member.harness_ids[0], payload, kind="task")
    rec = {
        "patrol_id": patrol_id,
        "target_member_id": target_id,
        "patrol_member_id": patrol_member.member_id,
        "dispatched": bool(ok),
        "dispatch_note": str(note or "")[:200],
        "symptom": symptom,
        "at": now_iso(),
        "status": "dispatched" if ok else "dispatch_failed",
        "report": None,
        "verdict": None,
    }
    _patrol_log(ws).append(rec)
    _append_msg(ws, "leader",
                "【巡逻】已派 %s（%s）去看 %s 为什么掉线%s"
                % (patrol_member.role, patrol_member.display_name, target.display_name,
                   "" if ok else "（派发失败：" + rec["dispatch_note"] + "）"),
                zone=2, display_name="组长")
    _audit_log.record("workshop.patrol.dispatch", actor="user", target=ws_id,
                      detail="patrol_id=%s → %s 看 %s（dispatched=%s）"
                             % (patrol_id, patrol_member.member_id, target_id, bool(ok)))
    save_state()
    return {"success": bool(ok), "patrol_id": patrol_id,
            "patrol_member_id": patrol_member.member_id, "target_member_id": target_id,
            "dispatched": bool(ok), "dispatch_note": note,
            "checklist": payload["checklist"], "allowed_actions": payload["allowed_actions"],
            "instruction": payload["message"]}


@router.post("/api/workshop/{ws_id}/patrol/{patrol_id}/result")
async def submit_patrol_result(ws_id: str, patrol_id: str, request: Request):
    """巡逻员回报：校验（三类动作/三态/必填理由）→ **平台复核** → 一句人话 + 审计。"""
    from ..server import Utf8JSONResponse, _append_msg, now_iso, save_state
    # 内核在 platform/，不在 routers/。判定口径只取 `rescue_needed()["signal_reasons"]`（见下）。
    from ..member_offline import rescue_needed as _rescue_needed

    ws = workshops.get(ws_id)
    if not ws:
        return Utf8JSONResponse({"error": "工作间不存在"}, status_code=404)
    rec = next((r for r in _patrol_log(ws) if r.get("patrol_id") == patrol_id), None)
    if rec is None:
        return Utf8JSONResponse({"error": f"巡逻单不存在: {patrol_id}"}, status_code=404)
    body = await request.json()
    ok, probs, norm = pt.validate_report(body)
    if not ok:
        return Utf8JSONResponse({"error": "巡逻回报不合规", "problems": probs,
                                 "how_to_fix": "逐条改；见 design-docs/V56 §5"}, status_code=422)

    target = _find_member(ws, rec["target_member_id"])
    offline_still = True
    if target is not None:
        # ⭐2026-10-19 修（掉线恢复链端到端判据抓到）：这里原先把 `offline_report(ws)` 的返回值
        #   当 dict 用（`rep.get("members")`），而它**返回的是 list** ⇒ 每次回报都在这里抛
        #   `AttributeError: 'list' object has no attribute 'get'`，被 except 兜底成「仍掉线」
        #   ⇒ **平台复核永远是"没修好"**：巡逻员就算真修好了也照样升级给用户，而且接口仍回 200、
        #   结论看着合理（典型"看起来成功"）。端到端判据把服务端日志里的异常抓了出来。
        # 现在改走**一处真相**：判定函数用 `patrol.py` 同一套（`rescue_needed` 的 signal_reasons），
        # 心跳/桥口径复用 `workshops_recover` 里那份（别在这里写第二份 `_hb_status`）。
        try:
            from .workshops_recover import _bridge_alive, _hb_status
            sig = _rescue_needed(ws, target, heartbeat_status=_hb_status,
                                bridge_alive=_bridge_alive)["signal_reasons"]
            offline_still = bool(sig)
        except Exception as _e:                 # 判不了就**如实**按"仍掉线"处理（宁可多升级，不谎报修好）
            print(f"[patrol] 复核掉线判据失败，按未修好处理: {_e}", flush=True)
            offline_still = True
    v = pt.verdict(norm, offline_still=offline_still)

    rec.update({"status": "reported", "report": norm, "verdict": v, "reported_at": now_iso()})
    if target is not None and v["fixed"]:
        target.entry_state = ""              # 真确认修好了：清掉"需要人工"
        target.entry_reason = ""
        try:
            target.offline_attempts = 0
        except Exception:
            pass
    elif target is not None and not v["fixed"]:
        target.entry_state = "needs_human"
        target.entry_reason = pt.human_line(norm, v)

    line = pt.human_line(norm, v)
    _append_msg(ws, "notice", "【巡逻结果】" + line, zone=3)
    _audit_log.record("workshop.patrol.result", actor="agent", target=ws_id,
                      detail="patrol_id=%s status=%s fixed=%s"
                             % (patrol_id, v["status"], v["fixed"]))
    save_state()
    return {"success": True, "patrol_id": patrol_id, "verdict": v, "report": norm,
            "human_line": line, "offline_still": offline_still}


@router.get("/api/workshop/{ws_id}/patrols")
async def list_patrols(ws_id: str, target_member_id: str = ""):
    """巡逻历史（每条带结论与证据）；`target_member_id` 可选过滤。"""
    from ..server import Utf8JSONResponse
    ws = workshops.get(ws_id)
    if not ws:
        return Utf8JSONResponse({"error": "工作间不存在"}, status_code=404)
    items = [r for r in _patrol_log(ws)
             if not target_member_id or r.get("target_member_id") == target_member_id]
    return {"success": True, "workshop_id": ws_id, "count": len(items), "items": items}
