#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""判据：**审忆员扩面**（`V118` §8 G6 / 队列 50-4）—— 三类体检真跑。

真跑：临时工作区（真写 `组织拓扑.md` / `asset/风格指南.md` / 声明的看板）。

| # | 格 | 断言 |
|---|---|---|
| ① | 三类都在 | `categories == [topology, assets, authority]`，响应带 `no_score` |
| ② | 拓扑有环 ⇒ 报告里出现 `AUD_C001`（error） | 真造一个环 |
| ③ | 风格指南全是「待填」⇒ `AUD_STYLE_BLANK`（info，且**不评分**） | 说清哪几维空着 |
| ④ | 声明了看板但文件不在 ⇒ `AUD_BOARD_DECLARED_MISSING`（warn） | 声明与现实两张皮 |
| ⑤ | **只读**：跑完前后文件列表逐字相同 | 体检不许改盘 |
| ⑥ | 干净的工作区 ⇒ `ok=True` 且 `items` 为空 | 不制造噪音 |

退出码：0＝全过；1＝有格没过。
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from agent_community.platform import audit_checks as A  # noqa: E402
from agent_community.platform import topology as T  # noqa: E402

ROWS: list = []


def check(label: str, ok: bool, detail: str = "") -> None:
    ROWS.append((label, bool(ok), detail))
    print("  %s %s%s" % ("PASS" if ok else "FAIL", label,
                        ("　（" + detail + "）") if detail and not ok else ""))


class _WS:
    def __init__(self, path: Path):
        self.workspace_dir = str(path)
        self.workshop_id = "judge-ws"
        self.name = "判据工作间（示例）"


def _files(root: Path) -> list:
    return sorted(str(p.relative_to(root)).replace("\\", "/") for p in root.rglob("*") if p.is_file())


def main() -> int:
    print("判据：审忆员扩面（V118 §8 G6 / 队列 50-4）")
    print("=" * 78)
    tmp = Path(tempfile.mkdtemp(prefix="ac_audit_"))
    try:
        ws = tmp / "ws"
        ws.mkdir(parents=True)
        mind = ws / "_mind"
        (mind / "asset" / "经典案例").mkdir(parents=True)
        # 干净场景要有**新鲜**案例（"案例陈旧"验的是文件时间，空目录也算陈旧）
        (mind / "asset" / "经典案例" / "本月案例.md").write_text("# 案例\n", encoding="utf-8")
        (mind / "asset" / "失败教训.md").write_text("# 失败教训\n", encoding="utf-8")

        # ── ⑥ 先验"干净"：有拓扑、风格指南填好、看板都在 ⇒ ok=True 且没条目 ──
        cells = [{"id": "W", "parent": None, "children": [], "verify": {"how": "diff", "where": "产物/"},
                  "board_path": "组织/W/看板.md"}]
        good = {"root": "W", "cells": cells}
        (ws / "组织拓扑.md").write_text(T.dump(good), encoding="utf-8")
        (ws / "组织" / "W").mkdir(parents=True)
        (ws / "组织" / "W" / "看板.md").write_text("# 看板\n", encoding="utf-8")
        (mind / "asset" / "风格指南.md").write_text(
            "# 风格指南\n\n" + "\n".join("- %s: 写好了" % d for d in A.DIMS) + "\n- 成员数: 2\n",
            encoding="utf-8")
        r0 = A.run(_WS(ws), mind_home=str(mind), members_count=2)   # 成员数要传对（写死 0 会报默契退化）
        check("⑥ 干净的工作区 ⇒ ok=True 且 items 为空（不制造噪音）",
              r0["ok"] and not r0["items"], "ok=%r items=%r"
              % (r0["ok"], [(i["code"], i["why"][:40]) for i in r0["items"]]))

        # ── ① 三类都在 + no_score ──
        check("① 三类都有 + 声明不评分（no_score，无 score/rank 字段）",
              list(r0["categories"]) == list(A.CATEGORIES) and r0.get("no_score") is True
              and "score" not in json.dumps({k: v for k, v in r0.items() if k != "no_score"},
                                            ensure_ascii=False).lower(),
              "categories=%r" % (r0.get("categories"),))

        # ── ② 拓扑有环 ⇒ AUD_C001 ──
        cyc = {"root": "A", "cells": [
            {"id": "A", "parent": "B", "children": ["B"], "verify": {"how": "x", "where": "y"}},
            {"id": "B", "parent": "A", "children": ["A"], "verify": {"how": "x", "where": "y"}}]}
        (ws / "组织拓扑.md").write_text(T.dump(cyc), encoding="utf-8")
        r2 = A.run(_WS(ws), mind_home=str(mind))
        codes2 = [i["code"] for i in r2["items"] if i["category"] == "topology"]
        check("② 拓扑有环 ⇒ 报告里出现 AUD_C001（error）",
              "AUD_C001" in codes2 and any(i["level"] == "error" and i["code"] == "AUD_C001"
                                           for i in r2["items"]),
              "codes=%r" % codes2[:5])

        # ── ③ 风格指南全「待填」⇒ AUD_STYLE_BLANK ──
        (ws / "组织拓扑.md").write_text(T.dump(good), encoding="utf-8")
        (mind / "asset" / "风格指南.md").write_text(
            "# 风格指南\n\n" + "\n".join("- %s: （待填：x）" % d for d in A.DIMS) + "\n",
            encoding="utf-8")
        r3 = A.run(_WS(ws), mind_home=str(mind))
        blank = [i for i in r3["items"] if i["code"] == "AUD_STYLE_BLANK"]
        check("③ 风格指南全「待填」⇒ AUD_STYLE_BLANK（info，说清哪几维空着）",
              bool(blank) and blank[0]["level"] == "info" and "代码审美" in blank[0]["why"],
              "items=%r" % [(i["code"], i["level"]) for i in r3["items"]][:5])

        # ── ④ 声明了看板但文件不在 ⇒ AUD_BOARD_DECLARED_MISSING ──
        (ws / "组织" / "W" / "看板.md").unlink()
        r4 = A.run(_WS(ws), mind_home=str(mind))
        miss = [i for i in r4["items"] if i["code"] == "AUD_BOARD_DECLARED_MISSING"]
        check("④ 声明了看板但文件不在 ⇒ AUD_BOARD_DECLARED_MISSING（warn）",
              bool(miss) and miss[0]["level"] == "warn", "items=%r"
              % [(i["code"], i["level"]) for i in r4["items"]][:6])

        # ── ⑤ 只读：跑完前后文件列表逐字相同 ──
        before = _files(ws)
        A.run(_WS(ws), mind_home=str(mind))
        after = _files(ws)
        check("⑤ **只读**：体检跑完前后文件列表逐字相同（不改盘）",
              before == after, "文件 %d→%d" % (len(before), len(after)))
    finally:
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
