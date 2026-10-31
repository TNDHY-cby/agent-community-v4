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
    """组长候选（**只列能当组长的**；按名字排序，不是推荐）。"""
    return _roster_mod.candidates(direction)


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
           "bridge_template": _bfill.get("template", ""),
           "bridge_fields": _bfill.get("fields", {}),
           "bridge_missing": _bfill.get("missing", []),
           "hint": "整段复制给组长；它照 Boot Protocol 先读两个根，再按「桥参数」生成桥"}
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
    if not tool["lead_eligible"]:
        return JSONResponse({"ok": False, "code": "NOT_LEAD_ELIGIBLE",
                             "error": "这个 harness 不能当组长",
                             "why": tool["tooling"].get("why")
                             or "缺 fs_write 或（protocol_call / bridge_poll）",
                             "next": "换一个能写文件的；或先让它报 `tools` 再探测一次"},
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
