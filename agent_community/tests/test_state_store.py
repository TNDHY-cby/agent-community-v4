# -*- coding: utf-8 -*-
"""守卫：**状态落盘这一层**（V90 从 `server.py` 抽出来的纯函数）。

为什么要单测这一层：它是 2026-10-23 数据被清空事故的现场。
抽出来的好处是**能在没有 HTTP、没有全局字典**的情况下逐条判：

| # | 判据 | 为什么在意 |
|---|---|---|
| ① | 降级实例 ⇒ 拒写 | 两个实例写同一数据目录必互相覆盖 |
| ② | 空内存 + 磁盘有数据 ⇒ 拒写 | 那次事故的直接机制 |
| ③ | 加载过状态（owned）⇒ 空也允许写 | 否则用户"删光最后一个工作间"会删不掉 |
| ④ | `AC_STATE_ALLOW_EMPTY=1` ⇒ 放行 | 首次运行/手工清库的正当口子 |
| ⑤ | 原子写不留 `.tmp` 残留、写一半失败也不毁旧文件 | 崩在写盘中间不能把文件写坏 |
| ⑥ | 逐文件失败互不牵连（返回失败名单） | 一个坏文件不该拖垮全部状态 |
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.append(str(_ROOT))

from agent_community.platform import state_store as ss  # noqa: E402


def _reason(**kw):
    base = {"empty_now": False, "owned": True, "degraded": False,
            "disk_has_data": False, "allow_empty": False}
    base.update(kw)
    return ss.block_reason(**base)


def test_degraded_instance_is_refused():
    why = _reason(degraded=True)
    assert why, "降级实例必须拒写"
    assert "降级实例" in why and "拒绝写盘" in why


def test_empty_memory_cannot_overwrite_existing_data():
    why = _reason(empty_now=True, owned=False, disk_has_data=True)
    assert why, "空内存不该覆盖磁盘上已有的数据（那次事故）"
    assert "没有加载过状态" in why
    assert "AC_STATE_ALLOW_EMPTY" in why, "要告诉用户怎么才放行：%s" % why


def test_empty_is_allowed_when_owned_or_nothing_on_disk():
    assert _reason(empty_now=True, owned=True, disk_has_data=True) == "", "删光最后一个也应当能落盘"
    assert _reason(empty_now=True, owned=False, disk_has_data=False) == "", "磁盘本来就空，没什么可毁的"


def test_allow_empty_env_opens_the_door(monkeypatch):
    assert _reason(empty_now=True, owned=False, disk_has_data=True, allow_empty=True) == ""
    monkeypatch.setenv("AC_STATE_ALLOW_EMPTY", "1")
    assert ss.allow_empty_write() is True
    monkeypatch.setenv("AC_STATE_ALLOW_EMPTY", "0")
    assert ss.allow_empty_write() is False


def test_write_payload_is_atomic_and_leaves_no_tmp(tmp_path):
    payload = {"tasks.json": {"t1": {"id": "t1"}}, "rooms.json": {}}
    ok, failed = ss.write_payload(payload, tmp_path)
    assert ok is True and failed == []
    assert json.loads((tmp_path / "tasks.json").read_text(encoding="utf-8")) == payload["tasks.json"]
    assert not list(tmp_path.glob("*.tmp")), "留下了 .tmp 残渣：%s" % list(tmp_path.glob("*.tmp"))


def test_write_payload_reports_failed_files_without_stopping_others(tmp_path, monkeypatch):
    """一个文件写失败，其它文件照样落盘，并且**返回失败名单**（不许静默）。"""
    payload = {"tasks.json": {"t": 1}, "bad.json": {"x": 1}}
    real = ss.atomic_write_text

    def flaky(path, text):
        if Path(path).name == "bad.json":
            raise OSError("模拟磁盘写不动")
        return real(path, text)

    monkeypatch.setattr(ss, "atomic_write_text", flaky)
    ok, failed = ss.write_payload(payload, tmp_path)
    assert ok is False and failed == ["bad.json"], (ok, failed)
    assert (tmp_path / "tasks.json").is_file(), "好文件没写成功（被坏文件牵连了）"


def test_read_json_is_honest_about_broken_files(tmp_path):
    good = tmp_path / "good.json"
    good.write_text('{"a": 1}', encoding="utf-8")
    data, err = ss.read_json(good)
    assert data == {"a": 1} and err == ""

    bad = tmp_path / "bad.json"
    bad.write_text("{不是 json", encoding="utf-8")
    data, err = ss.read_json(bad)
    assert data is None and err, "坏 JSON 必须给出错误说明（不许当成空数据）"

    data, err = ss.read_json(tmp_path / "nope.json")
    assert data is None and err == "", "文件不存在不是错误"


def test_disk_has_data_detects_content(tmp_path):
    assert ss.disk_has_data(tmp_path) is False
    (tmp_path / "workshops.json").write_text("{}", encoding="utf-8")
    assert ss.disk_has_data(tmp_path) is False, "空字典不算有数据"
    (tmp_path / "workshops.json").write_text('{"ws": {}}', encoding="utf-8")
    assert ss.disk_has_data(tmp_path) is True
