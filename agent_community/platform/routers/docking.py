# -*- coding: utf-8 -*-
"""platform/routers/docking：**对接中心**（`V34`）—— 只读总览。

## 这个端点补的是什么洞（`V34` §4.1）

现在**没有任何端点**能回答「**这个 harness 现在接在哪个工作间、哪个岗位**」：
`GET /api/harness/list` 只给"已注册的 harness"，工作间侧的 `members-status` 只给一个间。
总控（大厅）要的正是这个跨间视图，所以它是**唯一确定要新加的后端**。

## 硬约束（`V34` §4.1 / §7 / §8）

- **只读、无副作用**：不触发激活、不改状态（对齐 V17 的教训：只读端点别偷偷收敛状态）；
- **不新建存储**：绑定关系**由工作间成员表反查**（成员里记着 `harness_ids` + 通道状态），
  工作间量级小，逐间扫即可；
- **不造第二个词**：harness 的"接入事实"一律取自 `tier_guidance`
  （`interface_class` / `can_be_woken_by_platform` / `needs_protocol`），
  **不输出 `access_tier`** —— 那个字段已被裁定为"只写不读、界面话术一律不引用"（`tier_guidance` 顶部裁定）。
  ⚠️ `V34` §4.1 的形状提案里列了 `access_tier` / `has_template`，那是 **2026-10-10 的写法**；
  实现期以现行裁定为准（`V34` 自己也写了"形状提案，实现期可微调"）。

## 端点的两种范围（`V34` §1）

```
GET /api/docking/overview            # 总控：全局（所有工作间）
GET /api/docking/overview?ws=<wsid>  # 单间：只回该间
```
"""
from __future__ import annotations

import re
import types
from datetime import datetime, timezone
from urllib.parse import quote

from fastapi import APIRouter, Request

from ..state import workshops

router = APIRouter()


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _interface_facts(wakeup_method: str) -> dict:
    """harness 的**接入事实**（一处真相：映射表在 `tier_guidance`，这里只是喂给它一个壳）。"""
    from .. import tier_guidance as tg
    shim = types.SimpleNamespace(wakeup_method=wakeup_method or "")
    cls = tg.interface_class(shim)
    return {
        "interface_class": cls,
        "interface_class_label": tg.CLASS_LABEL.get(cls, ""),
        "can_be_woken_by_platform": bool(tg.can_be_woken_by_platform(shim)),
        "needs_protocol": bool(tg.needs_protocol_for(shim)),
    }


def _channel_state(m) -> str:
    return str(getattr(m, "channel_state", "pending") or "pending")


def _binding(ws, m, hid: str) -> dict:
    """一条"谁接进了哪个工作间、对上哪个岗位"。"""
    name = (list(getattr(m, "session_aliases", None) or []) or [""])[0]
    ref = getattr(m, "session_ref", None) or {}
    return {
        "ws_id": getattr(ws, "workshop_id", ""),
        "ws_name": getattr(ws, "name", ""),
        "member_id": getattr(m, "member_id", ""),
        "display_name": getattr(m, "display_name", ""),
        "role": getattr(m, "role", ""),
        "channel_state": _channel_state(m),
        "channel_seq": int(getattr(m, "channel_seq", 1) or 1),
        "member_status": str(getattr(m, "status", "") or ""),
        "session_name": name or str((ref or {}).get("session_name") or ""),
        "since": getattr(m, "channel_since", 0.0) or 0.0,
        "harness_id": hid,
    }


def _workshop_row(ws) -> dict:
    """一行工作间（供总控的工作间选择器）：**四个数分开给**，不合并、不美化。"""
    members = list(getattr(ws, "members", None) or [])
    docked = sum(1 for m in members if _channel_state(m) == "active")
    pending = sum(1 for m in members if _channel_state(m) == "pending")
    retired = sum(1 for m in members if _channel_state(m) == "retired")
    offline = sum(1 for m in members if str(getattr(m, "status", "") or "") == "offline")
    return {
        "ws_id": getattr(ws, "workshop_id", ""),
        "name": getattr(ws, "name", ""),
        "status": getattr(ws, "status", ""),
        "member_count": len(members),
        "docked": docked,       # 通道 active = 真的接上了
        "pending": pending,     # 通道还没接上
        "retired": retired,     # 通道停用（换过对话/换过人）
        "offline": offline,     # 成员掉线（与 member_offline 同一口径：status == offline）
    }


def build_overview(ws_id: str | None = None, *, workshop_map=None, sessions=None,
                   generated_at: str | None = None) -> dict:
    """**纯函数**（守卫直接调它，不用起服务）。

    `workshop_map` / `sessions` 可注入：默认取真实状态，测试喂合成数据。
    """
    wmap = workshops if workshop_map is None else workshop_map
    if sessions is None:
        from ..harness_adapter import harness_manager
        sessions = harness_manager.list_sessions()
    only = (ws_id or "").strip() or None

    bindings_by_hid: dict[str, list] = {}
    rows: list[dict] = []
    for w in (wmap.values() if hasattr(wmap, "values") else wmap):
        # ⚠️ `ws_id` 过滤必须**按 id 精确比**（别用 startswith 之类做"看起来对"的匹配）
        if only and str(getattr(w, "workshop_id", "")) != only:
            continue
        rows.append(_workshop_row(w))
        for m in (getattr(w, "members", None) or []):
            for hid in (list(getattr(m, "harness_ids", None) or []) or [""]):
                bindings_by_hid.setdefault(str(hid), []).append(_binding(w, m, str(hid)))

    harnesses: list[dict] = []
    seen: set[str] = set()
    for s in sessions:
        hid = str(s.get("harness_id") or "")
        if only and not bindings_by_hid.get(hid):
            # 单间范围：只列与这个间有关系的 harness（否则"单间"就不叫单间了）
            continue
        seen.add(hid)
        wake = (s.get("wakeup") or {})
        item = {
            "harness_id": hid,
            "harness_name": s.get("harness_name") or "",
            "harness_type": s.get("harness_type") or "",
            "online": str(s.get("status") or "").lower() == "online",
            "status": s.get("status") or "",
            "transport": s.get("transport") or "",
            "wakeup_method": wake.get("wakeup_method") or "",
            "registered_at": s.get("registered_at") or "",
            "bindings": sorted(bindings_by_hid.get(hid, []),
                               key=lambda b: (b["ws_id"], b["member_id"])),
        }
        item["docked_count"] = sum(1 for b in item["bindings"] if b["channel_state"] == "active")
        item.update(_interface_facts(item["wakeup_method"]))
        harnesses.append(item)

    # ⚠️ **悬空引用要说话**：成员表里引用了某个 harness_id，但它不在已注册清单里
    #   （注销/改名/手工改数据都会造成）—— 不报出来的话，界面会显示"接进去了"却谁也找不到。
    dangling = [
        {"harness_id": hid,
         "bindings": sorted(bs, key=lambda b: (b["ws_id"], b["member_id"]))}
        for hid, bs in sorted(bindings_by_hid.items()) if hid not in seen and hid
    ]

    return {
        "scope": {"type": "workshop" if only else "global", "ws": only},
        "harnesses": sorted(harnesses, key=lambda h: h["harness_id"]),
        "workshops": sorted(rows, key=lambda r: r["ws_id"]),
        "dangling_bindings": dangling,
        "generated_at": generated_at or _now_iso(),
    }


@router.get("/api/docking/overview")
async def docking_overview(request: Request, ws: str = ""):
    """对接中心总览（**只读**）：`?ws=<工作间id>` 收窄到单间。

    ⚠️ 这里**只读**：不 save_state、不触发激活、不改任何成员/通道状态。
    没有 `ws` 参数就是总控视角（全局）；给了就只回该间（`V34` §1 的"两个入口、同一页面"）。
    """
    from ..server import Utf8JSONResponse
    only = (ws or "").strip()
    if only:
        found = None
        for w in workshops.values():
            if str(getattr(w, "workshop_id", "")) == only:
                found = w
                break
        if found is None:
            return Utf8JSONResponse({"error": "工作间不存在"}, status_code=404)
    return build_overview(only or None)


# ══════════════════════════════════════════════════════════════════════════
# FE-26：前台的四件事 —— **定位 / 试投 / 档案读 / 档案写**
#
# 老板 2026-10-15 晚场裁定：
#   · 脚本**存进 harness 档案**（`HarnessInfo.activation_script`，不另建目录）；
#   · 加「详情」按钮可看可改四块（框架性能/适配模型/具体桥模板/激活方式）；
#   · 视觉激活＝**用户填关键词 → agent 去定位 → 落成脚本**。
#
# 三条纪律（照 FE-26 §3.3）：
#   ① **定位只读**（只列窗口，不注入、不改状态）；② **试投放给界面按钮**（不是给 Agent 自动跑）；
#   ③ **保存必须用户点**（可审计、可回退）。⇒ 所以本文件里没有"Agent 自己往窗口送字"的路。
# ══════════════════════════════════════════════════════════════════════════

#: 档案里**允许通过本端点改**的字段（白名单制：不在表里的一律拒绝，避免手滑改坏身份字段）
_HARNESS_EDITABLE = (
    "harness_name", "harness_type", "harness_version", "description",
    "transports", "preferred_transport", "callback_url",
    "wakeup_method", "wakeup_url", "wakeup_dir", "acp_command", "acp_cwd",
    "api_base_url", "api_message_path", "api_outbox_dir",
    "bridge_dir", "bridge_status",
    "can_wake", "waking_models", "waking_protocols",
    "ai", "tools", "metadata", "activation_script",
)


def match_candidates(windows: list, keywords: list, limit: int = 20) -> list:
    """**纯函数**：按关键词给窗口打分排序（可单测，不碰系统）。

    匹配＝关键词**逐个**在标题里出现（大小写不敏感）；匹配分＝命中数/关键词数。
    排序：分高在前，同分按标题（稳定，便于界面与测试对照）。
    """
    kws = [str(k).strip().lower() for k in (keywords or []) if str(k).strip()]
    if not kws:
        return []
    scored = []
    for w in (windows or []):
        title = str((w or {}).get("title") or "")
        low = title.lower()
        hits = [k for k in kws if k in low]
        if hits:
            scored.append({**w, "hits": hits, "score": round(len(hits) / len(kws), 3)})
    scored.sort(key=lambda x: (-x["score"], str(x.get("title"))))
    return scored[:max(1, int(limit or 20))]


def patch_harness_dump(old: dict, patch: dict) -> tuple:
    """**纯函数**：把界面提交的补丁并进档案。返回 `(新档案 dict, 错误中文)`。

    只接受 `_HARNESS_EDITABLE` 里的键；**未知键直接拒绝并列出**（不静默忽略 ——
    静默忽略会让用户以为改了，实际没改，正是准则⑤要防的那种状态）。
    """
    patch = dict(patch or {})
    bad = sorted(k for k in patch if k not in _HARNESS_EDITABLE)
    if bad:
        return None, "这些字段不允许在这里改（或字段名写错了）：%s" % "、".join(bad)
    new = dict(old or {})
    new.update(patch)
    return new, ""


@router.post("/api/docking/locate")
async def docking_locate(request: Request):
    """**定位（只读）**：按关键词列出候选窗口。**不注入、不改任何状态。**

    body: `{"keywords": ["TraeCode","合作社"], "limit": 20}`
    返回：`{ok, supported, keywords, total_windows, candidates[], probe_note}`
    —— 非 Windows / 没有注入脚本时 `supported=false` 且 `probe_note` **说清为什么**。
    """
    from ..server import Utf8JSONResponse
    from .. import visual_launch
    import asyncio

    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        body = {}
    keywords = [str(k).strip() for k in (body.get("keywords") or []) if str(k).strip()]
    fuzzy = bool(body.get("fuzzy"))
    if not keywords:
        return Utf8JSONResponse({"error": "keywords 不能为空 —— 给几个**会出现在那个窗口标题里**的字"}, status_code=400)
    res = await asyncio.to_thread(visual_launch.list_windows, "", fast=True)
    wins = res.get("windows") or []
    limit = int(body.get("limit") or 20)
    if fuzzy:
        # ★老板 2026-10-24：「让用户先输入模糊的窗口名，点击确定，平台**根据相似程度**搜索窗口」
        #   ⇒ 不再只做"标题包含"，而是对所有标题算相似度排前 N；`sim < 0.3` 的丢掉（宁少不滥）。
        cands = await asyncio.to_thread(visual_launch.fuzzy_candidates,
                                        wins, keywords[0], limit=limit, min_sim=0.3)
    else:
        cands = match_candidates(wins, keywords, limit)
    # 候选里补**真实尺寸与是否最小化**：快路（ctypes）已经带回来了就直接用；注入路才逐个量。
    for c in cands:
        c.setdefault("minimized", None)
        if c.get("client_w") is None or c.get("minimized") is None:
            info = await asyncio.to_thread(visual_launch.window_info, int(c.get("hwnd") or 0))
            c["width"], c["height"] = info.get("width"), info.get("height")
            c["client_w"], c["client_h"] = info.get("client_w"), info.get("client_h")
            c["minimized"] = info.get("minimized")
        c["usable"] = bool(not c.get("minimized")
                           and int(c.get("width") or 0) >= 200
                           and int(c.get("height") or 0) >= 200)
    return {
        "ok": bool(res.get("ok")),
        "supported": bool(res.get("supported")),
        "keywords": keywords,
        "fuzzy": fuzzy,
        "total_windows": len(wins),
        "candidates": cands,
        "probe_note": res.get("detail") or "",
        "checked_at": _now_iso(),
    }



def _default_probe_text() -> str:
    """试投用的默认探针文本（一处真相：两条路都用它）。"""
    return "【平台试投】视觉激活自检 %s —— 看到这行说明这条路能通，可以忽略。" % _now_iso()

@router.post("/api/docking/try-activate")
async def docking_try_activate(request: Request):
    """**试投一次**（写：真的往窗口送一句话）—— 由**界面按钮**调用，**Agent 不自动调**。

    body: `{"title": "<选中的窗口标题>", "keywords": [...], "text": "可选", "method": "auto"}`
    `title` 优先（用户选中的那个窗口最精确）；没有 `title` 就用第一个关键词当匹配键。

    返回 `visual_launch.launch()` 的**原样结果**（含 `verified` 三态）＋ `key`（实际用的匹配键）。
    **本端点不保存任何东西** —— 保存是用户下一步按「保存」时的事（可审计、可回退）。
    """
    from ..server import Utf8JSONResponse
    from .. import visual_launch
    import asyncio

    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        body = {}
    title = str(body.get("title") or "").strip()
    keywords = [str(k).strip() for k in (body.get("keywords") or []) if str(k).strip()]
    key = title or (keywords[0] if keywords else "")
    # ★2026-10-23（禁止私人化…落地到通用框架）：**只给 harness_id、没给窗口**时才走统一投递层。
    #   理由：若只说"送给某位员工"，该走哪条通道、目标在哪是通用投递层的事。
    #   ⚠️ 但屏2/3 的语义是"**投给这个窗口**"（用户刚点选的候选）：这时必须留在窗口这条路 ——
    #   实测（2026-10-23 老板试投）：带 harness_id 时被"按员工找目标"顶掉，界面答的是
    #   "既没有手动绑定目标，也还没有会话名"，而用户明明已经选好了窗口。
    hid = str(body.get("harness_id") or "").strip()
    if hid and not key and _find_harness(hid) is not None:
        from .. import delivery

        _t = str(body.get("text") or "").strip() or _default_probe_text()
        res = await asyncio.to_thread(delivery.deliver_to_harness, hid, _t,
                                      prefer=str(body.get("prefer") or ""))
        out = dict(res or {})
        out["key"] = key
        out["text"] = _t
        out["via"] = "harness"
        out["checked_at"] = _now_iso()
        return out
    if not key:
        return Utf8JSONResponse({"error": "得先给我**要投给哪个窗口**（选中一个候选，或给一个关键词）"}, status_code=400)
    text = str(body.get("text") or "").strip() or (
        "【平台试投】视觉激活自检 %s —— 看到这行说明这条路能通，可以忽略。" % _now_iso())
    # ★★2026-10-15 走查抓到的**假成功**（准则⑤）：
    #   `method="auto"` 在 wmpaste 失败后**会自动退到剪贴板**，而剪贴板**总是成功** ⇒
    #   把一段文本投给**根本不存在的窗口**时，`launch()` 返回 `ok=True`（附一句 wmpaste 失败），
    #   包装层若照抄 `ok` 就会显示"试投成功" —— 而真相是"这个窗口根本没找到"。
    #   ⇒ **试投必须钉住方法**（默认钉在消息式注入上，不自动兜底）：
    #     试投回答的是"**这条路能不能通**"；剪贴板兜底是**另一档服务**，要由用户显式选。
    import os as _os
    # ★2026-10-23（老板实测）：试投这条路也要能用"教过的位置"——
    #   有 harness_id 就读这位员工的相对位置，后面 `launch(anchor=...)` 会**先点一下输入框**再粘贴。
    #   没给 harness_id 就照旧（不猜位置、不乱点）。
    _anchor = None
    _hid_for_anchor = str(body.get("harness_id") or "").strip()
    if _hid_for_anchor:
        try:
            from .. import input_anchor as _ia

            _anchor = _ia.get(_hid_for_anchor)
        except Exception:                                     # noqa: BLE001 — 读不到就当没教过
            _anchor = None
    method = str(body.get("method") or "").strip() or ("wmpaste" if _os.name == "nt" else "clipboard")
    if method == "auto":
        method = "wmpaste" if _os.name == "nt" else "clipboard"
    # ★2026-10-23（老板定「模拟鼠标键盘，从表层交互」）：`method="surface"` ＝
    #   置顶 → 点到**教过的位置** → 逐字键入（真鼠标键盘）→ 能读回就核对。
    #   对 Electron（Trae 这类）是唯一真正能把字送进输入框的路：它没有文本框子窗口，
    #   系统层粘贴没有落点；而"点一下再打字"正是人做的动作。
    if method == "surface":
        from .. import input_anchor

        anchor = body.get("anchor") or input_anchor.get(str(body.get("harness_id") or ""))
        res = await asyncio.to_thread(visual_launch.surface_send, key, text,
                                      anchor=anchor, press_enter=bool(body.get("enter")))
        out = dict(res or {})
        out["key"] = key
        out["text"] = text
        out["method"] = "surface"
        out["anchor_used"] = anchor or None
        out["checked_at"] = _now_iso()
        if out.get("ok") is not True and not anchor:
            out["next_step"] = (out.get("next_step") or "") + (
                "　（表层交互需要先「教一次位置」：把鼠标放到目标输入框上，再按一次「教位置」）")
        return out
    res = await asyncio.to_thread(visual_launch.launch, None, text, method=method,
                                 session_name=key, verify=True, anchor=_anchor)
    out = dict(res or {})
    out["key"] = key
    out["text"] = text
    out["anchor_used"] = bool(_anchor)
    out["method"] = method                 # 钉住的那个方法（不是 auto）
    out["checked_at"] = _now_iso()
    if out.get("ok") is not True and method == "wmpaste":
        out["next_step"] = (out.get("next_step") or "") + (
            "　（消息式注入走不通时，可以改用**剪贴板兜底档**：把 method 传 clipboard，"
            "平台会把内容放好剪贴板、你点一下那个窗口再粘贴 —— 那一档**不等于**验证通过。）")
    return out


def _find_harness(harness_id: str):
    """按 id 找已注册的 harness 会话（找不到返回 None）。"""
    from ..harness_adapter import harness_manager
    return harness_manager.sessions.get(harness_id)


@router.get("/api/docking/harness/{harness_id}/bind")
async def docking_harness_bind_get(harness_id: str):
    """**读回"手动绑定的目标"**（这位员工的界面是哪个窗口/页面）。

    为什么要有绑定（2026-10-23，前端反馈"已登记员工还没有会话名 ⇒ 送不了话"）：
    自动定位靠"会话名 ≈ 窗口标题"，而会话名要用户走完命名流程；绑定让用户在候选列表里**点一下**就定下来。
    """
    from .. import input_anchor

    b = input_anchor.get_bind(harness_id)
    return {"ok": True, "harness_id": harness_id, "bind": b,
            "hint": ("" if b else "还没绑定目标；可在候选窗口/页面里点一下「绑定到这个员工」，或先命名会话")}


@router.post("/api/docking/harness/{harness_id}/bind")
async def docking_harness_bind_set(harness_id: str, request: Request):
    """**手动绑定目标**：`{hwnd, title?}` 或 `{ws, idx?, title?}`（二选一）。

    绑定后 `deliver` 直接用它的目标，不再靠猜标题 —— **不依赖会话名**、也不认任何具体应用。
    """
    from .. import input_anchor
    from ..server import Utf8JSONResponse

    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        body = {}
    body = body if isinstance(body, dict) else {}
    hwnd = int(body.get("hwnd") or 0)
    ws = str(body.get("ws") or "").strip()
    if not hwnd and not ws:
        return Utf8JSONResponse(
            {"error": "要么给窗口句柄 hwnd，要么给页面 ws（从 /api/docking/web/probe 里取）"},
            status_code=400)
    res = input_anchor.set_bind(harness_id, hwnd=hwnd, ws=ws,
                                title=str(body.get("title") or ""),
                                idx=int(body.get("idx") or 0),
                                source=str(body.get("source") or "manual"))
    if not res.get("ok"):
        return Utf8JSONResponse({"error": res.get("why") or "绑定失败"}, status_code=400)
    return res


@router.delete("/api/docking/harness/{harness_id}/bind")
async def docking_harness_bind_clear(harness_id: str):
    """取消绑定（返回原来有没有）—— 换了窗口/页面时先清掉，别让平台一直往旧目标送。"""
    from .. import input_anchor

    return {"ok": True, "cleared": input_anchor.clear_bind(harness_id)}


@router.get("/api/docking/harness/{harness_id}/screenshot")
async def docking_harness_screenshot(harness_id: str, hwnd: int = 0, title: str = ""):
    """**这个员工现在长什么样**：抓一帧窗口/页面所在窗口的彩色 PNG（给"框选"用）。

    为什么要有它（老板 2026-10-23 夜）：「输入框检测不出也可以让用户截图上传，
    平台最好能提供类似作业帮框题的功能，让用户框出输入框和发送按钮。」
    平台自己能抓，就不用让用户去装截图工具再上传。
    """
    from fastapi.responses import Response
    from ..server import Utf8JSONResponse

    target = None
    try:
        from .. import delivery as _dl

        target = _dl.resolve_target(harness_id)
    except Exception:                                         # noqa: BLE001 — 拿不到目标就如实说
        target = None
    # 前端在屏2/3 已经选好窗口时，直接用 `?hwnd=` / `?title=` 指定（不必等会话名/绑定）
    # ⚠️ 优先级：**显式给的 > 标题挑出来的 > 绑定的**。原来绑定排在 title 前面，
    #    结果用户在界面上换了一个窗口、传了 `?title=`，后端仍然去截那个旧的绑定窗口（2026-10-24 真机踩到）。
    _rejected: list = []          # 标题对上了、但都不能截的候选（要如实告诉用户为什么）

    def _note_rejected(wins) -> None:
        for w in wins or []:
            try:
                _rejected.append((w, _vl.window_info(int(w.get("hwnd") or 0))))
            except Exception:                                 # noqa: BLE001 — 量不到就记个空信息
                _rejected.append((w, {}))

    hwnd = int(hwnd or 0)
    if not hwnd and title:
        try:
            from .. import visual_launch as _vl

            wins = [w for w in (_vl.list_windows(fast=True).get("windows") or [])
                    if title.lower() in (w.get("title") or "").lower()]
            # ★按**真实尺寸**挑最大的（`list_windows` 不带尺寸，原来那句 sort 等于没排 —— 实测挑中 93x150 小窗）
            pick = _vl.pick_biggest(wins)
            hwnd = int(pick["hwnd"]) if pick else 0
            if not hwnd:
                _note_rejected(wins)
        except Exception:                                     # noqa: BLE001
            hwnd = 0
    if not hwnd:
        # 没有窗口（页面型目标），退而用同名窗口 / 绑定的窗口（**优先级最低**）
        try:
            from .. import visual_launch as _vl

            name = str((target or {}).get("name") or "").strip()
            wins = [w for w in (_vl.list_windows(fast=True).get("windows") or [])
                    if name and name.lower() in (w.get("title") or "").lower()]
            pick = _vl.pick_biggest(wins)
            hwnd = int(pick["hwnd"]) if pick else 0
            if not hwnd and wins:
                _note_rejected(wins)
        except Exception:                                     # noqa: BLE001
            hwnd = 0
    if not hwnd:
        hwnd = int((target or {}).get("hwnd") or 0)
    if not hwnd:
        why = "没定位到这个 harness 的窗口 —— 先「绑定目标」或起会话名，再截图"
        if _rejected:
            parts = []
            for w, info in _rejected:
                t = w.get("title") or "（无标题）"
                if info.get("minimized"):
                    parts.append("「%s」已**最小化**（先把它恢复出来）" % t)
                elif info.get("width"):
                    parts.append("「%s」只有 %dx%d，像浮层/影子窗口，不是主窗" %
                                 (t, info["width"], info["height"]))
                else:
                    parts.append("「%s」量不到尺寸" % t)
            why = ("标题对上了 %d 个窗口，但没有一个能截图：%s —— 把目标窗口恢复出来再看，"
                   "或者干脆自己截一张图（剪贴板/上传）来框。" % (len(_rejected), "；".join(parts)))
        return Utf8JSONResponse({"error": why}, status_code=409)
    from .. import screen_vision

    # ★绑定/指定的窗口也要先体检（2026-10-24 真机：绑的那个窗口被最小化了，
    #   `GetWindowRect` 给 237x39 这种占位值，用户看到的是"窗口客户区太小" —— 说不清是**最小化**了）
    try:
        from .. import visual_launch as _vl2

        _info = _vl2.window_info(hwnd)
        if _info.get("minimized"):
            return Utf8JSONResponse(
                {"error": "这个窗口**已最小化**（%s×%s 是 Windows 给的占位值）—— 先把目标窗口恢复出来再截图；"
                          "或者干脆自己截一张图（从剪贴板/上传）来框。"
                          % (_info.get("width"), _info.get("height"))}, status_code=409)
        if int(_info.get("width") or 0) < 200 or int(_info.get("height") or 0) < 200:
            return Utf8JSONResponse(
                {"error": "这个窗口只有 %s×%s，太小了（像浮层/影子窗口，不是主窗）—— 换一个窗口，"
                          "或自己截一张图（从剪贴板/上传）来框。"
                          % (_info.get("width"), _info.get("height"))}, status_code=409)
    except Exception as e:                                    # noqa: BLE001 — 体检失败不挡截图，但要留话
        print("[screenshot] 窗口体检失败（继续截图）：%s: %s" % (type(e).__name__, e), flush=True)

    shot = screen_vision.grab_png(hwnd)
    if not shot.get("ok"):
        return Utf8JSONResponse({"error": shot.get("why") or "截图失败"}, status_code=409)
    return Response(content=shot["png"], media_type="image/png",
                    headers={"Cache-Control": "no-store",
                             "X-Client-Width": str(shot.get("client_w") or ""),
                             "X-Client-Height": str(shot.get("client_h") or ""),
                             "X-Image-Width": str(shot.get("width") or ""),
                             "X-Image-Height": str(shot.get("height") or "")})


@router.get("/api/docking/clipboard-image")
async def docking_clipboard_image():
    """**用户刚截的那张图**（在系统剪贴板里）→ 直接当"截图"用（老板 2026-10-24 定）。

    为什么有它：原来的路是"平台自己去找窗口截屏"，真机上连踩两坑（同名窗口里挑中 93x150 浮层；
    用户想截的那扇窗当时是最小化的）。换成"用户截什么、平台就用什么"，与窗口当前状态无关。

    返回 `image/png`；响应头带 `X-Image-Width/Height`。
    **`frame`（相对比例的基准）不在这里给** —— 前端用 `POST /api/docking/locate` 里选中窗口的
    `client_w/client_h`，因为"用户截的是整窗"这件事只有用户知道。
    """
    import asyncio

    from fastapi.responses import Response

    from .. import clipboard_image
    from ..server import Utf8JSONResponse

    res = await asyncio.to_thread(clipboard_image.grab_clipboard_png)
    if not res.get("ok"):
        return Utf8JSONResponse(
            {"error": res.get("why") or "剪贴板里没有图", "supported": res.get("supported")},
            status_code=404 if res.get("supported") else 501)
    return Response(content=res["png"], media_type="image/png",
                    headers={"Cache-Control": "no-store",
                             "X-Image-Width": str(res.get("width") or ""),
                             "X-Image-Height": str(res.get("height") or "")})


@router.get("/api/docking/harness/{harness_id}/regions")
async def docking_harness_regions_get(harness_id: str):
    """读回这个 harness 的**框选区域**（输入框 / 发送按钮 / 用户给的关键词）。"""
    from .. import input_regions

    rec = input_regions.get(harness_id)
    return {"ok": True, "harness_id": harness_id, "regions": rec,
            "hint": ("" if rec else "还没框过：先看截图，把「输入框」和（可选）「发送按钮」框出来")}


@router.post("/api/docking/harness/{harness_id}/regions")
async def docking_harness_regions_set(harness_id: str, request: Request):
    """保存框选：`{input:{x,y,w,h}, send:{x,y,w,h}, keywords:[…], frame:{w,h}, hwnd?, title?}`。

    - 坐标全部是**相对比例 0~1**；`frame` 是"按多大的客户区算出来的"（会给用户看，也用于排查）；
    - `frame` **不许是占位值**（最小化窗口会给出 237×39 这种）：收到就 400，并说清为什么 ——
      2026-10-24 真机，老板框完存下了 `frame=237×39`，之后按这个基准算出来的位置全错；
    - 可选 `hwnd`/`title`：**保存顺手把窗口绑给这位员工**（原来要前端再发一次 `…/bind`，
      中途失败就变成"框了但没有目标"，老板真机就卡在这 —— 2026-10-24）。
    """
    import asyncio

    from .. import input_anchor, input_regions
    from ..server import Utf8JSONResponse

    try:
        body = await request.json()
    except Exception:                                         # noqa: BLE001
        body = {}
    body = body if isinstance(body, dict) else {}
    frame = body.get("frame")
    if isinstance(frame, dict):
        try:
            fw, fh = int(frame.get("w") or 0), int(frame.get("h") or 0)
        except (TypeError, ValueError):
            fw = fh = 0
        if (fw or fh) and (fw < 200 or fh < 200):
            return Utf8JSONResponse(
                {"error": "这个 frame 只有 %dx%d，是**最小化窗口的占位值**（不是真大小）—— "
                          "先把目标窗口恢复出来，再重新框一次" % (fw, fh)}, status_code=400)
    res = input_regions.set_regions(
        harness_id,
        input_box=body.get("input"), send_box=body.get("send"),
        sessions_box=body.get("sessions"), new_session_box=body.get("new_session"),
        keywords=body.get("keywords"), frame=body.get("frame"),
        activation_message=body.get("activation_message"),
        tests=body.get("tests"),
        source=str(body.get("source") or "box-select"))
    if not res.get("ok"):
        return Utf8JSONResponse({"error": res.get("why") or "保存失败"}, status_code=400)
    # 顺手绑定（可选）：攒成一次动作，别让"框了但没目标"这种中间态出现
    hwnd = int(body.get("hwnd") or 0)
    title = str(body.get("title") or "").strip()
    if hwnd or title:
        b = await asyncio.to_thread(input_anchor.set_bind, harness_id, hwnd=hwnd, title=title,
                                    source="box-select")
        res["bind"] = b
        if not b.get("ok"):
            res["bind_hint"] = "框选已存好，但**绑定窗口没成**：%s" % (b.get("why") or "原因不明")
    return res


@router.delete("/api/docking/harness/{harness_id}/regions")
async def docking_harness_regions_clear(harness_id: str):
    """取消框选（换了界面/窗口时先清掉，别让平台一直点旧位置）。"""
    from .. import input_regions

    return {"ok": True, "cleared": input_regions.clear(harness_id)}


@router.post("/api/docking/harness/{harness_id}/visual-run")
async def docking_harness_visual_run(harness_id: str, request: Request):
    """**一次跑完的视觉激活**：新建会话 → 点输入框 → 粘固定话术 → 发送（老板 2026-10-24 规格）。

    body: `{text?, enter?:true, new_session?:false, restore_focus?:true, settle_new?:1.2}`
    → `{ok, steps:[{name,ok,why,pasted,ratio}], state, focus_restored, why}`

    为什么要"一次跑完"（老板原话：「**新建会话之后就没办法发送通知了**」）：
    分两个按钮两次请求时，中间那段（已经点了新建会话、界面正切过去）没人管，
    下一次请求去粘贴时前台/焦点/布局可能已经变了 ⇒ 新建会话成了、通知发不出去。
    这里把 ①抬前台自证 ②新建会话 ③点输入框+粘贴 ④发送 ⑤把平台拉回最前 **一条链跑完**，
    任一步失败就停在原地并说清是哪一步。
    """
    import asyncio

    from ..server import Utf8JSONResponse
    from .. import delivery as _dl
    from .. import input_regions, visual_run

    try:
        body = await request.json()
    except Exception:                                         # noqa: BLE001
        body = {}
    body = body if isinstance(body, dict) else {}
    reg = input_regions.get(harness_id) or {}
    if not reg.get("input"):
        return Utf8JSONResponse(
            {"ok": False, "steps": [], "state": "not_sent",
             "why": "这个 harness 还没框过输入框 —— 先框一次（截图 → 框 → 保存）"}, status_code=409)
    target = await asyncio.to_thread(_dl.resolve_target, harness_id)
    hwnd = int((target or {}).get("hwnd") or 0)
    if not (target or {}).get("ok") or not hwnd:
        return Utf8JSONResponse(
            {"ok": False, "steps": [], "state": "not_sent",
             "why": (target or {}).get("why") or "还没认出这个 harness 的窗口 —— 先选窗口（会绑定）"},
            status_code=409)
    text = str(body.get("text") or "").strip() or DEFAULT_ACTIVATION_MESSAGE
    res = await asyncio.to_thread(
        visual_run.run, hwnd, reg, text,
        enter=bool(body.get("enter", True)),
        with_new_session=bool(body.get("new_session", False)),
        settle_new=float(body.get("settle_new") or 1.2),
        restore_focus=bool(body.get("restore_focus", True)))
    res["text"] = text
    return res


@router.post("/api/docking/harness/{harness_id}/visual-probe")
async def docking_harness_visual_probe(harness_id: str, request: Request):
    """**点一块，看画面变没变** —— "测试新建会话"就用它（老板 2026-10-24 口述流程）。

    body: `{box:{x,y,w,h}, restore_focus?:true}`（相对比例 0~1，就是框选出来的那块）
    → `{ok, clicked, changed, ratio, why, box}`：

    - 先抬前台（自证），点这块的**正中**（老板原话「脚本点击时默认点击中间」）；
    - 点之前/之后各抓一帧，用 `band_diff` 比这一块所在的那条带 ——
      **变了**说明确实有反应（例如会话列表多了一条），**没变**就如实说没变；
    - `restore_focus` 默认 **true**：做完把前台还给调用前的那个窗口（老板规格 ③②）；
    - 读不回文字（Electron 常态），所以这里只给"画面变没变"这个硬信号，**不假装知道多了几条**。
    """
    import asyncio

    from ..server import Utf8JSONResponse
    from .. import delivery as _dl
    from .. import input_regions as _ir
    from .. import input_sim, screen_vision

    try:
        body = await request.json()
    except Exception:                                         # noqa: BLE001
        body = {}
    body = body if isinstance(body, dict) else {}
    box = _ir._clamp_box(body.get("box") or {})
    if not box:
        return Utf8JSONResponse({"error": "要测的那块框给得不全（要 x/y/w/h，且 w、h 大于 0）"},
                                status_code=400)
    target = await asyncio.to_thread(_dl.resolve_target, harness_id)
    hwnd = int((target or {}).get("hwnd") or 0)
    if not (target or {}).get("ok") or not hwnd:
        return Utf8JSONResponse(
            {"ok": False, "clicked": False, "changed": None,
             "error": (target or {}).get("why") or "还没认出这个 harness 的窗口 —— 先选窗口（会绑定）"},
            status_code=409)
    pt = _ir.input_point(box)                                  # 正中
    prev_fg = input_sim.foreground_window()

    def _run():
        input_sim.ensure_foreground(hwnd)
        before = screen_vision.grab_band(hwnd, pt["rel_y"], half=0.12)
        ck = input_sim.click_in_window(hwnd, pt["rel_x"], pt["rel_y"])
        if not ck.get("ok"):
            return {"ok": False, "clicked": False, "changed": None,
                    "why": ck.get("why") or "点不到目标窗口", "box": box}
        import time as _t
        _t.sleep(0.45)                                          # 给界面一点时间反应
        after = screen_vision.grab_band(hwnd, pt["rel_y"], half=0.12)
        d = screen_vision.band_diff(before, after, pt["rel_y"], half=0.12)
        return {"ok": True, "clicked": True, "changed": d.get("changed"),
                "ratio": d.get("ratio"), "why": d.get("why") or "", "box": box,
                "point": pt}

    res = await asyncio.to_thread(_run)
    if bool(body.get("restore_focus", True)) and prev_fg and prev_fg != hwnd:
        r = await asyncio.to_thread(input_sim.set_foreground, prev_fg)
        res["focus_restored"] = bool(r.get("ok"))
        if not r.get("ok"):
            res["focus_note"] = r.get("why") or ""
    return res


@router.get("/api/docking/harness/{harness_id}/delivery")
async def docking_harness_delivery_target(harness_id: str):
    """**这个 harness 现在能把话送到哪儿**（通用入口的"解析结果"，只读）。

    按会话名自动找：记过的页面 → 开了调试端口的同名页面 → 同名窗口；
    找不到时 `why` 是能照着做的一句话（例如"还没有会话名"）。
    """
    import asyncio

    from .. import delivery

    return await asyncio.to_thread(delivery.resolve_target, harness_id)


@router.post("/api/docking/harness/{harness_id}/deliver")
async def docking_harness_deliver(harness_id: str, request: Request):
    """**通用送话**：把文本送给这个 harness（自动定位目标 + 四通道投递 + 读回核对）。

    上层（界面按钮 / 脚本激活 / 唤醒 / 任务提示）**只说"送给这个 harness"**，
    不必知道该用 CDP 还是原生通道、目标窗口在哪 —— 那是本入口的事。

    body: `{text, prefer?, idx?, enter?, restore_focus?}`；
    返回 `{ok, channel, verified, attempts, resolved, …}`。

    `restore_focus` 默认 **true**（老板 2026-10-24 规格 ③②：「最后将平台拉至最前，恢复合作社展示」）：
    送完把前台还给**调用前那个窗口**（通常就是平台界面）。要盯着目标看就传 false。
    """
    import asyncio

    from ..server import Utf8JSONResponse
    from .. import delivery, input_sim

    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        body = {}
    body = body if isinstance(body, dict) else {}
    text = str(body.get("text") or "")
    if not text:
        return Utf8JSONResponse({"error": "要送的文本是空的"}, status_code=400)
    prev_fg = input_sim.foreground_window()
    res = await asyncio.to_thread(delivery.deliver_to_harness, harness_id, text,
                                  prefer=str(body.get("prefer") or ""),
                                  idx=(int(body["idx"]) if body.get("idx") is not None else None),
                                  press_enter=bool(body.get("enter")))
    if bool(body.get("restore_focus", True)) and prev_fg:
        target_hwnd = int(((res.get("resolved") or {}).get("hwnd")) or 0)
        if target_hwnd and target_hwnd != prev_fg:
            r = await asyncio.to_thread(input_sim.set_foreground, prev_fg)
            res["focus_restored"] = bool(r.get("ok"))
            if not r.get("ok"):
                res["focus_note"] = r.get("why") or ""
    return res


@router.get("/api/docking/delivery/channels")
async def docking_delivery_channels(hwnd: int = 0, ws: str = "", title: str = ""):
    """**这个目标能用哪条投递通道**（老板 2026-10-23「只能浏览器吗」）。

    四条通道：`cdp`（Chromium/Electron，需开调试端口）｜`native`（Win32 原生文本框，WM_PASTE+WM_GETTEXT）
    ｜`uia`（UI Automation，如 Win11 记事本）｜`clipboard`（半自动，**不能核实**）。
    返回每条的 `available` 与**原因**，外加 `recommended` —— 界面照着显示就行，别自己猜。
    """
    import asyncio

    from .. import delivery

    return await asyncio.to_thread(delivery.channels, hwnd or None, ws=ws, title=title)


@router.post("/api/docking/delivery/send")
async def docking_delivery_send(request: Request):
    """**统一投递**：按 `cdp → native → uia → clipboard` 自动选路，如实标 `verified` 三态。

    body: `{text, hwnd?, title?, ws?, idx?, prefer?}`
    - `prefer` 可锁定一档（排障用；锁定档失败就**如实失败**，不偷偷换档）；
    - `verified=True`：读回核对通过；`False`：读回不一致；`None`：这档本来就不能核实（半自动）。
    """
    import asyncio

    from ..server import Utf8JSONResponse
    from .. import delivery

    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        body = {}
    body = body if isinstance(body, dict) else {}
    text = str(body.get("text") or "")
    if not text:
        return Utf8JSONResponse({"error": "要投的文本是空的"}, status_code=400)
    hwnd = int(body.get("hwnd") or 0)
    ws = str(body.get("ws") or "")
    if not hwnd and not ws:
        return Utf8JSONResponse(
            {"error": "至少给我一个目标：窗口句柄 hwnd（原生窗口）或页面 ws（浏览器目标）"},
            status_code=400)
    res = await asyncio.to_thread(delivery.deliver, text, hwnd=hwnd,
                                  title=str(body.get("title") or ""), ws=ws,
                                  idx=int(body.get("idx") or 0),
                                  prefer=str(body.get("prefer") or ""))
    return res


@router.get("/api/docking/web/probe")
async def docking_web_probe():
    """**浏览器级投递的探测**：本机有哪些 Chromium/Electron 开了调试端口（`AC_CDP_PORT`）。

    老板 2026-10-23：「不能是真鼠标」⇒ 改成 CDP 往输入框里塞文本。
    没开调试端口时**如实说怎么开**（目标应用自己加 `--remote-debugging-port=9222` 重启）。
    """
    import asyncio

    from .. import cdp_input

    return await asyncio.to_thread(cdp_input.probe)


@router.post("/api/docking/web/editors")
async def docking_web_editors(request: Request):
    """扫某个页面 target 里"能输入的地方"（textarea / contenteditable / 文本 input）。

    body: `{"ws": "<webSocketDebuggerUrl>"}`（从 `/api/docking/web/probe` 里取）。
    返回每项的 `idx/tag/editable/placeholder/maxlength/cls/尺寸` —— 用户据此指认哪个是聊天框。
    """
    import asyncio

    from .. import cdp_input
    from ..server import Utf8JSONResponse

    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        body = {}
    ws = str((body or {}).get("ws") or "").strip()
    if not ws:
        return Utf8JSONResponse({"error": "要给我页面的 ws 地址（先调 /api/docking/web/probe）"},
                                status_code=400)

    async def _scan():
        async with cdp_input.CDP(ws) as conn:
            return await cdp_input.scan_editors(conn)

    try:
        res = await asyncio.wait_for(_scan(), timeout=20)
    except Exception as e:                                   # noqa: BLE001 — 连不上要如实说
        return Utf8JSONResponse({"error": "连不上这个调试目标：%s: %s" % (type(e).__name__, e),
                                 "ws": ws}, status_code=502)
    return res


@router.post("/api/docking/web-send")
async def docking_web_send(request: Request):
    """**浏览器级投递**：把文本塞进指定页面第 `idx` 个输入框（三档自动降级 + 读回核对）。

    body: `{ws, text, idx=0, mode="auto", enter=false, harness_id?}`
    - 三档：`insert`（Input.insertText）→ `paste-event`（合成 paste，保留换行）→ `clipboard`（真剪贴板+Ctrl+V）；
    - **每档后都读回核对**，读回不够就换下一档；三档都不行 ⇒ `ok=false` 并说清；
    - `enter=true` 才会回车（有些聊天框 Enter＝发送，**默认不发**，由用户决定）。
    """
    import asyncio

    from .. import cdp_input, input_anchor
    from ..server import Utf8JSONResponse

    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        body = {}
    body = body if isinstance(body, dict) else {}
    ws = str(body.get("ws") or "").strip()
    text = str(body.get("text") or "")
    if not ws:
        return Utf8JSONResponse({"error": "要给我页面的 ws 地址（先调 /api/docking/web/probe）"},
                                status_code=400)
    if not text:
        return Utf8JSONResponse({"error": "要投的文本是空的"}, status_code=400)
    idx = int(body.get("idx") or 0)
    mode = str(body.get("mode") or "auto")

    async def _go():
        return await cdp_input.deliver(ws, text, idx=idx, mode=mode)

    try:
        res = await asyncio.wait_for(_go(), timeout=90)
    except Exception as e:                                   # noqa: BLE001
        return Utf8JSONResponse({"error": "投递时连不上调试目标：%s: %s" % (type(e).__name__, e)},
                                status_code=502)
    res["ws"] = ws
    res["idx"] = idx
    res["text_len"] = len(text)
    # ★2026-10-23：**同一口径**——/web/send 也必须带 state（界面只看这一个字段，
    # 不许这条路径有、那条路径没有，否则前端还得为每条路写一套判断）。
    from .. import delivery as _delivery

    res["state"] = _delivery.state_of(res)
    res.setdefault("text_control", {"found": True if res.get("verified") else None,
                                     "why": "" if res.get("verified") else "CDP 已扫到输入框，但读回没核对上"})
    if body.get("enter") and res.get("ok"):
        async def _enter():
            async with cdp_input.CDP(ws) as conn:
                return await conn.press_enter()

        try:
            res["enter"] = await asyncio.wait_for(_enter(), timeout=15)
        except Exception as e:                               # noqa: BLE001
            res["enter"] = {"ok": False, "why": "%s: %s" % (type(e).__name__, e)}
    if body.get("harness_id") and res.get("ok"):
        input_anchor.set_web(str(body["harness_id"]), ws=ws, idx=idx,
                             target_id=str(body.get("target_id") or ""),
                             title=str(body.get("title") or ""))
    return res


@router.get("/api/docking/harness/{harness_id}/anchor")
async def docking_anchor_get(harness_id: str):
    """**读回"教过的输入框位置"**（表层交互用）。没教过 ⇒ `{ok:true, anchor:null}`（不猜）。"""
    from .. import input_anchor

    a = input_anchor.get(harness_id)
    return {"ok": True, "harness_id": harness_id, "anchor": a,
            "hint": ("" if a else "还没教过位置；表层交互需要先教一次（把鼠标放到目标输入框上）")}


@router.post("/api/docking/harness/{harness_id}/anchor")
async def docking_anchor_set(harness_id: str, request: Request):
    """**教一次位置**：记下目标窗口里输入框的相对位置（0~1）。

    body 两种写法（都行）：
    - `{"rel_x": 0.5, "rel_y": 0.88, "title": "<窗口标题>"}` —— 已知相对位置；
    - `{"from_cursor": true, "title": "<窗口标题>"}` —— **用鼠标当前所在位置**：
      用户把鼠标停在目标输入框上，界面按一下"教位置"，平台把光标位置换算成窗口内相对坐标。
    """
    from .. import input_anchor
    from ..server import Utf8JSONResponse

    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        body = {}
    if not isinstance(body, dict):
        body = {}
    title = str(body.get("title") or "").strip()
    if body.get("from_cursor"):
        import asyncio

        from .. import input_sim, visual_launch

        cursor = input_sim.cursor_pos()
        hwnd = 0
        if title:
            rc, out = visual_launch._run_inject(["-TitleLike", title, "-FocusOnly"])
            hwnd = visual_launch._hwnd_from_output(out)
        if not hwnd:
            return Utf8JSONResponse(
                {"error": "没定位到窗口（title 为空或没匹配到）—— 先选一个候选窗口再教位置",
                 "cursor": cursor}, status_code=400)
        rel = input_sim.screen_to_relative(hwnd, cursor[0], cursor[1])
        if not rel.get("inside"):
            return Utf8JSONResponse(
                {"error": "鼠标现在**不在**那个窗口里（相对坐标 %s, %s）—— 把鼠标放到目标输入框上再教一次"
                          % (rel.get("rel_x"), rel.get("rel_y")), "cursor": cursor, "rect": rel.get("rect")},
                status_code=400)
        res = input_anchor.set_anchor(harness_id, rel["rel_x"], rel["rel_y"],
                                      title=title, source="cursor")
        res["cursor"] = cursor
        res["rect"] = rel.get("rect")
        return res
    try:
        rel_x = float(body.get("rel_x"))
        rel_y = float(body.get("rel_y"))
    except Exception:  # noqa: BLE001
        return Utf8JSONResponse({"error": "要么给 rel_x/rel_y（0~1），要么给 from_cursor:true"},
                                status_code=400)
    return input_anchor.set_anchor(harness_id, rel_x, rel_y, title=title, source="manual")


@router.delete("/api/docking/harness/{harness_id}/anchor")
async def docking_anchor_clear(harness_id: str):
    """忘掉教过的位置（点了没反应时先清掉重教，别让平台一直点旧位置）。"""
    from .. import input_anchor

    return {"ok": True, "cleared": input_anchor.clear(harness_id)}


@router.get("/api/docking/harness/{harness_id}")
async def docking_harness_get(harness_id: str):
    """**档案详情（只读）**：`FE-26` §2b 那四块 ＋ 平台算出的档位事实（只读项）。"""
    from ..server import Utf8JSONResponse
    sess = _find_harness(harness_id)
    if sess is None:
        return Utf8JSONResponse({"error": "没有这个 harness（可能已注销或 id 写错）"}, status_code=404)
    dump = sess.info.model_dump()
    facts = _interface_facts(dump.get("wakeup_method") or "")
    return {
        "harness_id": harness_id,
        "archive": dump,                       # ① 可编辑的档案本体（四块都在这）
        "facts": facts,                        # ② 平台算出来的档位事实（**只读**，别回写）
        "editable": list(_HARNESS_EDITABLE),   # ③ 哪些字段允许改（与后端白名单**同一处真相**）
        "readonly_reason": ("档位事实由 tier_guidance 按档案现算，不落库、不许回写 —— "
                            "它要是能被改，界面就会出现「改了唤醒方式、档位还显示旧的」那种假状态"),
        "checked_at": _now_iso(),
    }


@router.post("/api/docking/harness/{harness_id}")
async def docking_harness_update(harness_id: str, request: Request):
    """**改档案**（写）—— 只改声明白名单里的字段，**不重建会话/桥**（那与"重新注册"是两件事）。

    body: 要改的字段（可含 `activation_script`）。返回**改后的档案 ＋ 重算后的档位事实**
    （改了 `wakeup_method` ⇒ 事实跟着变，界面据此回显 —— 不许"改了还显示旧档位"）。
    """
    from ..server import Utf8JSONResponse, save_state
    from ..protocol import HarnessInfo
    sess = _find_harness(harness_id)
    if sess is None:
        return Utf8JSONResponse({"error": "没有这个 harness（可能已注销或 id 写错）"}, status_code=404)
    try:
        patch = await request.json()
    except Exception:  # noqa: BLE001
        return Utf8JSONResponse({"error": "请求体不是 JSON"}, status_code=400)
    if not isinstance(patch, dict):
        return Utf8JSONResponse({"error": "请求体必须是一个对象（字段名 → 新值）"}, status_code=400)
    merged, err = patch_harness_dump(sess.info.model_dump(), patch)
    if err:
        return Utf8JSONResponse({"error": err}, status_code=400)
    try:
        new_info = HarnessInfo(**merged)      # 逐字段校验（类型/枚举/必填）
    except Exception as e:  # noqa: BLE001
        return Utf8JSONResponse({"error": "改不动：%s" % str(e)[:300]}, status_code=400)
    if new_info.harness_id != harness_id:     # 身份字段不许被顺手改掉
        return Utf8JSONResponse({"error": "harness_id 不能改（要换身份请注销后重新登记）"}, status_code=400)
    sess.info = new_info
    save_state()
    dump = new_info.model_dump()
    return {
        "ok": True,
        "archive": dump,
        "facts": _interface_facts(dump.get("wakeup_method") or ""),
        "changed": sorted(k for k in (patch or {})),
        "saved_at": _now_iso(),
    }


# ══════════════════════════════════════════════════════════════════════════
# FE-26 §11（2026-10-15 深夜）：继承书 §7.2 的两件后端收尾
#   ① **候选关键词** —— §4 要求前台「主动给候选（可点即填）」，可候选全是平台侧的事实
#      （`probe_hints` 的三条线索、真扫到的进程名、成员的会话名、真扫到的窗口标题）
#      ⇒ 界面自己拼不出来（要它自己拼＝把平台知识抄进界面，规则 20 禁止）⇒ 一个**只读**端点。
#   ② **脚本副本导出** —— §5 末句「导出成 `.ps1` 只是**给用户看的副本**，不是第二份真相」。
#   两件**都是 GET（只读）** ⇒ **不进写请求白名单**（那份白名单管的是**非 GET**）⇒
#   §5 的契约与 §3.5 交接包都不用改，外包方照旧按同一份白名单验收。
# ══════════════════════════════════════════════════════════════════════════

def suggest_keywords(info: dict, *, probe_hints: dict | None = None, session_names=None,
                     process_names=None, window_titles=None, limit: int = 12) -> list:
    """**纯函数**：从**已有事实**推候选关键词（`FE-26` §4）。

    返回 `[{text, source, why}]`（去重、按来源优先级排序，最多 `limit` 条）。

    纪律：**每条候选都得有出处**（`source` ＋ 一句给人看的 `why`）——
    编不出出处的不给：用户点了一个没来由的词、找不到窗口，只会以为平台在瞎猜
    （`FE-26` §4 的原话就是"不让用户卡在第一步、也不让用户干瞪眼"）。
    """
    info = dict(info or {})
    hints = dict(probe_hints or {})
    out: list[dict] = []
    seen: set[str] = set()

    def add(text, source: str, why: str) -> None:
        t = str(text if text is not None else "").strip()
        if not t or len(t) > 120 or t.lower() in seen:
            return
        seen.add(t.lower())
        out.append({"text": t, "source": source, "why": why})

    # 优先级＝"最可能出现在窗口标题里"的顺序（会话名 > 线索 > 真扫到的进程名 > 名字 > 端口 > 扫到的标题）
    for n in (session_names or []):
        add(n, "会话名", "成员登记时用的会话名 —— 那个窗口的标题里通常就有它")
    ph = str(hints.get("process_hint") or "").strip()
    if ph:
        add(ph, "进程线索", "登记时你给过的进程名线索")
    for p in (process_names or []):
        add(p, "进程名（真扫到）", "平台刚在本机**真的扫到了**这个进程")
    pa = str(hints.get("path_hint") or "").strip()
    if pa:
        add(pa.replace("\\", "/").rstrip("/").split("/")[-1], "路径线索",
            "登记时你给过的安装路径（取最后一段当线索）")
    add(info.get("harness_name"), "员工名", "这位员工在档案里的名字")
    pt = hints.get("port_hint")
    if pt not in (None, "", 0):
        add(pt, "端口线索", "登记时你给过的端口线索（窗口标题里带端口号时才用得上）")
    # ★窗口标题里的**相关内容排在前面**（它更可能就是你要找的那个窗口），其余照原样跟在后面 ——
    #   一条都不藏（用户可能就是要在里面点出那个窗口），但也不让无关标题把有关系的挤到看不见
    #   （"哪个最像"正是 §3.2 屏 2 要回答的问题）。
    titles = [str(t or "") for t in (window_titles or [])]
    related = [t for t in titles if t and any(s in t.lower() for s in seen)]
    others = [t for t in titles if t and t not in related]
    for t in related:
        add(t, "窗口标题（跟这位员工有关）", "标题里出现了上面的线索 —— 最可能就是你要找的那个窗口")
    for t in others:
        add(t, "窗口标题", "平台刚扫到的一个窗口标题（整条标题＝最精确的关键词）")
    return out[:max(1, int(limit or 12))]


def _session_names_for(harness_id: str) -> list:
    """**平台的**这个 harness 相关的会话名（从工作间成员表反查，与总览同一处口径）。"""
    names: list[str] = []
    for w in workshops.values():
        for m in (getattr(w, "members", None) or []):
            if str(harness_id) not in [str(h) for h in (getattr(m, "harness_ids", None) or [])]:
                continue
            n = _binding(w, m, str(harness_id)).get("session_name") or ""
            if n and n not in names:
                names.append(n)
    return names


@router.get("/api/docking/keyword-suggestions")
async def docking_keyword_suggestions(harness_id: str = "", probe: int = 1):
    """**只读**：给「脚本激活」第一步的**候选关键词**（`FE-26` §4）—— 每条都带出处。

    `?probe=0` 跳过本机扫描（只按档案推，快路径）。**不写状态、不改档案。**
    探不到（非 Windows 等）⇒ `supported=false` ＋ `probe_note` **说清为什么**，不是空列表。
    """
    from ..server import Utf8JSONResponse
    from .. import visual_launch
    import asyncio

    hid = (harness_id or "").strip()
    if not hid:
        return Utf8JSONResponse(
            {"error": "得先说是**哪一位员工**（带 harness_id）—— 候选关键词是从它的档案和线索里推的"},
            status_code=400)
    sess = _find_harness(hid)
    if sess is None:
        return Utf8JSONResponse({"error": "没有这个 harness（可能已注销或 id 写错）"}, status_code=404)
    dump = sess.info.model_dump()
    hints = dict((dump.get("metadata") or {}).get("probe_hints") or {})

    process_names: list[str] = []
    window_titles: list[str] = []
    supported = True
    probe_note = "（未扫本机：probe=0）"
    notes: list[str] = []
    if int(probe or 0):
        needles = [v for v in (hints.get("process_hint"), hints.get("path_hint"))
                   if str(v or "").strip()]
        if needles:
            def _scan():
                from .. import sysprobe
                return sysprobe.find_processes(needles, limit=8)
            try:
                for row in (await asyncio.to_thread(_scan) or []):
                    nm = str((row or {}).get("name") or "").strip()
                    if nm and nm not in process_names:
                        process_names.append(nm)
            except Exception as e:  # noqa: BLE001
                notes.append("本机进程没扫成：%s（不影响下面的候选）" % str(e)[:80])
        res = await asyncio.to_thread(visual_launch.list_windows, "", fast=True)
        supported = bool(res.get("supported"))
        probe_note = str(res.get("detail") or "")
        window_titles = [str(w.get("title") or "") for w in (res.get("windows") or [])]

    sugs = suggest_keywords(dump, probe_hints=hints,
                            session_names=_session_names_for(hid),
                            process_names=process_names,
                            window_titles=window_titles)
    if not sugs:
        notes.append("档案和你给过的线索里都推不出候选 ⇒ 直接手填一个"
                     "**会出现在那个窗口标题里**的字（它窗口上写着的那个名字最准）")
    if not window_titles and not supported:
        notes.append("这台机器上平台看不到窗口列表 ⇒ 关键词只能手填；"
                     "消息式注入是 Windows 专有，其它平台走剪贴板兜底档（如实说）")
    return {
        "harness_id": hid,
        "suggestions": sugs,
        "windows_scanned": len(window_titles),
        "supported": supported,        # 能不能扫窗口（探不到 ≠ 真没有，见 probe_note）
        "probe_note": probe_note,
        "notes": notes,
        "checked_at": _now_iso(),
    }


def render_script_export(info: dict, script: dict, *, generated_at: str) -> str:
    """**纯函数**：把档案里那份脚本正文渲染成**给人看的 `.ps1` 副本**（`FE-26` §11.2）。

    **只是副本**：真相在档案的 `activation_script` 里（`protocol.py` 的字段注释原话就是
    "text 是唯一真相，导出只是副本"）⇒ 注释头必须写明这一点，免得有人拿副本当正本改。

    三态**如实**：`verified` 不是 `True` 就写"⚠ 未核实"，**绝不写"已完成"**（准则⑤）。
    含中文 ⇒ **带 UTF-8 BOM**（PS 5.1 无 BOM 时按 GBK 读 ⇒ ParserError，本项目已有同款事故）。
    """
    info = dict(info or {})
    script = dict(script or {})
    text = str(script.get("text") or "")
    kws = [str(k) for k in (script.get("keywords") or []) if str(k).strip()]
    state = ("✓ 已自证（上次试投读回一致）" if script.get("verified") is True
             else "⚠ 未核实（还没自证过 —— 别当成已验证）")
    lines = [
        "# ⚠ 这是**副本**，不是真相：真相在平台的 harness 档案里（activation_script）。",
        "#   在平台上改了脚本，这份副本不会跟着变 —— 要新的就重新导出。",
        "# 员工：%s（harness_id=%s）" % (info.get("harness_name") or "", info.get("harness_id") or ""),
        "# 走的档：%s" % (script.get("method") or "（档案里没写）"),
        "# 定位关键词：%s" % ("、".join(kws) if kws else "（档案里没写）"),
        "# 上次试投：%s" % state,
        "# 导出时间：%s" % generated_at,
        "# 怎么用：把下面正文按你机器上的实际情况确认/改好，再用 PowerShell 执行；",
        "#   平台侧的等价动作在对接中心的**脚本激活**工作台（用的是档案里同一份参数）。",
        "",
        text,
    ]
    body = "\n".join(lines)
    if not body.endswith("\n"):
        body += "\n"
    if any(ord(ch) > 127 for ch in body):
        body = "\ufeff" + body
    return body


@router.get("/api/docking/harness/{harness_id}/export.ps1")
async def docking_harness_export_ps1(harness_id: str):
    """**只读**：把员工档案里的视觉激活脚本导出成 `.ps1` 副本（`FE-26` §11.2）。

    ⚠️ **副本，不是第二份真相**：正文＝`activation_script.text` 的**原样副本** ＋ 一段注释头
    （说明这是副本、属于谁、走哪一档、上次试投的三态、导出时间）。
    **没存过脚本** ⇒ 404 ＋ 中文 error **并指出下一步去哪做**（不许给"下载了个空文件"的死状态）。
    """
    from fastapi.responses import Response
    from ..server import Utf8JSONResponse

    sess = _find_harness(harness_id)
    if sess is None:
        return Utf8JSONResponse({"error": "没有这个 harness（可能已注销或 id 写错）"}, status_code=404)
    dump = sess.info.model_dump()
    script = dict(dump.get("activation_script") or {})
    if not str(script.get("text") or "").strip():
        return Utf8JSONResponse(
            {"error": "这位员工还没有存过激活脚本，没有副本可导 —— 先去「脚本激活」工作台做一份并保存"
                      "（填关键词 → 让平台去找 → 确认试投 → 保存为它的激活方式），之后这里就能导出了"},
            status_code=404)
    body = render_script_export(dump, script, generated_at=_now_iso())
    raw = "activation_%s.ps1" % (harness_id or "harness")
    real = re.sub(r'[\\/:*?"<>|\s]+', "_", raw) or "activation.ps1"
    ascii_name = re.sub(r"[^0-9A-Za-z_.-]+", "_", raw) or "activation.ps1"
    return Response(
        content=body.encode("utf-8"),
        media_type="text/plain; charset=utf-8",
        headers={
            "Content-Disposition": "attachment; filename=\"%s\"; filename*=UTF-8''%s"
                                   % (ascii_name, quote(real)),
            "Cache-Control": "no-store",
        },
    )


# ══════════════════════════════════════════════════════════════════════════
# FE-26 §12（2026-10-16）：激活脚本的「**正文从哪来**」与「**一键跑**」
#
# 为什么补（前端**正在外包开发中**）：§3.2 屏 4 要「显示脚本正文（可见/可复制/可手改）」并
# 「保存为该员工的激活方式 ⇒ 之后可一键脚本激活」，而 §5 只给了 候选/定位/试投/档案读写/导出。
# **前端不能自己编一套正文** —— 那会与平台真实的激活路径不一致（同一个词两套含义，规则 20 禁止）。
#
# 边界（§3.3，不变）：**生成**＝平台可以自动做（确定性、不落盘）；
# **试投 / 保存 / 一键跑**＝必须**用户点**（一次一按，结果如实回报）。
# ══════════════════════════════════════════════════════════════════════════

#: 档案里没记 `message`/`activation_message` 时用的**默认激活话术**（一处真相）。
#: 老板 2026-10-24 规格：「粘贴固定话术**提醒检查邮箱**」—— 原文他没逐字给，
#: 这里按"提醒查邮箱 + 请它回一句确认"写成一句；要改只改这一处（档案里可覆盖）。
DEFAULT_ACTIVATION_MESSAGE = ("【生产合作社】有新任务，请回大厅查看邮箱；"
                              "看到这条请回一句「收到」确认你在。")


def _ps_quote(text: str) -> str:
    """PowerShell **单引号字符串**字面量（里面的单引号要写成两个）。"""
    return "'" + str(text or "").replace("'", "''") + "'"


def default_activation_message() -> str:
    return DEFAULT_ACTIVATION_MESSAGE


LOCAL_SCRIPT_TMPL = r'''# ===== 视觉激活脚本（对接中心生成 · FE-26 §12 · **本机执行版**）=====
# 为什么是"本机执行"：有些 Electron 发行版**拒绝**开调试端口（CDP 走不通），
# 而让后台服务去抢前台/注入并不可靠；**你在自己的会话里跑这份脚本**才有桌面权限。
#
# 它做什么：
#   ① 按下面的标题/关键词找窗口；② 把它抬到最前；
#   ③ 投递话术：有原生文本框 ⇒ 直接 WM_PASTE 到控件；没有 ⇒ 抬到最前 + 剪贴板 + Ctrl+V；
#   ④ **能读回就核实**（WM_GETTEXT），读不回就如实说"未核实"，并**非零退出**（不替你报成功）。
#
# 走哪一档：__METHOD__
# 定位关键词：__KEYWORDS__
# 目标窗口标题：__TITLE__
# 激活话术：__MESSAGE__
# 上次平台试投：__STATE__
# 生成时间：__GENERATED_AT__
# 退出码：0=已核实 / 3=未核实（送出了但读不回）/ 4=没找到窗口 / 5=没走到前台（没投递）/ 6=其它错误
# 纪律：与平台内部注入同一套 Win32 手法（SendInput / WM_PASTE / WM_GETTEXT）；
#   平台侧对应实现在 agent_community\tools\inject_window.ps1。

param(
    [string]$TitleLike = __TITLE_LIT__,
    [string[]]$Keywords = __KEYWORDS_LIT__,
    [string]$Text = __MESSAGE_LIT__,
    [ValidateSet("auto", "clipboard")]
    [string]$Method = "__METHOD__",
    [switch]$Enter,
    [switch]$List,
    # 在窗口客户区的**相对位置**先点一下输入框（0..1）。Electron/浏览器类窗口里
    # Ctrl+V 只打到"有输入焦点的元素"，窗口在前台 ≠ 焦点在输入框 ⇒ 必须先点。
    [double]$ClickRelX = __CLICK_X__,
    [double]$ClickRelY = __CLICK_Y__
)
$ErrorActionPreference = 'Stop'
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}
# ★必须让 PowerShell **自己**声明 DPI 感知（2026-10-23 实测抓到）：
#   不声明时 GetClientRect / ClientToScreen 给的是"缩放后的虚拟坐标"，而 SetCursorPos 吃物理像素
#   ⇒ 在 125%/150% 缩放的屏上会点偏（表现就是"点了、Ctrl+V 也按了，字却没进输入框"）。
try {
    Add-Type -MemberDefinition '[DllImport("user32.dll")] public static extern bool SetProcessDPIAware();' -Name Dpi -Namespace Win32 -ErrorAction SilentlyContinue | Out-Null
    [Win32.Dpi]::SetProcessDPIAware() | Out-Null
} catch { }

Add-Type -TypeDefinition @"
using System;
using System.Text;
using System.Runtime.InteropServices;
public class WinAct {
    public delegate bool EnumWindowsProc(IntPtr hWnd, IntPtr lParam);
    [DllImport("user32.dll")] public static extern bool EnumWindows(EnumWindowsProc cb, IntPtr p);
    [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern int GetWindowText(IntPtr h, StringBuilder s, int n);
    [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern int GetClassName(IntPtr h, StringBuilder s, int n);
    [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
    [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
    [DllImport("user32.dll")] public static extern bool BringWindowToTop(IntPtr h);
    [DllImport("user32.dll")] public static extern bool ShowWindowAsync(IntPtr h, int c);
    [DllImport("user32.dll")] public static extern void SwitchToThisWindow(IntPtr h, bool fAltTab);
    [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
    [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
    [DllImport("user32.dll")] public static extern bool EnumChildWindows(IntPtr p, EnumWindowsProc cb, IntPtr l);
    [DllImport("user32.dll", CharSet=CharSet.Unicode, EntryPoint="SendMessageW")] public static extern IntPtr SendMessageS(IntPtr h, uint m, IntPtr w, StringBuilder l);
    [DllImport("user32.dll", CharSet=CharSet.Unicode, EntryPoint="SendMessageW")] public static extern IntPtr SendMessageI(IntPtr h, uint m, IntPtr w, IntPtr l);
    [DllImport("user32.dll")] public static extern void keybd_event(byte vk, byte scan, uint flags, UIntPtr extra);
    [DllImport("user32.dll")] public static extern uint SendInput(uint n, INPUT[] inputs, int size);
    [DllImport("user32.dll")] public static extern bool GetClientRect(IntPtr h, out RECT r);
    [DllImport("user32.dll")] public static extern bool ClientToScreen(IntPtr h, ref POINT p);
    [DllImport("user32.dll")] public static extern bool SetCursorPos(int x, int y);
    [DllImport("user32.dll")] public static extern void mouse_event(uint flags, uint dx, uint dy, uint data, UIntPtr extra);
    [StructLayout(LayoutKind.Sequential)] public struct POINT { public int X, Y; }
    [StructLayout(LayoutKind.Sequential)] public struct RECT { public int Left, Top, Right, Bottom; }
    [StructLayout(LayoutKind.Sequential)] public struct KEYBDINPUT { public ushort wVk, wScan; public uint dwFlags, time; public IntPtr extra; }
    [StructLayout(LayoutKind.Explicit)] public struct INPUTUNION { [FieldOffset(0)] public KEYBDINPUT ki; }
    [StructLayout(LayoutKind.Sequential)] public struct INPUT { public uint type; public INPUTUNION u; }
}
"@

function Get-Windows {
    $out = New-Object System.Collections.ArrayList
    $cb = [WinAct+EnumWindowsProc]{
        param($h, $p)
        if (-not [WinAct]::IsWindowVisible($h)) { return $true }
        $t = New-Object System.Text.StringBuilder 512
        [void][WinAct]::GetWindowText($h, $t, $t.Capacity)
        if ($t.Length -eq 0) { return $true }
        $r = New-Object WinAct+RECT
        $area = 0
        if ([WinAct]::GetWindowRect($h, [ref]$r)) { $area = ([int64]($r.Right-$r.Left)) * ([int64]($r.Bottom-$r.Top)) }
        [void]$out.Add([pscustomobject]@{ hwnd = [int64]$h; title = $t.ToString(); area = $area })
        return $true
    }
    [void][WinAct]::EnumWindows($cb, [IntPtr]::Zero)
    return $out
}
function Send-Unicode([string]$s) {
    $items = New-Object System.Collections.Generic.List[WinAct+INPUT]
    foreach ($ch in $s.ToCharArray()) {
        $code = [int]$ch
        $d = New-Object WinAct+INPUT; $d.type = 1; $d.u.ki.wScan = [uint16]$code; $d.u.ki.dwFlags = 4
        $u = New-Object WinAct+INPUT; $u.type = 1; $u.u.ki.wScan = [uint16]$code; $u.u.ki.dwFlags = 6
        $items.Add($d); $items.Add($u)
    }
    if ($items.Count -gt 0) {
        [void][WinAct]::SendInput($items.Count, $items.ToArray(), [System.Runtime.InteropServices.Marshal]::SizeOf([type][WinAct+INPUT]))
    }
}
function Send-Keys([byte]$vk) {
    [WinAct]::keybd_event($vk, 0, 0, [UIntPtr]::Zero); [WinAct]::keybd_event($vk, 0, 2, [UIntPtr]::Zero)
}
# 在**内容区**的相对位置点一下（0..1）。为什么需要它（2026-10-23 老板实测）：
#   Electron/浏览器类窗口里，Ctrl+V 只打到"当前有输入焦点的元素"上；**窗口在前台 ≠ 焦点在输入框**。
#   实测：Trae 前台了、Ctrl+V 也按下去了，词没进聊天框 —— 焦点根本不在聊天输入框里。
#   ★另外两个坑（同一轮排查抓到，都写进这里了）：
#     ① 坐标帧必须是**内容区**（渲染控件），不是整个窗口 —— 后者含标题栏/工具栏，点下去会点到地址栏；
#     ② 点之前必须**自证**这个点现在落在目标窗口上 —— 窗口被别的窗口挡住时，点了等于点到别人家程序。
function Get-ContentRect([int64]$hwnd) {
    # 优先类名含 RenderWidgetHost / Internet Explorer_Server / MozillaWindowClass 的子窗口；否则取最大子窗口
    $script:pickRect = $null
    $script:pickArea = 0
    $script:pickRender = $null
    $cbC = [WinAct+EnumWindowsProc]{
        param($h, $p)
        if (-not [WinAct]::IsWindowVisible($h)) { return $true }
        $c = New-Object System.Text.StringBuilder 256
        [void][WinAct]::GetClassName($h, $c, $c.Capacity)
        $name = $c.ToString()
        $r = New-Object WinAct+RECT
        if ([WinAct]::GetWindowRect($h, [ref]$r)) {
            $w = $r.Right - $r.Left; $hh = $r.Bottom - $r.Top
            if ($w -gt 60 -and $hh -gt 60) {
                if ($name -match 'RenderWidgetHost|Internet Explorer_Server|MozillaWindowClass') { $script:pickRender = [IntPtr]$h }
                $a = [int64]$w * [int64]$hh
                if ($a -gt $script:pickArea) { $script:pickArea = $a; $script:pickRect = $h }
            }
        }
        return $true
    }
    [void][WinAct]::EnumChildWindows([IntPtr]$hwnd, $cbC, [IntPtr]::Zero)
    $pick = $(if ($script:pickRender) { $script:pickRender } elseif ($script:pickRect) { $script:pickRect } else { $null })
    if ($pick) {
        $r = New-Object WinAct+RECT
        if ([WinAct]::GetWindowRect($pick, [ref]$r)) {
            return [pscustomobject]@{ Left = $r.Left; Top = $r.Top; W = ($r.Right - $r.Left); H = ($r.Bottom - $r.Top) }
        }
    }
    # 退路：窗口客户区
    $rc = New-Object WinAct+RECT
    if ([WinAct]::GetClientRect([IntPtr]$hwnd, [ref]$rc)) {
        $pt = New-Object WinAct+POINT
        if ([WinAct]::ClientToScreen([IntPtr]$hwnd, [ref]$pt)) {
            return [pscustomobject]@{ Left = $pt.X; Top = $pt.Y; W = ($rc.Right - $rc.Left); H = ($rc.Bottom - $rc.Top) }
        }
    }
    return $null
}

function Click-Rel([int64]$hwnd, [double]$rx, [double]$ry) {
    $rect = Get-ContentRect $hwnd
    if (-not $rect -or $rect.W -le 0 -or $rect.H -le 0) { return @{ ok = $false; why = "拿不到内容区矩形" } }
    $x = $rect.Left + [int][math]::Round($rect.W * $rx)
    $y = $rect.Top + [int][math]::Round($rect.H * $ry)
    # 点前自证：这个点现在是不是落在**目标窗口**上（顶层祖先 == 目标 hwnd）
    $hit = [WinAct]::WindowFromPoint((New-Object WinAct+POINT -Property @{ X = $x; Y = $y }))
    if (-not $hit) { return @{ ok = $false; why = "点($x,$y)上没有窗口" } }
    $root = [WinAct]::GetAncestor($hit, 2)
    if ([int64]$root -ne [int64]$hwnd) {
        return @{ ok = $false; why = "点($x,$y)落在**别的窗口**上（目标被挡住/没真到前台）—— 为不误伤别人程序，**没有点击**" }
    }
    [void][WinAct]::SetCursorPos($x, $y)
    Start-Sleep -Milliseconds 90
    [WinAct]::mouse_event(0x0002, 0, 0, 0, [UIntPtr]::Zero)   # 按下
    Start-Sleep -Milliseconds 60
    [WinAct]::mouse_event(0x0004, 0, 0, 0, [UIntPtr]::Zero)   # 抬起
    Start-Sleep -Milliseconds 150
    return @{ ok = $true; x = $x; y = $y }
}

# ① 找窗口：先按标题，再按关键词；同名取**面积最大**（主窗口）
$all = Get-Windows
if ($List) {
    $all | Sort-Object -Property area -Descending | Select-Object -First 25 | ForEach-Object { "[window] {0,10}  {1}" -f $_.hwnd, $_.title }
    exit 0
}
$cands = @($all | Where-Object { $TitleLike -and $_.title -like "*$TitleLike*" })
if (@($cands).Count -eq 0) {
    foreach ($k in $Keywords) {
        if (-not $k) { continue }
        $cands = @($all | Where-Object { $_.title -like "*$k*" })
        if (@($cands).Count -gt 0) { break }
    }
}
if (@($cands).Count -eq 0) {
    Write-Output "[FAIL] 没找到窗口（标题=$TitleLike 关键词=$($Keywords -join ',')）；用 -List 看现有窗口"
    exit 4
}
$win = ($cands | Sort-Object -Property area -Descending)[0]
Write-Output ("[target] hwnd={0} title={1}" -f $win.hwnd, $win.title)

# ② 抬到最前（Alt 点一下拿前台许可，再 SwitchToThisWindow + SetForegroundWindow）
[WinAct]::keybd_event(0x12, 0, 0, [UIntPtr]::Zero); [WinAct]::keybd_event(0x12, 0, 2, [UIntPtr]::Zero)
[void][WinAct]::ShowWindowAsync([IntPtr]$win.hwnd, 9)
[void][WinAct]::BringWindowToTop([IntPtr]$win.hwnd)
[void][WinAct]::SwitchToThisWindow([IntPtr]$win.hwnd, $true)
[void][WinAct]::SetForegroundWindow([IntPtr]$win.hwnd)
Start-Sleep -Milliseconds 250
if ([int64][WinAct]::GetForegroundWindow() -ne $win.hwnd) {
    [void][WinAct]::SetForegroundWindow([IntPtr]$win.hwnd)
    Start-Sleep -Milliseconds 200
}
$fg = [int64][WinAct]::GetForegroundWindow()
Write-Output ("[focus] is_foreground={0}" -f ($fg -eq $win.hwnd))

# ③ 投递
#    先看这个窗口里有没有**原生文本框**（WinForms/Win32 的 EDIT / RichEdit）：
#    有 ⇒ 直接 WM_PASTE 到那个控件（不经键盘、也不需要它在前台），随后读回核对；
#    没有（Electron/浏览器类）⇒ 只能抬到最前 + 剪贴板 + Ctrl+V，且**只能未核实**。
$script:editHwnd = [int64]0
$cbFind = [WinAct+EnumWindowsProc]{
    param($h, $p)
    $c = New-Object System.Text.StringBuilder 256
    [void][WinAct]::GetClassName($h, $c, $c.Capacity)
    if ($c.ToString() -match '(?i)edit') { $script:editHwnd = [int64]$h; return $false }
    return $true
}
[void][WinAct]::EnumChildWindows([IntPtr]$win.hwnd, $cbFind, [IntPtr]::Zero)

if ($script:editHwnd -ne 0 -and $Method -ne "clipboard") {
    try { Set-Clipboard -Value $Text -ErrorAction Stop } catch {
        Write-Output ("[FAIL] 剪贴板写不进去：{0}" -f $_.Exception.Message); exit 6
    }
    Start-Sleep -Milliseconds 120
    [void][WinAct]::SendMessageI([IntPtr]$script:editHwnd, 0x0302, [IntPtr]::Zero, [IntPtr]::Zero)  # WM_PASTE
    Start-Sleep -Milliseconds 200
    Write-Output ("[inject] WM_PASTE -> 原生文本框（{0} 字）" -f $Text.Length)
} else {
    if ($fg -ne $win.hwnd) {
        Write-Output "[FAIL] 没走到前台 -> **没有投递**（否则会打进别的窗口）；先手动点一下目标窗口再跑一次"
        exit 5
    }
    # ★关键一步（2026-10-23 老板实测）：Electron/浏览器类窗口，**窗口在前台 ≠ 焦点在输入框**。
    #   只按 Ctrl+V 会打到"当前有焦点的元素"上（很多时候是空处/编辑器），词就落空。
    #   所以：给了相对位置就先点一下输入框；没给就**明说**"可能落空 + 怎么补"。
    if ($ClickRelX -ge 0 -and $ClickRelY -ge 0) {
        $clicked = Click-Rel $win.hwnd $ClickRelX $ClickRelY
        if (-not $clicked.ok) {
            # 点不到目标窗口就**不投**（否则等于把话打进别人程序里）—— 如实退出 5
            Write-Output ("[FAIL] 没点进去：{0}" -f $clicked.why)
            Write-Output "       先把目标窗口切到最前（别被别的窗口挡住），再跑一次。"
            exit 5
        }
        Write-Output ("[click] 内容区相对位置 ({0}, {1}) -> 已点在 ({2}, {3})（输入框应拿到焦点）" -f `
                      $ClickRelX, $ClickRelY, $clicked.x, $clicked.y)
        Start-Sleep -Milliseconds 120
    } else {
        Write-Output "[warn] 没有给输入框位置（-ClickRelX/-ClickRelY）：Electron 类窗口里 Ctrl+V 很可能落空；"
        Write-Output "       建议先「教一次位置」（平台里鼠标停在输入框上点一下），或手动点一下输入框再跑本脚本。"
    }
    try { Set-Clipboard -Value $Text -ErrorAction Stop } catch {
        Write-Output ("[FAIL] 剪贴板写不进去：{0}" -f $_.Exception.Message); exit 6
    }
    Start-Sleep -Milliseconds 150
    [WinAct]::keybd_event(0x11, 0, 0, [UIntPtr]::Zero); Send-Keys 0x56; [WinAct]::keybd_event(0x11, 0, 2, [UIntPtr]::Zero)
    Write-Output ("[inject] 剪贴板 + Ctrl+V（{0} 字）" -f $Text.Length)
}
if ($Enter) { Send-Keys 0x0D; Write-Output "[inject] Enter" }

# ④ 读回核对（只有原生文本框读得到；Electron 类读不回 ⇒ 如实说未核实）
$script:editHwnd = [int64]0
$cb2 = [WinAct+EnumWindowsProc]{
    param($h, $p)
    $c = New-Object System.Text.StringBuilder 256
    [void][WinAct]::GetClassName($h, $c, $c.Capacity)
    if ($c.ToString() -match '(?i)edit') { $script:editHwnd = [int64]$h; return $false }
    return $true
}
[void][WinAct]::EnumChildWindows([IntPtr]$win.hwnd, $cb2, [IntPtr]::Zero)
if ($script:editHwnd -ne 0) {
    $sb = New-Object System.Text.StringBuilder 8192
    [void][WinAct]::SendMessageS([IntPtr]$script:editHwnd, 0x000D, [IntPtr]8192, $sb)
    $got = $sb.ToString()
    if ($got.Contains($Text)) { Write-Output ("[verify] 读回核对通过（{0} 字）" -f $got.Length); exit 0 }
    Write-Output ("[verify] 读回不一致（{0} 字）—— 内容没进去或进了别处" -f $got.Length)
    exit 3
}
Write-Output "[verify] 这个窗口没有系统可读的输入框（Electron/浏览器类常见）—— **未核实**；请看一眼目标窗口里有没有出现这段话"
exit 3
'''


def compose_activation_script(*, keywords, title: str = "", method: str = "", message: str = "",
                              harness_id: str = "", verified=None, saved_at: str = "",
                              default_port=None, generated_at: str = "", anchor=None) -> dict:
    """**纯函数**：把"定位结果 + 档位"落成一份**可复现、可本机执行**的 PowerShell 正文。

    2026-10-23 改口径（老板：「那就得视觉脚本」）：以前正文是"转调平台 `try-activate`"，
    真正干活的是**后台服务**，它对前台锁/交互桌面没把握（实测 Electron 类常抢不到前台）。
    现在正文改成**在你自己的会话里干活**：找窗口 → 抬到最前 → 键入/粘贴 → 读回核对；
    平台只负责生成与记录。既不依赖 CDP（有的应用直接拒绝开端口），也不靠后台抢前台。

    正文要点（都写进注释，让人看得懂、改得动）：
      ① 只按标题/关键词找窗口，**不写死任何路径/端口**（也不依赖平台在跑）；
      ② 抬到最前才投递；**没走到前台就 exit 5，绝不盲投**（盲投会把话打进别的程序）；
      ③ 两档：有原生文本框 ⇒ `WM_PASTE` 到控件（可核实）；
        没有 ⇒ 抬到最前 + 剪贴板 + Ctrl+V（**只能未核实**）。`method="clipboard"` 可强制只走后者；
      ④ 能读回就核实（WM_GETTEXT）；读不回如实说"未核实"并 **exit 3**（不替你报成功）。
    """
    kws = [str(k).strip() for k in (keywords or []) if str(k).strip()]
    requested = str(method or "").strip() or "auto"          # 档案里记的那一档（原样回传）
    # 脚本内部的"档"只有两种：auto（先试 WM_PASTE）/ clipboard（只走剪贴板）
    tier = "clipboard" if requested == "clipboard" else "auto"
    msg = str(message or "").strip() or DEFAULT_ACTIVATION_MESSAGE
    title = str(title or "").strip()
    state = ("✓ 已自证（上次试投读回一致）" if verified is True
             else "⚠ 未核实（还没自证过 —— 别把这份正文当成已经验证过）")

    ax = ay = None
    # 没教过位置时：让**视觉**估一次（平台在生成脚本这一刻算好，写进默认参数；
    # 运行脚本时不需要 Python，也就还是"自己一个人就能跑"的本机脚本）。
    if not (isinstance(anchor, dict) and anchor.get("rel_x") is not None):
        try:
            from .. import screen_vision as _sv
            from .. import visual_launch as _vl

            wins = [w for w in (_vl.list_windows(fast=True).get("windows") or [])
                    if (title and title.lower() in (w.get("title") or "").lower())
                    or (not title and kws and any(k.lower() in (w.get("title") or "").lower()
                                                  for k in kws))]
            pick = _vl.pick_biggest(wins)          # 按真实尺寸挑，别挑到 93x150 的影子窗口
            if pick:
                g = _sv.guess_input_anchor(int(pick["hwnd"]))
                if g.get("ok"):
                    anchor = {"rel_x": g["rel_x"], "rel_y": g["rel_y"],
                              "source": "auto-vision(%.2f)" % float(g.get("score") or 0)}
        except Exception as e:                                # noqa: BLE001 — 估不出来就留 -1
            print("[compose] 视觉估位失败（脚本留 -1，运行时不会点）：%s: %s"
                  % (type(e).__name__, e), flush=True)
    if isinstance(anchor, dict) and anchor.get("rel_x") is not None and anchor.get("rel_y") is not None:
        try:
            ax, ay = float(anchor["rel_x"]), float(anchor["rel_y"])
        except (TypeError, ValueError):
            ax = ay = None

    def q(v: str) -> str:
        return "'" + str(v).replace("'", "''") + "'"

    body = LOCAL_SCRIPT_TMPL
    for k, v in (("__METHOD__", tier),
                 ("__KEYWORDS__", "、".join(kws) if kws else "（没给关键词 —— 只能按标题匹配）"),
                 ("__TITLE__", title or "（没给标题 —— 由关键词匹配）"),
                 ("__MESSAGE__", msg), ("__STATE__", state),
                 ("__GENERATED_AT__", generated_at or ""),
                 ("__TITLE_LIT__", q(title)),
                 ("__KEYWORDS_LIT__", "@(" + ", ".join(q(k) for k in kws) + ")"),
                 ("__MESSAGE_LIT__", q(msg)),
                 # 教过的输入框位置（相对客户区 0..1）：给了就**先点一下再粘贴**
                 ("__CLICK_X__", ("%.4f" % float(ax)) if ax is not None else "-1"),
                 ("__CLICK_Y__", ("%.4f" % float(ay)) if ay is not None else "-1")):
        body = body.replace(k, v)
    return {
        "text": body if body.endswith("\n") else body + "\n",
        "method": requested,          # 档案里记的那一档（原样回传，不替它改口）
        "tier": tier,                 # 脚本内部实际走的档：auto / clipboard
        "keywords": kws,
        "title": title,
        "message": msg,
        "verified": verified,
        "saved_at": saved_at or "",
        "harness_id": harness_id,
        "source": "local-script",
    }


def render_script_export(info: dict, script: dict, *, generated_at: str) -> str:
    """**纯函数**：把档案里那份脚本正文渲染成**给人看的 `.ps1` 副本**（`FE-26` §11.2）。

    **只是副本**：真相在档案的 `activation_script` 里（`protocol.py` 的字段注释原话就是
    "text 是唯一真相，导出只是副本"）⇒ 注释头必须写明这一点，免得有人拿副本当正本改。

    三态**如实**：`verified` 不是 `True` 就写"⚠ 未核实"，**绝不写"已完成"**（准则⑤）。
    含中文 ⇒ **带 UTF-8 BOM**（PS 5.1 无 BOM 时按 GBK 读 ⇒ ParserError，本项目已有同款事故）。
    """
    info = dict(info or {})
    script = dict(script or {})
    text = str(script.get("text") or "")
    kws = [str(k) for k in (script.get("keywords") or []) if str(k).strip()]
    state = ("✓ 已自证（上次试投读回一致）" if script.get("verified") is True
             else "⚠ 未核实（还没自证过 —— 别当成已验证）")
    lines = [
        "# ⚠ 这是**副本**，不是真相：真相在平台的 harness 档案里（activation_script）。",
        "#   在平台上改了脚本，这份副本不会跟着变 —— 要新的就重新导出。",
        "# 员工：%s（harness_id=%s）" % (info.get("harness_name") or "", info.get("harness_id") or ""),
        "# 走的档：%s" % (script.get("method") or "（档案里没写）"),
        "# 定位关键词：%s" % ("、".join(kws) if kws else "（档案里没写）"),
        "# 上次试投：%s" % state,
        "# 导出时间：%s" % generated_at,
        "# 怎么用：把下面正文按你机器上的实际情况确认/改好，再用 PowerShell 执行；",
        "#   平台侧的等价动作在对接中心的**脚本激活**工作台（用的是档案里同一份参数）。",
        "",
        text,
    ]
    body = "\n".join(lines)
    if not body.endswith("\n"):
        body += "\n"
    if any(ord(ch) > 127 for ch in body):
        body = "\ufeff" + body
    return body


def _ps_quote(text: str) -> str:
    """PowerShell **单引号字符串**字面量（里面的单引号要写成两个）。"""
    return "'" + str(text or "").replace("'", "''") + "'"


def default_activation_message() -> str:
    return DEFAULT_ACTIVATION_MESSAGE


def _pin_method(method: str) -> str:
    """把档位**钉死成具体方法**（`auto` 一律翻译掉）。

    ⚠️ 为什么必须钉：`auto` 在 wmpaste 失败后**自动退剪贴板**，而剪贴板**总是成功**
    ⇒ 会把"这个窗口根本没找到"报成成功（2026-10-15 走查抓到的**假成功**，准则⑤）。
    """
    import os as _os
    m = str(method or "").strip()
    if m == "auto" or not m:
        m = "wmpaste" if _os.name == "nt" else "clipboard"
    return m


@router.post("/api/docking/compose-script")
async def docking_compose_script(request: Request):
    """**只读**：生成激活脚本正文（`FE-26` §12.1）—— **确定性、可复现、不落盘**。

    body: `{keywords:[…], title?, method?, text?, harness_id?}`
    → `{ok, script:{text, method, keywords, title, message, verified, source, saved_at}, notes[], checked_at}`
    """
    from ..server import Utf8JSONResponse

    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        body = {}
    keywords = [str(k).strip() for k in (body.get("keywords") or []) if str(k).strip()]
    title = str(body.get("title") or "").strip()
    if not keywords and not title:
        return Utf8JSONResponse(
            {"error": "得先给我**定位结果**：一个关键词或一个窗口标题（屏 2/3 点出来的那个）"},
            status_code=400)
    hid = str(body.get("harness_id") or "").strip()
    verified, saved_at = None, ""
    if hid:
        sess = _find_harness(hid)
        if sess is None:
            return Utf8JSONResponse({"error": "没有这个 harness（可能已注销或 id 写错）"}, status_code=404)
        old = dict(getattr(sess.info, "activation_script", None) or {})
        verified, saved_at = old.get("verified"), str(old.get("saved_at") or "")

    from ...platform_address import resolve_port
    try:
        port = resolve_port()
    except Exception:  # noqa: BLE001
        port = ""
    # ★2026-10-23：把「教过的输入框位置」一起写进脚本 —— 否则 Electron 类窗口里
    #   窗口在前台、Ctrl+V 也按了，词却进不了聊天框（焦点不在输入框）。
    anchor = None
    if hid:
        try:
            from .. import input_anchor as _ia

            anchor = _ia.get(hid)
        except Exception:                                     # noqa: BLE001 — 读不到就当没教过（脚本会提示）
            anchor = None
    script = compose_activation_script(
        keywords=keywords, title=title, method=_pin_method(body.get("method")),
        message=str(body.get("text") or ""), harness_id=hid, verified=verified, saved_at=saved_at,
        default_port=port, generated_at=_now_iso(), anchor=anchor)
    notes = ["这段正文**没有落盘**：要它成为这位员工的激活方式，得让用户在屏 4 点「保存」。",
             "正文里的中文按 UTF-8 字节发送 —— 直接拿 Invoke-RestMethod 送中文会被 PS 5.1 换成 '?'。",
             "剪贴板兜底档**不等于**验证通过（它只把内容放进剪贴板，要人点一下窗口再粘贴）。"]
    if _pin_method(body.get("method")) == "clipboard":
        notes.append("当前钉的是剪贴板档：跑完仍需要人操作，`verified` 会是 null（未核实）。")
    return {"ok": True, "script": script, "notes": notes, "checked_at": _now_iso()}


@router.post("/api/docking/run-script")
async def docking_run_script(request: Request):
    """**一键脚本激活**（`FE-26` §12.2）：用**档案里已保存**的参数跑一次，结果**如实回报**。

    body: `{harness_id}` → `visual_launch.launch()` 的原样三态 ＋ `used:{method, keywords, title}`。
    **必须用户点**（与试投同一档纪律）；**钉住档案里记的方法**，不 auto 兜底（防假成功）。
    档案里没有脚本 ⇒ 404 ＋ 中文下一步。
    """
    from ..server import Utf8JSONResponse
    from .. import visual_launch
    import asyncio

    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        body = {}
    hid = str(body.get("harness_id") or "").strip()
    if not hid:
        return Utf8JSONResponse({"error": "得先说是**哪一位员工**（带 harness_id）"}, status_code=400)
    sess = _find_harness(hid)
    if sess is None:
        return Utf8JSONResponse({"error": "没有这个 harness（可能已注销或 id 写错）"}, status_code=404)
    script = dict(getattr(sess.info, "activation_script", None) or {})
    if not str(script.get("text") or "").strip():
        return Utf8JSONResponse(
            {"error": "这位员工还没有存过激活脚本，没有可跑的东西 —— 先去「脚本激活」工作台做一份并保存"
                      "（填关键词 → 让平台去找 → 确认试投 → 保存为它的激活方式）"},
            status_code=404)
    keywords = [str(k).strip() for k in (script.get("keywords") or []) if str(k).strip()]
    title = str(script.get("title") or "").strip()
    key = title or (keywords[0] if keywords else "")
    if not key:
        return Utf8JSONResponse(
            {"error": "这份脚本里既没有窗口标题也没有关键词 —— 平台不知道该找哪个窗口，"
                      "请回工作台补上再保存"}, status_code=400)
    method = _pin_method(script.get("method"))
    text = str(script.get("message") or "").strip() or DEFAULT_ACTIVATION_MESSAGE
    # ★2026-10-23（老板：禁止私人化…落地到通用框架）：一键激活**也走统一投递层**。
    #   为什么换：以前这里只认 `wmpaste/clipboard` 那一套（会抢焦点、对 Electron 无效、多半"未核实"）；
    #   现在按脚本里存的**标题/关键词**在四通道里自动选（cdp → 原生 → UIA → 半自动），
    #   能核实的就核实，核不了就如实说"未核实"。脚本里若钉了旧档（wmpaste/clipboard），
    #   只影响"要不要半自动"，不改变"能核实优先"的原则。
    from .. import delivery

    prefer = "clipboard" if method == "clipboard" else ""
    out = dict(await asyncio.to_thread(delivery.deliver_by_name, key, text, prefer=prefer))
    out["used"] = {"method": method, "channel": out.get("channel") or "",
                   "keywords": keywords, "title": title, "key": key}
    if out.get("ok") is not True:
        out["next_step"] = (out.get("next_step") or "") + (
            "　（一键激活走不通时，回「脚本激活」工作台重新定位/换一档；"
            "剪贴板兜底档只是把内容放好，**不等于**验证通过。）")
    out["checked_at"] = _now_iso()
    return out
