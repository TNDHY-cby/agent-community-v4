# -*- coding: utf-8 -*-
"""守卫：**视觉脚本注册模板**那套后端契约（老板 2026-10-24 口述规格）。

| # | 判据 |
|---|---|
| ① | 框选能存四块（输入框 / 发送按钮 / **会话列表** / **新建会话**）+ 固定话术 + 上次测试结果，且能读回 |
| ② | `visual-probe` 真的**点了框的正中**、并按"前后画面变没变"给结论（读不回文字时的唯一硬信号） |
| ③ | 送完 / 测完要能把**平台自己**换回最前（老板规格 ③②），且换不回来时如实说 |
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
    monkeypatch.setenv("AC_DATA_DIR", str(tmp_path))
    from fastapi.testclient import TestClient

    from agent_community.platform.server import app

    return TestClient(app, client=("127.0.0.1", 54321))


def test_regions_store_four_boxes_and_activation_message(client):
    body = {
        "sessions": {"x": 0.02, "y": 0.1, "w": 0.2, "h": 0.7},
        "new_session": {"x": 0.03, "y": 0.05, "w": 0.1, "h": 0.04},
        "input": {"x": 0.15, "y": 0.85, "w": 0.4, "h": 0.05},
        "send": {"x": 0.56, "y": 0.85, "w": 0.03, "h": 0.03},
        "frame": {"w": 1600, "h": 900},
        "activation_message": "【生产合作社】有新任务，请回大厅查看邮箱。",
        "tests": {"new_session": {"changed": True}, "send": {"paste_seen": True, "press_ok": True}},
    }
    r = client.post("/api/docking/harness/h1/regions", json=body)
    assert r.status_code == 200, r.text
    rec = client.get("/api/docking/harness/h1/regions").json()["regions"]
    assert rec["sessions"]["w"] == 0.2 and rec["new_session"]["w"] == 0.1, rec
    assert rec["activation_message"].startswith("【生产合作社】"), rec
    assert rec["tests"]["send"]["press_ok"] is True, rec


def test_only_new_session_box_is_enough(client):
    r = client.post("/api/docking/harness/h2/regions",
                    json={"new_session": {"x": 0.03, "y": 0.05, "w": 0.1, "h": 0.04},
                          "frame": {"w": 1600, "h": 900}})
    assert r.status_code == 200, r.text
    rec = client.get("/api/docking/harness/h2/regions").json()["regions"]
    assert rec["input"] is None and rec["new_session"]["h"] == 0.04, rec


def test_visual_probe_clicks_center_and_reports_band_change(client, monkeypatch):
    from agent_community.platform import delivery, input_sim, screen_vision

    monkeypatch.setattr(delivery, "resolve_target",
                        lambda hid, **k: {"ok": True, "hwnd": 4321, "title": "示例", "name": "示例"},
                        raising=True)
    clicked = {}

    def fake_click(hwnd, rx, ry, **kw):
        clicked.update(hwnd=hwnd, rx=rx, ry=ry)
        return {"ok": True}

    monkeypatch.setattr(input_sim, "click_in_window", fake_click, raising=True)
    monkeypatch.setattr(input_sim, "ensure_foreground", lambda h, **k: {"ok": True}, raising=True)
    monkeypatch.setattr(input_sim, "foreground_window", lambda: 4321, raising=True)
    monkeypatch.setattr(screen_vision, "grab_gray", lambda h, **k: {"ok": True, "w": 10, "h": 10,
                                                                   "rows": [bytearray(10) for _ in range(10)]},
                        raising=True)
    monkeypatch.setattr(screen_vision, "band_diff",
                        lambda a, b, y, **k: {"ok": True, "ratio": 0.02, "changed": True, "why": ""},
                        raising=True)

    box = {"x": 0.2, "y": 0.4, "w": 0.4, "h": 0.2}
    r = client.post("/api/docking/harness/h3/visual-probe", json={"box": box})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["clicked"] is True and out["changed"] is True and out["ratio"] == 0.02, out
    # 点的是正中：0.4 + 0.2*0.5 = 0.5
    assert abs(clicked["rx"] - 0.4) < 1e-6 and abs(clicked["ry"] - 0.5) < 1e-6, clicked


def test_visual_probe_refuses_without_target(client):
    r = client.post("/api/docking/harness/h4/visual-probe",
                    json={"box": {"x": 0.1, "y": 0.1, "w": 0.1, "h": 0.1}})
    assert r.status_code == 409, r.text
    assert r.json()["clicked"] is False


def test_deliver_restores_focus_to_where_it_was(client, monkeypatch):
    """送完把前台还给调用前那个窗口（老板规格：最后把平台拉回最前）。"""
    from agent_community.platform import delivery, input_sim

    monkeypatch.setattr(delivery, "deliver_to_harness",
                        lambda *a, **k: {"ok": True, "channel": "clipboard", "state": "sent_unverified",
                                         "resolved": {"hwnd": 999}}, raising=True)
    monkeypatch.setattr(input_sim, "foreground_window", lambda: 555, raising=True)
    back = {}
    monkeypatch.setattr(input_sim, "set_foreground",
                        lambda h, **k: (back.update(hwnd=h), {"ok": True})[1], raising=True)
    r = client.post("/api/docking/harness/h5/deliver", json={"text": "你好", "enter": True})
    assert r.status_code == 200, r.text
    assert r.json()["focus_restored"] is True, r.json()
    assert back["hwnd"] == 555, back

    # 传 false 就不动前台（有人要盯着目标看）
    back.clear()
    r2 = client.post("/api/docking/harness/h5/deliver",
                     json={"text": "你好", "restore_focus": False})
    assert r2.status_code == 200 and back == {}, r2.json()
