# -*- coding: utf-8 -*-
"""工作间开工闸（EXT-20261022-01 B/C）：**纯逻辑**，判"现在能不能真的开始讨论"。

三条规则（与外部测试问题单 §5 的建议一致）：
1. **没有成员** ⇒ 不能开工：否则讨论区空转，界面看起来一切正常；
2. **成员全都没接上**（`entry_state` 不在 entered 且 `status` 不是 entered/working/idle）⇒ 不能开工：
   桥没取走唤醒消息、agent 根本没醒来，讨论发出去也是白搭；
3. 其余情况放行（只要有一个成员接上了，就允许开工 —— 不能因为部分成员没验证就整个不让干）。

返回 `{ok, state, why, next_step}`；`next_step` 必须可执行（去哪、点什么）。
"""
from __future__ import annotations

REACHED = ("entered", "working", "idle")


def _reached(member) -> bool:
    st = str(getattr(member, "status", "") or "")
    es = str(getattr(member, "entry_state", "") or "")
    return st in REACHED or es in REACHED


#: 只有这两个阶段算"要开工"；division / review / running 是**已在推进**，不拦
GATED_STAGES = ("draft", "discussing", "")


def check(ws) -> dict:
    stage = str(getattr(ws, "status", "") or "")
    if stage not in GATED_STAGES:
        return {"ok": True, "state": "in_flight", "why": "", "next_step": ""}
    members = list(getattr(ws, "members", None) or [])
    if not members:
        return {"ok": False, "state": "no_members",
                "why": "这个工作间还没有成员：讨论发出去也没人接，平台不会假装在讨论",
                "next_step": "先去「对接中心」接入一个 agent（第 4/5 步：新建会话 / 重命名会话都要做），"
                             "再回本工作间加成员"}
    if not any(_reached(m) for m in members):
        pend = "、".join(str(getattr(m, "display_name", "") or getattr(m, "member_id", "")) for m in members[:3])
        return {"ok": False, "state": "no_verified_member",
                "why": "成员都还没接上（桥没取走唤醒消息，agent 没醒）：%s" % (pend or "全部成员"),
                "next_step": "对每个成员点「验一次」；人工中转型点「我确认它回了」。"
                             "然后在验证间补齐第 4/5 步，成员状态变成「已进入」再回来讨论"}
    return {"ok": True, "state": "ready", "why": "", "next_step": ""}


def info(ws) -> dict:
    """给界面用的轻量版（序列化时带上）。"""
    g = check(ws)
    return {"ok": g["ok"], "state": g["state"], "why": g["why"], "next_step": g["next_step"],
            "members_count": len(getattr(ws, "members", None) or []),
            "verified_count": sum(1 for m in (getattr(ws, "members", None) or []) if _reached(m))}
