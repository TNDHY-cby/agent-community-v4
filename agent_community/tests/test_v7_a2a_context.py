# -*- coding: utf-8 -*-
"""守卫：**A2A 会话（contextId）轮换** —— 老板 2026-11-01 拍板：

> 一个员工可开多个会话，但是必须在**上下文老化之后**（上下文压缩三四次后）。

守四件事：
  ① **连续性**：同一个员工/工位，正常派活时**复用同一个** contextId（不失忆）；
  ② **老化换新**：到阈值（活数/字数）就换新，**旧的留痕**（seq/用量/为什么换），且**不删**；
  ③ **对端主动要求** 也能触发换新（它回复里带 `[NEED_NEW_CONTEXT]`）；
  ④ **互不影响**：不同员工/工作间/工位各自独立。

⚠️ 诚实边界（写在断言旁边）：平台**看不到对端的"压缩次数"**，所以老化用的是
**平台自己数的代理指标**（派了几条活 / 累计多少字），不依赖对端自报。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.append(str(_ROOT))


@pytest.fixture()
def ctx(tmp_path, monkeypatch):
    monkeypatch.setenv("AC_DATA_DIR", str(tmp_path))
    from agent_community.platform import a2a_context as X

    return X


def test_same_slot_reuses_context(ctx):
    a = ctx.current_for("h1", "WS", "前端组/员工01")
    b = ctx.current_for("h1", "WS", "前端组/员工01")
    assert a["context_id"] and a["context_id"] == b["context_id"], "正常情况要复用，别每次失忆"
    assert b["seq"] == 1


def test_rotates_on_task_threshold_and_keeps_trace(ctx):
    a = ctx.current_for("h1", "WS", "工位A", max_tasks=2)
    ctx.note_used("h1", "WS", "工位A", chars=10)
    ctx.note_used("h1", "WS", "工位A", chars=10)
    b = ctx.current_for("h1", "WS", "工位A", max_tasks=2)
    assert b["rotated"] is True, "派满 2 条就该按老化换新"
    assert b["context_id"] != a["context_id"]
    assert b["seq"] == a["seq"] + 1
    assert "2 条活" in b["why"], "换新理由要说人话（为什么换）"
    v = ctx.view("h1", "WS", "工位A")
    assert v["retired_count"] == 1, "旧会话必须留痕"
    old = v["retired"][0]
    assert old["context_id"] == a["context_id"] and old["tasks"] == 2 and old["why"]


def test_rotates_on_char_threshold(ctx):
    a = ctx.current_for("h1", "WS", "工位B", max_chars=100)
    ctx.note_used("h1", "WS", "工位B", chars=120)
    b = ctx.current_for("h1", "WS", "工位B", max_chars=100)
    assert b["rotated"] is True and b["context_id"] != a["context_id"]
    assert "字" in b["why"]


def test_peer_can_request_rotation(ctx):
    a = ctx.current_for("h1", "WS", "工位C")
    assert ctx.request_rotate("h1", "WS", "工位C", why="对端说该换了") is True
    b = ctx.current_for("h1", "WS", "工位C")
    assert b["rotated"] is True and b["context_id"] != a["context_id"]
    assert "对端" in b["why"]
    # 换完之后不再反复换（标记要清掉，否则每条都换）
    c = ctx.current_for("h1", "WS", "工位C")
    assert c["rotated"] is False and c["context_id"] == b["context_id"]


def test_slots_are_independent(ctx):
    a = ctx.current_for("h1", "WS", "工位X")
    b = ctx.current_for("h1", "WS", "工位Y")
    assert a["context_id"] != b["context_id"], "不同工位不能共用同一个会话"
    ctx.note_used("h1", "WS", "工位X", chars=99999)
    assert ctx.view("h1", "WS", "工位Y")["tasks"] == 0, "一个工位的用量不能算到另一个头上"


def test_context_id_is_ascii(ctx):
    """contextId 会跨实现传输 —— 中文/全角有些对端不收。"""
    a = ctx.current_for("h1", "工作间甲", "前端组/员工01")
    assert a["context_id"].isascii(), "contextId 必须纯 ASCII（拿到 %r）" % a["context_id"]


def test_manual_rotate_now(ctx):
    a = ctx.current_for("h1", "WS", "工位D")
    b = ctx.rotate_now("h1", "WS", "工位D")
    assert b["context_id"] != a["context_id"] and b["seq"] == a["seq"] + 1
