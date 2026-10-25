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


#: 这些协议的 harness **不需要"新建会话/重命名会话"那套视觉步骤** —— 注册好、加进工作间就能被派活
PROTOCOL_KINDS = ("mcp", "http", "http_api", "a2a", "grpc", "ws", "pipe", "file_poll")


def _proto_hint(wake_methods) -> bool:
    """这批成员里有没有"协议型"（不需要人去开会话）的。"""
    return any(str(w or "").lower() in PROTOCOL_KINDS for w in (wake_methods or []))


def check(ws, wake_methods=None) -> dict:
    stage = str(getattr(ws, "status", "") or "")
    if stage not in GATED_STAGES:
        return {"ok": True, "state": "in_flight", "why": "", "next_step": ""}
    members = list(getattr(ws, "members", None) or [])
    if not members:
        # ★2026-10-25 修：原来这句只写了"去对接中心做第 4/5 步（新建会话/重命名会话）"，
        #   那是**桌面类 harness** 的必经步骤；对 MCP/HTTP/A2A 这类**根本不适用**
        #   （老板原话：「MCP 协议适配的 harness 注册后无法拉入工作间，也没引导说明」）。
        if _proto_hint(wake_methods):
            nxt = ("在这个工作间点「加成员」，把已登记的 harness 选进来 —— **协议型（MCP/HTTP 等）不需要"
                   "新建会话/重命名会话**，选上就能被派活；一个都没有就去「大厅 · 对接中心」注册一个")
        else:
            nxt = ("先在这个工作间点「加成员」选一个已登记的 harness；桌面类还要在验证间把目标会话打开、"
                   "跑一次自检，再回来讨论")
        return {"ok": False, "state": "no_members",
                "why": "这个工作间还没有成员：讨论发出去也没人接，平台不会假装在讨论",
                "next_step": nxt}
    if not any(_reached(m) for m in members):
        pend = "、".join(str(getattr(m, "display_name", "") or getattr(m, "member_id", "")) for m in members[:3])
        if _proto_hint(wake_methods):
            nxt = ("对每个成员点「自检」看它回不回话 —— 协议型（MCP/HTTP 等）**不靠视觉脚本**，"
                   "自检通就说明能被派活")
        else:
            nxt = ("对每个成员点「验一次」；人工中转型点「我确认它回了」；桌面类先把目标会话打开")
        return {"ok": False, "state": "no_verified_member",
                "why": "成员都还没接上（桥没取走唤醒消息，agent 没醒）：%s" % (pend or "全部成员"),
                "next_step": nxt}
    return {"ok": True, "state": "ready", "why": "", "next_step": ""}


def info(ws, wake_methods=None) -> dict:
    """给界面用的轻量版（序列化时带上）。"""
    g = check(ws, wake_methods)
    return {"ok": g["ok"], "state": g["state"], "why": g["why"], "next_step": g["next_step"],
            "members_count": len(getattr(ws, "members", None) or []),
            "verified_count": sum(1 for m in (getattr(ws, "members", None) or []) if _reached(m))}
