# -*- coding: utf-8 -*-
"""守卫：外端回执（`V110`）与 intake 三档 —— P1 批的 20 条判据。

判据编号与设计一致（`V115` §2.R）：R1~R20。
每条都是**真解析/真 HTTP/真文件**，不查源码文本。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.append(str(_ROOT))

from agent_community.platform import intake_token as tok  # noqa: E402
from agent_community.platform import receipt as rc  # noqa: E402
from agent_community.platform import receipt_store as store  # noqa: E402

GOOD = """#回执 v1
类型: 名单
工作间: WS-T
成员: 组长
凭据:
序号: 1
---
岗位: 前端组/员工01 | harness: h_cursor | model: claude-x | 人数: 1
岗位: 后端组/员工01 | harness: h_trae_cn | model: deepseek-v3 | 人数: 2
---
行数: 2"""


def _codes(res: dict) -> list:
    return [e["code"] for e in (res.get("errors") or [])]


# ── 解析器（R1~R5、R9）───────────────────────────────────────────────
def test_r1_version_line():
    res = rc.parse(GOOD.replace("#回执 v1", "#回执 v2"))
    assert not res["ok"] and _codes(res) == ["R001"]
    assert res["errors"][0]["fix"], "错误必须带怎么改"


def test_r2_missing_head_line():
    bad = GOOD.replace("序号: 1\n", "")
    res = rc.parse(bad)
    assert not res["ok"] and "R002" in _codes(res)
    assert res["errors"][0]["field"] == "序号"


def test_r3_unknown_type():
    res = rc.parse(GOOD.replace("类型: 名单", "类型: 汇报"))
    assert not res["ok"] and _codes(res) == ["R003"]


def test_r4_row_field_name_wrong_is_atomic():
    """4 行里第 2 行字段名写错 ⇒ 整单拒（不部分写入）。"""
    bad = GOOD.replace("model: deepseek-v3", "mdl: deepseek-v3")
    res = rc.parse(bad)
    assert not res["ok"]
    assert res["receipt"] is None, "整单拒时不许留下半份 receipt"
    err = res["errors"][0]
    assert err["code"] == "R004" and err["line"] > 0 and err["fix"]


def test_r5_count_mismatch_says_actual():
    res = rc.parse(GOOD.replace("行数: 2", "行数: 3"))
    assert not res["ok"] and _codes(res) == ["R005"]
    assert "实际" in res["errors"][0]["message"]


def test_r9_halfwidth_pipe_in_text():
    bad = GOOD.replace("岗位: 前端组/员工01 | harness", "岗位: 前端组|员工01 | harness")
    res = rc.parse(bad)
    assert not res["ok"] and "R009" in _codes(res)
    assert "｜" in res["errors"][0]["fix"], "要教它用全角竖线"


def test_parse_accepts_all_four_types():
    for t, body in (("派活", "任务: T-1 | 派给: 前端组/员工01 | 标题: 接口 | 期限: 2026-10-26"),
                    ("验收", "验收: T-1 | 结果: 通过 | 产物: 2 | 备注: ok"),
                    ("状态", "状态: 前端组/员工01 | 新: active | 原因: 已确认")):
        text = GOOD.replace("类型: 名单", "类型: " + t).replace(
            "岗位: 前端组/员工01 | harness: h_cursor | model: claude-x | 人数: 1\n"
            "岗位: 后端组/员工01 | harness: h_trae_cn | model: deepseek-v3 | 人数: 2", body
        ).replace("行数: 2", "行数: 1")
        res = rc.parse(text)
        assert res["ok"], (t, res["errors"])
        assert res["receipt"]["type"] in ("dispatch", "accept", "state")


# ── 幂等与序号（R6~R8）+ 落盘（R10、R20）─────────────────────────────
def test_r6_duplicate_same_seq_same_hash(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "_root", lambda: tmp_path / "receipts", raising=True)
    h = rc.sha256_text(GOOD)
    assert store.check_seq("WS-T", "组长", 1, h)[0] == "ok"
    store.record("WS-T", kind="ok", source="C", member="组长", seq=1, raw_hash=h,
                 raw_rel="raw/x.rcpt", ok=True, rtype="roster", rows=2)
    assert store.check_seq("WS-T", "组长", 1, h)[0] == "duplicate"


def test_r7_same_seq_different_hash(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "_root", lambda: tmp_path / "receipts", raising=True)
    h = rc.sha256_text(GOOD)
    store.record("WS-T", kind="ok", source="C", member="组长", seq=1, raw_hash=h,
                 raw_rel="raw/x.rcpt", ok=True, rtype="roster", rows=2)
    verdict, detail = store.check_seq("WS-T", "组长", 1, rc.sha256_text(GOOD + "\n"))
    assert verdict == "R010" and "下一个用" in detail["fix"]


def test_r8_seq_rollback(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "_root", lambda: tmp_path / "receipts", raising=True)
    store.record("WS-T", kind="ok", source="C", member="组长", seq=5, raw_hash="h1",
                 raw_rel="raw/a.rcpt", ok=True, rtype="roster", rows=1)
    verdict, detail = store.check_seq("WS-T", "组长", 3, "h2")
    assert verdict == "R006" and detail["last_seq"] == 5 and "6" in detail["fix"]


def test_r10_rejected_receipts_are_recorded_with_raw(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "_root", lambda: tmp_path / "receipts", raising=True)
    bad = GOOD.replace("model: deepseek-v3", "mdl: deepseek-v3")
    parsed = rc.parse(bad)
    raw_rel = store.save_raw("WS-T", bad, parsed["raw_hash"], "组长")
    store.record("WS-T", kind="rejected", source="C", member="组长", seq=None,
                 raw_hash=parsed["raw_hash"], raw_rel=raw_rel, ok=False,
                 errors=parsed["errors"], note="解析失败")
    items = store.recent("WS-T", 5)
    assert items and items[0]["kind"] == "rejected" and items[0]["errors"], "被拒也要留痕"
    saved = (tmp_path / "receipts" / "WS-T" / raw_rel).read_text(encoding="utf-8")
    assert saved == bad, "原文要一字不差地留（含错的那行）"


# ── token（R11、R12）────────────────────────────────────────────────
def test_r11_token_scope_and_bad(tmp_path, monkeypatch):
    monkeypatch.setattr(tok, "_rev_path", lambda: tmp_path / "revoked.json", raising=True)
    monkeypatch.setattr("agent_community.platform.identity._ensure_secret", lambda: "s3cret",
                        raising=True)
    got = tok.issue("WS-A", "组长")
    ws, member = tok.verify(got["token"])[0] or (None, None)
    assert (ws, member) == ("WS-A", "组长")
    # 改一个字符 ⇒ 签名不对
    bad = got["token"][:-2] + ("AA" if not got["token"].endswith("AA") else "BB")
    assert tok.verify(bad)[0] is None
    assert tok.verify("")[1] == "TOKEN_MISSING"


def test_r12_revoke_takes_effect_immediately(tmp_path, monkeypatch):
    monkeypatch.setattr(tok, "_rev_path", lambda: tmp_path / "revoked.json", raising=True)
    monkeypatch.setattr("agent_community.platform.identity._ensure_secret", lambda: "s3cret",
                        raising=True)
    t = tok.issue("WS-A", "组长")["token"]
    assert tok.verify(t)[0] is not None
    tok.revoke("WS-A", "组长")
    got, why = tok.verify(t)
    assert got is None and why == "TOKEN_REVOKED"


# ── HTTP 端到端（R13~R19）：真路由、真文件 ──────────────────────────
@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("AC_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(store, "_root", lambda: tmp_path / "data" / "receipts", raising=True)
    monkeypatch.setattr(tok, "_rev_path", lambda: tmp_path / "data" / "revoked.json",
                        raising=True)
    monkeypatch.setattr("agent_community.platform.identity._ensure_secret", lambda: "s3cret",
                        raising=True)
    from fastapi.testclient import TestClient

    from agent_community.platform import server as srv

    ws_dir = tmp_path / "wsroot"
    ws_dir.mkdir()
    ws = srv.Workshop(workshop_id="WS-T", name="示例间", workspace_dir=str(ws_dir),
                      hall_content="方向")
    srv.workshops["WS-T"] = ws
    return TestClient(srv.app, client=("127.0.0.1", 54321))


def test_r13_a_tier_requires_token(client):
    r = client.post("/api/workshop/WS-T/intake?source=A", json={"text": GOOD})
    assert r.status_code == 401, r.text


def test_r14_a_tier_with_token_ok_and_scope(client):
    t = client.post("/api/workshop/WS-T/members/组长/token").json()["token"]
    r = client.post("/api/workshop/WS-T/intake?source=A", json={"text": GOOD},
                    headers={"Authorization": "Bearer " + t})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] and body["row_count"] == 2
    # P2 起名单会**真落库**（applied=true）；不许假装的是派活/验收那两类
    assert body["applied"] is True and body["stage"].startswith("P2"), body
    assert body["result"]["members_created"], body


def test_r15_a_tier_scope_mismatch(client):
    t = client.post("/api/workshop/WS-T/members/组长/token").json()["token"]
    bad = GOOD.replace("成员: 组长", "成员: 员工01")
    r = client.post("/api/workshop/WS-T/intake?source=A", json={"text": bad},
                    headers={"Authorization": "Bearer " + t})
    assert r.status_code == 403 and r.json()["code"] == "TOKEN_SCOPE"


def test_r16_c_tier_needs_human_confirm(client):
    r = client.post("/api/workshop/WS-T/intake?source=C", json={"text": GOOD})
    assert r.status_code == 403 and r.json()["code"] == "NEED_HUMAN_CONFIRM"
    ok = client.post("/api/workshop/WS-T/intake?source=C&confirm=1", json={"text": GOOD})
    assert ok.status_code == 200 and ok.json()["ok"]


def test_r17_c_tier_same_text_twice_is_duplicate(client):
    assert client.post("/api/workshop/WS-T/intake?source=C&confirm=1",
                       json={"text": GOOD}).json()["ok"]
    again = client.post("/api/workshop/WS-T/intake?source=C&confirm=1", json={"text": GOOD})
    assert again.json().get("duplicate") is True


def test_r18_receipt_flow_is_queryable_and_atomic(client):
    bad = GOOD.replace("model: deepseek-v3", "mdl: deepseek-v3")
    r = client.post("/api/workshop/WS-T/intake?source=C&confirm=1", json={"text": bad})
    assert r.status_code == 422 and r.json()["code"] == "RECEIPT_ATOMIC_FAIL"
    assert r.json()["detail"]["errors"][0]["line"] > 0
    listing = client.get("/api/workshop/WS-T/receipts?limit=5").json()
    assert listing["count"] >= 1 and listing["items"][0]["kind"] == "rejected"
    assert listing["counts"]["rejected"] >= 1


def test_r19_dispatch_body_not_stored_but_index_is(client):
    text = GOOD.replace("类型: 名单", "类型: 派活").replace(
        "岗位: 前端组/员工01 | harness: h_cursor | model: claude-x | 人数: 1\n"
        "岗位: 后端组/员工01 | harness: h_trae_cn | model: deepseek-v3 | 人数: 2",
        "任务: T-9 | 派给: 前端组/员工01 | 标题: 接口对接 | 期限: 2026-10-26T16:00:00",
    ).replace("行数: 2", "行数: 1")
    r = client.post("/api/workshop/WS-T/intake?source=C&confirm=1", json={"text": text})
    body = r.json()
    assert body["ok"] and body["type"] == "dispatch"
    # ★P3 起派活**真落库**（写文件+notice+manifest+激活）；派给不存在的岗位 ⇒ 进 failed 并说清
    assert body["stage"].startswith("P3")
    failed = (body.get("result") or {}).get("failed") or []
    assert failed and "工位" in json.dumps(failed, ensure_ascii=False), \
        "派给不存在的岗位必须明确失败（不许静默）"


def test_r20_b_tier_poll_moves_file_and_leaves_result(client, tmp_path):
    inbox = tmp_path / "wsroot" / "_meta" / "_inbox"
    inbox.mkdir(parents=True)
    (inbox / "0001-组长.rcpt").write_text(GOOD, encoding="utf-8")
    out = client.post("/api/workshop/WS-T/intake/poll").json()
    assert out["ok"] and out["found"] == 1
    first = out["processed"][0]
    assert first["ok"] is True
    assert not (inbox / "0001-组长.rcpt").exists(), "成功要移走"
    assert (inbox / "_done" / "0001-组长.rcpt").exists()
    assert (inbox / "_done" / ("0001-组长.rcpt.result.json")).is_file()


def test_b_tier_failure_keeps_file_and_writes_err(client, tmp_path):
    inbox = tmp_path / "wsroot" / "_meta" / "_inbox"
    inbox.mkdir(parents=True)
    bad = GOOD.replace("#回执 v1", "#回执 v9")
    (inbox / "0002.rcpt").write_text(bad, encoding="utf-8")
    out = client.post("/api/workshop/WS-T/intake/poll").json()
    assert out["processed"][0]["ok"] is False
    assert (inbox / "0002.rcpt").exists(), "失败要留在原地（人能看到）"
    assert (inbox / "_fail" / "0002.rcpt.err").is_file()
