# -*- coding: utf-8 -*-
"""监控室端点（V88）：画面墙 / 放大 / 观察。

口径（老板 2026-10-22 拍板）：
- 监控对象挂在 **agent** 上：`{harness_id, on}` —— 不是让用户挑窗口句柄；
- 默认 **PNG 快照墙**（零依赖）；有 Pillow 才有 MJPEG 连续流；
- **AI 观察默认关**，开了也最多每 `AC_MONITOR_AI_INTERVAL_MIN`（默认 30）分钟一次；
- 画面**不出本机**：`/api/monitor` 进只读档 DENY（见 `server.EXTERNAL_READONLY_DENY`）。
"""
from __future__ import annotations

import asyncio
import time

from fastapi import APIRouter, Request
from fastapi.responses import Response

from .. import monitor as mn

router = APIRouter()


def _sess(hid: str):
    from ..harness_adapter import harness_manager

    return harness_manager.sessions.get(hid)


def _info_of(hid: str):
    sess = _sess(hid)
    if sess is None:
        return None
    try:
        return sess.info.model_dump()
    except Exception:                                        # noqa: BLE001 — 读不到就当没有
        return {}


def _hwnd_for(hid: str) -> tuple:
    """给某个 agent 找它的窗口：用户指定过就用指定的，否则按线索解析。"""
    row = next((e for e in mn.watched() if e.get("harness_id") == hid), None) or {}
    if row.get("hwnd"):
        return int(row["hwnd"]), "用户指定的窗口"
    info = _info_of(hid) or {}
    md = info.get("metadata") if isinstance(info.get("metadata"), dict) else {}
    try:
        pid = int(md.get("pid") or 0)
    except (TypeError, ValueError):
        pid = 0
    return mn.resolve_hwnd([info.get("harness_name"), info.get("harness_id") or hid], pid)


@router.get("/api/monitor/health")
async def monitor_health():
    """能不能用、用什么方法、要不要装东西 —— 一处真相，界面据此决定亮不亮。"""
    out = mn.health()
    out["watched_on"] = len([e for e in mn.watched() if e.get("on")])
    return out


@router.get("/api/monitor/sources")
async def monitor_sources():
    """可抓的窗口列表（只在"手动指定一次"时用得上；正常走 agent 自动解析）。"""
    return {"supported": mn.IS_WINDOWS, "windows": await asyncio.to_thread(mn.sources)}


@router.get("/api/monitor/agents")
async def monitor_agents():
    """监控室的**选择器**数据：哪些 agent 能监控、现在开没开、不能开是为什么。

    老板 2026-10-22：「监控的开始和关闭都在监控室里面进行」——
    ⇒ 这个端点必须自己就能回答"我能开哪几个、为什么不能开"，
    不让用户再跑去对接中心找开关。
    """
    from ..harness_adapter import harness_manager

    watched = {e.get("harness_id"): e for e in mn.watched()}
    rows = []
    for hid, sess in sorted(harness_manager.sessions.items()):
        try:
            info = sess.info.model_dump()
        except Exception:                                    # noqa: BLE001 — 读不到就当没有
            info = {}
        bridge_status = str(info.get("bridge_status") or "")
        ok_bridge, bridge_why = mn.eligible(bridge_status)
        row = watched.get(hid) or {}
        hwnd, resolve_why = _hwnd_for(hid)
        able, able_why = mn.can_capture(hid, hwnd)
        rows.append({
            "harness_id": hid,
            "harness_name": info.get("harness_name") or hid,
            "status": str(info.get("status") or ""),
            "bridge": {"status": bridge_status, "ready": ok_bridge, "why": bridge_why},
            "eligible": ok_bridge,
            "monitor": {"on": bool(row.get("on")), "state": row.get("state") or "stopped",
                        "why": row.get("why") or ""},
            "window": {"hwnd": int(hwnd), "resolved_by": resolve_why,
                       "can_capture": able, "why": able_why},
            "last_ok_at": row.get("last_ok_at", 0),
        })
    # 能开的排前面，其次按名字（用户一眼看到"我现在能开哪几个"）
    rows.sort(key=lambda r: (not r["eligible"], not r["monitor"]["on"], r["harness_name"]))
    return {"supported": mn.IS_WINDOWS, "agents": rows,
            "max_watch": mn.max_watch(),
            "watched_on": len([r for r in rows if r["monitor"]["on"]]),
            "note": "开关就在这个列表上：POST /api/monitor/watched {harness_id, on}"}


@router.get("/api/monitor/watched")
async def monitor_watched():
    """已开监控的 agent 名单 + 每一路现在能不能抓（如实说为什么不能）。"""
    rows = []
    for e in mn.watched():
        hid = e.get("harness_id")
        hwnd, why = _hwnd_for(hid)
        rows.append({**{k: e.get(k) for k in ("harness_id", "on", "title", "state", "why")},
                     "hwnd": hwnd, "resolve": why,
                     "last_ok_at": e.get("last_ok_at", 0),
                     "last_view_at": e.get("last_view_at", 0)})
    return {"entries": rows, "max_watch": mn.max_watch(), "supported": mn.IS_WINDOWS}


@router.post("/api/monitor/watched")
async def monitor_watched_set(request: Request):
    """开/关对某个 **agent** 的监控。`{harness_id, on}`；可选 `hwnd` 手动指定窗口。"""
    from ..audit import audit_log as _audit
    from ..server import Utf8JSONResponse

    try:
        body = await request.json()
    except Exception:                                        # noqa: BLE001
        body = {}
    body = body if isinstance(body, dict) else {}
    hid = str(body.get("harness_id") or "").strip()
    if not hid:
        return Utf8JSONResponse({"error": "要传 harness_id（监控对象是 agent，不是窗口）"},
                                status_code=400)
    if "on" not in body:
        return Utf8JSONResponse({"error": "要传 on: true|false（开/关监控）"}, status_code=400)
    if _sess(hid) is None:
        return Utf8JSONResponse({"error": "没有这个 harness（未注册？先注册再开监控）",
                                 "how_to_fix": "GET /api/harness/list 看现有 id"}, status_code=404)
    on = bool(body["on"])
    out = mn.set_watched(hid, on, hwnd=int(body.get("hwnd") or 0), title=str(body.get("title") or ""))
    if out.get("ok"):
        _audit.record("monitor.watched", actor="user", target=hid,
                      detail="%s 监控（同时在看的 %s 路）" % ("开启" if on else "关闭", out.get("entries")))
    return out


@router.get("/api/monitor/{harness_id}/frame")
async def monitor_frame(harness_id: str, max_w: int = 320):
    """一帧 PNG。**放大查看就是同一个端点**给 `max_w=0`（抓原始尺寸）。"""
    from ..server import Utf8JSONResponse

    if not mn.IS_WINDOWS:
        return Utf8JSONResponse({"error": "窗口抓取只支持 Windows",
                                 "how_to_fix": "非 Windows 用 /api/harness/{id}/mirror 的协议级映射"},
                                status_code=501)
    row = next((e for e in mn.watched() if e.get("harness_id") == harness_id), None)
    if row is None or not row.get("on"):
        return Utf8JSONResponse({"error": "这个 agent 没开监控", "how_to_fix":
                                 "先 POST /api/monitor/watched {harness_id, on:true}"}, status_code=403)
    hwnd, why = _hwnd_for(harness_id)
    if not hwnd:
        return Utf8JSONResponse({"error": why, "how_to_fix":
                                 "GET /api/monitor/sources 挑一个窗口，再 POST watched 带 hwnd 指定一次"},
                                status_code=404)
    png, meta = await asyncio.to_thread(mn.frame_png, hwnd, max_w)
    if png is None:
        return Utf8JSONResponse(dict(meta, error=meta.get("why", "抓不到")), status_code=409)
    mn.note_viewer(harness_id)
    mn.touch(harness_id, last_ok_at=time.time(), why="", hwnd=int(hwnd))
    mn.auto_stop_idle()
    return Response(content=png, media_type="image/png",
                    headers={"Cache-Control": "no-store",
                             "X-Monitor-Method": str(meta.get("method") or ""),
                             "X-Monitor-Why": ""})


@router.get("/api/monitor/{harness_id}/stream")
async def monitor_stream(harness_id: str):
    """MJPEG 连续流（**需要 Pillow**；没装就如实 501 并告诉怎么装）。"""
    from ..server import Utf8JSONResponse

    health = mn.health()
    if not health.get("supported"):
        return Utf8JSONResponse({"error": health.get("why"), "how_to_fix":
                                 "非 Windows 用 /api/harness/{id}/mirror 的协议级映射"},
                                status_code=501)
    # 本版**只做 PNG 快照墙**（老板 2026-10-22 拍板①）——所以这里永远是 501，
    # 且文案要说"本版没做"，**不许**甩锅给"缺 Pillow"（那是下一版的事）。
    return Utf8JSONResponse({"error": health.get("stream_why") or "本版只做 PNG 快照墙",
                             "how_to_fix": "用 GET /api/monitor/{id}/frame 轮询；需要更流畅等下一版"},
                            status_code=501)


@router.post("/api/monitor/{harness_id}/observe")
async def monitor_observe(harness_id: str, request: Request):
    """agent 的"简单观察"：画面变没变、多久没动。

    `ai=1` 时才可能调 AI —— 且要过闸门：**默认关** + **最小间隔 30 分钟**（可配）。
    """
    from ..audit import audit_log as _audit
    from ..server import Utf8JSONResponse

    if not mn.IS_WINDOWS:
        return Utf8JSONResponse({"error": "窗口抓取只支持 Windows"}, status_code=501)
    try:
        body = await request.json()
    except Exception:                                        # noqa: BLE001
        body = {}
    body = body if isinstance(body, dict) else {}
    want_ai = bool(body.get("ai"))

    row = next((e for e in mn.watched() if e.get("harness_id") == harness_id), None)
    if row is None or not row.get("on"):
        return Utf8JSONResponse({"error": "这个 agent 没开监控"}, status_code=403)
    hwnd, _why = _hwnd_for(harness_id)
    out = await asyncio.to_thread(mn.observe, harness_id, _info_of(harness_id) or {}, hwnd)
    out["ai"] = {"requested": want_ai, "ran": False, "why": "未请求"}
    if want_ai:
        ok, why = mn.observe_ai_gate(harness_id)
        if not ok:
            out["ai"] = {"requested": True, "ran": False, "why": why}
        else:
            # ⚠️ 本版**不接视觉**（模型多半看不了图，也不许假装看了）：
            #    只把"变化统计 + 窗口标题 + 这个 agent 的当前状态"交给文本模型说一句话。
            out["ai"] = {"requested": True, "ran": False,
                         "why": "本版只做本地变化统计，不调用模型（视觉观察留到下一版）"}
    _audit.record("monitor.observe", actor="agent", target=harness_id,
                  detail="changed=%s diff=%s ai=%s" % (out.get("changed"), out.get("diff_ratio"),
                                                       out["ai"].get("ran")))
    return out
