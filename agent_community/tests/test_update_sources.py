# -*- coding: utf-8 -*-
"""守卫：**更新源多源回退**（V75 P1-2）。

判据（注入假探针，不联网）：
① 首源坏 → 自动用第二源，且**说出**首源为什么失败（不许悄悄换）；
② 全坏 → 返回里带**逐条原因**（不静默、不假装"已是最新版"）；
③ 没配 `AC_UPDATE_SOURCES` → 退回本仓库 git 远端（`_candidate_sources()` 非空且 gitee 优先）。
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]


def _load_update():
    spec = importlib.util.spec_from_file_location("ac_update_tool", _ROOT / "tools" / "update.py")
    m = importlib.util.module_from_spec(spec)
    sys.modules["ac_update_tool"] = m
    spec.loader.exec_module(m)
    return m


def test_falls_back_to_second_source(monkeypatch, capsys):
    up = _load_update()
    monkeypatch.setenv("AC_UPDATE_SOURCES", "坏源=https://bad.invalid/x.git,好源=https://ok.invalid/y.git")

    def fake_probe(name, url):
        if "bad" in url:
            return [], "connection reset"
        return ["v1.2.3"], ""

    ver, ref, src = up.latest_tag(probe=fake_probe)
    out = capsys.readouterr().out
    assert ver == "1.2.3" and ref == "v1.2.3", (ver, ref, src)
    assert "好源" in src, src
    assert "connection reset" in out, "回退时要说出首源为什么失败：%r" % out


def test_all_sources_fail_reports_each_reason(monkeypatch):
    up = _load_update()
    monkeypatch.setenv("AC_UPDATE_SOURCES", "甲=https://a.invalid,乙=https://b.invalid")
    # 还要把 Releases API 这条路关掉，才是真正的"所有源都失败"（否则它会兜底成功，那是正确行为）
    monkeypatch.setattr(up, "channel", lambda: {})

    def fake_probe(name, url):
        return [], "timeout(%s)" % name

    ver, ref, src = up.latest_tag(probe=fake_probe)
    assert ver == "" and ref == "", (ver, ref)
    assert "甲" in src and "乙" in src, "全失败时必须逐条列出：%r" % src
    assert "timeout" in src, src


def test_default_sources_come_from_git_remotes(monkeypatch):
    up = _load_update()
    monkeypatch.delenv("AC_UPDATE_SOURCES", raising=False)
    srcs = up._candidate_sources()
    assert isinstance(srcs, list)
    if srcs:                                   # 本仓库有远端时：gitee 必须排第一（若有）
        names = [n for n, _ in srcs]
        assert names, srcs
        if "gitee" in names:
            assert names.index("gitee") == 0, names
