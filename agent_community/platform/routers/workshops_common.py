"""platform/routers/workshops_common：workshops 端点组的共享辅助（V-13 自 workshops.py 拆分）。

共享：_sv()（双副本活跃 server 取用）、_leader_*（组长派工辅助）、_platform_base_url_safe。
四个业务子模块只依赖本模块，彼此无跨组调用（拆分前已实测：10 处跨组引用全指向 common）。
"""
from __future__ import annotations
import sys
from uuid import uuid4
from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect

router = APIRouter()

def _sv():
    """返回活跃 server 副本（__main__，uvicorn 实际运行的实例）模块。"""
    return sys.modules["__main__"]


def _leader_pick_member(ws, member_id: str):
    """按 member_id 从工作间中取成员，找不到返回 None。"""
    if not member_id:
        return None
    for m in ws.members:
        if m.member_id == member_id:
            return m
    return None


def _leader_member_hid(member) -> str:
    """取成员首个 harness_id；未绑定返回空串。"""
    return (member.harness_ids or [None])[0] or ""


def _leader_recent_context(ws, member, limit: int = 2) -> dict:
    """**多轮协作的上下文**：该成员最近的指令与回报（供派活载荷携带）。

    ★2026-10-19（核心判据抓到）：原来的派活载荷只有"这一条指令 + 大厅内容"，
    **不带上一轮做过什么/回报了什么** ⇒ 第二批活下来时 agent 只能重读工作区文件去猜，
    多轮协作等于每轮从零开始。这里把最近几轮的 `{instruction, at}` 与该成员最近一次
    回报正文带上（**只带摘要，不塞全量历史**，避免载荷膨胀）。
    """
    out = {"previous_instructions": [], "last_result": "", "rounds": 0}
    try:
        hist = list((getattr(ws, "assignments", {}) or {}).get(member.member_id) or [])
    except Exception:                                        # noqa: BLE001
        hist = []
    out["rounds"] = len(hist)
    out["previous_instructions"] = [
        {"at": h.get("at", ""), "instruction": str(h.get("instruction") or "")[:200]}
        for h in hist[-limit:]
    ]
    # 最近一次该成员交回来的结果（按消息顺序倒着找）
    try:
        for msg in reversed(list(getattr(ws, "messages", []) or [])):
            if not isinstance(msg, dict):
                continue
            if str(msg.get("member_id") or "") != member.member_id:
                continue
            if str(msg.get("role") or "") not in ("member", "harness", "employee"):
                continue
            text = str(msg.get("content") or msg.get("text") or "").strip()
            if text:
                out["last_result"] = text[:600]
                break
    except Exception:                                        # noqa: BLE001
        pass
    return out


def _leader_build_assign_payload(ws, member, instruction: str, note: str, kind: str) -> dict:
    """构造组长委派/转向 payload（与 _leader_division_discuss 同构，供 _dispatch_to_harness 使用）。

    ★2026-10-19：**带上多轮上下文**（`context.previous_instructions` / `context.last_result`），
    并把一段人能读的「上一轮上下文」写进 `message` —— 否则多轮协作时 agent 每轮都从零开始。
    """
    ctx = _leader_recent_context(ws, member)
    hid = _leader_member_hid(member)
    if kind == "redirect":
        label = "任务转向·承接"
        body = (
            "你是本工作间的员工，组长刚把一项任务转交给你承接，请立即接手并推进。\n"
            "【转向指令】" + instruction + ("\n【组长备注】" + note if note else "") + "\n\n"
            "工作区实时路径：" + ws.workspace_dir + "\n"
            "任务（大厅内容）：\n" + ws.hall_content
        )
    else:
        label = "组长委派细化"
        body = (
            "你是本工作间的员工，组长刚给你下发/调整了细化工作指令，请按指令执行或调整当前工作。\n"
            "【细化指令】" + instruction + ("\n【组长备注】" + note if note else "") + "\n\n"
            "工作区实时路径：" + ws.workspace_dir + "\n"
            "任务（大厅内容）：\n" + ws.hall_content
        )
    # 多轮上下文（人能读的那段）：指令 + 上一轮交回来的结果摘要
    ctx_block = ""
    if ctx.get("rounds"):
        ctx_block = "\n\n【上一轮上下文】（本工作间已给你下过 %d 次指令，最近 %d 次如下）" % (
            ctx["rounds"], len(ctx.get("previous_instructions") or []))
        for it in (ctx.get("previous_instructions") or []):
            ctx_block += "\n  · [%s] %s" % (it.get("at", ""), it.get("instruction", ""))
        if ctx.get("last_result"):
            ctx_block += "\n【你上一轮回报的要点】\n" + ctx["last_result"]
    else:
        ctx_block = "\n\n【上一轮上下文】这是本工作间给你的第一条指令。"
    return {
        "type": "leader_assign" if kind == "assign" else "leader_redirect",
        "kind": kind,
        # ★2026-10-19（判据抓到）：**每次派活必须有一个可对账的 id**。
        #   原来载荷里没有任何 task_id ⇒ agent 回报时无法指出"这是哪一件活"，
        #   平台也无法把回报与某次派活对上（多轮时尤其致命）。
        "task_id": "t-" + uuid4().hex[:10],
        "workshop_id": ws.workshop_id,
        "member_id": member.member_id,
        "role": member.role,
        "workspace_dir": ws.workspace_dir,
        "instruction": instruction,
        "context": ctx,
        "report_endpoint": "/api/harness/task-result",
        "message": body + ctx_block + (
            "\n\n【回报要求】处理完必须把结果回报给平台，否则用户看不到：\n"
            "POST " + _platform_base_url_safe() + "/api/harness/task-result\n"
            "body: {\"workshop_id\":\"" + ws.workshop_id + "\",\"member_id\":\"" + member.member_id
            + "\",\"harness_id\":\"" + hid + "\",\"ok\":true,\"result\":\"你的完整回复/结果\"}\n"
            "把你的回复内容放进 result 字段回报上去。"
        ),
    }


def _platform_base_url_safe() -> str:
    """取平台基准地址，避免重复 import 冲突（函数体内延迟导入）。"""
    from ..server import _platform_base_url
    try:
        return _platform_base_url()
    except Exception:
        from ...platform_address import base_url as _addr_base_url
    return _addr_base_url()


