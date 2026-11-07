# -*- coding: utf-8 -*-
"""发布同步面**内容级**核对：dev 里该发出去的东西，oss 里是不是真的一样。

为什么需要它：`publish_acv4.ps1` 只保证"同步命令跑过了 + git push 成功了"，它不看内容；
而"发布后公开仓库少了文件/旧了版本"这类事故，靠日志和 sha 都发现不了（sha 只说明推了）。
本工具按同步面逐文件比 sha256，直接给出"哪些文件没同步 / 内容不一致"。

口径（与 `publish_acv4.ps1` 的同步面一致）：
  agent_community/ design-docs/ tools/ tests/ 外包交接_前端界面/ .github/ + 根文档若干
  排除：data/ bridges/ __pycache__/ temp/ .git/ node_modules/ 与 *.bak* *.old_* *.before_* *.log *.pyc

用法：
  python tools/dev_oss_sync_check.py --dev <开发副本> --oss <发布副本>          # 只读报告
  python tools/dev_oss_sync_check.py --dev <开发副本> --oss <发布副本> --check  # 有差异 ⇒ exit 1
  python tools/dev_oss_sync_check.py --dev . --oss ../ac-oss --json            # 机器可读

`--oss` 省略时按约定推断：同盘相邻目录 `<dev　目录名>-oss`（**不写死任何用户名/盘符**）。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOTS = ["agent_community", "design-docs", "tools", "tests", "外包交接_前端界面", ".github"]
ROOT_FILES = [
    "README.md", "AGENTS.md", ".gitignore", "pytest.ini", "requirements.txt",
    "start.py", "PORTABILITY.md", "PRODUCTIZATION.md", "LICENSE", "CONTRIBUTING.md",
    "CHANGELOG.md", "config.example.json",
]
SKIP_DIR = {"data", "bridges", "__pycache__", "temp", ".git", "node_modules"}
SKIP_SUFFIX = (".log", ".pyc")
SKIP_SUBSTR = (".bak", ".old_", ".before_", ".pre_ui", ".wetware_backup")
SKIP_PREFIX = ("temp_",)


def _skipped(rel: Path) -> bool:
    if set(rel.parts[:-1]) & SKIP_DIR:
        return True
    name = rel.name
    if name.endswith(SKIP_SUFFIX) or name.startswith(SKIP_PREFIX):
        return True
    return any(s in name for s in SKIP_SUBSTR)


def walk(base: Path) -> dict[str, Path]:
    out: dict[str, Path] = {}
    for rel in ROOTS:
        d = base / rel
        if not d.is_dir():
            continue
        for p in d.rglob("*"):
            if not p.is_file():
                continue
            r = p.relative_to(base)
            if _skipped(r):
                continue
            # 目录名的 bak 形态（如 agent_community/data.bak_xxx）也要跳过
            if any(s in part for part in r.parts[:-1] for s in SKIP_SUBSTR):
                continue
            out[r.as_posix()] = p
    for f in ROOT_FILES:
        p = base / f
        if p.is_file():
            out[f] = p
    return out


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def compare(dev: Path, oss: Path) -> dict:
    dmap, omap = walk(dev), walk(oss)
    only_dev = sorted(set(dmap) - set(omap))
    only_oss = sorted(set(omap) - set(dmap))
    differ = [k for k in sorted(set(dmap) & set(omap)) if sha256(dmap[k]) != sha256(omap[k])]
    return {
        "dev": str(dev),
        "oss": str(oss),
        "counts": {"dev": len(dmap), "oss": len(omap)},
        "only_dev": only_dev,
        "only_oss": only_oss,
        "differ": differ,
        "out_of_sync": len(only_dev) + len(differ),
    }


def guess_oss(dev: Path) -> Path:
    return dev.parent / (dev.name + "-oss")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="发布同步面内容级核对（不写死盘符/用户名）")
    ap.add_argument("--dev", default=".", help="开发副本根目录（默认当前目录）")
    ap.add_argument("--oss", default=None, help="发布副本根目录（默认同盘相邻 <dev名>-oss）")
    ap.add_argument("--check", action="store_true", help="有差异就 exit 1（给 CI/发布后判据用）")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    ap.add_argument("--max-list", type=int, default=20, help="每类最多列几条（默认 20）")
    args = ap.parse_args(argv)

    dev = Path(args.dev).resolve()
    oss = Path(args.oss).resolve() if args.oss else guess_oss(dev).resolve()
    for p, label in ((dev, "开发副本"), (oss, "发布副本")):
        if not p.is_dir():
            print(f"[FAIL] {label}不存在：{p}", file=sys.stderr)
            return 2

    res = compare(dev, oss)
    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return 0 if res["out_of_sync"] == 0 else 1

    print("=" * 78)
    print("发布同步面内容核对")
    print(f"  开发副本：{res['dev']}")
    print(f"  发布副本：{res['oss']}")
    print(f"  同步面文件数：dev={res['counts']['dev']}  oss={res['counts']['oss']}")
    print(f"  只在 dev（没发出去）：{len(res['only_dev'])}")
    for k in res["only_dev"][: args.max_list]:
        print("    -", k)
    if len(res["only_dev"]) > args.max_list:
        print(f"    …（还有 {len(res['only_dev']) - args.max_list} 个）")
    print(f"  只在 oss（发布副本独有，通常正常）：{len(res['only_oss'])}")
    for k in res["only_oss"][: args.max_list]:
        print("    +", k)
    if len(res["only_oss"]) > args.max_list:
        print(f"    …（还有 {len(res['only_oss']) - args.max_list} 个）")
    print(f"  两边都有但内容不同：{len(res['differ'])}")
    for k in res["differ"][: args.max_list]:
        print("    ~", k)
    if len(res["differ"]) > args.max_list:
        print(f"    …（还有 {len(res['differ']) - args.max_list} 个）")

    bad = res["out_of_sync"]
    if bad == 0:
        print("\n结论：**一致**（dev 该发的内容都在 oss 里，且逐字节相同）")
    else:
        print(f"\n结论：**{bad} 处没同步** —— 若是刚发版，说明这批是刻意攒批（未发版），不是漏发；"
              f"若不是，请查 publish 日志。")
    return 0 if bad == 0 or not args.check else 1


if __name__ == "__main__":
    sys.exit(main())
