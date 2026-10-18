# -*- coding: utf-8 -*-
"""**核心体检一条命令**：六协议矩阵 + 闭环消费 + acp 真拉起 + 桥产物落点。

老板 2026-10-18：「先把核心做好：各种协议、桥、唤醒脚本」→ 由此立了三项判据，
它们各自是独立工具；本文件把它们**合成一条命令**，再补一项"跑完仓库干净没被污染"。

```bash
python tools/core_check.py            # 全跑（约 2 分钟）
python tools/core_check.py --plan     # 只打印会跑什么
```

**退出码**：0＝全过；1＝有项红；2＝有项**没跑成**（容器/平台缺件，如实算"部分"）。

| # | 项 | 判据来源 |
|---|---|---|
| 1 | 六协议矩阵（登记→叫醒脚本→桥模板→生成桥+自检→投递） | `tools/protocol_matrix.py` |
| 2 | 闭环消费（派活→桥搬件→agent 回执→桥转发→单实例锁） | 同上，第 ⑥ 段 |
| 3 | acp 真拉起 + ACP 一轮对话 | `tools/acp_core_check.py` |
| 4 | **桥产物落点**：跑完这些检查后，仓库 `agent_community/bridges/` 不许多出目录 | 本文件快照对比 |
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent.parent
BRIDGES = ROOT / "agent_community" / "bridges"


def _snapshot_bridges() -> dict:
    """给"生成的桥产物目录"拍快照（它们是运行期产物，不该由检查工具留在仓库里）。"""
    if not BRIDGES.exists():
        return {}
    out = {}
    for p in BRIDGES.rglob("*"):
        try:
            st = p.stat()
            out[str(p.relative_to(BRIDGES))] = (st.st_mtime_ns, st.st_size)
        except OSError:
            continue
    return out


def _run(name: str, argv: list, timeout: int = 900) -> tuple:
    t0 = time.time()
    try:
        r = subprocess.run(argv, cwd=str(ROOT), capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout,
                           env={**os.environ, "PYTHONPATH": str(ROOT)})
        out = (r.stdout or "") + (r.stderr or "")
        rc = r.returncode
    except subprocess.TimeoutExpired:
        return "fail", "超时 %ds" % timeout, ""
    except Exception as e:                                   # noqa: BLE001
        return "skip", "%s: %s" % (type(e).__name__, e), ""
    secs = time.time() - t0
    tail = [ln.strip() for ln in out.strip().splitlines() if ln.strip()][-2:]
    return ("pass" if rc == 0 else "fail"), "%.0fs ｜ %s" % (secs, " / ".join(tail)[-160:]), out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", action="store_true")
    a = ap.parse_args()

    py = sys.executable
    items = [
        ("六协议矩阵（含闭环消费）", [py, str(ROOT / "tools" / "protocol_matrix.py")]),
        ("acp 真拉起 + ACP 一轮对话", [py, str(ROOT / "tools" / "acp_core_check.py")]),
    ]
    if a.plan:
        print("核心体检会跑 %d 项：" % (len(items) + 1))
        for i, (n, argv) in enumerate(items, 1):
            print("  %d. %s  →  %s" % (i, n, " ".join(Path(x).name for x in argv[1:])))
        print("  %d. 桥产物落点（跑完后仓库 `agent_community/bridges/` 不许新增）" % (len(items) + 1))
        print("退出码：0=全过；1=有红；2=有项没跑成（＝部分）。")
        return 0

    before = _snapshot_bridges()
    print("=" * 88)
    print("核心体检 —— %s" % time.strftime("%Y-%m-%d %H:%M:%S"))
    print("=" * 88)
    bad = skipped = 0
    for name, argv in items:
        st, msg, out = _run(name, argv)
        bad += st == "fail"
        skipped += st == "skip"
        print("  [%-4s] %-30s %s" % ({"pass": "OK", "fail": "FAIL", "skip": "跳过"}[st], name, msg))
        if st != "pass":
            for ln in (out or "").strip().splitlines()[-6:]:
                print("            %s" % ln[:150])

    after = _snapshot_bridges()
    new = sorted(set(after) - set(before))
    changed = sorted(k for k in set(after) & set(before) if after[k] != before[k])
    polish_ok = not new and not changed
    print("  [%-4s] %-30s %s" % ("OK" if polish_ok else "FAIL", "桥产物落点（仓库不许新增）",
                                 "仓库 bridges/ 无新增" if polish_ok else
                                 "新增 %d 项 / 改动 %d 项" % (len(new), len(changed))))
    if not polish_ok:
        bad += 1
        for k in (new[:5] + changed[:5]):
            print("            %s" % k)

    print("-" * 88)
    if bad:
        print("结论：**%d 项红** ⇒ 核心不算通过" % bad)
        return 1
    if skipped:
        print("结论：**%d 项没跑成（＝部分）** ⇒ 如实说，不算全绿" % skipped)
        return 2
    print("结论：**核心全绿**（六协议 + 闭环 + acp + 产物落点）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
