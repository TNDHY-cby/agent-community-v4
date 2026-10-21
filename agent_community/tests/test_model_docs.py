# -*- coding: utf-8 -*-
"""守卫：**《项目完整模型》只放当前口径、拆成多册、主索引与分册互相对得上**。

老板 2026-10-21 两句要求：
1. 「项目完整建模里面**不应该存在旧数据旧资料**，防止污染建模」；
2. 「上下文过长，**得分文件、做索引**」。

判据（全部真读文件 / 真数路由）：
  ① 主文件 ≤ 200 行、每个分册 ≤ 400 行（超了就该再拆）；
  ② 主文件里列的每个分册**都存在**；`model/*.md` 里的每个分册**都被主文件引用**（不许孤儿）；
  ③ 分册里**不许出现历史污染**（`v4.8.x` 这类旧版本号、`已停更`、`原实现`、`269 KB`、`5498` 等）；
  ④ 主文件里的**关键数字必须与代码实测一致**（端点数 / 路由总数 / 版本号）——防止"文档写 42、代码已经 45"。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.append(str(_ROOT))

DOCS = _ROOT / "design-docs"
MAIN = DOCS / "项目完整模型.md"
MODEL_DIR = DOCS / "model"

MAX_MAIN_LINES = 200
MAX_PART_LINES = 400

#: 历史污染标记（只放"一定是旧数据"的；不要放"版本号"这三个字本身）
STALE = [
    r"v4\.(?:8|9)\.\d+",           # 旧版本号
    r"已停更",
    r"旧版写",
    r"原实现",
    r"269\s*KB",
    r"5498",                        # 旧行数
    r"46 个端点",
    r"白名单 19 条",
]


def _lines(p: Path) -> int:
    return len(p.read_text(encoding="utf-8", errors="replace").splitlines())


def test_main_doc_and_parts_have_size_limits():
    assert MAIN.exists(), "缺 design-docs/项目完整模型.md"
    n = _lines(MAIN)
    assert n <= MAX_MAIN_LINES, "主文件 %d 行，超过上限 %d（该把细节挪进分册）" % (n, MAX_MAIN_LINES)
    parts = sorted(MODEL_DIR.glob("*.md"))
    assert parts, "缺 design-docs/model/ 分册"
    big = [(p.name, _lines(p)) for p in parts if _lines(p) > MAX_PART_LINES]
    assert not big, "这些分册太长（> %d 行），该再拆：%s" % (MAX_PART_LINES, big)


def test_every_part_is_linked_and_every_link_exists():
    main = MAIN.read_text(encoding="utf-8", errors="replace")
    parts = {p.name for p in MODEL_DIR.glob("*.md")}
    linked = set(re.findall(r"\]\(model/([^)]+\.md)\)", main))
    missing = sorted(linked - parts)
    assert not missing, "主文件引用了不存在的分册：%s" % missing
    orphans = sorted(parts - linked)
    assert not orphans, "这些分册没被主文件引用（孤儿，等于藏起来）：%s" % orphans


def test_parts_have_no_history_pollution():
    bad = []
    for p in sorted(MODEL_DIR.glob("*.md")):
        text = p.read_text(encoding="utf-8", errors="replace")
        for i, line in enumerate(text.splitlines(), 1):
            for pat in STALE:
                m = re.search(pat, line)
                if m:
                    bad.append("%s:%d 命中 %r" % (p.name, i, m.group(0)))
                    break
    assert not bad, "分册里有旧数据/历史表述（模型里只放当前口径，历史进 CHANGELOG）：%s" % bad[:8]


def test_main_doc_numbers_match_code():
    """主文件里写的**关键数字**必须与代码实测一致。"""
    from agent_community import __version__
    from agent_community.platform.server import app

    main = MAIN.read_text(encoding="utf-8", errors="replace")

    # 版本号
    m = re.search(r"当前版本：`(\d+\.\d+\.\d+)`", main)
    assert m, "主文件里找不到「当前版本：`x.y.z`」"
    assert m.group(1) == __version__, "主文件版本 %s ≠ __version__ %s" % (m.group(1), __version__)

    # 端点数 / 路由总数：从 app.routes 真数（含 _IncludedRouter 展开）
    # ⚠️ 要**排除 FastAPI 自动挂的 4 条文档路由**（/openapi.json /docs /redoc /docs/oauth2-redirect），
    #    否则"业务路由"会被算多 4 条——第一版守卫就因此判红（写 42、实测 46）。
    AUTO = {"/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc"}
    pairs = set()

    # `(method,path)` **业务路由**：要展开 `_IncludedRouter`（否则看不到 routers/ 里那些），set 去重
    def walk(routes):
        for r in routes:
            orig = getattr(r, "original_router", None)
            if orig is not None and getattr(r, "path", None) is None:
                walk(orig.routes)
                continue
            p = getattr(r, "path", None)
            if not p or p in AUTO:
                continue
            for mm in (getattr(r, "methods", None) or set()):
                if mm not in ("HEAD", "OPTIONS"):
                    pairs.add((mm.upper(), p))

    walk(app.routes)

    # `@app.*` 端点数：**只数直接挂在 app 上的**（path 非空，不含 include_router 那些），
    # 并排除 FastAPI 自动挂的 4 条文档路由。⚠️ 第一版用"展开后去重路径数"，算出 236，两种口径混了。
    app_endpoints = sum(1 for r in app.routes
                        if getattr(r, "path", None) and r.path not in AUTO)

    doc_total = re.search(r"\*\*(\d+) 条\*\*\s*`\(method,path\)`", main)
    assert doc_total, "主文件里找不到「**NNN 条** `(method,path)`」这一句"
    assert int(doc_total.group(1)) == len(pairs), \
        "主文件写 %s 条路由，实测 %d 条（改代码要一起改文档）" % (doc_total.group(1), len(pairs))

    doc_app = re.search(r"\*\*(\d+) 个\*\*\s*`@app\.\*`", main)
    assert doc_app, "主文件里找不到「**NN 个** `@app.*`」这一句"
    assert int(doc_app.group(1)) == app_endpoints, \
        "主文件写 %s 个 @app 端点，实测 %d 个" % (doc_app.group(1), app_endpoints)
