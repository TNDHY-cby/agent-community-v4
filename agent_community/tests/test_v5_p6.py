# -*- coding: utf-8 -*-
"""守卫：**P6 结构化讨论区（AILP 2.0）+ tooling 实测**（`V117` §7-5、`V116` §9）。

真跑：真 HTTP、真 manifest 对账、真写一次盘。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.append(str(_ROOT))

from agent_community.platform import ailp as A  # noqa: E402
from agent_community.platform import lead_roster as LR  # noqa: E402
from agent_community.platform import tooling_probe as TP  # noqa: E402

REF = "前端组/员工01/任务/T-001.md"


def _env(**kw):
    base = {"ailp_version": "2.0", "msg_id": "msg-1", "msg_type": "PROPOSE",
            "sender": "员工01", "target": "组长", "manifest_ref": REF,
            # ★2026-11-01（`V122` 批 1）：依据现在要**真存在**（A014）。
            #   默认指向 manifest_ref 本身 —— 它一定在 allowed 里，夹具不必额外造数据。
            "payload": {"proposal": "先做接口", "confidence": 0.8, "basis_ids": [REF]}}
    base.update(kw)
    return base


# ── 信封与四类 payload ─────────────────────────────────────────
def test_a002_version_must_match():
    ok, errs, _ = A.validate(_env(ailp_version="1.0"))
    assert not ok and errs[0]["code"] == "A002" and "2.0" in errs[0]["fix"]


def test_a003_free_chat_is_rejected():
    ok, errs, _ = A.validate(_env(msg_type="闲聊", payload={"text": "随便聊聊"}))
    assert not ok and any(e["code"] == "A003" for e in errs)
    assert any("自由聊天不收" in (e.get("fix") or "") or "自由聊天" in (e.get("message") or "")
               for e in errs)


def test_a005_manifest_ref_required():
    env = _env()
    env.pop("manifest_ref")
    ok, errs, _ = A.validate(env)
    assert not ok and any(e["code"] == "A005" for e in errs)


def test_a006_manifest_ref_must_be_in_manifest():
    ok, errs, _ = A.validate(_env(manifest_ref="别的地方/x.md"), allowed=[REF])
    assert not ok and errs[0]["code"] == "A006"
    assert REF in errs[0]["fix"], "要告诉它能用哪些"


def test_all_seven_types_validate():
    """七类都要能过（`V122` 批 1：四类 → 七类，新增 CLARIFY / ACT / RECOMMEND）。"""
    cases = {
        "PROPOSE": {"proposal": "x", "confidence": 0.5, "basis_ids": [REF]},
        "CHALLENGE": {"contradiction": "x", "evidence_ids": [REF], "counter_proposal": "y"},
        # ★REFINE 现在**必须带 reason**（置信度只追加、不原地改）
        "REFINE": {"target_id": "msg-0", "modifications": "m", "new_confidence": 0.9,
                   "reason": "拿到新证据"},
        "CONTEXT_ECHO": {"echo_target": "蚁群", "shared_context": "呼应去中心化"},
        "CLARIFY": {"question": "T-001 的接口要返回什么", "to_ref": "msg-0"},
        "ACT": {"task_ref": "T-001", "owner": "员工01", "due": "2026-11-05"},
        "RECOMMEND": {"target_id": "msg-0", "reason": "它依据更全", "confidence": 0.7},
    }
    for t, payload in cases.items():
        ok, errs, norm = A.validate(_env(msg_type=t, payload=payload), allowed=[REF],
                                    msg_ids={"msg-0"}, task_ids={"T-001", "1"},
                                    members={"员工01", "组长"})
        assert ok, (t, errs)
        assert norm["msg_type"] == t and norm["fingerprint"]


def test_a010_basis_ids_cannot_be_empty():
    ok, errs, _ = A.validate(_env(payload={"proposal": "x", "confidence": 0.5, "basis_ids": []}))
    assert not ok and any(e["code"] == "A010" for e in errs)


def test_a011_confidence_range():
    ok, errs, _ = A.validate(_env(payload={"proposal": "x", "confidence": 1.8,
                                           "basis_ids": ["a"]}))
    assert not ok and any(e["code"] == "A011" for e in errs)


def test_catalog_is_single_source():
    cat = A.types_catalog()
    # ★2026-11-01（`V122` 批 1）：四类 → 七类
    assert cat["ailp_version"] == "2.0" and len(cat["types"]) == 7
    assert [t["msg_type"] for t in cat["types"]] == list(A.TYPES)
    assert any(r.startswith("自由聊天不收") for r in cat["rules"])
    # 规则里承诺的每一条，代码都得真有（`REFINE` 的 target / 依据存在性 / ACT 的任务号）
    joined = " ".join(cat["rules"])
    for must in ("A013", "A014", "A016", "A017", "reason"):
        assert must in joined, "规则表里没写清 %s" % must


# ── tooling 实测 ───────────────────────────────────────────────
def test_probe_writes_and_records(tmp_path, monkeypatch):
    monkeypatch.setenv("AC_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(TP, "_path", lambda: tmp_path / "harness_tooling.json", raising=True)
    res = TP.probe_platform_fs("h_x")
    assert res["ok"] is True and Path(res["dir"]).is_dir()
    TP.record("h_x", res)
    m = TP.measured("h_x")
    assert m["fs_write"] is True and "不等于" in m["scope"], "边界要写清"


def test_measured_overrides_declared(monkeypatch):
    class _Info:
        tools = []
        capabilities = []
        wakeup_method = type("W", (), {"value": "mcp"})()
        harness_name = "示例"
        ai = None
        fit_directions = []

    monkeypatch.setattr(LR, "_harness_infos", lambda: [("h1", _Info())], raising=True)
    monkeypatch.setattr(LR, "_merge_measured",
                        lambda hid, tool: dict(tool, fs_write=True, fs_write_source="measured"),
                        raising=True)
    out = LR.candidates("")
    assert out["count"] == 1, "实测为真 ⇒ 可以当组长（这条路由来是终结候选=0）"


# ── HTTP：讨论区 ───────────────────────────────────────────────
@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("AC_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("AC_MIND_ROOT", str(tmp_path / "minds"))
    monkeypatch.setattr(TP, "_path", lambda: tmp_path / "data" / "harness_tooling.json",
                        raising=True)
    from fastapi.testclient import TestClient

    from agent_community.platform import server as srv

    wp = tmp_path / "ws"
    (wp / "_lead").mkdir(parents=True)
    (wp / "_lead" / "建模.md").write_text("# 建模\n", encoding="utf-8")
    (wp / REF).parent.mkdir(parents=True, exist_ok=True)
    (wp / REF).write_text("# T-001\n", encoding="utf-8")
    ws = srv.Workshop(workshop_id="WS-T", name="示例间", workspace_dir=str(wp),
                      hall_content="方向", status="running")
    srv.workshops["WS-T"] = ws
    c = TestClient(srv.app, client=("127.0.0.1", 54321))
    (tmp_path / "minds" / "A-01").mkdir(parents=True, exist_ok=True)
    c.post("/api/workshop/WS-T/manifest", json={"mind_path": str(tmp_path / "minds" / "A-01"),
                                                "entry_points": ["_lead/建模.md"],
                                                "active_tasks": [REF]})
    return c


def test_http_types_catalog(client):
    body = client.get("/api/ailp/types").json()
    assert body["ok"] and body["ailp_version"] == "2.0"


def test_http_discuss_accepts_structured(client):
    r = client.post("/api/workshop/WS-T/ailp", json=_env())
    assert r.status_code == 200, r.text
    assert r.json()["msg_id"] == "msg-1"
    listed = client.get("/api/workshop/WS-T/ailp").json()
    assert listed["count"] == 1 and listed["counts"]["PROPOSE"] == 1


def test_http_discuss_rejects_free_chat_and_bad_ref(client):
    free = client.post("/api/workshop/WS-T/ailp", json={"text": "大家好，随便聊聊"})
    assert free.status_code == 422 and free.json()["code"] == "AILP_INVALID"
    bad = client.post("/api/workshop/WS-T/ailp", json=_env(manifest_ref="乱写的/x.md"))
    assert bad.status_code == 422
    assert any(e["code"] == "A006" for e in bad.json()["errors"])


def test_http_discuss_needs_manifest(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from agent_community.platform import server as srv

    wp = tmp_path / "ws2"
    wp.mkdir()
    srv.workshops["WS-T2"] = srv.Workshop(workshop_id="WS-T2", name="无清单",
                                          workspace_dir=str(wp), hall_content="x")
    c = TestClient(srv.app, client=("127.0.0.1", 54321))
    r = c.post("/api/workshop/WS-T2/ailp", json=_env())
    assert r.status_code == 409 and r.json()["code"] == "GATE_G0"


def test_http_replay_is_idempotent(client):
    client.post("/api/workshop/WS-T/ailp", json=_env())
    again = client.post("/api/workshop/WS-T/ailp/replay", json={"msg_id": "msg-1"})
    assert again.status_code == 200 and again.json()["duplicate"] is True


def test_http_probe_tooling_endpoint(client):
    r = client.post("/api/harness/h_probe/probe-tooling")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True and body["measured"]["fs_write"] is True
    assert "不等于" in body["measured"]["scope"]

# ── 可选字段：**设计稿里有、就得广告出来**（W前端-55 抓到的口径不一致）──
def test_optional_payload_is_advertised():
    """`counter_proposal` 在 `V117 §7-5` 里是「可空字符串」⇒ 字段表必须告诉前端它可以给。"""
    cat = A.types_catalog()
    ch = next(t for t in cat["types"] if t["msg_type"] == "CHALLENGE")
    assert [f["name"] for f in ch["required_payload"]] == ["contradiction", "evidence_ids"]
    assert [f["name"] for f in ch["optional_payload"]] == ["counter_proposal"]
    assert any("optional_payload" in r for r in cat["rules"]), "规则里要说清可选字段的口径"


def test_optional_payload_absent_is_fine():
    ok, errs, norm = A.validate(_env(msg_type="CHALLENGE",
                                     payload={"contradiction": "c", "evidence_ids": [REF]}),
                                allowed=[REF])
    assert ok, errs
    assert "counter_proposal" not in norm["payload"]


def test_optional_payload_validated_when_given():
    ok, errs, norm = A.validate(_env(msg_type="CHALLENGE",
                                     payload={"contradiction": "c", "evidence_ids": [REF],
                                              "counter_proposal": "改成这样"}),
                                allowed=[REF])
    assert ok and norm["payload"]["counter_proposal"] == "改成这样"
    bad, errs2, _ = A.validate(_env(msg_type="CHALLENGE",
                                    payload={"contradiction": "c", "evidence_ids": [REF],
                                             "counter_proposal": 123}),
                               allowed=[REF])
    assert bad is False and any(e["code"] == "A012" for e in errs2), "给了但类型错要拒"
