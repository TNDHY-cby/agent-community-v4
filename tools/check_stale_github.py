# -*- coding: utf-8 -*-
"""全仓扫描：还有没有"推 GitHub / 从 GitHub 克隆"这类**已经不对的活指令**。

判据口径：出现 `github.com/TNDHY` 的行，只有在**明确说明已停更/仅归档/不用更新**时才算合规；
否则就是**会害人的旧指令**（下一个 agent 会照着去推一个停更的仓库）。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent.parent
EXTS = {".md", ".yml", ".yaml", ".ps1", ".py", ".sh", ".bat", ".cmd", ".txt", ".json"}
SKIP_DIRS = {".git", "__pycache__", "node_modules", "data", "bridges", "temp"}
OK_MARKERS = ("停更", "归档", "历史", "不用更新", "别再从", "已停更", "不要")

hits = []
for p in ROOT.rglob("*"):
    try:
        if not p.is_file() or p.suffix.lower() not in EXTS:
            continue
    except Exception:
        continue
    parts = set(p.relative_to(ROOT).parts)
    if parts & SKIP_DIRS or ".bak" in p.name:
        continue
    if p.name == "check_stale_github.py":      # 别把自己算进去（本文件本来就要提到那个域名）
        continue
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except Exception:
        continue
    if "github.com/TNDHY" not in text:
        continue
    for i, line in enumerate(text.split("\n"), 1):
        if "github.com/TNDHY" not in line:
            continue
        if any(m in line for m in OK_MARKERS):
            continue
        hits.append((str(p.relative_to(ROOT)), i, line.strip()[:110]))

print("扫描完成：可疑旧指令 %d 处" % len(hits))
for rel, ln, line in hits[:20]:
    print("  %s:%d  %s" % (rel, ln, line))
raise SystemExit(0 if not hits else 1)
