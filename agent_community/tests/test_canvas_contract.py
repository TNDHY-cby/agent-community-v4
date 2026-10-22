# -*- coding: utf-8 -*-
"""守卫：**前端画布调用的每个端点，后端都真的有**（避免"界面点了 404"）。

做法（真判据，不是查文本）：
1. 从 `frontend/flow_canvas.js` 里抽出所有 `'/api/...'` 字面量（含拼接，只取静态部分）；
2. 展开成"前缀"，逐个检查是否被 `app` 里某个路由的 path 前缀覆盖（`{param}` 段按任意值算）；
3. 断言 `workshop.html` 引入了 `flow_canvas.js`（否则面板根本没加载）。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.append(str(_ROOT))

FE = _ROOT / "agent_community" / "frontend"
JS = FE / "flow_canvas.js"


def _all_route_paths() -> list:
    from agent_community.platform.server import app

    AUTO = {"/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc"}
    out = []

    def walk(routes):
        for r in routes:
            inner = getattr(r, "original_router", None)
            if inner is not None and getattr(r, "path", None) is None:
                walk(inner.routes)
                continue
            p = getattr(r, "path", None)
            if p and p not in AUTO:
                out.append(p)

    walk(app.routes)
    return out


def _normalize(p: str) -> list:
    """把 `/api/workshops/{wid}/workflow` 与前端拼出的 `/api/workshops//workflow` 归一成段列表。"""
    return [s for s in p.split("/") if s and not s.startswith("{")]


def _covered(front_path: str, routes: list) -> bool:
    fseg = _normalize(front_path)
    if not fseg:
        return True
    for rp in routes:
        rseg = [s for s in rp.split("/") if s]
        # 路由段里的 {param} 可与前端任意段匹配
        if len(rseg) < len(fseg):
            continue
        ok = True
        for i, s in enumerate(fseg):
            rs = rseg[i]
            if rs.startswith("{"):
                continue
            if rs != s:
                ok = False
                break
        if ok:
            return True
    return False


def test_canvas_js_is_loaded_by_workshop_page():
    html = (FE / "workshop.html").read_text(encoding="utf-8", errors="replace")
    assert "flow_canvas.js" in html, "workshop.html 没引入 flow_canvas.js，画布面板不会加载"


def test_every_endpoint_canvas_calls_exists():
    js = JS.read_text(encoding="utf-8", errors="replace")
    literals = re.findall(r"'(/api/[^']*)'", js)
    assert literals, "flow_canvas.js 里没找到任何 /api 调用（解析失败？）"
    routes = _all_route_paths()
    missing = []
    for lit in literals:
        # 去掉前端拼接产生的占位：'/api/workshops/' + encodeURIComponent(...) + '/workflow'
        if not _covered(lit, routes):
            missing.append(lit)
    assert not missing, "前端调了后端不存在的端点：%s" % missing
