# -*- coding: utf-8 -*-
"""守卫：V85 六条。

① 记样本 + 上限截断留痕；② 连续失败天数算得对；③ 满 7 天进候选、6 天不进；
④ 隐藏不删数据；⑤ 申诉恢复并留痕；⑥ 清理按 90 天只删明细，dry-run 不动。
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.append(str(_ROOT))

from agent_community.platform import registry_health as rh  # noqa: E402
from agent_community.platform import registry_lifecycle as rl  # noqa: E402


def _iso(days_ago: float = 0) -> str:
    return (datetime.now() - timedelta(days=days_ago)).isoformat(timespec="seconds")


def _seed(monkeypatch, tmp_path, hid, samples):
    monkeypatch.setenv("AC_DATA_DIR", str(tmp_path))
    data = {hid: {"samples": samples, "hidden": False, "hidden_reason": "",
                  "hidden_at": "", "appeals": [], "trimmed": 0}}
    import json

    (tmp_path / "registry_health.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def test_record_and_trim_marker(monkeypatch, tmp_path):
    monkeypatch.setenv("AC_DATA_DIR", str(tmp_path))
    for i in range(rh.MAX_SAMPLES + 3):
        st = rh.record("示例Harness-A", ok=False, why="第%d次" % i)
    assert st["samples"] == rh.MAX_SAMPLES, st
    assert st["trimmed"] == 3, "截断要留痕（不能悄悄丢）：%r" % st


def test_days_all_failing_counts(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path, "h-days", [
        {"at": _iso(10), "ok": False, "why": ""},
        {"at": _iso(1), "ok": False, "why": ""},
    ])
    st = rh.status("h-days")
    assert st["days_all_failing"] >= 9, st


def test_candidate_only_after_seven_days(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path, "h-6", [{"at": _iso(6), "ok": False, "why": ""}])
    assert rh.status("h-6")["candidate"] is False, rh.status("h-6")

    _seed(monkeypatch, tmp_path, "h-7", [{"at": _iso(7.2), "ok": False, "why": ""}])
    assert rh.status("h-7")["candidate"] is True, rh.status("h-7")
    assert rl.should_hide(7) is True and rl.should_hide(6) is False


def test_hide_keeps_data(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path, "h-hide", [{"at": _iso(9), "ok": False, "why": "连不上"}])
    out = rh.set_hidden("h-hide", True, reason="连续 9 天全失败")
    assert out["hidden"] is True and out["hidden_reason"]
    assert out["samples"] == 1, "隐藏**不许删数据**：%r" % out


def test_appeal_restores_and_records(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path, "h-appeal", [{"at": _iso(9), "ok": False, "why": ""}])
    rh.set_hidden("h-appeal", True, reason="x")
    out = rh.appeal("h-appeal", note="我已修好桥，请恢复")
    assert out["hidden"] is False and out["appeals"] == 1, out


def test_purge_only_detail_and_dry_run(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path, "h-purge", [
        {"at": _iso(120), "ok": False, "why": "老样本"},
        {"at": _iso(1), "ok": True, "why": ""},
    ])
    dry = rh.purge_detail(days=90, dry_run=True)
    assert dry["applied"] is False and dry["dropped"].get("h-purge") == 1, dry
    assert rh.status("h-purge")["samples"] == 2, "dry-run 不许动数据"

    real = rh.purge_detail(days=90, dry_run=False)
    assert real["applied"] is True
    st = rh.status("h-purge")
    assert st["samples"] == 1 and st["trimmed"] == 1, "清理明细要留汇总痕迹：%r" % st
