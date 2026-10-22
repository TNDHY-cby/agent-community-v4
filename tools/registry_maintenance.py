#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""名册维护（V85）：按 90 天清理健康明细。**默认 dry-run**，要真删加 --apply。"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent_community.platform import registry_health as rh  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=0, help="保留多少天明细（默认取 V82 口径 90）")
    ap.add_argument("--apply", action="store_true", help="真的删（不加就只报告）")
    a = ap.parse_args()
    out = rh.purge_detail(days=a.days, dry_run=not a.apply)
    print("名册健康明细清理 %s（保留 %d 天）" % ("已执行" if out["applied"] else "dry-run", out["keep_days"]))
    if not out["dropped"]:
        print("  没有需要清理的明细")
    for hid, n in out["dropped"].items():
        print("  · %-32s 将清理 %d 条明细" % (hid, n))
    if not out["applied"] and out["dropped"]:
        print("要真删请加 --apply（汇总与隐藏/申诉记录不会动）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
