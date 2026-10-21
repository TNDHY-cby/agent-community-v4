# -*- coding: utf-8 -*-
"""全仓扫描：**GitHub 的口径还对不对**（2026-10-21 反转后）。

## 背景（反转）

- 2026-10-16 老板定「GitHub 停更，只推 Gitee」⇒ 当时的判据是"仓库里不许再有推 GitHub 的活指令"；
- **2026-10-21 老板指示「更新 Github」** ⇒ 口径反转：**Gitee 与 GitHub 都推**。
  ⇒ 本判据也跟着反转：现在要拦的是**相反方向的过时说法**——
  还写着「GitHub 已停更 / 仅归档 / 别再从那儿拉 / 不用更新」的行，
  会让下一个 agent 又不去推 GitHub（这正是"过时指令"的新形态）。

判据（真扫全仓文本，按行判定）：
含 `GitHub` 且含"停更 / 仅归档 / 不用更新 / 别再从 / 已停更"这类**旧结论**的行，
若没有同时说明"已恢复 / 都推 / 同步镜像"（历史沿革允许带说明保留），即报红。

用法：`python tools/check_stale_github.py`（退 0 = 口径已对齐；退 1 = 还有过时说法）
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent.parent
EXTS = {".md", ".yml", ".yaml", ".ps1", ".py", ".sh", ".bat", ".cmd", ".txt", ".json"}
SKIP_DIRS = {".git", "__pycache__", "node_modules", "data", "bridges", "temp"}
#: "当年停更"的旧结论词
STALE_MARKERS = ("停更", "仅归档", "不用更新", "别再从", "已停更")
#: 同一行里说明了"已恢复"就不算过时（历史沿革允许保留）
RECOVERED_MARKERS = ("恢复", "都推", "同步镜像", "已更新", "反转")


def main() -> int:
    hits = []
    for p in ROOT.rglob("*"):
        try:
            if not p.is_file() or p.suffix.lower() not in EXTS:
                continue
        except Exception:                                    # noqa: BLE001
            continue
        parts = set(p.relative_to(ROOT).parts)
        if parts & SKIP_DIRS or ".bak" in p.name:
            continue
        if p.name == "check_stale_github.py":                 # 本文件自己就要提到这些词
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except Exception:                                     # noqa: BLE001
            continue
        if "GitHub" not in text:
            continue
        for i, line in enumerate(text.split("\n"), 1):
            if "GitHub" not in line:
                continue
            if not any(m in line for m in STALE_MARKERS):
                continue
            if any(m in line for m in RECOVERED_MARKERS):
                continue
            hits.append((str(p.relative_to(ROOT)), i, line.strip()[:120]))

    print("扫描完成：仍写着「GitHub 停更/归档」类过时说法 %d 处" % len(hits))
    for rel, ln, line in hits[:20]:
        print("  %s:%d  %s" % (rel, ln, line))
    if hits:
        print("→ 口径已反转（2026-10-21 老板指示恢复更新 GitHub）："
              "这些行请改成「一边 Gitee、一边 GitHub，都推」")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
