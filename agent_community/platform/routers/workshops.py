"""platform/routers/workshops：工作间全生命周期端点组（V-9 自 server.py 拆分）。

拆分原则：
- 可变全局状态统一引用 ..state（python -m 双副本共享同一组对象）
- server 内部辅助函数/常量在函数体内延迟 from ..server import（避免循环导入）
- ai_provider / ai_provider_config 由 __main__ 副本持有，经 _sv() 访问
"""
from __future__ import annotations

import sys

from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect

router = APIRouter()


def _sv():
    """返回活跃 server 副本（__main__，uvicorn 实际运行的实例）模块。"""
    return sys.modules["__main__"]


# ── 独立模块引用（与 server.py 共享同一实例）──
from ..task_state_machine import CREATED
from ..task_state_machine import DISCUSSING
from ..task_state_machine import EV_COMPLETE
from ..task_state_machine import EV_DROP
from ..task_state_machine import EV_RESOLVE
from ..task_state_machine import EV_RESUME
from ..task_state_machine import EV_TIMEOUT
from ..task_state_machine import EXECUTING
from ..protocol import Message
from ..protocol import MessageType
from pathlib import Path
from ..protocol import Task
from ..protocol import TaskStatus
from ..workshop import Workshop
from ..workshop import WorkshopMember
from ..experience_v2 import harness_reputation_bonus as _v2_harness_reputation_bonus
from ..experience_v2 import evaluate_workshop_completion as _v2_evaluate_workshop_completion
from ..audit import audit_log as _audit_log
from ..ai_external import run_ai_call as ai_external_run_ai_call
import asyncio
from ..memory import capability_ledger
from ..harness_adapter import harness_manager
import json
import os
from ..task_state_machine import should_interject
from ..memory import task_memory
from uuid import uuid4
from ..workshop import write_resources_manifest
from ..workshop import write_workspace_files

# ── 共享可变状态（..state 单例，双副本一致）──
from ..state import interject_store, pending_activations, task_state_machine, tasks, workshops

@router.get("/api/workshop/{ws_id}/mode")
async def api_workshop_mode_get(ws_id: str):
    from ..server import _load_workshop_modes
    modes = _load_workshop_modes()
    return {"workshop_id": ws_id, "mode": modes.get(ws_id, "standard")}

@router.post("/api/workshop/{ws_id}/mode")
async def api_workshop_mode_set(ws_id: str, request: Request):
    from ..server import PLUGIN_MODES_FILE, Utf8JSONResponse, _load_workshop_modes
    body = await request.json()
    mode = str(body.get("mode") or "").strip()
    if not mode:
        return Utf8JSONResponse({"error": "mode 不能为空"}, status_code=400)
    modes = _load_workshop_modes()
    modes[ws_id] = mode
    PLUGIN_MODES_FILE.write_text(json.dumps(modes, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"success": True, "workshop_id": ws_id, "mode": mode}

@router.get("/api/workshop/{ws_id}/parallel_limit")
async def api_workshop_parallel_limit_get(ws_id: str):
    from ..server import Utf8JSONResponse, _parallel_limit
    ws = workshops.get(ws_id)
    if not ws:
        return Utf8JSONResponse({"error": "工作间不存在"}, status_code=404)
    max_n = max(1, len(ws.members))
    return {"workshop_id": ws_id, "parallel_limit": _parallel_limit(ws_id, len(ws.members)), "max": max_n}

@router.post("/api/workshop/{ws_id}/parallel_limit")
async def api_workshop_parallel_limit_set(ws_id: str, request: Request):
    from ..server import PLUGIN_MODES_FILE, Utf8JSONResponse, _load_workshop_modes
    ws = workshops.get(ws_id)
    if not ws:
        return Utf8JSONResponse({"error": "工作间不存在"}, status_code=404)
    body = await request.json()
    try:
        n = int(body.get("limit") or 0)
    except Exception:
        n = 0
    max_n = max(1, len(ws.members))
    if n < 1:
        return Utf8JSONResponse({"error": "并行度需 ≥1"}, status_code=400)
    n = min(n, max_n)
    modes = _load_workshop_modes()
    modes[f"{ws_id}:parallel_limit"] = n
    PLUGIN_MODES_FILE.write_text(json.dumps(modes, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"success": True, "workshop_id": ws_id, "parallel_limit": n}

@router.post("/api/workshop")
async def create_workshop(request: Request):
    from ..server import MAX_WORKSHOPS, Utf8JSONResponse, WORKSHOP_BASE, now_iso, save_state
    body = await request.json()
    name = (body.get("name") or "").strip()
    hall = body.get("hall_content", "") or ""
    # 工作间以工作内容命名：未传 name 或仍为默认值时，用任务内容截断
    if not name or name in ("员工工作间", "未命名工作间", "默认工作间"):
        name = hall.strip().replace("\n", " ")[:20] or "未命名工作间"
    ws = Workshop(
        workshop_id=uuid4().hex[:8],
        name=name,
        workspace_dir=str(WORKSHOP_BASE / f"ws_{uuid4().hex[:8]}"),
        hall_content=hall,
        members=[
            WorkshopMember(
                member_id=f"m{i}",
                role=m.get("role", "员工"),
                display_name=m.get("display_name", "dsh"),
                harness_ids=m.get("harness_ids", ["dsh"]),
            )
            for i, m in enumerate(body.get("members") or [])
        ],
        created_at=now_iso(),
    )
    # V-13 修复：工作间总数上限，防无限创建耗尽磁盘（每个工作间含工作区目录文件）
    if len(workshops) >= MAX_WORKSHOPS:
        return Utf8JSONResponse({"error": f"工作间总数已达上限 {MAX_WORKSHOPS}，请清理旧工作间后再试"}, status_code=429)
    workshops[ws.workshop_id] = ws
    # 落盘工作区文件（hall.md=任务 / AGENTS.md=流程指引）：外端 harness 激活协议依赖
    # 读取 hall.md 完成对接，缺失会导致激活永远无法完成（历史 bug：write_workspace_files
    # 仅被 import 从未接线，新建工作间目录长期为空）。
    try:
        write_workspace_files(ws)
    except Exception as _e:
        print(f"[workshop] 工作区文件写入失败: {_e}", flush=True)
    # P2-1 审计：工作间创建
    _audit_log.record("workshop.create", actor="user", target=ws.workshop_id, detail=ws.name)
    save_state()
    return {"success": True, "workshop_id": ws.workshop_id, "workspace_dir": ws.workspace_dir}

@router.get("/api/workshops")
async def list_workshops():
    """列出所有工作间（大厅侧边栏用）：置顶优先，组内按创建时间倒序。"""
    items = [w.to_dict() for w in workshops.values()]
    items.sort(key=lambda w: (not w["pinned"], w.get("created_at", "")) if w.get("created_at") else (not w["pinned"], ""))
    # 置顶组内按 created_at 倒序
    pinned = sorted([i for i in items if i["pinned"]], key=lambda w: w.get("created_at", ""), reverse=True)
    normal = sorted([i for i in items if not i["pinned"]], key=lambda w: w.get("created_at", ""), reverse=True)
    return {"workshops": pinned + normal}

@router.patch("/api/workshop/{ws_id}")
async def rename_workshop(ws_id: str, request: Request):
    from ..server import Utf8JSONResponse, save_state
    """重命名工作间。"""
    ws = workshops.get(ws_id)
    if not ws:
        return Utf8JSONResponse({"error": "工作间不存在"}, status_code=404)
    body = await request.json()
    new_name = (body.get("name") or "").strip()
    if not new_name:
        return Utf8JSONResponse({"error": "name 不能为空"}, status_code=400)
    ws.name = new_name
    save_state()
    return {"success": True, "workshop_id": ws_id, "name": ws.name}

@router.post("/api/workshop/{ws_id}/pin")
async def pin_workshop(ws_id: str, request: Request):
    from ..server import Utf8JSONResponse, save_state
    """置顶/取消置顶工作间。"""
    ws = workshops.get(ws_id)
    if not ws:
        return Utf8JSONResponse({"error": "工作间不存在"}, status_code=404)
    try:
        body = await request.json()
    except Exception:
        body = {}
    ws.pinned = bool(body.get("pinned", not ws.pinned))
    save_state()
    return {"success": True, "workshop_id": ws_id, "pinned": ws.pinned}

@router.delete("/api/workshop/{ws_id}")
async def delete_workshop(ws_id: str):
    from ..server import Utf8JSONResponse, _trash_bridge_dir, save_state
    """删除工作间（含持久化记录 + 状态机记录 + 工作区目录进回收站）。

    V-17 增强（2026-09-24）：删除时同步回收——状态机记录移除、工作区目录
    （data/workshops/ws_xxx）移入回收站（可恢复），避免残留卡死记录与孤儿目录。
    """
    ws = workshops.pop(ws_id, None)
    if not ws:
        return Utf8JSONResponse({"error": "工作间不存在"}, status_code=404)
    # 1) 状态机记录清理：工作间彻底退出状态机（含 timeout/discussing 卡死残留）
    sm_removed = task_state_machine.remove(ws_id)
    # 2) 插话池清理：同步移除该工作间全部插话条目（防 interjects.json 脏数据残留）
    #    V-18 修复（缺陷 D）：删除工作间不再遗留插话池条目
    it_removed = interject_store.remove_workshop(ws_id)
    # 3) 工作区目录移入回收站（不物理删除，可恢复）
    trash_result = await asyncio.to_thread(_trash_bridge_dir, ws.workspace_dir) \
        if ws.workspace_dir and os.path.isdir(ws.workspace_dir) else {"trashed": False, "reason": "目录不存在或未记录"}
    save_state()
    return {"success": True, "deleted": ws_id, "state_machine_removed": sm_removed,
            "interjects_removed": it_removed, "trash": trash_result}

@router.get("/api/workshops/stale")
async def list_stale_workshops():
    from ..server import _is_stale_workshop
    """列出卡死工作间候选（不删除，供前端展示确认）。"""
    cands = []
    for ws in workshops.values():
        j = _is_stale_workshop(ws)
        if j["stale"]:
            cands.append({
                "workshop_id": ws.workshop_id,
                "name": ws.name,
                "status": ws.status,
                "created_at": ws.created_at,
                "reason": j["reason"],
                "discussion_count": len(ws.discussion),
                "member_count": len(ws.members),
            })
    cands.sort(key=lambda x: x.get("created_at", ""))
    return {"success": True, "stale_count": len(cands), "stale": cands}

@router.post("/api/workshops/recycle-stale")
async def recycle_stale_workshops(request: Request):
    from ..server import Utf8JSONResponse, _is_stale_workshop, _trash_bridge_dir, save_state
    """批量回收卡死工作间：body 传 {"ws_ids": [...]} 或 {"all": true}。

    每个工作间执行与 DELETE /api/workshop/{ws_id} 相同的回收语义：
    移除工作间记录 + 状态机记录清理 + 工作区目录进回收站（可恢复）。
    不终止共享 harness 桥进程（可能被其他工作间复用）。
    """
    try:
        body = await request.json() or {}
    except Exception:
        body = {}
    ws_ids = body.get("ws_ids") or []
    all_flag = bool(body.get("all"))
    if not ws_ids and not all_flag:
        return Utf8JSONResponse({"error": "需传 ws_ids 列表或 all=true"}, status_code=400)
    targets = list(workshops.keys()) if all_flag else [x for x in ws_ids if x in workshops]
    if not targets:
        return {"success": True, "recycled": [], "skipped": ws_ids}
    recycled, skipped = [], []
    for wid in targets:
        ws = workshops.get(wid)
        if not ws:
            skipped.append({"workshop_id": wid, "reason": "not_found"})
            continue
        if ws.pinned:
            skipped.append({"workshop_id": wid, "reason": "pinned"})
            continue
        j = _is_stale_workshop(ws)
        if not j["stale"]:
            skipped.append({"workshop_id": wid, "reason": f"not_stale({j['reason']})"})
            continue
        sm_removed = task_state_machine.remove(wid)
        # V-18 修复（缺陷 D）：回收卡死工作间同样同步清理插话池
        it_removed = interject_store.remove_workshop(wid)
        trash_result = await asyncio.to_thread(_trash_bridge_dir, ws.workspace_dir) \
            if ws.workspace_dir and os.path.isdir(ws.workspace_dir) else {"trashed": False, "reason": "目录不存在或未记录"}
        workshops.pop(wid, None)
        recycled.append({"workshop_id": wid, "name": ws.name,
                         "state_machine_removed": sm_removed, "interjects_removed": it_removed,
                         "trash": trash_result})
    save_state()
    return {"success": True, "recycled": recycled, "skipped": skipped}

@router.get("/api/workshop/{ws_id}")
async def get_workshop(ws_id: str, after_seq: int = -1):
    from ..server import Utf8JSONResponse, _normalize_discussion
    ws = workshops.get(ws_id)
    if not ws:
        return Utf8JSONResponse({"error": "工作间不存在"}, status_code=404)
    return {
        "workshop_id": ws.workshop_id,
        "name": ws.name,
        "status": ws.status,
        "workspace_dir": ws.workspace_dir,
        "hall_content": ws.hall_content,
        "discussion": _normalize_discussion(ws, after_seq=after_seq),
        "members": [{"member_id": m.member_id, "role": m.role, "display_name": m.display_name, "harness_ids": m.harness_ids, "status": m.status} for m in ws.members],
        "resources": ws.resources,
    }

@router.post("/api/workshop/{ws_id}/discuss")
async def workshop_discuss(ws_id: str, request: Request):
    from ..server import Utf8JSONResponse, _activate_by_hooks, _append_msg, _discussion_ctx, _dispatch_leader_review_discuss, _leader_division_discuss, _member_progress_summary, _review_sufficiency_check
    ws = workshops.get(ws_id)
    if not ws:
        return Utf8JSONResponse({"error": "工作间不存在"}, status_code=404)
    body = await request.json()
    user_msg = (body.get("message") or "").strip()
    if not user_msg:
        return Utf8JSONResponse({"error": "消息不能为空"}, status_code=400)
    # V-25 F3：用户消息上传工作间前先做敏感数据扫描，命中即报错拒绝写入（不落讨论区）
    from ..server import _scan_sensitive
    _hit, _kind = _scan_sensitive(user_msg)
    if _hit:
        return Utf8JSONResponse(
            {"error": f"检测到敏感数据（{_kind}），已拒绝上传工作间", "success": False},
            status_code=400,
        )
    if ws.status not in ("division", "review", "running"):
        ws.status = "discussing"
    _u = _append_msg(ws, "user", user_msg)
    user_zone = _u["zone"]
    # ── 钩子检测（L1/L2）：用户消息点名即激活对应成员 ────────
    hook_notes = _activate_by_hooks(ws, user_msg, "用户")
    if hook_notes:
        note_text = "\n".join(hook_notes)
        _append_msg(ws, "orchestrator", "【钩子激活】" + note_text, zone=user_zone)
    # ── 二级讨论：平台 AI 退出，组长接管 ──────────────────────
    if ws.status == "division":
        return await _leader_division_discuss(ws, user_msg)
    # ── 一级 / 三级讨论：平台 AI（Orchestrator）参与 ──────────
    if not _sv().ai_provider:
        return Utf8JSONResponse({"error": "AI Provider 未配置"}, status_code=400)
    # 三级联动：三级讨论（review）时，AI 的 context 在讨论历史之前叠加「当前成员进度汇总」
    if ws.status == "review":
        roster = "\n".join(f"- {m.role}（{m.display_name}）" for m in ws.members)
        context = (
            f"任务（大厅内容）：\n{ws.hall_content}\n\n"
            f"当前成员进度汇总：\n{_member_progress_summary(ws)}\n\n"
            "讨论历史（最近10条）：\n" + _discussion_ctx(ws, limit=10)
            + f"\n当前员工名单：\n{roster}\n"
        )
        system = (
            "你是 外端Agent生产合作社（External Agent Community） 平台的进度讨论伙伴（Orchestrator）。"
            "工作已进行一个阶段，现在进入三级讨论（阶段复盘）：平台 AI 主持，与用户、组长、员工同台讨论。"
            "组长会以「汇报+讨论」角色实时参与（汇报本阶段进展/卡点/下一步）；员工的回报也会实时出现。"
            "你负责主持讨论：围绕进展如何、有没有卡点、员工状态、下一步方向与用户自然讨论。"
            "像真同事自然讨论，不要问卷式。用中文，回复简洁但完整。"
            "当你判断可以继续推进时，在回复末尾写一句：可以继续工作；当你判断任务已完成时，写一句：任务已完成。"
            # 服务端依赖上述结尾标记句触发 action 流转（去文本化兼容），AI 回复必须原样保留标记句。
        )
    else:
        context = f"任务（大厅内容）：\n{ws.hall_content}\n\n讨论历史（最近10条）：\n" + _discussion_ctx(ws, limit=10)
        system = (
            "你是 外端Agent生产合作社（External Agent Community） 平台的讨论伙伴（Orchestrator）。像一位有经验的技术同事那样和用户自然讨论需求，"
            "不要做成问卷/选择题。做法：先用自己的话复述对任务的理解，主动抛出你的分析、初步设想、可能的坑和权衡，"
            "再用开放式问题引导用户发散补充（不要只列 A/B/C 选项）。对话有来有回，像真人在聊。"
            "用中文，回复简洁但完整。"
            "当你判断需求已经足够清楚、可以进入「选定员工」阶段时，在回复末尾写一句：需求已明确，可以选定员工。"
            # 服务端依赖上述结尾标记句触发 action 流转（去文本化兼容），AI 回复必须原样保留标记句。
        )
    try:
        reply = await ai_external_run_ai_call(
            _sv().ai_provider.chat(system, context),
            label="server.discussion_reply",
        )
    except Exception as e:
        reply = f"[AI 讨论失败: {e}]"
    # V-25 F4：AI key 失效等错误文本（[Error: HTTP 401] / [AI 讨论失败: / [AI 降级]）不再静默写入讨论区
    # 冒充正常回复（此前返回 200 但流转静默失效）；改为显式报错，由前端展示失败原因，绝不落讨论区。
    _ai_failed = str(reply or "").startswith(("[Error:", "[AI 讨论失败:", "[AI 降级]"))
    if _ai_failed:
        print(f"[discuss] AI 调用失败，已显式报错（不再静默写入讨论区）: {str(reply)[:200]}", flush=True)
        return Utf8JSONResponse(
            {"success": False, "error": "AI 服务不可用（密钥失效或后端错误）", "detail": str(reply)[:300], "status": ws.status},
            status_code=502,
        )
    _append_msg(ws, "orchestrator", reply, zone=user_zone)
    # 三级讨论同台：平台 AI 回复后，同步把用户消息派发给组长 harness 实时参与讨论
    # （组长路径转向：二级讨论组长是指挥者；三级讨论组长转为「汇报+讨论」参与者，与平台 AI / 用户 / 员工同台）
    if ws.status == "review":
        _dispatch_leader_review_discuss(ws, user_msg)
        # 讨论充分性判定（补齐缺口1）：全员参与 + AI 已回复 → notice 提示可裁决流转
        _review_sufficiency_check(ws)
    # 阶段流转去文本化：由服务端按原有关键词判定 action（与 reply 文本并存，前端优先读 action）
    action = "none"
    if "任务已完成" in reply:
        action = "complete"
    elif "可以继续工作" in reply:
        action = "continue"
    elif "分工已明确，可以开始工作" in reply:
        action = "start"
    elif "需求已明确，可以选定员工" in reply:
        action = "select"
    return {"success": True, "reply": reply, "status": ws.status, "stage": "orchestrator", "zone": user_zone, "action": action}

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


# ── 组长转向 / 委派细化能力（V-19）──────────────────────────────
# 背景缺口：review 态已实现组长同台派发，但组长「将任务转向其他成员 / 细化派发指令 /
# 调整成员任务」无专属端点，前端亦无入口。以下端点最小侵入补齐：
# - POST /api/workshop/{ws_id}/leader/assign   组长向指定成员细化/调整派发指令
# - POST /api/workshop/{ws_id}/leader/redirect 组长将任务从一名成员转向另一名成员
# - GET  /api/workshop/{ws_id}/leader/assignments 查看组长派发指令/转向记录
# 实现复用既有 _dispatch_to_harness（HTTP/file_poll/pending 统一派发）与 _append_msg
# （讨论区唯一写入口），持久化沿用 save_state（ws.assignments 落 workshops.json）。

def _leader_pick_member(ws, member_id: str):
    """按 member_id 从工作间中取成员，找不到返回 None。"""
    if not member_id:
        return None
    for m in ws.members:
        if m.member_id == member_id:
            return m
    return None


def _leader_member_hid(member) -> str:
    """取成员首个 harness_id；未绑定返回空串。"""
    return (member.harness_ids or [None])[0] or ""


def _leader_build_assign_payload(ws, member, instruction: str, note: str, kind: str) -> dict:
    """构造组长委派/转向 payload（与 _leader_division_discuss 同构，供 _dispatch_to_harness 使用）。"""
    hid = _leader_member_hid(member)
    if kind == "redirect":
        label = "任务转向·承接"
        body = (
            "你是本工作间的员工，组长刚把一项任务转交给你承接，请立即接手并推进。\n"
            "【转向指令】" + instruction + ("\n【组长备注】" + note if note else "") + "\n\n"
            "工作区实时路径：" + ws.workspace_dir + "\n"
            "任务（大厅内容）：\n" + ws.hall_content
        )
    else:
        label = "组长委派细化"
        body = (
            "你是本工作间的员工，组长刚给你下发/调整了细化工作指令，请按指令执行或调整当前工作。\n"
            "【细化指令】" + instruction + ("\n【组长备注】" + note if note else "") + "\n\n"
            "工作区实时路径：" + ws.workspace_dir + "\n"
            "任务（大厅内容）：\n" + ws.hall_content
        )
    return {
        "type": "leader_assign" if kind == "assign" else "leader_redirect",
        "kind": kind,
        "workshop_id": ws.workshop_id,
        "member_id": member.member_id,
        "role": member.role,
        "workspace_dir": ws.workspace_dir,
        "instruction": instruction,
        "report_endpoint": "/api/harness/task-result",
        "message": body + (
            "\n\n【回报要求】处理完必须把结果回报给平台，否则用户看不到：\n"
            "POST " + _platform_base_url_safe() + "/api/harness/task-result\n"
            "body: {\"workshop_id\":\"" + ws.workshop_id + "\",\"member_id\":\"" + member.member_id
            + "\",\"harness_id\":\"" + hid + "\",\"ok\":true,\"result\":\"你的完整回复/结果\"}\n"
            "把你的回复内容放进 result 字段回报上去。"
        ),
    }


def _platform_base_url_safe() -> str:
    """取平台基准地址，避免重复 import 冲突（函数体内延迟导入）。"""
    from ..server import _platform_base_url
    try:
        return _platform_base_url()
    except Exception:
        return "http://127.0.0.1:18920"


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

@router.post("/api/workshop/{ws_id}/review")
async def workshop_review(ws_id: str):
    from ..server import Utf8JSONResponse, _append_msg, save_state
    """进入三级讨论（依进度讨论）。"""
    ws = workshops.get(ws_id)
    if not ws:
        return Utf8JSONResponse({"error": "工作间不存在"}, status_code=404)
    ws.status = "review"
    # 自治边界：进入三级讨论 → discussing 态
    task_state_machine.set_state(ws_id, DISCUSSING, stage="review")
    ws._review_notified = False  # 新进入 review：允许 poll 兜底唤醒组长一次
    ws._auto_review_done = False  # 手动进入：重置自动触发防重标记，允许后续阶段再次自动进入
    ws._review_suff_notified = False  # 重置充分性判定防重标记
    _append_msg(ws, "notice", "【三级讨论】一个阶段工作已告一段落，进入阶段复盘。规则：① 平台 AI 主持，与用户、组长、员工同台讨论；② 组长转为「汇报+讨论」角色，汇报本阶段进展/卡点/下一步；③ 员工的回报会实时出现在这里；④ 讨论充分后，平台 AI 会判定进入「继续工作」或「任务已完成」。请说说这一阶段的进展：完成得怎么样、有没有卡点、下一步想怎么走？", zone=3)
    # P2-1 审计：进入三级讨论
    _audit_log.record("workshop.review", actor="user", target=ws_id, detail="进入三级讨论")
    save_state()
    return {"success": True, "status": ws.status, "action": "review"}

@router.post("/api/workshop/{ws_id}/continue")
async def workshop_continue(ws_id: str):
    from ..server import Utf8JSONResponse, _append_msg, _notify_leader_on_continue, _write_review_archive, save_state
    """三级讨论后继续工作。"""
    ws = workshops.get(ws_id)
    if not ws:
        return Utf8JSONResponse({"error": "工作间不存在"}, status_code=404)
    ws.status = "running"
    _append_msg(ws, "notice", "【继续工作】已回到工作状态。员工继续执行，进展与回报会实时出现在二级讨论区。", zone=2)
    # 讨论产出物归档（补齐缺口3）：三级讨论内容落盘 REVIEW.md
    _write_review_archive(ws, "continue")
    save_state()
    # 三级联动：continue 端点喂组长 harness 的 context 前叠加「当前成员进度汇总」，让组长带进度继续指挥
    _notify_leader_on_continue(ws)
    return {"success": True, "status": ws.status, "action": "running"}

@router.post("/api/workshop/{ws_id}/complete")
async def workshop_complete(ws_id: str):
    from ..server import Utf8JSONResponse, _write_review_archive, save_state
    """标记任务完成。"""
    ws = workshops.get(ws_id)
    if not ws:
        return Utf8JSONResponse({"error": "工作间不存在"}, status_code=404)
    ws.status = "done"
    # 自治边界：全部完成 → done 终态
    task_state_machine.on_event(ws_id, EV_COMPLETE, {"by": "user"})
    # 讨论产出物归档（补齐缺口3）：三级讨论内容落盘 FINAL_SUMMARY.md
    _write_review_archive(ws, "complete")
    # V2-3：完成评估闭环（观察 → 评估 → 沉淀 → 复用，失败不阻塞完成）
    _v2_evaluate_workshop_completion(
        ws, task_memory=task_memory, capability_ledger=capability_ledger,
        harness_manager=harness_manager,
    )
    # P2-1 审计：工作间完成
    _audit_log.record("workshop.complete", actor="user", target=ws_id, detail="complete")
    save_state()
    return {"success": True, "status": ws.status, "action": "complete"}

@router.post("/api/workshop/{ws_id}/start")
async def start_workshop(ws_id: str):
    from ..server import Utf8JSONResponse, _activate_workshop
    ws = workshops.get(ws_id)
    if not ws:
        return Utf8JSONResponse({"error": "工作间不存在"}, status_code=404)
    if ws.status == "running":
        return {"success": True, "status": ws.status, "action": "running", "note": "已在运行"}
    ws.status = "running"
    # 自治边界：进入工作 → executing 态
    task_state_machine.set_state(ws_id, EXECUTING, stage="running")
    asyncio.create_task(_activate_workshop(ws))
    return {"success": True, "status": ws.status, "action": "running"}

@router.get("/api/workshop/{ws_id}/decision-mode")
async def get_decision_mode_api(ws_id: str):
    from ..server import Utf8JSONResponse, _get_decision_mode
    ws = workshops.get(ws_id)
    if not ws:
        return Utf8JSONResponse({"error": "工作间不存在"}, status_code=404)
    return {"success": True, "workshop_id": ws_id, "decision_mode": _get_decision_mode(ws_id)}

@router.post("/api/workshop/{ws_id}/decision-mode")
async def set_decision_mode_api(ws_id: str, request: Request):
    from ..server import Utf8JSONResponse, _set_decision_mode
    ws = workshops.get(ws_id)
    if not ws:
        return Utf8JSONResponse({"error": "工作间不存在"}, status_code=404)
    body = await request.json()
    mode = _set_decision_mode(ws_id, str(body.get("mode") or ""))
    return {"success": True, "workshop_id": ws_id, "decision_mode": mode}

@router.post("/api/workshop/{ws_id}/halt-and-reset")
async def workshop_halt_and_reset(ws_id: str, request: Request):
    from ..server import Utf8JSONResponse, _EXPERIENCE_INDEX_KEYS, _dispatch_to_harness, now_iso, save_state
    """确认走弯路：停止任务 → 反思总结入资料库 → 擦除成员/组长记忆 → 从头开始。

    body:
      reason: str         弯路原因（必填，写入复盘文档）
      summary: str        经验总结（选填）
      wipe_members: bool  是否擦除员工成员记忆（默认 true）
      wipe_leader: bool   是否擦除组长记忆（默认 false）
      restart: bool       是否重置工作间回 draft 可重新开始（默认 true）
    """
    ws = workshops.get(ws_id)
    if not ws:
        return Utf8JSONResponse({"error": "工作间不存在"}, status_code=404)
    body = await request.json()
    reason = str(body.get("reason") or "").strip()
    if not reason:
        return Utf8JSONResponse({"error": "缺少 reason（弯路原因）"}, status_code=400)
    summary = str(body.get("summary") or "").strip()
    wipe_members = bool(body.get("wipe_members", True))
    wipe_leader = bool(body.get("wipe_leader", False))
    restart = bool(body.get("restart", True))

    results = {"discussion_cleared": False, "task_memory_removed": 0, "harness_index_cleared": []}

    # 1. 停止任务 + 状态机重置
    ws.status = "draft"
    task_state_machine.set_state(ws_id, CREATED, stage="halt_reset")
    for m in ws.members:
        m.status = "pending"
    for attr in (
        "_auto_review_done", "_review_suff_notified", "_assign_dispatched",
        "_vote_tally", "_vote_invited", "_review_notified", "_leader_ack_seq",
        "_parallel_done",
    ):
        try:
            if hasattr(ws, attr):
                delattr(ws, attr)
        except Exception:
            pass

    # 2. 反思总结归档（DETOUR_SUMMARY.md）
    ws_dir = Path(ws.workspace_dir)
    try:
        ws_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    detour_path = ws_dir / "DETOUR_SUMMARY.md"
    try:
        lines = [
            "# 弯路复盘总结（纠偏记录）", "",
            f"- 工作间：{ws.name}（{ws.workshop_id}）",
            f"- 归档时间：{now_iso()}",
            "",
            "## 错误路径（已否定）", "",
            f"{reason}", "",
            "## 正确路径（推荐）", "",
            f"{summary or '（未总结，建议按错误路径反推正确做法）'}", "",
            "## 重置前最后讨论记录", "",
        ]
        recent = [m for m in ws.discussion if isinstance(m, dict) and not m.get("meta", {}).get("system")][-20:]
        if not recent:
            lines.append("（无讨论记录）")
        else:
            for m in recent:
                who = m.get("display_name") or m.get("role") or "未知"
                ts = str(m.get("timestamp") or "")[:19]
                lines.append(f"- [{ts}] {who}：{str(m.get('content'))[:300]}")
        detour_path.write_text("\n".join(lines), encoding="utf-8")
    except Exception as _e:
        print(f"[halt_reset] 弯路复盘归档失败: {_e}", flush=True)

    # 2b. 任务树落纠偏节点：错误路(dropped) + 正路(active)，复盘资源挂正路
    wrong_node = {
        "node_id": f"t_{uuid4().hex[:8]}",
        "label": f"错误路径（已否定）：{reason[:40]}",
        "parent_id": "",
        "kind": "correction",
        "status": "dropped",
        "note": reason,
        "resources": [],
        "created_at": now_iso(),
    }
    right_node = {
        "node_id": f"t_{uuid4().hex[:8]}",
        "label": f"正确路径（推荐）：{summary[:40] if summary else '按错误路径反推正确做法'}",
        "parent_id": "",
        "kind": "correction",
        "status": "active",
        "note": summary or "",
        "resources": [],
        "created_at": now_iso(),
    }
    ws.task_tree.append(wrong_node)
    ws.task_tree.append(right_node)
    results["tree"] = {"wrong_node": wrong_node["node_id"], "right_node": right_node["node_id"]}

    # 3. 入工作间资料库（自动登记，挂正路节点）
    try:
        detour_res = {
            "rid": uuid4().hex[:8],
            "name": "弯路复盘总结",
            "kind": "file",
            "path": str(detour_path),
            "note": f"确认走弯路后的反思总结：{reason[:60]}",
            "uploader": "platform",
            "task_node": right_node["node_id"],
            "created_at": now_iso(),
        }
        ws.resources.append(detour_res)
        right_node.setdefault("resources", []).append(detour_res["rid"])
        write_resources_manifest(ws)
    except Exception as _e:
        print(f"[halt_reset] 资料库登记失败: {_e}", flush=True)

    # 4. 擦除记忆
    if wipe_members or wipe_leader:
        # 4a. 讨论上下文清零（工作间会话记忆）
        ws.discussion = []
        results["discussion_cleared"] = True
        # 4b. 平台历史任务记忆（按工作间 ID / 名称删除）
        for frag in (ws.workshop_id, ws.name):
            try:
                results["task_memory_removed"] += task_memory.remove_by_fragment(frag)
            except Exception as _e:
                print(f"[halt_reset] 任务记忆擦除失败: {_e}", flush=True)
        # 4c. harness 经验索引清零 + 通知成员记忆已重置
        targets = []
        if wipe_members:
            targets += [m for m in ws.members if m.role != "组长"]
        if wipe_leader:
            targets += [m for m in ws.members if m.role == "组长"]
        seen_hid = set()
        for m in targets:
            hid = (m.harness_ids or [None])[0] if m else None
            if not hid or hid in seen_hid:
                continue
            seen_hid.add(hid)
            sess = harness_manager.sessions.get(hid)
            if sess is not None:
                cleared = []
                metas = [getattr(sess, "metadata", None)]
                if getattr(sess, "info", None):
                    metas.append(getattr(sess.info, "metadata", None))
                for meta in metas:
                    if not isinstance(meta, dict):
                        continue
                    for key in _EXPERIENCE_INDEX_KEYS:
                        if isinstance(meta.get(key), list) and meta.get(key):
                            meta[key] = []
                            if key not in cleared:
                                cleared.append(key)
                results["harness_index_cleared"].append({"hid": hid, "cleared": cleared})
            try:
                _dispatch_to_harness(hid, {
                    "type": "notice",
                    "workshop_id": ws_id,
                    "member_id": m.member_id,
                    "role": m.role,
                    "message": "【记忆重置】工作间确认走弯路，你的任务记忆与经验索引已被平台擦除。请忘记本工作间既往结论，等待重新激活后从零开始。",
                }, kind="notice")
            except Exception as _e:
                print(f"[halt_reset] 记忆重置通知派发失败: {_e}", flush=True)
    save_state()

    # 5. 从头开始
    if restart:
        note = "工作间已重置为 draft，可从一级讨论重新开始（点击「开始工作」激活）。"
    else:
        note = "工作间已停止并保留在 draft。"
    print(f"[halt_reset] 工作间 {ws_id} 走弯路重置完成：reason={reason[:40]}", flush=True)
    return {
        "success": True,
        "status": ws.status,
        "note": note,
        "results": results,
        "detour_path": str(detour_path),
    }

@router.post("/api/workshop/{ws_id}/interject")
async def submit_interject(ws_id: str, request: Request):
    from ..server import Utf8JSONResponse, _inject_interject_to_workshop, _phase_of, save_state
    """提交一条插话（用户侧边输入框）。纯规则判定：允许→注入工作循环；不允许→进池。"""
    ws = workshops.get(ws_id)
    if not ws:
        return Utf8JSONResponse({"error": "工作间不存在"}, status_code=404)
    body = await request.json()
    content = str(body.get("content") or "").strip()
    if not content:
        return {"success": False, "error": "内容为空"}
    priority = str(body.get("priority") or "一般")
    if priority not in ("紧急", "灵感", "一般"):
        priority = "一般"
    it = interject_store.submit(ws_id, content, priority=priority, kind="user")
    judge = should_interject(priority, _phase_of(ws))
    if judge.get("allowed"):
        _inject_interject_to_workshop(ws, it)
    save_state()
    return {"success": True, "interject": it, "judge": judge}

@router.get("/api/workshop/{ws_id}/interjects")
async def list_interjects(ws_id: str):
    from ..server import Utf8JSONResponse, save_state
    """拉出插话列表（状态 × 优先级排序；顺带清理积压过期）。"""
    ws = workshops.get(ws_id)
    if not ws:
        return Utf8JSONResponse({"error": "工作间不存在"}, status_code=404)
    expired = interject_store.expire_stale(ws_id)
    if expired:
        save_state()
    return {"success": True, "interjects": interject_store.list(ws_id)}

@router.post("/api/workshop/{ws_id}/interject/{it_id}/break")
async def interject_break(ws_id: str, it_id: str):
    from ..server import Utf8JSONResponse, _inject_interject_to_workshop, save_state
    """紧急条目直接打断：强停当前工作循环，立即注入讨论区。"""
    ws = workshops.get(ws_id)
    if not ws:
        return Utf8JSONResponse({"error": "工作间不存在"}, status_code=404)
    it = interject_store.get(ws_id, it_id)
    if not it:
        return Utf8JSONResponse({"error": "插话不存在"}, status_code=404)
    if it.get("status") != "pending":
        return {"success": False, "error": "已在流程中"}
    task_state_machine.on_event(ws_id, EV_TIMEOUT, {"reason": "user_break", "interject": it_id})
    _inject_interject_to_workshop(ws, it)
    save_state()
    return {"success": True, "interject": it, "note": "已直接打断当前工作循环"}

@router.post("/api/workshop/{ws_id}/interject/{it_id}/execute")
async def interject_execute(ws_id: str, it_id: str):
    from ..server import Utf8JSONResponse, _append_hall, _broadcast_and_collect, _orchestrated_flow, bcast_to_clients, save_state
    """紧急插话立刻执行：标记已插入 → 状态机直达 executing → 创建 Task 作为
    source=interject 紧急委托直接交 Orchestrator 执行（跳过讨论排队，与状态机衔接）。"""
    ws = workshops.get(ws_id)
    if not ws:
        return Utf8JSONResponse({"error": "工作间不存在"}, status_code=404)
    it = interject_store.get(ws_id, it_id)
    if not it:
        return Utf8JSONResponse({"error": "插话不存在"}, status_code=404)
    if it.get("status") != "pending":
        return {"success": False, "error": "已在流程中"}
    # 1) 插话落定：标记已插入（与 break/resolve 同收敛路径）
    interject_store.mark(ws_id, it_id, "inserted", related_task="execute")
    # 2) 状态机衔接：跳过讨论排队，直达 executing 态（后续 stuck/blocked/report/timeout 事件均可正常流转）
    task_state_machine.set_state(ws_id, EXECUTING, stage="interject_execute", interject=it_id)
    # 3) 创建 Task，作为 source=interject 的紧急委托直接交 Orchestrator 执行
    content = str(it.get("content") or "")
    title = content[:30] + ("..." if len(content) > 30 else "")
    task = Task(
        title=title, description=content,
        status=TaskStatus.BROADCASTING,
    )
    tasks[task.id] = task
    save_state()
    create_msg = Message(
        type=MessageType.EVENT,
        from_agent="user", content=content,
        task_id=task.id,
        payload={"event": "task_created", "source": "interject", "task": task.model_dump()},
    )
    _append_hall(create_msg)
    await bcast_to_clients(create_msg)
    if _sv().ai_provider:
        print(f"[interject_execute] 紧急插话 {it_id} -> task {task.id} 交 Orchestrator 执行", flush=True)
        asyncio.create_task(_orchestrated_flow(task, content))
    else:
        # 无 AI 时回退传统全局广播（与 api_command 一致）
        asyncio.create_task(_broadcast_and_collect(task.id, content))
    return {"success": True, "task_id": task.id, "interject": interject_store.get(ws_id, it_id)}

@router.post("/api/workshop/{ws_id}/interject/{it_id}/resolve")
async def interject_resolve(ws_id: str, it_id: str):
    from ..server import Utf8JSONResponse, save_state
    """组长裁决：插话已解决 → 状态机从 stuck/timeout 回到 discussing。"""
    ws = workshops.get(ws_id)
    if not ws:
        return Utf8JSONResponse({"error": "工作间不存在"}, status_code=404)
    it = interject_store.get(ws_id, it_id)
    if not it:
        return Utf8JSONResponse({"error": "插话不存在"}, status_code=404)
    interject_store.mark(ws_id, it_id, "inserted", related_task="resolved")
    ev = task_state_machine.on_event(ws_id, EV_RESOLVE, {"interject": it_id})
    save_state()
    return {"success": True, "event": ev}

@router.post("/api/workshop/{ws_id}/interject/{it_id}/dismiss")
async def interject_dismiss(ws_id: str, it_id: str):
    from ..server import Utf8JSONResponse, save_state
    """忽略一条插话（组长/用户裁决为不处理）。"""
    ws = workshops.get(ws_id)
    if not ws:
        return Utf8JSONResponse({"error": "工作间不存在"}, status_code=404)
    it = interject_store.get(ws_id, it_id)
    if not it:
        return Utf8JSONResponse({"error": "插话不存在"}, status_code=404)
    interject_store.mark(ws_id, it_id, "ignored")
    save_state()
    return {"success": True, "interject": interject_store.get(ws_id, it_id)}

@router.post("/api/workshop/{ws_id}/interject/{it_id}/status")
async def interject_status(ws_id: str, it_id: str, request: Request):
    from ..server import Utf8JSONResponse, save_state
    """通用状态流转（pending/inserted/ignored/expired），供前端/组长台使用。"""
    ws = workshops.get(ws_id)
    if not ws:
        return Utf8JSONResponse({"error": "工作间不存在"}, status_code=404)
    it = interject_store.get(ws_id, it_id)
    if not it:
        return Utf8JSONResponse({"error": "插话不存在"}, status_code=404)
    body = await request.json()
    status = str(body.get("status") or "").strip()
    if status not in ("pending", "inserted", "ignored", "expired"):
        return {"success": False, "error": "invalid status"}
    interject_store.mark(ws_id, it_id, status)
    save_state()
    return {"success": True, "interject": interject_store.get(ws_id, it_id)}

@router.post("/api/workshop/{ws_id}/sm/resume")
async def sm_resume(ws_id: str):
    from ..server import Utf8JSONResponse, save_state
    """组长/用户裁决后恢复：stuck-paused/timeout/blocked-retrying → executing。"""
    ws = workshops.get(ws_id)
    if not ws:
        return Utf8JSONResponse({"error": "工作间不存在"}, status_code=404)
    ev = task_state_machine.on_event(ws_id, EV_RESUME, {"by": "leader"})
    for m in ws.members:
        if m.status in ("stuck", "blocked", "failed"):
            m.status = "entered"
    save_state()
    return {"success": True, "event": ev}

@router.post("/api/workshop/{ws_id}/sm/drop")
async def sm_drop(ws_id: str):
    from ..server import Utf8JSONResponse, save_state
    """放弃当前工作循环（用户/组长明确放弃，不静默）。"""
    ws = workshops.get(ws_id)
    if not ws:
        return Utf8JSONResponse({"error": "工作间不存在"}, status_code=404)
    ev = task_state_machine.on_event(ws_id, EV_DROP, {"by": "user"})
    for m in ws.members:
        if m.status in ("stuck", "blocked", "failed"):
            m.status = "entered"
    save_state()
    return {"success": True, "event": ev}

@router.get("/api/workshop/{ws_id}/sm/status")
async def sm_status(ws_id: str):
    from ..server import Utf8JSONResponse
    """状态机当前状态 + 最近事件日志（诊断用）。"""
    ws = workshops.get(ws_id)
    if not ws:
        return Utf8JSONResponse({"error": "工作间不存在"}, status_code=404)
    st = task_state_machine.get_state(ws_id)
    logs = task_state_machine._states.get(ws_id, {}).get("logs", [])[-20:]
    return {"success": True, "state": st, "logs": logs}

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

@router.get("/api/workshop/{ws_id}/resources")
async def list_resources(ws_id: str):
    from ..server import Utf8JSONResponse
    """列出工作间资源库（全部成员可读）。"""
    ws = workshops.get(ws_id)
    if not ws:
        return Utf8JSONResponse({"error": "工作间不存在"}, status_code=404)
    items = []
    for r in ws.resources:
        item = dict(r)
        item.setdefault("kind", "file")
        items.append(item)
    return {"success": True, "workshop_id": ws_id, "resources": items}

@router.post("/api/workshop/{ws_id}/resources")
async def add_resource(ws_id: str, request: Request):
    from ..server import Utf8JSONResponse, _append_msg, _tree_find, now_iso, save_state
    """登记资源到工作间资源库。

    需求：组长生成工作间文件夹/产出后上传登记，供其他成员查看。
    body: {name, kind(file/dir/link), path, note, task_node, uploader}
    - path: 资源所在路径（工作区内相对路径或绝对路径均可，记录路径用）
    - task_node: 关联任务树节点标识（可选，与任务树联系的挂载点）
    """
    ws = workshops.get(ws_id)
    if not ws:
        return Utf8JSONResponse({"error": "工作间不存在"}, status_code=404)
    body = await request.json()
    name = (body.get("name") or "").strip()
    if not name:
        return Utf8JSONResponse({"error": "资源名称不能为空"}, status_code=400)
    path = (body.get("path") or "").strip()
    if not path:
        return Utf8JSONResponse({"error": "资源路径不能为空"}, status_code=400)
    kind = (body.get("kind") or "file").strip() or "file"
    if kind not in ("file", "dir", "link"):
        kind = "file"
    res = {
        "rid": uuid4().hex[:8],
        "name": name,
        "kind": kind,
        "path": path,
        "note": (body.get("note") or "").strip(),
        "uploader": (body.get("uploader") or "").strip(),
        "task_node": (body.get("task_node") or "").strip(),
        "created_at": now_iso(),
    }
    # 可选：登记时直接挂载到任务树节点（node_id 双向关联）
    node_id = (body.get("node_id") or "").strip()
    if node_id:
        node = _tree_find(ws, node_id)
        if node is None:
            return Utf8JSONResponse({"error": f"任务树节点不存在：{node_id}"}, status_code=400)
        res["task_node"] = node_id
        attached = node.setdefault("resources", [])
        if res["rid"] not in attached:
            attached.append(res["rid"])
    ws.resources.append(res)
    try:
        write_resources_manifest(ws)
    except Exception as _e:
        print(f"[workshop] 资源清单落盘失败: {_e}", flush=True)
    _append_msg(ws, "notice", f"组长登记资源「{name}」（{kind}）→ {path}", zone=3)
    save_state()
    return {"success": True, "resource": res}

@router.delete("/api/workshop/{ws_id}/resources/{rid}")
async def remove_resource(ws_id: str, rid: str):
    from ..server import Utf8JSONResponse, _append_msg, save_state
    """从资源库移除登记（不删除实体文件，仅解除共享）。"""
    ws = workshops.get(ws_id)
    if not ws:
        return Utf8JSONResponse({"error": "工作间不存在"}, status_code=404)
    before = len(ws.resources)
    ws.resources = [r for r in ws.resources if r.get("rid") != rid]
    if len(ws.resources) == before:
        return Utf8JSONResponse({"error": "资源不存在"}, status_code=404)
    try:
        write_resources_manifest(ws)
    except Exception as _e:
        print(f"[workshop] 资源清单落盘失败: {_e}", flush=True)
    _append_msg(ws, "notice", f"已从资源库移除登记：{rid}", zone=3)
    save_state()
    return {"success": True}

@router.get("/api/workshop/{ws_id}/tree")
async def get_task_tree(ws_id: str):
    from ..server import Utf8JSONResponse, _tree_build
    """获取工作间任务树（嵌套结构，含节点挂载的资源明细）。"""
    ws = workshops.get(ws_id)
    if not ws:
        return Utf8JSONResponse({"error": "工作间不存在"}, status_code=404)
    return {"success": True, "workshop_id": ws_id, "tree": _tree_build(ws)}

@router.post("/api/workshop/{ws_id}/tree/node")
async def create_tree_node(ws_id: str, request: Request):
    from ..server import Utf8JSONResponse, _append_msg, _tree_find, _tree_persist, now_iso
    """创建任务树节点。

    body: {label, parent_id, kind, note, status}
    - label: 节点名称（必填）
    - parent_id: 父节点 node_id（可选，空则挂根）
    - kind: task(任务) / correction(纠偏) / phase(阶段)（默认 task）
    - note: 说明（可选）
    - status: active / done / bypassed / dropped（默认 active；纠偏错误路用 dropped）
    """
    ws = workshops.get(ws_id)
    if not ws:
        return Utf8JSONResponse({"error": "工作间不存在"}, status_code=404)
    body = await request.json()
    label = (body.get("label") or "").strip()
    if not label:
        return Utf8JSONResponse({"error": "节点名称不能为空"}, status_code=400)
    parent_id = (body.get("parent_id") or "").strip()
    if parent_id and _tree_find(ws, parent_id) is None:
        return Utf8JSONResponse({"error": f"父节点不存在：{parent_id}"}, status_code=400)
    kind = (body.get("kind") or "task").strip() or "task"
    if kind not in ("task", "correction", "phase"):
        kind = "task"
    status = (body.get("status") or "active").strip() or "active"
    if status not in ("active", "done", "bypassed", "dropped"):
        status = "active"
    node = {
        "node_id": f"t_{uuid4().hex[:8]}",
        "label": label,
        "parent_id": parent_id,
        "kind": kind,
        "status": status,
        "note": (body.get("note") or "").strip(),
        "resources": [],
        "created_at": now_iso(),
    }
    ws.task_tree.append(node)
    _tree_persist(ws)
    _append_msg(ws, "notice", f"任务树新增节点「{label}」（{kind}/{status}）", zone=3)
    return {"success": True, "node": node}

@router.post("/api/workshop/{ws_id}/tree/node/{node_id}/status")
async def update_tree_node_status(ws_id: str, node_id: str, request: Request):
    from ..server import Utf8JSONResponse, _append_msg, _tree_find, _tree_persist
    """更新任务树节点状态（active/done/bypassed/dropped）。"""
    ws = workshops.get(ws_id)
    if not ws:
        return Utf8JSONResponse({"error": "工作间不存在"}, status_code=404)
    node = _tree_find(ws, node_id)
    if node is None:
        return Utf8JSONResponse({"error": "节点不存在"}, status_code=404)
    body = await request.json()
    status = (body.get("status") or "").strip()
    if status not in ("active", "done", "bypassed", "dropped"):
        return Utf8JSONResponse({"error": "非法状态，应为 active/done/bypassed/dropped"}, status_code=400)
    old = node.get("status", "")
    node["status"] = status
    _tree_persist(ws)
    _append_msg(ws, "notice", f"任务树节点「{node.get('label')}」状态 {old} → {status}", zone=3)
    return {"success": True, "node": node}

@router.post("/api/workshop/{ws_id}/tree/node/{node_id}/attach")
async def attach_resource_to_node(ws_id: str, node_id: str, request: Request):
    from ..server import Utf8JSONResponse, _append_msg, _tree_find, _tree_persist
    """把资源库资源挂到任务树节点（资源与任务双向关联）。

    body: {rid} 资源库资源 ID（必填，须存在）
    """
    ws = workshops.get(ws_id)
    if not ws:
        return Utf8JSONResponse({"error": "工作间不存在"}, status_code=404)
    node = _tree_find(ws, node_id)
    if node is None:
        return Utf8JSONResponse({"error": "节点不存在"}, status_code=404)
    body = await request.json()
    rid = (body.get("rid") or "").strip()
    res = next((r for r in ws.resources if r.get("rid") == rid), None)
    if res is None:
        return Utf8JSONResponse({"error": f"资源库中不存在该资源：{rid}"}, status_code=400)
    attached = node.setdefault("resources", [])
    if rid not in attached:
        attached.append(rid)
    res["task_node"] = node["node_id"]
    _tree_persist(ws)
    _append_msg(ws, "notice", f"资源「{res.get('name')}」已挂载到任务树节点「{node.get('label')}」", zone=3)
    return {"success": True, "node": node, "resource": res}
