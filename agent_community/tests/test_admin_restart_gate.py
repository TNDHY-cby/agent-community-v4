# -*- coding: utf-8 -*-
"""守卫：**受控重启端点** `POST /api/admin/restart`（V58，2026-10-19 拍板）。

⚠️ 这里**只测拒绝路径**：成功路径会 `os._exit()` 结束**当前进程**（设计如此，
   让看门狗按原命令行重生），在 pytest 里测它等于自杀。成功路径由
   `tools/admin_restart_check.py` 在**独立子进程**里真跑（含"自己回来"）。

判据（按真行为）：
  ① 缺 `confirm` ⇒ 400（不静默执行重建）；
  ② **没有看门狗 ⇒ 409**（这条是安全底线：没它重启＝永久宕机）；
  ③ 非本机来源 ⇒ 403；
  ④ 看门狗状态认 `AC_DATA_DIR`（**不许**读仓库里那个——那是别的实例的，会误认）；
  ⑤ 拒绝时**服务仍活着**（同一个 client 还能正常打别的端点）。
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.append(str(_ROOT))


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("AC_DATA_DIR", str(tmp_path / "data"))
    from fastapi.testclient import TestClient
    from agent_community.platform.server import app
    yield TestClient(app, client=("127.0.0.1", 54321))


def test_restart_requires_explicit_confirm(client):
    r = client.post("/api/admin/restart", json={})
    assert r.status_code == 400, r.text[:200]
    assert "confirm" in r.json()["error"]


def test_restart_refused_without_watchdog(client):
    """没有看门狗时必须 409 —— 放行就等于把平台弄成永久宕机。"""
    r = client.post("/api/admin/restart", json={"confirm": True})
    assert r.status_code == 409, r.text[:200]
    body = r.json()
    assert "看门狗" in body["error"] and "watchdog" in body["how_to_fix"]
    # 拒绝之后服务必须还活着（同一个 client 还能正常应答）
    assert client.get("/api/status").status_code == 200


def test_restart_rejects_foreign_origin(client):
    r = client.post("/api/admin/restart", json={"confirm": True},
                    headers={"Origin": "https://evil.example"})
    assert r.status_code == 403, r.text[:200]
    assert "来源不是本机" in r.json()["error"]


def test_watchdog_state_follows_data_dir(tmp_path, monkeypatch):
    """看门狗状态必须认 `AC_DATA_DIR`：读不到仓库里那份（那可能是**别的实例**的）。

    背景：`watchdog.pid` 是单实例锁，看门狗**独占持有**它（Windows 上别人读会 PermissionError），
    所以平台改读 `logs/watchdog.json`（可读、带端口）。这条守住"读哪一份"。
    """
    data = tmp_path / "data"
    (data / "logs").mkdir(parents=True)
    (data / "logs" / "watchdog.json").write_text(json.dumps({
        "pid": 1, "port": "18999", "data_dir": str(data),
    }), encoding="utf-8")
    monkeypatch.setenv("AC_DATA_DIR", str(data))
    from agent_community.platform.routers import system as sys_router
    st = sys_router.watchdog_state()
    assert st.get("port") == "18999", "必须读 AC_DATA_DIR 下那份：%r" % st
    # pid=1 不是真的 watchpg 进程 ⇒ 不许被认成"有看门狗"
    assert sys_router.watchdog_pids(18999) == [], "死 pid 不许算数"
