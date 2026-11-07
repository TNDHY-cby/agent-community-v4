# -*- coding: utf-8 -*-
"""`platform/lead_capabilities`：**这个接入能干什么、缺什么、平台替它补什么**（设计 `V134` ②）。

## 为什么要有它

老板 2026-11-07：「**组长最适配，功能最全，所以可以用来带动不适配者**」。
但"最适配"必须先是个**能算出来的事实**，否则"带动"只能靠人记着 —— 换一个组长就得重新口头安排。

本模块把三处已有的真源合成一份能力面（**不新造状态、不手填**）：

| 能力 | 从哪来（唯一真源） |
|---|---|
| `can_write_files` | `lead_roster.tooling_of()` + `_merge_measured()`（声明工具 + 实测写过盘） |
| `lead_eligible` | `lead_roster.eligible()`（既有门槛，原样） |
| `can_dispatch` / `dispatch_via` | `protocol_plan.dispatch_route()`（能不能派活、走哪条路） |
| `can_be_visual` | `protocol_plan.script_ready()`（有没有可用视觉脚本） |
| `tools` | 注册信息里的工具名（只读展示） |

## 输出三层（界面/计划都读这一份，别各写一段人话）

1. `can_*`：能做/不能做（布尔，**测不到就是 null**，不猜）；
2. `gaps[]`：缺什么、为什么、**平台替它做什么**（补位建议）；
3. `note`：一句总口径 —— "组长负责判断，平台负责搬运与记账"。

## 边界

- **只读**：不写盘、不改档案；
- **不排名、不推荐**：与 `lead_roster` 同一纪律（`sort_note` 原话照抄）；
- **不碰冻结实现**（信誉 / 能力指纹 / 身份定位方案）。
"""
from __future__ import annotations

from . import lead_roster as _roster
from . import protocol_plan as _plan

#: 能读文件的工具名线索（只用来判断"它有没有读的能力"，匹配不到就是 null）
FS_READ_HINTS = ("read_file", "readfile", "cat", "view", "open_file", "fs_read",
                 "glob", "grep", "ls", "list_dir", "search")


def _tools_of(info) -> list:
    raw = getattr(info, "tools", None) or getattr(info, "capabilities", None) or []
    out = []
    for t in raw:
        name = str(getattr(t, "name", "") or t or "").strip()
        if name:
            out.append(name)
    return out


def _can_read_files(info, tool: dict) -> object:
    """能不能读文件：工具名线索命中 ⇒ True；一条工具都没报 ⇒ None（测不到）；报了但没有读类 ⇒ False。"""
    tools = [t.lower() for t in _tools_of(info)]
    if not tools:
        return None
    if any(h in t for t in tools for h in FS_READ_HINTS):
        return True
    # acp：平台是它的宿主，产物/文件由平台代读 → 视为能读（但不是它自己读）
    wake = str(tool.get("wakeup_method") or "")
    if wake == "acp":
        return True
    return False


def capabilities_of(info, *, harness_id: str = "") -> dict:
    """算一份能力面。`info` 可以是 None（那就全部 None + 说清原因）。"""
    hid = str(harness_id or getattr(info, "harness_id", "") or "")
    if info is None:
        return {
            "ok": True, "harness_id": hid, "known": False,
            "note": "拿不到这个接入的档案（可能已注销）—— 平台不猜它的能力",
            "can_write_files": None, "can_read_files": None, "can_receive_notice": None,
            "can_dispatch": None, "dispatch_via": "", "can_be_visual": None,
            "can_review": None, "lead_eligible": None, "gaps": [],
        }

    tool = _roster._merge_measured(hid, _roster.tooling_of(info))   # 既有真源（含实测覆盖）
    route, route_why = _plan.dispatch_route(info)
    visual_ok, visual_why = _plan.script_ready(info)
    fs_write = tool.get("fs_write")
    can_read = _can_read_files(info, tool)
    has_channel = bool(tool.get("protocol_call") or tool.get("bridge_poll"))
    lead_ok = bool(_roster.eligible(tool))

    # 收通知：能写文件 ⇒ 读得到 `_notice.md`；否则有视觉脚本 ⇒ 能投递；再否则只能走协议/人工
    if fs_write is True:
        can_notice = "file"
    elif visual_ok:
        can_notice = "visual"
    elif has_channel:
        can_notice = "protocol"
    else:
        can_notice = "none"

    # 能验收：要"能看产物"且"能把结论表达回来"。缺一样就由平台出结论 + 人复核。
    can_read_for_review = (can_read is True) or (fs_write is True)
    can_review = bool(can_read_for_review and (has_channel or visual_ok))

    # 收到回执 / 能不能自动唤醒（`protocol_fit` 要用；一处真源在 tool + 视觉脚本）
    can_recv = "yes" if has_channel else "no"
    can_wake_auto = bool(tool.get("protocol_call")) or bool(visual_ok)
    # 它声明了多少种协议（多协议＝"高适配"的直接体现）
    proto_ids = [str(x).strip().lower() for x in (getattr(info, "protocols", None) or [])
                 if str(x).strip()]
    for t in (getattr(info, "transports", None) or []):
        tv = str(getattr(t, "value", t) or "").strip().lower()
        if tv and tv not in proto_ids:
            proto_ids.append(tv)

    caps = {
        "ok": True, "known": True, "harness_id": hid,
        "harness_name": str(getattr(info, "harness_name", "") or hid),
        "tooling": tool,
        "lead_eligible": lead_ok,
        "can_write_files": fs_write,
        "can_read_files": can_read,
        "can_receive_notice": can_notice,
        "can_receive_result": can_recv,
        "can_wake_automatically": can_wake_auto,
        "can_dispatch": route != "none",
        "dispatch_via": route,
        "dispatching_why": route_why,
        "can_be_visual": bool(visual_ok),
        "visual_why": visual_why,
        "can_review": can_review,
        "protocols_declared": proto_ids,
        "protocol_count": len(proto_ids),
        "tools": _tools_of(info)[:40],
    }
    caps["gaps"] = _gaps(caps, tool=tool, route=route, visual_ok=visual_ok)
    caps["protocol_fit"] = protocol_fit(caps)
    caps["note"] = ("组长负责**判断与决定**，平台负责**搬运与记账**："
                    "下面 `gaps` 里每一条都是平台已经或应该替它做的事 —— 越不适配的接入，"
                    "平台要补的越多，但它不能因此被说成'什么都干不了'。"
                    "`protocol_fit` 说明它**适不适合当拍板人**（由事实算出，不排名）。")
    return caps


def protocol_fit(caps: dict) -> dict:
    """**协议适配度**（`V135`）：能不能当"拍板人"的组长。

    `high`   = 能派活 + 能收回执 + 能写文件 + 能读文件（四项全齐）；
    `medium` = 能派活 + 能收回执 + 能写文件（读不了文件 ⇒ 产物靠平台代读）；
    `low`    = 其余（含"测不到"：`None` 不算通过）—— 只能当助手/成员。
    """
    ch = bool(caps.get("can_dispatch")) and str(caps.get("can_receive_result") or "") == "yes"
    files = caps.get("can_write_files") is True
    read = caps.get("can_read_files") is True
    level = "high" if (ch and files and read) else ("medium" if (ch and files) else "low")
    reasons = []
    if not ch:
        reasons.append("没有「能派活 + 能收回执」的协议通道（现在是 %s）"
                       % (caps.get("dispatch_via") or "none"))
    if not files:
        reasons.append("写不了文件（或测不到）—— 任务文件与产物都要平台代写/代收")
    if not read:
        reasons.append("读不了文件 —— 产物/证据要靠平台代读，或让助手核对")
    return {"level": level, "reasons": reasons,
            "items": {"channel": ch, "files": files, "read": read,
                      "wake": bool(caps.get("can_wake_automatically")),
                      "visual": bool(caps.get("can_be_visual")),
                      "protocol_count": int(caps.get("protocol_count") or 0)},
            "note": ("`high` 才能当**主组长（拍板人）**；`medium` 可降级当、但响应会明说"
                     "『读不了文件，产物由平台代读』；`low` 只能当助手/成员（选它会 409 NOT_LEAD_FIT）。"
                     "**不按分数排序推荐**：平台只摆事实，人选。")}


def _gaps(caps: dict, *, tool: dict, route: str, visual_ok: bool) -> list:
    """缺什么 → 为什么 → 平台替它做什么（补位建议，一处真相）。"""
    gaps = []
    if caps["can_write_files"] is not True:
        gaps.append({
            "what": "写不了文件（或测不到）",
            "why": str(tool.get("why") or "注册时没报工具名单 / 报的工具里没有文件操作"),
            "platform_will": ("任务文件由平台代写；产物由平台到**收件目录**去收"
                              "（`V133`：档案 `artifact_inbox_dir` 或 `<工位>/产物/`）"),
        })
    if caps["can_read_files"] is not True:
        gaps.append({
            "what": "读不到文件内容",
            "why": "它没有读文件/检索类工具，平台也没有它的宿主权限",
            "platform_will": ("平台把**摘要**放进能送到它的通道（notice / 视觉投递 / 讨论区），"
                              "原文仍只在盘上；需要它读全文时，得让人转达或给它加读文件工具"),
        })
    if caps["can_receive_notice"] == "none":
        gaps.append({
            "what": "收不到任何通知",
            "why": "既不能写文件、也没有视觉脚本、也没有协议通道",
            "platform_will": "平台不假装通知到了 —— 事件只留在事件流与界面上，标『未送达』，请人转达",
        })
    if caps["can_receive_notice"] == "visual":
        gaps.append({
            "what": "只能靠视觉投递收通知",
            "why": "它有视觉脚本，但读不到 `_notice.md`",
            "platform_will": "平台把通知改成**投一句话**进它的窗口（`V132` send-text）；**投递≠它执行**",
        })
    if not caps["can_dispatch"]:
        gaps.append({
            "what": "平台不能给它派活",
            "why": str(caps.get("dispatching_why") or ""),
            "platform_will": ("派活由平台执行（写任务文件 + notice + 触达别人的通道），"
                              "或由组长/人转达；它只当'被安排'的一端"),
        })
    if caps["can_review"] is not True:
        gaps.append({
            "what": "它不能独立验收",
            "why": "看不了产物，或没法把结论表达回来",
            "platform_will": ("平台按**证据档**出结论（L0 扫盘 / sha256）+ 人复核；"
                              "账本记 `who=platform-review`，**不冒充它签的字**"),
        })
    if not visual_ok:
        gaps.append({
            "what": "没有可用视觉脚本（叫不醒的桌面型）",
            "why": str(caps.get("visual_why") or ""),
            "platform_will": "去「脚本激活」工作台做一份（标题/关键词 + 一句话）；做完平台就能自动投递",
        })
    return gaps


def of_workshop_lead(ws_id: str) -> dict:
    """算**这个工作间组长**的能力面（组长＝名单里 `slot_dir == "_lead"` 的成员）。"""
    from . import v5_roster as _v5

    mem = _v5.effective_members(ws_id)
    lead = None
    for m in mem.get("members") or []:
        if str((m or {}).get("slot_dir") or "").strip().strip("/") == "_lead":
            lead = m
            break
    if lead is None:
        return {"ok": False, "code": "NO_LEAD",
                "error": "这个工作间还没选组长（名单里没有 `_lead` 工位）",
                "next": "先 POST /api/workshop/{id}/lead 选一个；或 GET /api/lead-roster 看候选"}
    hid = str(lead.get("harness") or "")
    info = None
    try:
        from .server import harness_manager  # type: ignore[attr-defined]

        sess = (getattr(harness_manager, "sessions", {}) or {}).get(hid) if hid else None
        info = getattr(sess, "info", None) if sess is not None else None
    except Exception:                                          # noqa: BLE001 — 取不到档案就按"能力未知"回
        info = None
    out = capabilities_of(info, harness_id=hid)
    out["workshop_id"] = ws_id
    out["slot_dir"] = "_lead"
    out["member_seq"] = lead.get("member_seq")
    out["member_state"] = str(lead.get("state") or "")
    return out
