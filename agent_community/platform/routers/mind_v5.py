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


def _esc_path():
    from ..env_spec import data_dir as _dd

    return _dd() / "escalations.json"


@router.get("/api/escalation/levels")
async def escalation_levels():
    """**危急度五级表 + 阶段大会议程口径**（`V118` §5 / 队列 49-7，只读）。

    级别是**数据不是 if**：时限 / 处理层级 / 载体都在表里，改口径只改 `platform/escalation.LEVELS`。
    超时未处理 ⇒ **自动升一级**（`POST /api/escalation/sweep`）。
    """
    from fastapi.responses import JSONResponse

    from .. import escalation as _esc

    if not _esc.enabled():
        return JSONResponse({"ok": False, "code": "ESCALATION_DISABLED", "retryable": False,
                             "error": "危急度上报已关闭（AC_ESCALATION=0）",
                             "next": "要把 AC_ESCALATION 设回 1 并重启服务"}, status_code=404)
    return _esc.levels_table()


@router.post("/api/escalation")
async def escalation_create(request: Request):
    """提报一条（body：`{level, summary, workshop_id?, task_ref?, who?}`）。"""
    from fastapi.responses import JSONResponse

    from .. import escalation as _esc
    from ..audit import audit_log as _audit_log

    if not _esc.enabled():
        return JSONResponse({"ok": False, "code": "ESCALATION_DISABLED", "retryable": False,
                             "error": "危急度上报已关闭（AC_ESCALATION=0）"}, status_code=404)
    try:
        body = await request.json()
    except Exception:                                          # noqa: BLE001 — 空 body 下面会按缺项拒
        body = {}
    body = body if isinstance(body, dict) else {}
    if not str(body.get("summary") or "").strip():
        return JSONResponse({"ok": False, "code": "ESCALATION_NEEDS_SUMMARY", "retryable": False,
                             "error": "提报要写清「发生了什么」（summary 不能空）",
                             "next": "在 body.summary 里写一句"}, status_code=400)
    rec = _esc.new_record(level=str(body.get("level") or "L2"),
                          summary=str(body.get("summary") or ""),
                          workshop_id=str(body.get("workshop_id") or ""),
                          task_ref=str(body.get("task_ref") or ""),
                          who=str(body.get("who") or "member"))
    rows = _esc.load(_esc_path())
    rows.append(rec)
    _esc.save(_esc_path(), rows)
    try:
        _audit_log.record("escalation.open", actor=str(rec["who"]), target=str(rec["id"]),
                          detail="%s %s" % (rec["level"], str(rec["summary"])[:60]))
    except Exception as e:                                     # noqa: BLE001 — 审计写不了要说，但提报本身已落盘
        print("[escalation] 审计写入失败：%s: %s" % (type(e).__name__, e), flush=True)
    lim = _esc.level_of(rec["level"]).get("limit_min")
    return {"ok": True, "escalation": rec,
            "next": ("时限 %s 分钟，到期没人处理会自动升一级（`POST /api/escalation/sweep`）"
                     % lim if lim is not None else "L1 无时限（只留在看板观察区）")}


@router.post("/api/escalation/sweep")
async def escalation_sweep():
    """扫一遍超时的 ⇒ **自动升一级**并标「超时未决」（状态机，不是文案）。"""
    from .. import escalation as _esc
    from ..audit import audit_log as _audit_log

    rows = _esc.load(_esc_path())
    out = _esc.sweep(rows)
    if out.get("changed"):
        _esc.save(_esc_path(), rows)
        for c in out["changed"]:
            try:
                _audit_log.record("escalation.escalate", actor="system", target=str(c["id"]),
                                  detail="%s → %s（%s）" % (c["from"], c["to"], c["why"]))
            except Exception as e:                             # noqa: BLE001 — 审计失败不影响升级结果
                print("[escalation] 审计写入失败：%s: %s" % (type(e).__name__, e), flush=True)
    out["view"] = _esc.view(rows)
    return out


@router.get("/api/escalation")
async def escalation_view():
    """看板：谁的、几级、还有多久到点、超没超（**只报事实，不评分**）。"""
    from .. import escalation as _esc

    return _esc.view(_esc.load(_esc_path()))


@router.get("/api/asset-health/{agent_key}")
async def asset_health(agent_key: str, members_count: int | None = None):
    """**团队资产体检**（`V118` §4 / 队列 49-8）：五维风格指纹 + 腐化四信号。

    **只报事实、不评分不排名**：每条给「事实 + 一句建议」，没有健康度百分比。
    `?members_count=N` 传当前名单人数，用来算「默契退化」。
    """
    from fastapi.responses import JSONResponse

    from .. import asset_health as _ah
    from ..mind import mind_root as _mr

    out = _ah.scan(str(_mr()), agent_key, members_count=members_count)
    # ⚠️ `scan` 失败会返回 `ok:false` + `code` —— 端点是 200 的话就成了"看起来成功"
    #   （真打抓到：不存在的根返回 200 NO_MIND_HOME）。失败一律 404 + 人话。
    if not out.get("ok"):
        return JSONResponse(out, status_code=404)
    return out


@router.get("/api/asset-snapshot/{agent_key}")
async def asset_snapshot(agent_key: str, members_count: int | None = None):
    """**只读风格快照**（子集派遣 / 共用：`V118` §4「不自动稀释」）。

    返回 `readonly: true` 的一份副本；**写回父团队必须显式「风格合并」并被记账**（另档）。
    """
    from fastapi.responses import JSONResponse

    from .. import asset_health as _ah
    from ..mind import mind_root as _mr

    out = _ah.snapshot(str(_mr()), agent_key, members_count=members_count)
    if not out.get("ok"):
        return JSONResponse(out, status_code=404)
    return out


@router.get("/api/workshop/{workshop_id}/audit-report")
async def audit_report(workshop_id: str, agent_key: str = ""):
    """**审忆员体检报告**（`V118` §8 G6 / 队列 50-4）：拓扑 / 资产 / 越权 三类一次给全。

    - `topology`：`topology.validate()` 的 C001~C005（有环、双亲、超跨度、超深、空壳）；
    - `assets`：风格指南是否还是「待填」+ 腐化四信号（僵化/默契退化/案例陈旧/教训遗忘）；
    - `authority`：拓扑里**声明了**的看板文件在不在（声明与现实不能两张皮）。

    **只读**：跑完不改任何文件。**不评分**（`no_score: true`）—— 只列「哪里不对、为什么、下一步」。
    """
    from fastapi.responses import JSONResponse

    from .. import audit_checks as _ac
    from .. import mind as _mind
    from .. import v5_roster as _rw

    ws = _ws(workshop_id)
    if ws is None:
        return _no_ws(workshop_id)
    try:
        mem = _rw.effective_members(workshop_id)
        n = len((mem or {}).get("members") or [])
    except Exception as e:                                     # noqa: BLE001 — 名单读不到就说清（别写死 0）
        return JSONResponse({"ok": False, "code": "ROSTER_UNREADABLE", "retryable": True,
                             "error": "名单读不到，体检会报假信号（成员数算不出：%s: %s）"
                                      % (type(e).__name__, e),
                             "next": "先 GET …/roster 看名单是否正常"}, status_code=409)
    key = str(agent_key or "").strip() or ("%s-组长" % workshop_id)
    try:
        mind_home = str(_mind.mind_path(key))
    except Exception as e:                                     # noqa: BLE001 — 心智根路径算不出也要如实说
        return JSONResponse({"ok": False, "code": "MIND_PATH_FAILED", "retryable": True,
                             "error": "算不出心智根路径（%s: %s）" % (type(e).__name__, e)},
                            status_code=500)
    out = _ac.run(ws, mind_home=mind_home, members_count=n)
    out["workshop_id"] = workshop_id
    out["agent_key"] = key
    out["members_count"] = n
    return out


@router.post("/api/workshop/{workshop_id}/topology/route")
async def topology_route(workshop_id: str, request: Request):
    """**看板强制路由**（`V118` §8 G5 / 队列 50-2）：这条写入该不该放行。

    body：`{src, dst, kind?}` —— 谁在写、写给哪个 Cell 的看板、全文还是摘要。
    规则（三句，设计稿原话）：**只写自己看板 / 往父写摘要 / 跨组找 LCA**。
    具体口径：往祖先只收 `summary`；不能替**后代**写；跨组一律拒并**给出共同祖先**。
    """
    from fastapi.responses import JSONResponse

    from .. import topology as _topo

    ws = _ws(workshop_id)
    if ws is None:
        return _no_ws(workshop_id)
    wp = str(getattr(ws, "workspace_dir", "") or "")
    f = Path(wp) / "组织拓扑.md"
    if not f.is_file():
        return JSONResponse({"ok": False, "code": "NO_TOPOLOGY", "retryable": False,
                             "error": "这个工作间还没有 `组织拓扑.md`，没有父子关系就没法判该往哪写",
                             "next": "先 POST /api/workshop/{id}/topology 交一份拓扑"}, status_code=409)
    topo, err = _topo.parse(f.read_text(encoding="utf-8", errors="replace"))
    if err:
        return JSONResponse({"ok": False, "code": "TOPOLOGY_UNREADABLE", "retryable": False,
                             "error": "读不懂 `组织拓扑.md`：%s" % err}, status_code=422)
    try:
        body = await request.json()
    except Exception:                                          # noqa: BLE001 — 空 body 下面会按缺项拒
        body = {}
    body = body if isinstance(body, dict) else {}
    out = _topo.route_write(topo, str(body.get("src") or ""), str(body.get("dst") or ""),
                            kind=str(body.get("kind") or "full"))
    out["ok"] = True                                          # 路由答复本身是成功的（allow=False 是结论，不是错误）
    out["workshop_id"] = workshop_id
    return out


@router.get("/api/workshop/{workshop_id}/topology")
async def topology_get(workshop_id: str):
    """读工作区的**组织拓扑**（`组织拓扑.md`，`V118` §2 的唯一真源）+ 顺手校验一遍。

    文件不存在 ⇒ 返回一棵**合法的空树**（只有根 Cell）并标 `exists: false` ——
    **不代写**：要不要落一份拓扑是用户/前端的事（平台不替你造结构）。
    """
    from fastapi.responses import JSONResponse

    from .. import topology as _topo

    ws = _ws(workshop_id)
    if ws is None:
        return _no_ws(workshop_id)
    wp = str(getattr(ws, "workspace_dir", "") or "")
    if not wp:
        return JSONResponse({"ok": False, "code": "NO_WORKSPACE_DIR", "retryable": False,
                             "error": "这个工作间没有 workspace_dir，读不了拓扑"}, status_code=409)
    f = Path(wp) / "组织拓扑.md"
    if not f.is_file():
        t = _topo.empty(workshop_id)
        return {"ok": True, "exists": False, "topology": t, "validation": _topo.validate(t),
                "file": str(f),
                "next": "要落一份就直接 `POST /api/workshop/{id}/topology` 把拓扑交上来（校验通过才会写）"}
    text = f.read_text(encoding="utf-8", errors="replace")
    topo, err = _topo.parse(text)
    if err:
        return JSONResponse({"ok": False, "code": "TOPOLOGY_UNREADABLE", "retryable": False,
                             "error": "读不懂这份 `组织拓扑.md`：%s" % err,
                             "file": str(f),
                             "next": "看文件的 ```json 块（要合法 JSON，含 cells 数组）"}, status_code=422)
    return {"ok": True, "exists": True, "topology": topo, "validation": _topo.validate(topo),
            "file": str(f), "chars": len(text)}


@router.post("/api/workshop/{workshop_id}/topology")
async def topology_put(workshop_id: str, request: Request):
    """写工作区的**组织拓扑**：**校验通过才写**；不通过 ⇒ 422 + 错误清单，**文件一个字节不动**。

    body：拓扑本身（`{root, cells:[...]}`），或包一层 `{topology: {...}}`。
    五条硬约束：C001 无环 / C002 单亲 / C003 跨度 / C004 深度 / C005 可验证产物。
    C003/C004 是**警告**（可写，但要在 `policies` 里写明为什么）；其余是**拒**。
    """
    from fastapi.responses import JSONResponse

    from .. import topology as _topo

    ws = _ws(workshop_id)
    if ws is None:
        return _no_ws(workshop_id)
    wp = str(getattr(ws, "workspace_dir", "") or "")
    if not wp:
        return JSONResponse({"ok": False, "code": "NO_WORKSPACE_DIR", "retryable": False,
                             "error": "这个工作间没有 workspace_dir，写不了拓扑"}, status_code=409)
    try:
        body = await request.json()
    except Exception:                                          # noqa: BLE001 — 空 body 下面会按缺项拒
        body = {}
    body = body if isinstance(body, dict) else {}
    topo = body.get("topology") if isinstance(body.get("topology"), dict) else body
    if not isinstance(topo.get("cells"), list):
        return JSONResponse({"ok": False, "code": "TOPOLOGY_NEEDS_CELLS", "retryable": False,
                             "error": "拓扑缺 `cells` 数组",
                             "next": "body 传 `{root: <根 id>, cells: [{id, parent, children, verify:{how,where}}]}`"},
                            status_code=400)

    f = Path(wp) / "组织拓扑.md"
    old_text = f.read_text(encoding="utf-8", errors="replace") if f.is_file() else ""
    out = _topo.apply(old_text, topo)
    if not out.get("ok"):
        res = out.get("result") or {}
        return JSONResponse({"ok": False, "code": "TOPOLOGY_REJECTED", "retryable": False,
                             "error": "拓扑没过校验，**文件没动**",
                             "errors": res.get("errors") or [],
                             "warnings": res.get("warnings") or [],
                             "codes": res.get("codes") or {},
                             "file": str(f),
                             "next": "按 errors 里的 code 改（C001 环 / C002 单亲 / C005 空壳 是硬拒）；改完再 POST"},
                            status_code=422)
    try:
        if f.is_file():                                        # 覆盖前留备份（备份纪律）
            import time as _t

            bak = f.with_suffix(".md.bak_%s" % _t.strftime("%Y%m%d_%H%M%S"))
            bak.write_text(old_text, encoding="utf-8")
        f.write_text(out["text"], encoding="utf-8")
    except OSError as e:                                       # noqa: BLE001 — 写不进去要说清
        return JSONResponse({"ok": False, "code": "TOPOLOGY_WRITE_FAILED", "retryable": True,
                             "error": "校验过了但写不进文件（%s: %s）" % (type(e).__name__, e),
                             "file": str(f)}, status_code=500)
    res = out.get("result") or {}
    return {"ok": True, "file": str(f), "format_version": out.get("format_version"),
            "warnings": res.get("warnings") or [], "stats": res.get("stats") or {},
            "note": "已写入；警告项（跨度/深度）请在对应 Cell 的 `policies` 里写明为什么，别让下一个人再猜一次"}


@router.post("/api/mind/appeal")
async def mind_appeal(request: Request):
    """**审忆申诉**（`V141` §3.2 R3 / 队列 49-4）：被驳回/被覆盖的一方可以申辩。

    三条硬规矩（设计稿原文）：
    1. **带证据才收**（`evidence_ref`：哪个库/哪个会话/谁核的/用什么工具核的）；
    2. **不自动通过** —— 只落一条 `pending` 申诉，进侧根待办，由人或审忆员裁；
    3. **宪章不可被申诉改**（`target_tier=charter` 一律拒）—— 宪章只能人改。
    """
    from fastapi.responses import JSONResponse

    from ..audit import audit_log as _audit_log
    from .. import root_tiers as _rt
    from ..env_spec import data_dir as _dd

    try:
        body = await request.json()
    except Exception:                                          # noqa: BLE001 — 空 body 下面会按缺项拒
        body = {}
    body = body if isinstance(body, dict) else {}
    tier = str(body.get("target_tier") or "review").strip().lower()
    if not _rt.tier_of(tier):
        return JSONResponse({"ok": False, "code": "NO_SUCH_TIER", "retryable": False,
                             "error": "没有这一层：%s（可用：%s）" % (tier, "、".join(_rt.names())),
                             "next": "GET /api/root-tiers 看四层"}, status_code=400)
    if not _rt.appealable(tier):
        return JSONResponse({"ok": False, "code": "TIER_NOT_APPEALABLE", "retryable": False,
                             "error": "「%s」这一层**不可申诉**（只能由人改）" % _rt.tier_of(tier)["label"],
                             "next": "宪章/日志层没有申诉通道：要改宪章请找人；日志是自动记录，不需要申诉"},
                            status_code=400)
    proof = str(body.get("evidence_ref") or "").strip()
    if not proof:
        return JSONResponse({"ok": False, "code": "APPEAL_NEEDS_PROOF", "retryable": False,
                             "error": "申诉**必须带证据指针**（哪个库/哪个会话/谁核的/用什么工具核的）",
                             "next": "在 body 里给 evidence_ref；空手申辩不收"}, status_code=400)
    reason = str(body.get("reason") or "").strip()
    if not reason:
        return JSONResponse({"ok": False, "code": "APPEAL_NEEDS_REASON", "retryable": False,
                             "error": "申诉要写清**你要推翻哪件事、为什么**",
                             "next": "在 body.reason 里写一句"}, status_code=400)

    stamp = __import__("time").strftime("%Y%m%d-%H%M%S")
    d = _dd() / "appeals"
    d.mkdir(parents=True, exist_ok=True)
    rec = {"id": "appeal-%s" % stamp, "at": __import__("agent_community.platform.timeutil",
                                                      fromlist=["x"]).now_iso(),
           "status": "pending", "target_tier": tier,
           "agent_key": str(body.get("agent_key") or ""),
           "workshop_id": str(body.get("workshop_id") or ""),
           "target_file": str(body.get("target_file") or ""),
           "reason": reason, "evidence_ref": proof,
           "who": str(body.get("who") or "member"),
           "note": "进侧根待办，**不自动通过**；只有人能裁定"}
    f = d / (rec["id"] + ".json")
    i = 1
    while f.exists():
        i += 1
        f = d / ("%s-%d.json" % (rec["id"], i))
        rec["id"] = "appeal-%s-%d" % (stamp, i)
    f.write_text(__import__("json").dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
    try:
        # ⚠️ `audit` 模块里 `record` 是 `AuditLog` 的**方法**，模块级只有实例 `audit_log`
        #   （原来写 `_audit_mod.record` ⇒ AttributeError 被宽 except 吞掉 ⇒ 审计一条没落）。
        _audit_log.record("mind.appeal", actor=str(rec["who"]), target=str(rec["id"]),
                          detail="申诉 %s 层：%s" % (tier, reason[:60]))
    except Exception as e:                                     # noqa: BLE001 — 审计写不了不影响申诉本身，但要喊
        print("[appeal] 审计写入失败：%s: %s" % (type(e).__name__, e), flush=True)
    return {"ok": True, "appeal": rec, "file": f.name,
            "note": "已收到，状态 `pending` —— **不会自动通过**；裁定结果会写审计"}


@router.post("/api/mind/{agent_key}")
# ⚠️ 静态段必须在动态段 `/api/mind/{agent_key}` **之前**注册（FastAPI 按注册顺序匹配）
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
                           source=str(body.get("source") or "agent"),
                           grade=str(body.get("grade") or ""))     # ★R1：必填，不给就拒
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

@router.get("/api/root-tiers")
async def root_tiers_table():
    """**根系四层的治理参数**（`V141` §3.1 / 队列 49-1，只读）。

    四层各带：谁能写 / 要不要证据 / 要不要交叉复核 / 能不能申诉 / 多久动一次 / 能不能淘汰。
    **一处真相**：这张表就是 `platform/root_tiers.py` 里的 `TIERS`，判据读的也是它。
    """
    from .. import root_tiers as _rt

    return _rt.table()


