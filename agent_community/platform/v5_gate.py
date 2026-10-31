# -*- coding: utf-8 -*-
"""`platform/v5_gate`：**闸门只有一个函数**（设计 `V112` §4，P2 批）。

为什么只能有一个：上一版把"拦"散在十几个端点里，文案各写一套 —— 被拦住的人看不出缺谁、
缺哪项证据、去哪补。现在统一返回 `(ok, code, four)`，端点只负责把它拼成 409。

`four` 四要素（`V111` §5）：`who`（缺谁）/ `what`（缺哪项）/ `where`（去哪补）/ `how`（照做的一句话）。
"""
from __future__ import annotations

from . import v5_roster as _roster

ACTIONS = ("fill_direction", "select_lead", "start_modeling", "start_staffing",
           "start_running", "dispatch", "change_path")


def _four(who: str, what: str, where: str, how: str) -> dict:
    return {"who": who, "what": what, "where": where, "how": how}


def check(workshop, action: str, ctx: dict | None = None) -> tuple:
    """统一闸门。`workshop` 可以是工作间对象或 id；`ctx` 给需要额外参数的动作（如 dispatch）。"""
    ctx = ctx or {}
    wid = getattr(workshop, "workshop_id", None) or str(workshop or "")
    if action not in ACTIONS:
        return False, "BAD_ACTION", _four("调用方", "动作名不认识：%s" % action,
                                          "代码里", "只用 %s 之一" % "/".join(ACTIONS))

    if action == "fill_direction":
        brief = str(ctx.get("brief") or "").strip()
        enum = ctx.get("enum") or []
        if not brief or not enum:
            return False, "GATE_G1", _four("用户", "方向长文或方向下拉是空的", "工作间首页",
                                          "粘贴外部谈好的方向，并在下拉里选至少一个方向")
        return True, "", {}

    if action == "select_lead":
        return True, "", {}

    r = _roster.roster(wid)
    members = r["members"]
    active = [m for m in members if m.get("state") == "active"]
    lead = next((m for m in members if str(m.get("slot_dir", "")).endswith("组长")
                 or m.get("is_lead")), None)

    if action in ("start_modeling", "start_staffing"):
        if lead is None:
            return False, "GATE_G2", _four("组长", "还没有组长工位", "组长对话框",
                                           "选一个组长 → 复制对接单 → 粘贴给它 → 回来打勾")
        if lead.get("state") != "active":
            ev = lead.get("evidence") or {}
            need = [k for k in ("protocol_ok", "bridge_ok", "paste_seen", "press_ok", "rename_seen")
                    if not ev.get(k)]
            return False, "GATE_G2", _four(
                "组长 #%s" % lead.get("member_seq"), "证据还差：%s" % ("、".join(need) or "（齐了，但 verified_scope 没写）"),
                "对接中心 · 组长", "补齐证据并写一句 verified_scope（覆盖到哪一段）")
        return True, "", {}

    if action == "start_running":
        if not active:
            near = [m for m in members if m.get("state") in ("acked", "entered")]
            return False, "GATE_G4", _four(
                "用户", "一个 active 成员都没有（已接近的 %d 个）" % len(near), "对接中心",
                "逐个复制成员对接单 → 粘贴 → 打勾；攒齐证据后点「重新判定」")
        return True, "", {}

    if action == "dispatch":
        seq = int(ctx.get("member_seq") or 0)
        m = _roster.find_member(wid, seq) if seq else None
        if m is None:
            return False, "GATE_G5", _four("组长", "派活目标不存在（member_seq=%s）" % seq,
                                           "工作间 · 名单", "先 GET …/roster 取一个真实 member_seq")
        if m.get("state") != "active":
            return False, "GATE_G5", _four(
                "成员 #%s" % seq, "它还不在 active（现在是 %s）" % m.get("state"), "对接中心",
                "先把它接上（证据齐 + verified_scope），再派活")
        return True, "", {}

    if action == "change_path":
        status = str(getattr(workshop, "status", "") or "")
        if status in ("running", "review", "integrate", "closed"):
            return False, "WS_PATH_LOCKED", _four(
                "用户", "工作间已在 %s，路径只读" % status, "工作间设置",
                "要改先降回 staffing（会要求重发成员对接单）")
        return True, "", {}

    return True, "", {}
