# -*- coding: utf-8 -*-
"""`routers/p5_v5`：**入口**（组长列表 · 对接单 · 命名 N+1），`V115` P5 批。

| 端点 | 干什么 |
|---|---|
| `GET  /api/lead-roster?direction=` | 组长候选（**只列 lead_eligible**；不排序不推荐） |
| `GET  /api/workshop/{id}/lead-kit?format=text\|json&agent_key=&issue_token=1` | 拼**组长对接单** |
| `POST /api/workshop/{id}/lead` | 选定组长（落 roster + 置 `lead_in`） |
| `POST /api/workshop/{id}/members/{seq}/kit` | 拼**成员对接单**（命名 N+1） |
| `GET  /api/harness-tooling` | 所有 harness 的 tooling 与"能不能当组长"（含原因） |

**边界（如实说）**：本批只做"拼装 + 记录"，**不代写**文件、不替它改会话名；
A 档 token 只有显式 `issue_token=1` 时才签，且**只出现在这一份文本里**（库里只存指纹）。
"""
from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from .. import intake_token as _tok
from .. import kit as _kit
from .. import lead_roster as _roster_mod
from .. import manifest as _mf
from .. import mind as _mind
from .. import v5_roster as _rw

router = APIRouter()


def _ws(workshop_id: str):
    from ..server import workshops

    return workshops.get(workshop_id)


def _no_ws(workshop_id: str):
    return JSONResponse({"ok": False, "code": "NO_SUCH_WORKSHOP",
                         "error": "没有这个工作间：%s" % workshop_id,
                         "next": "GET /api/workshops 看有哪些"}, status_code=404)


@router.get("/api/harness-tooling")
async def harness_tooling():
    """所有 harness 的 tooling 与"能不能当组长"（含为什么不能）。"""
    items = _roster_mod.harness_options()
    return {"ok": True, "count": len(items), "items": items,
            "hint": "组长门槛：`fs_write=true` 且（`protocol_call` 或 `bridge_poll`）；"
                    "`fs_write=null` 表示平台测不到 ⇒ 不进候选"}


@router.get("/api/lead-roster")
async def lead_roster(direction: str = ""):
    """**组长列表**（老板 2026-11-07：「这是为了做组长列表的」）。

    在既有"只列能当组长的（按名字排序，不是推荐）"之上，补三类**可判读**的信息
    （算法在 `platform/lead_algorithm.py`，能力面在 `platform/lead_capabilities.py`）：

    - 每个候选：`protocol_fit` / `covers`（覆盖哪几件事）/ `solo_feasible`（它自己能不能闭环）/
      `needs_helpers`（缺什么）/ `helper_options`（谁能补）；
    - 顶层 `groups`：**① 可独立胜任 ② 需配助手 ③ 只能当助手**（列表可以直接按这三组渲染）；
    - 顶层 `plans`：`V136` 两条路的结论（solo / team / blocked），与列表同一份事实。

    **仍然不排序、不推荐**：组内按 `harness_id` 字典序；分数不出现（只有事实与缺口）。
    """
    from .. import lead_algorithm as _alg
    from .. import lead_capabilities as _caps
    from ..server import harness_manager as _hm

    base = _roster_mod.candidates(direction)
    caps_by: dict = {}
    for hid, sess in (getattr(_hm, "sessions", {}) or {}).items():
        caps_by[str(hid)] = _caps.capabilities_of(getattr(sess, "info", None), harness_id=str(hid))

    def _covers(hid: str) -> list:
        c = caps_by.get(hid) or {}
        return _alg.covers_of(c)

    def _fit(hid: str) -> str:
        return str(((caps_by.get(hid) or {}).get("protocol_fit") or {}).get("level") or "low")

    # 助手候选：**不能当组长**但能补事的人（视觉型/落盘型都在这里）
    helper_pool = []
    for hid, c in sorted(caps_by.items()):
        cov = _alg.covers_of(c)
        if not cov:
            continue
        helper_pool.append({"harness_id": hid,
                            "harness_name": c.get("harness_name") or hid,
                            "protocol_fit": _fit(hid),
                            "lead_eligible": bool(c.get("lead_eligible")),
                            "covers": cov,
                            "can_do_for_lead": [(_alg.NEED_LABELS.get(x, x)) for x in cov]})

    groups = {"solo_ready": [], "needs_helpers": [], "helpers_only": []}
    enriched: list = []
    for c in base.get("candidates") or []:
        hid = str(c.get("harness_id") or "")
        cov = _covers(hid)
        need = [n for n in _alg.DEFAULT_NEEDS if n not in cov]
        opts = [h for h in helper_pool
                if h["harness_id"] != hid and set(h["covers"]) & set(need)]
        row = {**c,
               "protocol_fit": _fit(hid),
               "covers": cov,
               "solo_feasible": not need,
               "needs_helpers": need,
               "helper_options": [{"harness_id": h["harness_id"], "adds": [x for x in h["covers"]
                                                                           if x in need]}
                                  for h in opts[:6]]}
        enriched.append(row)
        if not need:
            groups["solo_ready"].append(hid)
        else:
            groups["needs_helpers"].append(hid)     # 需要助手（有没有人补，plans 里会如实说）
    for h in helper_pool:
        if not h["lead_eligible"] and h["covers"]:
            groups["helpers_only"].append(h["harness_id"])

    out = dict(base)
    out["candidates"] = enriched
    out["groups"] = groups
    out["helpers"] = helper_pool
    out["needs_def"] = {"items": list(_alg.DEFAULT_NEEDS),
                        "labels": dict(_alg.NEED_LABELS),
                        "note": "四件事：能派活 / 能收回执 / 能看产物 / 能自动唤醒"}
    out["plans"] = _alg.plan([{"harness_id": hid,
                               "harness_name": (caps_by.get(hid) or {}).get("harness_name") or hid,
                               "capabilities": caps_by.get(hid) or {}}
                              for hid in caps_by])
    out["group_hint"] = ("① 可独立胜任（四件事全包）② 需配助手（缺的项有谁能补）"
                         "③ 只能当助手 —— 顺序按 `harness_id` 字典序，**不是推荐度**")
    return out


def _a2a_contract_section(*, harness_id: str, workshop_id: str, slot_dir: str,
                          workshop_dir: str, member_seq: int = 0) -> tuple:
    """A2A 工位的「派活方式」段（**L3：L2 模板 + 工作间实时路径**）。

    ★2026-11-01（老板：「注册后变为一个具体模板，工作间里用**模板 + 实时路径**重新拉人」）：
    **成员对接单与组长对接单共用这一处** —— 别再各写一份副本（规则 20：一处真相）。

    返回 `(markdown 段, 元信息 dict)`。任何异常都在内部兜住并写进正文 ——
    **对接单绝不能因为它炸掉就 500**（用户看到"打不开"会当成平台坏了）。
    """
    l3, ctx_id, ci = {}, "", {}
    try:
        from ..server import harness_manager as _hm
        from ..a2a_context import current_for as _ctx_current
        from ..bridge_template import render_for_workshop as _render

        ci = _ctx_current(harness_id, workshop_id, slot_dir)
        ctx_id = str(ci.get("context_id") or "")
        sess = (_hm.sessions or {}).get(harness_id)
        if sess is not None and getattr(sess, "info", None) is not None:
            l3 = _render(sess.info, workshop_id=workshop_id, workshop_dir=workshop_dir,
                         slot_dir=slot_dir, member_seq=int(member_seq),
                         task_rel="", context_id=ctx_id)
    except Exception as e:                                     # noqa: BLE001 — 渲染失败要说清
        print("[kit] A2A 落地契约渲染失败（单子会缺端点/回报）：%s: %s" % (type(e).__name__, e),
              flush=True)
    meta = {"endpoint": "", "context_id": ctx_id, "seq": (ci or {}).get("seq"),
            "report": {}, "error": ""}
    try:
        meta["endpoint"] = str((l3 or {}).get("endpoint") or "")
        rp = (l3 or {}).get("report") or {}
        meta["report"] = rp if isinstance(rp, dict) else {}
        rows = [
            "- **平台会往这里发任务**：`%s`" % (meta["endpoint"]
                                          or "（缺 `api_base_url` —— 回对接中心补）"),
            "- **协议**：JSON-RPC `tasks/send`（对端只认 v1.0 时平台会自动改发 `SendMessage`）",
            "- **会话**：平台派活会带 `contextId`（同一工位复用同一上下文；上下文老化后自动换新）",
            "- **当前会话**：`%s`（第 %s 个）" % (ctx_id or "（还没建）",
                                          (str(meta["seq"] or "?") if ctx_id else "-")),
        ]
        if meta["report"].get("url"):
            rows.append("- **干完怎么回报**：`POST %s`\n\n```json\n%s\n```"
                        % (meta["report"].get("url"),
                           json.dumps(meta["report"].get("body") or {}, ensure_ascii=False, indent=1)))
        return ("\n\n## A2A 派活方式（平台直接调用你，**没有桥**）\n\n"
                + "\n".join(rows)
                + "\n\n注意：平台还会按 `tasks/get` 回查你的状态，**就算不回执**结果也可能被写回；"
                  "但产物清单会是空的 —— 要留下产物就按上面的方式交一次。\n", meta)
    except Exception as e2:                                    # noqa: BLE001 — 对外说清，不 500
        meta["error"] = "%s: %s" % (type(e2).__name__, str(e2)[:200])
        print("[kit] A2A 契约段拼装失败：%s" % meta["error"], flush=True)
        return ("\n\n## A2A 派活方式（**生成失败**）\n\n"
                "原因：`%s`\n\n这条工位按 A2A 派活（没有桥），所以这里**不给桥参数**（给了反而是误导）。"
                "请把上面的原因反馈给平台维护者。\n" % meta["error"], meta)


@router.get("/api/workshop/{workshop_id}/lead-kit")
async def lead_kit(workshop_id: str, format: str = "text", agent_key: str = "",
                   issue_token: int = 0, harness_id: str = "",
                   template: str = "pending_poll"):
    """拼组长对接单。`issue_token=1` 时才现签 A 档 token（只进这一份文本）。"""
    ws = _ws(workshop_id)
    if ws is None:
        return _no_ws(workshop_id)
    wp = str(getattr(ws, "workspace_dir", "") or "")
    if not wp:
        return JSONResponse({"ok": False, "code": "NO_WORKSHOP_PATH",
                             "error": "这个工作间还没有工作区路径",
                             "next": "先设 workshop_path（建间时可传）"}, status_code=400)
    key = agent_key or harness_id or "lead"
    mp = str(_mind.mind_path(key))
    if not Path(mp).is_dir():
        _mind.ensure(key)
    ok, mfb, errs = _mf.read(wp)
    entry = list((mfb or {}).get("entry_points") or ["_lead/建模.md"])
    ignore = list((mfb or {}).get("ignore_zones") or [])
    label = _kit.session_label(workshop_id, str(getattr(ws, "name", "") or ""), "组长", 1)
    token, cred_note = "", ""
    if int(issue_token or 0):
        t = _tok.issue(workshop_id, "组长")
        token = t["token"]
        cred_note = ("⚠️ 这是 A 档一次性凭据（绑本工作间本角色、可吊销、库里只存指纹）。"
                     "**别转贴到别处**；它只该出现在这一份对接单里。")
    text = _kit.build_kit(role="组长", workshop_id=workshop_id,
                          project_short=str(getattr(ws, "name", "") or ""),
                          session_label_value=label, workshop_path=wp, mind_path=mp,
                          slot_dir="_lead", notice_path="_lead/_notice.md",
                          entry_points=entry, ignore_zones=ignore,
                          member_field_value=_kit.member_field("_lead", True),
                          credential=token, credential_note=cred_note, is_lead=True)
    # ★2026-10-31（W前端-79 遗留口径）：**把桥模板参数一并拼进对接单** ——
    #   用户要"一键复制就把人拉起来"，不该再分两块拼。目录一律平台填（§8.2.1）。
    _bid = str(harness_id or "")
    if not _bid:
        _lead_slot = next((m for m in _rw.roster(workshop_id)["members"]
                           if str(m.get("slot_dir") or "") == "组长"), None)
        _bid = str((_lead_slot or {}).get("harness_id") or "")
    # ★2026-11-01（老板：「改」—— 组长对接单也要跟成员一样按"怎么被派活"分流）：
    #   组长若是 A2A 接入，给它桥参数同样是误导；这条路与成员共用 `_a2a_contract_section`。
    _lead_is_a2a = False
    try:
        from ..dispatch import _harness_is_a2a as _is_a2a_fn

        _lead_is_a2a = bool(_is_a2a_fn(_bid)) if _bid else False
    except Exception:                                          # noqa: BLE001 — 判不出来按桥型处理
        _lead_is_a2a = False
    _bfill = {}
    if _lead_is_a2a:
        _sec, _meta = _a2a_contract_section(harness_id=_bid, workshop_id=workshop_id,
                                           slot_dir="_lead", workshop_dir=wp, member_seq=1)
        text = str(text).rstrip() + _sec
    else:
        _bfill = _bridge_fields_for(ws, harness_id=_bid, for_member=False,
                                    template=template or "pending_poll")
        if _bfill.get("ok"):
            _rows = "\n".join("- `%s` = `%s`" % (k, v) for k, v in _bfill["fields"].items() if v)
            _miss = ("；**还缺**：" + "、".join(_bfill["missing"])) if _bfill["missing"] else ""
            text = (text.rstrip() + "\n\n## 桥参数（平台已填好路径，别手改）\n\n"
                    "模板：`%s`%s\n\n%s\n\n粘法：把这些值填进 `POST /api/bridge/generate` 生成桥。\n"
                    % (_bfill["template"], _miss, _rows))
        else:
            text = (text.rstrip() + "\n\n## 桥参数\n\n这间还没有可用的桥模板（%s）—— "
                    "确认 `agent_community/bridge_templates/` 里有这个模板。\n" % _bfill.get("code"))
    out = {"ok": True, "kit": text, "session_label": label, "mind_path": mp,
           "workshop_path": wp, "manifest_ok": bool(ok), "manifest_errors": errs,
           "has_token": bool(token),
           # A2A 时 `bridge_*` 一律给空 + 说明，别让调用方以为"该去架桥"
           "bridge_template": ("" if _lead_is_a2a else _bfill.get("template", "")),
           "bridge_fields": ({} if _lead_is_a2a else _bfill.get("fields", {})),
           "bridge_missing": ([] if _lead_is_a2a else _bfill.get("missing", [])),
           "wakeup_kind": ("a2a" if _lead_is_a2a else "bridge"),
           "a2a_contract": (_meta if _lead_is_a2a else {}),
           "hint": ("整段复制给组长；它是 A2A 接入 —— 平台直接按 tasks/send 派活，不需要架桥"
                    if _lead_is_a2a else
                    "整段复制给组长；它照 Boot Protocol 先读两个根，再按「桥参数」生成桥")}
    if (format or "text").lower() in ("md", "text"):
        return PlainTextResponse(text, media_type="text/markdown; charset=utf-8")
    return out


@router.post("/api/workshop/{workshop_id}/lead")
async def choose_lead(workshop_id: str, request: Request):
    """选定组长：落 roster（`_lead` 工位）+ 置 `lead_in`。body：`{harness_id, model?, agent_key?}`。"""
    ws = _ws(workshop_id)
    if ws is None:
        return _no_ws(workshop_id)
    try:
        body = await request.json()
    except Exception:                                          # noqa: BLE001
        body = {}
    body = body if isinstance(body, dict) else {}
    hid = str(body.get("harness_id") or "").strip()
    if not hid:
        return JSONResponse({"ok": False, "code": "BAD_REQUEST",
                             "error": "要 harness_id（选哪个壳当组长）",
                             "next": "GET /api/lead-roster 挑一个；网页版那类不能当组长"},
                            status_code=400)
    tool = None
    for item in _roster_mod.harness_options():
        if item["harness_id"] == hid:
            tool = item
            break
    if tool is None:
        return JSONResponse({"ok": False, "code": "NO_SUCH_HARNESS",
                             "error": "没有这个 harness：%s" % hid,
                             "next": "GET /api/harness-tooling 看已注册的"}, status_code=404)
    # ★2026-11-07（V135，老板：「组长要用协议高适配型（单个/多个？）的agent来承担」）：
    #   适配度先算出来，**放进入口闸门的响应里**（不新增错误码 —— 实测发现：
    #   `fit=low` ⇔ 缺"能派活+能收回执"或写不了文件 ⇒ 必然 `lead_eligible=false`，
    #   所以"低适配只能当助手"这件事天然由既有 `NOT_LEAD_ELIGIBLE` 拦住，只是要把原因说清）。
    _fit = {}
    try:
        from .. import lead_capabilities as _caps_mod
        from ..server import harness_manager as _hm

        sess = (getattr(_hm, "sessions", {}) or {}).get(hid)
        info = getattr(sess, "info", None) if sess is not None else None
        _fit = (_caps_mod.capabilities_of(info, harness_id=hid).get("protocol_fit") or {})
    except Exception as e:                                     # noqa: BLE001 — 算不出适配度不挡选人，但要标出来
        print("[lead] 算适配度失败（%s: %s）；本次按不拦处理" % (type(e).__name__, e), flush=True)
    if not tool["lead_eligible"]:
        _why = (tool["tooling"].get("why")
                or "缺 fs_write 或（protocol_call / bridge_poll）")
        if _fit.get("reasons"):
            _why = "%s（适配度 %s：%s）" % (_why, _fit.get("level") or "?",
                                         "；".join(_fit.get("reasons") or []))
        return JSONResponse({"ok": False, "code": "NOT_LEAD_ELIGIBLE",
                             "error": "这个 harness 不能当主组长（只能当助手/成员）",
                             "why": _why,
                             "protocol_fit": _fit,
                             "next": ("给它一个能派活并收回执的协议（A2A / HTTP_api / ACP / MCP / 桥），"
                                      "或让它报 `tools` 让平台测得它能写文件；再不行就换一个主组长、让它当助手"),
                             "policy": "拍板权唯一：主组长一个；助手可多个（只搬不拍板）"},
                            status_code=409)
    r = _rw.add_slots(workshop_id, [{"岗位": "_lead", "harness": hid,
                                     "model": str(body.get("model") or tool.get("model") or ""),
                                     "人数": 1}], source="lead-choice")
    try:
        ws.status = "lead_in"
        from ..server import save_state

        save_state()
    except Exception as e:                                     # noqa: BLE001 — 置状态失败要说
        return JSONResponse({"ok": False, "code": "STATE_WRITE_FAILED",
                             "error": "组长已落名单，但工作间状态没写成 lead_in（%s: %s）"
                                      % (type(e).__name__, e),
                             "next": "查日志后重试；名单不会重复建（幂等）"}, status_code=500)
    return {"ok": True, "workshop_id": workshop_id, "status": "lead_in",
            "harness_id": hid, "members_created": r["members_created"],
            "protocol_fit": _fit,
            "fit_note": ("它读不了文件 ⇒ 产物与证据由**平台代读**（L0 扫盘），结论仍要人复核"
                         if str(_fit.get("level") or "") == "medium" else
                         "适配度 high：能派活、能收回执、能读写文件 —— 适合当拍板人"),
            "next": "GET …/lead-kit 拿对接单，整段复制给组长"}


@router.post("/api/workshop/{workshop_id}/members/{member_seq}/kit")
async def member_kit(workshop_id: str, member_seq: int, request: Request):
    """拼成员对接单（命名 N+1；A 档可现签 token）。body：`{issue_token?, agent_key?}`。"""
    ws = _ws(workshop_id)
    if ws is None:
        return _no_ws(workshop_id)
    try:
        body = await request.json()
    except Exception:                                          # noqa: BLE001
        body = {}
    body = body if isinstance(body, dict) else {}
    m = _rw.find_member(workshop_id, member_seq)
    if m is None:
        return JSONResponse({"ok": False, "code": "NO_SUCH_MEMBER",
                             "error": "没有这个工位：%s" % member_seq,
                             "next": "GET …/roster 看有哪些 member_seq"}, status_code=404)
    wp = str(getattr(ws, "workspace_dir", "") or "")
    slot = str(m.get("slot_dir") or "")
    tier = str(m.get("tier") or "")
    key = str(body.get("agent_key") or m.get("session_label") or slot)
    mp = str(_mind.mind_path(key))
    if not Path(mp).is_dir():
        _mind.ensure(key)
    ok, mfb, errs = _mf.read(wp)
    label = _kit.session_label(workshop_id, str(getattr(ws, "name", "") or ""),
                               slot.split("/")[-1] or "成员", int(member_seq))
    token, cred_note = "", ""
    if int(body.get("issue_token") or 0) or tier == "a_protocol":
        t = _tok.issue(workshop_id, slot)
        token = t["token"]
        cred_note = ("⚠️ A 档一次性凭据（绑本工作间本工位、可吊销）。只出现在这一份对接单里，"
                     "**别转贴**。")
    # ★2026-10-31（老板追问「你怎么知道它接上了」）：A 档改成**平台自验** ——
    #   现生成一次性回话口令（库里只存指纹），写进对接单第 6b 节；它答对了平台自己盖时间戳。
    #   不再接受"随便发一次请求"或"人打个勾"当 A 档证据。
    handshake = ""
    if token:
        _hs = _rw.issue_handshake(workshop_id, int(member_seq))
        handshake = str(_hs.get("handshake") or "")
    text = _kit.build_kit(role=slot or "成员", workshop_id=workshop_id,
                          project_short=str(getattr(ws, "name", "") or ""),
                          session_label_value=label, workshop_path=wp, mind_path=mp,
                          slot_dir=slot, notice_path="%s/_notice.md" % slot.split("/")[0],
                          entry_points=list((mfb or {}).get("entry_points") or []),
                          ignore_zones=list((mfb or {}).get("ignore_zones") or []),
                          member_field_value=_kit.member_field(slot, False),
                          credential=token, credential_note=cred_note,
                          handshake=handshake, member_seq=int(member_seq))
    # ★2026-11-01（老板：「注册后变为一个具体模板，工作间里**不用连接好的会话**，
    #   而是用**模板 + 工作间实时路径**重新拉人」）：
    #   对接单也要按"这个工位怎么被派活"分流 —— A2A **没有桥**，给它桥参数是误导。
    #   A2A 给的是 **L3 落地契约**：端点（来自 L2 模板）+ 本次工作间的会话 + 回报地址。
    _is_a2a = False
    _mp_bfill = {}                                             # ★默认空：A2A 不走桥参数，但 return 里会读它
    try:
        from ..dispatch import _harness_is_a2a as _is_a2a_fn

        _is_a2a = bool(_is_a2a_fn(str(m.get("harness_id") or "")))
    except Exception:                                          # noqa: BLE001 — 判不出来按桥型处理
        _is_a2a = False

    if _is_a2a:
        _sec, _meta = _a2a_contract_section(harness_id=str(m.get("harness_id") or ""),
                                           workshop_id=workshop_id, slot_dir=slot,
                                           workshop_dir=wp, member_seq=int(member_seq))
        text = str(text).rstrip() + _sec
    else:
        # ★2026-10-31（W前端-81 §6）：**成员对接单也自带桥参数** —— 拉组长与拉员工是同一个动作，
        #   不该让用户再回头找 `bridge-fill`。目录字段一律平台填（§8.2.1）：成员用**自己的工位目录**。
        _mp_bfill = _bridge_fields_for(ws, harness_id=str(m.get("harness_id") or ""),
                                       for_member=True, slot_dir=slot,
                                       template=str(body.get("template") or "pending_poll"))
        if _mp_bfill.get("ok"):
            _rows = "\n".join("- `%s` = `%s`" % (k, v) for k, v in _mp_bfill["fields"].items() if v)
            _miss = ("；**还缺**：" + "、".join(_mp_bfill["missing"])) if _mp_bfill["missing"] else ""
            text = (text.rstrip() + "\n\n## 桥参数（平台已填好路径，别手改）\n\n"
                    "模板：`%s`%s\n\n%s\n\n粘法：把这些值填进 `POST /api/bridge/generate` 生成桥。\n"
                    % (_mp_bfill["template"], _miss, _rows))
        else:
            text = (text.rstrip() + "\n\n## 桥参数\n\n这间还没有可用的桥模板（%s）。\n"
                    % _mp_bfill.get("code"))
    return {"ok": True, "kit": text, "member_seq": int(member_seq), "slot_dir": slot,
            "tier": tier or "（未判定）", "session_label": label,
            "has_token": bool(token), "handshake_issued": bool(handshake),
            "handshake_endpoint": ("POST /api/workshop/%s/members/%d/handshake" % (workshop_id, int(member_seq)))
            if handshake else "",
            "manifest_ok": bool(ok), "manifest_errors": errs,
            "member_bridge_template": _mp_bfill.get("template", ""),
            "member_bridge_fields": _mp_bfill.get("fields", {}),
            "member_bridge_missing": _mp_bfill.get("missing", []),
            "next": ("整段复制给这个成员；它接上后回来打勾（平台不代打）。"
                     "A 档另外要它用第 6b 节口令打一次 handshake —— 那一下是平台自己验的"
                     if handshake else
                     "整段复制给这个成员；它接上后回来打勾（平台不代打）")}


@router.post("/api/workshop/{workshop_id}/members/{member_seq}/handshake")
async def member_handshake(workshop_id: str, member_seq: int, request: Request):
    """**A 档「平台自验」**：它用对接单第 6b 节的一次性口令回一次话（`V116` §6b，2026-10-31）。

    为什么要有这一下（老板追问「你怎么知道它接上了」）：以前 A 档的 `protocol_ok` 也靠人勾或接口喂，
    "随便发一次请求"就算接上。现在必须**答得出写在那张对接单上的一次性口令**，
    平台核对通过后**自己**记 `protocol_ok` + 来源 `platform-observed` + 时间戳。

    鉴权：必须带该工位的 A 档凭据（`Authorization: Bearer <凭据>` 或 `X-Intake-Token`），
    越界（换工作间/换工位）一律 403 —— 这挡住"拿别人的 token 来刷"。
    body：`{"code": "<口令>", "note": "我是 <会话名>"（可选）}`
    """
    from .. import intake_token as _tok2

    ws = _ws(workshop_id)
    if ws is None:
        return _no_ws(workshop_id)
    try:
        body = await request.json()
    except Exception:                                          # noqa: BLE001 — 没 body 就是没给口令
        body = {}
    body = body if isinstance(body, dict) else {}
    code = str(body.get("code") or "").strip()
    if not code:
        return JSONResponse({"ok": False, "code": "HANDSHAKE_MISSING",
                             "error": "没给口令（body 里要有 code）",
                             "next": "照对接单第 6b 节原样复制那段口令"},
                            status_code=400)

    # 凭据：只认这个工位的 A 档 token（没有就拒 —— 否则"谁知道口令谁就能刷"）
    hdr = request.headers
    raw_tok = ""
    auth = str(hdr.get("authorization") or "")
    if auth.lower().startswith("bearer "):
        raw_tok = auth[7:].strip()
    raw_tok = raw_tok or str(hdr.get("x-intake-token") or "").strip()
    # ⚠️ `intake_token.verify()` 的返回形状是 `((workshop_id, member), 错误码)`，**不是**扁平的
    pair, why = _tok.verify(raw_tok)
    if pair is None:
        return JSONResponse({"ok": False, "code": "TOKEN_MISSING" if not raw_tok else why,
                             "error": "这次回话必须带该工位的 A 档凭据" if not raw_tok
                                      else "凭据无效或已吊销（%s）" % why,
                             "next": "用对接单第 6 节那份凭据原样带上（Authorization: Bearer …）"},
                            status_code=401)
    wid_ok, member_key = pair
    m = _rw.find_member(workshop_id, int(member_seq))
    if m is None:
        return JSONResponse(
            {"ok": False, "code": "NO_SUCH_MEMBER", "error": "没有这个工位：%s" % member_seq,
             "next": "GET …/roster 看有哪些 member_seq"}, status_code=404)
    if str(wid_ok) != str(workshop_id) or str(member_key) != str(m.get("slot_dir")):
        return JSONResponse({"ok": False, "code": "TOKEN_SCOPE",
                             "error": "这份凭据不是给这个工作间这个工位的",
                             "next": "用这个工位自己的对接单（每份单子绑自己的工位）"},
                            status_code=403)

    out = _rw.verify_handshake(workshop_id, int(member_seq), code,
                               token_fingerprint=str(_tok.issue(workshop_id, str(m.get("slot_dir")))
                                                     .get("fingerprint") or ""))
    try:
        out["mirror"] = _rw.write_mirror(workshop_id, str(getattr(ws, "workspace_dir", "") or ""))
    except Exception as e:                                     # noqa: BLE001 — 镜像失败不阻断（主库已写）
        out["mirror"] = {"ok": False, "why": "镜像刷新失败：%s: %s" % (type(e).__name__, str(e)[:100])}
    return JSONResponse(out, status_code=200 if out.get("ok") else 400)


@router.get("/api/workshop/{workshop_id}/next-label")
async def next_label(workshop_id: str, role: str = "成员"):
    """命名 N+1：给"再接一个人"用的下一个会话名（**不复用**已用过的序号）。"""
    ws = _ws(workshop_id)
    if ws is None:
        return _no_ws(workshop_id)
    used = [int(m.get("member_seq") or 0) for m in _rw.roster(workshop_id)["members"]]
    seq = (max(used) if used else 0) + 1
    label = _kit.session_label(workshop_id, str(getattr(ws, "name", "") or ""), role, seq)
    return {"ok": True, "next_seq": seq, "session_label": label,
            "used_seqs": sorted(used),
            "hint": "序号 N+1 不复用；同 harness 多会话靠这个 label 区分（已拍：不做占用制）"}


@router.post("/api/harness/{harness_id}/probe-tooling")
async def probe_tooling(harness_id: str):
    """**实测**这个 harness 的落点能不能写：平台在 `<AC_DATA_DIR>/probe/<id>/` 真写一次再读回。

    ⚠️ 边界（如实说）：这一步证明的是**平台侧落点可写**，不等于那个 agent 自己有 fs 工具；
    它自己能不能写，仍要看它报的 `tools` 或它真写出产物（P4 的目录 diff）。
    """
    from .. import tooling_probe as _tp

    result = _tp.probe_platform_fs(harness_id)
    rec = _tp.record(harness_id, result)
    out = {"ok": bool(result.get("ok")), "harness_id": harness_id,
           "measured": rec["measured"],
           "hint": "实测为真 ⇒ 组长门槛的 `fs_write` 这一项算过；还要有 protocol_call 或 bridge_poll"}
    return out


def _lib_dir(ws) -> str:
    """资料库的**有效**目录（自定义优先，空则 `<根B>/library/`）—— 与 `library` 端点同一口径。"""
    from pathlib import Path

    custom = str(getattr(ws, "library_path", "") or "").strip()
    if custom:
        return custom
    return str(Path(str(getattr(ws, "workspace_dir", "") or "")) / "library")


@router.get("/api/workshop/{workshop_id}/console-context")
async def console_context(workshop_id: str):
    """**给控制台自动填盘用的一份上下文**（老板 2026-10-29：从工作间点进来要自动带路径）。

    返回的都是平台**已知的事实**，取不到的字段就是空串/0（并给 `notes` 说明为什么），
    前端据此填空即可 —— 不用自己拼路径、也不许猜。
    """
    ws = _ws(workshop_id)
    if ws is None:
        return _no_ws(workshop_id)
    wp = str(getattr(ws, "workspace_dir", "") or "")
    notes = []
    manifest_ok, manifest_body, manifest_errors = (False, {}, [])
    if wp:
        manifest_ok, manifest_body, manifest_errors = _mf.read(wp)
    else:  # 没有工作区路径 ⇒ 控制台第 0 块就得先补这个
        notes.append("这个工作间还没有工作区路径（根B）：控制台里先补 `workshop_path`")
    mind_path = str((manifest_body or {}).get("mind_path") or "")
    agent_key = ""
    if mind_path:
        agent_key = mind_path.replace("\\", "/").rstrip("/").split("/")[-1]
    else:
        notes.append("manifest 里没有 `mind_path`：控制台里先建心智根再写 manifest")
    if wp and not manifest_ok:
        notes.append("manifest 不合法（G0）：%s" % (
            "、".join(str(e.get("code")) for e in (manifest_errors or [])[:4]) or "原因见 errors"))
    roster = _rw.roster(workshop_id)
    direction = getattr(ws, "direction", None) or {}
    if isinstance(direction, dict):
        brief = str(direction.get("brief") or "")
        enum = [str(x) for x in (direction.get("enum") or [])]
    else:  # 历史数据可能是别的形状 ⇒ 如实说，不猜
        brief, enum = "", []
        notes.append("工作间的 direction 不是预期结构（%s）" % type(direction).__name__)
    return {"ok": True, "workshop_id": workshop_id,
            "name": str(getattr(ws, "name", "") or ""),
            "status": str(getattr(ws, "status", "") or ""),
            "workshop_path": wp, "mind_path": mind_path, "agent_key": agent_key,
            "direction": {"brief": brief, "enum": enum},
            "manifest_ok": bool(manifest_ok),
            "manifest_errors": manifest_errors,
            # ⚠️ counts 是按**状态**分桶的（pending/acked/entered/active/blocked/lost），
            #    没有 members 这个键 —— 总数得用 members 列表长度（2026-10-29 自测抓到，别写错）
            "members": {"total": len(roster["members"]),
                        "active": roster["counts"].get("active", 0)},
            "library_path": str(getattr(ws, "library_path", "") or ""),
            "library_effective": _lib_dir(ws),
            "console_url": "/v5.html?ws=%s" % workshop_id,
            "notes": notes,
            "hint": "控制台拿这一份就能把路径/方向/心智根键填好；填不出来的都在 `notes` 里说明了为什么"}

def _bridge_fields_for(ws, *, harness_id: str = "", for_member: bool = False,
                       slot_dir: str = "", template: str = "pending_poll") -> dict:
    """算出"路径已填好"的桥模板参数（**一处真相**：`bridge-fill` 端点与组长对接单都用它）。"""
    from ... import platform_address as _pa
    from .. import bridge_factory as _bf2

    wp = str(getattr(ws, "workspace_dir", "") or "")
    target_dir = wp if not for_member else (str(Path(wp) / slot_dir) if slot_dir else "")
    base_url = _pa.base_url()
    meta = next((t for t in _bf2.list_templates() if t.get("name") == template), None)
    if meta is None:
        return {"ok": False, "code": "NO_SUCH_TEMPLATE", "template": template}
    need = meta.get("required_fields") or meta.get("required") or []
    fields = {}
    for k in need:
        if k == "HARNESS_ID":
            fields[k] = harness_id
        elif k == "PLATFORM_URL":
            fields[k] = base_url
        elif k in ("ACP_CWD", "WORK_DIR"):
            fields[k] = target_dir
        elif k == "INBOX_DIR":
            fields[k] = str(Path(target_dir) / "_inbox") if target_dir else ""
        elif k == "DESCRIPTION":
            fields[k] = "%s · %s" % (getattr(ws, "name", "") or getattr(ws, "workshop_id", ""),
                                     slot_dir or "组长")
        else:
            fields[k] = ""
    missing = [k for k, v in fields.items() if not v]
    return {"ok": True, "template": template, "fields": fields, "missing": missing,
            "target_dir": target_dir, "workshop_path": wp}


@router.get("/api/workshop/{workshop_id}/bridge-fill")
async def bridge_fill(workshop_id: str, for_: str = Query("lead", alias="for"),
                      member_seq: int = 0, template: str = ""):
    """**把路径自动填好的桥模板参数**（老板 2026-10-30 流程第 3 / 11 步）。

    `for=lead` ⇒ 填**工作间路径**（根B）；`for=member&member_seq=N` ⇒ 填**该成员的工位目录**。
    返回 `fields`（可直接复制的键值）+ `missing`（还没定的东西，说明为什么）+ `how`（粘到哪）。
    """
    from .. import bridge_factory as _bf
    from ... import platform_address as _pa

    ws = _ws(workshop_id)
    if ws is None:
        return _no_ws(workshop_id)
    wp = str(getattr(ws, "workspace_dir", "") or "")
    if not wp:
        return JSONResponse({"ok": False, "code": "NO_WORKSPACE_PATH",
                             "error": "这个工作间还没有工作区路径（根B）",
                             "next": "先在控制台填工作区路径"}, status_code=409)
    want_member = str(for_ or "lead").lower() in ("member", "members", "employee")
    slot_dir, harness_id = "", ""
    if want_member:
        if not member_seq:
            return JSONResponse({"ok": False, "code": "MEMBER_SEQ_REQUIRED",
                                 "error": "给成员填单要带 `member_seq`（照名单里的序号）",
                                 "next": "GET /api/workshop/{id}/roster 看 member_seq"}, 400)
        snap = _rw.roster(workshop_id)
        m = next((x for x in snap["members"] if int(x.get("member_seq") or 0) == int(member_seq)), None)
        if m is None:
            return JSONResponse({"ok": False, "code": "NO_SUCH_MEMBER",
                                 "error": "名单里没有 member_seq=%d" % member_seq,
                                 "next": "GET /api/workshop/{id}/roster 看现有序号"}, 404)
        slot_dir = str(m.get("slot_dir") or "")
        harness_id = str(m.get("harness_id") or "")
    else:
        lead = next((x for x in _rw.roster(workshop_id)["members"]
                     if str(x.get("slot_dir") or "") == "组长"), None)
        harness_id = str((lead or {}).get("harness_id") or "")

    # 目标目录：组长 = 工作间路径；成员 = 自己的工位目录
    target_dir = wp if not want_member else str((Path(wp) / slot_dir)) if slot_dir else ""
    base_url = _pa.base_url()

    tpl_name = template or ("file_poll" if not want_member else "file_poll")
    try:
        meta = next((t for t in _bf.list_templates() if t.get("name") == tpl_name), None)
    except Exception as e:                                   # noqa: BLE001 — 模板库坏了要说
        return JSONResponse({"ok": False, "code": "TEMPLATE_LIB_BROKEN",
                             "error": "桥模板库读不到：%s" % e,
                             "next": "检查 agent_community/bridge_templates/"}, 500)
    if meta is None:
        return JSONResponse({"ok": False, "code": "NO_SUCH_TEMPLATE",
                             "error": "没有这个桥模板：%s" % tpl_name,
                             "next": "GET /api/bridge/templates 看可用模板"}, 404)
    # ⚠️ `list_templates()` 的键叫 `required_fields`（不是 `required`）——
    #    写错不会报错，只会静默返回一张空参数表（这次就是这么发现的）
    need = meta.get("required_fields") or meta.get("required") or []
    fields = {}
    for k in need:
        if k == "HARNESS_ID":
            fields[k] = harness_id
        elif k == "PLATFORM_URL":
            fields[k] = base_url
        elif k in ("ACP_CWD", "WORK_DIR"):
            fields[k] = target_dir
        elif k == "INBOX_DIR":
            fields[k] = (str(Path(target_dir) / "_inbox") if target_dir else "")
        elif k == "MODEL_NAME":
            fields[k] = ""
        elif k == "PROVIDER":
            fields[k] = ""
        elif k == "DESCRIPTION":
            fields[k] = "%s · %s" % (getattr(ws, "name", "") or workshop_id,
                                     "组长" if not want_member else slot_dir)
        else:
            fields[k] = ""
    missing = [{"field": k, "why": ("名单里没有这个工位" if k == "HARNESS_ID" else
                                    "需要人填（模型名/供应商等平台不猜）")}
               for k, v in fields.items() if not v]
    return {"ok": True, "for": "member" if want_member else "lead", "template": tpl_name,
            "workshop_path": wp, "slot_dir": slot_dir, "target_dir": target_dir,
            "fields": fields, "missing": missing,
            "how": ("把 `fields` 里的值填进这套模板生成桥（`POST /api/bridge/generate`）；"
                    "路径不要手改 —— 改错了就是接不上"),
            "source": "V116 §8.2.1：目录字段一律由平台填"}

# ══════════════════════════════════════════════════════════════════════════
# ★2026-11-07（V134 ⑦，老板「组长最适配…可以用来带动不适配者」）
#
# **把组长对接单送到它手上** —— 按组长的能力面选通道，选不出来就如实说。
#
# 为什么要这条：`GET …/lead-kit` 只返回文本，用户得自己复制粘贴给组长；
# 而"组长自己就是不适配的那一端"（视觉型桌面 agent）时，这一步正是要靠平台代劳的。
#
# 通道优先级（一处真相在 `platform/lead_capabilities.py`）：
#   ① 组长的档案里有可用视觉脚本 ⇒ 真投（`visual_send.send_text`），投递≠它执行；
#   ② 组长能读文件（fs_write=true）⇒ 追加 `_lead/_notice.md` 一行 + 返回全文；
#   ③ 都不行 ⇒ **不假装发了**：返回正文 + 明确说"这类接入没有'收一段长文本'的语义"。
# ══════════════════════════════════════════════════════════════════════════
@router.post("/api/workshop/{workshop_id}/lead-kit/send")
async def lead_kit_send(workshop_id: str, request: Request):
    """把**组长对接单**送到组长那里（按能力面选通道；失败如实说）。

    body（可选）：`{"full": false}` —— 默认只投**开头摘要**（对接单可能几 KB，整段投进输入框不现实）；
    `full=true` 才投全文。无论哪种，完整正文都在返回的 `kit` 里。
    """
    import asyncio

    from .. import lead_capabilities as _caps
    from .. import visual_send as _vs

    ws = _ws(workshop_id)
    if ws is None:
        return _no_ws(workshop_id)
    try:
        body = await request.json()
    except Exception:                                          # noqa: BLE001 — 无 body 用默认
        body = {}
    body = body if isinstance(body, dict) else {}
    mem = _rw.effective_members(workshop_id)
    lead = next((m for m in (mem.get("members") or [])
                 if str((m or {}).get("slot_dir") or "").strip().strip("/") == "_lead"), None)
    if lead is None:
        return JSONResponse({"ok": False, "code": "NO_LEAD",
                             "error": "这个工作间还没选组长（名单里没有 `_lead` 工位）",
                             "next": "先 POST /api/workshop/{id}/lead 选一个"},
                            status_code=404)
    hid = str(lead.get("harness") or "")
    info = None
    try:
        from ..server import harness_manager  # type: ignore[attr-defined]

        sess = (getattr(harness_manager, "sessions", {}) or {}).get(hid) if hid else None
        info = getattr(sess, "info", None) if sess is not None else None
    except Exception:                                          # noqa: BLE001 — 取不到档案就按"能力未知"
        info = None
    caps = _caps.capabilities_of(info, harness_id=hid)

    kit_res = await lead_kit(workshop_id, format="json", issue_token=0)  # type: ignore[call-arg]
    if not isinstance(kit_res, dict):
        return kit_res                                         # 工作间没路径那类错误原样返回
    kit = str(kit_res.get("kit") or "")
    if not kit.strip():
        return JSONResponse({"ok": False, "code": "EMPTY_KIT",
                             "error": "对接单正文是空的（拼装失败）",
                             "next": "先 GET …/lead-kit?format=json 看看哪里不对"},
                            status_code=500)
    text = kit if bool(body.get("full")) else (
        "【生产合作社·组长对接单（摘要）】\n" + kit.strip()[:800]
        + "\n\n（完整对接单在平台 `GET /api/workshop/%s/lead-kit`，"
          "需要全文请让人复制，或重新请求时带 full=true）" % workshop_id)

    channels: list = []
    sent: dict = {}
    # ① 视觉投递（真投；投递≠它执行，口径与 V132 一致）
    if caps.get("can_be_visual"):
        try:
            res = await asyncio.to_thread(_vs.send_text, info, text)
            sent["visual"] = {k: res.get(k) for k in
                              ("ok", "delivered", "verified", "channel", "target", "detail", "error")}
            if res.get("delivered"):
                channels.append("visual")
        except Exception as e:                                 # noqa: BLE001 — 投递失败要说，不假装已发
            sent["visual"] = {"ok": False, "error": "%s: %s" % (type(e).__name__, str(e)[:160])}
    # ② 写进它的 `_notice.md`（它能读文件时这一条最稳，不抢焦点）
    if caps.get("can_receive_notice") == "file":
        try:
            from ..dispatch import append_notice

            r = append_notice(str(getattr(ws, "workspace_dir", "") or ""), "_lead",
                              "组长对接单已生成（%d 字）；完整正文见 `GET /api/workshop/%s/lead-kit`"
                              % (len(kit), workshop_id))
            sent["notice"] = {"ok": bool(r.get("ok")), "file": str(r.get("file") or "")}
            if r.get("ok"):
                channels.append("notice")
        except Exception as e:                                 # noqa: BLE001 — 写通知失败要说
            sent["notice"] = {"ok": False, "error": "%s: %s" % (type(e).__name__, str(e)[:160])}

    ok = bool(channels)
    out = {"ok": True, "workshop_id": workshop_id, "harness_id": hid,
           "sent": ok, "channels": channels, "attempts": sent,
           "capabilities": {k: caps.get(k) for k in
                            ("can_receive_notice", "can_be_visual", "can_write_files",
                             "can_read_files", "lead_eligible")},
           "kit_chars": len(kit), "text_sent_chars": len(text), "full": bool(body.get("full")),
           "kit": kit,
           "note": ("投递成功 ≠ 组长收到并照做：平台只能证明「话进了它的窗口 / 通知写进了它的目录」，"
                    "读不到它的回复、也不代表它已开始干活（`V132` §5.6.1 同一口径）。")}
    if not ok:
        out.update({"code": "NO_TEXT_CHANNEL",
                    "error": ("这位组长既没有可用的视觉脚本，也不读 `_notice.md`（或读写不了文件）"
                              "⇒ 平台**不会假装把它送达**"),
                    "next": ("给它做一份视觉脚本（对接中心「脚本激活」工作台），"
                             "或直接人工把 `kit` 复制给它")})
    return out
