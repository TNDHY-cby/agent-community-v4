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
from pathlib import Path
from uuid import uuid4
from ..workshop import write_workspace_files
from ..state import interject_store, pending_activations, task_state_machine, tasks, workshops
from ..harness_adapter import harness_manager   # 与其余 8 个 router 同范式（本文件此前未导入）

router = APIRouter()


def _harness_display_name(hid) -> str:
    """把 harness_id 翻成给人看的显示名（**只读、失败返回空串**，绝不编一个名字出来）。

    用途：建工作间时调用方没给 `display_name` ⇒ 用它绑定的 harness 的注册名当显示名，
    避免出现「所有成员同名」或「拿写死的示例名当默认值」（2026-10-19 多成员并发判据抓到）。
    """
    hid = str(hid or "").strip()
    if not hid:
        return ""
    try:
        sess = harness_manager.sessions.get(hid)
        return str(getattr(getattr(sess, "info", None), "harness_name", "") or "").strip()
    except Exception:                                        # noqa: BLE001
        return ""

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

_KNOWN_CREATE_FIELDS = frozenset({
    "name", "hall_content", "members", "mode", "parallel_limit",
    "debate_enabled", "budget_default", "pinned",
    # ★2026-10-25（`V104` 跑 MCP 全链路时抓到的真缺陷）：MCP 工具 `create_workshop` 的说明写着
    #   「发起新任务并广播」，实际只建了个**空工作间**，一个 Task 都没有（`/api/tasks` 空、
    #   `origin_task_id` 空）⇒ 对方按协议"建间干活"，建完却没有任务号可回报，**看着成功其实跑不通**。
    #   现在：带 `create_task: true` 就在建间的同时建出这条任务并挂上 `origin_task_id`。
    #   ⚠️ 与 `/api/command` 的区别：这条**不广播、不叫 AI**（大厅发布才广播），
    #      适合外部 agent 按协议"先建活、再干活、再回执"；要广播仍然走大厅 `/api/command`。
    "create_task", "task_title", "task_description", "task_id",
})


@router.post("/api/workshop")
async def create_workshop(request: Request):
    from ..server import MAX_WORKSHOPS, Utf8JSONResponse, WORKSHOP_BASE, now_iso, save_state
    body = await request.json()
    if not isinstance(body, dict):
        body = {}
    name = (body.get("name") or "").strip()
    hall = body.get("hall_content", "") or ""
    # 工作间以工作内容命名：未传 name 或仍为默认值时，用任务内容截断
    if not name or name in ("员工工作间", "未命名工作间", "默认工作间"):
        name = hall.strip().replace("\n", " ")[:20] or "未命名工作间"
    # ★2026-10-27（`V109` §6 / `V117` §2）：**工作区路径可以由调用方指定**
    #   以前一律平台自造 `<WORKSHOP_BASE>/ws_xxx` ⇒ 根B（工作区）永远落在平台数据目录里，
    #   既没法与根A（心智根）分开，也没法让用户挑盘 —— 双根就是空话。
    #   规则：给了 `workshop_path`（旧名 `workspace_dir` 也认）就用它，必须是**绝对路径**且可写；
    #   不给就照旧自造。**不猜、不吞错**：建不出来就说清哪个路径、为什么。
    _requested = str(body.get("workshop_path") or body.get("workspace_dir") or "").strip()
    _ws_dir = str(WORKSHOP_BASE / f"ws_{uuid4().hex[:8]}")
    if _requested:
        _p = Path(_requested)
        if not _p.is_absolute():
            return Utf8JSONResponse({"success": False, "code": "BAD_WORKSHOP_PATH",
                                     "error": "workshop_path 必须是绝对路径",
                                     "next": "传绝对路径（例：D:/coop/项目A 或 /home/me/coop/项目A）"},
                                    status_code=400)
        try:
            _p.mkdir(parents=True, exist_ok=True)
            _probe = _p / ".ac_write_probe"
            _probe.write_text("", encoding="utf-8")
            _probe.unlink()
        except OSError as e:
            return Utf8JSONResponse({"success": False, "code": "WORKSHOP_PATH_NOT_WRITABLE",
                                     "error": "这个工作区路径建不了/写不了：%s（%s: %s）"
                                              % (_p, type(e).__name__, e),
                                     "next": "换一个有写权限的目录，或先手动建好它"}, status_code=400)
        _ws_dir = str(_p)
    ws = Workshop(
        workshop_id=uuid4().hex[:8],
        name=name,
        workspace_dir=_ws_dir,
        hall_content=hall,
        members=[
            WorkshopMember(
                member_id=f"m{i}",
                role=m.get("role", "员工"),
                # ★2026-10-19（多成员并发判据抓到）**两处默认值都是假数据**：
                #   ① `display_name` 默认写死「示例Harness-E」⇒ 调用方不传就读出**所有成员同名**，
                #      界面上分不清两个同时干活的员工（前端一律 `display_name || member_id` 兜底，空着反而正确）；
                #   ② `harness_ids` 默认写死 ["示例Harness-E"] ⇒ 不传就绑到一个**根本不存在的 harness**，
                #      成员必然 blocked（典型"开箱假数据"，PRODUCTIZATION 规则 B）。
                #   现改为：名字缺省→取该成员绑的 harness 的显示名（还没有就留空，前端显示成员 id）；
                #   绑定缺省→**空**（不假装绑了谁，`_activate_single` 会如实标 blocked 并说清没绑）。
                display_name=str(m.get("display_name") or "").strip() or _harness_display_name(
                    (m.get("harness_ids") or [None])[0]),
                harness_ids=[h for h in (m.get("harness_ids") or []) if str(h or "").strip()],
            )
            for i, m in enumerate(body.get("members") or [])
        ],
        created_at=now_iso(),
    )
    # ★2026-10-11 修复（老板口径 + 示例Harness-F 94 号实测报告）：
    #   大厅「发布」走的就是这条（`index.html:670` → body `{hall_content}`）。
    #   修前：构造不设 status ⇒ 落默认 `draft`；而 `POST …/discuss` 里那句
    #         `if ws.status not in ("division","review","running"): ws.status = "discussing"`
    #         只会在**用户发出第一条消息时**才把状态掰过来 ⇒
    #         表现为「进去先显示草稿态，发了消息才变讨论中」，与老板要的
    #         「大厅发任务 → 自动建好工作间 → **进去就能直接开始聊**（一级讨论）」不符。
    #   修后：**有任务内容就进一级讨论**，与另一条自动建间入口一致
    #         （`server.py:1365` `_ws.status = "discussing"  # 自动建间即进入一级讨论`）；
    #         **没有任务内容仍保留 `draft`**（纯手工建的空间，不假装在讨论）。
    #   状态机权威：`workshop.py:80` draft → discussing → selecting → division → running → review → done
    # 注：讨论端点里那句"发消息即掰成 discussing"是**兜底自愈**，保留不动（历史 draft 工作间仍能救回）。
    if hall.strip():
        ws.status = "discussing"
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
    out = {"success": True, "workshop_id": ws.workshop_id, "workspace_dir": ws.workspace_dir,
           "name": ws.name}
    # ★`create_task`：外部 agent 按协议建活时，工作间背后**必须真有一条任务**
    #   （否则它没法回报 `task_id`，平台也没处收结果 —— MCP 全链路判据先抓到这个洞，
    #     2026-10-25 又发现 **A2A / gRPC 的"派任务"是同一个洞**：它们把客户端给的 task_id
    #     塞进 `/api/workshop`，而这里根本不认 ⇒ 建出来的还是空房间）。
    #   现在认 `task_id`（客户端指定就用它，**幂等**：已存在就复用），并挂上 `origin_task_id`。
    #   只建任务与关联，**不广播、不叫 AI**（广播走大厅 `/api/command`）。
    _want_tid = str(body.get("task_id") or "").strip()
    if body.get("create_task") or _want_tid:
        try:
            from ..server import Task, TaskStatus, tasks

            _title = (str(body.get("task_title") or "").strip()
                      or (hall.strip().splitlines()[0][:40] if hall.strip() else "") or ws.name)
            _desc = str(body.get("task_description") or "").strip() or hall.strip() or _title
            _task = tasks.get(_want_tid) if _want_tid else None
            _reused = _task is not None
            if _task is None:
                _task = Task(id=(_want_tid or None), title=_title[:80], description=_desc,
                             status=TaskStatus.CREATED, created_by="external_agent") \
                    if _want_tid else Task(title=_title[:80], description=_desc,
                                           status=TaskStatus.CREATED, created_by="external_agent")
                tasks[_task.id] = _task
            ws.origin_task_id = _task.id
            save_state()
            _audit_log.record("workshop.create_task", actor="harness", target=ws.workshop_id,
                              detail="task=%s%s" % (_task.id, "（复用已有）" if _reused else ""),
                              extra={"task_id": _task.id, "workshop_id": ws.workshop_id})
            out.update({"task_id": _task.id, "task_title": _task.title, "task_reused": _reused,
                        "task_status": str(getattr(_task.status, "value", _task.status))})
        except Exception as e:                                 # noqa: BLE001 — 建不了任务要如实说
            out["task_error"] = "工作间建好了，但任务没建出来：%s: %s" % (type(e).__name__, e)
            out["next"] = "重试一次；还不行就先只建工作间，用「大厅发布」建任务"
    # ★2026-10-22 夜场三（我拿 `title` 建间、结果得到一个「未命名工作间」才发现）：
    #   这个端点的名字字段叫 **`name`**，传 `title` 会被**静默忽略** ⇒ 用户/前端以为命名成功，
    #   大厅里却是一排「未命名工作间」。**失败要说话**：认不出的字段一律回 warning 说清该叫什么。
    _unknown = sorted(k for k in body if k not in _KNOWN_CREATE_FIELDS)
    if _unknown:
        out["warnings"] = ["这些字段没被识别：%s —— 工作间名字的字段叫 `name`，任务内容叫 `hall_content`"
                           "（本次已按 %s 命名：%s）"
                           % ("、".join(_unknown),
                              "任务内容" if hall.strip() else "默认值", ws.name)]
    return out

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


def effective_library_dir(ws) -> str:
    """**资料库指向**的有效值（老板 2026-10-30 流程第 7-9 步）。

    规则：`ws.library_path` 非空就以它为准（**可以在工作区之外**——那是"人写的资产"，
    不是一次性产物，所以允许外置）；空则默认 `<工作区>/library/`。
    """
    custom = str(getattr(ws, "library_path", "") or "").strip()
    if custom:
        return custom
    from ..workshop import LIBRARY_DIRNAME

    return str(Path(str(getattr(ws, "workspace_dir", "") or "")) / LIBRARY_DIRNAME)


@router.get("/api/workshop/{ws_id}/library")
async def workshop_library(ws_id: str):
    """**共享资料库**（老板主流程 ⑥）的只读视图：目录 + 文件实况 + 内容预览。

    形态（老板 2026-10-07）：「可以是一个自动建立的文件夹，也可以是 git 之类的库」。
    当前实现=**自动建立的文件夹** `<工作间目录>/library/`，布局为纯文本，
    日后 `git init` 即可升格为库，不需要改结构。
    """
    from ..server import Utf8JSONResponse
    from ..workshop import LIBRARY_DIRNAME
    ws = workshops.get(ws_id)
    if not ws:
        return Utf8JSONResponse({"error": "工作间不存在"}, status_code=404)
    d = Path(effective_library_dir(ws))
    files = []
    if d.is_dir():
        for p in sorted(d.iterdir()):
            if p.is_file():
                files.append({"name": p.name, "size": p.stat().st_size})
    # ★2026-10-08 补：**把子目录也列出来**（尤其"每个员工一个 `<agent名>-<职位>/` 分工文件夹"）。
    # 原实现只列文件 ⇒ 界面上**永远看不到员工分工文件夹**（老板明确要的东西，接口却没给）。
    # 新增 `directories` 字段，对既有消费方**向后兼容**（files/preview 原样保留）。
    directories = []
    try:
        for p in sorted(d.iterdir()):
            if not p.is_dir():
                continue
            _sub = []
            try:
                for q in sorted(p.iterdir()):
                    if q.is_file():
                        _sub.append({"name": q.name, "size": q.stat().st_size})
            except Exception:  # noqa: BLE001
                pass
            _entry = {"name": p.name, "files": _sub, "count": len(_sub)}
            _ap = p / "assignment.md"
            if _ap.is_file():
                try:
                    _entry["assignment_preview"] = _ap.read_text(encoding="utf-8")[:1200]
                except Exception:  # noqa: BLE001
                    _entry["assignment_preview"] = ""
            directories.append(_entry)
    except Exception:  # noqa: BLE001
        pass
    preview = {}
    for name in ("plan.md", "framework.md", "README.md", "model.md"):
        p = d / name
        if p.is_file():
            try:
                preview[name] = p.read_text(encoding="utf-8")[:1200]
            except Exception:  # noqa: BLE001
                preview[name] = ""
    # ★V29 项目模型（老板 2026-10-10 拍板）：只读状态给界面（是否存在/执笔人/版本/缺哪些块）
    try:
        from ..workshop import read_framework_info as _rfi, read_model_info as _rmi
        _model = _rmi(ws)
        _framework = _rfi(ws)
    except Exception as e:  # noqa: BLE001
        _model = {"exists": False, "error": f"{type(e).__name__}: {e}"}
        _framework = {"exists": False, "error": f"{type(e).__name__}: {e}"}
    return {
        "workshop_id": ws_id, "dir": str(d), "exists": d.is_dir(),
        "files": files, "directories": directories, "preview": preview,
        "model": _model,
        "framework": _framework,
        "rule": "共享区（成员都能读）；成员只在自己的隔间里写文件。",
        "form": "folder（可日后 git init 升格）",
        "note": ("`directories` 里是**每个员工的分工文件夹** `<agent名>-<职位>/`，"
                 "内含 `assignment.md`（他该做什么，附 `assignment_preview`）——外端 agent 拿路径自己去看。"),
    }



@router.post("/api/workshop/{ws_id}/library-path")
async def workshop_library_path(ws_id: str, request: Request):
    """**设置/清空「资料库指向」**（老板流程第 9 步；body `{path}`，空串 = 回默认 `<根B>/library/`）。

    只**存指向**不代建目录：用户自己在外面新建文件夹（可以在工作区之外），
    组长把「项目建模 + 分工明细」放进去；这里只校验"绝对路径 + 目录存在"。
    """
    from ..server import Utf8JSONResponse, save_state

    ws = workshops.get(ws_id)
    if not ws:
        return Utf8JSONResponse({"error": "工作间不存在"}, status_code=404)
    try:
        body = await request.json()
    except Exception:                                        # noqa: BLE001 — 没 body 就按空
        body = {}
    raw = str((body or {}).get("path") or "").strip()
    if not raw:
        ws.library_path = ""
        save_state()
        return {"ok": True, "library_path": "", "effective": effective_library_dir(ws),
                "note": "已清空自定义指向，回到默认 `<工作区>/library/`"}
    p = Path(raw)
    if not p.is_absolute():
        return Utf8JSONResponse({"ok": False, "code": "PATH_NOT_ABSOLUTE",
                                 "error": "资料库指向必须是**绝对路径**（收到：%s）" % raw,
                                 "next": "填你新建的那个文件夹的完整路径，例如 `D:/项目资料`"}, 400)
    # 允许"稍后再建"：不存在就**如实说没找到**，但仍记下（用户可以先去建，回来再点一次）
    exists = p.is_dir()
    ws.library_path = str(p)
    save_state()
    return {"ok": True, "library_path": ws.library_path, "effective": effective_library_dir(ws),
            "exists": exists,
            "note": ("指向已记下；目录存在，组长可以把建模与分工写进去" if exists
                     else "指向已记下，但**这个目录现在不存在** —— 请先去新建它（平台不代建）")}

@router.post("/api/workshop/{ws_id}/library/update")
async def workshop_library_update(ws_id: str, request: Request):
    """**更新**共享资料库（老板流程 ⑥「更新共享资料库：分工安排、项目框架」）。

    body（都可选）：
      - `framework`: 项目框架正文。给了就写入 `framework.md`；**不给则不覆盖已有内容**
        （尊重人的编辑）。`plan.md`（分工安排）永远按当前名单+委派记录重新生成。
    """
    from ..server import Utf8JSONResponse, save_state
    from ..workshop import write_library
    ws = workshops.get(ws_id)
    if not ws:
        return Utf8JSONResponse({"error": "工作间不存在"}, status_code=404)
    try:
        body = await request.json()
    except Exception:
        body = {}
    _fw = str((body or {}).get("framework") or "")
    # ★V29：`model` 可以是**五块字典**（goal/parts/roles/deps/outputs），也可以是**整篇 markdown 文本**；
    # `author` = 执笔人（组长/整合者/用户）——平台**没有**身份 ACL，故如实记录执笔人而不是假装拦得住。
    _model_raw = (body or {}).get("model")
    _model_blocks = _model_raw if isinstance(_model_raw, dict) else None
    _model_text = _model_raw if isinstance(_model_raw, str) else ""
    _author = str((body or {}).get("author") or "")
    out = write_library(ws, framework=_fw, updated_by="api",
                        model=_model_blocks, model_text=_model_text, model_author=_author)
    if not out.get("ok"):
        return Utf8JSONResponse({"error": "更新失败", "detail": out.get("error")}, status_code=500)
    _mo = out.get("model") or {}
    try:
        _audit_log.record("library.update", actor="user", target=ws_id,
                          detail=(f"framework={'yes' if _fw.strip() else 'no'} "
                                  f"model={('v%s' % _mo.get('version')) if _mo.get('ok') else 'no'} "
                                  f"author={_author or '-'} files={len(out.get('written') or [])}"),
                          extra={"framework_written": bool(_fw.strip()),
                                 "model_written": bool(_mo.get("ok")),
                                 "model_version": _mo.get("version") or 0,
                                 "model_author": _author,
                                 "model_missing": _mo.get("missing") or []})
    except Exception:  # noqa: BLE001
        pass
    save_state()
    _note = ("plan.md 已按当前名单重生成；framework.md "
             + ("已写入" if _fw.strip() else "保持已有内容（未传 framework）"))
    if _mo.get("ok"):
        _note += (f"；model.md 已写入 v{_mo.get('version')}（执笔：{_author or '未记录'}）"
                  + (f"，缺 {len(_mo.get('missing') or [])} 块（界面应显示未完成）" if _mo.get("missing") else ""))
    elif _mo.get("error") and _mo.get("error") != "未提供 model":
        _note += f"；⚠️ model.md 写入失败：{_mo.get('error')}"
    return {"success": True, "workshop_id": ws_id, "dir": out["dir"],
            "written": out.get("written", []),
            "model": _mo,
            "note": _note}


@router.get("/api/workshop/{ws_id}/outputs")
async def workshop_outputs(ws_id: str):
    """**只读**：各成员隔间 `output/` 里的产物清单（V64）。

    为什么要有它：公司间合作时，对端的 a2a 回执要能带回"**这趟活交了什么**"
    （`a2a_server` 会把这里的结果映射成 A2A `artifacts`）。而原来的 `integrate` 是**写操作**
    （落清单 + 派活给整合者），不能拿来当只读查询用。

    **只给清单，不给文件内容**（附 `note` 说明：文件在对端那台机器上，本侧不自动下载）。
    """
    from ..server import Utf8JSONResponse
    from ..workshop import collect_member_outputs
    ws = workshops.get(ws_id)
    if not ws:
        return Utf8JSONResponse({"error": "工作间不存在"}, status_code=404)
    col = collect_member_outputs(ws)
    rows = []
    for r in (col.get("members") or []):
        for f in (r.get("files") or []):
            rows.append({"member_id": r.get("member_id"), "role": r.get("role"),
                         "name": f.get("name"), "bytes": f.get("bytes"),
                         "mtime": f.get("mtime")})
    return {"success": True, "workshop_id": ws_id, "total_files": len(rows),
            "files": rows[:200],
            "note": "这些是产物清单（名字/大小/归属成员）；文件内容不在这个接口里——"
                    "跨公司时文件在对端那台机器上，本侧不自动下载"}


@router.post("/api/workshop/{ws_id}/integrate")
async def workshop_integrate(ws_id: str):
    """**阶段整合**（老板流程 ⑧ 后半：「阶段完成**由一个 agent 整合**」）。

    做三件事，顺序固定：
      1. **收产物**：把每个成员隔间 `output/` 里的文件列成清单（`collect_member_outputs`，只读）；
      2. **落清单**：写 `<工作间>/library/integration.md`，交给整合者按路径去取；
      3. **派活**：把「去整合」这件事派给**整合者**（默认组长）的真实 harness。

    ⚠️ 准则⑤ **失败要说话**：若所有成员 `output/` 都是空的，**照样出清单但明确标"无产物可整合"**，
    响应里 `has_output=false`，**绝不在这种状态下说"整合成功"**。

    与批次汇总的分工：`_parallel_batch_summary` 汇总的是**文字回报**；本端点汇总的是**磁盘产物**。
    """
    from ..server import Utf8JSONResponse, _append_msg, _dispatch_to_harness, save_state
    from ..workshop import collect_member_outputs, write_integration_manifest
    ws = workshops.get(ws_id)
    if not ws:
        return Utf8JSONResponse({"error": "工作间不存在"}, status_code=404)
    members = list(ws.members or [])
    if not members:
        return Utf8JSONResponse(
            {"error": "名单为空，没有可整合的成员", "hint": "先选人（select-members / members）再整合"},
            status_code=409)
    _leader = next((m for m in members if m.role == "组长"), None) or members[0]
    man = write_integration_manifest(ws, integrator=f"{_leader.display_name or _leader.member_id}（{_leader.role}）")
    _has = man["total_files"] > 0

    # 讨论区留痕（用户能在房间里看到"该整合了/有没有东西可整合"）
    _msg = (f"【阶段整合】整合者：{_leader.display_name or _leader.member_id}。"
            f"产物清单：`{man['path']}`。"
            + (f"共 {man['total_files']} 个产物，来自 {len(man['with_output'])} 名成员；"
               f"**未产出：{','.join(man['without_output']) or '无'}**。"
               if _has else
               "**所有成员 output/ 均为空——本次无产物可整合**（不是整合完成）。"))
    try:
        _append_msg(ws, "notice", _msg, zone=3)
    except Exception:  # noqa: BLE001
        pass

    # 派活给整合者
    _hid = (_leader.harness_ids or [None])[0]
    _disp_ok, _disp_note = (False, "整合者未绑定 harness")
    if _hid:
        _col = collect_member_outputs(ws)
        _paths = "\n".join(
            f"- {r['display_name'] or r['member_id']}（{r['role']}）：{r['output_dir']}"
            + (f" —— {r['file_count']} 个产物" if r["file_count"] else " —— **无产物**")
            for r in _col["members"])
        _lib = os.path.join(str(ws.workspace_dir), "library")
        _prompt = (
            "你是本工作间的**整合者**（阶段成果汇总）。老板流程规定：**阶段完成由一个 agent 整合**。\n"
            f"整合清单（含每人产物路径与文件名）：{man['path']}\n"
            f"共享资料库：{_lib}\n\n"
            "各成员产物（在**他们各自隔间**的 output/ 下，**平台不搬动，你自己去取**）：\n"
            f"{_paths}\n\n"
            "请：① 逐个核对路径、确认产物真的在；② 整合成一份阶段成果，写进共享资料库；"
            "③ **如实报告缺项**——谁没交、交的对不对，别把缺的算成有。"
        )
        _disp_ok, _disp_note = _dispatch_to_harness(_hid, {
            "type": "task", "kind": "integrate", "workshop_id": ws_id,
            "role": "组长", "integrator": _leader.member_id,
            "manifest_path": man["path"], "message": _prompt, "task": _prompt,
        }, kind="task")

    try:
        _audit_log.record("workshop.integrate", actor="user", target=ws_id,
                          detail=f"integrator={_leader.member_id} files={man['total_files']} "
                                 f"dispatched={bool(_disp_ok)}",
                          extra={"integrator": _leader.member_id, "total_files": man["total_files"],
                                 "without_output": man["without_output"], "has_output": _has,
                                 "manifest": man["path"], "dispatched": bool(_disp_ok)})
    except Exception:  # noqa: BLE001
        pass
    save_state()
    return {
        "success": True,
        "integrator": _leader.member_id,
        "integrator_role": _leader.role,
        "manifest": man["path"],
        "total_files": man["total_files"],
        "with_output": man["with_output"],
        "without_output": man["without_output"],
        "has_output": _has,                       # ← 前端据此决定措辞，别把"无产物"说成成功
        "dispatched": bool(_disp_ok), "dispatch_note": _disp_note,
        "note": man["note"] or _msg,
    }


@router.get("/api/workshop/{ws_id}/compartments")
async def workshop_compartments(ws_id: str):
    """**每个成员隔间的磁盘实况**（只读）。

    应答 FE-03 §7 的接口变更申请（示例Harness-F 2026-10-07）：
    前端原本靠"镜像后端路径公式 + 读 bridge.auto 审计"间接展示隔间，
    但**审计只能证明"何时发过桥、落在哪"，证明不了目录/文件此刻在不在**；
    而公式镜像会随后端改动漂移。故由后端直接给出实况与权威路径。

    ⚠️ 回给前端的 `dir` 是**权威路径**，前端不得再自行推导（见 compartment.py 的定义块）。
    """
    from ..server import Utf8JSONResponse
    from .. import compartment as _cmp
    ws = workshops.get(ws_id)
    if not ws:
        return Utf8JSONResponse({"error": "工作间不存在"}, status_code=404)
    rows = []
    for m in (ws.members or []):
        row = _cmp.inspect_compartment(ws.workspace_dir, m.role, m.member_id)
        row["display_name"] = getattr(m, "display_name", "")
        row["status"] = getattr(m, "status", "")
        row["entry_state"] = getattr(m, "entry_state", "")
        row["subdir_names"] = list(_cmp.SUBDIRS)     # 规范分区名，供前端展示"应含分区"
        rows.append(row)
    return {
        "workshop_id": ws_id,
        "workspace_dir": ws.workspace_dir,           # 共享区（非成员可写）
        "compartments": rows,
        "count": len(rows),
        "exists_count": sum(1 for r in rows if r.get("exists")),
        "rule": "一个会话 = 一个隔间 + 一个具体桥；隔间在「架起具体桥」那一步创建。",
        "path_authority": "dir 由后端给出，前端不要自行推导路径（会随公式改动漂移）。",
    }

def _v5_roster_view(ws) -> dict:
    """给旧界面看的 v5 名单摘要（**只读、不含凭据**）；取不到就如实说 empty + why。

    `roster_summary` 是**一句人话**（名单 X 人 / 已接上 Y / 还差 Z（哪几个空工位）），
    与 `GET …/roster` 用同一个函数 `v5_roster.roster_summary()`（规则 20：一处真相）。
    """
    try:
        from .. import v5_roster as _rw

        wid = getattr(ws, "workshop_id", "") or ""
        eff = _rw.effective_members(wid)
        snap = _rw.roster(wid)
        return {"members_v5": eff["members"], "members_source": eff["source"],
                "roster_counts": snap.get("counts", {}),
                "roster_summary": _rw.roster_summary(wid)}
    except Exception as e:                                   # noqa: BLE001 — 名单坏了不该让详情 500
        return {"members_v5": [], "members_source": "unknown",
                "roster_counts": {}, "roster_summary": {},
                "roster_why": "名单取不到：%s" % str(e)[:120]}

@router.get("/api/workshop/{ws_id}")
async def get_workshop(ws_id: str, after_seq: int = -1):
    from ..server import Utf8JSONResponse, _normalize_discussion
    ws = workshops.get(ws_id)
    if not ws:
        return Utf8JSONResponse({"error": "工作间不存在"}, status_code=404)
    # ★2026-10-23（外部测试问题 EXT-20261022-01 的收尾）：**详情也要带 gate**。
    #   列表 /api/workshops 早就带了（Workshop.to_dict() 里），但详情是逐字段映射，
    #   漏了 gate ⇒ 用户在详情页看不到为什么不能开工，只有点了才发现 409。
    #   一处真相在 workshop_gate.info()；这里只做透传，别再自己写一套判断。
    try:
        from .. import workshop_gate as _gate_mod

        _gate_info = _gate_mod.info(ws)
    except Exception as _e:                                  # noqa: BLE001 — 闸门坏了不该让详情 500
        _gate_info = {"ok": True, "state": "unknown", "why": str(_e)[:120], "next_step": ""}
    return {
        "workshop_id": ws.workshop_id,
        "name": ws.name,
        "status": ws.status,
        "workspace_dir": ws.workspace_dir,
        "hall_content": ws.hall_content,
        "gate": _gate_info,
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
        # ★2026-10-30：**v5 名单摘要（只读）**。旧 `members` 是 v4 模型，和 v5 工位并存期间
        #   只读它 ⇒ 界面显示"未选员工"、闸门判"没有成员"，而实际有 active 工位（真机实测到）。
        #   这里只**加字段**，不动旧 `members` 的形状，也不做对象替换（那是待拍板的迁移）。
        **_v5_roster_view(ws),
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


@router.post("/api/workshop/{ws_id}/library/model/draft")
async def workshop_model_draft(ws_id: str):
    """**项目模型草案**（V29 §2「路径 B」）：配了 AI 时，平台把讨论内容整理成五块草案。

    **不落盘** —— 草案只是给组长改的起点；确认后用 `POST .../library/update` 带 `model` 保存
    （即「人确认后才算建立」，准则⑤）。**未配 AI 就明确报错 + 给手工填写指引，不假装生成**。
    """
    import json as _json

    from ..ai_external import run_ai_call
    from ..server import Utf8JSONResponse, _discussion_ctx
    from ..workshop import MODEL_SECTIONS
    from .workshops_common import _sv

    ws = workshops.get(ws_id)
    if not ws:
        return Utf8JSONResponse({"error": "工作间不存在"}, status_code=404)
    prov = getattr(_sv(), "ai_provider", None)
    if not prov:
        return Utf8JSONResponse({
            "error": "未配置 AI：无法生成草案",
            "hint": "手工填五块即可（界面「建立模型」里有骨架提示）；配好 AI 后可回来一键生成。",
            "sections": [tt for _k, tt, _h in MODEL_SECTIONS],
        }, status_code=400)

    roster = "、".join(f"{m.role}（{m.display_name or m.member_id}）" for m in (ws.members or [])) or "（尚无成员）"
    ctx = (f"任务（大厅内容）：\n{ws.hall_content}\n\n"
           f"当前成员：{roster}\n\n讨论历史（最近 12 条）：\n" + _discussion_ctx(ws, limit=12))
    system = ("你在给一个协作项目**建模**：把上面的讨论收敛成一份项目模型草案，"
              "**严格输出 JSON**（不要解释、不要 markdown 代码块），键固定五个："
              '{"goal":"…","parts":"…","roles":"…","deps":"…","outputs":"…"}；'
              "含义：goal=目标与验收；parts=组成（模块/任务分解）；roles=分工对应（谁负责哪块）；"
              "deps=依赖与顺序；outputs=产物落点（放谁的 output/）。"
              "讨论里没有依据的部分**留空字符串**，不要编造。中文，每项可含少量换行与项目符号。")
    try:
        reply = await run_ai_call(prov.chat(system, ctx), label="server.model_draft")
    except Exception as e:  # noqa: BLE001
        return {"success": False, "draft": {}, "error": f"AI 调用失败：{type(e).__name__}: {e}"}
    txt = str(reply or "")
    from ..ai_provider import AI_FAILURE_PREFIXES as _AI_FAIL_PREFIXES   # 一处真相
    if txt.startswith(_AI_FAIL_PREFIXES):
        return {"success": False, "draft": {}, "error": txt[:200]}
    i, j = txt.find("{"), txt.rfind("}")
    if i < 0 or j <= i:
        return {"success": False, "draft": {}, "raw": txt[:800],
                "error": "AI 返回不是合法 JSON（草案未生成）"}
    try:
        d = _json.loads(txt[i:j + 1])
    except Exception as e:  # noqa: BLE001
        return {"success": False, "draft": {}, "raw": txt[:800],
                "error": f"解析草案 JSON 失败：{type(e).__name__}"}
    draft = {}
    for _k, _title, _hint in MODEL_SECTIONS:
        _v = d.get(_k)
        draft[_k] = "" if _v is None else str(_v).strip()
    return {
        "success": True,
        "draft": draft,
        "filled": [k for k, v in draft.items() if v],
        "missing": [title for k, title, _h in MODEL_SECTIONS if not draft.get(k)],
        "verified": False,
        "note": "这是**草案**（未经组长确认）：请修改后点「保存」写入 library/model.md；"
                "缺块会被如实标为未完成（准则⑤：不许当已建立）",
    }


# ── ★FE-17 §4：右侧栏「按阶段收敛」的权威判据 ────────────────────────────────
# 为什么放后端：**"现在该按哪几个按钮"是业务规则**，散在前端会变成前后端两套口径；
# 放这一处可测、可审计、可记录改动（FE-17 §4 原话）。
#
# 老板原话（2026-10-10 真机测试）：「工作间右侧边栏内容太复杂，有一些按不了，
# 我不知道怎么按，没办法使用」⇒ 实测该页**不同文案的按钮共 58 个**。
# 契约目标：**任何时刻"当前该按的"主操作 ≤5 个**，阶段专属且用不上的**直接不渲染**
# （不是置灰 —— 置灰正是"按不了"的来源）。
#
# ⚠️ 本端点是**只读**的：不得触发激活、不得改状态（对齐 V17 那次的教训）。
# ★2026-11-01（老板："赶紧改新到 V5"）：阶段名与动作全部对齐 **v5 现行口径**。
#   原来这套是 v4 的「一级讨论 → 选定员工 → 二级讨论 → 工作中 → 三级讨论」，其中：
#   · 「一级讨论」「三级讨论」**已退役**（`POST …/discuss` ⇒ 410 FREECHAT_RETIRED）；
#   · 「选定员工（让平台 AI 选人）」**已退役**（⇒ 410 AI_SELECT_RETIRED）——组长由**用户**选、名单由**组长回执**交回；
#   · 端点也写错了：`finish_discuss` 指向 `select-members`（410），真正该走 `begin-selecting`。
#   后端这张表是**界面按钮的权威判据**（FE-17 §4），错在这里 = 界面教你点已经废掉的按钮。
_FE17_STAGES = {
    "discuss1": {"index": 1, "label": "定方向"},
    "select":   {"index": 2, "label": "选组长"},
    "discuss2": {"index": 3, "label": "分工"},
    "working":  {"index": 4, "label": "干活"},
    "review":   {"index": 5, "label": "验收"},
    "done":     {"index": 5, "label": "归档"},
}

# 后端 status -> FE-17 的阶段 id（FE-17 §1 的表；status 取值源自 `workshop.py:97`）
# ★2026-11-01：补 `lead_in`（v5 新增状态：组长已选定、正在建模/开岗）——
#   原来没有它 ⇒ 落到默认 `discuss1`，界面会退回"定方向"，用户选完组长又被弹回去。
_FE17_STATUS_TO_STAGE = {
    "draft": "discuss1", "discussing": "discuss1", "entered": "discuss1",
    "selecting": "select", "lead_in": "select", "division": "discuss2",
    "running": "working", "active": "working", "activating": "working", "idle": "working",
    "review": "review",
    "done": "done", "closed": "done",
    "blocked": "working", "dropped": "done", "pending_approval": "working",
}

# 通用动作（多个阶段都可能出现；`stages` 为它所属阶段集合）
_FE17_ACTIONS = {
    "discuss": {
        "label": "发送一条结构化消息", "kind": "primary",
        "effects": "发进本间讨论区（七类之一：提案/质疑/改进/呼应/提问/行动项/推荐）；自由聊天不收",
        "endpoint": "POST /api/workshop/{ws_id}/ailp",
    },
    "finish_discuss": {
        "label": "方向定了，去选组长 →", "kind": "primary",
        "effects": "结束「定方向」，进入「选组长」（组长由**你**选，平台只筛资格；它不会替你选）",
        "endpoint": "POST /api/workshop/{ws_id}/begin-selecting",
    },
    # ★2026-11-01 删除 `select_members`（"让平台 AI 选定员工"）：
    #   端点 `POST …/select-members` 已 **410 AI_SELECT_RETIRED**（平台不选人，V116 §16）。
    #   留在表里 = 界面让你点一个必然失败的按钮。
    "add_member": {
        "label": "+ 添加职位 / harness", "kind": "more",
        "effects": "手工往名单里补一行（标准路径是组长交名单回执，这是补漏用）",
        "endpoint": "（界面本地的名单编辑）",
    },
    "save_roster": {
        "label": "保存名单", "kind": "primary",
        "effects": "把当前名单存进工作间（不改阶段）",
        "endpoint": "POST /api/workshop/{ws_id}/members",
    },
    "confirm_members": {
        "label": "确认名单，进入分工", "kind": "primary",
        "effects": "确认成员名单并进入分工阶段；平台会派发组长开始对接",
        "endpoint": "POST /api/workshop/{ws_id}/confirm-members",
    },
    "start_work": {
        "label": "🚀 开始工作", "kind": "primary",
        "effects": "按名单激活成员（建会话/送桥并绑定），进入工作阶段",
        "endpoint": "POST /api/workshop/{ws_id}/start",
    },
    "rescue_scan": {
        "label": "🔄 全间抢救扫描", "kind": "primary",
        "effects": "检查全间谁掉线了，能救的救、救不了的上报给你",
        "endpoint": "POST /api/workshop/{ws_id}/recover-scan",
    },
    "leader_check": {
        "label": "📋 组长检查员工状态", "kind": "more",
        "effects": "让组长扫一遍成员状态与进度",
        "endpoint": "POST /api/workshop/{ws_id}/leader-status",
    },
    "register_resource": {
        "label": "⬆️ 登记到资源库", "kind": "more",
        "effects": "把当前成果登记进资源库",
        "endpoint": "POST /api/workshop/{ws_id}/resources",
    },
    "activate_template": {
        "label": "📋 手动激活模板", "kind": "more",
        "effects": "取该成员的激活提示词（没配脚本时人工用）",
        "endpoint": "POST /api/workshop/{ws_id}/member/{member_id}/establish-session",
    },
    "third_discuss": {
        "label": "📋 转验收", "kind": "primary",
        "effects": "让全员回报进展并转入验收（验收要证据：产物在盘上 / 平台验的 / 你拍板）",
        "endpoint": "POST /api/workshop/{ws_id}/review",
    },
    "continue_work": {
        "label": "▶ 继续工作", "kind": "primary",
        "effects": "验收后继续推进（不回退阶段）",
        "endpoint": "POST /api/workshop/{ws_id}/continue",
    },
    "complete_task": {
        "label": "✅ 完成任务", "kind": "primary",
        "effects": "收口本工作间（此后再往下就是整合产出）",
        "endpoint": "POST /api/workshop/{ws_id}/complete",
    },
    "integrate": {
        "label": "📦 收口整合产出", "kind": "primary",
        "effects": "把各成员的 output/ 收成清单交给整合者",
        "endpoint": "POST /api/workshop/{ws_id}/integrate",
    },
}

# 阶段 -> (主操作, 收进「更多 ⋯」的) —— 与 FE-17 §2 的白名单表逐行对应
_FE17_BY_STAGE = {
    # ★2026-11-01（老板："上面那个是干嘛的（发送一条结构化消息），用不上"）：
    #   「定方向」阶段的主操作**只留**「方向定了，去选组长 →」。
    #   结构化发言仍可用（`POST …/ailp`），但不再占主操作位 —— 它是可选动作，不是这一步的必经。
    "discuss1": (["finish_discuss"],
                 ["discuss", "leader_check", "register_resource", "activate_template"]),
    "select":   (["add_member", "save_roster", "confirm_members"],
                 ["rescue_scan", "leader_check"]),
    "discuss2": (["discuss", "start_work"],
                 ["leader_check", "register_resource"]),
    "working":  (["rescue_scan", "leader_check", "integrate"],
                 ["register_resource", "activate_template"]),
    "review":   (["third_discuss", "continue_work", "complete_task"],
                 ["register_resource"]),
    "done":     ([], ["integrate"]),
}


@router.get("/api/workshop/{ws_id}/sidebar")
async def workshop_sidebar(ws_id: str):
    """★FE-17 §4：右侧栏「**当前该按哪几个按钮**」的权威判据（只读、无副作用）。

    返回 `stage`（阶段）+ `visible_actions`（主操作，**≤5**）+ `more_actions`（收进「更多 ⋯」）
    + `blocked_reason`（非空时必须显著显示原因与下一步）。

    ⚠️ 界面**不得盲信**：本端点失败时，前端应**降级为"显示全部 + 提示"**，
    不许因为接口挂了就让用户无从下手（协作书规则 17「自服务标准」）。
    """
    from ..server import Utf8JSONResponse
    ws = workshops.get(ws_id)
    if not ws:
        return Utf8JSONResponse({"error": "工作间不存在"}, status_code=404)

    status = str(getattr(ws, "status", "") or "")
    stage_id = _FE17_STATUS_TO_STAGE.get(status, "discuss1")
    stage_meta = _FE17_STAGES[stage_id]
    primary_ids, more_ids = _FE17_BY_STAGE.get(stage_id, ([], []))

    def _act(aid):
        a = dict(_FE17_ACTIONS[aid])
        a["id"] = aid
        return a

    # 失败要说话：异常状态必须给出**原因 + 怎么办**，不能只给一个灰按钮
    blocked_reason = None
    if status in ("blocked", "dropped", "pending_approval"):
        blocked_reason = {
            "blocked": "该工作间被标记为受阻。先看「组长检查员工状态」或运维看板，"
                       "确认卡在哪一步，再决定继续还是收口。",
            "dropped": "该工作间已放弃。如仍需推进，请在大厅重新发布任务。",
            "pending_approval": "有一步在等人工批准（策略闸门）。批准后流程才会继续。",
        }.get(status, f"状态异常：{status}")

    return {
        "success": True,
        "workshop_id": ws_id,
        "status": status,                     # 原样给后端权威状态，界面不得改名
        "stage": {"id": stage_id, "label": stage_meta["label"],
                  "index": stage_meta["index"], "total": 5},
        "visible_actions": [_act(x) for x in primary_ids],
        "more_actions": [_act(x) for x in more_ids],
        "blocked_reason": blocked_reason,
        "member_count": len(getattr(ws, "members", []) or []),
        "note": "主操作 ≤5（FE-17 §2）；阶段专属且用不上的按钮**不渲染**（不是置灰）。"
                "端点失败时前端应降级为显示全部并提示，不要留白。",
    }
