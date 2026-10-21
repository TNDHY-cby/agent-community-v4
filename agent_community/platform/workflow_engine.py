# -*- coding: utf-8 -*-
"""工作流执行引擎（V78）：**纯逻辑**。

三条语义（别再扩散）：
1. **拓扑**：父节点没 `done` ⇒ 子节点不可跑；同层按 `task_tree` 原顺序（稳定、可复现）。
2. **一次一步 + 幂等**：`next_node()` 只给"现在能跑的那一个"；已是 `running/done/waiting_approval` 的跳过。
3. **收口**：失败先 `retry` 一次（`retry_count`），再失败 `blocked`；整棵树没可跑且有 blocked ⇒ `blocked`。
"""
from __future__ import annotations

OPEN_STATES = ("planned", "retry", "")
DONE = ("done",)
IN_FLIGHT = ("running", "waiting_approval")


def _nodes(ws) -> list:
    return [n for n in (getattr(ws, "task_tree", None) or []) if isinstance(n, dict) and n.get("node_id")]


def _by_id(ws) -> dict:
    return {str(n["node_id"]): n for n in _nodes(ws)}


def state_of(node: dict) -> str:
    return str(node.get("status") or "planned")


def deps_done(ws, node: dict) -> bool:
    """父节点（依赖）是否都完成了。没有父 ⇒ 直接可跑。"""
    pid = str(node.get("parent_id") or "")
    if not pid:
        return True
    m = _by_id(ws)
    parent = m.get(pid)
    if parent is None:                       # 父节点被删了：当作依赖满足（不因此永久卡死）
        return True
    return state_of(parent) in DONE


def next_node(ws):
    """返回现在该跑的那一个节点（dict）或 None（没有可跑的）。"""
    for node in _nodes(ws):
        st = state_of(node)
        if st in DONE or st in IN_FLIGHT:
            continue
        if not deps_done(ws, node):
            continue
        return node
    return None


def status(ws) -> dict:
    """给端点用的执行状态：节点状态表 + 卡在哪 + 下一步能跑什么。"""
    nodes = _nodes(ws)
    rows = [{"node_id": str(n["node_id"]), "label": n.get("label") or "",
             "kind": n.get("kind") or "task", "status": state_of(n),
             "parent_id": str(n.get("parent_id") or ""),
             "retry_count": int(n.get("retry_count") or 0)} for n in nodes]
    blocked = [r for r in rows if r["status"] == "blocked"]
    nxt = next_node(ws)
    if not nodes:
        state, why = "empty", "这个工作间还没有流程节点（先 POST /api/workshops/{id}/workflow 导入）"
    elif blocked:
        state, why = "blocked", "卡在节点 %s（%s）" % (blocked[0]["node_id"], blocked[0]["label"] or "无标题")
    elif all(r["status"] == "done" for r in rows):
        state, why = "done", "全部节点完成"
    elif nxt is None:
        state, why = "waiting", "没有可跑的节点（可能在等审批，或依赖未完成）"
    else:
        state, why = "running", "下一个可跑：%s" % nxt["node_id"]
    return {"state": state, "why": why, "nodes": rows,
            "next": ({"node_id": str(nxt["node_id"]), "label": nxt.get("label") or "",
                      "kind": nxt.get("kind") or "task"} if nxt else None),
            "blocked": [r["node_id"] for r in blocked]}


def mark(node: dict, status: str) -> dict:
    node["status"] = status
    return node


def on_fail(node: dict) -> str:
    """失败收口：先给一次 retry，再失败就 blocked。返回新状态。"""
    n = int(node.get("retry_count") or 0)
    if n < 1:
        node["retry_count"] = n + 1
        mark(node, "retry")
        return "retry"
    mark(node, "blocked")
    return "blocked"


def reset_blocked(ws) -> list:
    """把 blocked / running 复位成 planned（人工干预后用），返回被复位的 node_id 列表。"""
    out = []
    for node in _nodes(ws):
        if state_of(node) in ("blocked", "running", "retry"):
            node["retry_count"] = 0
            mark(node, "planned")
            out.append(str(node["node_id"]))
    return out
