# -*- coding: utf-8 -*-
"""守卫：**路由不许重复注册**，也不许再长出"永远返回成功"的老桩。

## 起因（2026-10-21，清结构债时实测）

`server.py` 里曾有 **6 组重复注册**（同一 method+path 挂了两次）：
FastAPI 只认**第一个**，后面那些永远不可达——既是死代码，也让"改哪一份生效"变成猜谜。
实测 `list_tasks` 这个名字被用了两次，靠"函数名比"根本查不出来（按**出现次数**才查得准）。

另外还有 3 个**老桩**：`/api/bridge/pending` 返回 `{"pending": []}`、
`/api/bridge/{id}/response` 与 `/cancel` 无条件 `{"success": True}` —— 全仓 0 处调用，
却会让人以为"桥接流程可用"。这类"看起来成功"比死代码更危险，已删；本守卫防它回来。

判据都是**真读 `app.routes`**（不是扫源码文本）。
"""
from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.append(str(_ROOT))

#: 已删除的老桩：谁要恢复它们，必须先有真实实现（否则就是"假成功"）
REMOVED_LYING_STUBS = (
    ("GET", "/api/bridge/pending"),
    ("POST", "/api/bridge/{bridge_id}/response"),
    ("POST", "/api/bridge/{bridge_id}/cancel"),
)


def _route_pairs(app) -> list:
    """把 app.routes（含 include_router 展开）摊平成 [(method, path)]。

    ⚠️ 本机 starlette 把 include_router 的东西放成 `_IncludedRouter`（`.path is None`），
    真实路由在 `.original_router.routes` —— 与 `test_sessions_v15._mounted_paths()` 同一写法。
    """
    pairs = []

    def walk(routes):
        for r in routes:
            orig = getattr(r, "original_router", None)
            if orig is not None and getattr(r, "path", None) is None:
                walk(orig.routes)
                continue
            path = getattr(r, "path", None)
            methods = getattr(r, "methods", None) or set()
            if not path:
                continue
            for m in methods:
                if m in ("HEAD", "OPTIONS"):
                    continue
                pairs.append((m.upper(), path))

    walk(app.routes)
    return pairs


def test_no_duplicate_routes():
    from agent_community.platform.server import app

    pairs = _route_pairs(app)
    dup = {k: n for k, n in Counter(pairs).items() if n > 1}
    assert not dup, "同一 method+path 被注册了多次（FastAPI 只认第一个，其余是死代码）：%s" % dup


def test_removed_lying_stubs_stay_removed():
    from agent_community.platform.server import app

    pairs = set(_route_pairs(app))
    back = [p for p in REMOVED_LYING_STUBS if p in pairs]
    assert not back, ("这些老桩又回来了，而且当年它们只会无脑返回成功（假成功）：%s"
                      "——要恢复请先给真实实现与判据" % back)


def test_removed_stubs_now_404():
    """真打一次：删掉的桩必须真的 404（不是"删了代码但路由还在"）。"""
    from fastapi.testclient import TestClient
    from agent_community.platform.server import app

    client = TestClient(app, client=("127.0.0.1", 54321))
    for method, path in REMOVED_LYING_STUBS:
        url = path.replace("{bridge_id}", "demo")
        r = client.request(method, url)
        assert r.status_code in (404, 405), "%s %s 应该 404（已删），实际 %s" % (method, path, r.status_code)
