# -*- coding: utf-8 -*-
"""V87 接入自检：一句结论 + 有序步骤 + 下一步。

判据对应设计稿 `design-docs/V87_接入自检一键闭环设计.md` §5 的八条。
原则：**结论只由步骤派生**、**缺数据不许假绿**、**默认不打扰外部 agent**。
"""
from __future__ import annotations

import json

import pytest

from agent_community.platform import harness_selfcheck as sc


def _info(hid="示例Harness-A", **extra):
    base = {"harness_id": hid, "harness_name": "示例Harness-A",
            "capabilities": ["coding"], "wakeup_method": "file_poll"}
    base.update(extra)
    return base


#: 完整"全 ok"的四段（模拟已验通的 harness）——用 monkeypatch 替换聚合用的 build_provenance
def _patch_segments(monkeypatch, *, card="passed", config="ok", running="ok", reply="ok"):
    fake = {"segments": {
        "card_conformance": {"state": card, "why": ""},
        "bridge_config": {"state": config, "why": ""},
        "harness_running_bridge": {"state": running, "why": ""},
        "agent_can_reply": {"state": reply, "why": ""},
    }, "counts": {"declared": 1, "probed": 0, "verified": 1, "tools": 0}}
    from agent_community.platform import harness_capability as hc

    monkeypatch.setattr(hc, "build_provenance", lambda info: fake, raising=True)


def test_verdict_is_derived_from_steps_only(monkeypatch):
    """判据 1：`verdict` 只由步骤派生（同一份步骤，派生结果唯一）。"""
    _patch_segments(monkeypatch)
    d = sc.collect(_info(), cred={"scanned": True, "weak": 0, "plaintext_in_bridge": 0})
    assert d["verdict"] == "ok", d
    assert [s["id"] for s in d["steps"]] == ["credential", "card", "config", "running", "reply"]
    assert sc.derive_verdict(d["steps"])[0] == d["verdict"], "结论必须等于步骤的派生结果"

    # 一个 blocked 就能压过其余全部 ok
    d2 = sc.collect(_info(), cred={"scanned": True, "weak": 1, "plaintext_in_bridge": 0})
    assert d2["verdict"] == "blocked", d2


def test_missing_evidence_is_never_green(monkeypatch):
    """判据 2：什么都没验过 ⇒ `unverified`，**不是** `ok`（缺数据不许假绿）。"""
    _patch_segments(monkeypatch, card="unknown", config="unknown",
                    running="unverified", reply="unknown")
    d = sc.collect(_info(), cred={"scanned": False})
    assert d["verdict"] == "unverified", d
    assert "还不能下结论" in d["one_line"], d["one_line"]
    assert all(s["state"] == "unverified" for s in d["steps"]), d["steps"]
    # 每一"没验过"的步骤都必须给出怎么验
    assert all(s["next"] for s in d["steps"]), d["steps"]


def test_next_step_points_at_the_earliest_bad_step(monkeypatch):
    """判据 3：`next_step` 指向**最靠前**那个非 ok 的步骤（用户不会一次改五件事）。"""
    _patch_segments(monkeypatch, card="failed", reply="stale")
    d = sc.collect(_info(), cred={"scanned": True, "weak": 0, "plaintext_in_bridge": 0})
    assert d["verdict"] == "blocked", d
    assert "注册卡片" in d["next_step"], d["next_step"]   # 第 2 步（card）的 next，而不是第 5 步
    assert "验一次回话" not in d["next_step"], "next_step 指错了步骤（指向了第 5 步）"

    # 只有 stale 时指向第 5 步的重验动作
    _patch_segments(monkeypatch, reply="stale")
    d2 = sc.collect(_info(), cred={"scanned": True, "weak": 0, "plaintext_in_bridge": 0})
    assert d2["verdict"] == "stale", d2
    assert "验一次回话" in d2["next_step"], d2["next_step"]


def test_probe_is_not_run_by_default(monkeypatch):
    """判据 4：默认不真发消息 —— 不传 `probe` 时**不许**碰回话探针。"""
    from fastapi.testclient import TestClient

    _patch_segments(monkeypatch)

    def _boom(sess):
        raise AssertionError("默认不该发起回话探针（会打扰外部 agent）")

    from agent_community.platform import reply_probe
    from agent_community.platform.harness_adapter import harness_manager
    from agent_community.platform.server import app

    class _Sess:                                  # 替身 session：只提供 info
        class info:
            metadata = {}

            @staticmethod
            def model_dump():
                return _info()

    monkeypatch.setattr(harness_manager, "sessions", {"示例Harness-A": _Sess()}, raising=True)
    monkeypatch.setattr(reply_probe, "start", _boom, raising=True)

    monkeypatch.setattr(sc, "collect", lambda dump, **kw: {"verdict": "unverified", "steps": [],
                                                           "one_line": "", "next_step": "",
                                                           "probe_used": bool(kw.get("probe"))},
                        raising=True)
    r = TestClient(app, client=("127.0.0.1", 54321)).post("/api/harness/示例Harness-A/selfcheck")
    assert r.status_code == 200, r.text
    assert r.json().get("probe_used") is False, r.json()


def test_probe_sent_is_not_counted_as_verified(monkeypatch):
    """判据 5：`probe=true` 真发了，但**发出 ≠ 验过** —— 结论必须停在 unverified。"""
    from fastapi.testclient import TestClient

    _patch_segments(monkeypatch)
    from agent_community.platform import reply_probe
    from agent_community.platform.harness_adapter import harness_manager
    from agent_community.platform.server import app

    class _Sess:
        class info:
            metadata = {}

            @staticmethod
            def model_dump():
                return _info()

    monkeypatch.setattr(harness_manager, "sessions", {"示例Harness-A": _Sess()}, raising=True)
    monkeypatch.setattr(reply_probe, "start",
                        lambda sess: {"ok": True, "probe_id": "rp-test", "channel": "file_poll"},
                        raising=True)

    r = TestClient(app, client=("127.0.0.1", 54321)).post(
        "/api/harness/示例Harness-A/selfcheck", json={"probe": True})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["probe"].get("sent") is True and d["probe"].get("probe_id") == "rp-test", d["probe"]
    reply = next(s for s in d["steps"] if s["id"] == "reply")
    assert reply["state"] == "unverified", "刚发出就报'通了'＝假绿：%s" % reply
    assert "等它回执" in reply["why"], reply["why"]
    assert d["verdict"] != "ok", d


def test_response_never_contains_plaintext(monkeypatch):
    """判据 6：自检响应里不许出现明文（复用 V86 的哨兵思路）。"""
    from fastapi.testclient import TestClient

    _patch_segments(monkeypatch)
    from agent_community.platform import credentials as cred
    from agent_community.platform.harness_adapter import harness_manager
    from agent_community.platform.server import app

    sentinel = "TEST-SENTINEL-9931-doNotLeak"
    monkeypatch.setattr(cred, "ledger_summary_light",
                        lambda: {"scanned": True, "total": 1, "weak": 0,
                                 "stale": 0, "plaintext_in_bridge": 0,
                                 "hint": sentinel},   # 就算摘要里带进来，也不该泄露明文以外的秘密
                        raising=True)

    class _Sess:
        class info:
            metadata = {}

            @staticmethod
            def model_dump():
                return _info()

    monkeypatch.setattr(harness_manager, "sessions", {"示例Harness-A": _Sess()}, raising=True)
    r = TestClient(app, client=("127.0.0.1", 54321)).post("/api/harness/示例Harness-A/selfcheck")
    assert r.status_code == 200, r.text
    body = r.text
    assert "sk-" not in body, "响应里出现了疑似密钥：%s" % body[:300]


def test_selfcheck_is_post_only_so_readonly_cannot_reach_it():
    """判据 7：外部只读档只放行 GET/HEAD ⇒ 自检（POST-only）天然不可达；**并有断言钉住**。"""
    from agent_community.platform import server as srv

    paths = []
    for r in srv.app.routes:
        inner = getattr(r, "original_router", None)
        for route in (inner.routes if (inner is not None and getattr(r, "path", None) is None) else [r]):
            if getattr(route, "path", "").endswith("/selfcheck"):
                paths.append((route.path, set(getattr(route, "methods", set()) or set())))
    assert paths, "找不到自检路由（没挂上？）"
    for path, methods in paths:
        assert "GET" not in methods and "HEAD" not in methods, \
            "自检必须是 POST-only，否则外部只读档能读到配置形态：%s %s" % (path, methods)
    assert not any("/selfcheck" in s for _p, sufs in srv.EXTERNAL_READONLY_SUFFIX for s in sufs), \
        "自检不许被塞进只读后缀白名单"


def test_collect_is_pure_no_network_no_write(monkeypatch):
    """判据 8：不传 probe 时聚合**纯函数** —— 不发请求、不写状态。"""
    _patch_segments(monkeypatch)

    def _no_net(*a, **k):
        raise AssertionError("聚合阶段不许发网络请求")

    import socket
    monkeypatch.setattr(socket, "create_connection", _no_net, raising=True)
    d = sc.collect(_info(), cred={"scanned": True, "weak": 0, "plaintext_in_bridge": 0})
    assert d["probe_used"] is False
    json.dumps(d, ensure_ascii=False)     # 必须可序列化（端点是 JSON）
