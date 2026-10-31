# -*- coding: utf-8 -*-
"""守卫：**名单与实到对不上**（`V116` §8.0，老板 2026-10-31 拍板的两条口径）。

老板的问题（原话）：「名单上写了五个员工，实际只拉进来三个；或者拉进来的人换了名字 ——
平台是发现差一个就拦住不让往下走，还是先接着干、以后再补？」

拍板结果：
  · **人数不齐 ⇒ 先接着干**，只在给"空工位"派活时拦（`GATE_G5`），并**必须给一句人话**
    （`roster_summary`：名单 X 人 / 已接上 Y / 还差 Z（哪几个空工位））；
  · **名字对不上 ⇒ 平台核不出来**，所以接人时可选填 `actual_name`，填了就比、不一致**只提醒不拦**。

全部真跑：真名单落库、真证据推进、真 HTTP（TestClient）；`roster_summary` 另有脱机单测。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.append(str(_ROOT))

from agent_community.platform import task_ledger as ledger  # noqa: E402
from agent_community.platform import v5_roster as roster  # noqa: E402

SLOT = "前端组"
C_EVIDENCE = {"paste_seen": True, "press_ok": True, "rename_seen": True}


def _mk_ws(tmp: Path) -> Path:
    wp = tmp / "ws"
    (wp / "_lead").mkdir(parents=True)
    (wp / "_lead" / "建模.md").write_text("# 建模\n", encoding="utf-8")
    (wp / SLOT).mkdir()
    (wp / SLOT / "分工.md").write_text("- 版本: 1\n", encoding="utf-8")
    (wp / SLOT / "_notice.md").write_text("", encoding="utf-8")
    (wp / SLOT / "任务").mkdir()
    (wp / SLOT / "产物").mkdir()
    return wp


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("AC_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("AC_MIND_ROOT", str(tmp_path / "minds"))
    monkeypatch.setattr(roster, "_path", lambda: tmp_path / "data" / "v5_roster.json",
                        raising=True)
    monkeypatch.setattr(ledger, "_path", lambda: tmp_path / "data" / "task_ledger.json",
                        raising=True)
    wp = _mk_ws(tmp_path)
    roster.add_slots("WS-GAP", [{"岗位": SLOT, "harness": "h_x", "model": "m", "人数": 5}],
                     source="C")
    return {"tmp": tmp_path, "ws": wp}


@pytest.fixture()
def client(env):
    from fastapi.testclient import TestClient

    from agent_community.platform import server as srv

    ws = srv.Workshop(workshop_id="WS-GAP", name="缺人演示间", workspace_dir=str(env["ws"]),
                      hall_content="方向", status="running")
    srv.workshops["WS-GAP"] = ws
    return TestClient(srv.app, client=("127.0.0.1", 54321))


def _join(seq: int, **extra):
    patch = dict(C_EVIDENCE)
    patch["verified_scope"] = "已接上（夹具）"
    patch.update(extra)
    return roster.set_evidence("WS-GAP", seq, patch)


# ── ① 名单写「人数: 5」⇒ 真建 5 个工位 ────────────────────────────
def test_headcount_creates_real_slots_not_just_a_number(env):
    members = roster.roster("WS-GAP")["members"]
    assert len(members) == 5, [m.get("member_seq") for m in members]
    assert all(m["state"] == "pending" for m in members)
    slots = roster.roster("WS-GAP")["slots"]
    assert slots[0]["headcount"] == 5 and slots[0]["state"] == "filled"


# ── ② 只接上 3 个 ⇒ 一句话说清「还差 2 个（哪两个）」──────────────
def test_summary_says_how_many_are_missing_and_which_ones(env):
    for seq in (1, 2, 3):
        _join(seq)
    s = roster.roster_summary("WS-GAP")
    assert (s["total"], s["active"], s["missing"]) == (5, 3, 2), s
    assert "名单 5 人" in s["sentence"] and "已接上 3 人" in s["sentence"]
    assert "还差 2 个" in s["sentence"], s["sentence"]
    assert "前端组#4" in s["sentence"] and "前端组#5" in s["sentence"], s["sentence"]
    assert "空工位不能派活" in s["sentence"]
    assert s["missing_labels"] == ["前端组#4", "前端组#5"]
    assert s["all_active"] is False and s["can_dispatch_anyone"] is True


def test_summary_all_joined_and_none_joined_and_empty(env):
    assert "还没接上" in roster.roster_summary("WS-GAP")["sentence"]
    for seq in range(1, 6):
        _join(seq)
    s = roster.roster_summary("WS-GAP")
    assert s["all_active"] is True and s["missing"] == 0
    assert s["sentence"] == "名单 5 人，全部接上了"
    assert "名单上还没有人" in roster.roster_summary("WS-空")["sentence"]


# ── ③ 少人照样能开工（这是刻意的，不许改成"全员到齐"）─────────────
def test_partial_roster_still_allows_start(client, env):
    for seq in (1, 2, 3):
        _join(seq)
    g = client.get("/api/workshop/WS-GAP/gate/start_running").json()
    assert g["allowed"] is True, g
    assert roster.roster_summary("WS-GAP")["missing"] == 2, "放行 ≠ 假装全员到齐"


# ── ④ 「还差几个」在**两个端点上是同一份**（规则 20）──────────────
def test_summary_is_identical_on_both_endpoints(client, env):
    for seq in (1, 2, 3):
        _join(seq)
    a = client.get("/api/workshop/WS-GAP/roster").json()["roster_summary"]
    b = client.get("/api/workshop/WS-GAP").json()["roster_summary"]
    assert a == b, "同一件事两处口径不一致：\n%s\n%s" % (a, b)
    assert a["missing"] == 2 and "还差 2 个" in a["sentence"]


# ── ⑤ 给「没接上」的工位派活 ⇒ 拦，且四要素齐全 ──────────────────
def test_dispatch_to_unjoined_slot_is_blocked_with_four_elements(client, env):
    for seq in (1, 2, 3):
        _join(seq)
    r = client.post("/api/workshop/WS-GAP/dispatch",
                    json={"member_seq": 4, "title": "给空工位派活", "goal": "试试"})
    assert r.status_code == 409, r.text
    b = r.json()
    assert b["code"] == "GATE_G5"
    four = b.get("four") or {}
    assert set(four) == {"who", "what", "where", "how"}, four
    assert "4" in str(four["who"]) and "pending" in str(four["what"]), four


# ── ⑥ 名字：填了就对，不一致只提醒不拦 ───────────────────────────
def test_actual_name_matching_identity_is_recorded_as_match(env):
    expected = roster.find_member("WS-GAP", 1)["session_label"]
    out = _join(1, actual_name=expected)
    assert out["name_check"]["match"] is True, out
    assert out["name_check"]["expected"] == expected
    assert "name_check_warn" not in out


def test_actual_name_mismatch_warns_but_does_not_block(env):
    out = _join(2, actual_name="它现在叫 小助手")
    assert out["name_check"]["match"] is False
    assert "名单上叫" in out["name_check_warn"] and "小助手" in out["name_check_warn"]
    # ★关键：名字不一致**不许影响接上与派活**（老板选 A：填了就比，但只提醒）
    assert out["state"] == "active", out
    assert roster.find_member("WS-GAP", 2)["actual_name"] == "它现在叫 小助手"


def test_without_actual_name_we_do_not_pretend_to_have_checked(env):
    out = _join(3)
    assert "name_check" not in out, "没填就别伪造核对结果"
    m = roster.find_member("WS-GAP", 3)
    assert not m.get("name_check")
    # 但界面上要能看出"没核过"：effective_members 里 name_check 为空 dict
    eff = roster.effective_members("WS-GAP")
    row = next(x for x in eff["members"] if x["member_seq"] == 3)
    assert row["name_check"] == {} and row["actual_name"] == ""


def test_effective_members_carries_actual_name_for_ui(env):
    _join(4, actual_name="它现在叫 小A")
    eff = roster.effective_members("WS-GAP")
    row = next(x for x in eff["members"] if x["member_seq"] == 4)
    assert row["actual_name"] == "它现在叫 小A"
    assert row["name_check"]["match"] is False
