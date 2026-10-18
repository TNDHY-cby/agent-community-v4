# -*- coding: utf-8 -*-
"""**发布前判据一条命令**（2026-10-17 新增）。

## 为什么有它

发布前我一共要跑五类判据，散在五个地方（`PORTABILITY.md` §4.1 / 交接区验收清单 / PRODUCTIZATION / 通道）——
**靠人记就会漏**（2026-10-17 我就漏过"自己的工作树没提交"和"CHANGELOG 写了真实路径"，都是闸门替我兜住的）。
本工具把它们收成**一条命令 + 一张表 + 一个退出码**，且**一次真跑**。

```bash
python tools/pre_release_check.py            # 全跑（约 10–15 分钟）
python tools/pre_release_check.py --fast     # 跳过双平台（只跑快的四项，约 1 分钟）
python tools/pre_release_check.py --plan     # 只打印会跑什么（不跑）
```

**退出码（判据本身也如实说话）**：`0`=全过；`1`=有红；`2`=有项**没跑成**（＝部分，不算通过）。

## 五项各是什么（都按真结果判，不查源码文本）

| # | 项 | 命令 | 判据 |
|---|---|---|---|
| 1 | 对外预检 | `外包交接_前端界面/check_frontend_spec.py` | **0 FAIL**（`待人工` 不参与退出码） |
| 2 | 交接区守卫 | `pytest agent_community/tests/test_frontend_handover.py` | 全过（含 LF / .gitattributes / 工具已发布） |
| 3 | 双平台真跑 | `tools/dual_platform_check.py` | 两侧都绿（**它自己的退出码 2 ＝部分**） |
| 4 | 陌生人首跑 | `tools/first_run_check.py --src <发布副本>` | 五步走完 + 仓库干净 |
| 5 | 发布面三件套 | `check_portability` / `check_productization` / `gen_env_docs --check` | 各 fail=0（在**发布副本**里跑） |

⚠️ 第 5 项与 `publish_acv4.ps1` 的闸门**同源**（那边是最后一道）；这里提前跑一次，是为了**早发现**。
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
OSS = Path(r"D:\外端Agent生产合作社（External Agent Community）-oss")


def py() -> str:
    return sys.executable


def run(name: str, argv: list, cwd: Path | None = None, timeout: int = 1800) -> tuple:
    """跑一项，返回 (状态, 摘要)。状态 ∈ {"pass","fail","skip"}。"""
    t0 = time.time()
    try:
        r = subprocess.run(argv, cwd=str(cwd or ROOT), capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout)
        rc, out = r.returncode, (r.stdout or "") + (r.stderr or "")
    except subprocess.TimeoutExpired:
        return "fail", "超时（%ds）" % timeout
    except Exception as e:  # noqa: BLE001
        return "skip", "%s: %s" % (type(e).__name__, e)
    tail = " | ".join([ln.strip() for ln in out.strip().splitlines()[-3:] if ln.strip()])[:220]
    secs = time.time() - t0
    if rc == 0:
        return "pass", "%.0fs %s" % (secs, tail)
    # 双平台工具：退出码 2 ＝只跑成一侧（如实算"部分/没跑成"，不是"红"）
    if name.startswith("双平台") and rc == 2:
        return "skip", "%.0fs 只跑成一侧（＝部分，不算通过）%s" % (secs, tail)
    return "fail", "%.0fs rc=%s %s" % (secs, rc, tail)


def checks(fast: bool) -> list:
    pre = ROOT / "外包交接_前端界面" / "check_frontend_spec.py"
    items = [
        ("对外预检（FE-26 逐条）", [py(), str(pre)]),
        ("交接区守卫（含 LF/.gitattributes/工具已发布）",
         [py(), "-m", "pytest", "agent_community/tests/test_frontend_handover.py", "-q",
          "-p", "no:cacheprovider"]),
    ]
    if not fast:
        items.append(("双平台真跑（Windows + WSL）", [py(), str(ROOT / "tools" / "dual_platform_check.py")]))
    items.append(("陌生人首跑五步", [py(), str(ROOT / "tools" / "first_run_check.py"),
                                "--src", str(OSS)]))
    items += [
        ("前端本机坐标体检（产品侧零违规）", [py(), str(ROOT / "tools" / "check_frontend_localization.py")]),
        ("设计稿索引未漂移（design-docs/索引.md）", [py(), str(ROOT / "tools" / "gen_design_index.py"), "--check"]),
        ("发布面 check_portability", [py(), "tools/check_portability.py"], OSS),
        ("发布面 check_productization", [py(), "tools/check_productization.py"], OSS),
        ("发布面 env_docs --check", [py(), "tools/gen_env_docs.py", "--check"], OSS),
    ]
    return items


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fast", action="store_true", help="跳过双平台（只跑快的四项）")
    ap.add_argument("--plan", action="store_true", help="只打印会跑什么")
    a = ap.parse_args()

    items = checks(a.fast)
    if a.plan:
        print("发布前判据（%s）会跑 %d 项：" % ("fast" if a.fast else "全量", len(items)))
        for i, it in enumerate(items, 1):
            cwd = "（在发布副本里）" if len(it) > 2 else ""
            shown = " ".join(str(x) for x in it[1][1:]) or "(本脚本)"
            print("  %d. %-42s %s %s" % (i, it[0], shown[:70], cwd))
        print("退出码：0=全过；1=有红；2=有项没跑成（＝部分，不算通过）。")
        return 0

    print("=" * 78)
    print("发布前判据 —— %s" % time.strftime("%Y-%m-%d %H:%M:%S"))
    print("=" * 78)
    rows, bad, skipped = [], 0, 0
    for it in items:
        name, argv = it[0], it[1]
        cwd = it[2] if len(it) > 2 else None
        st, msg = run(name, argv, cwd)
        rows.append((st, name, msg))
        if st == "fail":
            bad += 1
        elif st == "skip":
            skipped += 1
        print("  [%-4s] %-42s %s" % ({"pass": "OK", "fail": "FAIL", "skip": "跳过"}[st], name, msg))
    print("-" * 78)
    if bad:
        print("结论：**有 %d 项红** ⇒ 不许发布（先修红的）" % bad)
        return 1
    if skipped:
        print("结论：**%d 项没跑成（＝部分）** ⇒ 不算通过（如实说，别当全绿）" % skipped)
        return 2
    print("结论：**%d 项全过** ⇒ 可以发布" % len(rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
