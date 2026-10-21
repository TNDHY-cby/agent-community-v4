# -*- coding: utf-8 -*-
"""守卫：**能力与证据来源**（V73 §9）——不许把"声明"说成"验证过"。

判据（真跑/真读）：
  ① 三段分开：只有 `bridge_config` 可能 `ok`；`harness_running_bridge` / `agent_can_reply`
     一律 `unverified`（除非有证据——目前没有任何证据来源，故恒为 unverified）；
  ② 只有真实存在 `bridge_test.ok` / `bridge_template_verified[].state=="ok"` 才出 `verified`，
     且 `verified` 的范围原话必须带上"agent 能否回话，本次未验证"；
  ③ 能力标签归一：`shell`/`bash`/`exec_command` 归成一个，`raw` 原样保留；
  ④ 声明与布尔打架时**如实记进 notes**，不许替它圆场；
  ⑤ 端点真跑：`GET /api/harness/{id}/capability` 200 / 404 两种情况都说得清。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.append(str(_ROOT))

from agent_community.platform import harness_capability as hc  # noqa: E402


def _record(**kw) -> dict:
    """按本机真实记录的字段形状造一条（字段名照 `harnesses.json`）。"""
    base = {
        "harness_id": "示例Harness-A",
        "harness_name": "示例Harness-A",
        "harness_type": "desktop-agent",
        "wakeup_method": "clipboard",
        "can_wake": False,
        "ai": {"model_name": "demo", "provider": "demo",
               "capabilities": ["coding", "file_ops"], "can_code": True, "can_file_ops": True},
        "tools": [],
        "metadata": {},
    }
    base.update(kw)
    return base


# ── ① 三段 ───────────────────────────────────────────────────────────
def test_three_segments_and_only_bridge_can_be_ok():
    out = hc.build_provenance(_record(metadata={
        "bridge_test": {"test_id": "t1", "ok": True, "echo": "pending-poll-bridge alive"},
        "bridge_template_verified": [{"verified_at": 1, "evidence": {
            "state": "ok", "bridge_kind": "pending_poll",
            "verified_scope": "**仅桥配置**（…）——**harness 侧是否运行该桥、agent 能否回话，本次未验证**"}}],
    }))
    seg = out["segments"]
    assert seg["bridge_config"]["state"] == "ok"
    assert seg["harness_running_bridge"]["state"] == "unverified"
    assert seg["agent_can_reply"]["state"] == "unverified"
    assert out["connected"] is False, "没有 agent 回话证据时不许说'已接上'"
    assert "没有证据证明 agent 能回话" in out["verdict_text"]


# ── ② verified 必须带范围原话 ─────────────────────────────────────────
def test_verified_only_with_evidence_and_scope_says_what_it_does_not_cover():
    no_evi = hc.build_provenance(_record())
    assert no_evi["counts"]["verified"] == 0
    assert no_evi["segments"]["bridge_config"]["state"] == "unknown"
    assert "未验证" in no_evi["segments"]["bridge_config"]["why"]

    with_evi = hc.build_provenance(_record(metadata={"bridge_test": {"ok": True, "echo": "alive"}}))
    scope = with_evi["segments"]["bridge_config"]["why"]
    assert "仅桥配置" in scope and "agent 能否回话" in scope, scope
    assert with_evi["counts"]["verified"] == 1


def test_failed_bridge_is_reported_as_failed_not_unknown():
    out = hc.build_provenance(_record(metadata={"bridge_test": {"ok": False, "echo": "boom"}}))
    assert out["segments"]["bridge_config"]["state"] == "failed"


# ── ③ 标签归一 ───────────────────────────────────────────────────────
def test_capability_aliases_collapse_and_keep_raw():
    out = hc.build_provenance(_record(ai={"capabilities": ["shell", "bash", "exec_command", "coding"],
                                          "can_code": True}))
    names = [c["name"] for c in out["capabilities"]]
    assert names.count("shell") == 1, names
    assert "coding" in names
    shell = [c for c in out["capabilities"] if c["name"] == "shell"][0]
    assert shell["display"] == "执行命令"
    assert shell["source"] == "declared"
    assert "shell" in shell["raw"] and isinstance(shell["raw"], list)


# ── ④ 声明与布尔打架要记进 notes ──────────────────────────────────────
def test_contradiction_between_capabilities_and_flags_is_reported():
    out = hc.build_provenance(_record(ai={"capabilities": ["coding", "file_ops"],
                                          "can_code": False, "can_file_ops": True,
                                          "can_browse": True}))
    joined = " | ".join(out["notes"])
    assert "can_code" in joined, joined          # 声明了 coding 但 can_code=false
    assert "can_browse" in joined, joined        # can_browse=true 但列表里没有 web_search


# ── ⑤ probe / tools 来源 ─────────────────────────────────────────────
def test_probe_hints_marked_probed_and_tools_listed_separately():
    out = hc.build_provenance(_record(
        tools=[{"name": "bash", "capability_tag": "coding", "parameters": {}}],
        metadata={"probe_hints": {"process_hint": "demo-app", "port_hint": 1234}}))
    assert {p["source"] for p in out["probed"]} == {"probed"}
    assert len(out["probed"]) == 2
    assert len(out["tools"]) == 1, out["tools"]
    tool = out["tools"][0]
    assert tool["source"] == "declared" and tool["evidence"] == "tools[]"
    assert tool["params_known"] is False, "parameters 为空时要如实说'参数未知'"
    # 工具的 capability_tag 会把对应能力标成"也在工具里出现过"，但**不重复列一条能力**
    coding = [c for c in out["capabilities"] if c["name"] == "coding"]
    assert len(coding) == 1 and coding[0]["also_in_tools"] is True


def test_capabilities_are_deduplicated_by_canonical_name():
    """真实记录里 `shell`/`bash`/`exec_command` 会同时出现 ⇒ 界面不许看到三条"执行命令"。"""
    out = hc.build_provenance(_record(ai={"capabilities": ["shell", "bash", "exec_command", "coding"],
                                          "can_code": True}))
    names = [c["name"] for c in out["capabilities"]]
    assert names.count("shell") == 1, names
    shell = [c for c in out["capabilities"] if c["name"] == "shell"][0]
    assert sorted(shell["raw"]) == ["bash", "exec_command", "shell"], shell["raw"]


# ── ⑥ 端点真跑 ───────────────────────────────────────────────────────
def test_endpoint_returns_404_for_unknown_harness():
    from fastapi.testclient import TestClient
    from agent_community.platform.server import app

    client = TestClient(app, client=("127.0.0.1", 54321))
    r = client.get("/api/harness/definitely-not-exist/capability")
    assert r.status_code == 404
    body = r.json()
    assert "error" in body and "how_to_fix" in body, body


def test_endpoint_shape_for_a_real_session(monkeypatch):
    """真跑端点：把 harness_manager 里的一个会话换成夹具，看响应形状。"""
    from fastapi.testclient import TestClient
    from agent_community.platform.server import app
    from agent_community.platform.harness_adapter import harness_manager
    from agent_community.platform.protocol import HarnessInfo

    info = HarnessInfo(**{"harness_id": "示例Harness-B", "harness_name": "示例Harness-B",
                          "harness_type": "cli-agent", "wakeup_method": "file_poll",
                          "ai": {"model_name": "demo-model", "provider": "demo-provider",
                                 "capabilities": ["coding"], "can_code": True}})

    class _Sess:
        def __init__(self, i):
            self.info = i

    sessions = getattr(harness_manager, "sessions", {})
    monkeypatch.setitem(sessions, "示例Harness-B", _Sess(info))
    client = TestClient(app, client=("127.0.0.1", 54321))
    r = client.get("/api/harness/示例Harness-B/capability")
    assert r.status_code == 200, r.text
    body = r.json()
    for key in ("segments", "capabilities", "counts", "verdict_text", "connected"):
        assert key in body, body
    assert body["segments"]["agent_can_reply"]["state"] == "unverified"
