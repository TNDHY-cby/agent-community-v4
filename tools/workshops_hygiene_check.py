#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""判据：**工作间数据卫生**（孤儿目录 / 账上有目录没有）。

## 为什么要有它（2026-11-09 实测发现）

老板问「19 还是 20」的时候我顺手对了一下磁盘：`data/workshops/` 下有 **6 个目录**
没有任何工作间声明使用（`ws_176cde29` 等）—— 删工作间时"目录移入回收站"这一步
在历史数据上没生效，于是**孤儿目录一直堆着**（现在 128 个文件 / 0.2 MB，用久了会涨）。

## 判据（只读，默认）

| 类别 | 含义 | 怎么判 |
|---|---|---|
| **孤儿目录** | `data/workshops/` 下有、但没有任何工作间的 `workspace_dir` 指向它 | 目录名集合 － 被引用集合 |
| **目录缺失** | 某工作间声明用 `data/workshops/<x>`，但那目录不在 | 只对"指向 data/workshops 下的"判（指向别处的绝对路径不判） |

退出码：0＝干净；1＝有孤儿或缺失（**有话说**）；2＝读不到数据（如实说）。

## 用法

```bash
python tools/workshops_hygiene_check.py              # 只报告
python tools/workshops_hygiene_check.py --json       # 机器可读
python tools/workshops_hygiene_check.py --quarantine # 把孤儿目录移到 data/回收站/orphan-<时间戳>/（可恢复）
```
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
if _ROOT not in sys.path:
    sys.path.append(_ROOT)


def _data_dir() -> Path:
    from agent_community.platform.env_spec import data_dir

    return Path(data_dir())


def scan() -> dict:
    dd = _data_dir()
    wfile = dd / "workshops.json"
    root = dd / "workshops"
    if not wfile.is_file():
        return {"ok": False, "code": "NO_WORKSHOPS_JSON", "error": "没有 %s" % wfile}
    try:
        d = json.loads(wfile.read_text(encoding="utf-8"))
    except Exception as e:                                     # noqa: BLE001 — 读不动要说清
        return {"ok": False, "code": "BAD_WORKSHOPS_JSON",
                "error": "读不了 workshops.json：%s: %s" % (type(e).__name__, e)}
    if not isinstance(d, dict):
        return {"ok": False, "code": "BAD_WORKSHOPS_JSON", "error": "workshops.json 不是对象"}
    dirs = {x.name for x in root.iterdir() if x.is_dir()} if root.is_dir() else set()
    used, missing = set(), []
    for wid, w in d.items():
        wp = str((w or {}).get("workspace_dir") or "")
        if not wp:
            continue
        p = Path(wp)
        used.add(p.name)
        # 只对"落在 data/workshops 下"的声明判缺失（指向别处的绝对路径不判 —— 那是用户自己的目录）
        try:
            if p.parent.resolve() == root.resolve() and not p.is_dir():
                missing.append({"workshop_id": wid, "name": str((w or {}).get("name") or ""),
                                "dir": wp})
        except OSError:                                        # noqa: BLE001 — resolve 失败不判
            continue

    def size(p: Path) -> tuple:
        n = b = 0
        for f in p.rglob("*"):
            if f.is_file():
                n += 1
                try:
                    b += f.stat().st_size
                except OSError:                                # noqa: BLE001 — 有意静默：单个文件 stat 不到就跳过，
                    pass                                       #   只影响"孤儿占多大"的参考值，不影响判据结论（数目录/文件数照常）
        return n, b

    orphans = []
    for name in sorted(dirs - used):
        n, b = size(root / name)
        orphans.append({"dir": name, "path": str(root / name), "files": n, "bytes": b})
    return {"ok": True, "data_dir": str(dd), "workshops": len(d), "dirs": len(dirs),
            "orphans": orphans, "missing": missing,
            "totals": {"orphans": len(orphans), "missing": len(missing),
                       "orphan_files": sum(o["files"] for o in orphans),
                       "orphan_kb": round(sum(o["bytes"] for o in orphans) / 1024.0, 1)}}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--quarantine", action="store_true",
                    help="把孤儿目录移到 data/回收站/orphan-<时间戳>/（可恢复，不物理删）")
    a = ap.parse_args()

    out = scan()
    if not out.get("ok"):
        print("[FAIL] %s" % out.get("error"))
        return 2
    if a.json:
        print(json.dumps(out, ensure_ascii=False, indent=1))
    else:
        print("工作间数据卫生（数据目录 %s）" % out["data_dir"])
        print("=" * 78)
        print("工作间 %d 个 / data/workshops 下目录 %d 个" % (out["workshops"], out["dirs"]))
        print("孤儿目录 %d 个（%d 个文件 / %.1f KB）"
              % (out["totals"]["orphans"], out["totals"]["orphan_files"], out["totals"]["orphan_kb"]))
        for o in out["orphans"][:12]:
            print("   - %-18s %4d 文件 %7.1f KB" % (o["dir"], o["files"], o["bytes"] / 1024.0))
        if len(out["orphans"]) > 12:
            print("   …另 %d 个" % (len(out["orphans"]) - 12))
        print("账上声明但目录不在 %d 个" % out["totals"]["missing"])
        for m in out["missing"][:8]:
            print("   - %s（%s）→ %s" % (m["workshop_id"], m["name"] or "?", m["dir"]))
        print("-" * 78)
        if not out["totals"]["orphans"] and not out["totals"]["missing"]:
            print("结论：**干净**（没有孤儿目录，也没有声明了却不在的目录）")
        else:
            print("结论：**不干净** —— 孤儿目录 %d 个 / 缺失 %d 个"
                  % (out["totals"]["orphans"], out["totals"]["missing"]))
            print("       清理（移到回收站，可恢复）：python tools/workshops_hygiene_check.py --quarantine")

    if a.quarantine and out["orphans"]:
        dd = Path(out["data_dir"])
        dest = dd / "回收站" / ("orphan-" + time.strftime("%Y%m%d-%H%M%S"))
        dest.mkdir(parents=True, exist_ok=True)
        moved = []
        for o in out["orphans"]:
            try:
                shutil.move(o["path"], str(dest / o["dir"]))
                moved.append(o["dir"])
            except OSError as e:                               # noqa: BLE001 — 单个失败要说
                print("[WARN] 移不动 %s：%s: %s" % (o["dir"], type(e).__name__, e))
        print("已移入回收站 %d 个：%s" % (len(moved), dest))
        print("要恢复：把它们从回收站移回 data/workshops/ 即可")
        return 0 if len(moved) == len(out["orphans"]) else 1

    return 0 if not out["totals"]["orphans"] and not out["totals"]["missing"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
