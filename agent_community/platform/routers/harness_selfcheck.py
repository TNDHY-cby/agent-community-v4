# -*- coding: utf-8 -*-
"""接入自检端点（V87 §2）：`POST /api/harness/{harness_id}/selfcheck`。

一句话：**用户点一下，就知道"这条到底通没通、下一步做什么"**。

三条纪律（与设计稿 §4 一致）：
- `probe` **默认 false**：不打扰外部 agent。要真发消息必须显式传 `true`；
- **探针是异步的**：点了也**不许当场报"通了"** —— 回执到了才算（缺证据 = `unverified`）；
- **不进外部只读档**：会暴露配置形态与失败细节 ⇒ 见 `server.EXTERNAL_READONLY_DENY`。
"""
from __future__ import annotations

from fastapi import APIRouter, Request

from .. import harness_selfcheck as sc

router = APIRouter()


@router.post("/api/harness/{harness_id}/selfcheck")
async def harness_selfcheck(harness_id: str, request: Request):
    from ..harness_adapter import harness_manager
    from ..server import Utf8JSONResponse

    try:
        body = await request.json()
    except Exception:                                        # noqa: BLE001 — 没 body 就是"不探针"
        body = {}
    body = body if isinstance(body, dict) else {}
    want_probe = bool(body.get("probe"))

    sess = harness_manager.sessions.get(harness_id)
    if sess is None:
        return Utf8JSONResponse(
            {"error": "没有这个 harness（可能已注销或 id 写错）",
             "how_to_fix": "先在对接中心登记；或 GET /api/harness/list 看现有 id"},
            status_code=404)
    try:
        dump = sess.info.model_dump()
    except Exception as e:                                    # noqa: BLE001 — 读档案失败要说明白
        return Utf8JSONResponse({"error": "读档案失败：%s: %s" % (type(e).__name__, e)},
                                status_code=500)

    # ① 凭据摘要（**只读账本、不体检**：体检是用户显式动作，见 V86）
    try:
        from ..credentials import ledger_summary_light
        cred = ledger_summary_light()
    except Exception as _e:                                   # noqa: BLE001 — 拿不到就如实标未验
        cred = {"scanned": False, "why": "读凭据摘要失败：%s" % _e}
    # ② 名册健康（同样只读）
    try:
        from .. import registry_health as rh
        health = rh.status(harness_id)
    except Exception as _e:                                   # noqa: BLE001
        health = {"samples": 0, "days_all_failing": 0, "note": "读健康记录失败：%s" % _e}

    # ③ 可选：真发一次回话探针（异步 —— 发出 ≠ 验过）
    probe_out: dict = {}
    probe_state = None
    if want_probe:
        try:
            from .. import reply_probe
            r = reply_probe.start(sess)
            probe_out = {"sent": bool(r.get("ok")), **{k: r.get(k) for k in ("probe_id", "channel", "error", "how_to_fix") if r.get(k)}}
            if r.get("ok"):
                # ⚠️ 关键：**刚发出不是"已验过"**。回执到了（`/api/harness/reply-probe-result`）
                #    才会变成 ok；这里必须停在 unverified，否则就是假绿。
                probe_state = {"state": "unverified",
                               "why": "探针已发出（probe_id=%s），等它回执才算验过" % r.get("probe_id")}
            else:
                probe_state = {"state": "failed",
                               "why": "%s —— %s" % (r.get("error") or "探针发不出去",
                                                     r.get("how_to_fix") or "")}
        except Exception as e:                                # noqa: BLE001 — 探针异常不许拖垮自检
            probe_out = {"sent": False, "error": "%s: %s" % (type(e).__name__, e)}
            probe_state = {"state": "failed", "why": "探针没发出去：%s" % e}

    out = sc.collect(dump, cred=cred, health=health, probe=probe_state)
    out["harness_id"] = harness_id
    out["probe"] = probe_out
    if want_probe:
        out["note"] = ("自检本身**不改任何东西**；`probe=true` 只是真发了一条极小消息给这个 agent，"
                       "回执到了才算验过（回执会自动写进回话证据与名册健康）。")
    return out
