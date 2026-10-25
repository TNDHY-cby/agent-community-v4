# -*- coding: utf-8 -*-
"""守卫：**逐文件闸** —— 空内容不许覆盖非空磁盘文件（2026-10-24 数据丢失事故后加）。

那次的机制：一台机器上，外部 agent 的诊断进程**只认识 harness**（`workshops` 在它内存里是空的），
它一调 `save_state()`，就把平台真实的 `workshops.json`（7603 字节）覆盖成 `{}`；
而平台自己的全局闸只看"**所有**列表是否都空"，看到它有 2 个 harness 就放行了。

| # | 判据 |
|---|---|
| ① | 新内容是空、磁盘这份非空 ⇒ **跳过这份**，磁盘原样不动，并打一条能看懂的危险提示 |
| ② | 显式 `allow_empty=True`（真清库）⇒ 照写 |
| ③ | 磁盘这份本来就是空的/不存在 ⇒ 照写（别把"本来就是空"也算成危险） |
| ④ | 有内容的新状态照常覆盖（别把闸做成拦路虎） |
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.append(str(_ROOT))


def _seed(tmp: Path, name: str, payload) -> Path:
    p = tmp / name
    p.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return p


def test_empty_payload_does_not_clobber_nonempty_file(tmp_path, capsys):
    from agent_community.platform import state_store as ss

    p = _seed(tmp_path, "workshops.json", {"ws_a": {"name": "真工作间"}})
    before = p.read_text(encoding="utf-8")
    # 新进程内存里 workshops 是空的
    ss.write_items([("workshops.json", lambda: {})], tmp_path)
    assert p.read_text(encoding="utf-8") == before, "磁盘上的真实工作间被覆盖了"
    out = capsys.readouterr().out
    assert "跳过这份" in out and "workshops.json" in out, out


def test_allow_empty_really_clears(tmp_path):
    from agent_community.platform import state_store as ss

    p = _seed(tmp_path, "workshops.json", {"ws_a": {"name": "真工作间"}})
    ss.write_items([("workshops.json", lambda: {})], tmp_path, allow_empty=True)
    assert json.loads(p.read_text(encoding="utf-8")) == {}, "显式允许清库时应照写"


def test_empty_disk_file_is_fine(tmp_path):
    from agent_community.platform import state_store as ss

    p = _seed(tmp_path, "tasks.json", {})
    ss.write_items([("tasks.json", lambda: {})], tmp_path)
    assert json.loads(p.read_text(encoding="utf-8")) == {}


def test_nonempty_payload_still_overwrites(tmp_path):
    from agent_community.platform import state_store as ss

    p = _seed(tmp_path, "harnesses.json", {"old": {"harness_id": "old"}})
    ss.write_items([("harnesses.json", lambda: {"new": {"harness_id": "new"}})], tmp_path)
    assert list(json.loads(p.read_text(encoding="utf-8"))) == ["new"]
