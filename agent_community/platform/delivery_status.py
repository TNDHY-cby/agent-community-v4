# -*- coding: utf-8 -*-
"""platform/delivery_status：**"消息到底送到没有"** —— 让卡点可见。

设计稿：`V37_接入功能产品化设计.md` §5（P1：先让问题可见）
老板 2026-10-11 的事故：邮箱里压着 4 条消息、**桥没在跑**，而**平台一个字都没说** ⇒
他看到的是"消息发了、agent 没反应"，无从下手。本模块就是回答这个问题。

**为什么"桥在不在"不能用进程判定（实测教训）**
──────────────────────────────────────────────
平台已有的 `_find_bridge_processes(harness_id, bridge_dir)` 是**按命令行里含 harness_id / bridge_dir
来匹配**的；而桥的正常启动命令行是 `python -u bridge.py`（相对路径，**不含** harness_id）
⇒ **匹配不到**，会误报"桥没在跑"。
⇒ 本模块**不靠猜进程**，只用**磁盘事实 + 会话事实**（下面五项），并如实说明每一项的含义：

    ① `inbox_pending`   还没被取走的任务数（>0 = 平台发了、没人取）
    ② `delivered_count` 已被取走的任务数（桥干过活的痕迹，**与"进程在不在"无关**）
    ③ `last_delivery_at` 最近一次被取走的时间（由 `delivered/` 里文件的 mtime 推）
    ④ `replies_pending` / `replies_failed`  回报未送 / 送失败的条数
    ⑤ `session_ref_present` 该成员有没有平台认到的会话

**如实性纪律（准则⑤）**：`bridge_running` 只在我们**有证据**时才给 True；
拿不到证据时给 `None` 并附 `liveness_evidence`，**绝不猜**、绝不把"没证据"说成"没在跑"。
"""
from __future__ import annotations

import os
import time
from typing import Any

#: 桥的"活过"痕迹的宽限窗口（秒）：最近一次投递在此窗口内，视为"最近在工作"
RECENT_WINDOW = 600


def _dir_stats(p: str) -> dict:
    """目录里的文件数 + 最新 mtime（不递归；拿不到就如实回 error）。"""
    out = {"exists": False, "count": 0, "last_at": 0.0}
    try:
        if not p or not os.path.isdir(p):
            return out
        out["exists"] = True
        for name in os.listdir(p):
            fp = os.path.join(p, name)
            if not os.path.isfile(fp):
                continue
            out["count"] += 1
            try:
                mt = os.path.getmtime(fp)
            except OSError:
                continue
            if mt > out["last_at"]:
                out["last_at"] = mt
    except Exception as e:  # noqa: BLE001
        out["error"] = f"{type(e).__name__}: {e}"
    return out


def member_status(ws: Any, m: Any, *, compartment_dir: str = "") -> dict:
    """算**一个成员**的送达状态（纯读，不写任何东西）。

    `compartment_dir` 由调用方给（本模块不自己推导路径 —— 路径来源只该有一处）。
    """
    comp = str(compartment_dir or "").strip()
    inbox = os.path.join(comp, "inbox") if comp else ""
    replies = os.path.join(inbox, "replies") if inbox else ""

    inbox_st = _dir_stats(inbox)                 # 注意：inbox 下的 delivered/ 是目录，不计入
    delivered_st = _dir_stats(os.path.join(inbox, "delivered")) if inbox else _dir_stats("")
    replies_st = _dir_stats(replies)
    failed_st = _dir_stats(os.path.join(replies, "failed")) if replies else _dir_stats("")

    session_ref = dict(getattr(m, "session_ref", {}) or {})
    bridge_ref = dict(getattr(m, "bridge_ref", {}) or {})
    now = time.time()
    last_delivery = float(delivered_st.get("last_at") or 0.0)
    recent = bool(last_delivery and (now - last_delivery) <= RECENT_WINDOW)

    # ── 结论：谁卡住了、为什么、怎么办（人话，界面直接显示）─────────────────
    # ⚠️ 判定**按严重度排序**（实测教训：我一开始让"session_ref 为空"参与判断，
    #    结果把"消息都已送达、只是还没建会话"的正常件误判成卡住，也盖住了
    #    "邮箱根本不存在"这个更要紧的原因）。⇒ 只把**真卡住**的情形算 stuck。
    pending = int(inbox_st.get("count") or 0)
    delivered_n = int(delivered_st.get("count") or 0)
    replies_n = int(replies_st.get("count") or 0)
    failed_n = int(failed_st.get("count") or 0)
    inbox_exists = bool(inbox_st.get("exists"))

    stuck_reason = ""
    advice = ""
    not_set_up = ""
    if failed_n > 0:
        stuck_reason = f"有 {failed_n} 条回报**平台没收下**（在 replies/failed/）"
        advice = "看 replies/failed/ 里那些文件的内容，平台会说明拒绝原因"
    elif replies_n > 0:
        stuck_reason = f"你的 agent 写了 {replies_n} 条回报，但还没被转发给平台"
        advice = "回报要靠桥转发；确认桥在运行"
    elif not inbox_exists and comp and pending > 0:
        # ⚠️ 只有"**确实有东西要送、却连邮箱都没有**"才算卡住。
        #    单纯"还没架桥"是**还没走到那一步**，不是卡住（实测纠正：我一开始把它算 stuck，
        #    结果全空的工作间报"有 4 个成员没送到（共 0 条积压）"，自相矛盾）。
        stuck_reason = "有消息要送给这个成员，但它还没有邮箱"
        advice = "先去工作间给这个成员「架桥」，平台会按它的隔间建好邮箱"
    elif not inbox_exists and comp:
        not_set_up = "这个成员还没有邮箱 —— 还没为它架桥（不是故障，是还没走到这一步）"
        advice = "需要它收活时，在工作间里给它「架桥」"
    elif pending > 0 and delivered_n == 0:
        stuck_reason = f"还有 {pending} 条消息压在邮箱里，**没有任何东西来取过**"
        advice = ("桥没有在运行。启动它：在成员的隔间目录里运行 bridge.py"
                  "（平台生成的脚本，见隔间里的 deliver_text.md）")
    elif pending > 0:
        stuck_reason = f"邮箱里还有 {pending} 条没被取走（曾取走过 {delivered_n} 条）"
        advice = "桥可能刚停；确认它还在运行"
    # ⚠️ 以下**不算卡住**（只是"还没走到那一步"，如实显示但不报红）：
    #    · delivered 有、session_ref 空 —— 消息送到过，只是平台还没认到会话
    #    · 全新成员（还没架桥、也没消息）—— 属正常起步态

    return {
        "member_id": getattr(m, "member_id", ""),
        "role": getattr(m, "role", ""),
        "display_name": getattr(m, "display_name", ""),
        "harness_id": (list(getattr(m, "harness_ids", []) or []) or [""])[0],
        "inbox_pending": pending,
        "delivered_count": delivered_n,
        "last_delivery_at": last_delivery,
        "delivered_recently": recent,
        "replies_pending": replies_n,
        "replies_failed": failed_n,
        "session_ref_present": bool(session_ref),
        "bridge_state": bridge_ref.get("state") or "none",
        # 桥"活过"的证据（有就给 True；没有明确证据时给 None —— 不猜）
        "bridge_running": True if recent else None,
        "liveness_evidence": ("最近一次投递在 %d 秒内" % int(now - last_delivery)) if recent
                            else ("曾投递过 %d 条，但最近一次在 %.0f 分钟前（桥可能已停）"
                                  % (int(delivered_st.get("count") or 0),
                                     (now - last_delivery) / 60.0) if last_delivery
                                  else "没有任何投递记录，无法判断桥是否在运行"),
        "stuck": bool(stuck_reason),
        "stuck_reason": stuck_reason,
        "advice": advice,
        # 还没走到那一步（不是故障）：界面应**平静地**说明，不要报红
        "not_set_up": not_set_up,
        # 界面直接可用的一句话（人话，无术语）
        "status_text": (stuck_reason if stuck_reason
                        else (not_set_up if not_set_up else "正常：没有积压的消息")),
    }


def workshop_status(ws: Any, *, compartments: dict | None = None) -> dict:
    """算**整个工作间**的送达状态（只读）。

    `compartments`：可选，`{member_id: 隔间绝对路径}`；不给则调用方应自行传入
    （路径来源只该有一处，所以本模块不自己推导）。
    """
    comps = compartments or {}
    rows = []
    for m in (getattr(ws, "members", None) or []):
        mid = getattr(m, "member_id", "")
        rows.append(member_status(ws, m, compartment_dir=comps.get(mid, "")))
    stuck = [r for r in rows if r.get("stuck")]
    total_pending = sum(int(r.get("inbox_pending") or 0) for r in rows)
    return {
        "workshop_id": getattr(ws, "workshop_id", ""),
        "member_count": len(rows),
        "members": rows,
        "stuck_count": len(stuck),
        "total_pending": total_pending,
        # ★全站口径：消息没送到时必须说话（V37 §4.3）
        "headline": (f"有 {len(stuck)} 个成员的消息没送到（共 {total_pending} 条积压）"
                     if stuck else "所有成员的消息都已送达"),
    }
