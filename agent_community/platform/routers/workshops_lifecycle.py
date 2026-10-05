"""platform/routers/workshops_lifecycle：工作间生命周期（V-13 自 workshops.py 拆分）。

承载创建/列表/详情/改名/pin/删除/stale 回收/mode/parallel_limit/决策模式 等端点。
拆分纪律：路由路径、参数、响应体与拆分前逐字一致（纯搬迁，零行为变更）。
"""
from __future__ import annotations
from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect
from ..workshop import Workshop
from ..workshop import WorkshopMember
from ..audit import audit_log as _audit_log
import asyncio
import json
import os
from uuid import uuid4
from ..workshop import write_workspace_files
from ..state import interject_store, pending_activations, task_state_machine, tasks, workshops
from ..harness_adapter import harness_manager   # 与其余 8 个 router 同范式（本文件此前未导入）

router = APIRouter()

@router.get("/api/workshop/{ws_id}/mode")
async def api_workshop_mode_get(ws_id: str):
    from ..server import _load_workshop_modes
    modes = _load_workshop_modes()
    return {"workshop_id": ws_id, "mode": modes.get(ws_id, "standard")}

@router.post("/api/workshop/{ws_id}/mode")
async def api_workshop_mode_set(ws_id: str, request: Request):
    _audit_log.record("workshop.mode", actor="user", target=ws_id, detail="设置模式")
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
    _audit_log.record("workshop.parallel_limit", actor="user", target=ws_id, detail="设置并发上限")
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
    _audit_log.record("workshop.rename", actor="user", target=ws_id, detail="重命名")
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
    _audit_log.record("workshop.pin", actor="user", target=ws_id, detail="置顶")
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
    _audit_log.record("workshop.delete", actor="user", target=ws_id, detail="删除工作间")
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
    # 4) 会话关闭联动（V-15 步骤8）：工作区已进回收站 → cwd 失效 → 会话成孤儿，必须发起 close（§九 + §12.2）
    sessions_close_requested = 0
    try:
        from ..session_registry import session_registry, sweep_and_audit
        # V-17 触发时机②：回收时机天然适合先收敛上一批到期未自述的会话
        sweep_and_audit()
        _closed = session_registry.on_workshop_deleted(ws_id)
        sessions_close_requested = len(_closed)
        if _closed:
            _audit_log.record(
                "session.close", actor="user", target=ws_id,
                detail=f"工作间删除（工作区进回收站），发起关闭 {len(_closed)} 个会话（close_requested，等 harness 自述）",
            )
    except Exception as _e:
        print(f"[workshops_lifecycle] 会话关闭联动失败（不阻断）: {_e}", flush=True)
    save_state()
    return {"success": True, "deleted": ws_id, "state_machine_removed": sm_removed,
            "interjects_removed": it_removed, "trash": trash_result,
            "sessions_close_requested": sessions_close_requested}

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
    _audit_log.record("workshop.recycle_stale", actor="user", target="", detail="回收过期工作间")
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
        # V-15 步骤8：回收语义与 DELETE 一致 —— 会话关闭联动（cwd 失效 → 发起 close）
        sess_closed = 0
        try:
            from ..session_registry import session_registry, sweep_and_audit
            # V-17 触发时机②（回收路径与 DELETE 同语义）
            sweep_and_audit()
            _closed = session_registry.on_workshop_deleted(wid)
            sess_closed = len(_closed)
            if _closed:
                _audit_log.record(
                    "session.close", actor="user", target=wid,
                    detail=f"stale 回收（工作区进回收站），发起关闭 {len(_closed)} 个会话（close_requested）",
                )
        except Exception as _e:
            print(f"[workshops_lifecycle] 会话关闭联动失败（不阻断）: {_e}", flush=True)
        workshops.pop(wid, None)
        recycled.append({"workshop_id": wid, "name": ws.name,
                         "state_machine_removed": sm_removed, "interjects_removed": it_removed,
                         "sessions_close_requested": sess_closed,
                         "trash": trash_result})
    save_state()
    return {"success": True, "recycled": recycled, "skipped": skipped}


def _member_wakeup(m) -> str:
    """成员所用 harness 的唤醒方式 —— 前端据此显示该档「最高可达」（V-15 §11）。

    取不到（未指定 harness / 读取异常）返回空串，前端按「未知档」处理，
    不猜；`entry_state.max_reachable` 会给保守值。
    """
    try:
        from ..server import _harness_wakeup_method
        hid = (getattr(m, "harness_ids", None) or [None])[0]
        return _harness_wakeup_method(hid) if hid else ""
    except Exception:
        return ""


@router.post("/api/workshop/{ws_id}/connect-artifact")
async def workshop_connect_artifact(ws_id: str, request: Request):
    """V-15 步骤5：生成 L3 接入产物，落进**本工作间的工作区**。

    ── §八 安全边界（这个端点存在的根本原因）──────────────────
    ❌ caller 传 out_dir  → 调用方定路径 ≈ 任意文件写入
                           （`/api/harness/{id}/bridge/generate` 的 V-6 护栏拦它，
                             **原样保留、不动**）
    ✅ 本端点            → 路径只由 `ws.workspace_dir` 派生（建间期平台生成的 ws_<hex8>）
    **请求体不接受任何路径字段**，只接受 `member_id` 这类逻辑 id；
    即便调用方塞了 `out_dir` / `target_dir`，也**根本没人读它**（并直接 400）。

    按唤醒方式派生产物（§5.3）：script / contract / prompt ——
    对 http_api 型要求"生成脚本"这个要求本身就是错的，故给连接契约 JSON。
    """
    from ..server import Utf8JSONResponse
    try:
        body = await request.json()
    except Exception:
        body = {}
    body = body if isinstance(body, dict) else {}

    # 安全：**只取逻辑 id，绝不取任何看起来像路径的字段**（§八）
    for _danger in ("out_dir", "target_dir", "path", "dir", "workspace_dir"):
        if _danger in body:
            return Utf8JSONResponse(
                {"error": f"本端点不接受路径字段 {_danger!r} —— 路径由平台按 "
                          f"workshop_id 派生（V-15 §八）"},
                status_code=400,
            )
    member_id = str(body.get("member_id") or "").strip()

    ws = workshops.get(ws_id)
    if not ws:
        return Utf8JSONResponse({"error": "工作间不存在"}, status_code=404)
    if not (ws.workspace_dir or "").strip():
        return Utf8JSONResponse({"error": "工作间无 workspace_dir，无处落产物"}, status_code=400)

    member = None
    if member_id:
        member = next((m for m in ws.members if m.member_id == member_id), None)
        if member is None:
            return Utf8JSONResponse({"error": f"成员 {member_id} 不在该工作间"}, status_code=404)
    elif ws.members:
        member = ws.members[0]

    hid = ((getattr(member, "harness_ids", None) or [None])[0] if member else None)
    if not hid:
        return Utf8JSONResponse(
            {"error": "该工作间没有已指定 harness 的成员，无法生成接入产物"}, status_code=400)
    sess = harness_manager.sessions.get(hid)
    if not sess or not sess.info:
        return Utf8JSONResponse({"error": f"harness {hid} 未注册"}, status_code=404)

    from ..connect_artifact import plan as _plan, render as _render
    pl = _plan(ws, sess.info,
               member_id=(getattr(member, "member_id", "") if member else ""))
    if not pl.get("complete"):
        return Utf8JSONResponse({"error": "产物计划不完整（无工作区坐标）", "plan": pl},
                                status_code=400)

    # ── V-14 策略闸门：本端点会真的 write_text 落盘，必须在 render **之前** ──
    # （与 /bridge/generate 同口径：写盘但不启动 -> 出厂 ASK）
    from ..policy import (BRIDGE_WRITE, actor_from_request, blocked_message,
                          check, pending_message)
    _ev, _pending = check(
        BRIDGE_WRITE,
        target=f"{hid}:{pl['artifact_kind']}:{ws_id}",
        actor=actor_from_request(request),
    )
    if _ev.blocked:
        return Utf8JSONResponse(
            {"error": blocked_message(_ev), "rule_id": _ev.rule_id}, status_code=403)
    if _ev.needs_approval:
        return Utf8JSONResponse(
            {"status": "pending_approval", "op": _ev.op,
             "message": pending_message(_ev, _pending), "pending": _pending},
            status_code=202)

    try:
        out = _render(pl)
    except Exception as e:
        return Utf8JSONResponse({"error": f"产物生成失败: {e}"}, status_code=500)

    try:
        _audit_log.record(
            "artifact.render",
            actor=actor_from_request(request),
            target=f"{ws_id}:{hid}",
            detail=f"kind={out.get('artifact_kind')} "
                   f"path={out.get('path') or out.get('error')}",
        )
    except Exception:
        pass

    # ── V-15 步骤6：建间后的提醒（§10.1 时机2）──
    #  产物路径 + 启动方式；C 档必须给「请建会话，cwd=工作区」+ 回执契约（§10.2）
    _g = {}
    try:
        from ..tier_guidance import guidance_at_artifact
        _g = guidance_at_artifact(
            info,
            workspace_dir=ws.workspace_dir,
            workshop_id=ws_id,
            member_id=pl.get("member_id", ""),
            platform_url=pl.get("params", {}).get("PLATFORM_URL", ""),
            artifact_path=out.get("path", ""),
            artifact_kind=out.get("artifact_kind") or pl.get("artifact_kind", ""),
        )
    except Exception as _e:
        _g = {"error": f"提醒生成失败（产物已生成，不影响）: {_e}"}

    return {"success": bool(out.get("ok")), "plan": pl,
            "artifact": out, "harness_id": hid, "guidance": _g}


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
        # V-15 步骤3：entry_state / entry_reason 必须显式列出 ——
        # 这里是**逐字段映射**，不加这两个键前端就拿不到，等于后端白做。
        "members": [
            {
                "member_id": m.member_id,
                "role": m.role,
                "display_name": m.display_name,
                "harness_ids": m.harness_ids,
                "status": m.status,
                "entry_state": getattr(m, "entry_state", "pending"),
                "entry_reason": getattr(m, "entry_reason", ""),
                "wakeup_method": _member_wakeup(m),
            }
            for m in ws.members
        ],
        "resources": ws.resources,
    }

@router.get("/api/workshop/{ws_id}/decision-mode")
async def get_decision_mode_api(ws_id: str):
    from ..server import Utf8JSONResponse, _get_decision_mode
    ws = workshops.get(ws_id)
    if not ws:
        return Utf8JSONResponse({"error": "工作间不存在"}, status_code=404)
    return {"success": True, "workshop_id": ws_id, "decision_mode": _get_decision_mode(ws_id)}

@router.post("/api/workshop/{ws_id}/decision-mode")
async def set_decision_mode_api(ws_id: str, request: Request):
    _audit_log.record("workshop.decision_mode", actor="user", target=ws_id, detail="设置决策模式")
    from ..server import Utf8JSONResponse, _set_decision_mode
    ws = workshops.get(ws_id)
    if not ws:
        return Utf8JSONResponse({"error": "工作间不存在"}, status_code=404)
    body = await request.json()
    mode = _set_decision_mode(ws_id, str(body.get("mode") or ""))
    return {"success": True, "workshop_id": ws_id, "decision_mode": mode}

