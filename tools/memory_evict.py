#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""**经验淘汰**（`V141` §3.2 R2 / 队列 49-3）：只动 `record` 档，归档不物理删。

## 为什么要它

`memories/` 是 append-only 的 —— 只增不减。用久了会**污染检索面**：
真正能复用的经验被一堆"一次性记录"埋掉。储藏根层的治理参数写着"**可淘汰**"，
但淘汰不能凭感觉：**只有 `record` 档能被自动淘汰**，`reusable` 档要人决定。

## 判据（三条，都在代码里）

1. 只动 `grade=record`（`reusable` 一条都不动，只列出来说"要人决定"）；
2. 超过 N 天没人动（默认 30，`--days`）；
3. `--apply` **移进 `<心智根>/<key>/archive/memories/`**，**不物理删**（可恢复）。

## 用法

```bash
python tools/memory_evict.py --dry-run           # 只看（默认就是 dry-run，不写盘）
python tools/memory_evict.py --apply             # 真移入 archive/
python tools/memory_evict.py --days 60 --apply   # 放宽到 60 天
```

退出码：0＝没有可淘汰项（或已按 --apply 处理完）；1＝有可淘汰项（dry-run 时"有话说"）；2＝读不到心智根。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
if _ROOT not in sys.path:
    sys.path.append(_ROOT)

GRADE_RE = re.compile(r"^-\s*档[:：]\s*(reusable|record)", re.M)
TIME_RE = re.compile(r"^-\s*时间[:：]\s*(.+)$", re.M)


def _mind_root() -> Path:
    env = str(os.environ.get("AC_MIND_ROOT") or "").strip()
    if env:
        return Path(env)
    from agent_community.platform.env_spec import data_dir

    return Path(data_dir()) / "minds"


def _mtime_of(text: str, fallback: float) -> float:
    """文件头里的 `- 时间:` 优先（人话时间），解析不了用文件 mtime（如实降级）。"""
    m = TIME_RE.search(text or "")
    if m:
        s = m.group(1).strip()
        for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                return time.mktime(time.strptime(s[:len(time.strftime(fmt))], fmt))
            except (ValueError, TypeError):
                continue
    return fallback


def scan(days: int) -> dict:
    root = _mind_root()
    if not root.is_dir():
        return {"ok": False, "code": "NO_MIND_ROOT", "error": "心智根不在：%s" % root}
    now = time.time()
    cutoff = now - days * 86400
    cand, keep_reusable, no_grade = [], [], []
    for f in root.rglob("memories/*.md"):
        if "archive" in f.parts:                               # 已经在归档里的不再扫
            continue
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
            st = f.stat()
        except OSError as e:                                   # noqa: BLE001 — 读不到就跳过，但记下来
            no_grade.append({"file": str(f), "why": "%s: %s" % (type(e).__name__, e)})
            continue
        g = (GRADE_RE.search(text or "") or [None, ""])[1] if GRADE_RE.search(text or "") else ""
        row = {"file": str(f), "key": f.relative_to(root).parts[0], "grade": g or "(未标档)",
               "age_days": round((now - max(_mtime_of(text, st.st_mtime), 0)) / 86400, 1)}
        if not g:
            no_grade.append(row)
        elif g == "reusable":
            keep_reusable.append(row)
        elif _mtime_of(text, st.st_mtime) < cutoff:
            cand.append(row)
    return {"ok": True, "mind_root": str(root), "days": days,
            "candidates": sorted(cand, key=lambda r: -r["age_days"]),
            "reusable_kept": keep_reusable, "no_grade": no_grade}


def apply_evict(rows: list, root: Path) -> dict:
    moved, failed = [], []
    for r in rows:
        src = Path(r["file"])
        dest_dir = Path(root) / r["key"] / "archive" / "memories"
        try:
            dest_dir.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dest_dir / src.name))
            moved.append(str(dest_dir / src.name))
        except OSError as e:                                   # noqa: BLE001 — 单个失败要说清，不假装成功
            failed.append({"file": str(src), "error": "%s: %s" % (type(e).__name__, e)})
    return {"moved": moved, "failed": failed}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30, help="超过多少天没人动算可淘汰（默认 30）")
    ap.add_argument("--apply", action="store_true", help="真移入 archive/（不给就只 dry-run）")
    # 设计稿（V141 §3.2 R2）写的就是 `--dry-run/--apply` 两个开关 ⇒ 显式给 `--dry-run`，
    # 与 `--apply` 互斥；不给任何开关时**默认也是 dry-run**（三处口径一致）。
    ap.add_argument("--dry-run", action="store_true", help="只看不动手（默认行为，写出来是为了显式）")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    if a.apply and a.dry_run:
        print("[FAIL] `--apply` 与 `--dry-run` 只能给一个（一个动手一个只看）")
        return 2
    out = scan(a.days)
    if not out.get("ok"):
        print("[FAIL] %s" % out.get("error"))
        return 2
    rows = out["candidates"]
    if a.json:
        print(json.dumps(out, ensure_ascii=False, indent=1))
    else:
        print("经验淘汰（心智根 %s，阈值 %d 天）" % (out["mind_root"], a.days))
        print("=" * 78)
        print("可淘汰（`record` 档且超过阈值）：**%d 条**" % len(rows))
        for r in rows[:15]:
            print("   - %-46s %6.1f 天" % (r["file"].split("memories")[-1], r["age_days"]))
        if len(rows) > 15:
            print("   …另 %d 条" % (len(rows) - 15))
        print("`reusable` 档（**不受自动淘汰**，要人决定）：%d 条" % len(out["reusable_kept"]))
        print("没有标档的老条目（R1 之前写的）：%d 条 —— 不会被自动淘汰（分不清档，不动手）"
              % len(out["no_grade"]))
        print("-" * 78)
        if not rows:
            print("结论：没有可淘汰的条目（干净）")
    if a.apply and rows:
        res = apply_evict(rows, Path(out["mind_root"]))
        print("已移入 archive/ **%d 条**（**没有物理删**，可恢复）：" % len(res["moved"]))
        for m in res["moved"][:10]:
            print("   →", m)
        if res["failed"]:
            print("[WARN] 有 %d 条移不动：" % len(res["failed"]))
            for f in res["failed"][:5]:
                print("   ", f["file"], "|", f["error"])
            return 1
        return 0
    if a.apply and not rows:
        print("`--apply` 已给，但没有可淘汰项 ⇒ 什么都没做")
        return 0
    return 1 if rows else 0


if __name__ == "__main__":
    raise SystemExit(main())
