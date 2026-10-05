# -*- coding: utf-8 -*-
"""platform/entry_state：成员「进入工作间」状态机（V-15 步骤3）。

设计稿：design-docs/V15_工作间会话联动与三层桥设计.md §11

**它与 `member.status` 的分工**：
- `member.status` = 成员**生命周期**（pending/activating/entered/working/idle/completed/blocked）
- 本模块 = **派发进度**（有没有送到、对方认没认、进没进），以及**为什么卡住**

两者不能合一：`activating` 既可能是「已推送等回应」也可能是「推都没推出去」，
前端看不出差别 —— 这正是 §11 要解决的问题（"已派发 N 名"可能全是假的）。

**按唤醒方式的最高可达**（§11 核心）：

    acp        -> entered      桥建会话并回报
    http_api   -> acked        HTTP 200；entered 靠其自述
    http       -> acked        同上
    file_poll  -> dispatched   文件写入成功即到头
    clipboard  -> entered      人工点击（§11.1）

**这一表是"分档设计必须跑到最终动作"那条教训的产物**：
CLIPBOARD 原来只写到 pending，而派活要求全部 entered —— 该类型整条链路走不通。
现在明确它**可达 entered，靠人工**，并配 §11.1 的人工入口。
"""
from __future__ import annotations

# ── 进入状态 ────────────────────────────────────────────────────
PENDING = "pending"          # 已登记，未派发
DISPATCHED = "dispatched"    # 已推送/已写入/已入队 —— 但对方没确认
ACKED = "acked"              # 送达确认（HTTP 200 / 回执到达）
ENTERED = "entered"          # 已进入工作间（会话就位）
BLOCKED = "blocked"          # 派发失败（桥不在/推送失败），附 reason
TIMEOUT = "timeout"          # 派发后长期无回应
NEEDS_HUMAN = "needs_human"  # 超时后需要人工介入

STATUSES = (
    PENDING, DISPATCHED, ACKED, ENTERED, BLOCKED, TIMEOUT, NEEDS_HUMAN,
)

# 线性主路径（blocking 分支出去再回来）
MAIN_PATH = (PENDING, DISPATCHED, ACKED, ENTERED)

# 阻塞原因（与验证矩阵 #3 的"桥不在场"对应）
REASON_NO_BRIDGE = "桥进程未运行（未入队，不会被领取）"
REASON_DISPATCH_FAIL = "派发失败"
REASON_NO_HARNESS = "未指定 harness"
REASON_NOT_REACHED = "该唤醒方式到不了此状态"
REASON_TIMEOUT = "派发后长期无回应，需人工介入"


def max_reachable(wakeup_method: str) -> str:
    """该唤醒方式最高能到的进入状态（§11 表）。

    未知类型保守返回 ``dispatched`` —— 入了队但能走到哪没把握，
    绝不能谎报 reached ``entered``。
    """
    m = (wakeup_method or "").strip().lower()
    if m in ("acp", "a2a"):
        return ENTERED
    if m in ("http_api", "http"):
        return ACKED
    if m == "file_poll":
        return DISPATCHED
    if m == "clipboard":
        return ENTERED          # 靠人工点击（§11.1）
    if m in ("", "none", "unknown"):
        return PENDING
    return DISPATCHED


def can_reach(wakeup_method: str, state: str) -> bool:
    """该唤醒方式能否到达某状态（超最高可达则否）。"""
    try:
        return STATUSES.index(state) <= STATUSES.index(max_reachable(wakeup_method))
    except ValueError:
        return False


def can_transition(frm: str, to: str) -> bool:
    """状态转移是否合法。返回 (是否合法, 原因)。"""
    if frm not in STATUSES or to not in STATUSES:
        return False, f"未知状态：{frm!r} -> {to!r}"
    if frm == to:
        return True, "幂等"
    if to == BLOCKED:
        return True, "任何阶段都可因失败转 blocked"
    if to == TIMEOUT:
        return True, "任何阶段都可超时"
    if frm in (BLOCKED, TIMEOUT) and to == PENDING:
        return True, "人工恢复/重派后回到 pending"
    if frm == TIMEOUT and to == NEEDS_HUMAN:
        return True, "超时升级人工"
    if frm == ENTERED and to in (DISPATCHED, ACKED, PENDING):
        # 必须排在「主路径逐级」判断**之前** —— ENTERED/DISPATCHED 都在 MAIN_PATH 里，
        # 否则会被"逐级推进"分支抢先捕获，"不得回退"这个更具体的理由永远不生效
        # （结果同为 False，但审计/前端拿到的原因会是笼统的那个）。
        return False, "已进入不得回退到派发阶段"
    if frm in MAIN_PATH and to in MAIN_PATH:
        if MAIN_PATH.index(to) == MAIN_PATH.index(frm) + 1:
            return True, "主路径前进一步"
        return False, f"主路径只能逐级推进：{frm} -> {to}"
    return False, f"未定义的转移：{frm} -> {to}"


def initial(wakeup_method: str = "") -> str:
    """新成员的初始进入状态。"""
    return PENDING


def advance(frm: str, wakeup_method: str = "") -> str:
    """在主路径上前进一步（用于派发成功后的状态推进）。"""
    i = MAIN_PATH.index(frm) if frm in MAIN_PATH else -1
    if i < 0:
        return frm                      # blocked/timeout 等不在主路径，不自动推进
    nxt = MAIN_PATH[i + 1] if i + 1 < len(MAIN_PATH) else frm
    if not can_reach(wakeup_method, nxt):
        return frm                      # 不越该档最高可达
    return nxt


def describe() -> dict:
    """给 GET /api/workshop 返回体或调试用的自省信息。"""
    return {
        "statuses": list(STATUSES),
        "main_path": list(MAIN_PATH),
        "max_reachable": {m: max_reachable(m)
                          for m in ("acp", "http_api", "http", "file_poll",
                                    "clipboard", "unknown")},
        "reasons": {
            "no_bridge": REASON_NO_BRIDGE,
            "dispatch_fail": REASON_DISPATCH_FAIL,
            "no_harness": REASON_NO_HARNESS,
            "not_reached": REASON_NOT_REACHED,
            "timeout": REASON_TIMEOUT,
        },
    }


def entry_label(state: str) -> str:
    """给前端的中文标签（与 workshop.html 现有 st 映射风格一致）。"""
    return {
        PENDING: "待派发",
        DISPATCHED: "已派发",
        ACKED: "已送达",
        ENTERED: "已进入",
        BLOCKED: "受阻",
        TIMEOUT: "无回应",
        NEEDS_HUMAN: "待人工",
    }.get(state, state or PENDING)
