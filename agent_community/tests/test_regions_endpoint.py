# -*- coding: utf-8 -*-
"""守卫：**保存框选这两个坑**（2026-10-24 老板真机踩出来的）。

| # | 判据 |
|---|---|
| ① | `frame` 是最小化窗口的占位值（237×39 这种）⇒ **400 并说清**，不许存进去
      （老板框完存下 237×39，之后按这个基准算出来的位置全错） |
| ② | 带 `hwnd`/`title` 保存框选 ⇒ **顺手把窗口绑给这位员工**（原来要前端再发一次 `…/bind`，
      中途失败就变成"框了但没有目标"——老板真机就卡在这） |
| ③ | 正常保存仍然能读回来（别把上面的校验做成拦路虎） |
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.append(str(_ROOT))


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("AC_DATA_DIR", str(tmp_path))     # 别碰真实数据目录
    from fastapi.testclient import TestClient

    from agent_community.platform.server import app

    return TestClient(app, client=("127.0.0.1", 54321))


def test_placeholder_frame_is_rejected(client):
    r = client.post("/api/docking/harness/h-frame/regions",
                    json={"input": {"x": 0.1, "y": 0.8, "w": 0.5, "h": 0.1},
                          "frame": {"w": 237, "h": 39}})
    assert r.status_code == 400, r.text
    body = r.json()
    assert "占位值" in body["error"] and "237" in body["error"], body
    # 没存进去
    assert client.get("/api/docking/harness/h-frame/regions").json()["regions"] is None


def test_regions_save_can_bind_window_in_one_call(client):
    r = client.post("/api/docking/harness/h-bind/regions",
                    json={"input": {"x": 0.15, "y": 0.85, "w": 0.4, "h": 0.05},
                          "frame": {"w": 1600, "h": 900},
                          "hwnd": 123456, "title": "示例窗口"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body.get("bind", {}).get("ok") is True, body
    assert int(body["bind"]["bind"]["hwnd"]) == 123456, body["bind"]
    # 读回一致
    got = client.get("/api/docking/harness/h-bind/regions").json()["regions"]
    assert got["frame"] == {"w": 1600, "h": 900}, got
    assert client.get("/api/docking/harness/h-bind/bind").json()["bind"]["hwnd"] == 123456
