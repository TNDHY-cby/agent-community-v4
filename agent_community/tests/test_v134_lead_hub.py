# -*- coding: utf-8 -*-
"""守卫（`V134`，老板 2026-11-07）：**组长枢纽** —— 能力面 / 验收署名 / 产物绑任务 / 未读 / 对接单投递。

判据都是"真结果"：真账本、真令牌、真扫盘；不查源码文本。

  ① 能力面：A2A / 视觉 / 什么都没报 三种接入给**不同**的 `can_*` 与 `gaps`，且每条"不能"都有原因；
  ② 验收署名：不填默认 `user`（不再默认 `lead`）；声称 lead 无凭据 ⇒ 403；
     组长令牌 ⇒ 记 `reviewed_by=_lead` + `reviewed_by_source=token`；成员令牌 ⇒ 403 NOT_LEAD；
  ③ 产物绑任务：扫盘发现文件 ⇒ 事件带 `task_id` / `needs_review`，任务从 `issued` 推到 `delivered`；
     同一工位两条未完成任务 ⇒ `task_id_ambiguous=true`（不猜）；
  ④ 未读：事件 `unread` 计数、`mark_read` 推进、只前进不后退；
  ⑤ 对接单投递：组长有视觉脚本 ⇒ 真投（`delivered`）；没有可用通道 ⇒ `sent=false` + `NO_TEXT_CHANNEL`。
"""
from __future__ import annotations

import sys
import types
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.append(str(_ROOT))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from agent_community.platform import artifact_inbox as inbox  # noqa: E402
from agent_community.platform import intake_token as tok  # noqa: E402
from agent_community.platform import lead_capabilities as caps  # noqa: E402
from agent_community.platform import server as srv  # noqa: E402
from agent_community.platform import task_ledger as ledger  # noqa: E402
from agent_community.platform import v5_roster as roster  # noqa: E402
from agent_community.platform.protocol import HarnessInfo  # noqa: E402

C = TestClient(srv.app, client=("127.0.0.1", 54321))

WS = "ws_v134"


def _info(**kw) -> HarnessInfo:
    base = dict(harness_id="示例Harness-A", harness_name="示例Harness-A",
                ai={"model_name": "示例模型"})
    base.update(kw)
    return HarnessInfo(**base)


@pytest.fixture(autouse=True)
def _ws_and_roster(tmp_path, monkeypatch):
    """真工作间目录 + 可控名单（`_lead` 与一名成员）。"""
    root = tmp_path / "ws"
    (root / "_lead").mkdir(parents=True, exist_ok=True)
    (root / "前端组/员工01/产物").mkdir(parents=True, exist_ok=True)
    srv.workshops[WS] = types.SimpleNamespace(workshop_id=WS, workspace_dir=str(root), status="active")
    ctl = {"members": [
        {"member_seq": 1, "slot_dir": "前端组/员工01", "harness": "示例Harness-A", "state": "active"},
        {"member_seq": 2, "slot_dir": "_lead", "harness": "示例Harness-Lead", "state": "active"},
    ]}
    monkeypatch.setattr(roster, "effective_members",
                        lambda wid: {"source": "v5", "total": len(ctl["members"]),
                                     "active": len(ctl["members"]), "members": ctl["members"]})
    infos = {"示例Harness-A": _info(), "示例Harness-Lead": _info(harness_id="示例Harness-Lead")}
    monkeypatch.setattr(srv, "harness_manager",
                        types.SimpleNamespace(sessions={
                            k: types.SimpleNamespace(info=v) for k, v in infos.items()}),
                        raising=False)
    yield {"root": root, "ctl": ctl, "infos": infos}
    srv.workshops.pop(WS, None)


# ── ① 能力面 ────────────────────────────────────────────────────────
def test_01_capabilities_differ_and_every_gap_has_reason():
    a2a = caps.capabilities_of(_info(wakeup_method="a2a",
                                     transports=["a2a"], api_base_url="http://127.0.0.1:9/a2a/x"),)
    visual = caps.capabilities_of(_info(wakeup_method="clipboard",
                                        activation_script={"text": "点一下", "title": "示例窗口"}))
    blind = caps.capabilities_of(_info(wakeup_method="clipboard"))
    assert a2a["can_dispatch"] is True and a2a["dispatch_via"] == "a2a"
    assert visual["can_be_visual"] is True and visual["can_receive_notice"] == "visual"
    assert blind["can_be_visual"] is False and blind["can_receive_notice"] == "none"
    for c in (a2a, visual, blind):
        for g in c["gaps"]:
            assert g.get("what") and g.get("platform_will"), g


# ── ② 验收署名 ──────────────────────────────────────────────────────
def _issue_task(seq: int, slot: str = "_lead") -> None:
    ledger.issue(WS, seq, slot_dir=slot, file="%s/任务/T-%03d.md" % (slot, seq),
                 member_seq=2, title="示例任务 %d" % seq)


def test_02_review_defaults_to_user_not_lead():
    _issue_task(1)
    r = C.post("/api/workshop/%s/tasks/1/review" % WS, json={"result": "通过", "evidence_level": "L4"})
    assert r.status_code in (200, 400), r.text
    rec = ledger.get(WS, 1) or {}
    assert rec.get("reviewed_by") == "user", rec.get("reviewed_by")
    assert rec.get("reviewed_by_source") == "user"


def test_03_claiming_lead_without_token_is_rejected():
    _issue_task(2)
    r = C.post("/api/workshop/%s/tasks/2/review" % WS,
               json={"result": "通过", "evidence_level": "L4", "who": "lead"})
    assert r.status_code == 403, r.text
    assert r.json().get("code") == "WHO_NOT_PROVEN"
    assert (ledger.get(WS, 2) or {}).get("state") == "issued", "被拒的验收不许落账"


def test_04_lead_token_review_is_recorded_with_source():
    _issue_task(3)
    tk = tok.issue(WS, "_lead")["token"]
    r = C.post("/api/workshop/%s/tasks/3/review" % WS,
               json={"result": "通过", "evidence_level": "L4", "who_source": "token"},
               headers={"Authorization": "Bearer %s" % tk})
    assert r.status_code == 200, r.text
    rec = ledger.get(WS, 3) or {}
    assert rec.get("reviewed_by") == "_lead", rec.get("reviewed_by")
    assert rec.get("reviewed_by_source") == "token"


def test_05_member_token_cannot_review_as_lead():
    _issue_task(4)
    tk = tok.issue(WS, "前端组/员工01")["token"]
    r = C.post("/api/workshop/%s/tasks/4/review" % WS,
               json={"result": "通过", "evidence_level": "L4", "who_source": "token"},
               headers={"Authorization": "Bearer %s" % tk})
    assert r.status_code == 403 and r.json().get("code") == "NOT_LEAD", r.text


# ── ③ 产物绑任务 + delivered ────────────────────────────────────────
def test_06_inbox_event_binds_task_and_advances_to_delivered(_ws_and_roster):
    _issue_task(5, slot="前端组/员工01")
    inbox.scan_workshop(WS)                                  # 基线
    f = Path(_ws_and_roster["root"]) / "前端组/员工01/产物/交付.md"
    f.write_text("产物正文", encoding="utf-8")
    out = inbox.scan_workshop(WS)
    ev = (out.get("new") or [{}])[0]
    assert ev.get("task_id") == "T-005", ev
    assert ev.get("needs_review") is True
    rec = ledger.get(WS, 5) or {}
    assert rec.get("state") == "delivered", rec.get("state")
    assert rec.get("delivered_by") == "platform"


def test_07_two_open_tasks_is_ambiguous_not_guessed(_ws_and_roster):
    _issue_task(6, slot="前端组/员工01")
    _issue_task(7, slot="前端组/员工01")
    inbox.scan_workshop(WS)
    f = Path(_ws_and_roster["root"]) / "前端组/员工01/产物/交付2.md"
    f.write_text("x", encoding="utf-8")
    out = inbox.scan_workshop(WS)
    ev = (out.get("new") or [{}])[0]
    assert ev.get("task_id_ambiguous") is True and not ev.get("task_id"), ev
    assert (ledger.get(WS, 6) or {}).get("state") == "issued", "猜不准就不许推进状态"


# ── ④ 未读 ──────────────────────────────────────────────────────────
def test_08_unread_and_mark_read_only_forward(_ws_and_roster):
    inbox.scan_workshop(WS)
    f = Path(_ws_and_roster["root"]) / "前端组/员工01/产物/未读.md"
    f.write_text("y", encoding="utf-8")
    out = inbox.scan_workshop(WS)
    u = out.get("unread") or {}
    assert u.get("unread", 0) >= 1, u
    r = inbox.mark_read(WS)
    assert (r.get("unread") or {}).get("unread") == 0
    back = inbox.mark_read(WS, 0)
    assert back.get("moved") is False, "只前进不后退"


# ── ⑤ 对接单投递 ────────────────────────────────────────────────────
def test_09_lead_kit_send_delivers_when_lead_has_visual_script(_ws_and_roster, monkeypatch):
    from agent_community.platform import visual_send as vs

    _ws_and_roster["infos"]["示例Harness-Lead"].activation_script = {
        "text": "点输入框粘一句", "title": "示例组长窗口"}
    got = {}

    def _fake(info, text, **kw):
        got["title"] = (info.activation_script or {}).get("title")
        got["text"] = text
        return {"ok": True, "delivered": True, "verified": True, "channel": "native",
                "target": "示例组长窗口", "detail": "", "note": ""}

    monkeypatch.setattr(vs, "send_text", _fake)
    r = C.post("/api/workshop/%s/lead-kit/send" % WS, json={})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("sent") is True and "visual" in (body.get("channels") or []), body
    assert got.get("title") == "示例组长窗口"
    assert "投递成功 ≠" in str(body.get("note")), body.get("note")


def test_10_lead_kit_send_is_honest_when_no_channel(_ws_and_roster):
    _ws_and_roster["infos"]["示例Harness-Lead"].activation_script = {}
    _ws_and_roster["infos"]["示例Harness-Lead"].tools = []
    r = C.post("/api/workshop/%s/lead-kit/send" % WS, json={})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("sent") is False and body.get("code") == "NO_TEXT_CHANNEL", body
    assert body.get("kit"), "送达不了也要把正文给出来（人工复制）"


# ── ★V135：组长要由"协议高适配型"承担（单主多助） ──────────────────
def test_11_protocol_fit_has_three_levels():
    high = caps.capabilities_of(_info(wakeup_method="a2a", transports=["a2a"],
                                      api_base_url="http://127.0.0.1:9/a2a/x",
                                      tools=[{"name": "read_file"}, {"name": "write_file"}]))
    medium = caps.capabilities_of(_info(wakeup_method="a2a", transports=["a2a"],
                                        api_base_url="http://127.0.0.1:9/a2a/x",
                                        tools=[{"name": "write_file"}]))
    low = caps.capabilities_of(_info(wakeup_method="clipboard", tools=[]))
    assert high["protocol_fit"]["level"] == "high", high["protocol_fit"]
    assert medium["protocol_fit"]["level"] == "medium", medium["protocol_fit"]
    assert low["protocol_fit"]["level"] == "low", low["protocol_fit"]
    # 每一档都要说清"为什么"（不许只给一个字母）
    assert high["protocol_fit"]["reasons"] == []
    assert medium["protocol_fit"]["reasons"] and low["protocol_fit"]["reasons"]


def test_12_low_fit_cannot_be_main_lead(_ws_and_roster):
    """低适配（视觉型、无协议通道）选主组长 ⇒ 409，且**原因是适配度哪一项不够**。

    ⚠️ 口径（实测更正）：`fit=low` 必然也 `lead_eligible=false`，所以门是既有的
       `NOT_LEAD_ELIGIBLE`；V135 的贡献是**把适配度原因并进 `why`**，不是新造错误码。
    """
    info = _ws_and_roster["infos"]["示例Harness-Lead"]
    info.wakeup_method = "clipboard"
    info.tools = []
    info.activation_script = {}
    r = C.post("/api/workshop/%s/lead" % WS, json={"harness_id": "示例Harness-Lead"})
    assert r.status_code == 409, r.text
    body = r.json()
    assert body.get("code") == "NOT_LEAD_ELIGIBLE", body
    assert body.get("why"), "拦下来必须说清缺什么"
    assert (body.get("protocol_fit") or {}).get("level") == "low"
    assert "拍板权唯一" in str(body.get("policy"))


def test_13_lead_team_view_lists_helpers_and_coverage(_ws_and_roster):
    _ws_and_roster["infos"]["示例Harness-A"].tools = []          # 成员是视觉/弱型
    _ws_and_roster["infos"]["示例Harness-A"].wakeup_method = "clipboard"
    r = C.get("/api/workshop/%s/lead-team" % WS)
    assert r.status_code == 200, r.text
    body = r.json()
    assert (body.get("lead") or {}).get("protocol_fit"), body
    assert body.get("helpers"), "助手（成员）要在里面"
    cov = body.get("coverage") or {}
    assert set(cov) >= {"dispatch", "receive_result", "read_artifacts", "wake"}
    # 团队里没人能读产物时，必须点出来 + 平台替它做什么
    assert cov["read_artifacts"]["missing"] is True
    assert cov["read_artifacts"]["platform_will"]
    assert "拍板权唯一" in str(body.get("decide_note"))
