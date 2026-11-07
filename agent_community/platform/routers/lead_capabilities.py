# -*- coding: utf-8 -*-
"""`routers/lead_capabilities`：**组长（或任一接入）的能力面**（`V134` ②，只读）。

老板 2026-11-07：「组长最适配，功能最全，所以可以用来带动不适配者」——
"最适配"必须是能算出来的事实，这个端点就是那份事实。

| 入口 | 用途 |
|---|---|
| `GET /api/workshop/{id}/lead-capabilities` | 这个工作间**组长**的能力面 + 平台补位建议 |
| `GET /api/harness/{id}/capabilities` | 任一接入的能力面（选组长前先看、排障也用） |

口径：`platform/lead_capabilities.py`（复用 `lead_roster` 的 tooling 与 `protocol_plan` 的路线，不另写一套）。
"""
from __future__ import annotations

from fastapi import APIRouter

from .. import lead_capabilities as caps

router = APIRouter(tags=["lead-capabilities"])


@router.get("/api/workshop/{ws_id}/lead-capabilities")
async def workshop_lead_capabilities(ws_id: str):
    """**这个工作间的组长能干什么、缺什么、平台替它补什么**（只读）。

    没有组长 ⇒ 404 `NO_LEAD` + 下一步（选组长 / 看候选），不返回空能力面假装成功。
    """
    from fastapi.responses import JSONResponse

    from .. import server as _srv

    if (_srv.workshops or {}).get(ws_id) is None:
        return JSONResponse({"ok": False, "code": "NO_SUCH_WORKSHOP",
                             "error": "没有这个工作间：%s" % ws_id}, status_code=404)
    out = caps.of_workshop_lead(ws_id)
    if not out.get("ok") and out.get("code") == "NO_LEAD":
        return JSONResponse(out, status_code=404)
    return out


@router.get("/api/harness/{harness_id}/capabilities")
async def harness_capabilities(harness_id: str):
    """**任一接入**的能力面（只读）。账号不存在 ⇒ 404。"""
    from fastapi.responses import JSONResponse

    from .. import server as _srv

    sess = (getattr(_srv.harness_manager, "sessions", {}) or {}).get(harness_id)
    if sess is None:
        return JSONResponse({"ok": False, "code": "NO_SUCH_HARNESS",
                             "error": "没有这个接入：%s" % harness_id,
                             "next": "去「对接中心」确认它还在；或 GET /api/harness-tooling 看已注册的"},
                            status_code=404)
    return caps.capabilities_of(getattr(sess, "info", None), harness_id=harness_id)


@router.get("/api/lead-preliminary")
async def lead_preliminary(task: str = ""):
    """**组长预列表**（`V137`，只读）：按**方向**给「单 / 团」两套配置。

    - 方向只认接入自己报的 `fit_directions`；**没报的不归任何方向组**（进 `no_direction`，
      写明"平台不替它编"）；
    - `?task=前端页面开发` 按**包含匹配**找方向；没匹配到就如实说 + 列出实际方向（**不退化**）；
    - 每个方向给 `solo`（一个人够不够）与 `team`（要不要搭团、搭谁、补哪项）；
    - 顶层 `by_mode` 按"单 / 团 / 还不行"三段总览。

    **不排分、不推荐**（`sort_note` 固定说明）。
    """
    from .. import lead_prelist as _pre
    from .. import server as _srv

    entries = []
    for hid, sess in (getattr(_srv.harness_manager, "sessions", {}) or {}).items():
        info = getattr(sess, "info", None)
        c = caps.capabilities_of(info, harness_id=str(hid))
        dirs = list(getattr(info, "fit_directions", None) or [])
        entries.append({"harness_id": str(hid),
                        "harness_name": c.get("harness_name") or str(hid),
                        "directions": dirs,
                        "capabilities": c})
    out = _pre.build_prelist(entries, task=task)
    out["candidate_count"] = len(entries)
    return out


@router.get("/api/lead-plan")
async def lead_plan(needs: str = "", workshop_id: str = ""):
    """**组长算法**（`V136`，只读）：两条路各给配置与理由。

    - 默认需求四项：`dispatch` / `receive_result` / `read_artifacts` / `wake`；
      `?needs=dispatch,receive_result` 可缩小（例如只要求能派活能收回执）；
    - `?workshop_id=` 时额外给**现状对照**（这间现在的组长是谁、fit 多少、缺什么）。

    输出：`mode=solo|team|blocked` + `lead/helpers/coverage/why/alternatives`；
    **不排分、不给"最佳人选"**（见 `sort_note`）。
    """
    from fastapi.responses import JSONResponse

    from .. import lead_algorithm as _alg
    from .. import server as _srv

    want = tuple(x.strip() for x in str(needs or "").split(",") if x.strip())
    bad = [x for x in want if x not in _alg.DEFAULT_NEEDS]
    if bad:
        return JSONResponse({"ok": False, "code": "BAD_NEEDS",
                             "error": "不认识的需求项：%s" % "、".join(bad),
                             "available": list(_alg.DEFAULT_NEEDS),
                             "next": "needs 只能从 available 里挑（逗号分隔）；不传就是四项全要"},
                            status_code=400)
    candidates = []
    for hid, sess in (getattr(_srv.harness_manager, "sessions", {}) or {}).items():
        c = caps.capabilities_of(getattr(sess, "info", None), harness_id=str(hid))
        candidates.append({"harness_id": str(hid),
                           "harness_name": c.get("harness_name") or str(hid),
                           "capabilities": c})
    out = _alg.plan(candidates, needs=(want or None))
    out["candidate_count"] = len(candidates)
    if workshop_id:
        if (_srv.workshops or {}).get(workshop_id) is None:
            return JSONResponse({"ok": False, "code": "NO_SUCH_WORKSHOP",
                                 "error": "没有这个工作间：%s" % workshop_id}, status_code=404)
        cur = caps.of_workshop_lead(workshop_id)
        out["current"] = {"ok": bool(cur.get("ok")),
                          "lead": {k: cur.get(k) for k in
                                   ("harness_id", "protocol_fit", "lead_eligible",
                                    "can_read_files", "can_write_files", "can_dispatch")},
                          "code": cur.get("code") or ""}
    out["sort_note"] = out.get("sort_note") or "按协议数与名字排序是技术性稳定，不是推荐度"
    return out


@router.get("/api/workshop/{ws_id}/lead-team")
async def workshop_lead_team(ws_id: str):
    """**主组长 + 助手 + 覆盖情况**（V135，只读）：谁拍板、谁当臂、还缺什么。

    - `lead`：主组长（唯一拍板/验收）的能力面与 `protocol_fit`；
    - `helpers[]`：每个成员"能给主组长补什么"（视觉型=平台代投递；能读文件=帮核产物；能收回执=能自己回报）；
    - `coverage`：派活 / 收回执 / 读产物 / 催办 四件事，团队里有人能做就 `covered_by`，没人做就 `missing=true`
      + `platform_will`（平台替它做什么）。

    **不排序、不推荐**：顺序按名单原序。
    """
    from fastapi.responses import JSONResponse

    from .. import server as _srv
    from .. import v5_roster as _v5

    if (_srv.workshops or {}).get(ws_id) is None:
        return JSONResponse({"ok": False, "code": "NO_SUCH_WORKSHOP",
                             "error": "没有这个工作间：%s" % ws_id}, status_code=404)
    mem = _v5.effective_members(ws_id)
    members = mem.get("members") or []
    lead_m = next((m for m in members
                   if str((m or {}).get("slot_dir") or "").strip().strip("/") == "_lead"), None)
    if lead_m is None:
        return JSONResponse({"ok": False, "code": "NO_LEAD",
                             "error": "这个工作间还没选组长（名单里没有 `_lead` 工位）",
                             "next": "先 POST /api/workshop/{id}/lead 选一个；或 GET /api/lead-roster 看候选"},
                            status_code=404)

    def _caps_of(m) -> dict:
        hid = str((m or {}).get("harness") or (m or {}).get("harness_id") or "")
        info = None
        try:
            sess = (getattr(_srv.harness_manager, "sessions", {}) or {}).get(hid) if hid else None
            info = getattr(sess, "info", None) if sess is not None else None
        except Exception:                                      # noqa: BLE001 — 取不到档案就按"能力未知"
            info = None
        return caps.capabilities_of(info, harness_id=hid)

    lead_caps = _caps_of(lead_m)
    helpers = []
    for m in members:
        slot = str((m or {}).get("slot_dir") or "").strip().strip("/")
        if not slot or slot == "_lead":
            continue
        c = _caps_of(m)
        fit = c.get("protocol_fit") or {}
        does = []
        if c.get("can_be_visual") or c.get("can_receive_notice") == "visual":
            does.append("平台把话投进它的窗口（视觉脚本）—— 主组长不必盯它")
        if c.get("can_read_files") is True:
            does.append("能读产物/证据 —— 可让它核对 sha256 或摘录")
        if str(c.get("can_receive_result") or "") == "yes":
            does.append("能自己交回执 —— 结果不用人转达")
        if c.get("can_write_files") is True:
            does.append("能写文件 —— 产物落在它工位的收件目录里，平台自动发现")
        helpers.append({
            "slot_dir": slot, "harness_id": str((m or {}).get("harness") or ""),
            "protocol_fit": fit.get("level") or "low",
            "can_do_for_lead": does or ["只能由平台代投递/代收，或人工转达"],
            "gaps": [g.get("what") for g in (c.get("gaps") or [])][:6],
            "capabilities": {k: c.get(k) for k in
                             ("can_write_files", "can_read_files", "can_receive_notice",
                              "can_receive_result", "can_be_visual", "can_dispatch")},
        })

    def _cover(pred) -> dict:
        who = [str((m or {}).get("slot_dir") or "") for m in members if pred(_caps_of(m))]
        return {"covered_by": who, "missing": not bool(who),
                "platform_will": "" if who else "平台代做（写文件/扫盘/投递/记账），结论仍要人复核"}

    coverage = {
        "dispatch": _cover(lambda c: bool(c.get("can_dispatch"))),
        "receive_result": _cover(lambda c: str(c.get("can_receive_result") or "") == "yes"),
        "read_artifacts": _cover(lambda c: c.get("can_read_files") is True),
        "wake": _cover(lambda c: bool(c.get("can_wake_automatically"))),
    }
    return {
        "ok": True, "workshop_id": ws_id,
        "lead": {"slot_dir": "_lead", "member_seq": lead_m.get("member_seq"),
                 "member_state": lead_m.get("state"),
                 "harness_id": str(lead_m.get("harness") or ""),
                 "protocol_fit": lead_caps.get("protocol_fit") or {},
                 "capabilities": {k: lead_caps.get(k) for k in
                                  ("can_write_files", "can_read_files", "can_receive_notice",
                                   "can_receive_result", "can_dispatch", "can_be_visual",
                                   "lead_eligible")},
                 "gaps": lead_caps.get("gaps") or []},
        "helpers": helpers,
        "coverage": coverage,
        "decide_note": ("**拍板权唯一**（主组长一个）：多主组长会让「这单谁放的」无解，"
                        "而平台不判断谁对。助手可以多个，只搬不拍板。"),
        "sort_note": "按名单原序，**不排序、不推荐**",
    }
