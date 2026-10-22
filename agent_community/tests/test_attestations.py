# -*- coding: utf-8 -*-
"""守卫：V83 五条。

① 签名记录可验真；② 改一个字节 ⇒ 验签失败并说清原因；③ 扫描报告结构齐且 `trust=platform-self`；
④ **只追加**：同 `att_id` 再写被拒（409）；⑤ 未知 `subject_type` / `kind` ⇒ 400 且给 supported。
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.append(str(_ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from agent_community.platform import attestations as at  # noqa: E402
from agent_community.platform import server as srv  # noqa: E402
from agent_community.platform.protocol import HarnessInfo  # noqa: E402

C = TestClient(srv.app, client=("127.0.0.1", 54321))


def _iso(monkeypatch, tmp_path):
    monkeypatch.setenv("AC_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("AC_ATTEST_KEY", "示例签名密钥-123")


def test_sign_and_verify(monkeypatch, tmp_path):
    _iso(monkeypatch, tmp_path)
    rec = at.append("harness", "示例Harness-A", "audit", note="人工确认过")
    assert rec["trust"] == "platform-self" and rec["signature"]
    assert at.verify(rec)["valid"] is True, at.verify(rec)


def test_tampered_record_fails_verify(monkeypatch, tmp_path):
    _iso(monkeypatch, tmp_path)
    rec = at.append("harness", "示例Harness-A", "audit", note="原件")
    bad = dict(rec)
    bad["note"] = "被改过了"
    out = at.verify(bad)
    assert out["valid"] is False and "签名不匹配" in out["why"], out


def test_scan_report_shape(monkeypatch, tmp_path):
    _iso(monkeypatch, tmp_path)
    info = HarnessInfo(harness_id="示例Harness-B", harness_name="示例Harness-B",
                       harness_type="custom", wakeup_method="file_poll",
                       ai={"model_name": "m", "provider": "p"})
    rec = at.append("harness", "示例Harness-B", "scan", info=info)
    res = rec["result"]
    assert "items" in res and res["items"], res
    for item in res["items"]:
        assert {"item", "passed", "why"} <= set(item), item
    assert any(i["item"] == "card_conformance" for i in res["items"]), res
    assert rec["trust"] == "platform-self"


def test_append_only_same_id_refused(monkeypatch, tmp_path):
    _iso(monkeypatch, tmp_path)
    at.append("harness", "示例Harness-C", "audit", att_id="att-fixed-1")
    r = C.post("/api/attest/harness/示例Harness-C", json={"kind": "audit", "att_id": "att-fixed-1"})
    assert r.status_code == 409 and "只追加" in r.text, r.text


def test_bad_subject_and_kind_are_400(monkeypatch, tmp_path):
    _iso(monkeypatch, tmp_path)
    bad_subject = C.post("/api/attest/不存在的类型/x", json={"kind": "audit"})
    assert bad_subject.status_code == 400 and "supported" in bad_subject.text, bad_subject.text
    bad_kind = C.post("/api/attest/harness/x", json={"kind": "乱写"})
    assert bad_kind.status_code == 400 and "signature" in bad_kind.text, bad_kind.text
