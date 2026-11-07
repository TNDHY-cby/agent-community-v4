# -*- coding: utf-8 -*-
"""前端 JS 语法检查：外链 `.js` 直接 `node --check`；`.html` 抽出每个内联 `<script>` 块逐个查。

为什么需要它（本项目踩过两次）：
  · 往 JS 字符串里贴多行 HTML，会让**整份 `<script>` 挂掉**（页面看起来"按钮没反应"，控制台才报错）；
  · 只查外链 js 不够 —— 大厅/工作间/对接中心的大量逻辑还在 html 内联块里。

用法：
  python tools/check_inline_js.py                       # 检查 agent_community/frontend 下全部 .html/.js
  python tools/check_inline_js.py path/a.html path/b.js  # 只查指定文件
  python tools/check_inline_js.py --check                # 有错就 exit 1（给判据/CI 用）

node 探测顺序：`AC_NODE` → PATH 里的 `node` → DSH runtime 下的 node（glob，不写死版本目录名）。
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
FRONT = REPO / "agent_community" / "frontend"


def find_node() -> str | None:
    env = os.environ.get("AC_NODE")
    if env and Path(env).exists():
        return env
    on_path = shutil.which("node")
    if on_path:
        return on_path
    # DSH 自带的 node（目录名可能变，用 glob 找，不写死版本）
    for base in (Path.home() / ".dsh" / "dsh-runtimes",):
        if base.is_dir():
            hits = sorted(base.glob("*/dependencies/node/bin/node.exe")) + sorted(base.glob("*/dependencies/node/bin/node"))
            if hits:
                return str(hits[0])
    return None


SCRIPT_RE = re.compile(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", re.S)


def check_js_file(node: str, path: Path) -> tuple[bool, str]:
    r = subprocess.run([node, "--check", str(path)], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if r.returncode == 0:
        return True, ""
    return False, (r.stderr or r.stdout).strip()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="前端 JS 语法检查（外链 + 内联）")
    ap.add_argument("files", nargs="*", help="要查的 .html/.js（默认：frontend 下全部）")
    ap.add_argument("--check", action="store_true", help="有错 exit 1")
    args = ap.parse_args(argv)

    node = find_node()
    if not node:
        print("[FAIL] 找不到 node：装 Node.js，或设 AC_NODE 指向 node 可执行", file=sys.stderr)
        return 2

    if args.files:
        targets = [Path(f).resolve() for f in args.files]
    else:
        targets = sorted(list(FRONT.glob("*.html")) + list(FRONT.glob("*.js")))

    bad = []
    checked_blocks = 0
    tmpdir = Path(tempfile.mkdtemp(prefix="inline_js_"))
    for p in targets:
        if not p.is_file():
            print(f"  SKIP {p}（不存在）")
            continue
        if p.suffix.lower() == ".js":
            ok, err = check_js_file(node, p)
            if ok:
                print(f"  OK   {p.name}")
            else:
                print(f"  FAIL {p.name}\n{err}")
                bad.append(p.name)
            continue
        src = p.read_text(encoding="utf-8", errors="replace")
        blocks = SCRIPT_RE.findall(src)
        for i, b in enumerate(blocks):
            checked_blocks += 1
            f = tmpdir / f"{p.stem}_{i}.js"
            f.write_text(b, encoding="utf-8")
            ok, err = check_js_file(node, f)
            if ok:
                print(f"  OK   {p.name} 内联块#{i}（{len(b)} 字符）")
            else:
                print(f"  FAIL {p.name} 内联块#{i}\n{err}")
                bad.append(f"{p.name}#{i}")

    print(f"\n检查 {len(targets)} 个文件、{checked_blocks} 个内联块；失败 {len(bad)}")
    if bad:
        print("失败项：" + "、".join(bad))
        return 1 if args.check else 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
