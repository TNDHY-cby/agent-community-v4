# -*- coding: utf-8 -*-
"""一次性：盘点 v4「三级讨论」相关的端点与前端入口（只读，不改任何文件）。

老板 2026-10-29：「不是要把三个级的讨论区换掉吗？你重新找找」
⇒ 先把"三个级"到底落在哪些代码/端点/界面上一一列出来，再谈换。
"""
import io
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

print("=" * 78)
print("① 运行时的路由自省（真数，不靠文本）")
from agent_community.platform.server import app  # noqa: E402

pairs = set()


def walk(routes):
    for r in routes:
        sub = getattr(r, "original_router", None)
        if sub is not None and getattr(r, "path", None) is None:
            walk(sub.routes)
            continue
        p = getattr(r, "path", None)
        if not p:
            continue
        for m in (getattr(r, "methods", None) or set()):
            if m not in ("HEAD", "OPTIONS"):
                pairs.add((m.upper(), p))


walk(app.routes)
keys = ("discuss", "review", "debate", "vote", "consensus", "division", "round", "ailp")
for m, p in sorted(pairs):
    if any(k in p for k in keys):
        print("   %-6s %s" % (m, p))

print()
print("=" * 78)
print("② 后端文件里「一级/二级/三级讨论」出现在哪（带计数，判断各属于哪一级）")
PAT = re.compile(r"一级讨论|二级讨论|三级讨论")
for f in sorted((ROOT / "agent_community" / "platform").rglob("*.py")):
    txt = f.read_text(encoding="utf-8", errors="replace")
    hits = PAT.findall(txt)
    if hits:
        from collections import Counter

        c = Counter(hits)
        print("   %-46s 一级×%d 二级×%d 三级×%d" % (
            str(f.relative_to(ROOT)), c["一级讨论"], c["二级讨论"], c["三级讨论"]))

print()
print("=" * 78)
print("③ 前端入口（哪些页面还在用 /discuss 自由发言）")
for f in sorted((ROOT / "agent_community" / "frontend").rglob("*.js")) + \
         sorted((ROOT / "agent_community" / "frontend").rglob("*.html")):
    txt = f.read_text(encoding="utf-8", errors="replace")
    n_discuss = txt.count("/discuss")
    n_ailp = txt.count("/ailp")
    n_review = txt.count("/review")
    if n_discuss or n_ailp or n_review:
        print("   %-42s /discuss×%d  /ailp×%d  /review×%d" % (
            str(f.relative_to(ROOT)), n_discuss, n_ailp, n_review))
