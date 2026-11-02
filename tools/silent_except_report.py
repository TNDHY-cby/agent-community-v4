# -*- coding: utf-8 -*-
"""tools/silent_except_report.py —— **静默吞异常**的存量统计与"只减不增"闸门。

## 为什么有它（外部审查 P3-7）

审计点名：生产代码里有 **78 处** `except …: pass`（tests 除外），失败既无日志也无返回值标记。
项目自己的准则是「**失败要说话**」，所以这些地方该至少留一条"我知道自己在吞什么"的理由
（`# noqa: BLE001 — 有意静默：<理由>`），或者补日志。

但**一把梭全改 78 处**风险很高（很多是刻意的兜底：解析失败就用默认值、探测不到就返回空）。
⇒ 本轮先建**闸门**：把现有数量记为基线，**新代码不许增加**；要新增必须带 `noqa` 理由。
存量清理按域分批做（每批都要有判据）。

用法：
    python tools/silent_except_report.py            # 打印每个文件的条数与总数
    python tools/silent_except_report.py --check    # 与基线比较：超了退 1（--write 可重设基线）
    python tools/silent_except_report.py --write    # 把当前数量写成新基线（**只在清理后变小的时候用**）
"""
from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
BASELINE = ROOT / "tools" / "silent_except_baseline.json"
SCAN_DIRS = ("agent_community", "tools", "tests")
SKIP_PARTS = {"tests", "__pycache__"}


def _is_silent(node: ast.ExceptHandler) -> bool:
    """`except …: pass`（body 只有 pass，或只有一条 pass + 注释不算）。"""
    body = [b for b in node.body if not isinstance(b, ast.Expr) or not isinstance(getattr(b, "value", None), ast.Constant)]
    return len(body) == 1 and isinstance(body[0], ast.Pass)


def _has_reason(source_lines: list, node: ast.ExceptHandler) -> bool:
    """这一处是否**已标记为"故意静默"**（`# noqa: BLE001` 或 logger 记录）。

    口径说明（2026-10-21 实测）：本仓库历史上大量使用 `# noqa: BLE001`，但多数**没写理由句子**。
    要求 224 处都补一句理由＝大改动静默兜底，风险 > 收益 ⇒ 本闸门认两种标记：
      ① `noqa`（任何一种写法，代表"作者当场知道自己在吞"）；
      ② 真的记了日志（`logger.*` / `log.*`）。
    **新增**处若两者都没有，才算"漏写"，闸门会拦。
    """
    start = node.lineno - 1
    end = getattr(node, "end_lineno", start + 1)
    chunk = "\n".join(source_lines[start:end])
    return ("noqa" in chunk.lower()) or ("logger" in chunk) or ("log." in chunk)


def scan() -> dict:
    out = {}
    for d in SCAN_DIRS:
        for p in (ROOT / d).rglob("*.py"):
            parts = set(p.relative_to(ROOT).parts)
            # ★2026-11-02：**数据目录里的历史快照不算"新写代码"** ——
            #   `data/backups/pre-reset-*/` 会把旧桥脚本（含 `except: pass`）整份拷进来，
            #   一扫就假红（本次就是：备份里一份老 `bridge.py` 让 37→38 被判"静默变多"）。
            #   备份是历史资产，不是新增源码 ⇒ 跳过 `data/` 下任何东西。
            if parts & SKIP_PARTS or ".bak" in p.name or "data" in parts:
                continue
            try:
                src = p.read_text(encoding="utf-8", errors="replace")
                tree = ast.parse(src)
            except Exception:                                # noqa: BLE001
                continue
            lines = src.splitlines()
            hits = [n for n in ast.walk(tree)
                    if isinstance(n, ast.ExceptHandler) and _is_silent(n) and not _has_reason(lines, n)]
            if hits:
                out[str(p.relative_to(ROOT)).replace("\\", "/")] = len(hits)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="静默吞异常：统计 / 闸门")
    ap.add_argument("--check", action="store_true", help="与基线比较，超出退 1")
    ap.add_argument("--write", action="store_true", help="把当前数量写成基线")
    args = ap.parse_args(argv)

    cur = scan()
    total = sum(cur.values())
    if args.write:
        BASELINE.write_text(json.dumps({"total": total, "files": cur}, ensure_ascii=False, indent=2) + "\n",
                            encoding="utf-8")
        print("已写入基线：%s（共 %d 处，%d 个文件）" % (BASELINE.name, total, len(cur)))
        return 0

    if not args.check:
        print("静默吞异常（`except: pass` 且没写理由）：**%d 处**，来自 %d 个文件" % (total, len(cur)))
        for f, n in sorted(cur.items(), key=lambda kv: -kv[1])[:15]:
            print("  %-46s %d" % (f, n))
        print("说明：这些不是「必须马上改」，而是「**只减不增**」——"
              "新增一律要带 `# noqa: BLE001 — 有意静默：<理由>`。")
        return 0

    if not BASELINE.exists():
        print("[FAIL] 没有基线文件（先跑一次 --write）")
        return 1
    base = json.loads(BASELINE.read_text(encoding="utf-8"))
    grew = {f: (cur.get(f, 0), n) for f, n in (base.get("files") or {}).items() if cur.get(f, 0) > n}
    new_files = {f: n for f, n in cur.items() if f not in (base.get("files") or {})}
    if grew or new_files:
        print("[FAIL] 静默吞异常**变多了**（基线 %d → 现在 %d）" % (base.get("total", 0), total))
        for f, (now, before) in grew.items():
            print("  %s: %d → %d" % (f, before, now))
        for f, n in new_files.items():
            print("  新增文件 %s: %d" % (f, n))
        print("→ 新写的 `except: pass` 请加理由：`# noqa: BLE001 — 有意静默：<为什么>`；或改用 logger.debug(..., exc_info=True)")
        return 1
    print("[OK] 静默吞异常没有变多（基线 %d，现在 %d）" % (base.get("total", 0), total))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
