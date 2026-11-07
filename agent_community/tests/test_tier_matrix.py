# -*- coding: utf-8 -*-
"""守卫（`V134` ⑧⑨，2026-11-07）：**档位矩阵一处真相** + **通知投递账不许把 written 说成送达**。

判据都是真结果（不查源码文本）：

  ① 矩阵 4 档 × 4 轴齐全，每格行为码都在 `CODES` 里；
  ② 新增一档**不填矩阵** ⇒ `gaps()` 立刻报出来（这就是"以后加档必漏"的闸门）；
  ③ 未知档位取码给**最保守**的（验收必须人看 / 没有自动通知）；
  ④ 通知账只收三类结果，别的值直接拒；
  ⑤ 通知账的 `written` 标签必须写清"不知道对方看没看"（不许暗示送达）。
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.append(str(_ROOT))

from agent_community.platform import notice_log as NL  # noqa: E402
from agent_community.platform import tier_matrix as TM  # noqa: E402


def test_01_matrix_is_complete():
    assert not TM.gaps(), TM.gaps()
    for tier, row in TM.TIER_MATRIX.items():
        assert row.get("label"), "档位 %s 缺 label" % tier
        for a in TM.AXES:
            assert row.get(a) in TM.CODES, "%s.%s 的行为码未定义：%r" % (tier, a, row.get(a))


def test_02_new_tier_must_fill_whole_row():
    snapshot = dict(TM.TIER_MATRIX)
    try:
        TM.TIER_MATRIX["d_experimental"] = {"label": "新档（故意不填）", "reach": "protocol_push"}
        gaps = TM.gaps()
        assert any(g["tier"] == "d_experimental" for g in gaps), gaps
        assert {g["axis"] for g in gaps if g["tier"] == "d_experimental"} == \
            set(TM.AXES) - {"reach"}, gaps
    finally:
        TM.TIER_MATRIX.clear()
        TM.TIER_MATRIX.update(snapshot)
    assert not TM.gaps(), "还原后矩阵必须重新完整"


def test_03_unknown_tier_is_conservative():
    assert TM.code_of("没这个档", "review") == "gate_reject_auto"
    assert TM.code_of("没这个档", "notice") == "no_auto_notice"
    assert TM.code_of("", "artifact") == "no_auto_notice"


def test_04_notice_log_rejects_bad_result(tmp_path, monkeypatch):
    monkeypatch.setattr(NL, "_path", lambda: tmp_path / "notice_log.json", raising=False)
    r = NL.record("ws-1", target="lead", result="已送达")
    assert r["ok"] is False and r["code"] == "BAD_RESULT"


def test_05_notice_log_counts_and_labels(tmp_path, monkeypatch):
    monkeypatch.setattr(NL, "_path", lambda: tmp_path / "notice_log.json", raising=False)
    NL.record("ws-2", target="lead", slot="_lead", channel="notice_file", result="written")
    NL.record("ws-2", target="member:前端组/员工01", slot="前端组/员工01",
              channel="visual", result="delivered")
    NL.record("ws-2", target="lead", channel="visual", result="failed", detail="没窗口")
    out = NL.log("ws-2")
    assert out["counts"] == {"written": 1, "delivered": 1, "failed": 1}, out["counts"]
    assert "不知道" in out["labels"]["written"], out["labels"]
    assert len(out["items"]) == 3
