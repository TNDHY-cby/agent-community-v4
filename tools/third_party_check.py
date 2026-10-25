#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""第三方代码登记核对（V81 §5）。

扫描仓库里带 `SPDX-License-Identifier` 的文件（那是"从别处搬来"的强信号），
逐个要求在 `THIRD_PARTY_NOTICES.md` 的**登记表**里能对上；对不上就**报错退出 1**。

★2026-10-25 外部审查（EXT-20261025-03）打回三处"假绿"，已修：
  ① `SKIP_DIRS` 原来拿**绝对路径的每一段**去比 ⇒ 仓库只要放在带 `temp`/`data` 的目录下，
     整仓被跳过、永远"0 个文件，全部已登记"（**假绿**）。现在只比**相对仓库根的第一层**，
     且默认不跳 `temp`/`data`（要跳用 `AC_TP_SKIP=temp,data` 显式给）。
  ② **没排除自己** ⇒ 本文件文档串里出现的 `SPDX-License-Identifier` 会把自己算成
     "未登记的第三方代码"（自我判负）。现在跳过自身。
  ③ 登记判定原来是**子串匹配**（NOTICES 里随口提一句路径就算登记）⇒ 靠"文档提到自己"过关。
     现在改成解析 NOTICES 里 `` `路径` `` 形式的登记行，**精确路径相等**才算登记。

用法：
    python tools/third_party_check.py                          # 判据：0=一致 / 1=有未登记
    AC_TP_SKIP=temp,data python tools/third_party_check.py     # 额外跳过这些顶层目录
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent.parent
NOTICE = ROOT / "THIRD_PARTY_NOTICES.md"
#: 只按**相对仓库根的第一层**匹配；`temp`/`data` 不再默认跳过（那正是假绿的来源）
SKIP_TOP = {"__pycache__", ".git", "node_modules", ".venv", "dist", "build"}
for _extra in (os.environ.get("AC_TP_SKIP") or "").split(","):
    if _extra.strip():
        SKIP_TOP.add(_extra.strip())

_CODE_SUFFIX = (".py", ".js", ".ts", ".go", ".rs", ".c", ".cc", ".cpp", ".h", ".hpp",
                ".java", ".cs", ".rb", ".php")
#: 登记表里的路径写法：反引号包起来的代码文件路径
_TABLE_PATH = re.compile(r"`([^`]+\.(?:py|js|ts|go|rs|c|cc|cpp|h|hpp|java|cs|rb|php))`")


def _skip(p: Path) -> bool:
    """只按**相对仓库根的第一层**目录名跳过（修 ①：不再拿绝对路径每一段去匹配）。"""
    try:
        rel = p.relative_to(ROOT)
    except ValueError:
        return True
    return bool(rel.parts) and rel.parts[0] in SKIP_TOP


def scan() -> list:
    """扫出所有带 SPDX 头的代码文件（**排除本工具自身**，修 ②）。"""
    me = Path(__file__).resolve()
    marker = "SPDX" + "-License-Identifier"          # 拼开写，免得扫到自己（双保险）
    hits = []
    for p in ROOT.rglob("*"):
        if not p.is_file() or p.suffix.lower() not in _CODE_SUFFIX:
            continue
        if _skip(p):
            continue
        try:
            if p.resolve() == me:
                continue
        except OSError:
            continue
        try:
            head = "\n".join(p.read_text(encoding="utf-8", errors="replace").splitlines()[:15])
        except OSError:
            continue
        if marker in head:
            hits.append(str(p.relative_to(ROOT)).replace("\\", "/"))
    return sorted(hits)


def registered_paths() -> set:
    """从 NOTICES 的登记行里解析**路径**（修 ③：精确相等，不做子串匹配）。"""
    if not NOTICE.exists():
        return set()
    text = NOTICE.read_text(encoding="utf-8", errors="replace")
    out = set()
    for line in text.splitlines():
        if not line.strip().startswith("|"):
            continue
        for m in _TABLE_PATH.finditer(line):
            out.add(m.group(1).strip().replace("\\", "/"))
    return out


def main() -> int:
    hits = scan()
    if not NOTICE.exists():
        print("[FAIL] 缺少 THIRD_PARTY_NOTICES.md（V81 §5 要求）")
        return 1
    reg = registered_paths()
    missing = [h for h in hits if h not in reg]
    if missing:
        print("[FAIL] 这些文件带第三方许可头，但**没在 THIRD_PARTY_NOTICES.md 的登记表里**：")
        for h in missing:
            print("   ·", h)
        print("  ⇒ 去补一行（| 项目 | 仓库/许可 | 文件 | 改动说明 |），或确认它是本项目自己写的缩进头。")
        print("  （判定口径：登记表里出现 `该文件路径` 且路径一致才算登记；文档里随口提一句不算）")
        return 1
    print("[OK] 第三方代码登记一致（扫描到 %d 个带 SPDX 头文件，均在登记表中）" % len(hits))
    for h in hits:
        print("   ·", h)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
