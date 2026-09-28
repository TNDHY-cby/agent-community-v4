"""platform/routers/mirror：Harness 监控室实时窗口映射（V-9 自 server.py 拆分）。

包含：
- _cdp_probe_targets / _mirror_by_cdp：CDP 代码级窗口映射（默认关闭，AC_CDP_MIRROR_ENABLED=1 开启）
- _mirror_by_protocol：协议级状态/消息流映射（无 CDP 时的兜底）
- GET /api/harness/{harness_id}/mirror 端点

安全披露（V-9）：CDP 通道会静默连接 9222-9225 浏览器调试端口并读取页面内容，
默认关闭；未启用时仅返回协议级映射并明确标注 cdp_enabled=false。
"""
from __future__ import annotations

import asyncio
import json
import os
import time

from fastapi import APIRouter

router = APIRouter()


async def _cdp_probe_targets(debug_ports):
    """并发探测本机 Chrome/Edge/Electron 的 CDP 调试端口，快速返回可用端口列表。"""
    import urllib.request
    async def probe(port):
        try:
            loop = asyncio.get_running_loop()
            def _get():
                with urllib.request.urlopen(
                        f"http://127.0.0.1:{port}/json/list", timeout=0.8) as resp:
                    data = json.loads(resp.read().decode("utf-8", "ignore"))
                return (port, data) if isinstance(data, list) else None
            return await loop.run_in_executor(None, _get)
        except Exception:
            return None
    results = await asyncio.gather(*(probe(p) for p in debug_ports))
    return [r for r in results if r]


async def _mirror_by_cdp(harness_id, hints, debug_ports):
    """代码级窗口映射（无需截图）：通过 CDP 连接宿主浏览器/Electron，
    定时提取页面实时状态文本（title/url/body 文本流），结构化返回。
    披露：本功能会静默连接 9222-9225 端口的浏览器调试服务并读取页面内容；
    由环境变量 AC_CDP_MIRROR_ENABLED=1 显式开启（默认关闭）。
    """
    import websockets
    import websockets.exceptions as ws_exc
    ports = await _cdp_probe_targets(debug_ports)
    for port, targets in ports:
        cands = []
        for t in targets:
            url = (t.get("url") or "")
            title = (t.get("title") or "")
            for h in hints:
                if h and (h in url or h in title):
                    cands.append(t)
                    break
        if not cands:
            # 无精确匹配：作为兜底可看非扩展页面的前台 target（避免误读调试器内部页）
            cands = [t for t in targets if (t.get("type") or "") == "page"
                     and not url.startswith("devtools://")
                     and not url.startswith("chrome://")
                     and url not in ("about:blank", "")]
        for t in cands[:3]:
            ws_url = t.get("webSocketDebuggerUrl") or ""
            if not ws_url:
                continue
            try:
                async with websockets.connect(ws_url, open_timeout=2, close_timeout=2) as ws:
                    expr = ("JSON.stringify({t:document.title||'',u:location.href||'',"
                            "x:(document.body?document.body.innerText:'').slice(0,1200)})")
                    await ws.send(json.dumps({"id": 1, "method": "Runtime.evaluate",
                                              "params": {"expression": expr,
                                                         "returnByValue": True}}))
                    while True:
                        msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=4))
                        if msg.get("id") != 1:
                            continue
                        result = (msg.get("result") or {}).get("result") or {}
                        value = result.get("value") or ""
                        if result.get("exceptionDetails"):
                            return {"mode": "cdp", "ok": False,
                                    "error": "页面拒绝执行（异常页面）"}
                        try:
                            d = json.loads(value)
                        except Exception:
                            d = {"t": "", "u": url, "x": ""}
                        return {"mode": "cdp", "ok": True, "port": port,
                                "title": (d.get("t") or title or harness_id)[:160],
                                "url": (d.get("u") or url or "")[:300],
                                "text": (d.get("x") or "").strip()[:1000],
                                "ts": int(asyncio.get_event_loop().time())}
            except (ws_exc.ConnectionClosed, ws_exc.InvalidStatus,
                    OSError, asyncio.TimeoutError, Exception):
                continue
    return {"mode": "cdp", "ok": False}


def _mirror_by_protocol(harness_id, sess):
    """协议级映射：无 CDP 时拉 harness 现有状态/消息流渲染员工工作台画面。"""
    info = getattr(sess, "info", None)
    ai = (getattr(info, "ai", None) or {}) if info else {}
    status = getattr(sess, "status", None)
    if status is None and info:
        status = getattr(info, "status", None)
    bridge_status = (getattr(info, "bridge_status", "") or "") if info else ""
    model = (ai.get("model_name") if isinstance(ai, dict) else getattr(ai, "model_name", "")) or ""
    recent = []
    try:
        hist = getattr(sess, "discussion", None) or getattr(sess, "messages", None) or []
        for m in list(hist)[-5:]:
            c = (getattr(m, "content", None) or (m.get("content") if isinstance(m, dict) else ""))
            if isinstance(c, str) and c.strip():
                recent.append(c.strip()[:300])
    except Exception:
        recent = []
    return {"mode": "protocol", "ok": True,
            "status": str(status or "idle"),
            "bridge_status": bridge_status,
            "model": model,
            "message_count": int(getattr(sess, "message_count", 0) or 0),
            "recent": recent,
            "ts": int(time.time())}


@router.get("/api/harness/{harness_id}/mirror")
async def harness_mirror(harness_id: str, debug_port: int = 0):
    """Harness 监控室实时窗口映射（代码级，无需截图）。
    优先 CDP DOM 提取（需要宿主浏览器/Electron 开 --remote-debugging-port），
    探测不到则退回协议级状态/消息流映射。
    V-9 安全收敛：CDP 通道默认关闭，需环境变量 AC_CDP_MIRROR_ENABLED=1 显式开启。
    """
    from ..server import Utf8JSONResponse, harness_manager  # 延迟引用，避免循环导入

    cdp_enabled = os.environ.get("AC_CDP_MIRROR_ENABLED", "0") == "1"
    sess = harness_manager.sessions.get(harness_id)
    if not sess:
        return Utf8JSONResponse({"error": f"harness {harness_id} 未注册"}, status_code=404)
    info = getattr(sess, "info", None)
    hints = [harness_id]
    if info:
        hn = (getattr(info, "harness_name", "") or "").strip()
        if hn and hn != harness_id:
            hints.append(hn)
    if not cdp_enabled:
        # CDP 未启用：跳过 9222-9225 端口探测，直接走协议级映射并明确披露
        proto = _mirror_by_protocol(harness_id, sess)
        proto["harness_id"] = harness_id
        proto["cdp_enabled"] = False
        proto["cdp_error"] = "cdp_disabled: 未启用 AC_CDP_MIRROR_ENABLED=1，仅协议级映射"
        return proto
    debug_ports = []
    if debug_port:
        debug_ports.append(int(debug_port))
    debug_ports += [9222, 9223, 9224, 9225]
    try:
        try:
            cdp = await asyncio.wait_for(
                _mirror_by_cdp(harness_id, hints, debug_ports), timeout=2.0)
        except asyncio.TimeoutError:
            cdp = {"mode": "cdp", "ok": False, "error": "cdp_timeout"}
        if cdp.get("ok"):
            cdp["harness_id"] = harness_id
            cdp["cdp_enabled"] = True
            return cdp
        proto = _mirror_by_protocol(harness_id, sess)
        proto["harness_id"] = harness_id
        proto["cdp_enabled"] = True
        proto["cdp_error"] = cdp.get("error", "")
        return proto
    except Exception as e:
        proto = _mirror_by_protocol(harness_id, sess)
        proto["harness_id"] = harness_id
        proto["cdp_enabled"] = True
        proto["error"] = str(e)
        return proto
