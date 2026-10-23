# -*- coding: utf-8 -*-
"""状态快照的**查看与恢复**（配合 `platform/state_backup.py` 的自动快照）。

```bash
python tools/state_restore.py --list                 # 看有哪些快照（新的在前）
python tools/state_restore.py --backup-now           # 立刻拍一张（不重启服务）
python tools/state_restore.py --restore <快照名> --dry-run   # 只看会覆盖哪些文件
python tools/state_restore.py --restore <快照名>             # 真恢复（会先给当前状态拍一张）
```

## 三条安全纪律

1. **服务在跑就不许恢复**：运行中的进程内存里是旧状态，恢复后它下一次落盘会把恢复白费掉。
   要恢复请先停服务（`python temp/quiet.py stop`），或用 `--force` 明确表示你知道后果。
2. **恢复前先给当前状态拍一张**（原因标 `pre-restore`）：万一恢复错了还能退回去。
3. **空快照不默认恢复**：`_meta.json` 里 `empty=true` 的快照（就是"被清空那一刻"）必须先加
   `--allow-empty`，避免把空状态又倒回去一次。

退出码：0=成功；1=拒绝/失败；2=没跑成（例如快照不存在）。
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent_community.platform import state_backup as bk  # noqa: E402


def _platform_alive() -> bool:
    """服务在不在跑（只用端口判，不去动平台）。"""
    try:
        from agent_community.port_resolve import probe

        return bool(probe(None))
    except Exception:                                        # noqa: BLE001 — 判不了就当"没在跑"，后面还有 --force
        return False


def cmd_list() -> int:
    snaps = bk.list_snapshots()
    print("快照目录：%s" % bk.backup_root())
    if not snaps:
        print("（还没有任何快照。平台起来时会自动拍；也可以 `--backup-now` 立刻拍一张）")
        return 0
    print("%-34s %-19s %-6s %6s  %s" % ("名称", "时间", "空?", "大小", "文件数"))
    for s in snaps:
        print("%-34s %-19s %-6s %5dKB  %d" % (s["name"], s["at"] or "(无 meta)", "是" if s["empty"] else "否",
                                              max(1, s["bytes"] // 1024), len(s["files"])))
    return 0


def cmd_backup_now(reason: str) -> int:
    res = bk.snapshot(reason or "manual")
    if res.get("ok"):
        print("[OK] 已拍快照：%s（%d 个文件%s）"
              % (res["dir"], len(res["files"]), "；注意：**状态是空的**" if res.get("empty") else ""))
        if res.get("pruned"):
            print("     清掉旧快照：%s" % "、".join(res["pruned"]))
        return 0
    print("[FAIL] 没拍成：%s" % res.get("why"))
    return 1


def cmd_restore(name: str, dry_run: bool, force: bool, allow_empty: bool) -> int:
    snaps = {s["name"]: s for s in bk.list_snapshots()}
    snap = snaps.get(name)
    if snap is None:
        # 允许传短名（唯一前缀）
        cand = [s for n, s in snaps.items() if n.startswith(name)]
        if len(cand) == 1:
            snap = cand[0]
        else:
            print("[没跑成] 找不到快照 %r（用 `--list` 看看；给了 %d 个候选）" % (name, len(cand)))
            return 2
    if snap["empty"] and not allow_empty:
        print("[FAIL] 这个快照本身是**空的**（就是「数据被清那一刻」）。真要倒回去请加 --allow-empty")
        return 1
    if _platform_alive() and not force:
        print("[FAIL] 平台正在跑（端口在听）——恢复会被它内存里的旧状态覆盖掉。\n"
              "       先停服务：`python temp/quiet.py stop`，或加 --force（你知道后果）")
        return 1

    data = Path(bk._data_dir())
    targets = [data / n for n in snap["files"]]
    print("快照：%s（%s，%d 个文件）" % (snap["name"], snap["at"] or "无 meta", len(snap["files"])))
    for t in targets:
        cur = t.stat().st_size if t.exists() else -1
        print("  %-22s %s  →  %s" % (t.name, ("当前 %d 字节" % cur) if cur >= 0 else "当前不存在",
                                     "将覆盖" if cur >= 0 else "将新建"))

    # ★「备份」的定义是**能还原**：动手前先校验这份快照本身没坏（sha256/字节数）
    v = bk.verify(snap)
    if v.get("verifiable") is False:
        # 老版本快照没记指纹 ⇒ **无法校验 ≠ 一定坏**。警告后放行（拦下来只会让人以为备份全废了）
        print("  ⚠️ 这份快照没法校验（%s）；内容仍按原样还原，建议恢复后立刻 --backup-now 拍一份带指纹的" % v.get("why"))
    elif not v.get("ok"):
        print("[FAIL] 这份快照**校验不过**（%s）：%s"
              % (v.get("why"), "；".join(v.get("bad") or [])[:300] or "（无细节）"))
        print("       坏掉的备份不能用（真倒回去只会二次损坏）。换一份：--list 看看别的")
        if not force:
            return 1
        print("       （--force：按你说的，继续倒）")
    else:
        print("  ✔ 快照校验通过（%d 个文件的 sha256 与记录一致）" % v.get("checked", 0))

    if dry_run:
        print("\n（--dry-run：什么都没写）")
        return 0

    pre = bk.snapshot("pre-restore")
    print("恢复前已给当前状态拍快照：%s" % (pre.get("dir") if pre.get("ok") else "（没拍成：%s）" % pre.get("why")))

    res = bk.copy_into_place(snap, data)
    if not res["ok"]:
        print("[FAIL] 有 %d 个文件没恢复好：%s" % (len(res["bad"]), "；".join(res["bad"])))
        return 1
    print("[OK] 已恢复 %d 个文件（逐个回读校验一致）。**重启平台**后才会生效"
          "（`python temp/quiet.py start` 或双击）" % len(res["restored"]))
    return 0


def cmd_verify(name: str = "") -> int:
    """校验快照能不能用（sha256/字节数与记录一致）。不给名字就校验全部。"""
    snaps = bk.list_snapshots()
    if not snaps:
        print("[没跑成] 还没有任何快照")
        return 2
    if name:
        picked = [s for s in snaps if s["name"] == name] or \
                 [s for s in snaps if s["name"].startswith(name)]
        if not picked:
            print("[没跑成] 找不到快照 %r" % name)
            return 2
    else:
        picked = snaps
    bad, unknown = 0, 0
    for s in picked:
        v = bk.verify(s)
        if v.get("ok"):
            print("[OK]   %-34s %d 个文件校验一致" % (s["name"], v.get("checked", 0)))
        elif v.get("verifiable") is False:
            unknown += 1
            print("[无法校验] %-30s %s" % (s["name"], v.get("why") or ""))
        else:
            bad += 1
            print("[FAIL] %-34s %s%s" % (s["name"], v.get("why") or "",
                                         ("：" + "；".join(v.get("bad") or [])[:200]) if v.get("bad") else ""))
    if bad:
        print("结论：**%d 份快照坏了**（内容与记录不一致，别拿它恢复）" % bad)
        return 1
    if unknown:
        print("结论：%d 份可校验且一致，**%d 份无法校验**（老版本没记指纹：不代表坏，恢复时只给警告）"
              % (len(picked) - unknown, unknown))
        return 2
    print("结论：**%d 份快照都可用**" % len(picked))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="状态快照查看/校验/恢复")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--list", action="store_true", help="列出快照")
    g.add_argument("--verify", metavar="NAME", nargs="?", const="", help="校验快照完整性（不给名字＝全部）")
    g.add_argument("--backup-now", action="store_true", help="立刻拍一张")
    g.add_argument("--restore", metavar="NAME", help="按名字（或唯一前缀）恢复")
    ap.add_argument("--reason", default="manual", help="--backup-now 时的原因标记")
    ap.add_argument("--dry-run", action="store_true", help="只打印会做什么")
    ap.add_argument("--force", action="store_true", help="平台在跑也强行恢复／快照校验不过也硬倒（危险）")
    ap.add_argument("--allow-empty", action="store_true", help="允许用「空快照」恢复")
    a = ap.parse_args()

    if a.list:
        return cmd_list()
    if a.verify is not None:
        return cmd_verify(a.verify)
    if a.backup_now:
        return cmd_backup_now(a.reason)
    return cmd_restore(a.restore, a.dry_run, a.force, a.allow_empty)


if __name__ == "__main__":
    raise SystemExit(main())
