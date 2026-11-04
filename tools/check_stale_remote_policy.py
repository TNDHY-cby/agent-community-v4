# -*- coding: utf-8 -*-
"""全仓扫描：还有没有**过时的远端口径**（"GitHub 已停更 / 只推 Gitee / 别从 GitHub 拉"）。

## 为什么要改成这样（2026-11-04）

老板：「**不更新 gitee 了，换成 Github**」⇒ 主发布远端变成 GitHub。
旧检查器 `check_stale_github.py` 的口径正好**反了**（"出现 GitHub 地址就必须写停更"），
于是 README 里写"主仓库在 GitHub"反而被判红 —— 检查器本身成了过时指令。

## 判据

扫**活指令面**（README / AGENTS / CLAUDE / PORTABILITY / PRODUCTIZATION / `design-docs/model/**` / `tools/**`），
出现下列**过期说法**就红：
  · "GitHub 已停更" / "GitHub 停止更新" / "GitHub 不跟随"
  · "只推 Gitee" / "只推 gitee"

**不算**的地方（历史记录，允许保留）：`HANDOVER.md`、协作书/交接书、`CHANGELOG.md`、
`design-docs/V*.md` 与 `RUNBOOK_*`（设计与沿革稿），以及 `temp/`、`.bak*`。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent.parent

EXTS = {".md", ".yml", ".yaml", ".ps1", ".py", ".sh", ".bat", ".cmd", ".txt", ".json"}
SKIP_DIRS = {".git", "__pycache__", "node_modules", "data", "bridges", "temp"}
#: 历史记录类文件（允许出现旧口径，属于沿革）——按**文件名前缀**跳
HISTORY_PREFIXES = ("HANDOVER", "协作书", "交接书", "CHANGELOG", "V", "RUNBOOK_")

STALE = (
    "GitHub 已停更",
    "GitHub 停止更新",
    "GitHub 已于",
    "GitHub 不跟随",
    "GitHub 那份已停止",
    "只推 Gitee",
    "只推 gitee",
)


def _is_history(rel: Path) -> bool:
    if rel.name.startswith(HISTORY_PREFIXES):
        return True
    # `design-docs/V*.md` 是设计与沿革稿（规则：模型面不写历史，但这些稿子可以）
    if rel.parent.name == "design-docs" and rel.name.startswith("V"):
        return True
    return False


hits = []
scanned = 0
for p in ROOT.rglob("*"):
    try:
        if not p.is_file() or p.suffix.lower() not in EXTS:
            continue
    except OSError:
        continue
    rel = p.relative_to(ROOT)
    if set(rel.parts) & SKIP_DIRS or ".bak" in p.name:
        continue
    if _is_history(rel) or p.name == "check_stale_remote_policy.py":
        continue                                     # 别把自己算进去（本文件本来就要提那些旧说法）
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        continue
    scanned += 1
    for i, line in enumerate(text.split("\n"), 1):
        for bad in STALE:
            if bad in line:
                hits.append((rel.as_posix(), i, bad, line.strip()[:110]))
                break

print("扫描 %d 个活指令文件：过时远端口径 %d 处" % (scanned, len(hits)))
for rel, ln, bad, line in hits[:20]:
    print("  %s:%d  [%s]  %s" % (rel, ln, bad, line))
if hits:
    print("\n口径（2026-11-04 起）：**主仓库＝GitHub**（`origin`，SSH over 443）；Gitee 是备用。")
raise SystemExit(0 if not hits else 1)
