#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""数接口面：业务路由总数、`@app.*` 端点、按域分布、`/mcp` 族、routers 文件数。

## 为什么要这个工具

改端点之后，文档里那三个数字（主文件「N 条路由」、`model/05` 的总数与分域、`@app.*` 个数）
都得跟着改；守卫 `test_model_docs` 只盯主文件那一个，分册经常漂移
（2026-11-04 实测：主文件写 384、分册写 369，差 15 条没人发现）。
⇒ 一条命令数出来，**照它改**，别再手抄。

口径与守卫**同一处**（`agent_community/tests/test_model_docs.py`）：
遍历 `app.routes` 并展开 `_IncludedRouter.original_router.routes`，
去掉 `HEAD/OPTIONS` 与 FastAPI 自动挂的 4 条文档路由，再按 `(method,path)` 去重。

用法：
    python tools/count_routes.py                 # 只打印
    python tools/count_routes.py --check 385     # 与给定数字比对（不等则 exit 1）
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

AUTO = {"/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc"}
DOMAINS = (
    ("/api/workshop/{ws_id}/…", "/api/workshop/"),
    ("/api/harness/{harness_id}/…", "/api/harness/"),
    ("/api/docking/…", "/api/docking/"),
    ("/api/tasks/…", "/api/tasks/"),
    ("/api/workshops/…", "/api/workshops/"),
    ("/api/room/…", "/api/room/"),
    ("/api/mind/…", "/api/mind/"),
    ("/api/mcp/…", "/api/mcp/"),
)


def count() -> dict:
    from agent_community.platform.server import app

    pairs: set = set()

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
    app_endpoints = sum(1 for r in app.routes if getattr(r, "path", None) and r.path not in AUTO)

    def domain_of(path: str) -> str:
        for label, pref in DOMAINS:
            if path.startswith(pref):
                return label
        return "其余"

    dist = Counter(domain_of(p) for _m, p in pairs)
    return {
        "total": len(pairs),
        "app_endpoints": app_endpoints,
        "by_domain": {label: dist.get(label, 0) for label, _p in DOMAINS},
        "rest": dist.get("其余", 0),
        "mcp_paths": sorted({"%s %s" % (m, p) for m, p in pairs
                             if p in ("/mcp",) or p.startswith("/api/mcp")}),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", type=int, default=None,
                    help="与这个数字比对业务路由总数（不等则 exit 1）")
    a = ap.parse_args()

    info = count()
    print("业务路由 (method,path) 总数：%d" % info["total"])
    print("直接挂在 app 上的 @app.* 端点：%d" % info["app_endpoints"])
    print()
    for label, n in info["by_domain"].items():
        print("  %-30s %s" % (label, n))
    print("  %-30s %s" % ("其余", info["rest"]))
    print()
    print("/mcp 族路由：")
    for x in info["mcp_paths"]:
        print("  " + x)

    if a.check is not None and a.check != info["total"]:
        print("\n[FAIL] 文档写的是 %d 条，实测 %d 条 —— 改文档（主文件 + model/05 分域数字）"
              % (a.check, info["total"]))
        return 1
    if a.check is not None:
        print("\n[OK] 与文档写的 %d 条一致" % a.check)
    return 0


if __name__ == "__main__":
    sys.exit(main())
