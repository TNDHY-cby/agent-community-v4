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

import types
from datetime import datetime, timezone

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
