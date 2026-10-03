"""platform/routers/plugins：插件注册表与执行接口（V-9 自 server.py 拆分）。

含：
- _load_plugins / _save_plugins：plugins.json 读写（原子写）
- GET/POST /api/plugins、DELETE /api/plugins/{name}
- POST /api/plugins/{name}/invoke：http/cmd 插件执行（V-18 强制 Token 鉴权 + 危险命令拦截）
"""
from __future__ import annotations

import json

from fastapi import APIRouter, Request

from ..core.security import _is_dangerous  # 共享层，不依赖 server，可顶层导入
from ..policy import (
    PLUGIN_INSTALL,
    PLUGIN_INVOKE,
    SHELL_EXEC,
    actor_from_request,
    blocked_message,
    check,
    pending_message,
)
from ..audit import audit_log as _audit_log

router = APIRouter()


def _policy_gate(op: str, target: str, request: Request):
    """统一策略闸门。返回 None 表示放行；否则返回该直接回给客户端的响应。

    - DENY → 403
    - ASK  → 202 + pending（**不执行**，等人批）
    """
    from ..server import Utf8JSONResponse  # 延迟引用，避免循环导入
    ev, pending = check(op, target=target, actor=actor_from_request(request))
    if ev.blocked:
        return Utf8JSONResponse(
            {"error": blocked_message(ev), "rule_id": ev.rule_id, "op": ev.op},
            status_code=403,
        )
    if ev.needs_approval:
        return Utf8JSONResponse(
            {"status": "pending_approval", "op": ev.op,
             "message": pending_message(ev, pending), "pending": pending},
            status_code=202,
        )
    return None


def _load_plugins() -> dict:
    from ..server import PLUGINS_FILE  # 延迟引用，避免循环导入
    try:
        if PLUGINS_FILE.exists():
            return json.loads(PLUGINS_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"[plugins] 读取失败: {e!r}", flush=True)
    return {}


def _save_plugins(data: dict):
    from ..server import PLUGINS_FILE, _atomic_write_text  # 延迟引用
    _atomic_write_text(PLUGINS_FILE, json.dumps(data, ensure_ascii=False, indent=2))


@router.get("/api/plugins")
async def api_plugins_list():
    return {"plugins": _load_plugins()}


@router.post("/api/plugins")
async def api_plugins_add(request: Request):
    _audit_log.record("plugin.install", actor="user", target="", detail="注册插件")
    from ..server import PLUGIN_TYPES, Utf8JSONResponse, now_iso  # 延迟引用
    body = await request.json()
    name = str(body.get("name") or "").strip()
    ptype = str(body.get("type") or "").strip().lower()
    target = str(body.get("target") or "").strip()
    if not name or not target:
        return Utf8JSONResponse({"error": "name 与 target 不能为空"}, status_code=400)
    if ptype not in PLUGIN_TYPES:
        return Utf8JSONResponse({"error": f"type 仅支持 {'/'.join(PLUGIN_TYPES)}"}, status_code=400)
    if ptype in ("http", "mcp") and not target.startswith(("http://", "https://")):
        return Utf8JSONResponse({"error": "http 类型 target 须为 http(s):// 开头"}, status_code=400)
    # V-14 策略闸门：注册插件=埋下可执行能力（尤其 cmd 型），出厂 ASK
    gated = _policy_gate(PLUGIN_INSTALL, f"{name}({ptype})", request)
    if gated is not None:
        return gated
    plugs = _load_plugins()
    if name in plugs:
        return Utf8JSONResponse({"error": f"插件 [{name}] 已存在"}, status_code=400)
    plugs[name] = {"type": ptype, "target": target, "created_at": now_iso()}
    _save_plugins(plugs)
    return {"success": True, "plugins": plugs}


@router.delete("/api/plugins/{name}")
async def api_plugins_del(name: str):
    _audit_log.record("plugin.remove", actor="user", target=name, detail="删除插件")
    from ..server import Utf8JSONResponse  # 延迟引用
    plugs = _load_plugins()
    if name not in plugs:
        return Utf8JSONResponse({"error": f"插件 [{name}] 不存在"}, status_code=404)
    del plugs[name]
    _save_plugins(plugs)
    return {"success": True, "plugins": plugs}


@router.post("/api/plugins/{name}/invoke")
async def api_plugins_invoke(name: str, request: Request):
    # V-18：插件执行接口强制鉴权——本地请求同样校验，未配置 Token 时整接口禁用
    _audit_log.record("plugin.invoke", actor="user", target=name, detail="调用插件")
    from ..server import ALLOWED_TOKENS, Utf8JSONResponse, _extract_token  # 延迟引用
    if not ALLOWED_TOKENS:
        return Utf8JSONResponse({"error": "插件执行接口未配置访问 Token，已禁用"}, status_code=403)
    if _extract_token(request) not in ALLOWED_TOKENS:
        return Utf8JSONResponse({"error": "需要有效的 Token 认证（Authorization: Bearer <token> 或 X-API-Key）"}, status_code=401)
    plugs = _load_plugins()
    if name not in plugs:
        return Utf8JSONResponse({"error": f"插件 [{name}] 不存在"}, status_code=404)
    plug = plugs[name]
    try:
        if plug["type"] == "http":
            # V-14 策略闸门：出网（出厂 ALLOW，可配 DENY 拦外泄域名）
            gated = _policy_gate(PLUGIN_INVOKE, plug["target"], request)
            if gated is not None:
                return gated
            import httpx
            async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
                resp = await client.get(plug["target"])
            text = (resp.text or "")[:500]
            return {"success": True, "output": f"HTTP {resp.status_code} · {text}"}
        elif plug["type"] == "mcp":
            # V-14 策略闸门：MCP 远程调用（出厂 ALLOW）
            gated = _policy_gate(PLUGIN_INVOKE, plug["target"], request)
            if gated is not None:
                return gated
            # MCP 工具：连接远程 MCP 服务器，单工具直接调用，多工具返回清单
            from ..mcp_client import mcp_call_tool, mcp_list_tools
            _url = plug["target"]
            try:
                _tools = await mcp_list_tools(_url)
            except Exception as e:
                return {"success": False, "output": f"MCP tools/list 失败: {e}"}
            if not _tools:
                return {"success": False, "output": "MCP 服务器未提供工具"}
            if len(_tools) == 1:
                _tname = _tools[0].get("name")
                try:
                    _res = await mcp_call_tool(_url, _tname, {})
                except Exception as e:
                    return {"success": False, "output": f"MCP tools/call 失败: {e}"}
                return {"success": bool(_res["ok"]), "output": f"[MCP:{_tname}] {str(_res['text'])[:2000]}"}
            _names = "、".join(str(_t.get("name", "")) for _t in _tools[:20])
            return {"success": True, "output": f"MCP 服务器有 {len(_tools)} 个工具：{_names}（带参数调用请注册为外端 Agent）"}

        else:  # cmd
            # V-9c 修复：危险拦截必须作用于"实际执行的命令"。
            # 之前仅校验注册时 target，body 里的 command 未参与执行与校验，
            # 导致 format / net stop / rm -rf 等危险命令可经 invoke 放行。
            _body = await request.json()
            _cmd = str(_body.get("command") or plug["target"]).strip()
            if not _cmd:
                return Utf8JSONResponse({"error": "command 不能为空"}, status_code=400)
            # V-14 策略闸门：cmd 插件是 `subprocess.Popen(shell=True)` 的真执行，
            # 故按 **执行类（SHELL_EXEC）** 处理而非 plugin.invoke —— 这样可以自动
            # 复用 builtin.danger 黑名单规则（该规则 op 就是 shell.exec），
            # 并且 target 传实际命令串，黑名单才检得到东西。
            gated = _policy_gate(SHELL_EXEC, _cmd, request)
            if gated is not None:
                return gated
            _hit = _is_dangerous(_cmd)
            if _hit:
                return Utf8JSONResponse({"error": f"插件命令被安全策略拦截: {_hit}"}, status_code=400)
            import subprocess
            proc = subprocess.Popen(
                _cmd, shell=True, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
            )
            try:
                out, _ = proc.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                out, _ = proc.communicate()
                return {"success": True, "output": f"[已运行超5s被终止] pid={proc.pid} · {(out or '')[:500]}"}
            return {"success": True, "output": f"[exit {proc.returncode}] {(out or '')[:500]}"}
    except Exception as e:
        return Utf8JSONResponse({"error": f"调用失败: {e!r}"}, status_code=500)
