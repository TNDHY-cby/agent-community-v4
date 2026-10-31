# -*- coding: utf-8 -*-
"""给真机上的每个工作间落一次名单镜像（2026-10-30，验证新代码在真数据上能写）。

用法：python temp/_write_mirrors_live.py [--dry-run]
"""
import json
import os
import sys
import urllib.request
from pathlib import Path

# tools/ 是脚本目录，直接 `python tools/x.py` 时包根不在 sys.path 上 —— 显式加仓库根
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

BASE = "http://127.0.0.1:%s" % os.environ.get("AC_PORT", "18920")


def main() -> int:
    dry = "--dry-run" in sys.argv
    ws = json.load(urllib.request.urlopen(BASE + "/api/workshops", timeout=15))["workshops"]
    tgt = [w for w in ws if w.get("workspace_dir")]
    print("有工作区路径的工作间：%d" % len(tgt))
    from agent_community.platform import v5_roster as R

    ok = skip = 0
    for w in tgt:
        wid = w["workshop_id"]
        ro = json.load(urllib.request.urlopen(BASE + "/api/workshop/%s/roster" % wid, timeout=15))
        n = len(ro.get("members", []))
        if dry:
            print("  [dry] %-10s 名单 %d 人 → 会写 %s" % (wid, n, w["workspace_dir"]))
            continue
        out = R.write_mirror(wid, w["workspace_dir"])
        if out.get("ok"):
            ok += 1
            print("  ✓ %-10s 名单 %d 人 → %s" % (wid, n, out["path"]))
        else:
            skip += 1
            print("  · %-10s 跳过：%s" % (wid, out.get("why")))
    print("合计：写成功 %d，跳过 %d" % (ok, skip))
    return 0


if __name__ == "__main__":
    sys.exit(main())
