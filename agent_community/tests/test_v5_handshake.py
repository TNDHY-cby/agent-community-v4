# -*- coding: utf-8 -*-
"""守卫：**A 档「平台自验」**（一次性回话口令，`V116` §6b，2026-10-31）。

老板追问原话：「A档改成平台自己观测，会不会有人随便发一次请求就算『接上了』？这一块你怎么卡的？」

这一组判据就是"怎么卡"的答案，每条都是**真的能拦住的东西**：
  1. 没发过口令 ⇒ 拒（拿不到"这张单子上的口令"就别想记成接上）
  2. 口令错 ⇒ 拒（随便编一个不算）
  3. 口令对但**没有该工位凭据** ⇒ 401（拿别人的单子刷不动）
  4. 口令对但**凭据是别的工位/别的工作间** ⇒ 403 `TOKEN_SCOPE`
  5. **重放**同一个口令 ⇒ 拒（一次性，用过的不能再用）
  6. 只有人勾、没有平台观测 ⇒ 账上标 `user-check`，**不许**冒充 `platform-observed`
  7. 平台观测成功 ⇒ 记 `platform-observed` + **时间戳**（"账上自己长出来的"就是这个）

全部真跑：真名册落库、真 HTTP（TestClient）、真口令比对。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.append(str(_ROOT))

from agent_community.platform import intake_token as tok  # noqa: E402
from agent_community.platform import task_ledger as ledger  # noqa: E402
from agent_community.platform import v5_roster as roster  # noqa: E402

SLOT = "前端组"
SLOT2 = "后端组"


def _mk_ws(tmp: Path) -> Path:
    wp = tmp / "ws"
    (wp / "_lead").mkdir(parents=True)
    (wp / "_lead" / "建模.md").write_text("# 建模\n", encoding="utf-8")
    for s in (SLOT, SLOT2):
        (wp / s).mkdir()
        (wp / s / "分工.md").write_text("- 版本: 1\n", encoding="utf-8")
        (wp / s / "_notice.md").write_text("", encoding="utf-8")
        (wp / s / "任务").mkdir()
        (wp / s / "产物").mkdir()
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
    roster.add_slots("WS-H", [{"岗位": SLOT, "harness": "h_x", "model": "m", "人数": 1},
                              {"岗位": SLOT2, "harness": "h_x", "model": "m", "人数": 1}],
                     source="C")
    return {"tmp": tmp_path, "ws": wp}


@pytest.fixture()
def client(env):
    from fastapi.testclient import TestClient

    from agent_community.platform import server as srv

    ws = srv.Workshop(workshop_id="WS-H", name="握手演示间", workspace_dir=str(env["ws"]),
                      hall_content="方向", status="running")
    srv.workshops["WS-H"] = ws
    return TestClient(srv.app, client=("127.0.0.1", 54321))


def _member(wid: str, seq: int) -> dict:
    return roster.find_member(wid, seq)


def _handshake(client, seq: int, code: str, token: str, wid: str = "WS-H"):
    h = {"Authorization": "Bearer " + token} if token else {}
    return client.post("/api/workshop/%s/members/%d/handshake" % (wid, seq),
                       json={"code": code}, headers=h)


# ── ① 没发过口令 ⇒ 拒 ────────────────────────────────────────────
def test_no_handshake_issued_is_rejected(env):
    out = roster.verify_handshake("WS-H", 1, "AAAA-BBBB")
    assert out["code"] == "HANDSHAKE_NOT_ISSUED", out
    assert "重拼" in out["next"] or "对接单" in out["next"]


# ── ② 口令错 ⇒ 拒；③ 没凭据 ⇒ 401 ───────────────────────────────
def test_wrong_code_is_rejected_and_right_code_needs_token(client, env):
    hs = roster.issue_handshake("WS-H", 1)
    code = hs["handshake"]
    assert len(code) == 9 and code[4] == "-", code

    bad = _handshake(client, 1, "ZZZZ-9999", tok.issue("WS-H", SLOT)["token"])
    assert bad.status_code == 400 and bad.json()["code"] == "HANDSHAKE_MISMATCH", bad.text

    no_token = _handshake(client, 1, code, "")
    assert no_token.status_code == 401 and no_token.json()["code"] == "TOKEN_MISSING", no_token.text

    # 到这一步口令还没被消耗（失败不消耗），正确的应当能过
    ok = _handshake(client, 1, code, tok.issue("WS-H", SLOT)["token"])
    assert ok.status_code == 200, ok.text
    assert ok.json()["observed"]["by"] == "platform"


# ── ④ 凭据是别的工位 ⇒ 403 TOKEN_SCOPE ───────────────────────────
def test_token_from_another_slot_cannot_confirm(client, env):
    code = roster.issue_handshake("WS-H", 1)["handshake"]
    other = tok.issue("WS-H", SLOT2)["token"]           # 后端组的凭据，去认前端组的工位
    r = _handshake(client, 1, code, other)
    assert r.status_code == 403 and r.json()["code"] == "TOKEN_SCOPE", r.text
    # 也别想拿它把状态推进
    assert _member("WS-H", 1)["state"] == "pending"


# ── ⑤ 重放 ⇒ 拒（一次性）────────────────────────────────────────
def test_replay_of_same_code_is_rejected(client, env):
    code = roster.issue_handshake("WS-H", 1)["handshake"]
    token = tok.issue("WS-H", SLOT)["token"]
    first = _handshake(client, 1, code, token)
    assert first.status_code == 200, first.text
    again = _handshake(client, 1, code, token)
    assert again.status_code == 400 and again.json()["code"] == "HANDSHAKE_ALREADY_USED", again.text


# ── ⑥ 人勾 vs 平台观测：账上必须分开 ────────────────────────────
def test_user_check_is_labelled_differently_from_platform_observation(client, env):
    # 纯人勾（C 档三项）
    roster.set_evidence("WS-H", 2, {"paste_seen": True, "press_ok": True, "rename_seen": True,
                                    "verified_scope": "人眼看过"})
    srcs = _member("WS-H", 2)["evidence"]["_sources"]
    assert srcs["paste_seen"] == "user-check", srcs
    assert "protocol_ok" not in srcs, "没做过平台观测，不许凭空出现 platform-observed"

    # 平台观测（口令）
    code = roster.issue_handshake("WS-H", 1)["handshake"]
    _handshake(client, 1, code, tok.issue("WS-H", SLOT)["token"])
    m1 = _member("WS-H", 1)
    assert m1["evidence"]["_sources"]["protocol_ok"] == "platform-observed", m1["evidence"]
    assert m1["evidence"]["protocol_ok_at"], "平台观测必须带时间戳"


# ── ⑦ 平台观测后：状态到 active、界面能看出"谁验的" ──────────────
def test_platform_observation_reaches_active_and_is_visible_to_ui(client, env):
    code = roster.issue_handshake("WS-H", 1)["handshake"]
    r = _handshake(client, 1, code, tok.issue("WS-H", SLOT)["token"]).json()
    assert r["state"] == "active", r
    assert r["observed"]["how"] == "handshake"
    eff = roster.effective_members("WS-H")
    row = next(x for x in eff["members"] if x["member_seq"] == 1)
    assert row["evidence_sources"]["protocol_ok"] == "platform-observed"
    assert row["evidence_at"].get("protocol_ok_at"), row["evidence_at"]


# ── ⑧ 重发口令 ⇒ 旧口令立刻失效 ─────────────────────────────────
def test_reissuing_handshake_invalidates_the_old_one(client, env):
    old = roster.issue_handshake("WS-H", 1)["handshake"]
    new = roster.issue_handshake("WS-H", 1)["handshake"]
    assert old != new
    token = tok.issue("WS-H", SLOT)["token"]
    stale = _handshake(client, 1, old, token)
    assert stale.status_code == 400 and stale.json()["code"] == "HANDSHAKE_MISMATCH", stale.text
    assert _handshake(client, 1, new, token).status_code == 200


# ── ⑨ 口令写进了对接单（一处真相：单子上有它才答得出）───────────
def test_member_kit_carries_the_handshake(client, env):
    roster.set_evidence("WS-H", 1, {"tier": "a_protocol"})       # 让它走 A 档
    j = client.post("/api/workshop/WS-H/members/1/kit", json={}).json()
    assert j["has_token"] is True and j["handshake_issued"] is True, j
    assert "## 6b. 回话口令" in j["kit"]
    assert j["handshake_endpoint"].endswith("/members/1/handshake")
    assert "一次性" in j["kit"]
    # 单子里那段口令必须真能用（拼装与校验是同一份真相）
    import re

    code = re.search(r"口令：`([A-Z0-9]{4}-[A-Z0-9]{4})`", j["kit"]).group(1)
    ok = _handshake(client, 1, code, tok.issue("WS-H", SLOT)["token"])
    assert ok.status_code == 200, ok.text
