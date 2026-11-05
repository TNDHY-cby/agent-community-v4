# -*- coding: utf-8 -*-
"""守卫：**跨组织证据采信**（`V130`，2026-11-04 老板拍板）。

对应设计稿 `design-docs/V130_跨组织证据采信规则_设计_2026-11-04.md` §4 / §5 / §6。
纯函数脱机喂合成数据；账本与端点真跑（TestClient + 真临时数据目录 + 真文件）。

被守的四条硬规则：
  ① `accepted_self` **不是**通过（自报换不到采信）；
  ② 复核＝本侧重算 `sha256`，对不上就停在 `accepted_self` 并留 `peer_diff`；
  ③ 有争议 ⇒ `disputed`，从可验收里摘出（记录不删，冻结待裁）；
  ④ `L1` 的凭据指向的路径不存在 ⇒ 按 `L4` 算（`L1_PROOF_MISSING`）。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.append(str(_ROOT))

from agent_community.platform import evidence as ev  # noqa: E402
from agent_community.platform import task_ledger as ledger  # noqa: E402
from agent_community.platform import v5_evidence_view as view  # noqa: E402
from agent_community.platform import v5_roster as roster  # noqa: E402

SLOT = "前端组/员工01"
SHA = "a" * 64


def _mk_ws(tmp: Path) -> Path:
    wp = tmp / "ws"
    (wp / "_lead").mkdir(parents=True)
    (wp / "_lead" / "建模.md").write_text("# 建模\n", encoding="utf-8")
    (wp / "前端组").mkdir()
    (wp / "前端组" / "分工.md").write_text("- 版本: 2\n", encoding="utf-8")
    (wp / "前端组" / "_notice.md").write_text("", encoding="utf-8")
    (wp / SLOT / "任务").mkdir(parents=True)
    (wp / SLOT / "产物").mkdir(parents=True)
    return wp


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("AC_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("AC_MIND_ROOT", str(tmp_path / "minds"))
    monkeypatch.setattr(ledger, "_path", lambda: tmp_path / "data" / "task_ledger.json",
                        raising=True)
    monkeypatch.setattr(roster, "_path", lambda: tmp_path / "data" / "v5_roster.json",
                        raising=True)
    return {"tmp": tmp_path, "ws": _mk_ws(tmp_path)}


@pytest.fixture()
def client(env):
    from fastapi.testclient import TestClient

    from agent_community.platform import server as srv

    ws = srv.Workshop(workshop_id="WS-T", name="示例间", workspace_dir=str(env["ws"]),
                      hall_content="方向", status="running")
    srv.workshops["WS-T"] = ws
    roster.add_slots("WS-T", [{"岗位": SLOT, "harness": "h_x", "model": "m", "人数": 1}], source="C")
    return TestClient(srv.app, client=("127.0.0.1", 54321))


# ── 纯函数：采信态四值（§4）────────────────────────────────────────
def test_accept_states_and_only_peer_can_pass():
    assert ev.ACCEPT_STATES == ("accepted_self", "accepted_peer", "disputed", "refuted")
    assert ev.ACCEPT_CAN_PASS == {"accepted_self": False, "accepted_peer": True,
                                  "disputed": False, "refuted": False}
    assert ev.accept_state()["state"] == "accepted_self"
    assert ev.accept_state(peer_checked=True, peer_ok=True)["state"] == "accepted_peer"
    # 复核没对齐 ⇒ 仍只算自证
    assert ev.accept_state(peer_checked=True, peer_ok=False)["state"] == "accepted_self"
    # 争议优先于"复核通过"（冻结待裁）；证伪优先级最高
    assert ev.accept_state(disputed=True, peer_checked=True, peer_ok=True)["state"] == "disputed"
    assert ev.accept_state(refuted=True, disputed=True)["state"] == "refuted"


# ── 纯函数：逐文件重算比对（§4 规则二）──────────────────────────────
def test_diff_peer_artifacts_matches_by_relative_key_and_normalizes_sha():
    local = {"报告.md": {"sha256": SHA, "bytes": 12},
             "表.md": {"sha256": "b" * 64, "bytes": 3}}
    out = ev.diff_peer_artifacts(
        [{"name": "码农_2/报告.md", "slot_dir": "码农_2", "sha256": "A" * 64, "bytes": 12},
         {"name": "码农_2/表.md", "sha256": "b" * 64, "bytes": 3}],
        local, local_slot_dir=SLOT)
    assert out["ok"] is True and out["matched"] == 2
    assert out["files"][0]["key"] == "报告.md", "比对键是相对产物目录的路径（两边工位名不同）"


def test_diff_peer_artifacts_rejects_wrong_sha_missing_and_no_hash():
    local = {"报告.md": {"sha256": SHA, "bytes": 12}}
    out = ev.diff_peer_artifacts(
        [{"name": "x/报告.md", "sha256": "c" * 64},
         {"name": "x/缺.md", "sha256": "d" * 64},
         {"name": "x/没hash.md"}], local, local_slot_dir=SLOT)
    assert out["ok"] is False
    statuses = sorted(f["status"] for f in out["files"] if f["status"] != "match")
    assert statuses == ["missing_local", "peer_no_hash", "sha_mismatch"]
    assert ev.diff_peer_artifacts([], local)["ok"] is False, "空清单不算复核"


def test_proof_looks_like_path_does_not_misjudge_text_proof():
    assert view.proof_looks_like_path("D:/x/y.db") is True
    assert view.proof_looks_like_path("/var/lib/x.db") is True
    assert view.proof_looks_like_path("./rel/x.db") is True
    # 文字凭据不含"本机路径形态"⇒ 不许 stat，也就不会误拒
    assert view.proof_looks_like_path("示例库 sqlite + 口令命中（人工核对）") is False
    assert view.proof_looks_like_path("a2a/session #12") is False


def test_check_level_L1_proof_missing_falls_back_to_L4():
    out = view.check_level("L1", "D:/nope/x.db", proof_exists=False)
    assert out["code"] == "L1_PROOF_MISSING" and out["normalized"] == "L4"
    assert "L4" in out["error"]
    assert view.check_level("L1", "文字凭据", proof_exists=None)["ok"] is True


# ── 账本：自证不算通过 / 复核才算（§6）──────────────────────────────
def test_self_attestation_cannot_pass_review(env):
    ledger.issue("WS-T", 1, slot_dir=SLOT, file="x")
    ledger.note_external_result("WS-T", 1, source="a2a", external_task_id="t1",
                                external_state="completed", result="我做完了")
    rec = ledger.get("WS-T", 1)
    assert rec["external"]["peer_accepted"] == "accepted_self"
    out = ledger.review("WS-T", 1, result="通过", evidence_level="L4")
    assert out["code"] == "PEER_NOT_REVIEWED"
    assert "自报换不到采信" in out["error"]
    assert ledger.get("WS-T", 1)["state"] != "accepted", "被拒后状态不许推进"


def test_peer_review_pass_records_checked_at_and_unlocks_review(env):
    local = {"报告.md": {"sha256": SHA, "bytes": 12}}
    ledger.issue("WS-T", 2, slot_dir=SLOT, file="x")
    ledger.note_external_result("WS-T", 2, source="a2a", result="交付")
    out = ledger.record_peer_review("WS-T", 2,
                                    peer_files=[{"name": "x/报告.md", "sha256": SHA, "bytes": 12}],
                                    local_files=local, local_slot_dir=SLOT)
    assert out["ok"] is True and out["accept_state"] == "accepted_peer"
    assert out["peer_checked_at"]
    rec = ledger.get("WS-T", 2)
    assert rec["external"]["peer_checked_at"] and rec["external"]["peer_diff"]["matched"] == 1
    ok = ledger.review("WS-T", 2, result="通过", evidence_level="L4")
    assert ok["state"] == "accepted", ok


def test_peer_review_failure_stays_self_and_lists_diff(env):
    out = ledger.record_peer_review("WS-T", 3, peer_files=[{"name": "x/报告.md", "sha256": "f" * 64}],
                                   local_files={"报告.md": {"sha256": SHA, "bytes": 12}},
                                   local_slot_dir=SLOT)
    assert out["code"] == "NO_SUCH_TASK" or out["ok"] is False
    ledger.issue("WS-T", 3, slot_dir=SLOT, file="x")
    ledger.note_external_result("WS-T", 3, source="a2a", result="交付")
    out = ledger.record_peer_review("WS-T", 3, peer_files=[{"name": "x/报告.md", "sha256": "f" * 64}],
                                   local_files={"报告.md": {"sha256": SHA, "bytes": 12}},
                                   local_slot_dir=SLOT)
    rec = ledger.get("WS-T", 3)
    assert out["ok"] is False and rec["external"]["peer_accepted"] == "accepted_self"
    bad = [f for f in rec["external"]["peer_diff"]["files"] if f["status"] != "match"]
    assert bad and bad[0]["status"] == "sha_mismatch"


def test_dispute_freezes_and_keeps_record(env):
    ledger.issue("WS-T", 4, slot_dir=SLOT, file="x")
    ledger.note_external_result("WS-T", 4, source="a2a", result="交付")
    out = ledger.mark_dispute("WS-T", 4, raised_by="接收方",
                              claims=[{"by": "交付方", "claim": "我交的是 v2"},
                                      {"by": "接收方", "claim": "sha256 对不上"}],
                              adjudicator="组长", why="哈希不一致")
    assert out["ok"] and out["accept_state"] == "disputed" and out["passable"] is False
    rec = ledger.get("WS-T", 4)
    assert rec["external"]["peer_accepted"] == "disputed" and rec["passable"] is False
    assert len(rec["external"]["dispute"]["claims"]) == 2
    rv = ledger.review("WS-T", 4, result="通过", evidence_level="L4")
    assert rv["code"] == "DISPUTE_OPEN"
    v = ledger.peer_view("WS-T", 4)
    assert v["dispute"]["raised_by"] == "接收方" and v["dispute"]["adjudicator"] == "组长"
    assert v["passable"] is False


def test_refuted_blocks_review(env):
    ledger.issue("WS-T", 5, slot_dir=SLOT, file="x")
    ledger.note_external_result("WS-T", 5, source="a2a", result="交付")
    assert ledger.mark_dispute("WS-T", 5, raised_by="组长",
                               claims=[{"by": "组长", "claim": "产物是另一个任务的"}],
                               outcome="refuted")["accept_state"] == "refuted"
    assert ledger.review("WS-T", 5, result="通过", evidence_level="L4")["code"] == "PEER_REFUTED"


def test_dispute_without_claims_is_rejected(env):
    ledger.issue("WS-T", 6, slot_dir=SLOT, file="x")
    out = ledger.mark_dispute("WS-T", 6, raised_by="接收方", claims=[], adjudicator="组长")
    assert out["code"] == "CLAIMS_REQUIRED", "争议记录必须留下双方说了什么"


# ── 端点：真 HTTP（peer-delivery / peer-review / dispute）─────────────
def test_http_peer_delivery_then_review_boxed_out(client, env):
    ledger.issue("WS-T", 1, slot_dir=SLOT, file="x")
    r = client.post("/api/workshop/WS-T/tasks/1/peer-delivery",
                    json={"source": "manual", "result": "我做完了", "artifacts": []})
    assert r.status_code == 200 and r.json()["accept_state"] == "accepted_self"
    rv = client.post("/api/workshop/WS-T/tasks/1/review",
                     json={"result": "通过", "evidence_level": "L4"})
    assert rv.status_code == 400 and rv.json()["code"] == "PEER_NOT_REVIEWED"


def test_http_peer_review_and_readonly_view(client, env):
    (env["ws"] / SLOT / "产物" / "报告.md").write_text("同一份内容", encoding="utf-8")
    ledger.issue("WS-T", 2, slot_dir=SLOT, file="x")
    ledger.note_external_result("WS-T", 2, source="a2a", result="交付")
    import hashlib
    data = (env["ws"] / SLOT / "产物" / "报告.md").read_bytes()
    sha = hashlib.sha256(data).hexdigest()
    r = client.post("/api/workshop/WS-T/tasks/2/peer-review",
                    json={"peer_files": [{"name": "对端工位/报告.md", "sha256": sha,
                                          "bytes": len(data)}]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["accept_state"] == "accepted_peer" and body["peer_checked_at"]
    g = client.get("/api/workshop/WS-T/tasks/2/peer-review").json()
    assert g["accept_state"] == "accepted_peer" and g["can_pass"] is True


def test_http_peer_review_without_peer_files_explains_next(client, env):
    ledger.issue("WS-T", 3, slot_dir=SLOT, file="x")
    r = client.post("/api/workshop/WS-T/tasks/3/peer-review", json={})
    assert r.status_code == 400 and r.json()["code"] == "PEER_FILES_REQUIRED"
    assert r.json()["next"], "失败要说清怎么办（不许空 next）"


def test_http_dispute_and_view(client, env):
    ledger.issue("WS-T", 4, slot_dir=SLOT, file="x")
    ledger.note_external_result("WS-T", 4, source="a2a", result="交付")
    r = client.post("/api/workshop/WS-T/tasks/4/dispute",
                    json={"raised_by": "接收方",
                          "claims": ["我交的是 v2", "本侧 sha256 对不上"],
                          "adjudicator": "组长"})
    assert r.status_code == 200 and r.json()["accept_state"] == "disputed"
    g = client.get("/api/workshop/WS-T/tasks/4/peer-review").json()
    assert g["dispute"]["raised_by"] == "接收方" and len(g["dispute"]["claims"]) == 2
    assert g["passable"] is False
