# -*- coding: utf-8 -*-
"""守卫：**状态落盘的防护**（2026-10-23 数据被清空事故后加）。

事故（真事）：dev 的 `agent_community/data/*.json` 在 00:00:33–34 被同时写成空 `{}` ——
`tasks / rooms / task_to_room / harnesses / workshops / state_machine / interjects` 全清空。
机制：`save_state()` 是"内存全量写盘"，写出空 `{}` ⇒ **调它的那个进程内存是空的**
（单实例锁本该挡住，但 `AC_SINGLETON_UNAVAILABLE=degraded` 是唯一例外通道）。

⇒ 两条硬判据（都按真文件、真输出判，不查源码文本）：
1. **降级实例不许写盘**：`save_state()` 直接返回 False，磁盘上的文件**一个字节都不变**，
   并且（每进程一次）把"为什么拒写"打出来；
2. **空状态但有历史痕迹 ⇒ 启动时必须喊**：状态 0 任务/0 工作间，而数据目录里还有
   工作区目录或 audit 历史 ⇒ 打 `[persist][警告]`，说清"可能被清空过 / 先确认只有一个实例 / 先备份再恢复"。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.append(str(_ROOT))

from agent_community.platform import server as srv  # noqa: E402


def test_degraded_instance_refuses_to_write_state(tmp_path, monkeypatch, capsys):
    """降级实例写盘 = 和持有者互相覆盖 ⇒ 必须拒写，且磁盘一字节不变。"""
    data = tmp_path / "data"
    data.mkdir()
    target = data / "workshops.json"
    original = json.dumps({"ws_keep": {"name": "别动我"}}, ensure_ascii=False, indent=2)
    target.write_text(original, encoding="utf-8")

    monkeypatch.setattr(srv, "DATA_DIR", data, raising=True)
    from agent_community.platform import data_health as dh

    monkeypatch.setattr(dh, "DEGRADED", True, raising=True)
    monkeypatch.setattr(dh, "WARNED", False, raising=True)

    ok = srv.save_state()

    assert ok is False, "降级实例不该报告写盘成功"
    assert target.read_text(encoding="utf-8") == original, "降级实例把真实状态文件改掉了！"
    out = capsys.readouterr().out
    assert "降级实例" in out and "拒绝写盘" in out, out


def test_empty_state_with_history_warns_on_startup(tmp_path, monkeypatch, capsys):
    """状态空 + 目录里有历史痕迹 ⇒ 启动必须**大声说**（那次平台是"安静地"起来的）。"""
    data = tmp_path / "data"
    (data / "workshops" / "ws_deadbeef").mkdir(parents=True)
    for name in ("tasks.json", "rooms.json", "task_to_room.json",
                 "harnesses.json", "workshops.json"):
        (data / name).write_text("{}", encoding="utf-8")
    (data / "audit.json").write_text("[" + "{}," * 500 + "]", encoding="utf-8")   # 造点体积

    # load_state 是往模块全局里填，先清空（并换掉 harness_manager），
    # 免得别的用例留下的数据把"空状态"这个前提变成"非空"（双平台真跑抓到过这种假红）。
    monkeypatch.setattr(srv, "DATA_DIR", data, raising=True)
    _empty_everything(monkeypatch)

    srv.load_state()

    out = capsys.readouterr().out
    assert "[persist][警告]" in out, "状态空了却没告警：%s" % out
    assert "历史痕迹" in out, out
    assert "备份" in out or "audit.json" in out, "告警要给去哪儿找/怎么救：%s" % out
    # 同一份事实也要能**被界面读到**（不靠翻日志）：/api/status 有 data_health
    dh = srv.data_health()
    assert dh["empty_but_history"] is True, dh
    assert dh["workspace_dirs"] >= 1 and dh["audit_kb"] >= 1, dh


def test_healthy_startup_is_quiet(tmp_path, monkeypatch, capsys):
    """正常有数据时**不许**吵（别把用户吵麻了：告警只在"空 + 有历史"时出现）。"""
    data = tmp_path / "data"
    (data / "workshops").mkdir(parents=True)
    (data / "workshops.json").write_text(
        json.dumps({"ws_1": {"name": "示例工作间-A"}}, ensure_ascii=False), encoding="utf-8")

    monkeypatch.setattr(srv, "DATA_DIR", data, raising=True)
    _empty_everything(monkeypatch)

    srv.load_state()

    out = capsys.readouterr().out
    assert "[persist][警告]" not in out, out
    assert srv.data_health()["empty_but_history"] is False, srv.data_health()


def test_status_exposes_data_health():
    """/api/status 必须带 `data_health`（界面显示横幅的唯一来源，别让它去翻日志）。"""
    from fastapi.testclient import TestClient

    d = TestClient(srv.app, client=("127.0.0.1", 54321)).get("/api/status").json()
    assert "data_health" in d, list(d)
    dh = d["data_health"]
    for k in ("empty_but_history", "state_empty", "workspace_dirs", "audit_kb"):
        assert k in dh, (k, dh)


# ── ② 空内存覆盖保护（2026-10-23 事故的直接机制）────────────────────────
class _EmptyMgr:
    """空 harness 管理器：把"0 个 harness"这个前提**自己保证**（别让别的用例影响本用例）。"""

    sessions = {}

    def register(self, info):
        raise AssertionError("这条用例不该注册 harness")


def _empty_everything(monkeypatch):
    """把 server 的四个状态集合都换成空 —— 快照/落盘判据都靠"空状态"这个前提。"""
    monkeypatch.setattr(srv, "tasks", {}, raising=True)
    monkeypatch.setattr(srv, "discussion_rooms", {}, raising=True)
    monkeypatch.setattr(srv, "workshops", {}, raising=True)
    monkeypatch.setattr(srv, "harness_manager", _EmptyMgr(), raising=True)


def _disk_with_data(tmp: Path) -> Path:
    """造一个"磁盘上有数据"的数据目录。"""
    data = tmp / "data"
    data.mkdir()
    (data / "workshops.json").write_text(
        json.dumps({"ws_keep": {"name": "别动我"}}, ensure_ascii=False, indent=2), encoding="utf-8")
    (data / "harnesses.json").write_text(
        json.dumps({"示例Harness-A": {"harness_id": "示例Harness-A"}}, ensure_ascii=False), encoding="utf-8")
    return data


def test_empty_memory_cannot_overwrite_disk_data(tmp_path, monkeypatch, capsys):
    """★核心：**没加载过状态**的进程（内存全空）不许把磁盘上的数据写成 `{}` —— 那次事故就这么发生的。"""
    data = _disk_with_data(tmp_path)
    before = {p.name: p.read_bytes() for p in data.glob("*.json")}

    monkeypatch.setattr(srv, "DATA_DIR", data, raising=True)
    _empty_everything(monkeypatch)
    monkeypatch.setattr(srv, "_STATE_OWNED", False, raising=True)
    monkeypatch.setattr(srv, "_EMPTY_WARNED", False, raising=True)
    monkeypatch.delenv("AC_STATE_ALLOW_EMPTY", raising=False)

    ok = srv.save_state()

    assert ok is False, "空内存应当被拒写"
    after = {p.name: p.read_bytes() for p in data.glob("*.json")}
    assert after == before, "磁盘上的数据被空状态覆盖了（这就是那次事故）"
    out = capsys.readouterr().out
    assert "拒绝用空状态覆盖" in out, out
    assert "AC_STATE_ALLOW_EMPTY" in out, "要告诉用户怎么才放行：%s" % out


def test_loaded_state_may_write_even_if_now_empty(tmp_path, monkeypatch):
    """加载过状态（或写过一次）之后，列表变空是**正常删除**，必须允许落盘（别把删除卡住）。"""
    data = _disk_with_data(tmp_path)
    monkeypatch.setattr(srv, "DATA_DIR", data, raising=True)
    _empty_everything(monkeypatch)
    monkeypatch.setattr(srv, "_STATE_OWNED", True, raising=True)      # 等价于"load_state 过了"
    monkeypatch.setattr(srv, "_EMPTY_WARNED", False, raising=True)

    ok = srv.save_state()

    assert ok is True, "加载过状态的进程应当能写（否则用户删不干净）"
    written = json.loads((data / "workshops.json").read_text(encoding="utf-8"))
    assert written == {}, "清空后的状态没落盘：%s" % written


def test_allow_empty_env_overrides_the_guard(tmp_path, monkeypatch):
    """故意清库（首次运行 / 手工重置）必须有明确口子：`AC_STATE_ALLOW_EMPTY=1`。"""
    data = _disk_with_data(tmp_path)
    monkeypatch.setattr(srv, "DATA_DIR", data, raising=True)
    _empty_everything(monkeypatch)
    monkeypatch.setattr(srv, "_STATE_OWNED", False, raising=True)
    monkeypatch.setattr(srv, "_EMPTY_WARNED", False, raising=True)
    monkeypatch.setenv("AC_STATE_ALLOW_EMPTY", "1")

    assert srv.save_state() is True
    assert json.loads((data / "workshops.json").read_text(encoding="utf-8")) == {}
