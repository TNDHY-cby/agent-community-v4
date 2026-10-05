"""platform/routers/workshops_assign：组长与成员编排（V-13 自 workshops.py 拆分）。

承载 leader-status / leader-assign / leader-redirect / leader-assignments /
select-members / members / confirm-members / leader-workbench 等端点。
拆分纪律：路由路径、参数、响应体与拆分前逐字一致（纯搬迁，零行为变更）。
"""
from __future__ import annotations
from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect
from ..task_state_machine import DISCUSSING
from ..workshop import WorkshopMember
from ..experience_v2 import harness_reputation_bonus as _v2_harness_reputation_bonus
from ..ai_external import run_ai_call as ai_external_run_ai_call
from ..memory import capability_ledger
from ..harness_adapter import harness_manager
from .workshops_common import _sv
import json
from ..state import interject_store, pending_activations, task_state_machine, tasks, workshops

router = APIRouter()

@router.post("/api/workshop/{ws_id}/leader-status")
async def workshop_leader_status(ws_id: str):
    from ..server import Utf8JSONResponse
    """组长检查并汇报各员工激活状态（二级讨论时前端调用）。"""
    ws = workshops.get(ws_id)
    if not ws:
        return Utf8JSONResponse({"error": "工作间不存在"}, status_code=404)
    status = [
        {"role": m.role, "display_name": m.display_name, "status": m.status}
        for m in ws.members
    ]
    return {"success": True, "members": status}


@router.post("/api/workshop/{ws_id}/leader/assign")
async def workshop_leader_assign(ws_id: str, request: Request):
    """组长向指定成员细化/调整派发指令：校验成员与 harness 绑定后，
    复用 _dispatch_to_harness 派发「组长委派细化」任务，并写入讨论区 + assignments 持久化。"""
    from ..server import Utf8JSONResponse, _append_msg, _dispatch_to_harness, now_iso, save_state
    ws = workshops.get(ws_id)
    if not ws:
        return Utf8JSONResponse({"error": "工作间不存在"}, status_code=404)
    body = await request.json()
    member_id = (body.get("member_id") or "").strip()
    instruction = (body.get("instruction") or "").strip()
    note = (body.get("note") or "").strip()
    if not member_id or not instruction:
        return Utf8JSONResponse({"error": "member_id 与 instruction 不能为空"}, status_code=400)
    member = _leader_pick_member(ws, member_id)
    if not member:
        return Utf8JSONResponse({"error": f"成员不存在: {member_id}"}, status_code=404)
    hid = _leader_member_hid(member)
    if not hid:
        return Utf8JSONResponse({"error": f"成员 {member.display_name} 未绑定 harness，无法派发"}, status_code=400)
    payload = _leader_build_assign_payload(ws, member, instruction, note, kind="assign")
    disp_ok, disp_note = _dispatch_to_harness(hid, payload, kind="task")
    ws.assignments.setdefault(member_id, []).append({
        "kind": "assign",
        "instruction": instruction,
        "note": note,
        "at": now_iso(),
        "by": "组长",
        "dispatched": disp_ok,
    })
    _append_msg(
        ws, "leader", f"【组长委派细化】给 {member.role}（{member.display_name}）：{instruction}"
        + (f"\n备注：{note}" if note else "") + f"\n（派发：{'成功' if disp_ok else '失败'}）",
        zone=2, display_name="组长",
    )
    save_state()
    return {"success": True, "member_id": member_id, "dispatched": disp_ok, "dispatch_note": disp_note}


@router.post("/api/workshop/{ws_id}/leader/redirect")
async def workshop_leader_redirect(ws_id: str, request: Request):
    """组长将任务从一名成员转向另一名成员：目标成员派发承接指令，
    原成员如有 harness 则派发移交通知，讨论区记录流转，assignments 持久化。"""
    from ..server import Utf8JSONResponse, _append_msg, _dispatch_to_harness, now_iso, save_state
    ws = workshops.get(ws_id)
    if not ws:
        return Utf8JSONResponse({"error": "工作间不存在"}, status_code=404)
    body = await request.json()
    from_member_id = (body.get("from_member_id") or "").strip()
    to_member_id = (body.get("to_member_id") or "").strip()
    instruction = (body.get("instruction") or "").strip()
    note = (body.get("note") or "").strip()
    if not from_member_id or not to_member_id:
        return Utf8JSONResponse({"error": "from_member_id 与 to_member_id 不能为空"}, status_code=400)
    if from_member_id == to_member_id:
        return Utf8JSONResponse({"error": "转向对象不能与原成员相同"}, status_code=400)
    src = _leader_pick_member(ws, from_member_id)
    dst = _leader_pick_member(ws, to_member_id)
    if not src or not dst:
        return Utf8JSONResponse({"error": f"转向成员不存在: {from_member_id if not src else to_member_id}"}, status_code=404)
    dst_hid = _leader_member_hid(dst)
    if not dst_hid:
        return Utf8JSONResponse({"error": f"目标成员 {dst.display_name} 未绑定 harness，无法承接"}, status_code=400)
    # 1) 向目标成员派发承接任务
    payload = _leader_build_assign_payload(ws, dst, instruction or "请承接并推进该任务。", note, kind="redirect")
    disp_ok, disp_note = _dispatch_to_harness(dst_hid, payload, kind="task")
    # 2) 原成员如有 harness，派发移交通知（不阻断主流程）
    src_hid = _leader_member_hid(src)
    src_note = ""
    if src_hid:
        _src_payload = _leader_build_assign_payload(
            ws, src, "你负责的这项任务已由组长转交给其他成员，请停止当前工作并等待新指令。", note, kind="assign"
        )
        _src_ok, _src_note = _dispatch_to_harness(src_hid, _src_payload, kind="task")
        src_note = f"；原成员通知：{'成功' if _src_ok else '失败'}"
    ws.assignments.setdefault(to_member_id, []).append({
        "kind": "redirect",
        "from_member_id": from_member_id,
        "instruction": instruction,
        "note": note,
        "at": now_iso(),
        "by": "组长",
        "dispatched": disp_ok,
    })
    _append_msg(
        ws, "leader", f"【组长任务转向】{src.role}（{src.display_name}）→ {dst.role}（{dst.display_name}）："
        + (instruction or "承接并推进该任务。") + (f"\n备注：{note}" if note else "")
        + f"\n（承接派发：{'成功' if disp_ok else '失败'}{src_note}）",
        zone=2, display_name="组长",
    )
    save_state()
    return {"success": True, "from_member_id": from_member_id, "to_member_id": to_member_id,
            "dispatched": disp_ok, "dispatch_note": disp_note, "src_notified": bool(src_hid)}


@router.get("/api/workshop/{ws_id}/leader/assignments")
async def workshop_leader_assignments(ws_id: str):
    """查看组长派发指令/转向记录（供前端组长工作台展示）。"""
    from ..server import Utf8JSONResponse
    ws = workshops.get(ws_id)
    if not ws:
        return Utf8JSONResponse({"error": "工作间不存在"}, status_code=404)
    recs = []
    for mid, items in (ws.assignments or {}).items():
        m = _leader_pick_member(ws, mid)
        recs.append({
            "member_id": mid,
            "role": m.role if m else "",
            "display_name": m.display_name if m else mid,
            "items": items[-10:],
        })
    return {"success": True, "assignments": recs}


@router.post("/api/workshop/{ws_id}/select-members")
async def workshop_select_members(ws_id: str):
    from ..server import Utf8JSONResponse, _discussion_ctx
    """平台 AI 根据任务+讨论内容，从已注册 harness 里初步选定员工填名单。"""
    ws = workshops.get(ws_id)
    if not ws:
        return Utf8JSONResponse({"error": "工作间不存在"}, status_code=404)
    if not _sv().ai_provider:
        return Utf8JSONResponse({"error": "AI Provider 未配置"}, status_code=400)
    harnesses = harness_manager.list_sessions()
    harness_desc = "\n".join(
        f"- {h['harness_id']}（{h['harness_name']}）：模型 {h['ai'].get('model_name', '')}，"
        f"能力 {', '.join(h['ai'].get('capabilities', []))}"
        for h in harnesses
    ) or "（无已注册 harness）"
    # V2-2：AI 选人输入附带 capability_ledger 信誉加权提示（纯规则排序，无信誉数据时返回空串保持原逻辑）
    _rep_bonus = _v2_harness_reputation_bonus(
        ws.hall_content, [h["harness_id"] for h in harnesses],
        harness_manager=harness_manager, capability_ledger=capability_ledger,
    )
    if _rep_bonus:
        harness_desc = harness_desc + "\n\n" + _rep_bonus
    context = f"任务：{ws.hall_content}\n\n讨论（最近10条）：\n" + _discussion_ctx(ws, limit=10)
    system = (
        "你是 外端Agent生产合作社（External Agent Community） 的员工选定助手。根据任务需求 + 讨论内容 + 已注册 harness 列表，选定员工填入名单。只输出 JSON，不要解释。\n"
        '输出格式：{"members":[{"role":"","display_name":"","harness_ids":[""]}]}\n'
        "规则：组长必须有（负责设计/分配/检查/纠错）；职业从 组长/码农/画师/搜索者 里选（按任务需要）；"
        "display_name 和 harness_ids 用已注册 harness 的完整名；没有合适 harness 的职业就不填。"
    )
    try:
        reply = await ai_external_run_ai_call(
            _sv().ai_provider.chat(system, f"已注册 harness：\n{harness_desc}\n\n{context}"),
            label="server.member_select",
        )
        start, end = reply.find("{"), reply.rfind("}") + 1
        data = json.loads(reply[start:end]) if start >= 0 and end > start else {}
    except Exception as e:
        return {"success": False, "error": f"选定失败: {e}"}
    members_raw = data.get("members", [])
    valid_harness_ids = {h["harness_id"] for h in harnesses}
    cleaned = []
    for m in members_raw:
        if not isinstance(m, dict):
            continue
        role = str(m.get("role", "") or "").strip()
        if not role or role in ("??", "选择职位", "null", "None"):
            role = "员工"
        dname = str(m.get("display_name", "") or "").strip()
        if not dname or dname in ("??", "null", "None"):
            dname = ""
        hids = [h for h in (m.get("harness_ids") or []) if isinstance(h, str) and h and h not in ("??", "null", "None") and h in valid_harness_ids]
        cleaned.append({"role": role, "display_name": dname, "harness_ids": hids})
    ws.members = [
        WorkshopMember(
            member_id=f"m{i}",
            role=m["role"],
            display_name=m["display_name"],
            harness_ids=m["harness_ids"],
        )
        for i, m in enumerate(cleaned)
    ]
    ws.status = "selecting"
    _selected_note = {} if cleaned else {"reason": "AI 未选出合适成员：可能已注册 harness 与任务需求不匹配或 AI 返回空名单，请人工补充名单"}
    return {
        "success": True,
        "members": [
            {"member_id": m.member_id, "role": m.role, "display_name": m.display_name, "harness_ids": m.harness_ids}
            for m in ws.members
        ],
        **_selected_note,
    }

@router.post("/api/workshop/{ws_id}/members")
async def workshop_save_members(ws_id: str, request: Request):
    from ..server import Utf8JSONResponse, _activate_single, save_state
    """前端增删改名单后即时同步。
    - 讨论/选定阶段：仅更新名单，不改变工作间状态；
    - 运行中（running）：增量同步 —— 新增/换绑成员立即激活（HA 专属提示词），
      被移除成员终止接入（清空未领取的激活队列、释放平台侧会话引用）。
    """
    ws = workshops.get(ws_id)
    if not ws:
        return Utf8JSONResponse({"error": "工作间不存在"}, status_code=404)
    body = await request.json()
    members_raw = body.get("members") or []
    new_members = [
        WorkshopMember(
            member_id=f"m{i}",
            role=m.get("role", "员工"),
            display_name=m.get("display_name", ""),
            harness_ids=m.get("harness_ids", []),
        )
        for i, m in enumerate(members_raw)
    ]
    added, removed = [], []
    if ws.status == "running":
        # 以 harness 绑定（hid）为准做接入 diff：成员身份=绑定哪个 HA
        old_hid2m = {(m.harness_ids or [None])[0]: m for m in ws.members if (m.harness_ids or [None])[0]}
        new_hid2m = {(m.harness_ids or [None])[0]: m for m in new_members if (m.harness_ids or [None])[0]}
        removed_hids = set(old_hid2m) - set(new_hid2m)
        added_hids = set(new_hid2m) - set(old_hid2m)
        # 被移除的 HA：终止接入（清激活队列 + 释放会话引用）
        for hid in removed_hids:
            old = old_hid2m[hid]
            removed.append({
                "member_id": old.member_id,
                "display_name": old.display_name,
                "role": old.role,
                "harness_ids": old.harness_ids,
            })
            pending_activations[hid] = [
                a for a in pending_activations.get(hid, [])
                if not (a.get("workshop_id") == ws.workshop_id and a.get("member_id") == old.member_id)
            ]
            old.session = None
            old.status = "removed"
        # 新增接入的 HA：立即激活（HA 专属提示词）；保留成员复制旧状态
        for m in new_members:
            hid = (m.harness_ids or [None])[0]
            if hid in added_hids:
                m.status = "activating"
                _activate_single(ws, m)
                added.append({
                    "member_id": m.member_id,
                    "display_name": m.display_name,
                    "role": m.role,
                    "harness_ids": m.harness_ids,
                })
            elif hid in old_hid2m:
                m.status = old_hid2m[hid].status
                m.session = old_hid2m[hid].session
    ws.members = new_members
    save_state()
    return {
        "success": True,
        "members": [m.member_id for m in ws.members],
        "added": added,
        "removed": removed,
    }

@router.post("/api/workshop/{ws_id}/confirm-members")
async def workshop_confirm_members(ws_id: str, request: Request):
    from ..server import Utf8JSONResponse, _activate_leader_if_needed, _append_msg, save_state
    """确认（可能已修改的）员工名单，进入二级讨论（明确分工）。"""
    ws = workshops.get(ws_id)
    if not ws:
        return Utf8JSONResponse({"error": "工作间不存在"}, status_code=404)
    body = await request.json()
    members_raw = body.get("members") or []
    if members_raw:
        if not isinstance(members_raw, list) or not all(isinstance(m, dict) for m in members_raw):
            return Utf8JSONResponse({"error": "members 需为对象数组（每个元素需含 role/display_name/harness_ids）"}, status_code=400)
        ws.members = [
            WorkshopMember(
                member_id=f"m{i}",
                role=m.get("role", "员工"),
                display_name=m.get("display_name", ""),
                harness_ids=m.get("harness_ids", []),
            )
            for i, m in enumerate(members_raw)
        ]
    ws.status = "division"
    # 自治边界：进入分工讨论 → discussing 态
    task_state_machine.set_state(ws_id, DISCUSSING, stage="division")
    _append_msg(ws, "notice", "【二级讨论】员工名单已确认，进入分工讨论。组长将接管对话：请等待组长的真实发言，或直接在下方输入消息与组长讨论分工；组长的回复会实时出现在这里。", zone=2)
    save_state()
    reply0, ok0, _leader0 = _activate_leader_if_needed(ws)
    if reply0 and not ok0:
        _append_msg(ws, "notice", reply0, zone=2)
    return {"success": True, "status": ws.status, "leader_activated": ok0}

@router.get("/api/workshop/{ws_id}/leader-workbench")
async def leader_workbench(ws_id: str):
    from ..server import Utf8JSONResponse, _idea_bag, _paused_members, _pending_reviews
    """组长工作台数据：暂停列表 / 点子袋 / 待裁决队列 / 状态机状态。"""
    ws = workshops.get(ws_id)
    if not ws:
        return Utf8JSONResponse({"error": "工作间不存在"}, status_code=404)
    return {
        "success": True,
        "paused": _paused_members(ws),
        "idea_bag": _idea_bag(ws),
        "pending_reviews": _pending_reviews(ws),
        "state": task_state_machine.get_state(ws_id),
    }

