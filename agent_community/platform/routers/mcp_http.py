# -*- coding: utf-8 -*-
"""`routers/mcp_http`：**平台当 MCP 服务端（HTTP 传输）** —— 让对方的 agent 真来调我们（`V104` §7）。

## 为什么要有它（老板 2026-10-25：「其实根本没给到它，只是平台在做梦」）

之前的"测一次"是平台去调**对方的插件**；那个插件跟平台一样，只是**另一个进程**，
它替应用往数据库里插一行 —— 于是平台看到"口令带回来了、库里也有"，就报"通了"，
而**那个 agent 本人从来没参与过**。这不叫发给它，这叫平台自言自语。

真正的"给到它"只有一种：**它自己来调我们**。这时平台是服务端，它发过来的每一个请求
（`initialize` / `tools/list` / `tools/call`）都是**它本人真的来过**的硬证据。

## 与 `agent_community/mcp_server.py` 的分工（一处真相）

- `mcp_server.py`：**stdio** 版（给能起子进程的客户端：Claude Code / Cursor）；
  工具定义与实现在那里，**本文件只做传输层**，复用同一份 `TOOLS`（不重写工具）。
- 本文件：**HTTP（Streamable HTTP）** 版，给桌面类应用（它们只能填 URL）；
  外加**每一次调用都留痕**（`GET /api/mcp/calls`）—— 那就是"它到底来没来"的证据。

## 端点

```
POST /mcp                 # MCP JSON-RPC（initialize / notifications/* / tools/list / tools/call / ping）
GET  /api/mcp/calls       # 最近 N 次真实调用（客户端名/工具/参数键/耗时/成败/时间）
```
"""
from __future__ import annotations

import json
import threading
import time
import uuid
from collections import deque

from fastapi import APIRouter, Request

router = APIRouter()

#: 最近多少次调用留痕（环形，最多这么多条）
MAX_CALLS = 300
_CALLS: deque = deque(maxlen=MAX_CALLS)
_LOCK = threading.Lock()
#: 会话 id → 客户端身份（`initialize` 时记下，后面按它认人）
_SESSIONS: dict = {}
#: 有没有从磁盘读过旧留痕（**平台重启后「它来没来过」不该丢**，2026-10-25）
_LOADED = [False]


def _calls_file():
    """留痕落盘位置：跟着数据目录走（`AC_DATA_DIR`），不进仓库。"""
    from ..server import DATA_DIR

    return DATA_DIR / "mcp_calls.json"


def _load_once() -> None:
    if _LOADED[0]:
        return
    _LOADED[0] = True
    try:
        p = _calls_file()
        if p.is_file():
            import json as _json

            items = _json.loads(p.read_text(encoding="utf-8") or "[]")
            with _LOCK:
                for it in (items or [])[-MAX_CALLS:]:
                    if isinstance(it, dict):
                        _CALLS.append(it)
    except Exception as e:                                   # noqa: BLE001 — 读不到就说一次，不影响服务
        print("[mcp] 旧调用留痕读不出来（原因是 %s: %s）；本次从空开始" % (type(e).__name__, e),
              flush=True)


def _persist() -> None:
    """把留痕写盘（每次真调用写一次；量很小 —— 它是人/agent 触发的，不是热路径）。"""
    try:
        import json as _json

        p = _calls_file()
        p.parent.mkdir(parents=True, exist_ok=True)
        with _LOCK:
            items = list(_CALLS)
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(_json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(p)
    except Exception as e:                                   # noqa: BLE001 — 落盘失败要喊一次（证据会丢）
        print("[mcp] 调用留痕没写进 %s：%s: %s" % (_calls_file(), type(e).__name__, e),
              flush=True)


def reset_for_tests() -> None:
    """测试用：清空内存与"已加载"标记（不动磁盘）。"""
    with _LOCK:
        _CALLS.clear()
        _SESSIONS.clear()
    _LOADED[0] = False


def _tools() -> list:
    """工具表**唯一真相**在 `agent_community/mcp_server.py`（这里不重写）。"""
    from agent_community import mcp_server as _srv

    return list(getattr(_srv, "TOOLS", []) or [])


def _record(kind: str, **kw) -> None:
    with _LOCK:
        _CALLS.append(dict({"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "kind": kind,
                            "at_ms": int(time.time() * 1000), **kw}))
    _persist()


def _client_of(session_id: str) -> str:
    with _LOCK:
        return str((_SESSIONS.get(session_id) or {}).get("name") or "")


def recent_calls(limit: int = 50) -> list:
    """最近 N 次调用（新的在前）。判据/界面都看它：**空 = 它一次都没来过**。"""
    _load_once()
    n = max(1, min(int(limit or 50), MAX_CALLS))
    with _LOCK:
        items = list(_CALLS)[-n:]
    return list(reversed(items))


def _ok(payload: dict, req_id=None) -> dict:
    return {"jsonrpc": "2.0", "id": req_id, "result": payload}


def _err(code: int, msg: str, req_id=None) -> dict:
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": msg}}


def _call_tool(name: str, args: dict) -> tuple:
    """调一个平台工具（**与 stdio 版同一份实现**）；返回 (ok, text)。"""
    for t in _tools():
        if t.get("name") == name:
            try:
                out = (t.get("fn") or (lambda a: {}))(dict(args or {}))
            except Exception as e:                              # noqa: BLE001 — 工具抛错要如实回给客户端
                return False, json.dumps({"error": "%s: %s" % (type(e).__name__, e)},
                                         ensure_ascii=False)
            text = out if isinstance(out, str) else json.dumps(out, ensure_ascii=False)
            return True, text
    return False, json.dumps({"error": "没有这个工具：%s" % name,
                              "tools": [t.get("name") for t in _tools()]}, ensure_ascii=False)


@router.post("/mcp")
async def mcp_http(request: Request):
    """MCP over HTTP（Streamable HTTP 的最小可用实现）。

    - `initialize` 回协议版本 + 能力 + serverInfo，并**记下调用方是谁**（`clientInfo`）；
    - `notifications/*` 回 202/200 空体（通知不需要 id）；
    - `tools/list` / `tools/call` 与 stdio 版**同一份工具表**；
    - 每次真调用都进留痕（`GET /api/mcp/calls`）。
    """
    from fastapi.responses import JSONResponse, Response

    try:
        body = await request.json()
    except Exception:                                          # noqa: BLE001 — 解析不了就说清
        return JSONResponse(_err(-32700, "JSON 解析失败"), status_code=400)
    if not isinstance(body, dict):
        return JSONResponse(_err(-32600, "只接受一条 JSON-RPC 请求"), status_code=400)

    method = str(body.get("method") or "")
    req_id = body.get("id")
    params = body.get("params") or {}
    # ★2026-10-25：头名大小写按客户端五花八门（Mcp-Session-Id / mcp-session-id …），
    #   直接 .get() 在有些客户端下拿不到 ⇒ 留痕里是谁来的变成空。这里**逐个按小写比**。
    sid = ""
    for _k, _v in (request.headers or {}).items():
        if str(_k).lower() == "mcp-session-id":
            sid = str(_v or "")
            break

    if method == "initialize":
        cname = str(((params.get("clientInfo") or {}).get("name")) or "")
        sid = sid or uuid.uuid4().hex
        with _LOCK:
            _SESSIONS[sid] = {"name": cname, "at_ms": int(time.time() * 1000),
                              "version": str(((params.get("clientInfo") or {}).get("version")) or "")}
        _record("initialize", client=cname, session=sid,
                ua=str(request.headers.get("user-agent") or "")[:120])
        from agent_community.mcp_server import SUPPORTED_PROTOCOL_VERSIONS

        want = str(params.get("protocolVersion") or "")
        ver = want if want in SUPPORTED_PROTOCOL_VERSIONS else SUPPORTED_PROTOCOL_VERSIONS[0]
        return JSONResponse(_ok({"protocolVersion": ver,
                                 "capabilities": {"tools": {"listChanged": False}},
                                 "serverInfo": {"name": "external-agent-community",
                                                "version": "4"}}, req_id),
                            headers={"Mcp-Session-Id": sid})

    if method.startswith("notifications/"):
        return Response(status_code=202)

    if method == "ping":
        return JSONResponse(_ok({}, req_id))

    if method == "tools/list":
        _record("tools/list", client=_client_of(sid), session=sid)
        return JSONResponse(_ok({"tools": [{"name": t.get("name"),
                                            "description": t.get("description", ""),
                                            "inputSchema": t.get("inputSchema") or
                                                           {"type": "object", "properties": {}}}
                                          for t in _tools()]}, req_id))

    if method == "tools/call":
        import asyncio

        name = str(params.get("name") or "")
        args = params.get("arguments") or {}
        started = time.time()
        # ★必须丢到线程里跑：工具实现是**同步**的，而且会回头调本平台的 REST（/api/status 等）。
        #   在事件循环里直接跑会把循环占死 ⇒ 它自己的那一条 HTTP 请求永远等不到响应
        #   （实测：status 报 "TimeoutError: timed out"）。
        ok, text = await asyncio.to_thread(_call_tool, name,
                                           args if isinstance(args, dict) else {})
        _record("tools/call", client=_client_of(sid), session=sid, tool=name,
                ok=bool(ok), args_keys=sorted((args or {}).keys())[:12],
                elapsed_ms=int((time.time() - started) * 1000))
        return JSONResponse(_ok({"content": [{"type": "text", "text": text}],
                                 "isError": not ok}, req_id))

    _record("unknown", client=_client_of(sid), session=sid, method=method)
    return JSONResponse(_err(-32601, "不支持的方法：%s" % method, req_id), status_code=200)


@router.get("/mcp")
async def mcp_get_not_supported():
    """**如实说**：本平台只实现 Streamable HTTP 的 **POST** 一半。

    有的 MCP 客户端会先用 `GET /mcp` 建一条"服务端→客户端"的推送流（SSE）。
    我们没实现这个方向（平台不主动向 agent 推东西）⇒ 明确回 **405**，
    而不是挂一条永远不响应的流让客户端以为服务坏了。
    """
    from fastapi.responses import JSONResponse

    return JSONResponse({"ok": False, "error": "本平台的 MCP 端点是 POST-only",
                         "why": "平台不主动向 agent 推送（服务端→客户端流未实现）；"
                                "工具调用请用 POST /mcp",
                         "how": {"method": "POST", "path": "/mcp",
                                 "body": {"jsonrpc": "2.0", "id": 1,
                                          "method": "tools/list", "params": {}}}},
                        status_code=405, headers={"Allow": "POST"})


@router.post("/api/mcp/session-notes")
async def mcp_session_note_add(request: Request):
    """**放一句"请它自己在会话里发"的话**（平台不代发，`V105` §8）。

    body：`{"text": "...", "harness_id": "可选", "note": "可选说明"}`
    """
    from fastapi.responses import JSONResponse

    from .. import session_notes as _sn

    try:
        body = await request.json()
    except Exception:                                          # noqa: BLE001 — 没 body 就是没内容
        body = {}
    body = body if isinstance(body, dict) else {}
    out = _sn.add(str(body.get("text") or ""), harness_id=str(body.get("harness_id") or ""),
                  note=str(body.get("note") or ""))
    return JSONResponse(out, status_code=200 if out.get("ok") else 400)


@router.get("/api/mcp/session-notes")
async def mcp_session_notes(limit: int = 20, pending: int = 0, harness_id: str = ""):
    """看这批"请它自己发"的待办：`pending=1` 只看没发的；默认给最近的（含已发的）。"""
    from .. import session_notes as _sn

    if pending:
        items = _sn.pending(harness_id=harness_id, limit=limit)
        return {"ok": True, "count": len(items), "pending": True, "notes": items,
                "counts": _sn.counts(), "hint": _sn_hint()}
    items = _sn.recent(limit)
    return {"ok": True, "count": len(items), "pending": False, "notes": items,
            "counts": _sn.counts(), "hint": _sn_hint()}


def _sn_hint() -> str:
    return ("这些是**平台请它自己发**的话；平台不代写它的会话。"
            "它发完会调 report_session_sent 回报 —— 那是**它自己的话**（自报），不是平台发的。")


@router.post("/api/mcp/session-notes/{note_id}/sent")
async def mcp_session_note_sent(note_id: str, request: Request):
    """它回报"我在自己的会话里发了"（**它的自报**，平台没代写任何东西）。"""
    from fastapi.responses import JSONResponse

    from .. import session_notes as _sn

    try:
        body = await request.json()
    except Exception:                                          # noqa: BLE001
        body = {}
    body = body if isinstance(body, dict) else {}
    out = _sn.mark_sent(note_id, said=str(body.get("text") or ""),
                        client=str(body.get("client") or ""),
                        harness_id=str(body.get("harness_id") or ""))
    return JSONResponse(out, status_code=200 if out.get("ok") else 404)


@router.post("/api/progress")
async def progress_add(request: Request):
    """**它在自己回合里报一步进度**（`V105` §6）：平台只记录它说的，不替它演过程。

    body：`{"text": "我已经把 X 改完了", "harness_id": "可选", "task_id": "可选", "kind": "step"}`
    `kind`：step（一步）/ note（备注）/ block（卡住了）/ done（收尾）。
    """
    from fastapi.responses import JSONResponse

    from .. import progress as _pg

    try:
        body = await request.json()
    except Exception:                                          # noqa: BLE001 — 没 body 就是没内容
        body = {}
    body = body if isinstance(body, dict) else {}
    out = _pg.add(str(body.get("text") or ""), harness_id=str(body.get("harness_id") or ""),
                  task_id=str(body.get("task_id") or ""), kind=str(body.get("kind") or "step"))
    return JSONResponse(out, status_code=200 if out.get("ok") else 400)


@router.get("/api/progress")
async def progress_list(limit: int = 30, harness_id: str = "", task_id: str = ""):
    """看「工作过程」：按时间正序给最近若干条（可按 harness / 任务筛）。"""
    from .. import progress as _pg

    items = _pg.recent(harness_id=harness_id, task_id=task_id, limit=limit)
    return {"ok": True, "count": len(items), "counts": _pg.counts(), "items": items,
            "hint": "这些是**它自己报的**进度（它说的话）；平台没有替它编过程。"
                    "要它在自己的会话里也看得见，靠它自己的 session_post 那类工具。"}


@router.get("/api/harness/{harness_id}/progress-guide")
async def harness_progress_guide(harness_id: str, format: str = "md"):
    """**给它看的引导**：干活时怎么把过程报上来（并且顺手写进它自己的会话）。"""
    from fastapi.responses import JSONResponse, PlainTextResponse

    text = _progress_guide_text(harness_id)
    if (format or "md").lower() == "md":
        return PlainTextResponse(text, media_type="text/markdown; charset=utf-8")
    return JSONResponse({"ok": True, "harness_id": harness_id, "guide_md": text})


def _progress_guide_text(harness_id: str) -> str:
    return "\n".join([
        "# 请你在干活时把「过程」报上来（这样用户能直接看到你在干嘛）",
        "",
        "## 你要做的（每次干活时）",
        "",
        "1. 每完成**一个可说明的步骤**，调用平台工具 `report_progress`：",
        "   ```json",
        "   {\"text\": \"一句话：我刚才做了什么、结果怎样\", \"task_id\": \"<可选>\", \"kind\": \"step\"}",
        "   ```",
        "   `kind` 用：`step`（一步）/ `note`（备注）/ `block`（卡住了，写清卡在哪）/ `done`（收尾）。",
        "2. **如果你自己有「往自己会话里写一句」的工具**（名字像 `session_post` / `post_to_session` /",
        "   `send_message` 这类），**同一句话也写进你自己的会话** —— 用户在你会话里能直接看到过程。",
        "   平台**不会**替你去写你的会话（那条路已经停了：平台代写的「过程」是假的）。",
        "3. 收尾时再报一条 `kind=done`，一句话总结这次干了什么。",
        "",
        "## 一句话要求（可直接抄给你自己）",
        "",
        "> 干活时每步都调 `report_progress(text=…)`；有往自己会话写话的工具就顺手也写一份；",
        "> 卡住时用 `kind=block` 并写清卡在哪；结束时 `kind=done` 总结一句。",
        "",
        "## 平台侧怎么看",
        "",
        "`GET /api/progress?harness_id=%s` —— 按时间列出你报的每一步（谁报的、什么时候）。" % harness_id,
    ])


@router.get("/api/mcp/calls")
async def mcp_calls(limit: int = 50):
    """**它到底来没来**：最近 N 次真实 MCP 调用（客户端名/工具/成败/时间）。

    判据口径：空 ⇒ 它一次都没来过（这时候"平台测通了"全是平台自己在说话）。
    """
    items = recent_calls(limit)
    return {"ok": True, "count": len(items),
            "persisted": str(_calls_file()),
            "tools": [t.get("name") for t in _tools()],
            "hint": ("这里每一条都是**它自己发过来的请求**；它没来过就是空的 —— "
                     "别拿'平台调它的插件'当'它接上了'"),
            "calls": items}
