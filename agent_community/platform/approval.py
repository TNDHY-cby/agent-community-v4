# -*- coding: utf-8 -*-
"""人工审批闸门（V76 §2）：**纯逻辑**，不碰 HTTP、不写盘。

为什么单独一层：状态机只认"状态怎么变"，而"谁来点确认、理由必填"是业务规则。
两层分开后，判据可以直接测规则，不必起服务。
"""
from __future__ import annotations

from datetime import datetime

DECISIONS = ("approve", "reject", "cancel")


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def policy_of(workshop) -> bool:
    """这个工作间开没开闸门（默认**关** —— 对齐"自动兜底默认关"）。"""
    ap = getattr(workshop, "approval_policy", None)
    if isinstance(ap, dict):
        return bool(ap.get("approval_required"))
    return False


def enter(task, *, required: bool, reason: str = "", resume_to: str = "") -> dict:
    """任务进入"等人批"：返回新的 approval 记录（**不改状态**，状态由调用方经状态机改）。"""
    if not required:
        return dict(getattr(task, "approval", None) or {})
    return {"required": True, "waiting": True, "at": _now(),
            "reason": str(reason or "")[:200],
            "resume_to": str(resume_to or getattr(task.status, "value", task.status) or "")}


def check_decision(task, decision: str, note: str) -> tuple:
    """校验一次决策。返回 (ok, 错误说明或规整后的字串)。"""
    st = str(getattr(task.status, "value", task.status) or "")
    if st != "waiting_approval":
        return False, "这个任务现在不是待批状态（当前状态：%s），不能点确认" % st
    d = str(decision or "").strip().lower()
    if d not in DECISIONS:
        return False, "decision 只支持 approve / reject / cancel，收到：%r" % (decision,)
    if d == "reject" and not str(note or "").strip():
        return False, "打回必须写理由（note 不能为空）—— 否则被打回的人不知道要改什么"
    return True, d


def exit_record(task, decision: str, note: str, by: str = "user") -> dict:
    """把一次决策落成记录（保留 resume_to 供 approve 用）。"""
    cur = dict(getattr(task, "approval", None) or {})
    cur.update({"waiting": False, "decided_at": _now(), "decision": str(decision),
                "note": str(note or "")[:200], "by": str(by or "user")})
    return cur


def workshop_of_task(task):
    """反查任务属于哪个工作间（`origin_task_id` 优先，其次任务树里登记的 id）。找不到返回 None。"""
    from .state import workshops as _works

    tid = str(getattr(task, "id", "") or "")
    for ws in list(_works.values()):
        if str(getattr(ws, "origin_task_id", "") or "") == tid:
            return ws
    for ws in list(_works.values()):
        for node in (getattr(ws, "task_tree", None) or []):
            if isinstance(node, dict) and str(node.get("task_id") or "") == tid:
                return ws
    return None


def maybe_gate(task, *, reason: str = "回执已验证，等人点确认") -> bool:
    """若所属工作间开了闸门 ⇒ 记 approval 并返回 True（调用方负责改状态）。"""
    from .state import workshops as _works

    ws = workshop_of_task(task)
    required = bool(policy_of(ws)) if ws is not None else False
    if not required:
        return False
    task.approval = enter(task, required=True, reason=reason,
                          resume_to=str(getattr(task.status, "value", task.status) or ""))
    return True
