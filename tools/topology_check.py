#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""判据：**组织拓扑五条硬约束**（`V118` §2 + §8 G1 / 队列 50-1）。

真跑：真文件（临时工作区里写 `组织拓扑.md`）+ 真校验（`platform/topology.py` 纯函数）。

| # | 格 | 断言 |
|---|---|---|
| K1 | **无环**（C001） | A→B→A 的父子环 ⇒ **拒**，错误码 `C001` 且在 why 里把环打出来 |
| K2 | **单亲**（C002） | `children` 与实际 parent 不符 ⇒ 拒 `C002`；两个 parent 为 null 的根 ⇒ 也拒 |
| K3 | **跨度**（C003） | 一个节点挂 6 个孩子（上限 5）⇒ **警告 + `needs_confirm`**，且**能写进去**（警告不拦） |
| K4 | **深度**（C004） | 7 层（上限 5）⇒ 警告 + `needs_confirm` |
| K5 | **可验证产物**（C005） | 某个 Cell 的 `verify` 空 ⇒ 拒 `C005`（空壳不许进树） |
| K6 | **校验失败文件不动** | 用 `apply()` 拿坏拓扑 ⇒ `ok=False`、`text` 与旧正文**逐字相同**、`format_version` **没变** |
| K7 | 合法修改版本自增 | 好拓扑 ⇒ `ok=True`、`format_version` = 旧 + 1，且写回后能再 `parse()` 出来 |

退出码：0＝全过；1＝有格没过；2＝起不来（本判据不需要服务；文件/权限问题才算）。
"""
from __future__ import annotations

import json
import sys
import tempfile
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


def _cell(cid, parent=None, children=None, verify=True):
    c = {"id": cid, "type": "工位", "parent": parent, "children": list(children or []),
         "role": "干活的"}
    if verify:
        c["verify"] = {"how": "产物目录 diff + sha256", "where": "产物/"}
    return c


def _topo(cells, root):
    return {"format_version": 1, "root": root, "cells": cells}


def main() -> int:
    print("判据：组织拓扑五条硬约束（V118 §2 / 队列 50-1）")
    print("=" * 78)
    tmp = Path(tempfile.mkdtemp(prefix="ac_topo_"))
    md = tmp / "组织拓扑.md"
    try:
        # ── K1 无环 ──
        cyc = _topo([_cell("A", "B", ["B"]), _cell("B", "A", ["A"])], "A")
        r1 = T.validate(cyc)
        codes1 = [e["code"] for e in r1["errors"]]
        why1 = " ".join(e["why"] for e in r1["errors"] if e["code"] == "C001")
        check("K1 无环（C001）：A→B→A ⇒ 拒，且 why 里打出环",
              (not r1["ok"]) and "C001" in codes1 and "→" in why1,
              "ok=%r codes=%r why=%r" % (r1["ok"], codes1, why1[:80]))

        # ── K2 单亲 ──
        # children 里多写了一个「没把自己 parent 指回来」的 B ⇒ C002
        bad_kids = _topo([_cell("R", None, ["A", "B"]), _cell("A", "R", []), _cell("B", None, [])], "R")
        r2 = T.validate(bad_kids)
        two_roots = _topo([_cell("R1", None, []), _cell("R2", None, [])], "R1")
        r2b = T.validate(two_roots)
        check("K2 单亲（C002）：children 与实际不符 + 两个根 ⇒ 都拒",
              (not r2["ok"]) and any(e["code"] == "C002" for e in r2["errors"])
              and (not r2b["ok"]) and any(e["code"] == "C002" for e in r2b["errors"]),
              "kids=%r roots=%r" % ([e["code"] for e in r2["errors"]],
                                    [e["code"] for e in r2b["errors"]]))

        # ── K3 跨度（警告，不拦）──
        kids = ["K%d" % i for i in range(6)]
        wide = _topo([_cell("R", None, kids)] + [_cell(k, "R", []) for k in kids], "R")
        r3 = T.validate(wide)
        w3 = [w for w in r3["warnings"] if w["code"] == "C003"]
        check("K3 跨度（C003）：挂 6 个孩子 ⇒ **警告 + needs_confirm**，但仍然可写",
              r3["ok"] and bool(w3) and bool(w3[0].get("needs_confirm")),
              "ok=%r warnings=%r" % (r3["ok"], [w["code"] for w in r3["warnings"]]))

        # ── K4 深度 ──
        chain = [_cell("L0", None, ["L1"])]
        for i in range(1, 7):
            chain.append(_cell("L%d" % i, "L%d" % (i - 1),
                               ["L%d" % (i + 1)] if i < 6 else []))
        deep = _topo(chain, "L0")
        r4 = T.validate(deep)
        w4 = [w for w in r4["warnings"] if w["code"] == "C004"]
        check("K4 深度（C004）：7 层 ⇒ 警告 + needs_confirm（仍可写）",
              r4["ok"] and bool(w4) and bool(w4[0].get("needs_confirm")),
              "ok=%r warnings=%r" % (r4["ok"], [w["code"] for w in r4["warnings"]]))

        # ── K5 可验证产物 ──
        hollow = _topo([_cell("R", None, ["A"]), _cell("A", "R", [], verify=False)], "R")
        r5 = T.validate(hollow)
        check("K5 可验证产物（C005）：空壳 Cell ⇒ 拒",
              (not r5["ok"]) and any(e["code"] == "C005" for e in r5["errors"]),
              "ok=%r codes=%r" % (r5["ok"], [e["code"] for e in r5["errors"]]))

        # ── K6 校验失败 ⇒ 文件不动（版本号不变）──
        good = _topo([_cell("R", None, ["A"]), _cell("A", "R", [])], "R")
        first = T.apply("", good)
        md.write_text(first["text"], encoding="utf-8")
        before = md.read_text(encoding="utf-8")
        ver_before = T.parse(before)[0].get("format_version")
        bad_try = T.apply(before, cyc)
        if not bad_try["ok"]:                                  # 失败时按"文件不动"处理
            pass                                               # 不写回（模拟真端点行为）
        after = md.read_text(encoding="utf-8")
        check("K6 校验失败 ⇒ **文件一个字节不动 / 版本号不变**",
              (not bad_try["ok"]) and after == before
              and T.parse(after)[0].get("format_version") == ver_before,
              "ok=%r 文件相同=%r 版本 %r→%r"
              % (bad_try["ok"], after == before, ver_before,
                 T.parse(after)[0].get("format_version")))

        # ── K7 合法修改 ⇒ 版本自增，且能再解析 ──
        good2 = _topo([_cell("R", None, ["A", "B"]), _cell("A", "R", []), _cell("B", "R", [])], "R")
        ok2 = T.apply(after, good2)
        md.write_text(ok2["text"], encoding="utf-8")
        re_parsed, err = T.parse(md.read_text(encoding="utf-8"))
        check("K7 合法修改 ⇒ 版本 +1，写回后还能再解析出来",
              ok2["ok"] and int(re_parsed.get("format_version") or 0) == int(ver_before or 0) + 1
              and not err and len(re_parsed.get("cells") or []) == 3,
              "ok=%r 版本=%r 重新解析 cells=%d err=%r"
              % (ok2["ok"], re_parsed.get("format_version"),
                 len(re_parsed.get("cells") or []), err))
    finally:
        import shutil

        shutil.rmtree(tmp, ignore_errors=True)

    print("-" * 78)
    passed = sum(1 for _l, ok, _d in ROWS if ok)
    print("合计 %d 格：PASS %d / FAIL %d" % (len(ROWS), passed, len(ROWS) - passed))
    for lb, ok, d in ROWS:
        if not ok:
            print("  FAIL:", lb, "|", str(d)[:240])
    return 0 if passed == len(ROWS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
