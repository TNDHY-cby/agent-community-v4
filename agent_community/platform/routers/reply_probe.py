# -*- coding: utf-8 -*-
"""platform/routers/reply_probe：回话探针端点（`V74`）。

```
POST /api/harness/{id}/probe-reply          发起一次探针（默认关；见 AC_REPLY_PROBE_ENABLED）
     body: {"mode": "auto" | "human_confirm", "note": "人工确认时的一句说明"}
GET  /api/harness/{id}/probe-reply          查最近一次结果（只读，没有就如实说没有）
POST /api/harness/reply-probe-result        harness 侧回报（必须带回本次 probe_id）
```

为什么单独一组：`V74` 要求"只有验过才叫接上"，这是一条新的判断通道，
与档案读写（`/api/docking/harness/{id}`）和桥测试（`/api/harness/bridge-test`）都不是一回事。
"""
from __future__ import annotations

from fastapi import APIRouter, Request

from ..audit import audit_log as _audit_log

router = APIRouter()


def _sess(harness_id: str):
    from ..harness_adapter import harness_manager

    return harness_manager.sessions.get(harness_id)


@router.post("/api/harness/{harness_id}/probe-reply")
async def harness_probe_reply(harness_id: str, request: Request):
    from ..server import Utf8JSONResponse, save_state
    from .. import reply_probe as rp

    try:
        body = await request.json()
    except Exception:                                        # noqa: BLE001 — 空 body 当空对象
        body = {}
    body = body if isinstance(body, dict) else {}

    if not rp.enabled():
        return Utf8JSONResponse(
            {"error": "回话探针默认关闭（避免无意义开销）",
             "how_to_fix": "想验一次就设 AC_REPLY_PROBE_ENABLED=1 再调；不想动环境变量就别验，"
                           "界面上会如实显示「未验证 agent 能回话」"},
            status_code=409)

    sess = _sess(harness_id)
    if not sess:
        return Utf8JSONResponse({"error": "没有这个 harness（可能已注销或 id 写错）",
                                 "how_to_fix": "先 GET /api/harness/list 看现有 id"}, status_code=404)

    mode = str(body.get("mode") or "auto").strip().lower()
    if mode in ("human", "human_confirm", "manual"):
        out = rp.human_confirm(sess, note=str(body.get("note") or ""),
                               by=str(body.get("by") or "user"))
        _audit_log.record("harness.reply_probe.human_confirm", actor="user",
                          target=harness_id, detail="人工确认 agent 能回话")
        save_state()
        return out

    if mode != "auto":
        return Utf8JSONResponse({"error": "mode 只支持 auto / human_confirm"},
                                status_code=400)

    out = rp.start(sess)
    if not out.get("ok"):
        return Utf8JSONResponse(out, status_code=400)
    _audit_log.record("harness.reply_probe.start", actor="user", target=harness_id,
                      detail="发起回话探针 probe_id=%s channel=%s"
                             % (out.get("probe_id"), out.get("channel")))
    save_state()
    return out


@router.get("/api/harness/{harness_id}/probe-reply")
async def harness_probe_reply_status(harness_id: str):
    from ..server import Utf8JSONResponse
    from .. import reply_probe as rp

    sess = _sess(harness_id)
    if not sess:
        return Utf8JSONResponse({"error": "没有这个 harness"}, status_code=404)
    rec = rp.status_of(sess.info)
    if not rec:
        return {"harness_id": harness_id, "state": "unverified",
                "why": "还没有验过——平台没有任何证据说明这个 agent 收到消息会回话",
                "how_to_verify": "设 AC_REPLY_PROBE_ENABLED=1 后 POST 本端点（mode=auto）；"
                                 "人工中转型请用 mode=human_confirm"}
    return {"harness_id": harness_id, **rec, "enabled": rp.enabled()}


@router.post("/api/harness/reply-probe-result")
async def harness_reply_probe_result(request: Request):
    """harness 侧的回报入口（桥脚本用）。必须带回平台发出的 probe_id。"""
    from ..server import Utf8JSONResponse, save_state
    from .. import reply_probe as rp

    try:
        body = await request.json()
    except Exception:                                        # noqa: BLE001
        body = {}
    body = body if isinstance(body, dict) else {}
    hid = str(body.get("harness_id") or "").strip()
    if not hid:
        return Utf8JSONResponse({"error": "harness_id required"}, status_code=400)
    sess = _sess(hid)
    if not sess:
        return Utf8JSONResponse({"error": "harness %s 未注册" % hid}, status_code=404)

    out = rp.record_result(sess, str(body.get("probe_id") or ""), echo=str(body.get("echo") or ""))
    if not out.get("ok"):
        _audit_log.record("harness.reply_probe.reject", actor="harness", target=hid,
                          detail="回报被拒：%s" % str(out.get("error"))[:80])
        return Utf8JSONResponse({"error": out.get("error")},
                                status_code=int(out.get("status_code") or 403))
    _audit_log.record("harness.reply_probe.result", actor="harness", target=hid,
                      detail="探针回执通过（probe_id=%s）" % str(body.get("probe_id")))
    save_state()
    return out
