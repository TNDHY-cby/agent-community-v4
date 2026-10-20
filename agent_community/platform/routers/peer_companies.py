# -*- coding: utf-8 -*-
"""platform/routers/peer_companies：**对端公司**端点（V62 §4）——"我要去谁家"。

与 `company_admin.py`（"谁能进我家"）互补：
- 这里管的是**我方主动访问的对端**（名字 + 隧道地址 + 对方令牌）；
- 隧道本身不在框架里：**对端只要有一个 http 地址**（直连 / frp / ngrok / 反代都行）。

纪律：
- 所有端点只接受**本机来源**（`origin_allowed`，一处真相）；
- 响应里**永远不含令牌明文**（只回 `token_masked`）；
- 派活/回查失败时**如实说清**（不可达 / 401 / 403 / 超时），绝不返回"看起来成功"。
"""
from __future__ import annotations

from fastapi import APIRouter, Request

from .. import peers as _peers
from .. import tunnel as _tunnel
from ..audit import audit_log as _audit_log

router = APIRouter()


def _local_only(request: Request):
    """只接受本机来源；返回 None 表示通过，否则返回该直接回的响应。"""
    from .system import origin_allowed
    from ..server import Utf8JSONResponse
    ok, why = origin_allowed(request)
    if not ok:
        return Utf8JSONResponse({"error": "来源不是本机", "detail": why}, status_code=403)
    return None


@router.get("/api/peers")
async def list_peers():
    """列出已登记的对端公司（令牌只回打码值）。"""
    data = _peers.load_peers()
    items = [_peers.public_view(rec) for rec in data.get("peers", {}).values()]
    items.sort(key=lambda r: str(r.get("created_at") or ""))
    return {"success": True, "peers": items, "total": len(items)}


@router.get("/api/peers/tunnel-advice")
async def tunnel_advice(peer_id: str = ""):
    """**隧道该怎么弄**（只给判断与建议，不发请求）：暴露哪个端口、能不能被外面连到、别暴露什么。"""
    peer_url = ""
    if peer_id:
        rec = _peers.get_peer(peer_id) or {}
        peer_url = str(rec.get("base_url") or "")
    out = _tunnel.advise(peer_url=peer_url)
    try:
        from ..identity import auth_secret_ready
        ready = bool(auth_secret_ready())
    except Exception:                                        # noqa: BLE001
        ready = False
    out["inbound_ready"] = _tunnel.check_inbound_ready(ready)
    return {"success": True, **out}


@router.post("/api/peers")
async def create_or_update_peer(request: Request):
    """登记/更新对端公司：`{display_name, base_url, token?, note?, peer_id?}`。"""
    deny = _local_only(request)
    if deny is not None:
        return deny
    from ..server import Utf8JSONResponse
    body = await request.json()
    rec, err = _peers.upsert_peer(body or {})
    if err:
        return Utf8JSONResponse({"error": err,
                                 "how_to_fix": "base_url 填对端**根地址**（形如 http://host:9104），"
                                               "不要带路径；令牌是对端公司在它自己那边签发给你的"},
                                status_code=400)
    _audit_log.record("peer.upsert", actor="user", target=rec["peer_id"],
                      detail="登记对端公司 %s → %s" % (rec["display_name"], rec["base_url"]))
    return {"success": True, "peer": _peers.public_view(rec)}


@router.post("/api/peers/{peer_id}/verify")
async def verify_peer(peer_id: str, request: Request):
    """**探活**：读对端 `/.well-known/agent-card.json`（A2A 规范：读卡不需要令牌 ⇒ **不烧 token**）。"""
    deny = _local_only(request)
    if deny is not None:
        return deny
    from ..server import Utf8JSONResponse
    rec = _peers.get_peer(peer_id)
    if not rec:
        return Utf8JSONResponse({"error": "对端公司不存在：%s" % peer_id}, status_code=404)
    base = str(rec.get("base_url") or "").rstrip("/")
    try:
        import asyncio
        from ...a2a_client import discover_card
        card = await asyncio.to_thread(discover_card, base, 10)
        who = str((card or {}).get("name") or (card or {}).get("agentName") or "").strip()
        ok, detail = True, ("读卡成功：%s" % (who or "（卡片里没写名字）"))
    except Exception as e:                                   # noqa: BLE001
        ok, detail = False, "读不到对端：%s: %s" % (type(e).__name__, str(e)[:160])
    _peers.record_verify(rec["peer_id"], ok, detail)
    _audit_log.record("peer.verify", actor="user", target=rec["peer_id"],
                      detail=("ok" if ok else "fail") + "：" + detail[:120])
    if not ok:
        return Utf8JSONResponse({
            "success": False, "ok": False, "detail": detail,
            "how_to_fix": ("对端地址不通。常见原因：① 隧道没起/端口变了 ② 对端没开 a2a 服务 "
                           "③ 防火墙挡了 ④ base_url 写错（要 http://host:端口，不带路径）"),
            "tunnel_advice": _tunnel.advise(peer_url=base),
        }, status_code=502)
    return {"success": True, "ok": True, "detail": detail, "peer": _peers.public_view(_peers.get_peer(peer_id) or {})}


@router.post("/api/peers/{peer_id}/dispatch")
async def dispatch_to_peer(peer_id: str, request: Request):
    """**派活给对端公司**：`{title, task_id?}` ⇒ 对端 `tasks/send`。"""
    deny = _local_only(request)
    if deny is not None:
        return deny
    from ..server import Utf8JSONResponse
    rec = _peers.get_peer(peer_id)
    if not rec:
        return Utf8JSONResponse({"error": "对端公司不存在：%s" % peer_id}, status_code=404)
    if not rec.get("enabled"):
        return Utf8JSONResponse({
            "error": "这个对端公司已停用", "peer_id": rec["peer_id"],
            "how_to_fix": "先 `POST /api/peers/%s/enabled` 传 {\"enabled\": true} 再派" % rec["peer_id"],
        }, status_code=403)
    body = await request.json()
    title = str((body or {}).get("title") or "").strip()
    if not title:
        return Utf8JSONResponse({"error": "title 不能为空（这是派给对端的任务内容）",
                                 "how_to_fix": "传 {\"title\": \"要做的事\"}"}, status_code=400)
    token = _peers.peer_token(rec)
    if not token:
        return Utf8JSONResponse({
            "error": "这个对端没登记令牌", "peer_id": rec["peer_id"],
            "how_to_fix": "对端要先在它那边登记我方并签发公司令牌，把令牌填进 /api/peers 的 token 字段",
        }, status_code=400)
    base = str(rec.get("base_url") or "").rstrip("/")
    task_id_req = str((body or {}).get("task_id") or "").strip()
    try:
        import asyncio
        from ...a2a_client import send_task
        sent = await asyncio.to_thread(send_task, base + "/", title, token, task_id_req, 60)
    except Exception as e:                                   # noqa: BLE001
        msg = "%s: %s" % (type(e).__name__, str(e)[:160])
        _audit_log.record("peer.dispatch", actor="user", target=rec["peer_id"], detail="fail: " + msg[:100])
        return Utf8JSONResponse({
            "success": False, "error": "派活失败：" + msg,
            "how_to_fix": ("① 先 `POST /api/peers/%s/verify` 确认能连上 "
                           "② 令牌是否过期/吊销（对端能吊销）③ 对端 a2a 服务是否在跑"
                           % rec["peer_id"]),
            "tunnel_advice": _tunnel.advise(peer_url=base),
        }, status_code=502)
    tid = ""
    if isinstance(sent, dict):
        tid = str(sent.get("id") or (sent.get("task") or {}).get("id") or "")
    if tid:
        _peers.record_task(rec["peer_id"], tid, title)
    # V63：**本侧也留一条可见记录**（外部任务）——否则投出去之后看板/任务树什么也看不到
    pt = {}
    if tid:
        try:
            from .. import peer_tasks as _pt
            pt = _pt.create(rec["peer_id"], rec.get("display_name") or rec["peer_id"],
                            title, tid, str((body or {}).get("workshop_id") or ""))
        except Exception as e:                               # noqa: BLE001 — 记录失败不该让派活失败，但要说话
            print("[peer] 外部任务记录写入失败: %s" % e, flush=True)
    _audit_log.record("peer.dispatch", actor="user", target=rec["peer_id"],
                      detail="task=%s 标题=%s" % (tid or "?", title[:60]))
    return {"success": True, "task_id": tid,
            "peer_task_id": (pt or {}).get("pt_id", ""),
            "peer": _peers.public_view(_peers.get_peer(peer_id) or {}),
            "note": "已投给对端；它的员工接不接、什么时候回，看对端平台。回查用下面的端点。"}


@router.get("/api/peer-tasks")
async def list_peer_tasks(peer_id: str = "", workshop_id: str = "", status: str = ""):
    """**对外任务**列表（V63）：我们派给外部公司的活，在本侧看得见。

    过滤：`peer_id` / `workshop_id` / `status`（`sent|working|done|failed|unreachable`）。
    """
    from .. import peer_tasks as _pt
    items = [_pt.public_view(x) for x in _pt.list_tasks(peer_id, workshop_id, status)]
    return {"success": True, "tasks": items, "total": len(items),
            "statuses": list(_pt.STATUSES)}


@router.get("/api/peer-tasks/poll-status")
async def peer_poll_status():
    """回执自动收回的状态（V63 §6）：是否开启、间隔、每轮几条。

    **默认关**（`AC_PEER_POLL_ENABLED=0`）——自动对外发请求属于"要用户点头"的那类；
    关闭时界面只给"手动刷新回执"，不给假承诺。
    """
    from .. import peer_poll as _poll
    return {"success": True, **_poll.status()}


@router.get("/api/peer-tasks/{pt_id}")
async def get_peer_task(pt_id: str):
    """单条详情（**只读本地记录**，不发网络请求；要刷新状态用下一个端点）。"""
    from ..server import Utf8JSONResponse
    from .. import peer_tasks as _pt
    rec = _pt.get(pt_id)
    if not rec:
        return Utf8JSONResponse({"error": "外部任务不存在：%s" % pt_id}, status_code=404)
    return {"success": True, "task": _pt.public_view(rec)}


@router.post("/api/peer-tasks/{pt_id}/refresh")
async def refresh_peer_task(pt_id: str, request: Request):
    """**真去查对端**并更新状态（人点才查，不自动轮询——省网络也省对端）。"""
    deny = _local_only(request)
    if deny is not None:
        return deny
    from ..server import Utf8JSONResponse
    from .. import peer_tasks as _pt
    rec = _pt.get(pt_id)
    if not rec:
        return Utf8JSONResponse({"error": "外部任务不存在：%s" % pt_id}, status_code=404)
    peer = _peers.get_peer(rec.get("peer_id") or "") or {}
    if not peer:
        updated = _pt.record_check(pt_id, False, error="对端公司登记已不存在（可能被删了）")
        return {"success": True, "task": _pt.public_view(updated),
                "note": "对端登记没了，已把状态标成 unreachable"}
    token = _peers.peer_token(peer)
    base = str(peer.get("base_url") or "").rstrip("/")
    try:
        import asyncio
        from ...a2a_client import get_task
        info = await asyncio.to_thread(get_task, base + "/", rec.get("remote_task_id") or "", token, 30)
        updated = _pt.record_check(pt_id, True, info=info)
        _audit_log.record("peer.task.refresh", actor="user", target=rec.get("peer_id") or "",
                          detail="%s → %s" % (pt_id, updated.get("status")))
        return {"success": True, "task": _pt.public_view(updated)}
    except Exception as e:                                   # noqa: BLE001
        err = "%s: %s" % (type(e).__name__, str(e)[:160])
        updated = _pt.record_check(pt_id, False, error=err)
        _audit_log.record("peer.task.refresh", actor="user", target=rec.get("peer_id") or "",
                          detail="%s → unreachable（%s）" % (pt_id, err[:80]))
        return {"success": True, "task": _pt.public_view(updated),
                "note": "查不到对端（隧道断/服务停/令牌失效）——已如实标 unreachable，不会假装还在跑",
                "tunnel_advice": _tunnel.advise(peer_url=base)}


@router.get("/api/peers/{peer_id}/tasks/{task_id}")
async def peer_task_status(peer_id: str, task_id: str):
    """回查对端任务状态（对端不可达时**如实报**，不返回假的"进行中"）。"""
    from ..server import Utf8JSONResponse
    rec = _peers.get_peer(peer_id)
    if not rec:
        return Utf8JSONResponse({"error": "对端公司不存在：%s" % peer_id}, status_code=404)
    token = _peers.peer_token(rec)
    base = str(rec.get("base_url") or "").rstrip("/")
    try:
        import asyncio
        from ...a2a_client import get_task
        info = await asyncio.to_thread(get_task, base + "/", task_id, token, 30)
    except Exception as e:                                   # noqa: BLE001
        return Utf8JSONResponse({
            "success": False, "error": "回查失败：%s: %s" % (type(e).__name__, str(e)[:160]),
            "how_to_fix": "对端不可达或令牌失效；先 verify 一次看它通不通",
        }, status_code=502)
    return {"success": True, "task_id": task_id, "task": info}


@router.post("/api/peers/{peer_id}/enabled")
async def set_peer_enabled(peer_id: str, request: Request):
    """停用/启用对端（停用后派活直接被拒）。"""
    deny = _local_only(request)
    if deny is not None:
        return deny
    from ..server import Utf8JSONResponse
    body = await request.json()
    if "enabled" not in (body or {}):
        return Utf8JSONResponse({"error": "要传 {\"enabled\": true/false}"}, status_code=400)
    rec, err = _peers.set_enabled(peer_id, bool(body.get("enabled")))
    if err:
        return Utf8JSONResponse({"error": err}, status_code=404)
    _audit_log.record("peer.enabled", actor="user", target=rec["peer_id"],
                      detail="enabled=%s" % rec["enabled"])
    return {"success": True, "peer": _peers.public_view(rec)}


@router.delete("/api/peers/{peer_id}")
async def delete_peer(peer_id: str, request: Request):
    """删除本地登记（**不动对端**）。"""
    deny = _local_only(request)
    if deny is not None:
        return deny
    from ..server import Utf8JSONResponse
    if not _peers.delete_peer(peer_id):
        return Utf8JSONResponse({"error": "对端公司不存在：%s" % peer_id}, status_code=404)
    _audit_log.record("peer.delete", actor="user", target=peer_id, detail="删除对端登记")
    return {"success": True, "note": "只删本机登记；对端那边的登记/令牌不受影响"}
