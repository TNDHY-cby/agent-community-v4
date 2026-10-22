#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""第三方代码登记核对（V81 §5）。

扫描仓库里带 `SPDX-License-Identifier` 的文件（那是"从别处搬来"的强信号），
逐个要求在 `THIRD_PARTY_NOTICES.md` 里能对上；对不上就**报错退出 1**。

用法：
    python tools/third_party_check.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent.parent
NOTICE = ROOT / "THIRD_PARTY_NOTICES.md"
SKIP_DIRS = {"__pycache__", ".git", "temp", "node_modules", "data"}


def scan() -> list:
    hits = []
    for p in ROOT.rglob("*.py"):
        if any(part in SKIP_DIRS for part in p.parts):
            continue
        try:
            head = "\n".join(p.read_text(encoding="utf-8", errors="replace").splitlines()[:15])
        except OSError:
            continue
        if "SPDX-License-Identifier" in head:
            rel = str(p.relative_to(ROOT)).replace("\\", "/")
            hits.append(rel)
    return hits


def main() -> int:
    hits = scan()
    if not NOTICE.exists():
        print("[FAIL] 缺少 THIRD_PARTY_NOTICES.md（V81 §5 要求）")
        return 1
    text = NOTICE.read_text(encoding="utf-8", errors="replace")
    missing = [h for h in hits if h not in text]
    if missing:
        print("[FAIL] 这些文件带第三方许可头，但没在 THIRD_PARTY_NOTICES.md 登记：")
        for h in missing:
            print("   ·", h)
        print("  ⇒ 登记（项目/仓库/许可证/文件/改动说明），或确认它们其实是本项目自己写的缩进头。")
        return 1
    print("[OK] 第三方代码登记一致（扫描到 %d 个带 SPDX 头的文件，均已登记）" % len(hits))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
