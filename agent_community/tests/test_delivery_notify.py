# -*- coding: utf-8 -*-
"""守卫：**派活顺手通知**（可选层）—— 默认关、失败不许污染派发、原因必须说话。

老板口径（2026-10-07 产品准则⑤ / 2026-10-23「禁止私人化…落地到通用框架」）：
- 往别人界面上打字是**有副作用**的动作 ⇒ **默认关**（自动兜底默认关）；
- 打开后它只是"顺手补一句"，**绝不影响派发结果**；
- 投不进去要**说清原因**（跳过也要有理由），不许静默。

| # | 判据 |
|---|---|
| ① | 默认关：`notify_dispatch` 直接 `skipped=True`，**不调投递层** |
| ② | 开了 + 投递成功：`ok=True` 且记下 `channel`/`verified` |
| ③ | 开了但投递抛异常：`ok=False`，`why` 里明说"不影响派活"（不吞成"成功"） |
| ④ | 没有可投文本：跳过并说清（只有结构化数据时不该硬编一句话） |
| ⑤ | **包装层**：通知层抛异常时，`_dispatch_to_harness` 仍返回原来的派发结果 |
| ⑥ | 取文本优先 `message`，其次 `task_text`/`content`/`text`（口径一致） |
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.append(str(_ROOT))


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("AC_DELIVERY_NOTIFY", raising=False)
    yield


def test_off_by_default_never_calls_delivery(monkeypatch):
    from agent_community.platform import delivery, delivery_notify

    called = []
    monkeypatch.setattr(delivery, "deliver_to_harness",
                        lambda *a, **k: called.append(a) or {"ok": True}, raising=True)
    res = delivery_notify.notify_dispatch("h1", {"message": "干活了"})
    assert res["skipped"] is True and res["ok"] is False
    assert "AC_DELIVERY_NOTIFY=0" in res["why"], res
    assert called == [], "关着的时候一个投递调用都不该发生"


def test_enabled_success_records_channel(monkeypatch):
    from agent_community.platform import delivery, delivery_notify

    monkeypatch.setenv("AC_DELIVERY_NOTIFY", "1")
    monkeypatch.setattr(delivery, "deliver_to_harness",
                        lambda hid, text, **k: {"ok": True, "channel": "cdp", "verified": True,
                                                "detail": "读回核对通过"}, raising=True)
    res = delivery_notify.notify_dispatch("h1", {"message": "干活了"})
    assert res["ok"] is True and res["channel"] == "cdp" and res["verified"] is True, res
    assert res["skipped"] is False


def test_enabled_failure_is_honest_and_harmless(monkeypatch):
    from agent_community.platform import delivery, delivery_notify

    monkeypatch.setenv("AC_DELIVERY_NOTIFY", "1")

    def boom(*a, **k):
        raise RuntimeError("模拟投递层炸了")

    monkeypatch.setattr(delivery, "deliver_to_harness", boom, raising=True)
    res = delivery_notify.notify_dispatch("h1", {"message": "干活了"})
    assert res["ok"] is False and "不影响派活" in res["why"], res


def test_no_text_skips_with_reason(monkeypatch):
    from agent_community.platform import delivery_notify

    monkeypatch.setenv("AC_DELIVERY_NOTIFY", "1")
    res = delivery_notify.notify_dispatch("h1", {"task_id": "t1"})
    assert res["skipped"] is True and "没有可投的文本" in res["why"], res


def test_text_extraction_prefers_message():
    from agent_community.platform import delivery_notify

    assert delivery_notify.text_of({"message": "A", "task_text": "B"}) == "A"
    assert delivery_notify.text_of({"task_text": "B"}) == "B"
    assert delivery_notify.text_of({"content": "C"}) == "C"
    assert delivery_notify.text_of({"text": "D"}) == "D"
    assert delivery_notify.text_of({}) == ""


def test_dispatch_wrapper_survives_notify_crash(monkeypatch):
    """★核心：通知层炸了，派发结果**一字不改**（不许副作用污染主流程）。"""
    from agent_community.platform import delivery_notify, server

    monkeypatch.setattr(server, "_dispatch_core", lambda hid, payload, kind="task": (True, "写进收件箱了"),
                        raising=True)

    def boom(*a, **k):
        raise RuntimeError("模拟通知层崩了")

    monkeypatch.setattr(delivery_notify, "notify_dispatch", boom, raising=True)
    ok, note = server._dispatch_to_harness("h1", {"message": "干活了"})
    assert ok is True and note == "写进收件箱了", (ok, note)
