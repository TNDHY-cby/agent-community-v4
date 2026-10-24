# -*- coding: utf-8 -*-
"""大厅广播通知（`/api/hall/notice`）的三条判据（2026-10-23）。

为什么有：老板要「大厅做广播通知之类的功能」。这个端点小，但**最容易做错的地方**是
"另造一套通知存储"（刷新后两份口径打架）与"什么都不拒绝"（空文本、超长、乱级别都收）。

判据：
  ① 发一条真的进大厅（`hall_messages` 里能查到，且带 `payload.message_type=notice`）；
  ② 空文本 / 超长 / 非法级别 ⇒ **400 + 中文原因**（不是静默成功）；
  ③ 读取端点只读、按条数取最近的，且**不依赖第二次存储**（数据源与①同源）。
"""
from __future__ import annotations

import asyncio
import os
import sys

_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
if _ROOT not in sys.path:
    sys.path.append(_ROOT)

from agent_community.platform.routers import hall  # noqa: E402


class _Req:
    """最小的 Request 替身：端点只用 `await request.json()`。"""

    def __init__(self, body):
        self._body = body

    async def json(self):
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


def _run(coro):
    return asyncio.run(coro)


def _payload(resp):
    """端点成功时返回 dict（交给 FastAPI 序列化），失败时返回 Utf8JSONResponse —— 两种都要认。"""
    import json

    if isinstance(resp, dict):
        return resp
    return json.loads(bytes(resp.body).decode("utf-8"))


def _status(resp):
    return 200 if isinstance(resp, dict) else resp.status_code


def _hall_messages():
    from agent_community.platform import server

    return server.hall_messages


def test_notice_is_appended_to_the_hall():
    before = len(_hall_messages())
    resp = _run(hall.hall_post_notice(_Req({"text": "今晚 20:00 统一停机维护 10 分钟", "level": "warn"})))
    data = _payload(resp)
    assert _status(resp) == 200 and data["ok"] is True, data
    assert data["notice"]["level"] == "warn" and data["notice"]["level_text"] == "提醒"
    msgs = _hall_messages()
    assert len(msgs) == before + 1
    last = msgs[-1]
    assert last.content == "今晚 20:00 统一停机维护 10 分钟"
    assert last.from_agent == "platform" and last.to_agent == "broadcast"
    assert (last.payload or {}).get("message_type") == "notice"
    assert (last.payload or {}).get("level") == "warn"


def test_bad_input_is_rejected_with_reason():
    cases = [
        ({"text": "   "}, "空的"),
        ({"text": "x" * (hall.MAX_TEXT + 1)}, "最多"),
        ({"text": "正常内容", "level": "loud"}, "级别"),
        (ValueError("坏 JSON"), "空的"),
    ]
    for body, keyword in cases:
        resp = _run(hall.hall_post_notice(_Req(body)))
        data = _payload(resp)
        assert resp.status_code == 400, (body, resp.status_code, data)
        assert data["ok"] is False and keyword in data["error"], (body, data)


def test_list_reads_the_same_source_and_is_bounded():
    _run(hall.hall_post_notice(_Req({"text": "第一条广播"})))
    _run(hall.hall_post_notice(_Req({"text": "第二条广播"})))
    resp = _run(hall.hall_list_notices(limit=1))
    data = _payload(resp)
    assert data["ok"] is True
    assert len(data["notices"]) == 1, data            # limit 真的被遵守
    assert data["notices"][0]["text"] == "第二条广播"  # 取最近的一条
    assert data["total"] >= 2
    # 超出范围 / 乱参数不许炸
    for bad in (0, -5, 10 ** 6, "abc"):
        d = _payload(_run(hall.hall_list_notices(limit=bad)))
        assert d["ok"] is True and 1 <= len(d["notices"]) <= 100, (bad, d.get("total"))
