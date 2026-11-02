# -*- coding: utf-8 -*-
"""`routers/mind_v5`：**根A（心智根）与根B（manifest）的接口**（设计 `V117`，P2.5 批）。

| 端点 | 干什么 |
|---|---|
| `POST /api/mind/{agent_key}` | 建（或确认）一个心智根（幂等；不动已有文件） |
| `GET  /api/mind/{agent_key}` | 只读：宪章/基模/索引的大小、记忆条数、最近日志（给人看） |
| `POST /api/mind/{agent_key}/memories` | agent 自己追加一条记忆（append-only） |
| `POST /api/workshop/{id}/manifest` | 写工作区 `manifest.json`（平台记账 hash） |
| `GET  /api/mind/{agent_key}/pending` | 列待审队列（**只读**：名字/目标/行数/是否已批） |
| `GET  /api/mind/{agent_key}/pending/{name}` | 读单条 diff 原文（单层名，防穿越） |
| `POST /api/mind/{agent_key}/pending/{name}/approve` | **记审批**（不动核心基模；批准人必填） |
| `POST /api/mind/{agent_key}/pending/{name}/apply` | **落地**：把人批过的 diff 真正写进核心基模（先归档原文；未批/基模变过/diff 套不上都拒） |
| `GET  /api/workshop/{id}/manifest` | 读 manifest + G0 校验结果（`ok/code/errors/four`） |
| `POST /api/workshop/{id}/boot-prompt` | 生成 Boot Prompt（写死两个真实路径；可附到 `_notice.md`） |
| `POST /api/workshop/{id}/daily-close` | **日结**：把当天任务/教训写回根A（模板拼接，不用 AI） |

**边界（如实说）**：本批只做"骨架 + 校验 + 日结"；`00_核心基模` 的改写属于夜间审忆员（P4）。
"""
from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from .. import boot_prompt as _boot
from .. import manifest as _mf
from .. import mind as _mind

router = APIRouter()


def _ws(workshop_id: str):
    from ..server import workshops

    return workshops.get(workshop_id)


def _no_ws(workshop_id: str):
    return JSONResponse({"ok": False, "code": "NO_SUCH_WORKSHOP",
                         "error": "没有这个工作间：%s" % workshop_id,
                         "next": "GET /api/workshops 看有哪些"}, status_code=404)


# ── 根A ────────────────────────────────────────────────────────────
@router.get("/api/mind/{agent_key}/pending")
async def mind_pending_list(agent_key: str):
    """列**待审队列** `_pending/*.diff`（**只读**）：界面用它渲染"审忆待审列表"。

    批准**不在这里**（走 `python tools/mind_reviewer.py --approve <id>`）；
    平台只如实列出：名字 / 大小 / 生成时间 / 目标文件 / diff 行数 / 是否已批（含批准人）。
    """
    return _mind.pending_list(agent_key)


@router.get("/api/mind/{agent_key}/pending/{name}")
async def mind_pending_read(agent_key: str, name: str):
    """读一条待审 diff 的**原文**（只读）。名字只能是单层文件名（防穿越）。"""
    try:
        out = _mind.pending_read(agent_key, name)
    except ValueError as e:
        return JSONResponse({"ok": False, "code": "BAD_PENDING_NAME", "error": str(e),
                             "next": "名字用 GET /api/mind/{agent_key}/pending 里的 `name` 原样传"},
                            status_code=400)
    if not out.get("ok"):
        return JSONResponse(out, status_code=404)
    return out


@router.post("/api/mind/{agent_key}/pending/{name}/approve")
async def mind_pending_approve(agent_key: str, name: str, request: Request):
    """**记审批**（**不动核心基模**）：body `{approver}` —— 界面上「一键批准」的落点。

    四条边界：① 只记审批（`00_核心基模.md` 一个字节不改）② 已批过就返回 `already=true`，**不覆盖**原记录
    ③ 批准人必填 ④ 名字必须单层 `.diff`（防穿越）。
    """
    try:
        body = await request.json()
    except Exception:                                          # noqa: BLE001 — 没 body 会走"批准人必填"的拒
        body = {}
    body = body if isinstance(body, dict) else {}
    try:
        out = _mind.approve_pending(agent_key, name, approver=str(body.get("approver") or ""))
    except ValueError as e:
        msg = str(e)
        is_name = ("文件名" in msg) or (".diff" in msg)
        return JSONResponse({"ok": False,
                             "code": "BAD_PENDING_NAME" if is_name else "APPROVER_REQUIRED",
                             "error": msg,
                             "next": ("名字用 GET /api/mind/{agent_key}/pending 里的 `name` 原样传"
                                      if is_name else "body 里给 `approver`（谁批的必须留痕）")},
                            status_code=400)
    if not out.get("ok"):
        return JSONResponse(out, status_code=404)
    return out


@router.post("/api/mind/{agent_key}/pending/{name}/apply")
async def mind_pending_apply(agent_key: str, name: str, request: Request):
    """**人拍板之后**把待审 diff 真正写进 `00_核心基模.md`：body `{approver}`。

    三条硬前置：**必须先 approve**、**基模自提案后没被改过**、**diff 能干净套上**。
    成功时原文先归档 `_archive/`，再写新内容，返回 before/after hash（可核对）。
    """
    try:
        body = await request.json()
    except Exception:                                          # noqa: BLE001 — 没 body 会走"批准人必填"的拒
        body = {}
    body = body if isinstance(body, dict) else {}
    try:
        out = _mind.apply_pending(agent_key, name, approver=str(body.get("approver") or ""))
    except ValueError as e:
        msg = str(e)
        is_name = ("文件名" in msg) or (".diff" in msg)
        return JSONResponse({"ok": False,
                             "code": "BAD_PENDING_NAME" if is_name else "APPROVER_REQUIRED",
                             "error": msg,
                             "next": ("名字用 GET /api/mind/{agent_key}/pending 里的 `name` 原样传"
                                      if is_name else "body 里给 `approver`（谁落的这一笔必须留痕）")},
                            status_code=400)
    if not out.get("ok"):
        code = str(out.get("code") or "")
        return JSONResponse(out, status_code=404 if code == "NO_SUCH_PENDING" else 409)
    return out


@router.post("/api/mind/{agent_key}")
async def mind_ensure(agent_key: str, request: Request):
    """建/确认一个心智根（幂等）。body 可选：`{display_name}`。"""
    try:
        body = await request.json()
    except Exception:                                          # noqa: BLE001 — 没 body 也行
        body = {}
    body = body if isinstance(body, dict) else {}
    return _mind.ensure(agent_key, display_name=str(body.get("display_name") or ""))


@router.get("/api/mind/{agent_key}")
async def mind_get(agent_key: str):
    """只读实况：文件大小/记忆条数/最近日志（**不含记忆正文**，避免一次拉全部）。"""
    info = _mind.describe(agent_key)
    if not info.get("exists"):
        return JSONResponse({"ok": False, "code": "NO_SUCH_MIND",
                             "error": "还没有这个心智根：%s" % info.get("mind_path"),
                             "next": "POST /api/mind/%s 建一个（会写宪章/基模/索引模板）" % agent_key},
                            status_code=404)
    info["ok"] = True
    info["hint"] = "根A 只存认知，不存任务与产物（那些在工作区根B）"
    return info


@router.post("/api/mind/{agent_key}/memories")
async def mind_add_memory(agent_key: str, request: Request):
    """追加一条记忆（append-only，一条一文件）。body：`{text, tags?}`。"""
    try:
        body = await request.json()
    except Exception:                                          # noqa: BLE001
        body = {}
    body = body if isinstance(body, dict) else {}
    out = _mind.add_memory(agent_key, str(body.get("text") or ""),
                           tags=[str(t) for t in (body.get("tags") or [])],
                           source=str(body.get("source") or "agent"))
    return JSONResponse(out, status_code=200 if out.get("ok") else 400)


# ── 根B：manifest ─────────────────────────────────────────────────
@router.post("/api/workshop/{workshop_id}/manifest")
async def write_manifest(workshop_id: str, request: Request):
    """写工作区 `manifest.json`。body 至少 `{mind_path, entry_points[]}`。

    `workshop_id` / `workshop_path` **由平台填**（不采信客户端传的路径）。
    """
    ws = _ws(workshop_id)
    if ws is None:
        return _no_ws(workshop_id)
    try:
        body = await request.json()
    except Exception:                                          # noqa: BLE001
        body = {}
    body = body if isinstance(body, dict) else {}
    wp = Path(str(getattr(ws, "workspace_dir", "") or ""))
    if not str(wp).strip():
        return JSONResponse({"ok": False, "code": "NO_WORKSHOP_PATH",
                             "error": "这个工作间还没有 workshop_path",
                             "next": "先设置工作间路径（P5 的入口会做）"}, status_code=400)
    wp.mkdir(parents=True, exist_ok=True)
    mf = _mf.build(workshop_id=workshop_id, workshop_path=str(wp),
                   mind_path=str(body.get("mind_path") or ""),
                   entry_points=list(body.get("entry_points") or []),
                   active_tasks=list(body.get("active_tasks") or []),
                   ignore_zones=list(body.get("ignore_zones") or []),
                   task_focus=str(body.get("task_focus") or ""))
    ok, code, errs, four = _mf.validate(mf, workshop_path=str(wp))
    if not ok:
        return JSONResponse({"ok": False, "code": code, "error": "manifest 不合法（未写入）",
                             "errors": errs, "four": four,
                             "next": four.get("how", "")}, status_code=422)
    written = _mf.write(wp, mf)
    return {"ok": True, "manifest": mf, **written,
            "next": "让组长按 manifest 的 entry_points 真建文件；再点「重新校验」"}


def _lead_chat_path(ws) -> Path:
    """组长对话落盘位置：<工作区>/_meta/lead-chat.jsonl（**落在用户自己的工作区里**）。"""
    wp = Path(str(getattr(ws, "workspace_dir", "") or ""))
    d = wp / "_meta"
    d.mkdir(parents=True, exist_ok=True)
    return d / "lead-chat.jsonl"


@router.post("/api/workshop/{workshop_id}/lead-chat")
async def lead_chat_append(workshop_id: str, request: Request):
    """追加一条「组长对话」记录。

    ★2026-11-02（老板：「刷新页面之后聊天记录都没了」）：
    组长对话框原来只往 DOM 里 append，**一刷新就没了** —— 用户以为"没回复到平台"，
    其实回复早就回来了（`…/a2a-task/{task_id}` 查得到），只是界面没存。

    body: `{role: "me"|"lead", text, task_id?, source?, state?}`
    `source` 写清这条是谁产生的：`sent`（平台发出）/ `a2a-tasks/get`（平台按任务号取回）/ `platform`。
    """
    ws = _ws(workshop_id)
    if ws is None:
        return _no_ws(workshop_id)
    try:
        body = await request.json()
    except Exception:                                          # noqa: BLE001
        body = {}
    body = body if isinstance(body, dict) else {}
    text = str(body.get("text") or "").strip()
    if not text:
        return JSONResponse({"ok": False, "code": "EMPTY_TEXT",
                             "error": "这条是空的，不记"}, status_code=400)
    rec = {
        "at": datetime.now().isoformat(timespec="seconds"),
        "role": ("lead" if str(body.get("role") or "") == "lead" else "me"),
        "text": text[:4000],
        "task_id": str(body.get("task_id") or "")[:80],
        "source": str(body.get("source") or "sent")[:24],
        "state": str(body.get("state") or "")[:24],
    }
    pf = _lead_chat_path(ws)
    try:
        with pf.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception as e:                                     # noqa: BLE001 — 写不进去要如实说
        return JSONResponse({"ok": False, "code": "WRITE_FAILED",
                             "error": "对话记录写不进去：%s: %s" % (type(e).__name__, str(e)[:160]),
                             "next": "看工作区 `_meta/` 是否可写"}, status_code=500)
    return {"ok": True, "saved": rec, "file": str(pf.name),
            "note": "存在工作区的 `_meta/lead-chat.jsonl`，刷新页面会读回来。"}


@router.get("/api/workshop/{workshop_id}/lead-chat")
async def lead_chat_list(workshop_id: str, limit: int = 200):
    """读回「组长对话」历史（最近 `limit` 条，按时间正序返回）。"""
    ws = _ws(workshop_id)
    if ws is None:
        return _no_ws(workshop_id)
    pf = _lead_chat_path(ws)
    if not pf.is_file():
        return {"ok": True, "messages": [], "total": 0,
                "note": "这个工作间还没有对话记录（发第一句就会建）"}
    items = []
    try:
        for line in pf.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                items.append(json.loads(line))
            except Exception:                                  # noqa: BLE001 — 坏行跳过，不整份失败
                continue
    except Exception as e:                                     # noqa: BLE001
        return JSONResponse({"ok": False, "error": "读对话记录失败：%s: %s"
                             % (type(e).__name__, str(e)[:160])}, status_code=500)
    lim = max(1, min(1000, int(limit or 200)))
    return {"ok": True, "messages": items[-lim:], "total": len(items)}


@router.get("/api/workshop/{workshop_id}/manifest")
async def read_manifest(workshop_id: str):
    """读 manifest + **G0 四要素**（界面用它显示红字横幅）。"""
    ws = _ws(workshop_id)
    if ws is None:
        return _no_ws(workshop_id)
    wp = str(getattr(ws, "workspace_dir", "") or "")
    ok, body, errs = _mf.read(wp)
    if not ok:
        ok2, code2, errs2, four2 = _mf.validate(None, workshop_path=wp)
        return JSONResponse({"ok": False, "code": code2, "stored": False,
                             "errors": errs or errs2, "four": four2,
                             "next": four2.get("how", "")}, status_code=409)
    ok2, code2, errs2, four2 = _mf.validate(body, workshop_path=wp)
    out = {"ok": bool(ok2), "stored": True, "code": code2, "errors": errs2,
           "manifest": body, "manifest_hash": _mf.content_hash(body)}
    if not ok2:
        out["four"] = four2
        out["next"] = four2.get("how", "")
        return JSONResponse(out, status_code=409)
    return out


@router.post("/api/workshop/{workshop_id}/boot-prompt")
async def boot_prompt(workshop_id: str, request: Request):
    """生成 Boot Prompt（两个根**写死成真实路径**）。body：`{agent_key, member_seq?, append_notice?}`。"""
    ws = _ws(workshop_id)
    if ws is None:
        return _no_ws(workshop_id)
    try:
        body = await request.json()
    except Exception:                                          # noqa: BLE001
        body = {}
    body = body if isinstance(body, dict) else {}
    wp = str(getattr(ws, "workspace_dir", "") or "")
    ok, mfb, errs = _mf.read(wp)
    # 没有 manifest 也允许生成（用工作区里的信息凑），但**如实标注 manifest 缺失**
    key = str(body.get("agent_key") or "").strip()
    if not key:
        return JSONResponse({"ok": False, "code": "BAD_REQUEST",
                             "error": "要 agent_key（每个心智根一个键）",
                             "next": "先在 POST /api/mind/{agent_key} 建档，再生成 Boot Prompt"},
                            status_code=400)
    mp = str(_mind.mind_path(key))
    if not Path(mp).is_dir():
        _mind.ensure(key)                                       # 自动建骨架（幂等）
    focus = str((mfb or {}).get("task_focus") or body.get("task_focus") or "")
    zones = list((mfb or {}).get("ignore_zones") or [])
    text = _boot.render(agent_key=_mind.safe_key(key), workshop_path=wp or "（工作区路径未设）",
                        mind_path=mp, task_focus=focus, ignore_zones=zones)
    real, bad = _boot.paths_are_real(text)
    out = {"ok": True, "boot_prompt": text, "mind_path": mp, "workshop_path": wp,
           "paths_are_real": real, "unreal_paths": bad,
           "manifest_ok": bool(ok), "manifest_errors": errs,
           "hint": "两个根写死是**故意**的：激活那一瞬间它们就是唯一事实（对方 2026-10-27 答案 2）"}
    if body.get("append_notice"):
        seq = int(body.get("member_seq") or 0)
        m = next((x for x in (ws.members or []) if int(getattr(x, "member_seq", -1) or -1) == seq), None)
        slot_dir = str(getattr(m, "display_name", "") or "") if m else ""
        notice = Path(wp) / slot_dir / "_notice.md" if slot_dir else Path(wp) / "_notice.md"
        notice.parent.mkdir(parents=True, exist_ok=True)
        with notice.open("a", encoding="utf-8") as fh:
            fh.write("\n- %s | Boot Protocol | 已注入（agent_key=%s）\n"
                     % (time.strftime("%Y-%m-%dT%H:%M"), key))
        out["notice"] = str(notice)
    return out


@router.post("/api/workshop/{workshop_id}/daily-close")
async def daily_close(workshop_id: str, request: Request):
    """**日结**：把当天的任务/教训写回根A（`02_任务日志` + `06_死胡同`）。

    body：`{agent_key, date?, done:[一句话], blocked:[一句话]}`。
    平台只做**模板拼接**（不判断哪条重要 —— 那是审忆员的活）。
    """
    ws = _ws(workshop_id)
    if ws is None:
        return _no_ws(workshop_id)
    try:
        body = await request.json()
    except Exception:                                          # noqa: BLE001
        body = {}
    body = body if isinstance(body, dict) else {}
    key = str(body.get("agent_key") or "").strip()
    if not key:
        return JSONResponse({"ok": False, "code": "BAD_REQUEST",
                             "error": "要 agent_key（日结写给谁的心智根）",
                             "next": "POST /api/mind/{agent_key} 建档案后再日结"},
                            status_code=400)
    date = str(body.get("date") or time.strftime("%Y-%m-%d"))
    done = [str(x) for x in (body.get("done") or []) if str(x).strip()]
    blocked = [str(x) for x in (body.get("blocked") or []) if str(x).strip()]
    if not done and not blocked:
        return JSONResponse({"ok": False, "code": "EMPTY",
                             "error": "日结没有任何内容（done / blocked 都空）",
                             "next": "至少写一条：做完了什么，或卡在哪"}, status_code=400)
    written = []
    if done:
        r = _mind.append_daily(key, date=date,
                               body="## 做完了\n" + "\n".join("- " + x for x in done),
                               kind="task_log")
        written.append(r["file"])
    if blocked:
        r = _mind.append_daily(key, date=date,
                               body="## 卡住 / 走错的路\n" + "\n".join("- " + x for x in blocked),
                               kind="dead_end")
        written.append(r["file"])
    return {"ok": True, "agent_key": _mind.safe_key(key), "written": written,
            "mind_path": str(_mind.mind_path(key)),
            "next": "夜间审忆员会把这些压成 3 行结论并更新 01_记忆索引表（默认关，需 --apply）"}
