# -*- coding: utf-8 -*-
"""执行预算与超时（V77）：**纯逻辑**，不碰 HTTP、不写盘。

口径（诚实）：
- 预算按**任务**记（`Task.budget`），可继承工作间默认（`Workshop.budget_default`）；
- 已花 = `Task.usage.cost`（桥上报的真实用量），**不是估算**；没有用量就按 0 算，并在返回里说明；
- 判定只有三种：`ok` / `over_budget` / `over_time`，每种都带**人话原因**与两条出路（加预算/停掉）。
"""
from __future__ import annotations

from datetime import datetime

FIELDS = ("max_cost", "max_rounds", "max_minutes")


def _num(v, default=0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def budget_of(task, workshop=None) -> dict:
    """任务预算：任务自己的优先，否则继承工作间默认（都没有就是空 = 不限）。"""
    own = getattr(task, "budget", None)
    if isinstance(own, dict) and any(k in own for k in FIELDS):
        return {k: own.get(k) for k in FIELDS if own.get(k) not in (None, "")}
    if workshop is not None:
        d = getattr(workshop, "budget_default", None)
        if isinstance(d, dict):
            return {k: d.get(k) for k in FIELDS if d.get(k) not in (None, "")}
    return {}


def spent_of(task) -> dict:
    u = getattr(task, "usage", None) or {}
    return {"cost": _num(u.get("cost")), "rounds": int(_num(u.get("rounds"))),
            "has_usage": bool(u)}


def check(task, workshop=None, now: datetime | None = None) -> dict:
    """返回 `{state, why, budget, spent, how_to_fix}`；state=ok 时 why 为空。"""
    b = budget_of(task, workshop)
    s = spent_of(task)
    base = {"budget": b, "spent": s}

    if not b:
        return {**base, "state": "ok", "why": "", "how_to_fix": ""}

    # ★D2：`max_cost=0` 表示"**一分钱都不许花**"（不是不限）；想不限就别设这个字段
    if "max_cost" in b and (s["cost"] > _num(b["max_cost"]) or
                            (_num(b["max_cost"]) == 0 and s["cost"] > 0)):
        return {**base, "state": "over_budget",
                "why": "已花 %.4f 超过上限 %.4f%s"
                       % (s["cost"], _num(b["max_cost"]),
                          "" if s["has_usage"] else "（注意：这个任务还没有用量记录，可能桥没上报）"),
                "how_to_fix": "要么调高预算（POST /api/tasks/{id}/budget），要么停掉这个任务"}
    if "max_rounds" in b and s["rounds"] > int(_num(b["max_rounds"])):
        return {**base, "state": "over_budget",
                "why": "已跑 %d 轮超过上限 %d 轮" % (s["rounds"], int(_num(b["max_rounds"]))),
                "how_to_fix": "要么调高预算，要么把任务拆小一点再派"}

    if "max_minutes" in b:
        started = str(getattr(task, "created_at", "") or "")
        if started:
            try:
                t0 = datetime.fromisoformat(started)
                cur = now or datetime.now()
                minutes = (cur - t0).total_seconds() / 60.0
                if minutes > _num(b["max_minutes"]):
                    return {**base, "state": "over_time",
                            "why": "已跑 %.1f 分钟超过上限 %.0f 分钟" % (minutes, _num(b["max_minutes"])),
                            "how_to_fix": "要么调高时限，要么停下看看卡在哪（GET /api/tasks/{id}/snapshots）"}
            except (TypeError, ValueError):     # noqa: BLE001 — 有意静默：created_at 解析不了就不判超时（不编）
                pass

    return {**base, "state": "ok", "why": "", "how_to_fix": ""}


def set_budget(task, body: dict) -> dict:
    """设置预算（只认三个字段；非法值 → 抛 ValueError，由调用方转 400）。"""
    cur = dict(getattr(task, "budget", None) or {})
    for k in FIELDS:
        if k not in body:
            continue
        if body[k] in (None, ""):
            cur.pop(k, None)                 # None/空 = 取消这一项限制
            continue
        # ★D2：0 是**合法值**（零花费/零轮次/零分钟），不再被当成"取消限制"

        try:
            v = float(body[k])
        except (TypeError, ValueError):
            raise ValueError("%s 必须是数字" % k)
        if v < 0:
            raise ValueError("%s 不能是负数（想去掉限制就传 null 或空字符串；0 表示不允许任何消费）" % k)
        cur[k] = v
    task.budget = cur
    return cur
