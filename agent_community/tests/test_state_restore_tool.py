# -*- coding: utf-8 -*-
"""守卫：**恢复真的能还原**（2026-10-23 加；"有备份"≠"备份能用"）。

背景：那一晚 dev 数据被清空时，**一份旧快照都没有** —— 于是先做了自动快照。
但"拍了快照"不等于"能还原"：备份文件本身也会坏（半截写入 / 磁盘清理 / 人手误改）。
所以这里钉三件事（全走真子进程 + 真文件）：

1. **拍 → 改 → 还**：还原后文件字节与快照一致；还原前当前状态会被另存一份（`pre-restore`）；
2. **校验**：快照里被人动过 ⇒ `--verify` 必须红，并**点名是哪个文件**；
3. **坏备份不许倒**：`--restore` 对校验不过的快照直接拒绝（防二次损坏）。
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
TOOL = _ROOT / "tools" / "state_restore.py"


def _free_port() -> int:
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])
    finally:
        s.close()


def _run(*args, data: Path):
    env = {**os.environ, "AC_DATA_DIR": str(data), "AC_PORT": str(_free_port()),
           "PYTHONIOENCODING": "utf-8"}
    return subprocess.run([sys.executable, str(TOOL), *args], cwd=str(_ROOT),
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=180, env=env)


@pytest.fixture()
def data_dir(tmp_path):
    d = tmp_path / "data"
    d.mkdir()
    (d / "workshops.json").write_text(
        json.dumps({"ws_a": {"name": "示例工作间-A"}}, ensure_ascii=False, indent=2), encoding="utf-8")
    (d / "tasks.json").write_text(json.dumps({"t1": {"id": "t1"}}, ensure_ascii=False), encoding="utf-8")
    return d


def _snapshot_path(out: str) -> Path:
    """从工具输出里取快照**完整路径**（`--restore` 只要名字，改目录要用全路径）。"""
    for line in out.splitlines():
        if "已拍快照" in line:
            return Path(line.split("已拍快照：")[1].split("（")[0].strip())
    raise AssertionError("没在输出里找到快照路径：%s" % out)


def test_backup_then_restore_roundtrips_bytes(data_dir):
    r = _run("--backup-now", "--reason", "drill", data=data_dir)
    assert r.returncode == 0, r.stdout + r.stderr
    name = _snapshot_path(r.stdout).name

    good = (data_dir / "workshops.json").read_bytes()
    # 模拟"坏掉/清空"：把状态改成别的内容
    (data_dir / "workshops.json").write_text('{"other": true}', encoding="utf-8")

    r2 = _run("--restore", name, data=data_dir)
    assert r2.returncode == 0, r2.stdout + r2.stderr
    assert (data_dir / "workshops.json").read_bytes() == good, "还原后的字节和快照不一致"

    # 还原前的那份当前状态必须另存（否则"倒错了"没法退）
    backups = [p for p in (data_dir / "backups").iterdir() if p.is_dir()]
    assert any("pre-restore" in p.name for p in backups), \
        "没有 pre-restore 快照：%s" % [p.name for p in backups]


def test_verify_detects_corrupted_snapshot(data_dir):
    r = _run("--backup-now", data=data_dir)
    assert r.returncode == 0, r.stdout
    snap_dir = _snapshot_path(r.stdout)
    target = next(p for p in snap_dir.iterdir() if p.name == "workshops.json")
    target.write_text("{}", encoding="utf-8")            # 人为改坏

    v = _run("--verify", data=data_dir)
    assert v.returncode == 1, "坏掉的快照居然校验通过（假绿）：%s" % (v.stdout + v.stderr)
    assert "workshops.json" in v.stdout, "校验失败要点名是哪个文件：%s" % v.stdout


def test_restore_refuses_corrupted_snapshot(data_dir):
    r = _run("--backup-now", data=data_dir)
    snap_dir = _snapshot_path(r.stdout)
    name = snap_dir.name
    (snap_dir / "tasks.json").write_text('{"hacked": 1}', encoding="utf-8")
    original = (data_dir / "tasks.json").read_bytes()

    rr = _run("--restore", name, data=data_dir)
    assert rr.returncode == 1, "坏备份居然被倒进去了：%s" % (rr.stdout + rr.stderr)
    assert "校验不过" in rr.stdout
    assert (data_dir / "tasks.json").read_bytes() == original, "被拒的恢复却改了当前数据"


def test_legacy_snapshot_without_digests_can_still_be_restored(data_dir):
    """**老快照（没有 sha256 记录）不许被当成"坏的"拦下来**。

    实际情况：本次改动之前拍的快照都没有 digests。把它们判成"不可用"会让用户以为备份全废了。
    正确语义：`--verify` 报"无法校验"（退出码 2＝没查成，不是红），`--restore` 给警告后照样还原。
    """
    r = _run("--backup-now", data=data_dir)
    snap_dir = _snapshot_path(r.stdout)
    meta_path = snap_dir / "_meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta.pop("digests", None)                       # 模拟老版本快照（没有指纹）
    meta_path.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")

    v = _run("--verify", data=data_dir)
    assert v.returncode == 2, "无法校验应当是 2（没查成），不是 1（坏）：%s" % (v.stdout + v.stderr)
    assert "无法校验" in v.stdout

    good = (data_dir / "workshops.json").read_bytes()
    (data_dir / "workshops.json").write_text("{}", encoding="utf-8")
    rr = _run("--restore", snap_dir.name, data=data_dir)
    assert rr.returncode == 0, "老快照被误拦了：%s" % (rr.stdout + rr.stderr)
    assert "没法校验" in rr.stdout, "放行时要提示它没法校验：%s" % rr.stdout
    assert (data_dir / "workshops.json").read_bytes() == good
