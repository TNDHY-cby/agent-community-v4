# -*- coding: utf-8 -*-
"""守卫：**表层交互**（模拟鼠标键盘 + 教位置）的契约与存储。

老板 2026-10-23 定的方向：「模拟鼠标键盘，从表层交互」。
真机判据在 `tools/surface_input_check.py`（Windows 上真点真打字，读回核对）；
这里钉住**不依赖窗口的东西**，让它在双平台（含 Linux）都能跑：

| # | 判据 |
|---|---|
| ① | 位置存的是**窗口内相对坐标 0~1**；越界**夹紧并如实 warning**（不硬塞） |
| ② | 没教过 ⇒ `get()` 返回 `None`（**绝不猜**一个位置出来） |
| ③ | `clear()` 之后读不回来，且返回值说明"原来有没有" |
| ④ | 非 Windows：`input_sim.send_text/click_screen/send_keys` **如实说不支持**（不假装成功） |
| ⑤ | 接受 "教位置" 的端点：没定位到窗口 / 鼠标不在窗口内 ⇒ **400 且说清为什么** |
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.append(str(_ROOT))


@pytest.fixture()
def client():
    """真 app 的 TestClient（和其它端点守卫同一套写法：本机来源，避免触发外部限制）。"""
    from agent_community.platform import server as srv

    return TestClient(srv.app, client=("127.0.0.1", 54321))


@pytest.fixture()
def anchor_dir(tmp_path, monkeypatch):
    """把锚点文件指到 tmp，绝不碰真实数据目录。"""
    monkeypatch.setenv("AC_DATA_DIR", str(tmp_path))
    from agent_community.platform import input_anchor as ia

    return ia


def test_anchor_is_relative_and_clamped(anchor_dir):
    ia = anchor_dir
    out = ia.set_anchor("示例Harness-A", 0.5, 0.88, title="示例窗口")
    assert out["ok"] and out["rel_x"] == 0.5 and out["rel_y"] == 0.88, out
    assert "warning" not in out

    bad = ia.set_anchor("示例Harness-A", 1.7, -0.3)
    assert bad["ok"] and bad["rel_x"] == 1.0 and bad["rel_y"] == 0.0, bad
    assert "warning" in bad and "0~1" in bad["warning"], bad


def test_missing_anchor_is_none_not_guessed(anchor_dir):
    assert anchor_dir.get("从没教过的-harness") is None


def test_clear_reports_whether_it_existed(anchor_dir):
    ia = anchor_dir
    ia.set_anchor("示例Harness-B", 0.3, 0.4)
    assert ia.clear("示例Harness-B") is True
    assert ia.get("示例Harness-B") is None
    assert ia.clear("示例Harness-B") is False


@pytest.mark.skipif(os.name == "nt", reason="这条只在非 Windows 上验'如实不支持'")
def test_non_windows_simulation_says_unsupported():
    from agent_community.platform import input_sim as ins

    assert ins.supported() is False
    for res in (ins.send_text("x"), ins.send_keys("enter"), ins.click_screen(1, 1)):
        assert res["ok"] is False, res
        assert "Windows" in res["why"], res
    assert "非 Windows" in ins.platform_note()


def test_anchor_endpoint_explains_why_it_refuses(client, anchor_dir):
    """端点：没给 title、或用 from_cursor 但鼠标不在窗内 ⇒ 400 + 说人话。"""
    r = client.post("/api/docking/harness/示例Harness-C/anchor", json={"from_cursor": True})
    assert r.status_code == 400, r.text
    assert "没定位到窗口" in r.json()["error"], r.text

    r2 = client.get("/api/docking/harness/示例Harness-C/anchor")
    assert r2.status_code == 200 and r2.json()["anchor"] is None
    assert "还没教过" in r2.json()["hint"]

    r3 = client.post("/api/docking/harness/示例Harness-C/anchor",
                     json={"rel_x": 0.5, "rel_y": 0.9, "title": "示例窗口"})
    assert r3.status_code == 200 and r3.json()["ok"] is True, r3.text
    r4 = client.get("/api/docking/harness/示例Harness-C/anchor")
    assert r4.json()["anchor"]["rel_y"] == 0.9
    assert client.delete("/api/docking/harness/示例Harness-C/anchor").json()["cleared"] is True


def test_surface_launch_without_anchor_refuses_to_click(client, anchor_dir):
    """没教过位置就走表层交互 ⇒ **拒绝**（不乱点），并给出可执行下一步。"""
    r = client.post("/api/docking/try-activate",
                    json={"title": "任意窗口", "text": "x", "method": "surface"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is False, body
    assert body.get("need_human") is True
    assert "没教过" in body["detail"], body["detail"]
    assert "教一次位置" in body["next_step"], body["next_step"]
