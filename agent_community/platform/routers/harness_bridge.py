"""platform/routers/harness_bridge：Harness 桥生成与激活（V-13 自 harness.py 拆分）。

承载 bridge-test / bridge-generate / bridge-path / bridge-verify / prefill / activate / peer-route 等端点。
拆分纪律：路由路径、参数、响应体与拆分前逐字一致（纯搬迁，零行为变更）。
"""
from __future__ import annotations
import sys
from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect
from ..protocol import HarnessInfo
from ..protocol import HarnessMessage
from ..protocol import HarnessStatus
from ..protocol import Message
from ..protocol import MessageType
from pathlib import Path
from ..protocol import WakeupMethod
from fastapi import WebSocketDisconnect
from ..ai_external import run_ai_call as ai_external_run_ai_call
import asyncio
from datetime import datetime
from .. import harness_launcher
from ..harness_adapter import harness_manager
from ..harness_adapter import harness_to_agent_card
import json
import os
from ..api_wakeup import probe_http_api
from ..api_wakeup import send_http_api_message
import time
from uuid import uuid4
from ..state import _bridge_tests_inflight, agents, assistant_history, assistant_history_max, pending_activations, pending_bridge_tests, pending_pre_register, pending_tasks, workshops
from ..protocol import HarnessTool
from ..audit import audit_log as _audit_log
from ..identity import check_request_token as _check_request_token
from ..identity import issue_agent_token as _issue_agent_token

router = APIRouter()

@router.post("/api/harness/bridge-test")
async def harness_bridge_test(request: Request):
    from ..server import Utf8JSONResponse, _file_poll_send, _harness_wakeup_method, _pending_push, _register_bridge_test_inflight
    """平台测试桥功能：向指定 harness 的桥发送一条测试消息。
    分发按 wakeup_method：
      - file_poll：写测试 JSON 到 harness 的 inbox（wakeup_dir），由文件桥/对象侧检测
      - 其余：进 pending_bridge_tests 队列，由通用 pending 桥轮询领取
    对象侧收到后应回报 POST /api/harness/bridge-test-result。
    """
    body = await request.json()
    hid = body.get("harness_id", "")
    if not hid:
        return Utf8JSONResponse({"error": "harness_id required"}, status_code=400)
    sess = harness_manager.sessions.get(hid)
    if not sess:
        return Utf8JSONResponse({"error": f"harness {hid} 未注册"}, status_code=404)
    test_id = uuid4().hex[:12]
    payload = {
        "type": "bridge_test",
        "harness_id": hid,
        "test_id": test_id,
        "sent_at": datetime.now().isoformat(),
        "message": "平台桥测试：请确认桥通道正常，然后回报 bridge-test-result",
        "report_endpoint": "/api/harness/bridge-test-result",
    }
    method = _harness_wakeup_method(hid)
    if method == "file_poll":
        ok = _file_poll_send(hid, payload)
        if not ok:
            return Utf8JSONResponse({"error": "写入 inbox 失败（wakeup_dir 不可写）"}, status_code=500)
        print(f"[bridgetest] file_poll 已写 inbox: {hid} test_id={test_id}", flush=True)
    else:
        _pending_push(pending_bridge_tests, hid, payload)
        print(f"[bridgetest] 已入 pending 队列: {hid} test_id={test_id}", flush=True)
    _register_bridge_test_inflight(hid, test_id)  # V-17：登记待回报测试，供回报归属校验
    return {"success": True, "harness_id": hid, "test_id": test_id, "sent_via": method}

@router.get("/api/harness/pending-bridge-tests")
async def harness_pending_bridge_tests(harness_id: str):
    """harness 桥轮询：领取该 harness 名下的桥测试任务。"""
    tests = pending_bridge_tests.pop(harness_id, [])
    return {"tests": tests}

@router.post("/api/harness/bridge-test-result")
async def harness_bridge_test_result(request: Request):
    from ..server import Utf8JSONResponse, save_state
    """harness 桥回报测试结果（对象侧确认桥通道正常）。"""
    body = await request.json()
    hid = body.get("harness_id", "")
    if not hid:
        return Utf8JSONResponse({"error": "harness_id required"}, status_code=400)
    sess = harness_manager.sessions.get(hid)
    if not sess:
        return Utf8JSONResponse({"error": f"harness {hid} 未注册"}, status_code=404)
    # V-17 归属校验：test_id 必须匹配平台近期发出的测试，且该测试未被回报过
    inflight = _bridge_tests_inflight.get(hid)
    body_test_id = str(body.get("test_id") or "").strip()
    if not inflight or inflight.get("test_id") != body_test_id:
        print(f"[安全][bridgetest] {hid} 回报 test_id={body_test_id!r} 与平台发出记录不匹配，已拒绝", flush=True)
        return Utf8JSONResponse(
            {"error": "桥测试回报不匹配：test_id 与平台发出的测试不一致（请先由平台发起测试）"},
            status_code=403,
        )
    if inflight.get("reported"):
        print(f"[安全][bridgetest] {hid} 重复回报 test_id={body_test_id}，已拒绝", flush=True)
        return Utf8JSONResponse({"error": "该测试已回报过，拒绝重复提交"}, status_code=403)
    if time.time() > inflight.get("expire_ts", 0):
        _bridge_tests_inflight.pop(hid, None)
        print(f"[安全][bridgetest] {hid} 回报超期（test_id={body_test_id}），已拒绝", flush=True)
        return Utf8JSONResponse({"error": "测试回报已过期，请重新发起桥测试"}, status_code=403)
    inflight["reported"] = True
    ok = bool(body.get("ok"))
    record = {
        "test_id": body.get("test_id", ""),
        "ok": ok,
        "echo": body.get("echo", ""),
        "at": datetime.now().isoformat(),
    }
    sess.metadata["bridge_test"] = record
    # 同步到 info.metadata 以便 save_state 持久化（session 层 metadata 不落盘）
    sess.info.metadata = dict(sess.info.metadata or {})
    sess.info.metadata["bridge_test"] = record
    if ok:
        sess.info.bridge_status = "tested"
    print(f"[bridgetest] {hid} 测试结果: ok={ok} echo={body.get('echo','')[:60]}", flush=True)
    save_state()
    return {"success": True, "harness_id": hid, "ok": ok}

@router.get("/api/harness/bridge-templates")
async def harness_bridge_templates():
    """列出平台内置桥模板库（按接口类型划分），供平台 Agent / 外部选择模板。"""
    from ..bridge_factory import list_templates
    templates = list_templates()
    return {"success": True, "count": len(templates), "templates": templates}

@router.post("/api/harness/{harness_id}/bridge/generate")
async def harness_bridge_generate(harness_id: str, request: Request):
    from ..server import Utf8JSONResponse, _platform_base_url, save_state
    """平台标准构桥 API：按 harness 注册信息渲染内置模板生成桥脚本。
    治本设计：平台内置桥模板库（cli_acp / file_poll / pending_poll 等），此处只做模板复制+配置注入的机械操作，
    不依赖 LLM 自由设计。生成后自动登记桥坐标（bridge_dir）。
    body: {"template": "file_poll", "out_dir": "可选输出目录"}
    """
    from ..bridge_factory import generate, safe_slug, list_templates, BridgeTemplateError
    try:
        body = await request.json()
    except Exception:
        body = {}
    template = (body.get("template") or "cli_acp").strip()
    out_dir = (body.get("out_dir") or "").strip()
    sess = harness_manager.sessions.get(harness_id)
    if not sess:
        return Utf8JSONResponse({"error": f"harness {harness_id} 未注册"}, status_code=404)
    available = {t["name"] for t in list_templates()}
    if template not in available:
        return Utf8JSONResponse({"error": f"模板不存在: {template}，可用: {sorted(available)}"}, status_code=400)
    info = sess.info
    # 平台自址：桥用同一地址回连
    try:
        platform_url = str(request.base_url).rstrip("/")
    except Exception:
        platform_url = _platform_base_url()
    # 按模板 interface_type 组装参数（模板 required_fields 兜底校验）
    if template == "cli_acp":
        acp_command = (info.acp_command or "").strip()
        if not acp_command:
            return Utf8JSONResponse(
                {"error": f"harness {harness_id} 未配置 acp_command，无法生成 cli_acp 桥"},
                status_code=400,
            )
        ai = info.ai
        params = {
            "HARNESS_ID": harness_id,
            "ACP_COMMAND": acp_command,
            "ACP_CWD": (info.acp_cwd or "").strip() or str(Path(__file__).resolve().parent.parent),
            "MODEL_NAME": (getattr(ai, "model_name", "") if ai else "") or "unknown-model",
            "PROVIDER": (getattr(ai, "provider", "") if ai else "") or "unknown",
            "DESCRIPTION": (getattr(ai, "description", "") if ai else "") or f"agent_community harness {harness_id}",
        }
    elif template == "file_poll":
        wakeup_dir = (info.wakeup_dir or "").strip()
        if not wakeup_dir:
            return Utf8JSONResponse(
                {"error": f"harness {harness_id} 未配置 wakeup_dir（inbox），无法生成 file_poll 桥"},
                status_code=400,
            )
        params = {
            "HARNESS_ID": harness_id,
            "INBOX_DIR": wakeup_dir,
            "TRIGGER_CMD": (body.get("trigger_cmd") or "").strip(),
            "PLATFORM_URL": platform_url,
        }
    elif template == "pending_poll":
        work_dir = (body.get("work_dir") or "").strip()
        if not work_dir:
            work_dir = str(Path(__file__).resolve().parent.parent / "bridges" / safe_slug(harness_id))
        params = {
            "HARNESS_ID": harness_id,
            "WORK_DIR": work_dir,
            "TRIGGER_CMD": (body.get("trigger_cmd") or "").strip(),
            "PLATFORM_URL": platform_url,
        }
    else:
        return Utf8JSONResponse({"error": f"暂不支持自动生成模板: {template}"}, status_code=400)
    bridges_root = Path(__file__).resolve().parent.parent / "bridges"
    if out_dir:
        # V-6 修复：out_dir 必须位于平台 bridges 根目录内，防任意文件写入
        p = Path(out_dir)
        if not p.is_absolute():
            p = bridges_root / p
        try:
            p.resolve().relative_to(bridges_root.resolve())
        except (ValueError, OSError):
            return Utf8JSONResponse(
                {"error": f"out_dir 必须位于平台 bridges 目录内: {bridges_root}"},
                status_code=400,
            )
        target = p
    else:
        target = bridges_root / safe_slug(harness_id)
    try:
        bridge_file = generate(template, params, target)
    except BridgeTemplateError as e:
        return Utf8JSONResponse({"error": f"桥生成参数校验失败: {e}"}, status_code=400)
    except Exception as e:
        return Utf8JSONResponse({"error": f"桥生成失败: {e}"}, status_code=500)
    # 自动登记桥坐标
    sess.info.bridge_dir = str(target)
    if sess.info.bridge_status != "tested":
        sess.info.bridge_status = "reported"
    save_state()
    return {
        "success": True,
        "harness_id": harness_id,
        "template": template,
        "bridge_file": str(bridge_file),
        "bridge_dir": str(target),
        "bridge_status": sess.info.bridge_status,
        "launch_hint": f'python -u "{bridge_file}" --url {_platform_base_url()}',
    }

@router.post("/api/harness/bridge-path")
async def harness_bridge_path(request: Request):
    from ..server import Utf8JSONResponse, save_state
    """对象建好桥、平台测试通过后，对象告知桥文件路径，平台记录到 harness 信息。
    body: {"harness_id": "...", "bridge_dir": "桥文件所在文件夹路径"}
    """
    body = await request.json()
    hid = body.get("harness_id", "")
    bridge_dir = (body.get("bridge_dir") or "").strip()
    if not hid:
        return Utf8JSONResponse({"error": "harness_id required"}, status_code=400)
    if not bridge_dir:
        return Utf8JSONResponse({"error": "bridge_dir required"}, status_code=400)
    sess = harness_manager.sessions.get(hid)
    if not sess:
        return Utf8JSONResponse({"error": f"harness {hid} 未注册"}, status_code=404)
    sess.info.bridge_dir = bridge_dir
    if sess.info.bridge_status != "tested":
        sess.info.bridge_status = "reported"
    print(f"[bridge] {hid} 桥坐标已记录: {bridge_dir} status={sess.info.bridge_status}", flush=True)
    save_state()
    return {"success": True, "harness_id": hid, "bridge_dir": bridge_dir, "bridge_status": sess.info.bridge_status}

@router.post("/api/harness/{harness_id}/activate")
async def harness_activate_window(harness_id: str):
    from ..server import Utf8JSONResponse, _activate_windows_by_pids, _find_bridge_processes
    """Harness 监控室：将指定 harness 对应的软件窗口激活并置顶。"""
    sess = harness_manager.sessions.get(harness_id)
    if not sess:
        return Utf8JSONResponse({"error": f"harness {harness_id} 未注册"}, status_code=404)
    info = getattr(sess, "info", None)
    bridge_dir = (getattr(info, "bridge_dir", "") or "") if info else ""
    hints = [harness_id]
    if info:
        hn = (getattr(info, "harness_name", "") or "").strip()
        if hn and hn != harness_id:
            hints.append(hn)
    procs = await asyncio.to_thread(_find_bridge_processes, harness_id, bridge_dir)
    pids = [p.get("pid") for p in procs if isinstance(p, dict) and p.get("pid")]
    result = await asyncio.to_thread(_activate_windows_by_pids, pids, hints)
    result["harness_id"] = harness_id
    if not result.get("success"):
        result["hint"] = "未找到该 harness 的可激活窗口（桥进程可能无主窗口）"
    return result

@router.post("/api/harness/bridge-verify")
async def harness_bridge_verify(request: Request):
    from ..server import Utf8JSONResponse, _file_poll_send, _find_bridge_processes, _harness_wakeup_method, _pending_push, _register_bridge_test_inflight, save_state
    """平台级桥验证：综合检查桥坐标、桥进程、历史测试，并实时发测试等待真实回报。
    判定标准：实时通道检查必须收到对象侧回报（test_id 匹配且 ok=true）才算通过，
    不采信页面上的 bridge_status 字段（防状态造假/残留）。
    """
    body = await request.json()
    hid = body.get("harness_id", "")
    if not hid:
        return Utf8JSONResponse({"error": "harness_id required"}, status_code=400)
    try:
        timeout = min(max(int(body.get("timeout", 10)), 2), 30)
    except Exception:
        timeout = 10
    sess = harness_manager.sessions.get(hid)
    if not sess:
        return Utf8JSONResponse({"error": f"harness {hid} 未注册"}, status_code=404)
    info = sess.info
    bridge_dir = getattr(info, "bridge_dir", "") or ""
    # 1) 桥坐标检查
    dir_check = {"recorded": bool(bridge_dir), "exists": False, "path": bridge_dir}
    if bridge_dir:
        dir_check["exists"] = os.path.isdir(bridge_dir)
        if dir_check["exists"]:
            try:
                files = sorted(p.name for p in Path(bridge_dir).iterdir())
                dir_check["file_count"] = len(files)
                dir_check["recent_files"] = files[-5:]
            except Exception as e:
                dir_check["file_count"] = -1
                dir_check["error"] = str(e)
    # 2) 桥进程检查（与删除清理同一匹配口径，只查不杀）
    procs = await asyncio.to_thread(_find_bridge_processes, hid, bridge_dir)
    proc_check = {"running": len(procs) > 0, "processes": procs[:5]}
    # 3) 历史测试记录（仅参考，不作为通过依据）
    bt = (info.metadata or {}).get("bridge_test") or {}
    hist_check = {
        "has_record": bool(bt.get("test_id")),
        "ok": bool(bt.get("ok")),
        "at": bt.get("at", ""),
    }
    # 4) 实时通道检查：发新测试，等对象侧真实回报
    test_id = uuid4().hex[:12]
    payload = {
        "type": "bridge_test",
        "harness_id": hid,
        "test_id": test_id,
        "sent_at": datetime.now().isoformat(),
        "message": "平台桥验证：请确认桥通道正常，然后回报 bridge-test-result",
        "report_endpoint": "/api/harness/bridge-test-result",
    }
    method = _harness_wakeup_method(hid)
    if method == "file_poll":
        send_ok = _file_poll_send(hid, payload)
    else:
        send_ok = _pending_push(pending_bridge_tests, hid, payload)
    _register_bridge_test_inflight(hid, test_id)  # V-17：登记待回报测试，供回报归属校验
    channel_check = {"sent": send_ok, "sent_via": method, "test_id": test_id, "reported": False, "ok": False}
    if send_ok:
        loop = asyncio.get_event_loop()
        deadline = loop.time() + timeout
        while loop.time() < deadline:
            rec = sess.metadata.get("bridge_test") or {}
            if rec.get("test_id") == test_id:
                channel_check["reported"] = True
                channel_check["ok"] = bool(rec.get("ok"))
                channel_check["echo"] = rec.get("echo", "")
                channel_check["reported_at"] = rec.get("at", "")
                break
            await asyncio.sleep(0.5)
    # 汇总：目录存在 + 进程在跑 + 实时回报 ok 才算通过
    ok = bool(dir_check["exists"] and proc_check["running"] and channel_check["sent"] and channel_check["ok"])
    report = {
        "harness_id": hid,
        "checked_at": datetime.now().isoformat(),
        "bridge_dir": dir_check,
        "bridge_process": proc_check,
        "history_test": hist_check,
        "live_channel": channel_check,
        "ok": ok,
    }
    # 验证结果留痕并持久化
    sess.metadata["bridge_verify"] = report
    sess.info.metadata = dict(sess.info.metadata or {})
    sess.info.metadata["bridge_verify"] = report
    save_state()
    print(f"[verify] {hid} 验证完成 ok={ok} dir={dir_check['exists']} proc={proc_check['running']} "
          f"live_sent={channel_check['sent']} live_ok={channel_check['ok']}", flush=True)
    return report

@router.post("/api/harness/prefill")
async def harness_prefill(request: Request):
    from ..server import Utf8JSONResponse, validate_acp_command, validate_callback_url, validate_harness_api_url, validate_wakeup_dir
    """AI 识别智能填入：根据自然语言描述提取 harness 结构化字段。"""
    body = await request.json()
    text = (body.get("text") or "").strip()
    if not text:
        return Utf8JSONResponse({"error": "描述不能为空"}, status_code=400)
    if not _sv().ai_provider:
        return Utf8JSONResponse({"error": "AI Provider 未配置，无法智能填入"}, status_code=400)
    system = (
        "你是 harness 信息解析助手。从用户对 harness 的描述中提取结构化字段，只输出 JSON，不要任何解释。\n"
        '输出格式：{"harness_id":"","harness_name":"","harness_type":"","model_name":"","provider":"","capabilities":["..."],"wakeup_method":"","callback_url":"","wakeup_dir":"","acp_command":"","acp_cwd":"","description":""}\n'
        "规则：harness_id 用完整名称（与显示名一致），不要用简写；capabilities 用简短能力标签（如 coding/file_ops/web_search）；"
        "wakeup_method 从 http/file_poll/clipboard/acp 里选（描述提到'回调地址/HTTP POST'→http，'文件轮询/监听目录'→file_poll，'剪贴板'→clipboard，'ACP/子进程/CLI 拉起'→acp）；description 保留坐标、能力、限制。"
    )
    try:
        reply = await ai_external_run_ai_call(
            _sv().ai_provider.chat(system, f"用户对 harness 的描述：\n{text}"),
            label="server.harness_smart_fill",
        )
        start, end = reply.find("{"), reply.rfind("}") + 1
        if start >= 0 and end > start:
            fields = json.loads(reply[start:end])
            if not isinstance(fields, dict):
                return {"success": False, "error": "AI 返回 JSON 不是对象"}
            # V-15 修复：对 AI 回填的关键字段做安全校验，非法值剔除并附警告，
            # 防止注入的 acp_command / 恶意回调 URL 被直接回填进注册表单。
            warnings: list[str] = []
            for uf in ("callback_url", "wakeup_url"):
                uv = str(fields.get(uf) or "").strip()
                if uv:
                    ok, err = validate_callback_url(uv)
                    if not ok:
                        fields[uf] = ""
                        warnings.append(f"{uf} 非法已清空: {err}")
            ab = str(fields.get("api_base_url") or "").strip()
            if ab:
                ok, err = validate_harness_api_url(ab)
                if not ok:
                    fields["api_base_url"] = ""
                    warnings.append(f"api_base_url 非法已清空: {err}")
            ac = str(fields.get("acp_command") or "").strip()
            if ac:
                ok, err = validate_acp_command(ac)
                if not ok:
                    fields["acp_command"] = ""
                    warnings.append(f"acp_command 非法已清空: {err}")
            wd = str(fields.get("wakeup_dir") or "").strip()
            if wd:
                ok, err = validate_wakeup_dir(wd)
                if not ok:
                    fields["wakeup_dir"] = ""
                    warnings.append(f"wakeup_dir 非法已清空: {err}")
            resp: dict = {"success": True, "fields": fields}
            if warnings:
                resp["warnings"] = warnings
            return resp
        return {"success": False, "error": "AI 返回无法解析为 JSON"}
    except Exception as e:
        return {"success": False, "error": f"智能填入失败: {e}"}

@router.post("/api/harness/peer-route")
async def get_peer_route(request: Request):
    from ..server import Utf8JSONResponse
    """查询目标 Harness Agent 的路由信息，供直连委托/审查使用。
    平台退为路由注册中心：当双 Harness 均在线时，
    委托和审查不再经平台中转，而是直接 HTTP POST 到对端。
    """
    body = await request.json()
    to_agent_id = body.get("to_agent_id", "")
    to_hid = harness_manager.id_to_harness.get(to_agent_id)
    if not to_hid:
        return Utf8JSONResponse(
            {"error": "target agent is not a harness"}, status_code=404
        )
    sess = harness_manager.sessions.get(to_hid)
    if not sess or sess.status != HarnessStatus.ONLINE:
        return Utf8JSONResponse({"error": "target harness offline"}, status_code=404)
    return {
        "harness_id": to_hid,
        "agent_id": to_agent_id,
        "callback_url": sess.info.callback_url,
        "transport": sess.transport.value,
        "harness_name": sess.info.harness_name,
    }

