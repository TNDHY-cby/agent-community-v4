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
from typing import Callable, Optional

# ── V-20 审计埋点（工单化审计，47 号任务书 DSH 侧）──────────────────
# 背景：本模块的迁移此前只 append 到 st["logs"]（每 key 仅留 20 条，且 to_dict() 显式排除）
# ⇒ 不落盘、重启即失，任务生命周期没有任何可追溯台账。
#
# 出口走**可注入 sink**，以守住文首「纯函数 + 依赖注入：不 import 主模块全局单例」的原则：
#   1) 构造参数 auditor 优先级最高（测试可注入 fake，断言调用序列，且完全不碰真实落盘）；
#   2) 其次模块级 set_audit_sink(fn)；
#   3) 都没有时，在**函数体内惰性**导入 audit.audit_log（与 policy.py / ai_verify.py 同范式）。
# 埋点全链路 try/except 包裹：审计失败绝不影响状态迁移（不阻塞主流程、失败不炸）。
_AUDIT_EVENT = "task.transition"
# V-20.1（2026-10-07，FE-01 接口变更申请 #1）：出口签名由 3 参扩为 4 参，
# 第 4 个 `extra` 是**结构化明细**（event_name/from/to/action/note），随事件一并落盘，
# 使前端不必再正则解析 `detail`。`detail` 保留不变。
_audit_sink: Optional[Callable[[str, str, str, dict], None]] = None


def set_audit_sink(fn: Optional[Callable[[str, str, str, dict], None]]) -> None:
    """注入审计出口 ``fn(actor, target, detail, extra)``；传 None 恢复惰性默认（走 audit_log）。"""
    global _audit_sink
    _audit_sink = fn


def _resolve_auditor() -> Optional[Callable[[str, str, str, dict], None]]:
    """解析默认审计出口：优先模块级 sink，否则惰性取 audit_log。取不到返回 None。"""
    if _audit_sink is not None:
        return _audit_sink
    try:
        from .audit import audit_log
    except Exception:
        return None
    return lambda actor, target, detail, extra: audit_log.record(
        _AUDIT_EVENT, actor=actor, target=target, detail=detail, extra=extra
    )


def _actor_of(ctx: Optional[dict]) -> str:
    """从迁移上下文里取「谁」：member_id > by > leader > actor > agent_id，缺省 system。"""
    for k in ("member_id", "by", "leader", "actor", "agent_id"):
        v = (ctx or {}).get(k)
        if v:
            return str(v)
    return "system"


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

    def __init__(
        self,
        timeout_sec: float = 300.0,
        max_auto_retry: int = 1,
        auditor: Optional[Callable[[str, str, str], None]] = None,
    ):
        self.timeout_sec = timeout_sec
        self.max_auto_retry = max_auto_retry
        self._states: dict[str, dict] = {}  # key=ws_id/task_id -> {state, ts, last_ts, retries, ctx, logs}
        # V-20：审计出口（None = 用模块级 sink / 惰性 audit_log）
        self._auditor = auditor

    # ── V-20 审计出口 ────────────────────────────────────────
    def _emit(self, key: str, event: str, from_state: str, to_state: str,
              action: str, note: str, ctx: Optional[dict] = None) -> None:
        """落一条 task.transition 审计。**任何失败都吞掉**，绝不阻塞状态迁移。"""
        try:
            fn = self._auditor or _resolve_auditor()
            if fn is None:
                return
            detail = f"{event}: {from_state}→{to_state} action={action} note={note}"
            # 结构化明细：与 detail 同源，供前端直接取字段（不必正则解析）
            extra = {
                "event_name": event,
                "from": from_state,
                "to": to_state,
                "action": action,
                "note": note,
            }
            fn(_actor_of(ctx), str(key), detail, extra)
        except Exception as e:  # noqa: BLE001 — 审计失败不影响业务
            print(f"[audit] 任务迁移审计失败（忽略）: {e}", flush=True)

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
        """外部直接置位（正常流程推进：created→assigned→…→done，非异常路径）。

        V-20.1 修（2026-10-07）：**新建 key 时先在 `created` 建账，再置到目标状态**。
        原实现是 `ensure(key, initial=state)`，于是新建 key 的 `from_state` 恒等于 `state`，
        审计里第一条迁移就成了无意义的 `X→X`（例：`executing→executing`）——
        而工单时间线（前端 FE-01）正是要渲染这个，`X→X` 没有任何信息量。

        终点状态、`ctx`、时间戳与改动前**完全一致**；**只有 `from_state` 的取值变正确**
        （`created→X`，与 `on_event` 新建 key 时的行为对齐）。已核对全仓：无任何调用方
        或测试依赖旧的 `from_state` 取值。
        """
        _is_new = key not in self._states
        st = self.ensure(key, initial=(CREATED if _is_new else state), **ctx)
        from_state = st["state"]
        st["state"] = state
        st["last_ts"] = time.time()
        if ctx:
            st["ctx"] = dict(st.get("ctx") or {}, **ctx)
        st["logs"] = (st.get("logs") or [])[-19:] + [
            {"t": time.time(), "event": "set_state", "from": from_state, "to": state, "action": ACT_NOOP}
        ]
        self._emit(key, "set_state", from_state, state, ACT_NOOP, "", st.get("ctx"))
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
        # V-20 修正：`last_ts` 只在**状态真正迁移**时更新。
        # 此前无条件 `st["last_ts"] = time.time()` 会把「被终态拒绝 / 被忽略」的事件也当成一次活动，
        # 与本模块声明的「终态幂等：终态之后任何事件都不再改变状态」相矛盾，
        # 也让 test_concurrency_fault::test_terminal_state_is_idempotent 变成**依赖时钟粒度的脆弱测试**
        # （Windows 上 time.time() 精度约 15.6ms，改前只是侥幸同刻度；V-20 埋点耗时放大后被击穿）。
        # 语义上也更正确：被忽略的事件不是进展，不该重置 tick() 的超时基准。
        if new_state != from_state:
            st["last_ts"] = time.time()
        st["logs"] = (st.get("logs") or [])[-19:] + [
            {"t": time.time(), "event": event, "from": from_state,
             "to": new_state, "action": action, "note": note}
        ]
        self._emit(key, event, from_state, new_state, action, note, st.get("ctx"))
        return {"action": action, "state": new_state, "from_state": from_state,
                "retries": st.get("retries", 0), "note": note}

    def remove(self, key: str) -> bool:
        """删除指定任务/工作间的状态机记录（回收/清理时调用，彻底退出状态机）。"""
        existed = key in self._states
        st = self._states.pop(key, None)
        if existed:
            self._emit(key, "remove", (st or {}).get("state", CREATED), "removed",
                       ACT_DROP, "removed", (st or {}).get("ctx"))
        return existed

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
