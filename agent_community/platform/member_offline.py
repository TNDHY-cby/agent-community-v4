"""员工掉线处理（老板口述②，V23 §2）—— **组长就地抢救，失败才上报人**。

老板原话的链条：
    员工掉线 → 通知【组长】 → 组长去检查 → 能调起就用【鼠标控制调起】（窗口激活）
    → 多次仍调不起 / 无法调起 → 通知【用户】

并明确**否掉了**两个选项（"只标记"与"自动改派"）：要的是**先就地抢救**，
抢救失败才上报人 —— 比自动改派更省事也更安全（不会造出两份工作）。

**为什么单独一个模块**：这是一条**有状态的抢救链**（尝试计数 / 冷却 / 升级阈值）。
散在 20s 循环里很容易退化成"每轮盲重派"——既浪费，又把真问题掩盖掉。

三条不变的规矩：
  1. **先就地抢救，失败才上报人**；
  2. **每次尝试都要如实判定成败**：拿到回报才算调起成功，否则一律「未核实」（准则⑤）；
  3. **"多次"= MAX_ATTEMPTS 次**；达到即 `needs_human` 并通知用户，**不再无限重试**。

本模块只做**判定与编排**，副作用（通知组长 / 尝试调起 / 通知用户）由调用方以回调注入——
这样它可以脱离 server 单测，也避免把 HTTP/工作间细节塞进恢复逻辑。
"""

from __future__ import annotations

import time
from typing import Any, Callable, Optional

MAX_ATTEMPTS = 3          # 老板说的"多次"；到这就上报人，不再耗着
COOLDOWN_SECONDS = 60.0   # 两次尝试之间的冷却，避免 20s 循环每轮都猛调


def _hid_of(member: Any) -> str:
    return (list(getattr(member, "harness_ids", []) or []) or [""])[0]


def has_pending_work(ws: Any, member: Any) -> tuple[bool, str]:
    """**它现在该干活吗？** —— 这是判掉线的**前提**（老板 2026-10-08：

    「这个不够智能，有时候对方**并没有工作需要进行**」）。

    为什么必须先问这句：**空闲是正常态**。一个成员没活干时心跳不报、桥闲着、窗口不开，
    都是天经地义的——若把它判成"掉线"，平台就会去通知组长、去弹人家的窗口、
    3 次之后还去叫用户，**全是白折腾**（正是老板要的"别打扰"的反面）。

    依据（任一成立即"有活"，并给出理由，便于前端显示与排错）：
      · `entry_state ∈ {dispatched, acked}`  —— 已派发/已确认但没走到完成；
      · `status ∈ {activating, working}`     —— 生命周期上正在开工；
      · 有**未结案的派发观察**（`_sim_watch.done is False`）—— 派了、在等回报；
      · `ws.assignments[member_id]` 里有 `dispatched` 但未标记完成的委派。
    """
    state = str(getattr(member, "entry_state", "") or "")
    if state in ("dispatched", "acked"):
        return True, f"entry_state={state}（已派发未完成）"
    if state in ("blocked", "timeout", "needs_human"):
        # 这三态**活儿仍然挂着**（卡住/超时/等人处理）⇒ 算"有活"。
        # 否则会出现荒谬结果：刚上报完人，下一轮就因为"没活"而不再理会（实测踩到）。
        return True, f"entry_state={state}（活还挂着，等人/等恢复）"
    st = str(getattr(member, "status", "") or "")
    if st in ("activating", "working"):
        return True, f"status={st}（正在开工）"
    watch = getattr(member, "_sim_watch", None)
    if isinstance(watch, dict) and not watch.get("done"):
        return True, "有未结案的派发观察（派了，在等回报）"
    try:
        items = (getattr(ws, "assignments", None) or {}).get(member.member_id) or []
        for it in items:
            if isinstance(it, dict) and it.get("dispatched") and not it.get("done"):
                return True, "有已派发未完成的委派"
    except Exception:  # noqa: BLE001
        pass
    return False, "当前没有在进行的工作（空闲是正常态）"


def signal_breakdown(ws: Any, member: Any, *, heartbeat_status=None,
                     bridge_alive=None) -> dict:
    """把"不正常信号"分成**硬**与**软**两类（2026-10-08 审 FE-09 时发现的口径问题）。

    · **硬信号**＝它**确实收不到活**：**桥进程已退出**（桥没了就没人取邮箱，这是实证）。
    · **软信号**＝"它没动静"，但**不能据此断言不可用**：
      心跳失联（**本项目心跳是可选的** —— 很多 harness 从不报心跳）、
      进入状态机处于 blocked/timeout（那是任务卡住，不是联系不上）。

    为什么要分开：若把软信号也当成"不可用"，界面就会把**其实好好的成员**显示成"联系不上"——
    正是老板说的"别乱报警"（准则⑤ 同时也要求"失败要说话"，但**不许说假话**）。
    ⇒ `available` 只看硬信号；软信号仍**如实带出**（列在 `soft_reasons`），供详情里展示。
    """
    hid = _hid_of(member)
    hard: list[str] = []
    soft: list[str] = []
    if not hid:
        soft.append("该成员未绑定 harness")
    else:
        try:
            hb = heartbeat_status(hid) if heartbeat_status else None
            if isinstance(hb, dict):
                if hb.get("stale"):
                    soft.append("harness 心跳失联（超时未上报；心跳在本项目中是可选的）")
                elif str(hb.get("status") or "").lower() in ("offline", "失联"):
                    soft.append("harness 状态为 offline")
            elif hb is False:
                soft.append("harness 心跳失联（超时未上报；心跳在本项目中是可选的）")
        except Exception as e:  # noqa: BLE001
            soft.append(f"心跳判定异常（{type(e).__name__}）")
        try:
            if bridge_alive is not None and bridge_alive(hid) is False:
                hard.append("桥进程已退出（**没人取邮箱**，确实收不到活）")
        except Exception as e:  # noqa: BLE001
            soft.append(f"桥存活判定异常（{type(e).__name__}）")
    state = str(getattr(member, "entry_state", "") or "")
    if state in ("blocked", "timeout"):
        soft.append(f"进入状态机处于 {state}（{getattr(member, 'entry_reason', '') or '无原因'}）")
    return {"hard": hard, "soft": soft, "all": hard + soft}


def offline_reasons(ws: Any, member: Any, *, heartbeat_status: Optional[Callable[[str], Any]] = None,
                    bridge_alive: Optional[Callable[[str], bool]] = None) -> list[str]:
    """该成员是否掉线，以及**原因**（原因要如实列出，前端直接显示）。

    判据（任一成立即算掉线，原因全部列出）：
      · harness 心跳失联（`heartbeat_status(hid)` 返回 stale/offline）；
      · **桥进程不在了**（`bridge_alive(hid)` 为 False）—— 桥没了就没人取邮箱，等于掉线；
      · 成员已处于阻断态（`entry_state` 为 blocked/timeout）且未结案。
    """
    return signal_breakdown(ws, member, heartbeat_status=heartbeat_status,
                            bridge_alive=bridge_alive)["all"]


def rescue_needed(ws: Any, member: Any, *, heartbeat_status=None, bridge_alive=None,
                  pending_work: Optional[Callable[[Any, Any], tuple]] = None) -> dict:
    """**要不要去救它？** —— 两级判断，这是本模块的"智能"所在。

    ```
    第一级：它现在**该干活吗**？  没有在进行的活 ⇒ **直接不救**（空闲是正常态，别打扰）
    第二级：它现在**看起来正常吗**？  有活 + 有不正常信号 ⇒ 才需要救
    ```

    返回 `{needed, pending_work, pending_work_why, signal_reasons, verdict}`：
      · `verdict = "idle"`     —— 没活（**老板说的"对方并没有工作需要进行"**）；
      · `verdict = "healthy"`  —— 有活且一切正常；
      · `verdict = "rescue"`   —— 有活却不对劲 ⇒ 触发组长抢救链（会通知人）。
    """
    fn = pending_work or has_pending_work
    try:
        busy, why = fn(ws, member)
    except Exception as e:  # noqa: BLE001
        busy, why = False, f"在途工作判定异常（{type(e).__name__}）⇒ 保守不救"
    sig = offline_reasons(ws, member, heartbeat_status=heartbeat_status, bridge_alive=bridge_alive)
    if not busy:
        return {"needed": False, "pending_work": False, "pending_work_why": why,
                "signal_reasons": sig, "verdict": "idle"}
    if not sig:
        return {"needed": False, "pending_work": True, "pending_work_why": why,
                "signal_reasons": [], "verdict": "healthy"}
    return {"needed": True, "pending_work": True, "pending_work_why": why,
            "signal_reasons": sig, "verdict": "rescue"}


def offline_report(ws: Any, *, heartbeat_status=None, bridge_alive=None,
                   pending_work=None, now: Optional[float] = None) -> list[dict]:
    """给界面：每个成员的情况 + 抢救进度 + 是否已上报人。

    ⚠️ **区分三态**（老板 2026-10-08：有时对方只是没活干）：
      · `verdict=idle`    —— 没在进行的工作（**不救、不叫人**）；
      · `verdict=healthy` —— 有活且正常；
      · `verdict=rescue`  —— **有活却不对劲 ⇒ 才需要救**。
    `available` 单独给出"能不能收活"（如桥进程确认已退出 ⇒ 不可用），
    但**不可用 ≠ 要去救**：没活的时候不可用也不打扰任何人。
    """
    now = now or time.time()
    rows = []
    for m in (getattr(ws, "members", None) or []):
        r = rescue_needed(ws, m, heartbeat_status=heartbeat_status, bridge_alive=bridge_alive,
                          pending_work=pending_work)
        sig = signal_breakdown(ws, m, heartbeat_status=heartbeat_status, bridge_alive=bridge_alive)
        attempts = int(getattr(m, "offline_attempts", 0) or 0)
        last = float(getattr(m, "offline_last_at", 0) or 0)
        rows.append({
            "member_id": m.member_id, "role": m.role,
            "display_name": getattr(m, "display_name", ""),
            "harness_id": _hid_of(m),
            # 兼容旧字段名：`offline` 现在表示"**需要救**"，别再当成"没动静"
            "offline": bool(r["needed"]),
            # `available` **只看硬信号**（桥确认退出）——心跳失联是软信号，不许据此说"联系不上"
            "available": not sig["hard"],
            "hard_reasons": sig["hard"],
            "soft_reasons": sig["soft"],
            "needs_rescue": bool(r["needed"]),
            "verdict": r["verdict"],
            "pending_work": r["pending_work"], "pending_work_why": r["pending_work_why"],
            "reasons": r["signal_reasons"],
            "attempts": attempts, "max_attempts": MAX_ATTEMPTS,
            "escalated": str(getattr(m, "entry_state", "")) == "needs_human",
            "cooldown_left": max(0, int(COOLDOWN_SECONDS - (now - last))) if last and r["needed"] else 0,
            "entry_state": getattr(m, "entry_state", ""),
            "entry_reason": getattr(m, "entry_reason", ""),
        })
    return rows


def reset_attempts(member: Any) -> None:
    """结案（调起成功/成员恢复）时清零，避免下次掉线沿用旧计数。"""
    member.offline_attempts = 0
    member.offline_last_at = 0.0
    member.offline_last_reason = ""


def handle_offline(ws: Any, member: Any, *, notify_leader: Callable[[str], Any],
                   attempt_revive: Callable[[], dict], notify_user: Callable[[str], Any],
                   heartbeat_status=None, bridge_alive=None,
                   pending_work: Optional[Callable[[Any, Any], tuple]] = None,
                   now: Optional[float] = None) -> dict:
    """对**一个**成员执行一轮抢救链。返回这一轮做了什么（供 API/日志）。

    ⭐ **先问"该不该救"**：没有在进行的工作 ⇒ `action="idle"`，**直接不救、不叫人**
    （老板 2026-10-08：「这个不够智能，有时候对方并没有工作需要进行」）。

    返回 `action` 取值：
      · `idle`       —— **没有在进行的活**（空闲是正常态）：不救、不通知任何人；
      · `none`       —— 有活且一切正常：顺带清零计数；
      · `cooldown`   —— 距上次尝试不足冷却，本轮不动；
      · `escalated`  —— 已上报人，不再自动重试；
      · `revived`    —— **调起成功且已自证**，清零计数；
      · `retry`      —— 本轮尝试失败/未核实，还会再试（未到上限）；
      · `gave_up`    —— 到上限：置 `needs_human` 并**通知用户**。
    """
    now = now or time.time()
    gate = rescue_needed(ws, member, heartbeat_status=heartbeat_status,
                         bridge_alive=bridge_alive, pending_work=pending_work)
    reasons = gate["signal_reasons"]
    if not gate["needed"]:
        reset_attempts(member)
        return {"action": "idle" if gate["verdict"] == "idle" else "none",
                "verdict": gate["verdict"], "reasons": reasons,
                "pending_work": gate["pending_work"], "pending_work_why": gate["pending_work_why"]}

    if str(getattr(member, "entry_state", "")) == "needs_human":
        return {"action": "escalated", "reasons": reasons, "attempts": getattr(member, "offline_attempts", 0)}

    attempts = int(getattr(member, "offline_attempts", 0) or 0)
    last = float(getattr(member, "offline_last_at", 0) or 0)
    if last and (now - last) < COOLDOWN_SECONDS:
        return {"action": "cooldown", "reasons": reasons, "attempts": attempts,
                "cooldown_left": int(COOLDOWN_SECONDS - (now - last))}

    # ── 第 1 步：通知组长（只在首轮通知，避免刷屏）────────────────────
    notified_leader = False
    if attempts == 0:
        try:
            notify_leader(
                f"【掉线】成员 {member.display_name or member.member_id}（{member.role}·{member.member_id}）"
                f"疑似掉线：{'；'.join(reasons)}。请你去检查；平台会同时尝试把它调起。")
            notified_leader = True
        except Exception as e:  # noqa: BLE001
            print(f"[offline] 通知组长失败: {e}", flush=True)

    # ── 第 2 步：尝试调起（组长检查的"手"）────────────────────────────
    res = {}
    try:
        res = attempt_revive() or {}
    except Exception as e:  # noqa: BLE001
        res = {"ok": False, "verified": False, "detail": f"调起异常：{type(e).__name__}: {e}"}

    attempts += 1
    member.offline_attempts = attempts
    member.offline_last_at = now
    member.offline_last_reason = "；".join(reasons)

    if res.get("verified"):
        reset_attempts(member)
        return {"action": "revived", "reasons": reasons, "attempts": 0,
                "notified_leader": notified_leader, "revive": res}

    # ── 第 3 步：到上限 → 上报人（老板：多次仍调不起才通知用户）────────
    if attempts >= MAX_ATTEMPTS:
        member.entry_state = "needs_human"
        member.entry_reason = (f"掉线 {attempts} 次调起仍未成功（{'；'.join(reasons)}）"
                               f"—— 最后一次：{res.get('detail', '')}")
        try:
            notify_user(
                f"【需要你处理】成员 {member.display_name or member.member_id}"
                f"（{member.role}·{member.member_id}）已尝试调起 {attempts} 次仍未成功。"
                f"原因：{'；'.join(reasons)}。最后一次尝试：{res.get('detail', '')}。"
                f"请人工确认该 harness 是否还开着。")
        except Exception as e:  # noqa: BLE001
            print(f"[offline] 通知用户失败: {e}", flush=True)
        return {"action": "gave_up", "reasons": reasons, "attempts": attempts,
                "notified_leader": notified_leader, "revive": res}

    return {"action": "retry", "reasons": reasons, "attempts": attempts,
            "notified_leader": notified_leader, "revive": res}
