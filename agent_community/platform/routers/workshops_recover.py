"""platform/routers/workshops_recover：**员工掉线抢救**（老板口述②，V23 §2）。

链条（老板原话）：员工掉线 → 通知【组长】 → 组长去检查 → 能调起就用【鼠标控制调起】
→ 多次仍调不起 / 无法调起 → 通知【用户】。

本文件只做**接线**：判定与编排在 `platform/member_offline.py`（纯逻辑、可单测），
这里把三个真实副作用注入进去：
  · 通知组长   → 讨论区 notice +（若组长有 harness）**派一条消息给它**；
  · 尝试调起   → **重新派发激活**（既有 `_activate_single`）+ **鼠标控制调起**（`_activate_windows_by_pids`）；
  · 通知用户   → 讨论区 notice + `entry_state=needs_human`（前端据此显示"需要你处理"）。

⭐ "调起成功"的判定**不采信"我发出去了"**：只有**掉线判据真的消失**才算成功（准则⑤）。
"""

from __future__ import annotations

import asyncio

import time

from fastapi import APIRouter, Request

from ..audit import audit_log as _audit_log
from ..state import workshops

router = APIRouter()


def _json_response(*a, **kw):
    from ..server import Utf8JSONResponse
    return Utf8JSONResponse(*a, **kw)


def _find_member(ws, member_id: str):
    return next((m for m in (ws.members or []) if m.member_id == member_id), None)


def _hid_of(m) -> str:
    return (list(getattr(m, "harness_ids", []) or []) or [""])[0]


def _hb_status(hid: str):
    """心跳口径：有记录就返回记录（含 `stale`）；**没记录返回 None**＝"无数据，不据此判掉线"。"""
    try:
        from ..heartbeat import heartbeat_registry      # 单例在 heartbeat 模块里（不在 state）
        rec = heartbeat_registry.status(hid) or heartbeat_registry.status(f"harness:{hid}")
        return rec or None
    except Exception:  # noqa: BLE001
        return None


def _bridge_alive(hid: str):
    """桥进程存活的三态口径：`alive→True / dead→False / unknown→None`。

    ⚠️ 必须保留 `unknown`：多数 harness 的桥不是平台拉起的（从未登记）——
    若把"没记录"当"已死"，会给**所有**成员报假掉线（准则⑤ 也不许乱报警）。
    """
    try:
        from .. import bridge_procs
        st = bridge_procs.status(hid)
        if st == "alive":
            return True
        if st == "dead":
            return False
        return None
    except Exception:  # noqa: BLE001
        return None


def _notify_leader(ws, member, text: str):
    from ..server import _append_msg, _dispatch_to_harness
    try:
        _append_msg(ws, "notice", text, zone=2)
    except Exception as e:  # noqa: BLE001
        print(f"[offline] 讨论区留痕失败: {e}", flush=True)
    leader = next((m for m in (ws.members or []) if m.role == "组长"), None)
    hid = _hid_of(leader) if leader else ""
    if not hid or leader is member:
        return {"dispatched": False, "detail": "无组长 harness，仅讨论区留痕"}
    try:
        ok, note = _dispatch_to_harness(hid, {
            "type": "task", "kind": "offline_check", "workshop_id": ws.workshop_id,
            "role": "组长", "about_member": member.member_id,
            "task": text, "message": text,
        }, kind="task")
        return {"dispatched": bool(ok), "detail": note}
    except Exception as e:  # noqa: BLE001
        return {"dispatched": False, "detail": f"{type(e).__name__}: {e}"}


def _notify_user(ws, member, text: str):
    from ..server import _append_msg
    try:
        _append_msg(ws, "notice", text, zone=3)
    except Exception as e:  # noqa: BLE001
        print(f"[offline] 上报用户留痕失败: {e}", flush=True)


def _attempt_revive(ws, member):
    """**组长检查的"手"**：重新派发激活 + 鼠标控制调起。返回如实结果。

    `verified` 的判定：动作做完后**重新看掉线判据**——判据消失才算调起成功；
    只"发出去了"永远算未核实（准则⑤）。
    """
    from ..server import _activate_single, _activate_windows_by_pids
    hid = _hid_of(member)
    detail = []
    window_hit = False
    try:
        _activate_single(ws, member)
        detail.append("已重新派发激活消息")
    except Exception as e:  # noqa: BLE001
        detail.append(f"重新派发失败：{type(e).__name__}: {e}")
    hint_name = ""
    try:
        from ..server import harness_manager
        sess = harness_manager.sessions.get(hid) if hid else None
        hint_name = getattr(getattr(sess, "info", None), "harness_name", "") or ""
    except Exception:  # noqa: BLE001
        hint_name = ""
    try:
        # 鼠标控制调起：按 harness_id / harness_name 匹配窗口标题（不需要 pid）
        res = _activate_windows_by_pids([], [hint_name, hid])
        errs = [x for x in (res or []) if isinstance(x, dict) and x.get("error")]
        window_hit = bool(res) and not errs
        detail.append("窗口激活" + ("成功" if window_hit else "未命中窗口（该 harness 可能无可见窗口）"))
    except Exception as e:  # noqa: BLE001
        detail.append(f"窗口激活异常：{type(e).__name__}: {e}")

    # ★复核：**"该不该救"的判据是否已消失**（不是"心跳有没有回来"）
    try:
        from ..member_offline import rescue_needed
        left = rescue_needed(ws, member, heartbeat_status=_hb_status,
                             bridge_alive=_bridge_alive)["signal_reasons"]
    except Exception:  # noqa: BLE001
        left = ["复核失败"]
    verified = not left
    return {"ok": True, "verified": verified, "how": "重新派发激活 + 鼠标控制调起",
            "window_hit": window_hit,
            "detail": "；".join(detail) + ("" if verified else f"；仍未恢复（{'；'.join(left)}）")}


def _run_one(ws, member, *, now=None) -> dict:
    from ..member_offline import handle_offline
    out = handle_offline(
        ws, member,
        notify_leader=lambda text: _notify_leader(ws, member, text),
        attempt_revive=lambda: _attempt_revive(ws, member),
        notify_user=lambda text: _notify_user(ws, member, text),
        heartbeat_status=_hb_status, bridge_alive=_bridge_alive, now=now,
    )
    try:
        _audit_log.record("workshop.member_recover", actor="system", target=ws.workshop_id,
                          detail=f"member={member.member_id} action={out.get('action')} "
                                 f"attempts={out.get('attempts')}",
                          extra={"member_id": member.member_id, "action": out.get("action"),
                                 "attempts": out.get("attempts"), "reasons": out.get("reasons"),
                                 "revive": out.get("revive")})
    except Exception:  # noqa: BLE001
        pass
    return out


@router.get("/api/workshop/{ws_id}/offline-report")
async def offline_report_api(ws_id: str):
    """**谁需要救、救到第几次、该不该上报人** —— 给界面（老板②的可视化落点）。

    ⚠️ 三态要分清（老板 2026-10-08）：`idle`=**没活干（正常，别打扰）**、
    `healthy`=有活且正常、`rescue`=**有活却不对劲（才需要救）**。
    """
    from ..member_offline import MAX_ATTEMPTS, offline_report
    ws = workshops.get(ws_id)
    if not ws:
        return _json_response({"error": "工作间不存在"}, status_code=404)
    rows = offline_report(ws, heartbeat_status=_hb_status, bridge_alive=_bridge_alive)
    rescue = [r for r in rows if r["verdict"] == "rescue"]
    idle = [r for r in rows if r["verdict"] == "idle"]
    esc = [r for r in rows if r["escalated"]]
    if rescue:
        summary = f"{len(rescue)} 名成员有活却不对劲，抢救中"
    elif esc:
        summary = f"{len(esc)} 名成员已上报人，等你处理"
    elif idle:
        summary = f"无需抢救（{len(idle)} 名成员当前没有在进行的工作，空闲属正常）"
    else:
        summary = "全部正常"
    return {
        "success": True, "workshop_id": ws_id, "max_attempts": MAX_ATTEMPTS,
        "members": rows,
        # 兼容旧字段：offline_count 现在表示"需要救的人数"
        "offline_count": len(rescue),
        "needs_rescue": [r["member_id"] for r in rescue],
        "idle": [r["member_id"] for r in idle],
        "escalated": [r["member_id"] for r in esc],
        "summary": summary,
    }


@router.post("/api/workshop/{ws_id}/member/{member_id}/recover")
async def recover_member(ws_id: str, member_id: str, request: Request):
    """对**一个**成员跑一轮抢救链（组长/用户手动触发，或自动化循环调用）。

    body 可传 `{"now": <epoch>}` 便于测试注入时间；返回本轮做了什么（action/attempts/reasons/revive）。
    """
    from ..server import save_state
    ws = workshops.get(ws_id)
    if not ws:
        return _json_response({"error": "工作间不存在"}, status_code=404)
    m = _find_member(ws, member_id)
    if not m:
        return _json_response({"error": "成员不存在"}, status_code=404)
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        body = {}
    if not isinstance(body, dict):
        body = {}
    now = None
    try:
        now = float(body.get("now")) if body.get("now") is not None else None
    except Exception:  # noqa: BLE001
        now = None
    out = await asyncio.to_thread(_run_one, ws, m, now=now)
    save_state()
    return {"success": True, "workshop_id": ws_id, "member_id": member_id,
            "entry_state": getattr(m, "entry_state", ""),
            "entry_reason": getattr(m, "entry_reason", ""), **out}


@router.post("/api/workshop/{ws_id}/recover-scan")
async def recover_scan(ws_id: str, request: Request):
    """扫**全工作间**跑一轮抢救（等价于 20s 自动循环做的那件事，便于手动演练）。"""
    from ..server import save_state
    ws = workshops.get(ws_id)
    if not ws:
        return _json_response({"error": "工作间不存在"}, status_code=404)
    results = {}
    for m in list(ws.members or []):
        # 同上：抢救链里有同步子进程（窗口激活脚本）⇒ 走线程，别卡住事件循环
        results[m.member_id] = await asyncio.to_thread(_run_one, ws, m)
    save_state()
    # ⚠️ "决策不动手"不算动作（准则⑤：别把没动说成动了）——`idle` 单列，摘要分别说清
    acted = {k: v.get("action") for k, v in results.items()
             if v.get("action") not in ("none", "idle", None)}
    idle_skipped = [k for k, v in results.items() if v.get("action") == "idle"]
    if acted:
        summary = f"本轮对 {len(acted)} 名成员做了动作"
    else:
        summary = "本轮无需动作"
    if idle_skipped:
        summary += f"；另有 {len(idle_skipped)} 名成员当前没有在进行的工作，**未打扰**"
    return {"success": True, "workshop_id": ws_id, "results": results,
            "acted": acted, "idle_skipped": idle_skipped, "summary": summary}
