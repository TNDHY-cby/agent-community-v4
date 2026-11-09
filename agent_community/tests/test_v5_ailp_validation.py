# -*- coding: utf-8 -*-
"""守卫：**AILP 批 1 的校验**（`V122` §3 + §4.1，2026-11-01 老板拍板：两个 bug 立即修、批 1 现在动）。

守的东西（每条都是"以前不做、现在做"的真行为，不是文案）：
  ① `REFINE.target_id` 必须**真存在**（以前规则里写了、代码里没有 ⇒ 随手改不存在的 id 也照收）；
  ② 正常 `POST …/ailp` **也查重**（以前只有 `/ailp/replay` 查）⇒ 同一条重发不重复入账；
  ③ `basis_ids` / `evidence_ids` 的每一项必须是**账本内实体**（以前只校验非空）；
  ④ `REFINE` 必须带 `reason`（置信度只追加、不原地改）；
  ⑤ `ACT.task_ref` 必须是**任务账本里的真实任务号**；`owner` 必须在名单里；
  ⑥ `CLARIFY` / `RECOMMEND` 的引用也要真存在；`RECOMMEND` **不产生终态**。

全部真跑：真 HTTP（TestClient）、真 manifest、真 JSONL 行数（不是查源码文本）。
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
from agent_community.platform import task_ledger as ledger  # noqa: E402
from agent_community.platform import v5_roster as roster  # noqa: E402

REF = "前端组/员工01/任务/T-001.md"


def _env(**kw):
    base = {"ailp_version": "2.0", "msg_id": "msg-1", "msg_type": "PROPOSE",
            "sender": "员工01", "target": "组长", "manifest_ref": REF,
            "payload": {"proposal": "先做接口", "confidence": 0.8, "basis_ids": [REF]}}
    base.update(kw)
    return base


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("AC_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("AC_MIND_ROOT", str(tmp_path / "minds"))
    monkeypatch.setattr(ledger, "_path", lambda: tmp_path / "data" / "task_ledger.json",
                        raising=True)
    monkeypatch.setattr(roster, "_path", lambda: tmp_path / "data" / "v5_roster.json",
                        raising=True)
    from fastapi.testclient import TestClient

    from agent_community.platform import server as srv

    wp = tmp_path / "ws"
    (wp / "_lead").mkdir(parents=True)
    (wp / "_lead" / "建模.md").write_text("# 建模\n", encoding="utf-8")
    (wp / REF).parent.mkdir(parents=True, exist_ok=True)
    (wp / REF).write_text("# T-001\n", encoding="utf-8")
    srv.Workshop(workshop_id="WS-A", name="批1间", workspace_dir=str(wp),
                 hall_content="方向", status="running")
    srv.workshops["WS-A"] = srv.workshops.get("WS-A") or None
    ws = srv.Workshop(workshop_id="WS-A", name="批1间", workspace_dir=str(wp),
                      hall_content="方向", status="running")
    srv.workshops["WS-A"] = ws
    c = TestClient(srv.app, client=("127.0.0.1", 54321))
    (tmp_path / "minds" / "A-01").mkdir(parents=True, exist_ok=True)
    c.post("/api/workshop/WS-A/manifest", json={"mind_path": str(tmp_path / "minds" / "A-01"),
                                                "entry_points": ["_lead/建模.md"],
                                                "active_tasks": [REF]})
    return c


def _log_lines(tmp_path: Path) -> int:
    """真读 JSONL 行数（判"账本只增一条"用这个，不看接口自己报的）。"""
    p = tmp_path / "data" / "discuss" / "WS-A.jsonl"
    if not p.is_file():
        return 0
    return len([x for x in p.read_text(encoding="utf-8").splitlines() if x.strip()])


# ── ① REFINE.target_id 必须真存在（以前的 bug）─────────────────────
def test_refine_target_must_exist_and_lists_available():
    ok, errs, _ = A.validate(
        _env(msg_type="REFINE", payload={"target_id": "msg-404", "modifications": "m",
                                         "new_confidence": 0.9, "reason": "新证据"}),
        allowed=[REF], msg_ids={"msg-1"})
    assert not ok
    e = next(x for x in errs if x["code"] == "A013")
    assert "msg-404" in e["message"]
    assert "msg-1" in e["fix"], "要告诉它能指哪些"


def test_refine_with_reason_and_existing_target_passes():
    ok, errs, norm = A.validate(
        _env(msg_type="REFINE", payload={"target_id": "msg-1", "modifications": "m",
                                         "new_confidence": 0.9, "reason": "新证据"}),
        allowed=[REF], msg_ids={"msg-1"})
    assert ok, errs
    assert norm["msg_type"] == "REFINE"


# ── ② 正常 POST 也查重：账本只增一条（真读行数）──────────────────
def test_http_post_dedup_keeps_single_row(client, tmp_path):
    first = client.post("/api/workshop/WS-A/ailp", json=_env())
    assert first.status_code == 200, first.text
    assert _log_lines(tmp_path) == 1

    again = client.post("/api/workshop/WS-A/ailp", json=_env())
    assert again.status_code == 200, again.text
    body = again.json()
    assert body.get("duplicate") is True, "同一条重复发要幂等，不是报错"
    assert body.get("msg_id") == first.json().get("msg_id"), "要返回已有的 msg_id"
    assert _log_lines(tmp_path) == 1, "账本不许重复入账"


def test_http_post_similar_but_legit_is_not_killed(client, tmp_path):
    """**相似但合法**的重复发言不能被误杀（老板点破的坑：只比 fingerprint 会误伤）。"""
    a = client.post("/api/workshop/WS-A/ailp", json=_env())
    assert a.status_code == 200
    # 同一个 fingerprint（内容一模一样），但 sender 不同 ⇒ 不是同一条重复
    b = client.post("/api/workshop/WS-A/ailp", json=_env(msg_id="msg-2", sender="员工02"))
    assert b.status_code == 200, b.text
    assert b.json().get("duplicate") is not True
    assert _log_lines(tmp_path) == 2


# ── ③ 依据必须真存在 ─────────────────────────────────────────────
def test_basis_id_must_exist(client, tmp_path):
    bad = client.post("/api/workshop/WS-A/ailp",
                      json=_env(payload={"proposal": "p", "confidence": 0.8,
                                         "basis_ids": ["msg-不存在"]}))
    assert bad.status_code == 422, bad.text
    assert any(e["code"] == "A014" for e in bad.json()["errors"])
    assert _log_lines(tmp_path) == 0, "被拒的不能入账"


def test_basis_can_point_to_task_id_from_ledger(client, tmp_path):
    ledger.issue("WS-A", 1, slot_dir="前端组/员工01", file=REF)
    ok = client.post("/api/workshop/WS-A/ailp",
                     json=_env(payload={"proposal": "p", "confidence": 0.8,
                                        "basis_ids": ["T-001"]}))
    assert ok.status_code == 200, ok.text


# ── ④ REFINE 必须带 reason ───────────────────────────────────────
def test_refine_without_reason_is_rejected():
    ok, errs, _ = A.validate(
        _env(msg_type="REFINE", payload={"target_id": "msg-1", "modifications": "m",
                                         "new_confidence": 0.9}),
        allowed=[REF], msg_ids={"msg-1"})
    assert not ok and any(e["code"] == "A008" for e in errs)


def test_refine_with_blank_reason_is_rejected():
    """空字符串不算"给了"—— 最容易蒙过去的一种。"""
    ok, errs, _ = A.validate(
        _env(msg_type="REFINE", payload={"target_id": "msg-1", "modifications": "m",
                                         "new_confidence": 0.9, "reason": "   "}),
        allowed=[REF], msg_ids={"msg-1"})
    assert not ok, "空 reason 必须拒"


# ── ⑤ ACT：任务号必须在账本里，owner 必须在名单里 ────────────────
def test_act_task_ref_must_exist_in_ledger(client):
    r = client.post("/api/workshop/WS-A/ailp",
                    json=_env(msg_type="ACT", payload={"task_ref": "T-999", "owner": "员工01"}))
    assert r.status_code == 422
    assert any(e["code"] == "A016" for e in r.json()["errors"])


def test_act_ok_when_task_exists(client):
    ledger.issue("WS-A", 1, slot_dir="前端组/员工01", file=REF)
    ok = client.post("/api/workshop/WS-A/ailp",
                     json=_env(msg_type="ACT", payload={"task_ref": "T-001", "owner": "员工01",
                                                        "due": "2026-11-05"}))
    assert ok.status_code == 200, ok.text


def test_act_owner_must_be_in_roster(client):
    ledger.issue("WS-A", 1, slot_dir="前端组/员工01", file=REF)
    roster.add_slots("WS-A", [{"岗位": "前端组/员工01", "harness": "h", "model": "m",
                               "人数": 1}], source="C")
    bad = client.post("/api/workshop/WS-A/ailp",
                      json=_env(msg_type="ACT", payload={"task_ref": "T-001",
                                                         "owner": "查无此人"}))
    assert bad.status_code == 422
    assert any(e["code"] == "A017" for e in bad.json()["errors"]), bad.text


# ── ⑥ CLARIFY / RECOMMEND 的引用也要真存在；RECOMMEND 不产生终态 ──
def test_clarify_to_ref_must_exist():
    ok, errs, _ = A.validate(_env(msg_type="CLARIFY",
                                  payload={"question": "然后呢", "to_ref": "msg-404"}),
                             allowed=[REF], msg_ids={"msg-1"})
    assert not ok and any(e["code"] == "A013" for e in errs)


def test_recommend_does_not_create_terminal_state(client, tmp_path):
    """`RECOMMEND` 只是推荐：**不产生 DECIDED、不改任何状态**（终态只能由人或组长给）。"""
    base = client.post("/api/workshop/WS-A/ailp", json=_env())
    msg_id = base.json()["msg_id"]
    r = client.post("/api/workshop/WS-A/ailp",
                    json=_env(msg_id="msg-rec", msg_type="RECOMMEND",
                              payload={"target_id": msg_id, "reason": "它依据更全",
                                       "confidence": 0.7}))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("msg_type") == "RECOMMEND"
    assert "state" not in body and "decided" not in body, "推荐不许产生终态"
    assert _log_lines(tmp_path) == 2

def test_http_post_dedup_survives_crossing_a_second(client, tmp_path):
    """**跨秒**重发也必须幂等。

    2026-11-09：`fingerprint` 原来把 `received_at`（秒级时间戳）也算进去了 ⇒
    同一份内容晚一秒再发就换了指纹、去重不命中。这条用例故意 `sleep(1.1)` 跨过一秒，
    把那个 bug 钉死（它曾是 `test_http_post_dedup_keeps_single_row` 偶发红的根因）。
    """
    import time as _t

    first = client.post("/api/workshop/WS-A/ailp", json=_env())
    assert first.status_code == 200, first.text
    _t.sleep(1.1)                                   # 跨过一秒，received_at 一定不同
    again = client.post("/api/workshop/WS-A/ailp", json=_env())
    assert again.status_code == 200, again.text
    body = again.json()
    assert body.get("duplicate") is True, "跨秒重发也必须判重（指纹不能含时间戳）"
    assert body.get("msg_id") == first.json().get("msg_id")
    assert _log_lines(tmp_path) == 1, "跨秒重发也不许重复入账"
