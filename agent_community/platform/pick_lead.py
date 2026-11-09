# -*- coding: utf-8 -*-
"""`platform/pick_lead`：**选定组长**的唯一实现（一处真相）。

## 为什么抽出这个模块（`V142`）

老板 2026-11-09：「用户在外界整理好方向之后，来平台**直接在大厅的组长列表里选组长**，
**点击选定组长后才建立工作间**，不用在大厅发言然后建工作间最后才拉组长。」

选组长的逻辑原本长在 `POST /api/workshop/{id}/lead` 里。现在多了一个入口
（建间时就指定组长）⇒ **两处必须走同一段代码**，否则迟早长出两套校验规则：
一条路允许"缺 fs_write 的壳"当组长、另一条不允许 —— 那种分裂最难查。

## 两步分开

- `precheck(harness_id)`：**不落盘**的校验（存在？能当组长？适配度？）。
  建间端点先调它 —— **不合格就直接拒，连间都不建**（不留"建了间没组长"的半成品）；
- `apply(ws, harness_id, model)`：落 `_lead` 槽位 + 置 `lead_in`（**幂等**：重复调用不重复建）。

## 口径不变

`lead-roster` 仍然**不排序、不推荐**（全项目口径：不评分不排名）。这里只负责"把一个选定的人装进去"。
"""
from __future__ import annotations


def _tool_of(harness_id: str) -> dict | None:
    from . import lead_roster as _roster_mod

    for item in _roster_mod.harness_options():
        if str(item.get("harness_id")) == str(harness_id):
            return item
    return None


def _fit_of(harness_id: str) -> dict:
    """协议适配度（算不出来就返回空 —— **不挡选人**，但要标出来）。"""
    try:
        from . import lead_capabilities as _caps_mod

        from . import server as _srv

        sess = (getattr(_srv.harness_manager, "sessions", {}) or {}).get(harness_id)
        info = getattr(sess, "info", None) if sess is not None else None
        return (_caps_mod.capabilities_of(info, harness_id=harness_id).get("protocol_fit") or {})
    except Exception as e:                                     # noqa: BLE001 — 适配度算不出不挡选人（如实喊一声）
        print("[pick_lead] 算适配度失败（%s: %s）；本次按不拦处理" % (type(e).__name__, e), flush=True)
        return {}


def precheck(harness_id: str) -> dict:
    """能不能让这个壳当组长（**不落盘**）。返回 `{ok, code?, error?, next?, tool?, fit?}`。"""
    hid = str(harness_id or "").strip()
    if not hid:
        return {"ok": False, "code": "BAD_REQUEST", "status": 400,
                "error": "要 harness_id（选哪个壳当组长）",
                "next": "GET /api/lead-roster 挑一个；网页版那类不能当组长"}
    tool = _tool_of(hid)
    if tool is None:
        return {"ok": False, "code": "NO_SUCH_HARNESS", "status": 404,
                "error": "没有这个 harness：%s" % hid,
                "next": "GET /api/harness-tooling 看已注册的"}
    fit = _fit_of(hid)
    if not tool.get("lead_eligible"):
        why = (tool.get("tooling") or {}).get("why") or "缺 fs_write 或（protocol_call / bridge_poll）"
        if fit.get("reasons"):
            why = "%s（适配度 %s：%s）" % (why, fit.get("level") or "?", "；".join(fit["reasons"]))
        return {"ok": False, "code": "NOT_LEAD_ELIGIBLE", "status": 409,
                "error": "这个 harness 不能当主组长（只能当助手/成员）",
                "why": why, "protocol_fit": fit,
                "next": ("给它一个能派活并收回执的协议（A2A / HTTP_api / ACP / MCP / 桥），"
                         "或让它报 tools 让平台测得它能写文件；再不行就换一个主组长、让它当助手"),
                "policy": "拍板权唯一：主组长一个；助手可多个（只搬不拍板）"}
    return {"ok": True, "tool": tool, "fit": fit, "harness_id": hid}


def apply(ws, harness_id: str, *, model: str = "", wake: bool = True) -> dict:
    """把选定的壳**装成组长**：落 `_lead` 槽位 + 置 `lead_in`。

    ⚠️ 入参 `ws` 是工作间对象（`server.workshops[wid]`）。失败时返回带 `code` 的 dict，**不抛**。
    """
    r = precheck(harness_id)
    if not r.get("ok"):
        return r
    hid = str(harness_id).strip()
    from . import v5_roster as _rw

    workshop_id = str(getattr(ws, "workshop_id", "") or "")
    if not workshop_id:
        return {"ok": False, "code": "BAD_REQUEST", "status": 400,
                "error": "取不到 workshop_id，没法落组长槽位"}
    used_model = str(model or (r.get("tool") or {}).get("model") or "")
    slots = _rw.add_slots(workshop_id, [{"岗位": "_lead", "harness": hid,
                                        "model": used_model, "人数": 1}],
                          source="lead-choice")
    try:
        ws.status = "lead_in"
        from .server import save_state

        save_state()
    except Exception as e:                                     # noqa: BLE001 — 状态没写成要说清（槽位已落）
        return {"ok": False, "code": "STATE_WRITE_FAILED", "status": 500,
                "error": "组长已落名单，但工作间状态没写成 lead_in（%s: %s）"
                         % (type(e).__name__, e),
                "next": "查日志后重试；名单不会重复建（幂等）",
                "slots": slots, "harness_id": hid}
    out = {"ok": True, "workshop_id": workshop_id, "status": "lead_in",
           "harness_id": hid, "model": used_model, "slots": slots,
           "protocol_fit": r.get("fit") or {},
           "note": "组长已进名单（`_lead` 槽位），工作间状态 lead_in"}
    # ★V142 补丁（老板 2026-11-09：「选中组长后得把**具体桥**给到它手里，从而唤醒、拉入」）：
    #   落完名单**立刻**交桥 + 唤醒 —— 三件（对接单 / 通知 / 触达）都在 `pick_lead_handoff.handoff()`。
    if wake:
        try:
            from . import pick_lead_handoff as _h

            out["handoff"] = _h.handoff(ws, hid)
            out["awake"] = bool((out["handoff"] or {}).get("awake"))
        except Exception as e:                                 # noqa: BLE001 — 交桥失败要说清（名单已落，不回滚）
            out["handoff"] = {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}
            out["awake"] = False
            out["handoff_error"] = ("组长已在名单里，但**交桥/唤醒这步炸了**：%s: %s。"
                                    "可重试 `POST /api/workshop/{id}/lead-kit/send` 或人工贴单"
                                    % (type(e).__name__, e))
    else:
        out["handoff"] = {"ok": True, "skipped": "wake=false（只落名单，不交桥不唤醒）"}
    return out
