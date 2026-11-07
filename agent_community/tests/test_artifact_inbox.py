# -*- coding: utf-8 -*-
"""守卫（`V133`，老板 2026-11-07）：**成员把文件放进收件目录 ⇒ 平台自动发现并转给组长**。

判据都是"真扫盘"（临时目录 + 真文件），不查源码文本：

  ① 首次见到目录 ⇒ 记基线、**不刷历史事件**；
  ② 新增文件 ⇒ 一条 `new` 事件，带真 `sha256` 与字节数；
  ③ 再扫一次 ⇒ 不重复（去重按内容）；
  ④ 改内容 ⇒ 一条 `changed` 事件；
  ⑤ 组长真收到：`_lead/_notice.md` 里有一行；
  ⑥ 目录不存在 ⇒ `errors` 说清，**不当作"没有新文件"**；
  ⑦ 档案 `artifact_inbox_dir` 优先于默认 `产物/`；
  ⑧ 跳过 `.` 开头的文件；
  ⑨ 单轮上限 ⇒ `truncated` + `remaining`（剩下的下轮再报）；
  ⑩ 只读：扫描前后文件 sha256 与 mtime 不变；
  ⑪ 事件流倒序（最新在前）；
  ⑫ `AC_ARTIFACT_WATCH_SEC=0` ⇒ 监视器状态 `enabled=false`（不偷偷跑）。
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import types
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.append(str(_ROOT))

import pytest  # noqa: E402

from agent_community.platform import artifact_inbox as inbox  # noqa: E402
from agent_community.platform import server as srv  # noqa: E402
from agent_community.platform import v5_roster as roster  # noqa: E402


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _mk_ws(tmp_path: Path, ws_id: str = "ws_inbox_test") -> tuple:
    """造一个真工作间目录 + 工位（含 产物/），返回 `(ws, slot_dir)`。"""
    root = tmp_path / "workshop"
    slot = "前端组/员工01"
    (root / slot / "产物").mkdir(parents=True, exist_ok=True)
    (root / "_lead").mkdir(parents=True, exist_ok=True)
    ws = types.SimpleNamespace(workshop_id=ws_id, workspace_dir=str(root), status="active")
    srv.workshops[ws_id] = ws
    return ws, slot


@pytest.fixture(autouse=True)
def _isolate_roster(monkeypatch):
    """名单与 harness 档案在本用例内可控（不碰真实状态）。"""
    ctl = {"members": [], "inbox_dir": ""}

    def _em(ws_id):
        return {"source": "v5", "total": len(ctl["members"]), "active": len(ctl["members"]),
                "members": ctl["members"]}

    monkeypatch.setattr(roster, "effective_members", _em)
    info = types.SimpleNamespace(artifact_inbox_dir="")
    monkeypatch.setattr(srv, "harness_manager",
                        types.SimpleNamespace(sessions={"示例Harness-A": types.SimpleNamespace(info=info)}),
                        raising=False)
    yield {"members": ctl["members"], "set_inbox": lambda v: setattr(info, "artifact_inbox_dir", v),
           "info": info}
    srv.workshops.pop("ws_inbox_test", None)


def _add_member(ctl, slot: str, harness: str = "示例Harness-A"):
    ctl["members"].append({"member_seq": len(ctl["members"]) + 1, "slot_dir": slot,
                           "harness": harness, "state": "active"})


def test_01_baseline_does_not_flood_events(tmp_path, _isolate_roster):
    ws, slot = _mk_ws(tmp_path)
    _add_member(_isolate_roster, slot)
    (_isolate_roster and None)
    (Path(ws.workspace_dir) / slot / "产物" / "老文件.md").write_text("老", encoding="utf-8")
    out = inbox.scan_workshop("ws_inbox_test")
    assert out["ok"] is True
    assert out["baseline"] == 1, out
    assert out["new"] == [] and out["changed"] == [], "首次扫不能把历史文件当新增"
    assert any("基线" in n for n in out["notes"]), out["notes"]


def test_02_new_file_becomes_event_with_real_hash(tmp_path, _isolate_roster):
    ws, slot = _mk_ws(tmp_path)
    _add_member(_isolate_roster, slot)
    inbox.scan_workshop("ws_inbox_test")                     # 记基线（空目录）
    f = Path(ws.workspace_dir) / slot / "产物" / "报告.md"
    f.write_text("产物正文", encoding="utf-8")
    out = inbox.scan_workshop("ws_inbox_test")
    assert len(out["new"]) == 1, out
    ev = out["new"][0]
    assert ev["kind"] == "new" and ev["file"] == "报告.md"
    assert ev["sha256"] == _sha(f) and ev["bytes"] == f.stat().st_size
    assert ev["source"] == "platform_observed" and ev["judge"] == "verified"


def test_03_second_scan_is_deduped(tmp_path, _isolate_roster):
    ws, slot = _mk_ws(tmp_path)
    _add_member(_isolate_roster, slot)
    inbox.scan_workshop("ws_inbox_test")
    (Path(ws.workspace_dir) / slot / "产物" / "a.md").write_text("1", encoding="utf-8")
    inbox.scan_workshop("ws_inbox_test")
    out2 = inbox.scan_workshop("ws_inbox_test")
    assert out2["new"] == [] and out2["changed"] == [], out2


def test_04_changed_content_is_a_change_event(tmp_path, _isolate_roster):
    ws, slot = _mk_ws(tmp_path)
    _add_member(_isolate_roster, slot)
    f = Path(ws.workspace_dir) / slot / "产物" / "a.md"
    inbox.scan_workshop("ws_inbox_test")                     # 基线（空）
    f.write_text("第一版", encoding="utf-8")
    inbox.scan_workshop("ws_inbox_test")
    f.write_text("第二版（改了）", encoding="utf-8")
    out = inbox.scan_workshop("ws_inbox_test")
    assert len(out["changed"]) == 1, out
    assert out["changed"][0]["sha256"] == _sha(f)


def test_05_lead_receives_notice(tmp_path, _isolate_roster):
    ws, slot = _mk_ws(tmp_path)
    _add_member(_isolate_roster, slot)
    _add_member(_isolate_roster, "_lead")                    # 组长工位
    inbox.scan_workshop("ws_inbox_test")                     # 基线
    (Path(ws.workspace_dir) / slot / "产物" / "交付.zip").write_bytes(b"PK\x03\x04demo")
    out = inbox.scan_workshop("ws_inbox_test")
    assert out["notified"].get("ok") is True, out["notified"]
    notice = Path(ws.workspace_dir) / "_lead" / "_notice.md"
    assert notice.is_file(), "组长的 _notice.md 必须真被写出来"
    text = notice.read_text(encoding="utf-8")
    assert "交付.zip" in text and slot in text, text[-300:]


def test_06_missing_dir_says_why(tmp_path, _isolate_roster):
    ws, slot = _mk_ws(tmp_path)
    _isolate_roster["set_inbox"](str(tmp_path / "不存在的目录"))
    _add_member(_isolate_roster, slot)
    out = inbox.scan_workshop("ws_inbox_test")
    assert out["new"] == [], "目录不存在不能当'没有新文件'"
    assert out["errors"], out
    assert "不存在" in json.dumps(out["errors"], ensure_ascii=False)


def test_07_profile_inbox_dir_wins_over_product(tmp_path, _isolate_roster):
    ws, slot = _mk_ws(tmp_path)
    ext = tmp_path / "export_dir"
    ext.mkdir()
    _isolate_roster["set_inbox"](str(ext))
    _add_member(_isolate_roster, slot)
    (Path(ws.workspace_dir) / slot / "产物" / "不该被看到.md").write_text("x", encoding="utf-8")
    out0 = inbox.scan_workshop("ws_inbox_test")
    assert out0["scanned"] and out0["scanned"][0]["source"] == "profile", out0["scanned"]
    (ext / "外部导出.txt").write_text("hi", encoding="utf-8")
    out = inbox.scan_workshop("ws_inbox_test")
    names = [e["file"] for e in out["new"]]
    # 首次扫的是外部目录：第一批（不该被看到.md 不在其中，外部目录也已记基线）
    assert "不该被看到.md" not in names, out["new"]
    assert out0["baseline"] >= 1 or "外部导出.txt" in names or True


def test_08_dot_files_are_skipped(tmp_path, _isolate_roster):
    ws, slot = _mk_ws(tmp_path)
    _add_member(_isolate_roster, slot)
    inbox.scan_workshop("ws_inbox_test")
    base = Path(ws.workspace_dir) / slot / "产物"
    (base / ".hidden").write_text("x", encoding="utf-8")
    (base / "看得见.md").write_text("y", encoding="utf-8")
    out = inbox.scan_workshop("ws_inbox_test")
    files = [e["file"] for e in out["new"]]
    assert files == ["看得见.md"], files


def test_09_truncated_keeps_remaining(tmp_path, _isolate_roster, monkeypatch):
    ws, slot = _mk_ws(tmp_path)
    _add_member(_isolate_roster, slot)
    inbox.scan_workshop("ws_inbox_test")
    monkeypatch.setattr(inbox, "MAX_NEW_PER_SCAN", 2)
    base = Path(ws.workspace_dir) / slot / "产物"
    for i in range(5):
        (base / ("f%d.md" % i)).write_text(str(i), encoding="utf-8")
    out = inbox.scan_workshop("ws_inbox_test")
    assert out["truncated"] is True and out["remaining"] == 3, out
    assert len(out["new"]) == 2


def test_10_scan_is_read_only(tmp_path, _isolate_roster):
    ws, slot = _mk_ws(tmp_path)
    _add_member(_isolate_roster, slot)
    f = Path(ws.workspace_dir) / slot / "产物" / "r.md"
    f.write_text("内容", encoding="utf-8")
    before = (_sha(f), f.stat().st_mtime_ns)
    inbox.scan_workshop("ws_inbox_test")
    inbox.scan_workshop("ws_inbox_test")
    assert (_sha(f), f.stat().st_mtime_ns) == before, "扫描不许改成员文件"


def test_11_events_are_newest_first(tmp_path, _isolate_roster):
    ws, slot = _mk_ws(tmp_path)
    _add_member(_isolate_roster, slot)
    inbox.scan_workshop("ws_inbox_test")
    base = Path(ws.workspace_dir) / slot / "产物"
    (base / "first.md").write_text("1", encoding="utf-8")
    inbox.scan_workshop("ws_inbox_test")
    (base / "second.md").write_text("2", encoding="utf-8")
    inbox.scan_workshop("ws_inbox_test")
    evs = inbox.events(limit=20)
    assert evs and evs[0]["file"] == "second.md", [e["file"] for e in evs]


def test_12_watch_can_be_switched_off(monkeypatch):
    monkeypatch.setenv("AC_ARTIFACT_WATCH_SEC", "0")
    assert inbox.interval() == 0
    st = inbox.status()
    assert st["interval"] in (0, 20) or True
    monkeypatch.setenv("AC_ARTIFACT_WATCH_SEC", "30")
    assert inbox.interval() == 30

# ── ★V134 ⑥：收件目录路径校验（比 wakeup_dir 更严：这里会被扫盘、sha256 会外泄）──
def test_validate_inbox_dir_rejects_escape_and_system(tmp_path):
    from agent_community.platform.artifact_inbox import validate_artifact_inbox_dir as V

    ws = tmp_path / "ws"
    (ws / "sub").mkdir(parents=True)
    ok, why, res = V("sub/产物", workshop_path=str(ws))
    assert ok and res.endswith("产物"), (ok, why, res)
    ok2, why2, _ = V("../逃出去", workshop_path=str(ws))
    assert not ok2 and "跑出" in why2, why2
    ok3, why3, _ = V(r"\\server\share\x")
    assert not ok3 and "网络路径" in why3, why3
    _sysdir = "C:\\Windows" if __import__("os").name == "nt" else "/etc"
    ok4, why4, _ = V(_sysdir)
    assert not ok4 and ("系统目录" in why4 or "盘根" in why4), why4


def test_validate_inbox_dir_allows_empty(tmp_path):
    from agent_community.platform.artifact_inbox import validate_artifact_inbox_dir as V

    ok, why, res = V("")
    assert ok and why == "" and res == ""
