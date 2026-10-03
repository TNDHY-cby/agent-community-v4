"""platform/routers/workshops_discuss：工作间讨论与完成（V-13 自 workshops.py 拆分）。

承载 discuss/review/continue/complete/start、插话 6 端点、状态机 sm/* 等端点。
拆分纪律：路由路径、参数、响应体与拆分前逐字一致（纯搬迁，零行为变更）。
"""
from __future__ import annotations
import sys
from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect
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
from ..state import interject_store, pending_activations, task_state_machine, tasks, workshops

router = APIRouter()

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

