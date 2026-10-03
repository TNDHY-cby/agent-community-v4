"""platform/routers/harness_messaging：Harness 消息与任务回报（V-13 自 harness.py 拆分）。

承载 message / task-result / pending-* / api-probe / api-message / auto-connect 等端点。
拆分纪律：路由路径、参数、响应体与拆分前逐字一致（纯搬迁，零行为变更）。
"""
from __future__ import annotations
from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect
from ..protocol import HarnessMessage
from ..protocol import WakeupMethod
from datetime import datetime
from ..harness_adapter import harness_manager
from ..api_wakeup import probe_http_api
from ..api_wakeup import send_http_api_message
from ..state import _bridge_tests_inflight, agents, assistant_history, assistant_history_max, pending_activations, pending_bridge_tests, pending_pre_register, pending_tasks, workshops
from ..audit import audit_log as _audit_log

router = APIRouter()

@router.post("/api/harness/message")
async def harness_message(request: Request):
    _audit_log.record("harness.message", actor="user", target="", detail="harness 消息")
    from ..server import Utf8JSONResponse, _sanitize_harness_content
    """接收来自 Harness 的消息回复"""
    body = await request.json()
    try:
        msg = HarnessMessage(**body)
    except Exception as e:
        return Utf8JSONResponse({"error": f"消息格式错误: {e}"}, status_code=400)
    # P1 身份凭证：目标 harness 已签发 token 时，强制校验 X-Agent-Token 头（向后兼容）
    _id_resp = _harness_identity_check(msg.harness_id, request.headers)
    if _id_resp:
        return _id_resp
    # V-14：外部 harness 主动上报内容做提示注入防护（进 pending futures 前）
    if isinstance(msg.content, str) and msg.content.strip():
        msg.content = _sanitize_harness_content(msg.content)
    harness_manager.handle_reply(msg)
    # 更新会话状态
    if msg.harness_id in harness_manager.sessions:
        harness_manager.sessions[msg.harness_id].last_heartbeat = datetime.now().isoformat()
        harness_manager.sessions[msg.harness_id].message_count += 1
    return {"success": True, "msg_id": msg.id}

@router.get("/api/harness/list")
async def list_harnesses():
    """列出所有已接入的 Harness"""
    return {"harnesses": harness_manager.list_sessions()}

@router.get("/api/harness/pending-activations")
async def harness_pending_activations(harness_id: str):
    """harness 桥轮询：领取该 harness 名下的待激活任务。"""
    acts = pending_activations.pop(harness_id, [])
    return {"activations": acts}

@router.get("/api/harness/pending-tasks")
async def harness_pending_tasks(harness_id: str):
    """harness 桥轮询：领取该 harness 名下的待执行任务。"""
    tasks = pending_tasks.pop(harness_id, [])
    return {"tasks": tasks}

@router.post("/api/harness/task-result")
async def harness_task_result(request: Request):
    _audit_log.record("harness.task_result", actor="user", target="", detail="任务回报")
    from ..server import _append_msg, _apply_harness_reply, _do_complete_workshop, _do_continue_workshop, _get_decision_mode, _sanitize_harness_content, _settle_vote, save_state
    """harness 桥回报任务结果。"""
    body = await request.json()
    # P1 身份凭证：若该 harness 已签发 token，强制校验 X-Agent-Token（向后兼容）
    _hid0 = str(body.get("harness_id") or "").strip()
    if _hid0:
        _id_resp = _harness_identity_check(_hid0, request.headers)
        if _id_resp:
            return _id_resp
    # ── 委托链结果兑现：无论 ok 真假，都要把回报路由给等待中的 Future ──
    # 否则 bridge.execute_subtask / review_subtask 会空等 300s / 30s 超时，
    # orchestrator.execute_layers 整条任务链表现为“永久卡在 broadcasting”。
    try:
        _hid = str(body.get("harness_id") or "").strip()
        _did = str(body.get("delegation_id") or "").strip()
        if not _hid:
            # 兼容旧桥：按 workshop/member 反查 harness
            _ws0 = workshops.get(body.get("workshop_id", ""))
            _mid0 = body.get("member_id")
            if _ws0 and _mid0:
                for _m in getattr(_ws0, "members", []):
                    if _m.member_id == _mid0:
                        _aid = getattr(_m, "agent_id", "") or ""
                        if _aid:
                            _hid = harness_manager.id_to_harness.get(_aid, "")
                        break
        if _hid and not _did:
            # 兜底：该桥只有一个待决委托时，视为它的结果
            _br = harness_manager.bridges.get(_hid)
            if _br:
                _pkeys = [k for k, _f in list(_br._pending.items()) if not _f.done()]
                if len(_pkeys) == 1:
                    _did = _pkeys[0]
        if _hid:
            _reply_content = _sanitize_harness_content(
                str(body.get("result") or body.get("text") or body.get("reply") or "")
            )  # V-14：外部内容提示注入防护
            _reply_msg = HarnessMessage(
                harness_id=_hid,
                direction="from_harness",
                msg_type="result",
                content=_reply_content,
                task_id=str(body.get("task_id") or ""),
                delegation_id=_did,
                payload={"ok": bool(body.get("ok", True)), "raw": body},
            )
            harness_manager.handle_reply(_reply_msg)
            print(
                f"[harness_reply] 回报路由: harness={_hid} delegation={_did or '-'} ok={body.get('ok', True)}",
                flush=True,
            )
    except Exception as _e:
        print(f"[harness_reply] 回报路由异常: {_e}", flush=True)
    ws = workshops.get(body.get("workshop_id", ""))
    if ws:
        mid = body.get("member_id")
        # ── R3：决策模式回报（组长独裁 decision / 举手表决 vote）──
        # 仅在 review 态且带明确裁决字段时接管收口；普通回报不受影响
        _dm = str(body.get("decision") or "").strip().lower()
        _vt = str(body.get("vote") or "").strip().lower()
        if ws.status == "review" and mid:
            if _dm in ("continue", "complete") and _get_decision_mode(ws.workshop_id) == "leader":
                _append_msg(ws, "notice", f"【组长裁决】组长回报 decision={_dm}，按裁决执行。", zone=3)
                if _dm == "complete":
                    _do_complete_workshop(ws, by="leader")
                else:
                    _do_continue_workshop(ws, by="leader")
                save_state()
            if _vt in ("continue", "complete") and _get_decision_mode(ws.workshop_id) == "vote":
                _settle_vote(ws, mid, _vt)
        # 通用回报接入：harness 通过 task-result 回报结果/发言，统一接入讨论区
        # （http_api 类 harness 也可通过 HTTP 直接回报到这里）
        result = _sanitize_harness_content(
            body.get("result") or body.get("text") or body.get("reply") or ""
        )  # V-14：进入讨论区前做提示注入防护
        hid = body.get("harness_id") or ""
        ok_flag = bool(body.get("ok", True))  # 桥 report_task 回传；旧桥未传默认 True（成功语义）
        # 结构化卡点上报：可选 status(done/blocked/progress/none，大小写不敏感) + summary 短文本
        status = body.get("status") or ""
        summary = body.get("summary") or ""
        if not ok_flag and (status or "").strip().lower() not in ("done", "blocked", "progress"):
            # 系统级失败占位（会话缺失/重建失败等）不是成员真实发言，但必须在讨论区可见：
            # 写入讨论区并标记为失败+系统消息（meta.status=failed / meta.system=True），
            # 同时 notify_leader=False —— 不推进组长事件水位、不参与事件判定，
            # 避免历史死循环「失败回报 → 唤醒组长 → 再次派发」。
            print(f"[harness_reply] {hid} 任务未完成回报（ok=false）已作为失败提示写入讨论区: {result[:100]}", flush=True)
            _apply_harness_reply(body.get("workshop_id", ""), mid or "", result, hid, source="harness_http",
                                 status="failed", summary=summary, notify_leader=False)
        else:
            _apply_harness_reply(body.get("workshop_id", ""), mid or "", result, hid, source="harness_http",
                                 status=status, summary=summary)
    return {"success": True}

@router.post("/api/harness/api-probe")
async def harness_api_probe(request: Request):
    _audit_log.record("harness.api_probe", actor="user", target="", detail="探测 HTTP API")
    from ..server import Utf8JSONResponse, save_state, validate_harness_api_url
    """探测 harness 自带 HTTP API 是否可用（http_api 类唤醒）。
    body: {"harness_id": "..."} 或 {"base_url": "...", "message_path": "/message"}
    若 base_url 缺省则从该 harness 注册信息取 api_base_url。
    探测成功自动把该 harness 的 wakeup_method 升级为 http_api。
    """
    body = await request.json()
    hid = (body.get("harness_id") or "").strip()
    base_url = (body.get("base_url") or "").strip()
    message_path = (body.get("message_path") or "/message").strip()
    if not base_url and hid:
        sess = harness_manager.sessions.get(hid)
        if sess and sess.info:
            base_url = getattr(sess.info, "api_base_url", "") or ""
            message_path = getattr(sess.info, "api_message_path", "") or message_path
    if not base_url:
        return Utf8JSONResponse({"error": "base_url 或 harness_id 需要提供（且该 harness 已配置 api_base_url）"}, status_code=400)
    ok, err = validate_harness_api_url(base_url)
    if not ok:
        return Utf8JSONResponse({"error": f"base_url 校验失败: {err}"}, status_code=400)
    ok, detail = probe_http_api(base_url, message_path)
    if ok and hid:
        sess = harness_manager.sessions.get(hid)
        if sess and sess.info:
            sess.info.wakeup_method = WakeupMethod.HTTP_API
            sess.info.api_base_url = base_url
            sess.info.api_message_path = message_path
            harness_manager.register(sess.info)
            save_state()
    return {"success": ok, "harness_id": hid, "detail": detail}

@router.post("/api/harness/api-message")
async def harness_api_message(request: Request):
    _audit_log.record("harness.api_message", actor="user", target="", detail="HTTP API 消息")
    from ..server import Utf8JSONResponse, validate_harness_api_url
    """向 harness 的 HTTP API 推送一条测试消息（验证 http_api 唤醒链路）。
    body: {"harness_id": "...", "content": "...", "from_id": "..."}
    平台用该 harness 的 api_base_url/api_message_path 推送。
    """
    body = await request.json()
    hid = (body.get("harness_id") or "").strip()
    content = (body.get("content") or "").strip()
    if not hid or not content:
        return Utf8JSONResponse({"error": "harness_id 和 content 必填"}, status_code=400)
    sess = harness_manager.sessions.get(hid)
    if not sess or not sess.info:
        return Utf8JSONResponse({"error": f"harness {hid} 未注册"}, status_code=404)
    base_url = getattr(sess.info, "api_base_url", "") or ""
    message_path = getattr(sess.info, "api_message_path", "") or "/message"
    if not base_url:
        return Utf8JSONResponse({"error": f"harness {hid} 未配置 api_base_url，无法 HTTP 推送"}, status_code=400)
    ok, err = validate_harness_api_url(base_url)
    if not ok:
        return Utf8JSONResponse({"error": f"harness {hid} 的 api_base_url 校验失败: {err}"}, status_code=400)
    from_id = body.get("from_id") or f"agent-community-{hid}"
    ok, detail = send_http_api_message(base_url, content, from_id=from_id, message_path=message_path)
    return {"success": ok, "ok": ok, "harness_id": hid, "detail": detail, "base_url": base_url}

@router.get("/api/harness/{harness_id}/activation_prompt")
async def get_harness_activation_prompt(harness_id: str):
    from ..server import Utf8JSONResponse
    """查看外端 Harness 的唤醒提示词模板（含 {role}/{workspace_dir} 占位符，激活时由平台填充）。"""
    sess = harness_manager.sessions.get(harness_id)
    if not sess:
        return Utf8JSONResponse({"error": "harness not found"}, status_code=404)
    prompt = sess.metadata.get("activation_prompt") or ""
    if not prompt:
        prompt = (sess.info.metadata or {}).get("activation_prompt") or ""
    return {"success": True, "harness_id": harness_id, "agent_id": sess.agent_id, "prompt": prompt}

@router.post("/api/harness/auto-connect")
async def harness_auto_connect(request: Request):
    _audit_log.record("harness.auto_connect", actor="user", target="", detail="自动架桥（启进程）")
    from ..server import Utf8JSONResponse
    """一键自动架桥：按 wakeup_method 自动生成并启动桥进程（用户不当信息中转）。

    http_api  → 平台直接推送，无需桥，直接返回就绪
    file_poll → 后台启动 filepoll_harness_bridge.py
    acp       → 后台启动 acp_harness_bridge.py（需 acp_command 真实可执行）
    clipboard → 不能全自动，返回 manual + 粘贴提示（不阻塞）
    其他      → 后台启动 pending_poll_bridge.py（兜底）
    """
    import os as _os
    import subprocess as _sp
    from ..protocol import WakeupMethod

    body = await request.json()
    hid = str(body.get("harness_id") or "").strip()
    if not hid:
        return Utf8JSONResponse({"error": "缺少 harness_id"}, status_code=400)

    sess = harness_manager.sessions.get(hid)
    if not sess:
        return Utf8JSONResponse({"error": f"harness {hid} 未注册"}, status_code=404)
    info = sess.info

    # 幂等：桥已在跑就直接返回
    existing = harness_manager.bridges.get(hid)
    if existing and getattr(existing, "proc", None) and existing.proc.poll() is None:
        return {"success": True, "harness_id": hid, "status": "connected",
                "bridge_type": "already-running", "pid": existing.proc.pid,
                "note": "桥已在运行"}

    wm = getattr(info, "wakeup_method", None)
    base_url = "http://127.0.0.1:18920"
    # __file__ = .../agent_community/platform/routers/harness.py
    # _routers = .../agent_community/platform/routers
    # _pkg     = .../agent_community          (= _routers/../..)
    # _ex      = .../agent_community/examples
    _here = _os.path.dirname(_os.path.abspath(__file__))          # .../platform/routers
    _pkg = _os.path.dirname(_os.path.dirname(_here))              # .../agent_community
    _ex = _os.path.join(_pkg, "examples")                         # .../agent_community/examples

    # 选桥脚本 + 参数
    bridge_cmd: list[str] | None = None
    bridge_type = ""
    if wm == WakeupMethod.HTTP_API:
        return {"success": True, "harness_id": hid, "status": "ready",
                "bridge_type": "http_api", "note": "http_api 类由平台直接推送，无需桥"}
    elif wm == WakeupMethod.FILE_POLL:
        script = _os.path.join(_ex, "filepoll_harness_bridge.py")
        inbox = (getattr(info, "wakeup_dir", "") or "").strip()
        if not _os.path.isfile(script):
            return Utf8JSONResponse({"error": f"桥脚本不存在: {script}"}, status_code=500)
        if not inbox:
            return {"success": True, "harness_id": hid, "status": "manual",
                    "bridge_type": "file_poll",
                    "note": "file_poll 需要 wakeup_dir（inbox 目录），注册时未填，请手动架桥"}
        bridge_cmd = ["-u", script, "--harness-id", hid, "--inbox", inbox, "--platform", base_url]
        bridge_type = "file_poll"
    elif wm == WakeupMethod.ACP:
        script = _os.path.join(_ex, "acp_harness_bridge.py")
        if not _os.path.isfile(script):
            return Utf8JSONResponse({"error": f"桥脚本不存在: {script}"}, status_code=500)
        if not (getattr(info, "acp_command", "") or "").strip():
            return {"success": True, "harness_id": hid, "status": "manual",
                    "bridge_type": "acp",
                    "note": "acp 类需要 acp_command，注册时未填，请手动架桥"}
        bridge_cmd = ["-u", script, "--harness-id", hid, "--url", base_url]
        bridge_type = "acp"
    elif wm == WakeupMethod.CLIPBOARD:
        return {"success": True, "harness_id": hid, "status": "manual",
                "bridge_type": "clipboard",
                "note": "clipboard 类需在 harness 侧手动粘贴激活提示词，无法全自动。请在 harness 输入框粘贴内容（见激活提示）"}
    else:
        # 兜底：pending_poll
        script = _os.path.join(_ex, "pending_poll_bridge.py")
        if not _os.path.isfile(script):
            return Utf8JSONResponse({"error": f"桥脚本不存在: {script}"}, status_code=500)
        work_dir = (getattr(info, "wakeup_dir", "") or "").strip() or _os.path.join(_pkg, "data", "bridges", hid)
        bridge_cmd = ["-u", script, "--harness-id", hid, "--url", base_url, "--work-dir", work_dir]
        bridge_type = "pending_poll"

    # 后台启动桥进程（DETACHED，父进程退出不带走）
    if bridge_cmd is None:
        return Utf8JSONResponse({"error": "未能确定桥脚本"}, status_code=500)

    # ── V-14 策略闸门：这里是真 subprocess.Popen 启动进程（拍板口径的最严档）──
    # actor 分级是关键：operator（人从本机 UI 点一键注册）走 actor_override 放行，
    # 不堵 V-12 的主流程；external_ai / harness（AI 自主架桥）出厂 ASK，需人批后才启动。
    # 威胁模型是「AI 驱动的代码执行」，不是「操作者自己点按钮」。
    from ..policy import (
        BRIDGE_LAUNCH,
        actor_from_request,
        blocked_message,
        check,
        pending_message,
    )
    _ev, _pending = check(
        BRIDGE_LAUNCH,
        target=f"{hid}:{bridge_type}",
        actor=actor_from_request(request),
    )
    if _ev.blocked:
        return Utf8JSONResponse(
            {"error": blocked_message(_ev), "rule_id": _ev.rule_id}, status_code=403
        )
    if _ev.needs_approval:
        return Utf8JSONResponse(
            {"status": "pending_approval", "op": _ev.op,
             "message": pending_message(_ev, _pending), "pending": _pending},
            status_code=202,
        )

    py_exe = _os.environ.get("AC_PYTHON") or _os.sys.executable
    full_cmd = [py_exe] + bridge_cmd
    try:
        env = dict(_os.environ)
        env["DSH_SUPERVISOR_PID"] = str(_os.getpid())
        if bridge_type == "pending_poll":
            _os.makedirs(work_dir, exist_ok=True)
        proc = _sp.Popen(
            full_cmd,
            stdout=_sp.DEVNULL,
            stderr=_sp.DEVNULL,
            creationflags=_sp.CREATE_NEW_PROCESS_GROUP,
            env=env,
        )
    except Exception as e:
        return Utf8JSONResponse({"error": f"桥启动失败: {e}"}, status_code=500)

    # 等 2 秒验证存活
    import time as _time
    _time.sleep(2.0)
    alive = proc.poll() is None
    return {
        "success": True,
        "harness_id": hid,
        "status": "connected" if alive else "failed",
        "bridge_type": bridge_type,
        "pid": proc.pid if alive else None,
        "cmd": " ".join(full_cmd),
        "note": "" if alive else "桥进程启动后立即退出，请检查 harness 配置",
    }

