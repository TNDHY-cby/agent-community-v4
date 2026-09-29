"""platform/routers 四个拆分模块冒烟测试。

验证 V-9 单体拆分产物：
- config.py / harness.py / mirror.py / workshops.py 均可正常 import（不触发 server 副作用）
- 各 router 路由表非空
- 关键端点路径与拆分前一致（不依赖真实 data 数据文件）
"""
import pytest

from agent_community.platform.routers import config as cfg_mod
from agent_community.platform.routers import harness as harness_mod
from agent_community.platform.routers import mirror as mirror_mod
from agent_community.platform.routers import workshops as ws_mod


def route_paths(mod):
    return {rt.path for rt in mod.router.routes}


# ── config.py ────────────────────────────────────────────────────
def test_config_module_importable():
    assert cfg_mod.router is not None


def test_config_routes_nonempty():
    assert len(cfg_mod.router.routes) >= 5


def test_config_key_endpoints():
    paths = route_paths(cfg_mod)
    for expected in [
        "/api/ai/providers",
        "/api/config/status",
        "/api/config",
        "/api/ai/pending",
        "/api/ai/reply",
    ]:
        assert expected in paths, f"config 缺少端点 {expected}"


# ── harness.py ───────────────────────────────────────────────────
def test_harness_module_importable():
    assert harness_mod.router is not None


def test_harness_routes_nonempty():
    assert len(harness_mod.router.routes) >= 20


def test_harness_key_endpoints():
    paths = route_paths(harness_mod)
    for expected in [
        "/api/harness/pre-register",
        "/api/harness/register",
        "/api/harness/launch",
        "/api/harness/heartbeat",
        "/api/harness/message",
        "/api/harness/list",
        "/api/harness/pending-tasks",
        "/api/harness/bridge-test",
        "/api/harness/{harness_id}/activate",
        "/api/harness/{harness_id}",
    ]:
        assert expected in paths, f"harness 缺少端点 {expected}"


# ── mirror.py ────────────────────────────────────────────────────
def test_mirror_module_importable():
    assert mirror_mod.router is not None


def test_mirror_routes_nonempty():
    assert len(mirror_mod.router.routes) >= 1


def test_mirror_key_endpoints():
    paths = route_paths(mirror_mod)
    assert "/api/harness/{harness_id}/mirror" in paths


# ── workshops.py ─────────────────────────────────────────────────
def test_workshops_module_importable():
    assert ws_mod.router is not None


def test_workshops_routes_nonempty():
    assert len(ws_mod.router.routes) >= 30


def test_workshops_key_endpoints():
    paths = route_paths(ws_mod)
    for expected in [
        "/api/workshops",
        "/api/workshop",
        "/api/workshop/{ws_id}/discuss",
        "/api/workshop/{ws_id}/start",
        "/api/workshop/{ws_id}/review",
        "/api/workshop/{ws_id}/tree",
    ]:
        assert expected in paths, f"workshops 缺少端点 {expected}"
