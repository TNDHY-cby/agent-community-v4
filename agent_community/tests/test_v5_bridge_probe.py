# -*- coding: utf-8 -*-
"""守卫：**B 档平台自验**（桥真取件一次，`V116` §6.2，2026-10-31 老板拍板做）。

老板选的 A 方案：「B 档（桥型）也做成平台自验，别只嘴上说」。

做法（复用 `bridge_selfcheck`，不发明协议）：
  ① 平台往该工位邮箱写一条 `task_<probe_id>.json`（**平台做的**）；
  ② 平台看 `delivered/`（桥取走了）与 `replies/`（它真回话了）（**平台看的**）；
  ③ 取件成立 ⇒ `bridge_ok` 记 `platform-observed`；真回话 ⇒ 再加 `mailbox_ok`。

全部真跑：真目录、真文件、真 HTTP（TestClient）。**"桥"由测试自己扮演**（把文件移进 delivered/，
这就是真实桥做的事），所以"取件"这一步是真文件事实，不是 mock 出来的布尔值。
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.append(str(_ROOT))

from agent_community.platform import v5_roster as roster  # noqa: E402

SLOT = "前端组"


def _mk_inbox(wp: Path) -> Path:
    """造一个像样的邮箱（与真实桥约定一致：inbox/ + inbox/delivered/ + inbox/replies/）。"""
    inbox = wp / "前端组_成员01" / "inbox"
    (inbox / "delivered").mkdir(parents=True)
    (inbox / "replies").mkdir(parents=True)
    return inbox


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("AC_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("AC_MIND_ROOT", str(tmp_path / "minds"))
    monkeypatch.setattr(roster, "_path", lambda: tmp_path / "data" / "v5_roster.json",
                        raising=True)
    wp = tmp_path / "ws"
    (wp / "_lead").mkdir(parents=True)
    (wp / SLOT).mkdir()
    (wp / SLOT / "分工.md").write_text("- 版本: 1\n", encoding="utf-8")
    roster.add_slots("WS-BP", [{"岗位": SLOT, "harness": "h_bridge", "model": "m", "人数": 1}],
                     source="C")
    return {"tmp": tmp_path, "ws": wp, "inbox": _mk_inbox(wp)}


@pytest.fixture()
def client(env):
    from fastapi.testclient import TestClient

    from agent_community.platform import server as srv

    ws = srv.Workshop(workshop_id="WS-BP", name="桥自验间", workspace_dir=str(env["ws"]),
                      hall_content="方向", status="running")
    srv.workshops["WS-BP"] = ws
    return TestClient(srv.app, client=("127.0.0.1", 54321))


def _probe(client, *, inbox: Path | None = None, probe_id: str = "", wait: float = 0):
    body = {}
    if inbox is not None:
        body["inbox_dir"] = str(inbox)
    if probe_id:
        body["probe_id"] = probe_id
    if wait:
        body["wait"] = wait
    return client.post("/api/workshop/WS-BP/members/1/bridge-probe", json=body)


# ── ① 没有邮箱 ⇒ 说清缺什么，不许假装 ────────────────────────────
def test_missing_inbox_is_reported(client, env):
    r = _probe(client, inbox=env["ws"] / "并不存在" / "inbox")
    assert r.status_code == 400
    b = r.json()
    assert b["code"] == "INBOX_MISSING"
    assert "insbox" in b["inbox"] or "inbox" in b["inbox"]
    assert b["next"], "要告诉用户先去架桥"
    assert roster.find_member("WS-BP", 1)["evidence"] == {}, "没邮箱时不许记任何证据"


# ── ② 平台真写了测试消息；桥没取 ⇒ 拒，且**一个字节都不许记进证据** ──
def test_probe_written_but_not_picked_records_nothing(client, env):
    r = _probe(client, inbox=env["inbox"])
    assert r.status_code == 409, r.text
    b = r.json()
    assert b["code"] == "PROBE_NOT_PICKED"
    assert b["probe"]["still_in_inbox"], "那条消息应当还躺在邮箱里（平台真写了）"
    assert b["probe_id"], "失败也要给出 probe_id，用户才能等会儿再看同一条"
    m = roster.find_member("WS-BP", 1)
    assert m["evidence"] == {}, "没被取走就不许写 bridge_ok：%s" % m.get("evidence")
    assert m["state"] == "pending"


# ── ③ 桥真取走（文件进 delivered/）⇒ bridge_ok + platform-observed ──
def test_picked_up_file_becomes_platform_observed_bridge_ok(client, env):
    first = _probe(client, inbox=env["inbox"]).json()
    pid = first["probe_id"]
    name = first["probe"]["still_in_inbox"][0]
    # 「桥」做的事：把 task_*.json 从 inbox 移到 delivered/（文件名不变）
    shutil.move(str(env["inbox"] / name), str(env["inbox"] / "delivered" / name))

    r = _probe(client, inbox=env["inbox"], probe_id=pid)
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["state"] == "acked" or b["state"] == "active", b
    m = roster.find_member("WS-BP", 1)
    assert m["evidence"]["bridge_ok"] is True
    assert m["evidence"]["_sources"]["bridge_ok"] == "platform-observed", m["evidence"]
    assert m["evidence"]["_probe"]["probe_id"] == pid
    assert m["tier"] == "b_bridge"
    assert "平台观测" in m["verified_scope"]
    # mailbox_ok 还没成立（只是取走，没回话）⇒ 不许凭空出现
    assert not m["evidence"].get("mailbox_ok"), "只取件没回话，不许记成 mailbox_ok"


# ── ④ 既取走又回话 ⇒ mailbox_ok 也成立 ⇒ 到 active ──────────────
def test_picked_up_and_replied_reaches_active(client, env):
    first = _probe(client, inbox=env["inbox"]).json()
    pid = first["probe_id"]
    name = first["probe"]["still_in_inbox"][0]
    shutil.move(str(env["inbox"] / name), str(env["inbox"] / "delivered" / name))
    # 「agent」做的事：在 replies/ 里回一句
    (env["inbox"] / "replies" / ("reply_%s.json" % pid)).write_text(
        '{"type":"self_check_ack","ok":true}', encoding="utf-8")

    r = _probe(client, inbox=env["inbox"], probe_id=pid)
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["state"] == "active", b
    m = roster.find_member("WS-BP", 1)
    assert m["evidence"]["bridge_ok"] is True and m["evidence"]["mailbox_ok"] is True
    assert m["evidence"]["_sources"]["mailbox_ok"] == "platform-observed"
    assert b["observed"]["how"] == "bridge_probe"


# ── ⑤ 人勾不能冒充平台观测（这是老板最在意的那条）────────────────
def test_user_check_cannot_impersonate_platform_observation(env):
    roster.set_evidence("WS-BP", 1, {"bridge_ok": True, "mailbox_ok": True,
                                     "verified_scope": "我自己勾的"})
    ev = roster.find_member("WS-BP", 1)["evidence"]
    assert ev["_sources"]["bridge_ok"] == "user-check", ev
    assert ev["_sources"]["mailbox_ok"] == "user-check"
    # 人勾能到 active（那是原有规则），但账上**明确写着是人勾的**
    assert roster.find_member("WS-BP", 1)["state"] == "active"
    eff = roster.effective_members("WS-BP")
    row = next(x for x in eff["members"] if x["member_seq"] == 1)
    assert row["evidence_sources"]["bridge_ok"] == "user-check"


# ── ⑥ 界面能一眼看出"谁验的" ────────────────────────────────────
def test_ui_sees_the_source_per_item(client, env):
    first = _probe(client, inbox=env["inbox"]).json()
    pid, name = first["probe_id"], first["probe"]["still_in_inbox"][0]
    shutil.move(str(env["inbox"] / name), str(env["inbox"] / "delivered" / name))
    _probe(client, inbox=env["inbox"], probe_id=pid)
    row = next(x for x in roster.effective_members("WS-BP")["members"] if x["member_seq"] == 1)
    assert row["evidence_sources"]["bridge_ok"] == "platform-observed"
    assert row["evidence_at"].get("bridge_ok_at"), row["evidence_at"]


# ── ⑦ 默认推导的邮箱路径与 compartment 的约定一致，且如实标「这是猜的」──
def test_guessed_inbox_matches_compartment_convention_and_is_labelled(client, env):
    """不给 `inbox_dir` 时按工位名推导：必须命中 `compartment.mailbox_dir` 的真实约定
    （`<ws>/<岗位>_<成员>/inbox`），同时**如实告诉用户这是推的**（同岗位多人会共用邮箱）。"""
    r = _probe(client)          # 不给 inbox_dir
    b = r.json()
    assert b["probe"]["inbox_guessed"] is True, b.get("probe")
    assert b.get("inbox_guessed_note"), "要说明这是按工位名推的、多人会共用"
    # 推导命中真实约定 ⇒ 平台真把那封测试消息写进去了（不是"找不到目录"）
    assert b["code"] == "PROBE_NOT_PICKED", b
    assert b["probe"]["still_in_inbox"], b["probe"]
    assert roster.find_member("WS-BP", 1)["evidence"] == {}, "没取走就不许记账"
