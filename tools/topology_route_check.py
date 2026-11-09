#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""判据：**看板强制路由**（`V118` §8 G5 / 队列 50-2）。

真跑：真拓扑数据 + 真路由函数（`platform/topology.route_write`），不 mock。

| # | 格 | 断言 |
|---|---|---|
| ① | 写自己看板 ⇒ 放行 `full` | `allow=True, mode=full` |
| ② | 往父写**摘要** ⇒ 放行 `summary` | `allow=True, mode=summary` |
| ③ | 往父写**全文** ⇒ 拒 `G5_PARENT_ONLY_SUMMARY`（且给 `next`） | 拒 + 有下一步 |
| ④ | 往**爷爷**（跳级）⇒ 也只能摘要 | `mode=summary`；全文照样拒 |
| ⑤ | **跨组** ⇒ 拒，并**给出 LCA** | `code=G5_CROSS_GROUP_VIA_LCA` 且 `via` 是共同祖先 |
| ⑥ | 写**后代**看板 ⇒ 拒 `G5_NO_DOWNWARD` | 不能替下级写 |
| ⑦ | 不认识的 Cell ⇒ 拒 `G5_UNKNOWN_CELL`（不假成功） | 两个 id 都验 |

退出码：0＝全过；1＝有格没过。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from agent_community.platform import topology as T  # noqa: E402

ROWS: list = []


def check(label: str, ok: bool, detail: str = "") -> None:
    ROWS.append((label, bool(ok), detail))
    print("  %s %s%s" % ("PASS" if ok else "FAIL", label,
                        ("　（" + detail + "）") if detail and not ok else ""))


def _c(cid, parent, kids):
    return {"id": cid, "parent": parent, "children": list(kids),
            "verify": {"how": "产物 diff", "where": "产物/"}}


def main() -> int:
    # 一棵四层的树：W → A → A1，W → B → B1（A 与 B 是兄弟组）
    topo = {"root": "W", "cells": [
        _c("W", None, ["A", "B"]),
        _c("A", "W", ["A1"]),
        _c("A1", "A", []),
        _c("B", "W", ["B1"]),
        _c("B1", "B", []),
    ]}
    print("判据：看板强制路由（V118 §8 G5 / 队列 50-2）")
    print("=" * 78)

    r1 = T.route_write(topo, "A", "A")
    check("① 写自己看板 ⇒ 放行 full", r1["allow"] and r1["mode"] == "full", str(r1)[:120])

    r2 = T.route_write(topo, "A", "W", kind="summary")
    check("② 往父写摘要 ⇒ 放行 summary", r2["allow"] and r2["mode"] == "summary", str(r2)[:120])

    r3 = T.route_write(topo, "A", "W", kind="full")
    check("③ 往父写**全文** ⇒ 拒 G5_PARENT_ONLY_SUMMARY（且给 next）",
          (not r3["allow"]) and r3.get("code") == "G5_PARENT_ONLY_SUMMARY" and bool(r3.get("next")),
          str(r3)[:160])

    r4 = T.route_write(topo, "A1", "W", kind="summary")
    r4b = T.route_write(topo, "A1", "W", kind="full")
    check("④ 往**爷爷**（跳级）⇒ 也只能摘要；全文照样拒",
          r4["allow"] and r4["mode"] == "summary"
          and (not r4b["allow"]) and r4b.get("code") == "G5_PARENT_ONLY_SUMMARY",
          "summary=%r full=%r" % (r4.get("mode"), r4b.get("code")))

    r5 = T.route_write(topo, "A1", "B1")
    check("⑤ **跨组** ⇒ 拒，并给出 LCA（共同祖先 W）",
          (not r5["allow"]) and r5.get("code") == "G5_CROSS_GROUP_VIA_LCA" and r5.get("via") == "W",
          str(r5)[:160])

    r6 = T.route_write(topo, "W", "A")
    check("⑥ 写**后代**看板 ⇒ 拒 G5_NO_DOWNWARD",
          (not r6["allow"]) and r6.get("code") == "G5_NO_DOWNWARD", str(r6)[:140])

    r7a = T.route_write(topo, "NOPE", "A")
    r7b = T.route_write(topo, "A", "NOPE")
    check("⑦ 不认识的 Cell ⇒ 拒 G5_UNKNOWN_CELL（不假成功）",
          (not r7a["allow"]) and r7a.get("code") == "G5_UNKNOWN_CELL"
          and (not r7b["allow"]) and r7b.get("code") == "G5_UNKNOWN_CELL",
          "src=%r dst=%r" % (r7a.get("code"), r7b.get("code")))

    print("-" * 78)
    passed = sum(1 for _l, ok, _d in ROWS if ok)
    print("合计 %d 格：PASS %d / FAIL %d" % (len(ROWS), passed, len(ROWS) - passed))
    for lb, ok, d in ROWS:
        if not ok:
            print("  FAIL:", lb, "|", str(d)[:240])
    return 0 if passed == len(ROWS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
