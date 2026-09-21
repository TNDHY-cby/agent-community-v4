"""TaskStateMachine — 工作循环自治边界状态机（事件驱动，纯规则，零 LLM token）。

设计依据：《平台设计：插话与自治边界》§二（状态机 + 分级升级）。
- 事件驱动：回报 / stuck / blocked / timeout / 心跳，非轮询扫描（心跳仅兜底）。
- 分级升级：
  - L0 可自愈：瞬时故障（网络抖动、超时无回报）→ 自动重试 1 次，记日志，不打扰；
  - L1 需决策：重试仍失败 / 连续 blocked / 300s 超时 / stuck 未解 → 升级组长裁决；
  - L2 需人工：平台能力外 / 需授权 / 组长卡住 → 立即喊用户，循环挂起等指令。
- 重试判据：只对"可能自己好"的瞬时故障重试；确定性困难（stuck、校验失败、缺依赖）
  不重试，直接暂停上报，防算力浪费。
- 自治边界一句话：能自愈的自愈（L0）；需要判断的给组长（L1）；需要授权的喊用户（L2）；
  用户不在线就挂起，绝不静默放弃或静默执行。

纯函数 + 依赖注入：不 import 主模块全局单例，可注入 fake 对象做函数级断言。
"""
from __future__ import annotations

import time
from typing import Optional

# ── 状态常量 ─────────────────────────────────────────────────
CREATED = "created"
ASSIGNED = "assigned"
DISCUSSING = "discussing"
EXECUTING = "executing"
WAITING_REPLY = "waiting_reply"
STUCK_PAUSED = "stuck-paused"
BLOCKED_RETRYING = "blocked-retrying"
TIMEOUT = "timeout"
DONE = "done"
DROPPED = "dropped"

# ── 事件常量 ─────────────────────────────────────────────────
EV_SUBMIT = "submit"
EV_ASSIGN = "assign"
EV_DISCUSS = "discuss"
EV_EXECUTE = "execute"
EV_REPORT = "report"          # 成功回报（等待组长/用户裁决）
EV_STUCK = "stuck"            # 过程性困难（不重试，暂停）
EV_BLOCK = "block"            # 确定性故障（不重试，暂停上报 L1）
EV_TIMEOUT = "timeout"        # 瞬时超时（L0 重试 1 次 → 仍失败 L1）
EV_RETRY_OK = "retry_ok"      # 重试成功
EV_RETRY_FAIL = "retry_fail"  # 重试仍失败
EV_RESUME = "resume"          # 组长/用户裁决后恢复
EV_RESOLVE = "resolve"        # 组长裁决解决
EV_COMPLETE = "complete"      # 全部完成
EV_DROP = "drop"              # 放弃

# ── 动作常量（事件处理后返回给调用方执行） ───────────────────
ACT_NOOP = "noop"
ACT_AUTO_RETRY = "auto_retry"        # L0：自动重试
ACT_ESCALATE_L1 = "escalate_l1"      # L1：升级组长裁决
ACT_ESCALATE_L2 = "escalate_l2"      # L2：需人工
ACT_PAUSE = "pause"                  # 暂停（stuck）
ACT_RESUME = "resume"
ACT_DONE = "done"
ACT_DROP = "drop"


class TaskStateMachine:
    """事件驱动状态机（每个工作间/任务一条状态记录）。纯规则，零 token。"""

    def __init__(self, timeout_sec: float = 300.0, max_auto_retry: int = 1):
        self.timeout_sec = timeout_sec
        self.max_auto_retry = max_auto_retry
        self._states: dict[str, dict] = {}  # key=ws_id/task_id -> {state, ts, last_ts, retries, ctx, logs}

    # ── 基础访问 ─────────────────────────────────────────────
    def get_state(self, key: str) -> str:
        return self._states.get(key, {}).get("state", CREATED)

    def ensure(self, key: str, initial: str = CREATED, **ctx) -> dict:
        if key not in self._states:
            now = time.time()
            self._states[key] = {
                "state": initial, "ts": now, "last_ts": now,
                "retries": 0, "ctx": ctx, "logs": [],
            }
        return self._states[key]

    def set_state(self, key: str, state: str, **ctx) -> dict:
        """外部直接置位（正常流程推进：created→assigned→…→done，非异常路径）。"""
        st = self.ensure(key, initial=state)
        from_state = st["state"]
        st["state"] = state
        st["last_ts"] = time.time()
        if ctx:
            st["ctx"] = dict(st.get("ctx") or {}, **ctx)
        st["logs"] = (st.get("logs") or [])[-19:] + [
            {"t": time.time(), "event": "set_state", "from": from_state, "to": state, "action": ACT_NOOP}
        ]
        return {"action": ACT_NOOP, "state": state, "from_state": from_state,
                "retries": st.get("retries", 0), "note": ""}

    def on_event(self, key: str, event: str, ctx: Optional[dict] = None) -> dict:
        """异常/回报事件入口：推进状态机并返回动作。返回 {action, state, from_state, retries, note}。"""
        st = self.ensure(key)
        from_state = st["state"]
        if ctx:
            st["ctx"] = dict(st.get("ctx") or {}, **ctx)

        action, new_state, note = ACT_NOOP, from_state, ""
        if from_state in (DONE, DROPPED):
            note = f"terminal:{from_state}"
        elif event == EV_STUCK and from_state in (EXECUTING, DISCUSSING, ASSIGNED, WAITING_REPLY):
            # 过程性困难：不重试，立即暂停（组长先解决再 resume）
            new_state, action = STUCK_PAUSED, ACT_PAUSE
        elif event == EV_BLOCK:
            # 确定性故障（校验失败/缺依赖等）：不重试，暂停上报 L1
            new_state, action = TIMEOUT, ACT_ESCALATE_L1
        elif event == EV_TIMEOUT:
            if from_state in (EXECUTING, DISCUSSING, ASSIGNED):
                if st.get("retries", 0) < self.max_auto_retry:
                    st["retries"] = st.get("retries", 0) + 1
                    new_state, action = BLOCKED_RETRYING, ACT_AUTO_RETRY
                else:
                    new_state, action = TIMEOUT, ACT_ESCALATE_L1
            elif from_state == BLOCKED_RETRYING:
                # 重试后仍超时 → L1
                new_state, action = TIMEOUT, ACT_ESCALATE_L1
            elif from_state in (STUCK_PAUSED, WAITING_REPLY):
                # stuck 未解 / 等待回复超时 → L1（不重试）
                new_state, action = TIMEOUT, ACT_ESCALATE_L1
            else:
                note = f"ignored:timeout@{from_state}"
        elif event == EV_RETRY_OK and from_state == BLOCKED_RETRYING:
            new_state, action = EXECUTING, ACT_RESUME
        elif event == EV_RETRY_FAIL and from_state == BLOCKED_RETRYING:
            new_state, action = TIMEOUT, ACT_ESCALATE_L1
        elif event == EV_REPORT and from_state in (EXECUTING, BLOCKED_RETRYING):
            new_state = WAITING_REPLY
        elif event == EV_RESUME and from_state in (STUCK_PAUSED, BLOCKED_RETRYING, TIMEOUT, WAITING_REPLY):
            new_state, action = EXECUTING, ACT_RESUME
        elif event == EV_RESOLVE and from_state in (STUCK_PAUSED, TIMEOUT, WAITING_REPLY):
            new_state = DISCUSSING
        elif event == EV_COMPLETE and from_state in (EXECUTING, WAITING_REPLY, DISCUSSING, STUCK_PAUSED):
            new_state, action = DONE, ACT_DONE
        elif event == EV_DROP:
            new_state, action = DROPPED, ACT_DROP
        elif event in (EV_SUBMIT, EV_ASSIGN, EV_DISCUSS, EV_EXECUTE):
            note = f"ignored:{event}@{from_state}"
        else:
            note = f"ignored:{event}@{from_state}"

        st["state"] = new_state
        st["last_ts"] = time.time()
        st["logs"] = (st.get("logs") or [])[-19:] + [
            {"t": time.time(), "event": event, "from": from_state,
             "to": new_state, "action": action, "note": note}
        ]
        return {"action": action, "state": new_state, "from_state": from_state,
                "retries": st.get("retries", 0), "note": note}

    def tick(self, now: Optional[float] = None) -> list:
        """兜底心跳：executing/waiting_reply/stuck-paused 超时 → timeout 事件。

        正常流程由回报/前端操作事件驱动；此处只做兜底，防止异常路径无人接管。
        返回 [(key, event_result), ...]，调用方据此执行 L0 重试或 L1 升级。
        """
        now = now or time.time()
        fired = []
        for key, st in list(self._states.items()):
            if st["state"] in (EXECUTING, WAITING_REPLY, STUCK_PAUSED, BLOCKED_RETRYING):
                last = st.get("last_ts") or st.get("ts") or now
                if now - last > self.timeout_sec:
                    ev = self.on_event(key, EV_TIMEOUT, {"at": now, "reason": "heartbeat_timeout"})
                    fired.append((key, ev))
        return fired

    # ── 持久化 ───────────────────────────────────────────────
    def to_dict(self) -> dict:
        return {
            "states": {k: {kk: vv for kk, vv in v.items() if kk != "logs"}
                       for k, v in self._states.items()},
            "timeout_sec": self.timeout_sec,
            "max_auto_retry": self.max_auto_retry,
        }

    def from_dict(self, d: dict) -> None:
        if not d:
            return
        self.timeout_sec = float(d.get("timeout_sec", self.timeout_sec))
        self.max_auto_retry = int(d.get("max_auto_retry", self.max_auto_retry))
        for k, v in (d.get("states") or {}).items():
            self._states[k] = dict(v)


# ── 插话时机判定：should_interject(priority, phase) 纯规则 ────
# 双维度：优先级(紧急/灵感/一般) × 阶段(讨论/执行/空闲)。零 LLM token。
_PRIORITIES = ("紧急", "灵感", "一般")
_PHASES = ("discussing", "executing", "idle")


def should_interject(priority: str, phase: str) -> dict:
    """纯规则判定插话是否可插入及动作。

    返回 {allowed, action, note, priority, phase}：
    - discussing：紧急→立即注入当前讨论（break_now）；灵感→本轮结束后注入(after_turn)；一般→进池(pool)
    - executing ：紧急→子任务收尾后平滑插入(insert_after_subtask)或用户直接打断；灵感→挂下一检查点(next_checkpoint)；一般→进池(pool)
    - idle      ：紧急→立即新建工作循环(start_new_cycle)；灵感→进池自动成任务(pool_auto_task)；一般→合并下个需求批次(pool_merge)
    """
    priority = (priority or "").strip()
    phase = (phase or "").strip().lower()
    if priority not in _PRIORITIES:
        priority = "一般"
    if phase not in _PHASES:
        phase = "executing"
    table = {
        ("紧急", "discussing"): (True, "break_now", "立即注入当前讨论（打断）"),
        ("紧急", "executing"): (True, "insert_after_subtask", "子任务收尾后平滑插入；或用户点【直接打断】强停"),
        ("紧急", "idle"): (True, "start_new_cycle", "立即新建工作循环"),
        ("灵感", "discussing"): (True, "after_turn", "当前讨论轮结束后注入"),
        ("灵感", "executing"): (True, "next_checkpoint", "挂到下一工作循环检查点，不打断"),
        ("灵感", "idle"): (True, "pool_auto_task", "进入待处理池（自动成任务开关默认开）"),
        ("一般", "discussing"): (False, "pool", "进入待处理池"),
        ("一般", "executing"): (False, "pool", "进入待处理池"),
        ("一般", "idle"): (False, "pool_merge", "合并进下个需求批次"),
    }
    allowed, action, note = table[(priority, phase)]
    return {"allowed": allowed, "action": action, "note": note,
            "priority": priority, "phase": phase}
