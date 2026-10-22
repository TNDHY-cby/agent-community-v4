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
FAILED = ("failed",)     # V79：重路由后留下的失败节点（不再跑）


def _nodes(ws) -> list:
    return [n for n in (getattr(ws, "task_tree", None) or []) if isinstance(n, dict) and n.get("node_id")]


def _by_id(ws) -> dict:
    return {str(n["node_id"]): n for n in _nodes(ws)}


def state_of(node: dict) -> str:
    return str(node.get("status") or "planned")


def parents_of(node: dict) -> list:
    """一个节点的全部父节点（D4：支持 `parents: [...]`；只有 `parent_id` 时当单父）。

    菱形依赖（A→B、A→C、B→D、C→D）必须两个父都完成才能跑 D，否则执行顺序与画布所见不符。
    """
    out = []
    multi = node.get("parents")
    if isinstance(multi, list):
        out.extend(str(x) for x in multi if str(x or ""))
    single = str(node.get("parent_id") or "")
    if single and single not in out:
        out.append(single)
    return out


def deps_done(ws, node: dict) -> bool:
    """**所有**父节点都完成了才算可跑。没有父 ⇒ 直接可跑。"""
    pids = parents_of(node)
    if not pids:
        return True
    m = _by_id(ws)
    for pid in pids:
        parent = m.get(pid)
        if parent is None:                   # 父节点被删了：当作这一条依赖满足（不永久卡死）
            continue
        if state_of(parent) not in DONE:
            return False
    return True


def next_node(ws):
    """返回现在该跑的那一个节点（dict）或 None（没有可跑的）。"""
    for node in _nodes(ws):
        st = state_of(node)
        if st in DONE or st in IN_FLIGHT or st in FAILED:
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


def reset_blocked(ws) -> list:
    """把 blocked / running 复位成 planned（人工干预后用），返回被复位的 node_id 列表。"""
    out = []
    for node in _nodes(ws):
        if state_of(node) in ("blocked", "running", "retry"):
            node["retry_count"] = 0
            mark(node, "planned")
            out.append(str(node["node_id"]))
    return out

# ── V79：并行与失败重路由 ────────────────────────────────────────

def _batch_limit() -> int:
    import os

    try:
        return max(1, int(os.environ.get("AC_WORKFLOW_BATCH", "3")))
    except Exception:                                        # noqa: BLE001
        return 3


def next_nodes(ws, limit: int = 0) -> list:
    """**现在可以同时跑的一组**节点（同层、无依赖、彼此不互为父子）。

    顺序沿用 `task_tree`；`limit` 默认取 `AC_WORKFLOW_BATCH`（默认 3）。
    """
    n = limit or _batch_limit()
    picked, picked_ids = [], set()
    for node in _nodes(ws):
        if len(picked) >= n:
            break
        st = state_of(node)
        if st in DONE or st in IN_FLIGHT or st in FAILED:
            continue
        if not deps_done(ws, node):
            continue
        nid = str(node["node_id"])
        if any(p in picked_ids for p in parents_of(node)):
            continue                       # 同一批里不放父子（父没跑完，子不能同时跑）
        picked.append(node)
        picked_ids.add(nid)
    return picked


def route_fallback(ws, node: dict) -> dict | None:
    """失败重路由：节点上写了 `fallback_to` 且在树里 ⇒ 返回那个节点（并把它复位成 planned）。"""
    target_id = str(node.get("fallback_to") or "")
    if not target_id:
        return None
    m = _by_id(ws)
    target = m.get(target_id)
    if target is None:
        return None
    if state_of(target) in DONE:
        return None
    target["retry_count"] = 0
    mark(target, "planned")
    return target


def on_fail(ws, node: dict) -> dict:
    """失败收口（V79 版）：先 retry 一次 → 再失败：有 fallback 就路由过去，否则 blocked。

    返回 `{state, routed_to, why}`（`state ∈ retry|routed|blocked`）。
    """
    n = int(node.get("retry_count") or 0)
    if n < 1:
        node["retry_count"] = n + 1
        mark(node, "retry")
        return {"state": "retry", "routed_to": "", "why": "首次失败，自动重试一次"}
    target = route_fallback(ws, node)
    if target is not None:
        mark(node, "failed")
        return {"state": "routed", "routed_to": str(target["node_id"]),
                "why": "已重路由到节点 %s" % target["node_id"]}
    mark(node, "blocked")
    return {"state": "blocked", "routed_to": "", "why": "重试后仍失败，没有备选节点"}
