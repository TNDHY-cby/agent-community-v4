# -*- coding: utf-8 -*-
"""守卫：**A2A 落地**（`V119` 批 B1~B4）—— 单元级。

分工说明（工作模式②）：
- 这里测**映射与判定逻辑**（纯函数 + 少量桩），跑得快、能定位；
- **真正的端到端**由 `tools/protocol_peer_check.py` 在临时实例 + 真 A2A sidecar 上跑（22 格）。
两处都要绿，缺一不算。

⚠️ 桩只替换"HTTP 调用"这一个边界（`_get`/`_post`），**不替换被测逻辑本身**。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.append(str(_ROOT))

from agent_community import a2a_server as A  # noqa: E402


# ── B3：账本 → A2A 状态（一一对应，不猜）─────────────────────────
@pytest.mark.parametrize("state,expect", [
    ("accepted", "completed"), ("rejected", "failed"),
    ("rework", "input-required"), ("", "working"), ("issued", "working"),
])
def test_status_from_review_is_one_to_one(state, expect):
    assert A._status_from_review({"state": state}, "working") == expect


# ── B1：产物同源（v5 目录 diff 优先，按名去重）───────────────────
def test_merge_artifacts_prefers_v5_and_dedups(monkeypatch):
    monkeypatch.setattr(A, "_v5_artifacts",
                        lambda ws: [{"name": "前端组/员工01/a.md",
                                     "metadata": {"sha256": "abc", "source": "v5_dir_diff"}}],
                        raising=True)
    monkeypatch.setattr(A, "_v4_artifacts",
                        lambda ws: [{"name": "前端组/员工01/a.md", "metadata": {"member_id": "x"}},
                                    {"name": "后端组/员工02/b.md", "metadata": {"member_id": "y"}}],
                        raising=True)
    got = A._merge_artifacts("WS-T")
    names = [x["name"] for x in got]
    assert names == ["前端组/员工01/a.md", "后端组/员工02/b.md"], "同名要去重，且 v5 的优先"
    assert got[0]["metadata"]["sha256"] == "abc"


def test_task_to_a2a_carries_artifacts_and_review(monkeypatch):
    monkeypatch.setattr(A, "_merge_artifacts",
                        lambda ws: [{"name": "x.md", "parts": [],
                                     "metadata": {"sha256": "s1"}}], raising=True)
    obj = A._task_to_a2a("WS-T", {"title": "接口"}, "working", a2a_id="t1",
                         review={"state": "accepted", "evidence_level": "L1", "note": "ok"})
    assert obj["id"] == "t1"
    assert obj["status"] == "completed", "验收通过 ⇒ completed（按账本映射）"
    assert obj["artifacts"] and obj["artifacts"][0]["metadata"]["sha256"] == "s1"
    assert obj["metadata"]["review"]["evidence_level"] == "L1"


def test_no_review_means_no_fake_review(monkeypatch):
    monkeypatch.setattr(A, "_merge_artifacts", lambda ws: [], raising=True)
    obj = A._task_to_a2a("WS-T", {}, "working", a2a_id="t2", review={})
    assert obj["metadata"]["review"] is None, "没验收就说没有，不许编一个"
    assert obj["status"] == "working"


# ── B4：未声明能力不许调（fail-closed）──────────────────────────
def test_send_rejects_undeclared_skill(monkeypatch):
    monkeypatch.setattr(A, "_declared_skill_ids", lambda: {"platform:review"}, raising=True)
    out = A.handle_rpc({"method": "tasks/send", "id": 1,
                        "params": {"skillId": "谁也没声明过",
                                   "message": {"parts": [{"text": "干活"}]}}}, "h", 1)
    assert out["error"]["code"] == -32004
    assert "未声明能力" in out["error"]["message"]
    assert "platform:review" in out["error"]["message"], "要告诉它能用哪些"


def test_send_accepts_declared_skill(monkeypatch):
    monkeypatch.setattr(A, "_declared_skill_ids", lambda: {"platform:review"}, raising=True)
    monkeypatch.setitem(A._TASK_TO_WS, "t9", "WS-T")
    # 桩掉 HTTP 边界：工作间存在 + 产物/验收为空（**不连本机平台**）
    monkeypatch.setattr(A, "_get",
                        lambda path, timeout=20: {"workshop_id": "WS-T"}, raising=True)
    monkeypatch.setattr(A, "_v5_review", lambda ws, tid="": {}, raising=True)
    monkeypatch.setattr(A, "_merge_artifacts", lambda ws: [], raising=True)
    out = A.handle_rpc({"method": "tasks/send", "id": 2,
                        "params": {"skillId": "platform:review", "taskId": "t9",
                                   "workshopId": "WS-T",
                                   "message": {"parts": [{"text": "干活"}]}}}, "h", 1)
    assert "error" not in out, out
    assert out["result"]["id"] == "t9"


def test_attach_to_unknown_workshop_is_rejected(monkeypatch):
    monkeypatch.setattr(A, "_get", lambda path, timeout=20: {"error": "404"}, raising=True)
    out = A.handle_rpc({"method": "tasks/send", "id": 3,
                        "params": {"workshopId": "WS-NOPE",
                                   "message": {"parts": [{"text": "干活"}]}}}, "h", 1)
    assert out["error"]["code"] == -32004
    assert "workshopId" in out["error"]["message"]


# ── B2：长轮询（超时要如实说没变）────────────────────────────────
def test_watch_timeout_says_unchanged(monkeypatch):
    monkeypatch.setattr(A, "_task_to_a2a",
                        lambda *a, **k: {"id": "t1", "status": "working", "artifacts": [],
                                         "metadata": {"review": None}}, raising=True)
    monkeypatch.setattr(A, "_TASK_TO_WS", {"t1": "WS-T"}, raising=False)
    monkeypatch.setattr(A, "_v5_review", lambda ws, tid="": {}, raising=True)
    monkeypatch.setattr(A, "_get", lambda path, timeout=20: {}, raising=True)
    out = A._watch_task("t1", 0.6)
    assert out["metadata"]["changed"] is False
    assert "长轮询" in out["metadata"]["wait_note"]


def test_watch_returns_immediately_when_snapshot_differs(monkeypatch):
    monkeypatch.setattr(A, "_task_to_a2a",
                        lambda *a, **k: {"id": "t2", "status": "completed", "artifacts": [],
                                         "metadata": {"review": None}}, raising=True)
    monkeypatch.setattr(A, "_TASK_TO_WS", {"t2": "WS-T"}, raising=False)
    monkeypatch.setattr(A, "_v5_review", lambda ws, tid="": {}, raising=True)
    monkeypatch.setattr(A, "_get", lambda path, timeout=20: {}, raising=True)
    A._TASK_SNAPSHOT["t2"] = ("working", "", "{}")     # 装作上一次是 working
    out = A._watch_task("t2", 5)
    assert out["metadata"]["changed"] is True, "状态真变了就该立刻返回，不许等到超时"


# ── 卡片必须如实声明边界 ───────────────────────────────────────
def test_card_declares_long_poll_and_no_streaming():
    card = A._agent_card("127.0.0.1", 9104)
    assert card["capabilities"]["streaming"] is False, "我们不做流式，卡上不许说支持"
    lp = card["capabilities"]["statusLongPoll"]
    assert lp["supported"] is True and lp["method"] == "tasks/watch"
    assert "长轮询" in lp["note"] and "不假装" in lp["note"]
    assert card["skills"], "能力清单不能空（否则 B4 的 fail-closed 会全拒）"
