# -*- coding: utf-8 -*-
"""守卫：**状态快照与恢复**（2026-10-23 数据被清空事故后的正面补强）。

判据（都按真文件判）：
1. 拍快照 ⇒ 文件**逐字节**复制过去，且带 `_meta.json`（写明原因、是否空状态）；
2. 只保留最近 N 份（超出的删最旧）；
3. 节流：同一间隔内连拍只会有一份（`maybe_snapshot` 返回"还没到间隔"）；
4. **空快照要打标记**（`empty: true`）——那是"被清空那一刻"的证据，恢复工具默认不许倒回去；
5. 状态文件一个都没有时，不假装成功（`ok=False` + 原因）。
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.append(str(_ROOT))

from agent_community.platform import state_backup as bk  # noqa: E402


@pytest.fixture()
def data_dir(tmp_path, monkeypatch):
    d = tmp_path / "data"
    d.mkdir()
    (d / "workshops.json").write_text(
        json.dumps({"ws_1": {"name": "示例工作间-A"}}, ensure_ascii=False), encoding="utf-8")
    (d / "tasks.json").write_text(json.dumps({"t1": {"id": "t1"}}, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(bk, "_data_dir", lambda: d, raising=True)
    monkeypatch.setenv("AC_STATE_BACKUP_MINUTES", "30")
    monkeypatch.setenv("AC_STATE_BACKUP_KEEP", "3")
    # 节流窗口在模块级，测试之间必须复位（否则第二条用例会莫名其妙"刚拍过"）
    monkeypatch.setattr(bk, "_LAST_AT", 0.0, raising=True)
    return d


def _snapshot_files(d: Path) -> dict:
    return {p.name: p.read_bytes() for p in d.iterdir() if p.is_file()}


def _backup_files(snap_dir: Path) -> dict:
    return {p.name: p.read_bytes() for p in snap_dir.iterdir() if p.is_file() and p.name != "_meta.json"}


def test_snapshot_copies_bytes_and_writes_meta(data_dir):
    data_before = _snapshot_files(data_dir)
    res = bk.snapshot("startup")
    assert res["ok"] is True, res
    snap = Path(res["dir"])
    assert snap.is_dir()
    assert _backup_files(snap) == data_before, "快照不是逐字节复制（那就是假备份）"
    meta = json.loads((snap / "_meta.json").read_text(encoding="utf-8"))
    assert meta["reason"] == "startup"
    assert meta["empty"] is False, "有数据却标成空：%s" % meta


def test_empty_state_snapshot_is_flagged(data_dir):
    for n in ("workshops.json", "tasks.json"):
        (data_dir / n).write_text("{}", encoding="utf-8")
    res = bk.snapshot("incident")
    assert res["ok"] is True, res
    snap = [s for s in bk.list_snapshots() if s["name"] == Path(res["dir"]).name][0]
    assert snap["empty"] is True, "空状态没被标记 ⇒ 恢复工具没法拦住「把空倒回去」"


def test_prune_keeps_latest_n(data_dir):
    names = []
    for i in range(5):
        res = bk.snapshot("t%d" % i)
        assert res["ok"], res
        names.append(Path(res["dir"]).name)
        time.sleep(0.01)                     # 让目录名（%S 精度）至少分开一点
    snaps = bk.list_snapshots()
    assert len(snaps) == 3, "保留份数没生效：%s" % [s["name"] for s in snaps]
    # 最新的三份应当留下（按名字倒序＝时间倒序）
    assert snaps[0]["name"] == sorted(names)[-1] or snaps[0]["name"] >= sorted(names)[-2]


def test_maybe_snapshot_throttles(data_dir):
    first = bk.maybe_snapshot("auto")
    assert first["ok"] is True, first
    second = bk.maybe_snapshot("auto")
    assert second["ok"] is False and "刚拍过" in second["why"], second
    assert len(bk.list_snapshots()) == 1


def test_no_state_files_means_no_fake_success(tmp_path, monkeypatch):
    empty = tmp_path / "empty_data"
    empty.mkdir()
    monkeypatch.setattr(bk, "_data_dir", lambda: empty, raising=True)
    res = bk.snapshot("startup")
    assert res["ok"] is False, "没有状态文件却报成功＝假绿：%s" % res
    assert "不存在" in (res.get("why") or "")
