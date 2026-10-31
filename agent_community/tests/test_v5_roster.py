# -*- coding: utf-8 -*-
"""守卫：**slot/member 双层 + 闸门**（`V108` §6、`V112`，P2 批的 12 条判据）。

编号与 `V115` §2.S 对齐（S1~S12）。全部真跑：真写库、真判状态、真 HTTP。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.append(str(_ROOT))

from agent_community.platform import v5_gate as gate  # noqa: E402
from agent_community.platform import v5_roster as roster  # noqa: E402


@pytest.fixture()
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(roster, "_path", lambda: tmp_path / "v5_roster.json", raising=True)
    return tmp_path


class _WS:
    def __init__(self, status="staffing", workshop_id="WS-T"):
        self.status = status
        self.workshop_id = workshop_id


ROSTER_ROWS = [
    {"岗位": "前端组/员工01", "harness": "h_cursor", "model": "claude-x", "人数": 2},
    {"岗位": "后端组/员工01", "harness": "h_trae_cn", "model": "deepseek-v3", "人数": 1},
]


# ── S1：slot.proposed 不参与闸门 ────────────────────────────────────
def test_s1_proposed_slot_does_not_block_any_gate(store):
    roster.add_slots("WS-T", ROSTER_ROWS, source="C")
    r = roster.roster("WS-T")
    assert all(s["state"] in ("approved", "filled") for s in r["slots"])
    # 名单已落但没人接上：start_running 该拦（G4），但**不是因为 slot.proposed**
    ok, code, four = gate.check(_WS("staffing"), "start_running")
    assert not ok and code == "GATE_G4" and four["how"]


# ── S2：主键 (workshop_id, member_seq)，同 harness 同 model 互不干扰 ──
def test_s2_same_harness_model_two_seats_are_independent(store):
    roster.add_slots("WS-T", [{"岗位": "前端组/员工01", "harness": "h_cursor",
                               "model": "claude-x", "人数": 2}], source="C")
    r = roster.roster("WS-T")
    seqs = [m["member_seq"] for m in r["members"]]
    assert len(seqs) == 2 and len(set(seqs)) == 2
    assert len({m["session_label"] for m in r["members"]}) == 2, "会话名要能区分两个工位"
    roster.set_evidence("WS-T", seqs[0], {"paste_seen": True})
    r2 = roster.roster("WS-T")
    states = {m["member_seq"]: m["state"] for m in r2["members"]}
    assert states[seqs[0]] == "acked" and states[seqs[1]] == "pending", "一个的证据不能串到另一个"


# ── S3：slot 1:N，够数才算 filled ───────────────────────────────────
def test_s3_headcount_and_filled(store):
    out = roster.add_slots("WS-T", ROSTER_ROWS, source="C")
    slots = {s["slot_dir"]: s for s in out["roster"]["slots"]}
    assert slots["前端组/员工01"]["headcount"] == 2 and slots["前端组/员工01"]["state"] == "filled"
    assert slots["后端组/员工01"]["headcount"] == 1
    # 再加一个人到同岗位 ⇒ 只更新人数并把新工位补出来，不重复建 slot
    out2 = roster.add_slots("WS-T", [{"岗位": "后端组/员工01", "harness": "h_trae_cn",
                                      "model": "deepseek-v3", "人数": 2}], source="C")
    assert out2["slots_created"] == [] and out2["members_created"], "应只补工位"
    assert len([s for s in out2["roster"]["slots"] if s["slot_dir"] == "后端组/员工01"]) == 1


# ── S4/S5：证据阶梯 —— 攒不齐停在 acked，并说清缺什么 ───────────────
def test_s4_c_visual_needs_three_items(store):
    roster.add_slots("WS-T", ROSTER_ROWS, source="C")
    e1 = roster.set_evidence("WS-T", 1, {"paste_seen": True})
    assert e1["state"] == "acked"
    assert set(e1["need"]["missing_evidence"]) == {"press_ok", "rename_seen"}
    e2 = roster.set_evidence("WS-T", 1, {"press_ok": True})
    assert e2["state"] == "acked", "两项也不算齐"
    e3 = roster.set_evidence("WS-T", 1, {"rename_seen": True})
    assert e3["state"] == "entered" or e3["state"] == "active", e3
    assert e3["state"] != "active", "没写 verified_scope 不许 active"


def test_s5_scope_required_for_active(store):
    roster.add_slots("WS-T", ROSTER_ROWS, source="C")
    roster.set_evidence("WS-T", 1, {"paste_seen": True, "press_ok": True, "rename_seen": True})
    m = roster.find_member("WS-T", 1)
    assert m["state"] != "active", "没写覆盖范围 ⇒ 不许 active"
    out = roster.set_evidence("WS-T", 1, {"verified_scope": "粘贴+回车+改名，未验证它能回话"})
    assert out["state"] == "active"


def test_s4b_a_tier_single_evidence_enough_with_scope(store):
    roster.add_slots("WS-T", ROSTER_ROWS, source="A")
    out = roster.set_evidence("WS-T", 1, {"protocol_ok": True, "tier": "a_protocol",
                                          "verified_scope": "真调 tools/list 回了 14 个工具"})
    assert out["state"] == "active" and out["tier"] == "a_protocol"


# ── S6：状态迁移留痕（每一步都记 at/to/why，不许无痕跳变）───────────
def test_s6_state_log_records_every_transition(store):
    roster.add_slots("WS-T", ROSTER_ROWS, source="C")
    roster.set_evidence("WS-T", 1, {"paste_seen": True})
    roster.set_evidence("WS-T", 1, {"press_ok": True, "rename_seen": True,
                                    "verified_scope": "三项齐了"})
    m = roster.find_member("WS-T", 1)
    tos = [x["to"] for x in m["state_log"]]
    assert tos[0] == "pending" and "acked" in tos and tos[-1] == "active"
    assert all(x.get("at") and x.get("why") for x in m["state_log"])


# ── S7：闸门四要素齐全（六道闸各造一次失败）─────────────────────────
def test_s7_all_gates_return_four_elements(store):
    roster.add_slots("WS-T", [{"岗位": "前端组/员工01", "harness": "h_cursor",
                               "model": "claude-x", "人数": 1}], source="C")
    cases = [
        ("fill_direction", {"brief": "", "enum": []}, "GATE_G1"),
        ("start_modeling", {}, "GATE_G2"),
        ("start_running", {}, "GATE_G4"),
        ("dispatch", {"member_seq": 1}, "GATE_G5"),
        ("change_path", {}, "WS_PATH_LOCKED"),
    ]
    for action, ctx, want in cases:
        ok, code, four = gate.check(_WS("running" if action == "change_path" else "staffing"),
                                    action, ctx)
        assert not ok and code == want, (action, code)
        assert set(four) == {"who", "what", "where", "how"} and all(four.values()), (action, four)


# ── S8：dispatch 只放 active 的工位 ─────────────────────────────────
def test_s8_dispatch_requires_active_member(store):
    roster.add_slots("WS-T", ROSTER_ROWS, source="C")
    ok, code, four = gate.check(_WS("running"), "dispatch", {"member_seq": 1})
    assert not ok and code == "GATE_G5" and "active" in four["what"]
    roster.set_evidence("WS-T", 1, {"paste_seen": True, "press_ok": True, "rename_seen": True,
                                    "verified_scope": "三项齐"})
    ok2, code2, _ = gate.check(_WS("running"), "dispatch", {"member_seq": 1})
    assert ok2 and code2 == ""


# ── S9：旧数据迁移只映射、不猜；映不出来的列出来 ────────────────────
def test_s9_legacy_migration_marks_unknown(store):
    legacy = [{"member_id": "m1", "display_name": "前端组/员工01", "status": "idle",
               "harness_ids": ["h_cursor"], "session_aliases": ["WS-T-前端组-01"]},
              {"member_id": "m2", "display_name": "后端组/员工01", "status": "某种旧状态",
               "harness_ids": [], "session_aliases": []}]
    out = roster.migrate_legacy("WS-T", legacy)
    assert out["mapped"][0]["state"] == "active", "旧 idle = 工具面已通 ⇒ active"
    assert out["legacy_unknown"] and out["legacy_unknown"][0]["status"] == "某种旧状态"
    assert out["roster"]["members"], "迁移后要看得到工位"
    again = roster.migrate_legacy("WS-T", legacy)
    assert again.get("skipped") is True, "重复迁移要跳过（不许翻倍）"


# ── S10：状态回执真迁移 member ─────────────────────────────────────
def test_s10_state_receipt_applies_to_member(store):
    roster.add_slots("WS-T", ROSTER_ROWS, source="C")
    out = roster.apply_state_rows("WS-T", [{"状态": "前端组/员工01", "新": "blocked",
                                           "原因": "凭据缺失"}], source="A")
    assert out["applied"], out
    m = [x for x in roster.roster("WS-T")["members"] if x["slot_dir"] == "前端组/员工01"][0]
    assert m["state"] == "blocked"
    bad = roster.apply_state_rows("WS-T", [{"状态": "不存在/岗位", "新": "active", "原因": ""}],
                                  source="A")
    assert bad["rejected"] and not bad["applied"], "错的行要明确拒，不许静默"


# ── S11/S12：HTTP 端到端（roster / evidence / gate 自省）──────────────
@pytest.fixture()
def client(store, monkeypatch):
    monkeypatch.setenv("AC_DATA_DIR", str(store))
    monkeypatch.setattr(roster, "_path", lambda: store / "v5_roster.json", raising=True)
    from fastapi.testclient import TestClient

    from agent_community.platform import server as srv

    ws = srv.Workshop(workshop_id="WS-T", name="示例间", workspace_dir=str(store / "ws"),
                      hall_content="方向")
    srv.workshops["WS-T"] = ws
    return TestClient(srv.app, client=("127.0.0.1", 54321))


def test_s11_roster_endpoint_shape(client):
    r = client.get("/api/workshop/WS-T/roster")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] and "slots" in body and "members" in body
    assert body["counts"]["pending"] == 0


def test_s12_end_to_end_roster_then_evidence_then_gate(client):
    text = ("#回执 v1\n类型: 名单\n工作间: WS-T\n成员: 组长\n凭据:\n序号: 7\n---\n"
            "岗位: 前端组/员工01 | harness: h_cursor | model: claude-x | 人数: 1\n---\n行数: 1")
    r = client.post("/api/workshop/WS-T/intake?source=C&confirm=1", json={"text": text})
    body = r.json()
    assert body["ok"] and body["applied"] is True, body
    seq = client.get("/api/workshop/WS-T/roster").json()["members"][0]["member_seq"]
    ev = client.post("/api/workshop/WS-T/members/%d/evidence" % seq,
                     json={"paste_seen": True, "press_ok": True, "rename_seen": True,
                           "verified_scope": "粘贴+回车+改名，未验证回话"})
    assert ev.json()["state"] == "active"
    g = client.get("/api/workshop/WS-T/gate/start_running").json()
    assert g["allowed"] is True and g["code"] == ""
    # draft 阶段允许改路径
    assert client.get("/api/workshop/WS-T/gate/change_path").json()["allowed"] is True
    # 进 running 后路径锁死，且必须给出"怎么做"
    from agent_community.platform import server as srv

    srv.workshops["WS-T"].status = "running"
    g2 = client.get("/api/workshop/WS-T/gate/change_path").json()
    assert g2["allowed"] is False and g2["code"] == "WS_PATH_LOCKED"
    assert g2["four"]["how"], "被拦要给出怎么做"

# ── 名单镜像 `_meta/roster.json`（2026-10-30 补 §21.3 欠账）──────────
_RCPT = ("#回执 v1\n类型: 名单\n工作间: WS-MIRROR\n成员: 组长\n凭据:\n序号: 1\n---\n"
         "岗位: 前端组/员工01 | harness: h1 | model: m | 人数: 1\n---\n行数: 1")


def _ws_with_dir(tmp_path, wid="WS-MIRROR"):
    from agent_community.platform import server as srv

    wp = tmp_path / "ws"
    wp.mkdir(parents=True, exist_ok=True)
    srv.workshops[wid] = srv.Workshop(workshop_id=wid, name="镜像", workspace_dir=str(wp),
                                      hall_content="x", status="staffing")
    return wp


def test_mirror_written_after_intake(tmp_path, monkeypatch):
    """名单落库 ⇒ `_meta/roster.json` 真出现，且与 `GET …/roster` 一致。"""
    import json as _json

    monkeypatch.setenv("AC_DATA_DIR", str(tmp_path / "data"))
    from fastapi.testclient import TestClient

    from agent_community.platform import server as srv
    from agent_community.platform import v5_roster as roster

    monkeypatch.setattr(roster, "_path", lambda: tmp_path / "data" / "v5_roster.json",
                        raising=True)
    wp = _ws_with_dir(tmp_path)
    c = TestClient(srv.app, client=("127.0.0.1", 54321))
    r = c.post("/api/workshop/WS-MIRROR/intake?source=C&confirm=1", json={"text": _RCPT})
    assert r.status_code == 200, r.text
    assert (r.json().get("mirror") or {}).get("ok") is True, "响应里要如实带镜像结果"
    f = wp / "_meta" / "roster.json"
    assert f.is_file(), "工作区里没落 `_meta/roster.json` 镜像"
    d = _json.loads(f.read_text(encoding="utf-8"))
    assert d["schema"] == "coop.workshop.roster/1"
    live = c.get("/api/workshop/WS-MIRROR/roster").json()
    assert len(d["members"]) == len(live["members"]), "镜像与主库成员数不一致"


def test_mirror_updates_when_evidence_changes(tmp_path, monkeypatch):
    """证据变了 ⇒ 镜像跟着更新（状态从 pending 变 active）。"""
    import json as _json

    monkeypatch.setenv("AC_DATA_DIR", str(tmp_path / "data"))
    from fastapi.testclient import TestClient

    from agent_community.platform import server as srv
    from agent_community.platform import v5_roster as roster

    monkeypatch.setattr(roster, "_path", lambda: tmp_path / "data" / "v5_roster.json",
                        raising=True)
    wp = _ws_with_dir(tmp_path)
    c = TestClient(srv.app, client=("127.0.0.1", 54321))
    c.post("/api/workshop/WS-MIRROR/intake?source=C&confirm=1", json={"text": _RCPT})
    f = wp / "_meta" / "roster.json"
    before = _json.loads(f.read_text(encoding="utf-8"))
    assert before["members"][0]["state"] == "pending"
    seq = before["members"][0]["member_seq"]
    r = c.post("/api/workshop/WS-MIRROR/members/%d/evidence" % seq,
               json={"tier": "c_visual", "paste_seen": True, "press_ok": True,
                     "rename_seen": True, "verified_scope": "三项齐"})
    assert r.status_code == 200
    after = _json.loads(f.read_text(encoding="utf-8"))
    assert after["members"][0]["state"] == "active", "镜像没跟着更新"


def test_mirror_is_not_the_source_of_truth(tmp_path, monkeypatch):
    """镜像不是真源：手改镜像不影响主库读数。"""
    import json as _json

    monkeypatch.setenv("AC_DATA_DIR", str(tmp_path / "data"))
    from fastapi.testclient import TestClient

    from agent_community.platform import server as srv
    from agent_community.platform import v5_roster as roster

    monkeypatch.setattr(roster, "_path", lambda: tmp_path / "data" / "v5_roster.json",
                        raising=True)
    wp = _ws_with_dir(tmp_path)
    c = TestClient(srv.app, client=("127.0.0.1", 54321))
    c.post("/api/workshop/WS-MIRROR/intake?source=C&confirm=1", json={"text": _RCPT})
    f = wp / "_meta" / "roster.json"
    d = _json.loads(f.read_text(encoding="utf-8"))
    d["members"] = []                       # 手改镜像（模拟有人瞎改）
    f.write_text(_json.dumps(d, ensure_ascii=False), encoding="utf-8")
    live = c.get("/api/workshop/WS-MIRROR/roster").json()
    assert len(live["members"]) == 1, "主库不该被镜像带偏（镜像不是真源）"


def test_mirror_reports_when_no_workspace_dir(tmp_path, monkeypatch):
    """没有工作区路径 ⇒ 如实说（ok:false + why），不假装写了。"""
    monkeypatch.setenv("AC_DATA_DIR", str(tmp_path / "data"))
    from agent_community.platform import v5_roster as roster

    monkeypatch.setattr(roster, "_path", lambda: tmp_path / "data" / "v5_roster.json",
                        raising=True)
    out = roster.write_mirror("WS-NONE", "")
    assert out["ok"] is False and "工作区路径" in out["why"]

# ── 闸门/详情必须看见 v5 工位（2026-10-30 修的真 bug）────────────────
def _ws_with_slot(tmp_path, wid="WS-GATE", state_active=False):
    from fastapi.testclient import TestClient

    from agent_community.platform import server as srv
    from agent_community.platform import v5_roster as roster

    wp = tmp_path / "ws"
    wp.mkdir(parents=True, exist_ok=True)
    ws = srv.Workshop(workshop_id=wid, name="闸门", workspace_dir=str(wp), hall_content="x")
    srv.workshops[wid] = ws
    roster.add_slots(wid, [{"岗位": "前端组/员工01", "harness": "h1", "model": "m",
                            "人数": 1}], source="C")
    if state_active:
        seq = roster.roster(wid)["members"][0]["member_seq"]
        roster.set_evidence(wid, seq, {"tier": "c_visual", "paste_seen": True,
                                       "press_ok": True, "rename_seen": True,
                                       "verified_scope": "三项齐"}, who="user")
    return TestClient(srv.app, client=("127.0.0.1", 54321)), ws


def test_gate_sees_v5_slots(tmp_path, monkeypatch):
    """有 v5 工位（pending）⇒ 闸门**不再说「没有成员」**，而是「没人接上」，且人数=1、口径=v5。"""
    monkeypatch.setenv("AC_DATA_DIR", str(tmp_path / "data"))
    from agent_community.platform import v5_roster as roster

    monkeypatch.setattr(roster, "_path", lambda: tmp_path / "data" / "v5_roster.json",
                        raising=True)
    c, _ws = _ws_with_slot(tmp_path)
    info = c.get("/api/workshop/WS-GATE").json()["gate"]
    assert info["state"] != "no_members", "有工位却说没有成员（假状态）"
    assert info["state"] == "no_verified_member"
    assert info["members_count"] == 1 and info["members_source"] == "v5"


def test_gate_ready_when_v5_member_active(tmp_path, monkeypatch):
    """v5 工位接上（active）⇒ 闸门放行。"""
    monkeypatch.setenv("AC_DATA_DIR", str(tmp_path / "data"))
    from agent_community.platform import v5_roster as roster

    monkeypatch.setattr(roster, "_path", lambda: tmp_path / "data" / "v5_roster.json",
                        raising=True)
    c, _ws = _ws_with_slot(tmp_path, state_active=True)
    info = c.get("/api/workshop/WS-GATE").json()["gate"]
    assert info["state"] == "ready", info
    assert info["verified_count"] >= 1


def test_workshop_detail_carries_v5_roster(tmp_path, monkeypatch):
    """详情接口带 `members_v5` / `roster_counts` / `members_source`，且旧 `members` 形状不变。"""
    monkeypatch.setenv("AC_DATA_DIR", str(tmp_path / "data"))
    from agent_community.platform import v5_roster as roster

    monkeypatch.setattr(roster, "_path", lambda: tmp_path / "data" / "v5_roster.json",
                        raising=True)
    c, _ws = _ws_with_slot(tmp_path)
    d = c.get("/api/workshop/WS-GATE").json()
    assert isinstance(d["members"], list), "旧 members 形状不许变（老逻辑还在用）"
    assert d["members_v5"] and d["members_v5"][0]["slot_dir"] == "前端组/员工01"
    assert d["members_source"] == "v5"
    assert d["roster_counts"]["pending"] == 1
    assert "token" not in str(d["members_v5"]).lower(), "名单摘要里不许出现凭据"


def test_gate_survives_roster_module_failure(tmp_path, monkeypatch):
    """名单模块坏掉 ⇒ 闸门**不许 500**，如实降级（并每进程喊一次）。"""
    monkeypatch.setenv("AC_DATA_DIR", str(tmp_path / "data"))
    from agent_community.platform import server as srv
    from agent_community.platform import v5_roster as roster
    from agent_community.platform import workshop_gate as gate

    def boom(*a, **k):
        raise RuntimeError("名单库坏了")

    monkeypatch.setattr(roster, "effective_members", boom, raising=True)
    ws = srv.Workshop(workshop_id="WS-BOOM", name="坏", workspace_dir=str(tmp_path),
                      hall_content="x")
    srv.workshops["WS-BOOM"] = ws
    out = gate.info(ws)                      # 不抛 = 不 500
    assert out["state"] in ("no_members", "no_verified_member", "ready")
    assert out["members_source"] in ("legacy", "empty")

# ── 界面加员工 ⇒ v5 名单里也有（老板 2026-10-30 SOP §0-11）──────────
def test_manual_add_lands_in_v5_roster(tmp_path, monkeypatch):
    """车间页「+ 添加职位」加的员工，必须同时出现在 v5 名单里（闸门/派活都看那份）。"""
    import json as _json

    monkeypatch.setenv("AC_DATA_DIR", str(tmp_path / "data"))
    from fastapi.testclient import TestClient

    from agent_community.platform import server as srv
    from agent_community.platform import v5_roster as roster

    monkeypatch.setattr(roster, "_path", lambda: tmp_path / "data" / "v5_roster.json",
                        raising=True)
    wp = tmp_path / "ws"
    wp.mkdir(parents=True, exist_ok=True)
    srv.workshops["WS-MAN"] = srv.Workshop(workshop_id="WS-MAN", name="手动加人",
                                           workspace_dir=str(wp), hall_content="x")
    c = TestClient(srv.app, client=("127.0.0.1", 54321))

    before = c.get("/api/workshop/WS-MAN").json()
    assert before["gate"]["state"] == "no_members"

    r = c.post("/api/workshop/WS-MAN/members",
               json={"members": [{"role": "前端组/员工01", "display_name": "小前",
                                  "harness_ids": ["h1"]}]})
    assert r.status_code == 200, r.text
    sync = r.json().get("v5_sync") or {}
    assert sync.get("ok") is True, sync

    live = c.get("/api/workshop/WS-MAN/roster").json()
    dirs = [m["slot_dir"] for m in live["members"]]
    assert "前端组/员工01" in dirs, "v5 名单里没有这个工位（界面加了人却看不见）"
    assert live["members"][0]["source"] == "manual", "来源要标 manual（别冒充组长回执）"

    d = c.get("/api/workshop/WS-MAN").json()
    assert d["gate"]["state"] == "no_verified_member", "闸门应看见这个工位（不再是 no_members）"
    assert d["members_source"] == "v5"

    mirror = wp / "_meta" / "roster.json"
    assert mirror.is_file(), "镜像也要跟着更新"
    assert "前端组/员工01" in _json.dumps(_json.loads(mirror.read_text(encoding="utf-8")),
                                         ensure_ascii=False)

    # 旧 members 字段是投影，照旧有（老逻辑还在读）
    assert isinstance(d["members"], list) and d["members"], "旧 members 投影不该空掉"


def test_manual_clear_members_is_honest(tmp_path, monkeypatch):
    """把名单清空：不报错，`v5_sync` 如实说"没有要同步的岗位"，不假装写了。"""
    monkeypatch.setenv("AC_DATA_DIR", str(tmp_path / "data"))
    from fastapi.testclient import TestClient

    from agent_community.platform import server as srv
    from agent_community.platform import v5_roster as roster

    monkeypatch.setattr(roster, "_path", lambda: tmp_path / "data" / "v5_roster.json",
                        raising=True)
    wp = tmp_path / "ws2"
    wp.mkdir(parents=True, exist_ok=True)
    srv.workshops["WS-MAN2"] = srv.Workshop(workshop_id="WS-MAN2", name="清空",
                                            workspace_dir=str(wp), hall_content="x")
    c = TestClient(srv.app, client=("127.0.0.1", 54321))
    r = c.post("/api/workshop/WS-MAN2/members", json={"members": []})
    assert r.status_code == 200
    sync = r.json().get("v5_sync") or {}
    assert sync.get("ok") is True and "没有要新增" in str(sync.get("note", "")), sync

def test_manual_remove_does_not_touch_roster(tmp_path, monkeypatch):
    """**故意不对称**：界面删人不自动删 v5 名单（真源是组长回执），但响应必须说清楚。"""
    monkeypatch.setenv("AC_DATA_DIR", str(tmp_path / "data"))
    from fastapi.testclient import TestClient

    from agent_community.platform import server as srv
    from agent_community.platform import v5_roster as roster

    monkeypatch.setattr(roster, "_path", lambda: tmp_path / "data" / "v5_roster.json",
                        raising=True)
    wp = tmp_path / "ws3"
    wp.mkdir(parents=True, exist_ok=True)
    srv.workshops["WS-DEL"] = srv.Workshop(workshop_id="WS-DEL", name="删人",
                                           workspace_dir=str(wp), hall_content="x")
    c = TestClient(srv.app, client=("127.0.0.1", 54321))
    c.post("/api/workshop/WS-DEL/members",
           json={"members": [{"role": "前端组/员工01", "harness_ids": ["h1"]}]})
    assert [m["slot_dir"] for m in c.get("/api/workshop/WS-DEL/roster").json()["members"]]

    r = c.post("/api/workshop/WS-DEL/members", json={"members": []})
    sync = r.json().get("v5_sync") or {}
    assert "删除不会" in str(sync.get("note", "")), "删人时必须如实说明「没删名单」"
    live = c.get("/api/workshop/WS-DEL/roster").json()
    assert [m["slot_dir"] for m in live["members"]] == ["前端组/员工01"], \
        "界面删人不该抹掉名单（真源是组长回执）"

# ── 「让平台 AI 选人」已退役（2026-10-31）────────────────────────────
def _ws_plain(tmp_path, wid="WS-AI"):
    from fastapi.testclient import TestClient

    from agent_community.platform import server as srv

    wp = tmp_path / "wsai"
    wp.mkdir(parents=True, exist_ok=True)
    srv.workshops[wid] = srv.Workshop(workshop_id=wid, name="选人", workspace_dir=str(wp),
                                      hall_content="x")
    return TestClient(srv.app, client=("127.0.0.1", 54321))


def test_ai_select_is_retired(tmp_path, monkeypatch):
    """默认 410 `AI_SELECT_RETIRED`，给 why/next，且**不改名单**。"""
    monkeypatch.setenv("AC_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("AC_ALLOW_LEGACY_AI_SELECT", raising=False)
    from agent_community.platform import v5_roster as roster

    monkeypatch.setattr(roster, "_path", lambda: tmp_path / "data" / "v5_roster.json",
                        raising=True)
    c = _ws_plain(tmp_path)
    r = c.post("/api/workshop/WS-AI/select-members")
    assert r.status_code == 410, r.text
    b = r.json()
    assert b["code"] == "AI_SELECT_RETIRED" and b["legacy"] is False
    assert b["why"] and b["next"], "失败要说话：why / next 都要有"
    assert "组长" in b["next"], "next 必须指向标准路径（组长回执）"
    assert c.get("/api/workshop/WS-AI/roster").json()["members"] == [], "拒的时候不许改名单"


def test_ai_select_legacy_flag_still_serves(tmp_path, monkeypatch):
    """兼容开关打开后不再 410（旧行为保留，便于回退）。"""
    monkeypatch.setenv("AC_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("AC_ALLOW_LEGACY_AI_SELECT", "1")
    from agent_community.platform import v5_roster as roster

    monkeypatch.setattr(roster, "_path", lambda: tmp_path / "data" / "v5_roster.json",
                        raising=True)
    c = _ws_plain(tmp_path, "WS-AI2")
    r = c.post("/api/workshop/WS-AI2/select-members")
    assert r.status_code != 410, "开了兼容开关还 410，等于把回退路也堵了"
    # 测试环境里没有真 uvicorn 实例，应急口子可能**如实失败**（不许崩成无信息的 500）
    if r.status_code == 500:
        assert r.json().get("code") == "LEGACY_AI_SELECT_FAILED"
        assert r.json().get("next"), "失败也要给出下一步"


def test_manual_add_still_works(tmp_path, monkeypatch):
    """「+ 添加职位」是**手动补录**，不随 AI 选人一起退役。"""
    monkeypatch.setenv("AC_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("AC_ALLOW_LEGACY_AI_SELECT", raising=False)
    from agent_community.platform import v5_roster as roster

    monkeypatch.setattr(roster, "_path", lambda: tmp_path / "data" / "v5_roster.json",
                        raising=True)
    c = _ws_plain(tmp_path, "WS-AI3")
    r = c.post("/api/workshop/WS-AI3/members",
               json={"members": [{"role": "前端组/员工01", "harness_ids": ["h1"]}]})
    assert r.status_code == 200
    assert [m["slot_dir"] for m in c.get("/api/workshop/WS-AI3/roster").json()["members"]]

# ── 三档激活判据（老板 2026-10-30 SOP；2026-10-31 修 B 档）────────────
def _one_slot(tmp_path, monkeypatch, wid="WS-TIER"):
    monkeypatch.setenv("AC_DATA_DIR", str(tmp_path / "data"))
    from agent_community.platform import v5_roster as roster

    monkeypatch.setattr(roster, "_path", lambda: tmp_path / "data" / "v5_roster.json",
                        raising=True)
    roster.add_slots(wid, [{"岗位": "前端组/员工01", "harness": "h1", "model": "m",
                            "人数": 1}], source="C")
    seq = roster.roster(wid)["members"][0]["member_seq"]
    return roster, wid, seq


def test_b_tier_needs_mailbox_takeout(tmp_path, monkeypatch):
    """**B 档只通没取件 ⇒ 最高 acked**（不许 active）——这是老板 SOP 的原话。"""
    roster, wid, seq = _one_slot(tmp_path, monkeypatch)
    out = roster.set_evidence(wid, seq, {"tier": "b_bridge", "bridge_ok": True,
                                         "verified_scope": "桥通"}, who="user")
    st = roster.roster(wid)["members"][0]["state"]
    assert st == "acked", "B 档只通就判成 %s（应为 acked，老板说过只通不算）" % st
    assert "mailbox_ok" in (out.get("need") or {}).get("missing_evidence", []), \
        "要说清还缺「真取走一次文件」"
    # 补上取件 ⇒ active
    roster.set_evidence(wid, seq, {"mailbox_ok": True}, who="user")
    assert roster.roster(wid)["members"][0]["state"] == "active"


def test_c_tier_needs_all_three(tmp_path, monkeypatch):
    """C 档三项缺一 ⇒ 不到 active，并说明缺哪项；补齐 ⇒ active。"""
    roster, wid, seq = _one_slot(tmp_path, monkeypatch, "WS-TIER-C")
    out = roster.set_evidence(wid, seq, {"tier": "c_visual", "paste_seen": True,
                                         "press_ok": True, "verified_scope": "粘贴+回车"},
                              who="user")
    assert roster.roster(wid)["members"][0]["state"] != "active"
    assert "rename_seen" in (out.get("need") or {}).get("missing_evidence", [])
    roster.set_evidence(wid, seq, {"rename_seen": True}, who="user")
    assert roster.roster(wid)["members"][0]["state"] == "active"


def test_a_tier_protocol_hit_is_enough(tmp_path, monkeypatch):
    """A 档：真收到它的请求（protocol_ok）+ 写清覆盖范围 ⇒ active。"""
    roster, wid, seq = _one_slot(tmp_path, monkeypatch, "WS-TIER-A")
    roster.set_evidence(wid, seq, {"tier": "a_protocol", "protocol_ok": True,
                                   "verified_scope": "它真打了 /api/status"}, who="user")
    assert roster.roster(wid)["members"][0]["state"] == "active"


def test_verified_scope_is_required(tmp_path, monkeypatch):
    """证据齐但没写"覆盖到哪一段" ⇒ 不许 active（防"看起来接上了"）。"""
    roster, wid, seq = _one_slot(tmp_path, monkeypatch, "WS-TIER-S")
    out = roster.set_evidence(wid, seq, {"tier": "a_protocol", "protocol_ok": True}, who="user")
    assert roster.roster(wid)["members"][0]["state"] != "active"
    assert "verified_scope" in (out.get("need") or {})
