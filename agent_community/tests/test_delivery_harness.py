# -*- coding: utf-8 -*-
"""守卫：**通用送话入口**（`deliver_to_harness`）——老板 2026-10-23「禁止私人化…落地到通用框架」。

真机判据仍在 `tools/delivery_check.py`（真窗口/真浏览器）；这里钉**通用性**与**自动定位**：

| # | 判据 | 为什么 |
|---|---|---|
| ① | 只有"会话名"就能定位目标：先查记忆 → 再按名字在调试端口里找页面 → 再按名字找窗口 | 上层只说"送给这个 harness"，不该知道通道/句柄 |
| ② | **没有会话名**时如实说"先去命名会话"，不去瞎找 | 瞎找会把话送到别的 agent 那儿 |
| ③ | 名字对不上任何页面/窗口 ⇒ `ok=False` + 可执行 `why`（不猜、不静默） | 失败要说话 |
| ④ | 成功一次就**记住**目标（`input_anchor`），下次直接命中 | 用户不该每次都要教 |
| ⑤ | 代码里**不许出现具体应用名**（只有通用概念：Chromium/Electron/Win32） | 反私人定制 |

### 关于第 ⑤ 条
本文件里的"应用名"只用于**说明这类目标**（例如"浏览器/Eelectron 类"），不作为任何分支条件。
判据写成"扫描 `platform/delivery.py` 与 `platform/cdp_input.py` 的源码里没有具体产品名"，
扫到就红——这样以后谁想把某个应用写死进来，会当场被拦下。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.append(str(_ROOT))

#: 断言"不许写死"的产品名（只列常见 Electron/浏览器产品；不含通用概念）
@pytest.fixture()
def client():
    """真 app 的 TestClient（本机来源；与其它端点守卫同一套写法）。"""
    from fastapi.testclient import TestClient

    from agent_community.platform import server as srv

    return TestClient(srv.app, client=("127.0.0.1", 54321))


PRODUCT_NAMES = ("trae", "cursor", "vscode", "code.exe", "sublime", "wechat", "dingtalk")


@pytest.fixture()
def fake_harness(monkeypatch):
    """一个假 harness 会话（只有会话名，没有别的）。"""
    from agent_community.platform import harness_adapter

    class _Sess:
        def __init__(self, name):
            self.session_aliases = [name] if name else []
            self.session_ref = {}

    store = {}

    class _Mgr:
        sessions = store

    monkeypatch.setattr(harness_adapter, "harness_manager", _Mgr(), raising=True)
    return store, _Sess


def test_resolve_prefers_remembered_page(monkeypatch, tmp_path, fake_harness):
    store, Sess = fake_harness
    monkeypatch.setenv("AC_DATA_DIR", str(tmp_path))
    store["h1"] = Sess("示例会话-甲")
    from agent_community.platform import input_anchor, delivery

    input_anchor.set_web("h1", ws="ws://remembered/1", idx=3, title="示例会话-甲")
    t = delivery.resolve_target("h1")
    assert t["ok"] is True and t["ws"] == "ws://remembered/1" and t["idx"] == 3, t
    assert "记忆" in t["source"], t["source"]


def test_resolve_by_page_title_when_not_remembered(monkeypatch, tmp_path, fake_harness):
    store, Sess = fake_harness
    monkeypatch.setenv("AC_DATA_DIR", str(tmp_path))
    store["h2"] = Sess("示例会话-乙")
    from agent_community.platform import cdp_input, delivery

    monkeypatch.setattr(cdp_input, "probe", lambda ports=None: {
        "ok": True,
        "ports": [{"port": 9222, "targets": [
            {"id": "a", "title": "别的页面", "url": "http://x", "type": "page", "ws": "ws://other"},
            {"id": "b", "title": "示例会话-乙 - 工作台", "url": "http://y", "type": "page", "ws": "ws://mine"},
        ]}],
        "why": ""}, raising=False)
    t = delivery.resolve_target("h2")
    assert t["ok"] is True and t["ws"] == "ws://mine", t
    assert "调试端口" in t["source"], t["source"]


def test_resolve_by_window_title_when_no_debug_page(monkeypatch, tmp_path, fake_harness):
    store, Sess = fake_harness
    monkeypatch.setenv("AC_DATA_DIR", str(tmp_path))
    store["h3"] = Sess("示例会话-丙")
    from agent_community.platform import cdp_input, delivery, visual_launch

    monkeypatch.setattr(cdp_input, "probe", lambda ports=None: {"ok": False, "ports": [], "why": "没端口"},
                        raising=False)
    monkeypatch.setattr(visual_launch, "list_windows",
                        lambda title_like="", timeout=30.0: {"ok": True, "windows": [
                            {"hwnd": 4242, "title": "示例会话-丙 — 编辑器", "pid": 1, "class": "X"}]},
                        raising=False)
    t = delivery.resolve_target("h3")
    assert t["ok"] is True and t["hwnd"] == 4242, t
    assert "窗口" in t["source"], t["source"]


def test_no_session_name_says_so_instead_of_guessing(monkeypatch, tmp_path, fake_harness):
    store, Sess = fake_harness
    monkeypatch.setenv("AC_DATA_DIR", str(tmp_path))
    store["h4"] = Sess("")
    from agent_community.platform import delivery

    t = delivery.resolve_target("h4")
    assert t["ok"] is False
    assert "会话名" in t["why"], t["why"]


def test_unmatched_name_fails_with_actionable_why(monkeypatch, tmp_path, fake_harness):
    store, Sess = fake_harness
    monkeypatch.setenv("AC_DATA_DIR", str(tmp_path))
    store["h5"] = Sess("对不上的名字")
    from agent_community.platform import cdp_input, delivery, visual_launch

    monkeypatch.setattr(cdp_input, "probe", lambda ports=None: {"ok": False, "ports": [], "why": "没端口"},
                        raising=False)
    monkeypatch.setattr(visual_launch, "list_windows",
                        lambda title_like="", timeout=30.0: {"ok": True, "windows": []}, raising=False)
    res = delivery.deliver_to_harness("h5", "hello")
    assert res["ok"] is False and res["attempts"] == []
    assert "没找到" in res["detail"] and res.get("next_step"), res


def test_success_is_remembered_for_next_time(monkeypatch, tmp_path, fake_harness):
    store, Sess = fake_harness
    monkeypatch.setenv("AC_DATA_DIR", str(tmp_path))
    store["h6"] = Sess("示例会话-己")
    from agent_community.platform import delivery, input_anchor

    monkeypatch.setattr(delivery, "resolve_target",
                        lambda hid: {"ok": True, "name": "示例会话-己", "ws": "ws://auto/9", "hwnd": 0,
                                     "title": "示例会话-己", "idx": 0, "source": "按会话名在调试端口里找到页面",
                                     "why": ""}, raising=False)
    monkeypatch.setattr(delivery, "deliver",
                        lambda text, **kw: {"ok": True, "channel": "cdp", "verified": True,
                                            "attempts": [], "detail": "ok", "next_step": ""},
                        raising=False)
    res = delivery.deliver_to_harness("h6", "你好", idx=2)
    assert res["ok"] is True and res["channel"] == "cdp", res
    saved = input_anchor.get_web("h6")
    assert saved and saved["ws"] == "ws://auto/9" and saved["idx"] == 2, saved


def test_no_product_names_in_delivery_code():
    """反私人定制：这两处**不许**出现具体产品名（只允许 Chromium/Electron/Win32 这类通用概念）。"""
    bad = []
    for rel in ("agent_community/platform/delivery.py", "agent_community/platform/cdp_input.py"):
        text = (_ROOT / rel).read_text(encoding="utf-8").lower()
        for name in PRODUCT_NAMES:
            if re.search(r"\b" + re.escape(name) + r"\b", text):
                bad.append((rel, name))
    assert not bad, "这些文件里写死了具体产品名（应改成通用概念）：%s" % bad

def test_manual_bind_wins_without_session_name(monkeypatch, tmp_path, fake_harness):
    """**没有会话名也能送**：只要用户手动绑定过目标（2026-10-23 前端反馈的那条卡点）。

    顺序是：手动绑定 → 记忆 → 按名找页面 → 按名找窗口 → 如实说。绑定排第一，因为它最确定。
    """
    store, Sess = fake_harness
    monkeypatch.setenv("AC_DATA_DIR", str(tmp_path))
    store["hb"] = Sess("")                       # 没会话名
    from agent_community.platform import delivery, input_anchor

    input_anchor.set_bind("hb", hwnd=777, title="示例窗口-绑定")
    t = delivery.resolve_target("hb")
    assert t["ok"] is True and t["hwnd"] == 777, t
    assert "绑定" in t["source"], t["source"]

    # 清掉绑定后又没有会话名 ⇒ 如实说清两条出路（绑定 / 命名），不许含糊
    input_anchor.clear_bind("hb")
    t2 = delivery.resolve_target("hb")
    assert t2["ok"] is False
    assert "绑定" in t2["why"] and "会话名" in t2["why"], t2["why"]


def test_bind_endpoint_roundtrip(client, tmp_path, monkeypatch):
    """端点往返：缺参 400 且说清要什么；绑定/读回/取消都真生效。"""
    monkeypatch.setenv("AC_DATA_DIR", str(tmp_path))
    r1 = client.post("/api/docking/harness/示例Harness-B/bind", json={})
    assert r1.status_code == 400 and "hwnd" in r1.json()["error"], r1.text
    r2 = client.post("/api/docking/harness/示例Harness-B/bind",
                     json={"hwnd": 4321, "title": "示例窗口"})
    assert r2.status_code == 200 and r2.json()["bind"]["hwnd"] == 4321, r2.text
    r3 = client.get("/api/docking/harness/示例Harness-B/bind")
    assert r3.json()["bind"]["hwnd"] == 4321
    r4 = client.delete("/api/docking/harness/示例Harness-B/bind")
    assert r4.json()["cleared"] is True
    assert client.get("/api/docking/harness/示例Harness-B/bind").json()["bind"] is None
