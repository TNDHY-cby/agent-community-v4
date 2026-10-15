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
    if not keywords:
        return Utf8JSONResponse({"error": "keywords 不能为空 —— 给几个**会出现在那个窗口标题里**的字"}, status_code=400)
    res = await asyncio.to_thread(visual_launch.list_windows, "")
    wins = res.get("windows") or []
    cands = match_candidates(wins, keywords, body.get("limit") or 20)
    return {
        "ok": bool(res.get("ok")),
        "supported": bool(res.get("supported")),
        "keywords": keywords,
        "total_windows": len(wins),
        "candidates": cands,
        "probe_note": res.get("detail") or "",
        "checked_at": _now_iso(),
    }


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
    method = str(body.get("method") or "").strip() or ("wmpaste" if _os.name == "nt" else "clipboard")
    if method == "auto":
        method = "wmpaste" if _os.name == "nt" else "clipboard"
    res = await asyncio.to_thread(visual_launch.launch, None, text, method=method,
                                 session_name=key, verify=True)
    out = dict(res or {})
    out["key"] = key
    out["text"] = text
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
        res = await asyncio.to_thread(visual_launch.list_windows, "")
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
