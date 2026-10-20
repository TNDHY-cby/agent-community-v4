# -*- coding: utf-8 -*-
"""platform/routers/relay：**中转**相关端点（V66 M1）——终端侧看状态、发内容、看收件箱。

- `GET  /api/relay/status`：连没连上中转、最后成功/错误、收件箱条数（界面用来显示"跨端可用吗"）；
- `POST /api/relay/send`：`{to_terminal, payload, kind?}` ⇒ 经中转发给另一台终端；
- `GET  /api/relay/inbox`：本地收件箱（`limit` 可选，默认 50）。

**全部本机来源**（转发是本地动作，别让外部直接调用）。
"""
from __future__ import annotations

from fastapi import APIRouter, Request

from ..audit import audit_log as _audit_log

router = APIRouter()


def _local_only(request: Request):
    from .system import origin_allowed
    from ..server import Utf8JSONResponse
    ok, why = origin_allowed(request)
    if not ok:
        return Utf8JSONResponse({"error": "来源不是本机", "detail": why}, status_code=403)
    return None


@router.get("/api/relay/status")
async def relay_status():
    """中转状态（**默认未配置也不报错**：本机一切照常，只是跨端不可用）。"""
    from .. import relay_client as client
    return {"success": True, **client.status()}


@router.post("/api/relay/send")
async def relay_send(request: Request):
    """经中转把内容发给另一台终端。"""
    deny = _local_only(request)
    if deny is not None:
        return deny
    from ..server import Utf8JSONResponse
    from .. import relay_client as client
    body = await request.json()
    to = str((body or {}).get("to_terminal") or "").strip()
    if not to:
        return Utf8JSONResponse({"error": "to_terminal 必填（对方终端 ID）",
                                 "how_to_fix": "填对方在它自己机器上配的 AC_HUB_TERMINAL_ID"}, status_code=400)
    if not client.is_configured():
        return Utf8JSONResponse({
            "error": "本机没有配置中转，发不出去",
            "how_to_fix": ("设 AC_HUB_URL（中转地址）与 AC_HUB_TERMINAL_ID（本机名字）后重启平台；"
                           "没配中转时本机功能一切照常，只是跨端不可用"),
        }, status_code=400)
    st, res = client.send(to, (body or {}).get("payload"),
                          kind=str((body or {}).get("kind") or "text"))
    _audit_log.record("relay.send", actor="user", target=to,
                      detail="HTTP %s ｜ %s" % (st, str((res or {}).get("note") or "")[:80]))
    if st == 200:
        return {"success": True, "to_terminal": to, **(res or {})}
    return Utf8JSONResponse({
        "success": False, "error": str((res or {}).get("error") or "发送失败（HTTP %s）" % st),
        "how_to_fix": "先 `GET /api/relay/status` 看中转连没连上；连不上就检查 AC_HUB_URL 与令牌",
    }, status_code=502 if st == 0 else st)


@router.post("/api/relay/dispatch")
async def relay_dispatch(request: Request):
    """**经中转派活**（V67 M2）：`{to_terminal, title, hall?, peer_name?}`。

    与 a2a 派活（`/api/peers/{id}/dispatch`）的区别：**不需要对方地址可达**，
    只要对方在同一个中转上 ⇒ 本机发一条 `task.assign`，对方收到就建工作间、并回 `task.ack`。

    本地立刻建一条外部任务（`status=sent`、`via=hub`），回执回来时**自动更新**（不用点刷新）。
    """
    deny = _local_only(request)
    if deny is not None:
        return deny
    from ..server import Utf8JSONResponse
    from .. import relay_client as client
    from .. import peer_tasks
    body = await request.json()
    to = str((body or {}).get("to_terminal") or "").strip()
    title = str((body or {}).get("title") or "").strip()
    if not to or not title:
        return Utf8JSONResponse({"error": "to_terminal 与 title 都必填",
                                 "how_to_fix": "to_terminal=对方终端 ID（它自己配的 AC_HUB_TERMINAL_ID）；"
                                               "title=这趟活的名字"}, status_code=400)
    if not client.is_configured():
        return Utf8JSONResponse({
            "error": "本机没有配置中转，派不出去",
            "how_to_fix": ("设 AC_HUB_URL 与 AC_HUB_TERMINAL_ID 后重启平台；"
                           "若对方有可达地址，也可以改用 /api/peers 点对点派活"),
        }, status_code=400)
    # 先建本地外部任务（pt_id 随消息发给对方，回执靠它精确对上）
    rec = peer_tasks.create(peer_id="hub:" + to,
                            peer_name=str((body or {}).get("peer_name") or to),
                            title=title, remote_task_id="", via="hub")
    st, res = client.send(to, {"pt_id": rec["pt_id"], "title": title,
                               "hall": str((body or {}).get("hall") or ""),
                               "from_terminal": client.terminal_id(),
                               "from_name": client.status().get("terminal_id") or ""},
                          kind="task.assign")
    online = bool((res or {}).get("online"))
    peer_tasks.add_timeline(rec["pt_id"], "dispatched",
                            "经中转投给 %s（对方%s）" % (to, "在线" if online else "不在线，已暂存"))
    _audit_log.record("relay.dispatch", actor="user", target=to,
                      detail="HTTP %s ｜ pt=%s ｜ online=%s" % (st, rec["pt_id"], online))
    if st == 200:
        return {"success": True, "pt_id": rec["pt_id"], "to_terminal": to, "via": "hub",
                "online": online,
                "note": ("已投进对方队列（%s），等它取" % ("对方在线" if online
                                                          else "对方不在线，已暂存（有上限、会过期）"))}
    # 发送失败：**本地任务不标成功**，如实说是哪一步断的
    peer_tasks.add_timeline(rec["pt_id"], "send_failed",
                            "中转发送失败：%s" % str((res or {}).get("error") or st)[:120])
    return Utf8JSONResponse({
        "success": False, "pt_id": rec["pt_id"], "via": "hub",
        "error": str((res or {}).get("error") or "发送失败（HTTP %s）" % st),
        "how_to_fix": "先 GET /api/relay/status 看中转连没连上；连不上就检查 AC_HUB_URL 与令牌",
    }, status_code=502 if st == 0 else st)


@router.get("/api/relay/inbox")
async def relay_inbox(limit: int = 50):
    """本地收件箱（**落盘在本机**；中转只负责转发，不存业务数据）。"""
    from .. import relay_client as client
    data = client.load_inbox()
    msgs = (data.get("messages") or [])
    n = max(1, min(200, int(limit or 50)))
    return {"success": True, "total": len(msgs), "cursor": int(data.get("cursor") or 0),
            "messages": msgs[-n:], "limit": n}
