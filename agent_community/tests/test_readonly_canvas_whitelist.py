# -*- coding: utf-8 -*-
"""守卫：**外部只读档的画布/探针白名单**（前端通道 231 第 3 条）。

判据：
① 这些**只读 GET** 放行：workflow / workflow/run / snapshots / approval / budget / capability / probe-reply；
② 同一路径的**写方法**（POST/PUT/DELETE）仍被拒；
③ 不属于白名单的 GET（如 /api/config、/api/logs/x）仍被拒。
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.append(str(_ROOT))

from agent_community.platform import server as srv  # noqa: E402


class _Req:
    def __init__(self, path, method="GET"):
        self.method = method
        self.url = type("U", (), {"path": path})()


def test_canvas_reads_allowed():
    for p in ("/api/workshops/w-1/workflow", "/api/workshops/w-1/workflow/run",
              "/api/tasks/t-1/snapshots", "/api/tasks/t-1/snapshot",
              "/api/tasks/t-1/approval", "/api/tasks/t-1/budget",
              "/api/harness/h-1/capability", "/api/harness/h-1/probe-reply"):
        assert srv._readonly_allows(_Req(p)) is True, "只读档应当放行 %s" % p


def test_writes_still_denied():
    for p in ("/api/workshops/w-1/workflow", "/api/tasks/t-1/approval",
              "/api/harness/h-1/probe-reply"):
        for m in ("POST", "PUT", "DELETE"):
            assert srv._readonly_allows(_Req(p, m)) is False, "%s %s 不该放行" % (m, p)


def test_non_whitelisted_reads_denied():
    assert srv._readonly_allows(_Req("/api/config")) is False
    assert srv._readonly_allows(_Req("/api/logs/x")) is False
