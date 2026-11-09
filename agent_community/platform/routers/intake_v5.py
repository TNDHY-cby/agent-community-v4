# -*- coding: utf-8 -*-
"""`routers/intake_v5`：**收外端回执**（设计 `V110`/`V111`，P1 批）。

四种东西：

| 端点 | 干什么 |
|---|---|
| `POST /api/workshop/{id}/intake?source=A` | A 档：`Authorization: Bearer <session_token>`，收原文 |
| `POST /api/workshop/{id}/intake?source=C&confirm=1` | C 档：用户粘贴（**必须 `confirm=1`**，记 L4） |
| `POST /api/workshop/{id}/intake/poll` | B 档：扫 `<workshop_path>/_meta/_inbox/*.rcpt` |
| `GET  /api/workshop/{id}/receipts?limit=` | 回执流水（含被拒的原文） |
| `POST /api/workshop/{id}/members/{member}/token` | 签 A 档 token（只在拼对接单时签） |
| `POST /api/workshop/{id}/members/{member}/revoke` | 吊销（成员 lost 时必做） |

⚠️ **P1 的边界（如实说）**：这一批**只解析 + 只留痕 + 只做幂等**，
**不写 slot/member**（那要等 P2 的双层成员模型）。所以响应里 `applied=false`、
`stage="P1 只记账"`，并给出 `would_apply`（落库时会写成什么），**绝不假装已经落库**。
"""
from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, Header, Request
from fastapi.responses import JSONResponse

from .. import intake_token as _tok
from .. import receipt as _rc
from .. import receipt_store as _store
from .. import task_ledger as _ledger

router = APIRouter()


def _no_ws(workshop_id: str):
    return JSONResponse({"ok": False, "code": "NO_SUCH_WORKSHOP",
                         "error": "没有这个工作间：%s" % workshop_id,
                         "next": "先 GET /api/workshops 看有哪些；或先建一个"},
                        status_code=404)


def _ws(workshop_id: str):
    from ..server import workshops

    return workshops.get(workshop_id)


def _members_of(ws) -> list:
    out = []
    for m in getattr(ws, "members", []) or []:
        for key in ("member_id", "display_name", "role"):
            v = str(getattr(m, key, "") or "").strip()
            if v:
                out.append(v)
    return out


def _canonical_member(workshop_id: str, member: str) -> str:
    """把「成员」统一成**工位名**（`slot_dir`）：token 绑定与回执校验必须同一口径。

    2026-10-28 判据抓到：`/members/2/token` 把 `"2"` 直接绑进 token，而回执头写的是
    `后端组/员工02` ⇒ A 档永远 403 `TOKEN_SCOPE`（平台自相矛盾）。现在数字按 `member_seq`
    解析成 `slot_dir`，非数字原样（组长就是 `组长`）。
    """
    m = str(member or "").strip()
    if not m.isdigit():
        return m
    try:
        from .. import v5_roster as _roster

        for row in _roster.roster(workshop_id)["members"]:
            if int(row.get("member_seq") or 0) == int(m):
                return str(row.get("slot_dir") or m)
    except Exception as e:                                      # noqa: BLE001 — 解析不了就用原值并说清
        print("[intake] 成员号解析失败（%s: %s）⇒ 按原值绑定" % (type(e).__name__, e), flush=True)
    return m


def _member_ok(ws, member: str) -> tuple:
    """成员校验：空成员名单时接受（接组长前先回名单是常态）但标 `member_unverified`。"""
    known = _members_of(ws)
    if not known:
        return True, {"member_unverified": True,
                      "why": "这个工作间还没有成员，所以无法核对「成员」这一行"}
    if member in known:
        return True, {}
    return False, {"known": sorted(set(known))[:20]}


def _roster_mirror(workshop_id: str) -> dict:
    """名单变更后**顺手刷新工作区镜像**（失败不阻断，但在响应里如实带出来）。"""
    ws = _ws(workshop_id)
    if ws is None:
        return {"ok": False, "why": "工作间不存在"}
    from .. import v5_roster as _roster_mod

    return _roster_mod.write_mirror(workshop_id, str(getattr(ws, "workspace_dir", "") or ""))


def _apply(workshop_id: str, rtype: str, rows: list, source: str) -> dict:
    """名单/状态回执**真正落库**的唯一收口：落库后顺手刷新工作区镜像。

    镜像失败**不阻断**（名单主库已经写成），但会在 `mirror` 里如实说明为什么。
    """
    out = _apply_raw(workshop_id, rtype, rows, source)
    if isinstance(out, dict):
        out.setdefault("mirror", _roster_mirror(workshop_id))
    return out


def _apply_raw(workshop_id: str, rtype: str, rows: list, source: str) -> dict:
    """把回执**真落库**（P2：名单→slots/members，状态→member 迁移）。

    派活与验收这两类**仍是索引**（正文只在文件里；任务状态要等 P3/P4），如实标 `applied=False`。
    """
    from .. import v5_roster as _roster

    if rtype == "roster":
        res = _roster.add_slots(workshop_id, rows, source=source)
        return {"kind": "slots", "stage": "P2 落库", "applied": True,
                "result": {k: res[k] for k in ("slots_created", "slots_updated", "members_created")},
                "roster_counts": res["roster"]["counts"],
                "next": "成员现在有工位了（pending）；接上要靠证据 + verified_scope 才会到 active"}
    if rtype == "state":
        res = _roster.apply_state_rows(workshop_id, rows, source=source)
        return {"kind": "member_state", "stage": "P2 落库",
                "applied": bool(res["applied"]), "result": res,
                "next": "" if res["applied"] else "这些行没落：看 result.rejected 里的原因"}
    if rtype == "dispatch":
        # ★P3：派活回执走**与 /dispatch 完全相同的那条核心**（一处真相）。
        #   正文不进球：文件不在就拒（`TASK_FILE_MISSING`），平台**不代写**。
        from .dispatch_v5 import do_dispatch

        done, failed = [], []
        members = _roster.roster(workshop_id)["members"]
        for r in rows:
            m = next((x for x in members if str(x.get("slot_dir")) == str(r.get("派给"))), None)
            if m is None:
                failed.append({"row": r, "why": "这个岗位没有工位（派给谁了？）"})
                continue
            out = do_dispatch(workshop_id,
                              {"member_seq": m["member_seq"], "title": r.get("标题"),
                               "goal": r.get("标题"), "due": r.get("期限"),
                               "task_id": r.get("任务"), "write_file": "0"},
                              issued_by="lead", source="receipt")
            (done if out.get("ok") else failed).append(out)
        return {"kind": "dispatch", "stage": "P3 落库（写文件+notice+manifest+激活）",
                "applied": bool(done), "result": {"done": done, "failed": failed},
                "next": "" if not failed else "失败的看 result.failed：多半是正文文件没先写好"}
    # ★P4：验收回执**真改任务状态**（`task_ledger.review`），并带证据等级落闸
    from .p4_v5 import review as _review

    done, failed = [], []
    for r in rows:
        tid = str(r.get("验收") or "")
        try:
            seq_n = int(tid.split("-", 1)[1])
        except (IndexError, ValueError):
            failed.append({"row": r, "why": "任务号看不懂（要形如 T-003）"})
            continue
        lvl = str(r.get("证据") or "")
        out = _ledger.review(workshop_id, seq_n, result=str(r.get("结果") or ""),
                             evidence_level=lvl, note=str(r.get("备注") or ""),
                             who="lead")
        (done if out.get("ok") else failed).append(out)
    return {"kind": "accept", "stage": "P4 验收（真改任务状态 + 证据等级落闸）",
            "applied": bool(done), "result": {"done": done, "failed": failed},
            "next": "" if not failed else "失败的看 result.failed（多为证据等级不够或任务号不存在）"}


def _handle_text(workshop_id: str, text: str, source: str, expect_member: str = "") -> JSONResponse:
    """三种投递进来后的**同一条**处理路径（解析 → 幂等 → 成员校验 → 留痕 → preview）。"""
    ws = _ws(workshop_id)
    if ws is None:
        return _no_ws(workshop_id)
    parsed = _rc.parse(text)
    raw_hash = parsed["raw_hash"]

    if not parsed["ok"]:
        first = parsed["errors"][0]
        raw_rel = _store.save_raw(workshop_id, text, raw_hash)
        _store.record(workshop_id, kind="rejected", source=source,
                      member=expect_member or "-", seq=None, raw_hash=raw_hash, raw_rel=raw_rel,
                      ok=False, errors=parsed["errors"], note="解析失败（整单拒）")
        return JSONResponse({"ok": False, "code": "RECEIPT_ATOMIC_FAIL",
                             "error": "回执没解析通过（整单拒，一条都没写）",
                             "detail": {"first": first, "errors": parsed["errors"],
                                        "raw_saved": raw_rel},
                             "next": first["fix"]}, status_code=422)

    rc = parsed["receipt"]
    if expect_member and rc["member"] != expect_member:
        return JSONResponse({"ok": False, "code": "TOKEN_SCOPE",
                             "error": "token 是 %s 的，报文写的是 %s" % (expect_member, rc["member"]),
                             "next": "用自己那份对接单里的 token，或让组长重新拼一份"},
                            status_code=403)
    ok_member, extra = _member_ok(ws, rc["member"])
    if not ok_member:
        raw_rel = _store.save_raw(workshop_id, text, raw_hash, rc["member"])
        _store.record(workshop_id, kind="rejected", source=source, member=rc["member"],
                      seq=rc["seq"], raw_hash=raw_hash, raw_rel=raw_rel, ok=False,
                      rtype=rc["type"], note="成员不属于本工作间")
        return JSONResponse({"ok": False, "code": "RECEIPT_R007",
                             "error": "「成员」不属于这个工作间：%s" % rc["member"],
                             "detail": {"known_members": extra.get("known", [])},
                             "next": "照对接单上的 `岗位` 原样写（区分大小写与斜杠）"},
                            status_code=400)

    verdict, detail = _store.check_seq(workshop_id, rc["member"], rc["seq"], raw_hash)
    raw_rel = _store.save_raw(workshop_id, text, raw_hash, rc["member"])
    if verdict == "duplicate":
        _store.record(workshop_id, kind="duplicate", source=source, member=rc["member"],
                      seq=rc["seq"], raw_hash=raw_hash, raw_rel=raw_rel, ok=True,
                      rtype=rc["type"], rows=len(rc["rows"]), note="幂等重复")
        return JSONResponse({"ok": True, "duplicate": True, "type": rc["type"],
                             "seq": rc["seq"], "raw_saved": raw_rel,
                             "message": "这份回执已经收过了，没有重复记账"})
    if verdict in ("R006", "R010"):
        _store.record(workshop_id, kind="rejected", source=source, member=rc["member"],
                      seq=rc["seq"], raw_hash=raw_hash, raw_rel=raw_rel, ok=False,
                      rtype=rc["type"], note=verdict)
        return JSONResponse({"ok": False, "code": "RECEIPT_" + verdict,
                             "error": detail.get("message", "序号不对"),
                             "detail": detail, "next": detail.get("fix", "")}, status_code=409)

    applied = _apply(workshop_id, rc["type"], rc["rows"], source)
    entry = _store.record(workshop_id, kind="ok", source=source, member=rc["member"],
                          seq=rc["seq"], raw_hash=raw_hash, raw_rel=raw_rel, ok=True,
                          rtype=rc["type"], rows=len(rc["rows"]),
                          note=extra.get("why", "") if extra else "")
    out = {"ok": True, "type": rc["type"], "type_cn": rc["type_cn"], "seq": rc["seq"],
           "member": rc["member"], "rows": rc["rows"], "row_count": len(rc["rows"]),
           "raw_saved": raw_rel, "receipt_entry": entry,
           "applied": bool(applied.get("applied")), "stage": applied["stage"],
           "result": applied.get("result", {}),
           "preview": {k: v for k, v in applied.items() if k in ("would_apply", "note", "next",
                                                                 "roster_counts")},
           "scope": "接受＝解析+留痕+幂等；`applied=true` 才表示真落了库（名单→slots、状态→member）"} 
    if applied.get("next"):
        out["next"] = applied["next"]
    if extra:
        out.update(extra)
    # 名单/状态真落库后刷新工作区镜像（失败不阻断，如实带出来）
    out["mirror"] = applied.get("mirror") or _roster_mirror(workshop_id)
    return JSONResponse(out)

@router.post("/api/workshop/{workshop_id}/intake")
async def intake(workshop_id: str, request: Request, source: str = "C", confirm: int = 0,
                 authorization: str = Header(default="")):
    """收一份回执。`source=A`（带 token）/ `source=C`（用户粘贴，必须 `confirm=1`）。"""
    src = (source or "C").strip().upper()
    if src not in ("A", "C"):
        return JSONResponse({"ok": False, "code": "BAD_SOURCE",
                             "error": "source 只能是 A 或 C（B 档请用 /intake/poll）",
                             "next": "A 档：Authorization: Bearer <token>；C 档：?source=C&confirm=1"},
                            status_code=400)
    try:
        body = await request.json()
    except Exception:                                        # noqa: BLE001 — 也可能是纯文本 body
        raw = await request.body()
        body = {"text": raw.decode("utf-8", "replace")}
    text = body.get("text") if isinstance(body, dict) else ""
    if not isinstance(text, str) or not text.strip():
        return JSONResponse({"ok": False, "code": "EMPTY",
                             "error": "没有回执正文（body 里给 `text`）",
                             "next": "把外端那一整段回执原样放进 `text`"}, status_code=400)

    expect_member = ""
    if src == "A":
        bearer = authorization[7:].strip() if authorization.lower().startswith("bearer ") else ""
        got, why = _tok.verify(bearer)
        if got is None:
            code_msg = {
                "TOKEN_MISSING": ("缺少 Authorization: Bearer <session_token>",
                                  "用对接单里那份 token（每条对接单一个）"),
                "TOKEN_BAD": ("token 无效（签名对不上）",
                              "别改 token 里的字符；要重发就让组长重新拼一份对接单"),
                "TOKEN_REVOKED": ("这个 token 已被吊销",
                                  "让组长重新拼一份对接单（旧通道已停用）"),
            }
            msg, fix = code_msg.get(why, ("token 不可用", "重新取 token"))
            return JSONResponse({"ok": False, "code": why, "error": msg, "next": fix},
                                status_code=401 if why != "TOKEN_BAD" else 403)
        t_ws, t_member = got
        if t_ws != workshop_id:
            return JSONResponse({"ok": False, "code": "TOKEN_SCOPE",
                                 "error": "token 只能写工作间 %s，不能写 %s" % (t_ws, workshop_id),
                                 "next": "用本工作间的对接单"}, status_code=403)
        expect_member = t_member
    else:
        if not int(confirm or 0):
            return JSONResponse({"ok": False, "code": "NEED_HUMAN_CONFIRM",
                                 "error": "C 档（人工粘贴）必须由人确认后才记账",
                                 "next": "前端打勾后带 `?source=C&confirm=1` 重发；"
                                         "确认会记成 L4（谁在什么时候确认的）"},
                                status_code=403)

    resp = _handle_text(workshop_id, text, src, expect_member)
    if src == "C":
        try:
            _log_human_confirm(workshop_id, text)
        except Exception as e:                                # noqa: BLE001 — 记不下来要说（L4 证据不能丢）
            print("[intake] C 档人工确认没记下来（%s: %s）" % (type(e).__name__, e), flush=True)
    return resp


def _log_human_confirm(workshop_id: str, text: str) -> None:
    from ..env_spec import data_dir as _data_dir

    d = Path(_data_dir()) / "receipts" / workshop_id.replace("/", "_") / "human"
    d.mkdir(parents=True, exist_ok=True)
    import time

    p = d / ("confirm-%d.json" % int(time.time() * 1000))
    p.write_text(json.dumps({"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "verified_by": "human",
                             "raw_hash": _rc.sha256_text(text), "chars": len(text)},
                            ensure_ascii=False), encoding="utf-8")


@router.post("/api/workshop/{workshop_id}/intake/poll")
async def intake_poll(workshop_id: str, limit: int = 20):
    """B 档：扫 `<workshop_path>/_meta/_inbox/*.rcpt` —— **位置即凭据**。

    处理完：成功移进 `_done/`（并留 `.result.json`），失败留原地并写 `_fail/<name>.err`。
    """
    ws = _ws(workshop_id)
    if ws is None:
        return _no_ws(workshop_id)
    base = Path(getattr(ws, "workspace_dir", "") or "")
    inbox = base / "_meta" / "_inbox"
    if not base.is_dir():
        return JSONResponse({"ok": False, "code": "NO_WORKSHOP_PATH",
                             "error": "这个工作间的目录不存在：%s" % base,
                             "next": "先在工作间里设定 `workshop_path`（P5 的入口会做）"},
                            status_code=400)
    if not inbox.is_dir():
        return {"ok": True, "found": 0, "processed": [], "detail": "还没有 %s 目录" % inbox.as_posix()}
    files = sorted(inbox.glob("*.rcpt"))[: max(1, int(limit or 20))]
    if not files:
        return {"ok": True, "found": 0, "processed": [], "detail": "收件夹是空的"}
    processed = []
    done = inbox / "_done"
    fail = inbox / "_fail"
    for f in files:
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError as e:                                  # noqa: BLE001 — 读不到要说清是哪个文件
            processed.append({"file": f.name, "ok": False, "code": "READ_FAIL",
                              "error": "%s: %s" % (type(e).__name__, e)})
            continue
        resp = _handle_text(workshop_id, text, "B")
        try:
            payload = json.loads(resp.body.decode("utf-8"))
        except ValueError:                                    # 响应不可解析（不该发生）
            payload = {"ok": False, "code": "INTERNAL", "error": "响应不可解析"}
        processed.append({"file": f.name, "ok": bool(payload.get("ok")),
                          "code": payload.get("code", ""), "body": payload})
        try:
            if payload.get("ok"):
                done.mkdir(parents=True, exist_ok=True)
                (done / (f.name + ".result.json")).write_text(
                    json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
                f.replace(done / f.name)
            else:
                fail.mkdir(parents=True, exist_ok=True)
                (fail / (f.name + ".err")).write_text(
                    json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
        except OSError as e:                                  # noqa: BLE001 — 挪不动要说
            print("[intake] B 档文件挪不动（%s: %s）" % (type(e).__name__, e), flush=True)
    return {"ok": True, "found": len(files),
            "processed": processed, "counts": _store.counts(workshop_id)}


@router.get("/api/workshop/{workshop_id}/receipts")
async def receipts(workshop_id: str, limit: int = 20):
    """回执流水（含**被拒的**原文路径与错误），最近在前。"""
    ws = _ws(workshop_id)
    if ws is None:
        return _no_ws(workshop_id)
    items = _store.recent(workshop_id, limit)
    return {"ok": True, "count": len(items), "counts": _store.counts(workshop_id),
            "items": items,
            "hint": "被拒的回执也在这里（`kind=rejected`+`errors`），原文在 `AC_DATA_DIR/receipts/…`"}


@router.post("/api/workshop/{workshop_id}/members/{member}/token")
async def issue_token(workshop_id: str, member: str):
    """签一个 A 档 token（**拼对接单时现签**；不写模板、不落明文）。"""
    ws = _ws(workshop_id)
    if ws is None:
        return _no_ws(workshop_id)
    member = _canonical_member(workshop_id, member)
    ok_member, extra = _member_ok(ws, member)
    if not ok_member:
        return JSONResponse({"ok": False, "code": "NO_SUCH_MEMBER",
                             "error": "这个工作间没有成员 %s" % member,
                             "detail": extra, "next": "先 GET …/members 看有哪些"}, status_code=404)
    return _tok.issue(workshop_id, member)


@router.post("/api/workshop/{workshop_id}/members/{member}/revoke")
async def revoke_token(workshop_id: str, member: str):
    """吊销 A 档 token（成员 `lost`/换通道时必做）。**立刻生效**。"""
    return _tok.revoke(workshop_id, _canonical_member(workshop_id, member))

@router.get("/api/workshop/{workshop_id}/roster")
async def get_roster(workshop_id: str):
    """**slot / member 双层名单**（V108 §6）。只有 member.* 参与闸门。

    另带 **`roster_summary`**（`V116` §8.0 口径一）：`名单 X 人 / 已接上 Y / 还差 Z（哪几个空工位）`
    的一句话 —— 与 `GET /api/workshop/{id}` 用的是**同一个函数**（规则 20：一处真相）。
    """
    from .. import v5_roster as _roster

    ws = _ws(workshop_id)
    if ws is None:
        return _no_ws(workshop_id)
    out = _roster.roster(workshop_id)
    out["ok"] = True
    # ★A1（V139）：每个成员补 `reports_to` / `level` / `squad_id`（**只加字段，不改既有字段**）。
    #   `AC_ORG_CHART=0` 时不动（回滚开关）。
    try:
        from .. import org_chart as _org
        from .. import squads as _sqs

        if _org.enabled():
            _sq_list = []
            try:
                _sq_list = list((getattr(ws, "squads", None) or []))
            except Exception:                                  # noqa: BLE001 — 分队读不出来就不带 squad 字段
                _sq_list = []
            _org.annotate_roster(out, _sq_list, workshop_id=workshop_id)
    except Exception as e:                                     # noqa: BLE001 — 组织关系是**加法**，出问题不能把名单也弄挂
        out["org_chart_error"] = "%s: %s" % (type(e).__name__, e)
    out["roster_summary"] = _roster.roster_summary(workshop_id)
    out["hint"] = ("slot.proposed 不参与闸门；member 要 active 才算接上。"
                   "证据攒不齐就停在 acked，并会告诉你还缺哪项")
    return out


@router.post("/api/workshop/{workshop_id}/members/{member_seq}/evidence")
async def set_member_evidence(workshop_id: str, member_seq: int, request: Request):
    """记通道证据并**当场判状态**（V112 §3 阶梯）。

    body：`{protocol_ok|bridge_ok|mailbox_ok|paste_seen|press_ok|rename_seen, verified_scope, tier, who}`
    ＋**可选** `actual_name`（「它现在实际叫什么」，`V116` §8.0 口径二）：
    填了就与平台给的身份名比，不一致在响应 `name_check_warn` 里提醒，**但不拦**（改名很常见）。
    """
    from .. import v5_roster as _roster

    ws = _ws(workshop_id)
    if ws is None:
        return _no_ws(workshop_id)
    try:
        body = await request.json()
    except Exception:                                          # noqa: BLE001 — 没 body 就按空
        body = {}
    body = body if isinstance(body, dict) else {}
    out = _roster.set_evidence(workshop_id, member_seq, body,
                              who=str(body.get("who") or "user"),
                              tier=str(body.get("tier") or ""))
    # 名单变了 ⇒ 刷新工作区镜像（失败不阻断，但如实带出来）
    out["mirror"] = _roster_mirror(workshop_id)
    return JSONResponse(out, status_code=200 if out.get("ok") else 404)


@router.post("/api/workshop/{workshop_id}/members/{member_seq}/bridge-probe")
async def bridge_probe(workshop_id: str, member_seq: int, request: Request):
    """**B 档平台自验**（`V116` §6.2）：平台自己往邮箱写一条测试消息，之后**亲眼看**它被取走没有。

    为什么要有它：`bridge_ok` / `mailbox_ok` 原来只能由调用方申报（账上 `user-check`，与人打勾同类），
    而「桥真取件一次」这件事**平台本来就看得见**——文件从 `inbox/` 进了 `inbox/delivered/`。
    做法复用 `bridge_selfcheck`（不发明新协议、不写死路径）：
      ① 写 `task_<probe_id>.json` 进邮箱（平台做的）；
      ② 看 `delivered/` / `replies/` 里有没有这个 `probe_id`（平台看的）；
      ③ 取件成立 ⇒ 记 `bridge_ok`，真回话 ⇒ 再加 `mailbox_ok`，来源一律 `platform-observed`。

    body：`{inbox_dir?: str, wait?: number}` —— `inbox_dir` 不给就按工位名推导（并标 `inbox_guessed`）；
    `wait` 是写完等几秒再看（0 = 立刻看，等价于"先点了再回来看"）。
    """
    from .. import bridge_selfcheck as _bs
    from .. import v5_roster as _roster
    from ..compartment import mailbox_dir

    ws = _ws(workshop_id)
    if ws is None:
        return _no_ws(workshop_id)
    try:
        body = await request.json()
    except Exception:                                          # noqa: BLE001 — 没 body 就按空
        body = {}
    body = body if isinstance(body, dict) else {}

    m = _roster.find_member(workshop_id, member_seq)
    if m is None:
        return JSONResponse({"ok": False, "code": "NO_SUCH_MEMBER",
                             "error": "没有这个工位：%s" % member_seq,
                             "next": "先 GET /api/workshop/{id}/roster 看有哪些 member_seq"},
                            status_code=404)

    wp = str(getattr(ws, "workspace_dir", "") or "")
    if not wp:
        return JSONResponse({"ok": False, "code": "NO_WORKSHOP_PATH", "error": "这个工作间没有工作区目录",
                             "next": "先设 workshop_path"}, status_code=400)

    guessed = False
    raw_inbox = str(body.get("inbox_dir") or "").strip()
    if raw_inbox:
        inbox = Path(raw_inbox)
    else:
        # 不写死路径：按工位名推导（同岗位多人时会共用同一个邮箱 —— 这是 v5 名单 slot 的真实边界）
        slot = str(m.get("slot_dir") or "")
        role, _, mem = slot.partition("/")
        inbox = mailbox_dir(wp, role or slot or "成员", mem or ("成员%02d" % int(member_seq)), "inbox")
        guessed = True
    if not inbox.is_dir():
        return JSONResponse({"ok": False, "code": "INBOX_MISSING",
                             "error": "这个工位还没有邮箱目录：%s" % inbox,
                             "inbox": str(inbox), "inbox_guessed": guessed,
                             "next": ("先给它架桥（平台会按隔间建好 inbox），"
                                      "或在 body 里显式给 `inbox_dir`")}, status_code=400)

    probe = None
    pid = str(body.get("probe_id") or "").strip()
    if pid:
        # 「再看一眼同一条」：桥取件发生在两次调用**之间**，每次都新发就永远看不到取走
        probe = {"ok": True, "probe_id": pid, "reused": True}
    else:
        probe = _bs.send_probe(str(inbox), harness_id=str(m.get("harness") or ""),
                               workshop_id=workshop_id, member_id=str(member_seq),
                               role=str(m.get("slot_dir") or ""))
    if not probe.get("ok"):
        return JSONResponse({"ok": False, "code": "PROBE_SEND_FAILED",
                             "error": probe.get("error") or "写不进这个邮箱",
                             "inbox": str(inbox), "inbox_guessed": guessed,
                             "next": "看这个目录的权限，或先确认它真的属于这个工位"},
                            status_code=400)

    try:
        wait = float(body.get("wait") or 0)
    except (TypeError, ValueError):
        wait = 0.0
    if wait > 0:
        # ⚠️ 必须 `await asyncio.sleep`：这是 async 端点，`time.sleep` 会把**整个事件循环**卡住
        #    （最长 30 秒）。`tests/test_async_no_blocking.py` 会直接判红，别图省事。
        import asyncio as _aio
        await _aio.sleep(min(wait, 30.0))

    st = _bs.inspect_probe(str(inbox), probe["probe_id"])
    v = _bs.verdict(st, inbox_exists=True)
    out = _roster.record_bridge_probe(
        workshop_id, member_seq, probe_id=probe["probe_id"],
        picked_up=bool(st.get("picked_up")), answered=bool(st.get("answered")),
        inbox_rel=str(inbox))
    out["probe"] = {"id": probe["probe_id"], "inbox": str(inbox), "inbox_guessed": guessed,
                    "reused": bool(probe.get("reused")),
                    "picked_up": bool(st.get("picked_up")), "answered": bool(st.get("answered")),
                    "still_in_inbox": st.get("still_in_inbox") or [],
                    "delivered": st.get("delivered") or [],
                    "reply_in_replies": st.get("reply_in_replies") or [],
                    "verdict": v.get("state"), "reason": v.get("reason"), "advice": v.get("advice")}
    if not out.get("ok") and out.get("code") == "PROBE_NOT_PICKED":
        # 失败时也要把 probe_id 给出去，用户才能"等一会儿再看同一条"
        out["next"] = ("等桥把它取走（文件会从邮箱进 `delivered/`），再带 `probe_id` 点一次；"
                       "%s" % (v.get("advice") or ""))
        out["probe_id"] = probe["probe_id"]
    if guessed:
        out["inbox_guessed_note"] = ("邮箱路径是按工位名推的；同一岗位有多人时会共用同一个邮箱，"
                                     "要精确到人就显式给 `inbox_dir`")
    return JSONResponse(out, status_code=200 if out.get("ok") else 409)


@router.post("/api/workshop/{workshop_id}/members/{member_seq}/protocol-probe")
async def protocol_probe(workshop_id: str, member_seq: int, request: Request):
    """**A 档「平台自验」**：平台自己按协议发一条，自己看它有没有回执。

    ★2026-11-01（老板：「组长接进工作间很麻烦」）：
    B 档早有 `bridge-probe`（平台自己写邮箱、自己看取件），而 **A 档反而只能人工打勾 + 写覆盖范围** ——
    最该自动验的一档却是最麻烦的一档。这里补上：**平台发出 + 平台看到回执 ⇒ 自己记 `protocol_ok`**，
    来源写 `platform-observed` 并盖时间戳。

    ⚠️ 铁律：`platform-observed` **只在本端点内部记**。前端传 `who=platform` 不算数 ——
    否则"打勾"换个马甲就能冒充"平台观测"，账就是假的。

    body：`{wait?: 秒（默认 20，最多 60）, text?: 自定义探针话}`
    返回：`{ok, verified, protocol, task_id, state, detail, evidence}`
    """
    import asyncio

    from .. import dispatch as _dp

    ws = _ws(workshop_id)
    if ws is None:
        return _no_ws(workshop_id)
    try:
        body = await request.json()
    except Exception:                                          # noqa: BLE001 — 没 body 按空
        body = {}
    body = body if isinstance(body, dict) else {}

    from .. import v5_roster as _roster

    m = _roster.find_member(workshop_id, int(member_seq))
    if m is None:
        return JSONResponse({"ok": False, "code": "NO_SUCH_MEMBER",
                             "error": "没有这个工位：%s" % member_seq,
                             "next": "GET …/roster 看有哪些 member_seq"}, status_code=404)
    hid = str(m.get("harness_id") or "").strip()
    if not hid:
        return JSONResponse({"ok": False, "code": "NO_HARNESS",
                             "error": "这个工位没绑 harness，平台不知道该跟谁说话",
                             "next": "先在名单里给它绑一个"}, status_code=409)

    # 只有 A2A 支持"平台自验到回执"这一步；别的协议如实拒绝，别假装验过
    try:
        is_a2a = bool(_dp._harness_is_a2a(hid))
    except Exception:                                          # noqa: BLE001
        is_a2a = False
    if not is_a2a:
        return JSONResponse({
            "ok": False, "code": "PROBE_NOT_SUPPORTED",
            "error": "这类接入暂时不能「平台自验」（只有 A2A 能自己发、自己看回执）",
            "next": ("如果你的 agent 支持 A2A，去「接入 Agent」按 A2A 接一次，这里就能一键验；"
                     "否则照对接单人工确认（打勾 + 写覆盖范围）。"),
        }, status_code=409)

    url = ""
    try:
        from ..harness_adapter import harness_manager as _hm
        _sess = _hm.sessions.get(hid)
        url = str(getattr(getattr(_sess, "info", None), "api_base_url", "") or "")
    except Exception:                                          # noqa: BLE001 — 取不到地址就如实失败
        url = ""
    if not url:
        return JSONResponse({"ok": False, "code": "NO_ENDPOINT",
                             "error": "这个 A2A agent 没有 api_base_url（平台无处可发）",
                             "next": "去「接入 Agent」补上端点基址"}, status_code=409)

    wait = min(60, max(3, int(body.get("wait") or 20)))
    text = str(body.get("text") or "").strip() or (
        "平台自验探针：这是本工作间「%s」的组长接入检查，收到请随便回一句，平台看回执就够。"
        % str(getattr(ws, "name", "") or workshop_id))

    detail, task_id, state, verified = "", "", "", False
    try:
        from ...a2a_client import get_task as _get, send_task as _send

        res = await asyncio.to_thread(_send, url, text, None, "", 60)
        _t = res.get("task") if isinstance(res.get("task"), dict) else (res or {})
        task_id = str(_t.get("id") or "").strip()
        # 等它把任务做完（终态才算真回执；submitted/working 不算）
        import time as _time
        _deadline = _time.time() + wait
        while task_id and _time.time() < _deadline:
            await asyncio.sleep(2)
            r2 = await asyncio.to_thread(_get, url, task_id, None, 20)
            _t2 = r2.get("task") if isinstance(r2.get("task"), dict) else (r2 or {})
            _st = _t2.get("status")
            state = str(_st.get("state") if isinstance(_st, dict) else _st or "").lower()
            if state.startswith("task_state_"):
                state = state[len("task_state_"):]
            if state in ("completed", "failed", "canceled", "cancelled", "rejected", "error"):
                break
        verified = state in ("completed",)
        detail = ("平台按 tasks/send 发出%s，回查状态 = %s"
                  % (("（任务 %s）" % task_id) if task_id else "（没拿到任务号）", state or "（还没回）"))
    except Exception as e:                                     # noqa: BLE001 — 发不出去要如实说
        return JSONResponse({"ok": False, "code": "PROBE_FAILED",
                             "error": "平台自验失败：%s: %s" % (type(e).__name__, str(e)[:200]),
                             "next": ("确认这个 agent 的端点还在跑。如果你的客户端**刚重启过**，它的端口可能变了（端口是它自己的事，平台不该假设）——去「接入 Agent」重新确认这个 agent 的地址，或直接打开它的面板看现在监听哪个端口。")},
                            status_code=409)

    if not verified:
        return JSONResponse({
            "ok": False, "code": "PROBE_NOT_CONFIRMED", "task_id": task_id,
            "state": state, "detail": detail,
            "error": "平台发出去了，但**没看到它把任务做完**（状态：%s）" % (state or "无回执"),
            "next": ("端点活着但没干完：可能它在忙、或它不接这个任务；"
                     "再验一次，或照对接单人工确认。"),
        }, status_code=409)

    # 只有走到这里才算"平台自己看到的" —— 由端点内部记，前端传什么都没用
    ev = _roster.set_evidence(
        workshop_id, int(member_seq),
        {"protocol_ok": True,
         "verified_scope": "平台自己发出任务并收到完成回执（%s）" % (task_id or "有回执")},
        who="platform", tier=str(body.get("tier") or "a_protocol"),
        evidence_source="platform-observed")
    warn = None
    try:
        warn = _roster_mirror(workshop_id)
    except Exception as e:                                     # noqa: BLE001 — 镜像失败不影响判定
        warn = {"ok": False, "why": "%s: %s" % (type(e).__name__, str(e)[:120])}
    return JSONResponse({"ok": True, "verified": True, "protocol": "a2a",
                         "task_id": task_id, "state": state, "detail": detail,
                         "evidence": ev, "mirror": warn,
                         "note": "这条是**平台自己观测**的（platform-observed），不是人打的勾。"},
                        status_code=200)


@router.post("/api/workshop/{workshop_id}/roster/migrate")
async def migrate_roster(workshop_id: str):
    """把旧单层 member.status 映射成双层（**只映射，映不出来的列在 legacy_unknown**）。"""
    from .. import v5_roster as _roster

    ws = _ws(workshop_id)
    if ws is None:
        return _no_ws(workshop_id)
    legacy = []
    for m in getattr(ws, "members", []) or []:
        legacy.append({"member_id": getattr(m, "member_id", ""),
                       "display_name": getattr(m, "display_name", ""),
                       "status": getattr(m, "status", ""),
                       "harness_ids": list(getattr(m, "harness_ids", []) or []),
                       "session_aliases": list(getattr(m, "session_aliases", []) or [])})
    out = _roster.migrate_legacy(workshop_id, legacy)
    out["mirror"] = _roster_mirror(workshop_id)
    return out


@router.get("/api/workshop/{workshop_id}/gate/{action}")
async def gate_probe(workshop_id: str, action: str, member_seq: int = 0):
    """**闸门自省**：现在做这个动作会不会被拦、被拦时缺什么（界面用它显示红字横幅）。"""
    from .. import v5_gate as _gate

    ws = _ws(workshop_id)
    if ws is None:
        return _no_ws(workshop_id)
    ok, code, four = _gate.check(ws, action, {"member_seq": member_seq})
    return {"ok": True, "allowed": bool(ok), "code": code, "four": four,
            "action": action}
