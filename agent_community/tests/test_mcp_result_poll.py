# -*- coding: utf-8 -*-
"""守卫（`V128`）：**MCP 侧异步结果的自动回收** —— 队列、去向、失败都判真结果。

判据：
  ① 非异步调用**不登记**（别把同步结果塞进待查表）；
  ② 异步但没句柄 ⇒ 直接记"查不了"（不许静默消失）；
  ③ 同一 `url + handle` 幂等（重复登记只留一条）；
  ④ 查到终态 ⇒ 出站表更新为终态、**待查队列出队**；
  ⑤ 挂得上任务（`workshop_id`+`task_seq`）⇒ **真写账本**（`external.source == "mcp"`）；
  ⑥ 账本里没有这条 ⇒ 出站表留着 + `why` 写清（不假装写进账本）；
  ⑦ 对端不支持异步 ⇒ `state=unsupported` + `why` 非空 + 出队（不装"还在跑"）；
  ⑧ 超过 TTL ⇒ `state=expired` 出队（并进出一致：出站表里能查到）；
  ⑨ 出站表有界（超了丢最老）。
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.append(str(_ROOT))

from agent_community.platform import mcp_client as mc  # noqa: E402
from agent_community.platform import mcp_result_poll as MR  # noqa: E402
from agent_community.platform import task_ledger as TL  # noqa: E402


def _seed(handle: str, *, workshop_id: str = "", task_seq: int = 0) -> dict:
    return MR.maybe_track(harness_id="h1", url="http://x/mcp", tool="do",
                          call={"async": True, "handle": handle},
                          workshop_id=workshop_id, task_seq=task_seq)


def test_sync_call_is_not_tracked():
    MR.reset_for_tests()
    assert MR.maybe_track(harness_id="h1", url="http://x/mcp", tool="do",
                          call={"ok": True, "text": "好了"}) is None
    assert MR.list_pending() == []


def test_async_without_handle_is_recorded_as_unsupported():
    MR.reset_for_tests()
    rec = MR.maybe_track(harness_id="h1", url="http://x/mcp", tool="do",
                         call={"async": True, "handle": ""})
    assert rec and rec["state"] == "unsupported" and "没给句柄" in rec["why"]
    assert MR.list_pending() == [], "没句柄就查不了，不该占待查队列"
    assert MR.list_outcalls("h1"), "但要在出站表里留痕，别静默丢掉"


def test_same_handle_is_idempotent():
    MR.reset_for_tests()
    a = _seed("t1")
    b = _seed("t1")
    assert a["call_id"] == b["call_id"]
    assert len(MR.list_pending()) == 1


def test_terminal_writes_outcall_and_dequeues(monkeypatch):
    MR.reset_for_tests()
    _seed("t1")

    async def _fake_get(url, handle, convention=None, timeout=30, args_key="taskId"):
        return {"ok": True, "state": "done", "terminal": True, "text": "活干完了",
                "convention": {"kind": "tasks", "tool": ""}, "raw": {}}

    monkeypatch.setattr(mc, "mcp_task_get", _fake_get, raising=True)
    stats = asyncio.run(MR.sweep_once())
    assert stats["done"] == 1, stats
    assert MR.list_pending() == [], "终态必须出队"
    rec = MR.list_outcalls("h1")[0]
    assert rec["state"] == "done" and "活干完了" in rec["text"], rec


def test_with_task_seq_writes_ledger(monkeypatch):
    MR.reset_for_tests()
    TL.issue("ws-1", 3, slot_dir="_lead", file="任务/t3.md", title="演示")
    _seed("t2", workshop_id="ws-1", task_seq=3)

    async def _fake_get(url, handle, convention=None, timeout=30, args_key="taskId"):
        return {"ok": True, "state": "done", "terminal": True, "text": "产物在这里",
                "convention": {"kind": "tasks"}, "raw": {}}

    monkeypatch.setattr(mc, "mcp_task_get", _fake_get, raising=True)
    asyncio.run(MR.sweep_once())
    rec = TL.get("ws-1", 3) or {}
    assert rec.get("state") == "delivered", rec
    assert (rec.get("external") or {}).get("source") == "mcp", rec
    assert (rec.get("external") or {}).get("state") == "done", rec


def test_ledger_missing_says_so(monkeypatch):
    """账本里没有这条任务 ⇒ 出站表留着 + why 说清，**不许当成写成功**。"""
    MR.reset_for_tests()
    _seed("t3", workshop_id="ws-ghost", task_seq=99)

    async def _fake_get(url, handle, convention=None, timeout=30, args_key="taskId"):
        return {"ok": True, "state": "done", "terminal": True, "text": "x",
                "convention": {"kind": "tasks"}, "raw": {}}

    monkeypatch.setattr(mc, "mcp_task_get", _fake_get, raising=True)
    stats = asyncio.run(MR.sweep_once())
    assert stats["dropped"] == 1, stats
    rec = MR.list_outcalls("h1")[0]
    assert "账本里没有" in (rec.get("why") or ""), rec


def test_unsupported_dequeues_and_says_why(monkeypatch):
    MR.reset_for_tests()
    _seed("t4")

    async def _fake_get(url, handle, convention=None, timeout=30, args_key="taskId"):
        return {"ok": False, "state": "unsupported", "terminal": True, "text": "",
                "error": "它不支持异步任务", "convention": {"kind": ""}}

    monkeypatch.setattr(mc, "mcp_task_get", _fake_get, raising=True)
    stats = asyncio.run(MR.sweep_once())
    assert stats["unsupported"] == 1, stats
    assert MR.list_pending() == []
    rec = MR.list_outcalls("h1")[0]
    assert rec["state"] == "unsupported" and rec["why"], rec


def test_expired_is_dropped_and_visible(monkeypatch):
    MR.reset_for_tests()
    _seed("t5")
    later = 10 ** 10          # 远超 TTL
    stats = asyncio.run(MR.sweep_once(now=later, gap=0))
    assert stats["dropped"] == 1, stats
    assert MR.list_pending() == []
    rec = MR.list_outcalls("h1")[0]
    assert rec["state"] == "expired", rec


def test_outcall_table_is_bounded():
    MR.reset_for_tests()
    for i in range(MR.MAX_OUT + 5):
        MR.maybe_track(harness_id="h1", url="http://x/mcp", tool="do",
                       call={"async": True, "handle": "h%d" % i})
    items = json.loads(Path(MR._out_path()).read_text(encoding="utf-8"))
    assert len(items) == MR.MAX_OUT, len(items)


if __name__ == "__main__":                                        # pragma: no cover
    import pytest

    raise SystemExit(pytest.main([__file__, "-v"]))
