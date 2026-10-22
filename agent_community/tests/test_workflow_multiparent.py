# -*- coding: utf-8 -*-
"""守卫：**菱形依赖（多父）** D4。

判据：
① `parents: [a, c]` 时，只有 a、c 都 done 才可跑；
② 只有一个父 done ⇒ 不可跑（旧实现会误判可跑）；
③ 只有一个父且有父未完成 ⇒ 不可跑（兼容 `parent_id`，不许退化）；
④ 同批里不放"父与子"（多父也要算）。
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.append(str(_ROOT))

from agent_community.platform import workflow_engine as we  # noqa: E402
from agent_community.platform.workshop import Workshop  # noqa: E402


def _ws(nodes):
    ws = Workshop(workshop_id="mp", name="mp", workspace_dir="", hall_content="")
    ws.task_tree = nodes
    return ws


DIAMOND = [
    {"node_id": "a", "label": "A", "kind": "task", "status": "planned"},
    {"node_id": "b", "label": "B", "kind": "task", "status": "planned", "parent_id": "a"},
    {"node_id": "c", "label": "C", "kind": "task", "status": "planned", "parent_id": "a"},
    {"node_id": "d", "label": "D", "kind": "task", "status": "planned", "parents": ["b", "c"]},
]


def test_diamond_needs_both_parents():
    ws = _ws([dict(n) for n in DIAMOND])
    m = {n["node_id"]: n for n in ws.task_tree}
    assert we.parents_of(m["d"]) == ["b", "c"], we.parents_of(m["d"])
    m["a"]["status"] = "done"
    m["b"]["status"] = "done"
    assert we.deps_done(ws, m["d"]) is False, "只有一个父完成，D 不能跑"
    m["c"]["status"] = "done"
    assert we.deps_done(ws, m["d"]) is True, "两个父都完成，D 才能跑"


def test_single_parent_not_regressed():
    ws = _ws([{"node_id": "a", "label": "A", "kind": "task", "status": "planned"},
              {"node_id": "b", "label": "B", "kind": "task", "status": "planned", "parent_id": "a"}])
    m = {n["node_id"]: n for n in ws.task_tree}
    assert we.deps_done(ws, m["b"]) is False
    m["a"]["status"] = "done"
    assert we.deps_done(ws, m["b"]) is True


def test_batch_never_pairs_parent_and_child_multi():
    ws = _ws([dict(n) for n in DIAMOND])
    batch = we.next_nodes(ws, limit=4)
    ids = [n["node_id"] for n in batch]
    assert ids == ["a"], "第一层只能跑 A（B/C 的父没完成；D 的两个父也没完成）：%r" % ids
