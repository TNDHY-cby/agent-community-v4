# -*- coding: utf-8 -*-
"""守卫：**协议目录必须按"层"表达能力，不许把 MCP 说成"最好"**（老板 2026-10-28 更正）。

判据（真读运行时目录，不查源码文本）：
1. 每个协议都有 `layer` 与 `semantics`，且 `layer` 在允许枚举内；
2. **MCP 的层是 `transport_tools`**（工具面），不得被标成任务/会话语义层；
3. A2A / gRPC 的层是 `transport_task`（对等任务语义）——比 MCP 更"协作"，但不含验收；
4. `semantics` 里**不许**出现"MCP 最好/首选/优先"这类排名词（反过来：MCP 的语义里要写明它不表达什么）。
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.append(str(_ROOT))

ALLOWED_LAYERS = {"transport_tools", "transport_task", "transport_session",
                  "transport_generic", "transport_events", "transport_local"}
RANKING_WORDS = ("最好", "首选", "优先", "最强", "第一")


def _catalog():
    from agent_community.platform.protocol_catalog import get_protocol_catalog

    return {p["id"]: p for p in get_protocol_catalog(host="127.0.0.1")}


def test_every_protocol_declares_layer_and_semantics():
    cat = _catalog()
    assert cat, "协议目录是空的"
    for pid, p in cat.items():
        assert p.get("layer") in ALLOWED_LAYERS, "%s 的 layer 不合法：%r" % (pid, p.get("layer"))
        assert str(p.get("semantics") or "").strip(), "%s 缺 semantics 一句话" % pid


def test_mcp_is_tools_layer_and_says_what_it_cannot_do():
    p = _catalog()["mcp"]
    assert p["layer"] == "transport_tools", "MCP 是工具面，不许标成任务/会话层"
    sem = p["semantics"]
    assert ("不表达" in sem) or ("不能" in sem), "MCP 的语义要写明它不表达什么（身份/归属/验收）"


def test_task_semantics_live_in_a2a_and_grpc():
    cat = _catalog()
    assert cat["a2a"]["layer"] == "transport_task"
    assert cat["grpc"]["layer"] == "transport_task"
    assert cat["acp"]["layer"] == "transport_session"


def test_no_ranking_words_in_any_semantics():
    cat = _catalog()
    for pid, p in cat.items():
        sem = str(p.get("semantics") or "") + " " + str(p.get("best_for") or "")
        for w in RANKING_WORDS:
            assert w not in sem, "%s 的说明里出现排名词「%s」" % (pid, w)

# ── 批 D：协议边界必须写死在目录里（不假装支持）────────────────
def test_unary_boundaries_are_declared():
    """gRPC 只一元、WS 不承载回执 —— 目录里必须写出来，别让客户端猜。"""
    cat = _catalog()
    assert "一元" in cat["grpc"]["semantics"], "gRPC 只做一元，要在 semantics 写明"
    ws_sem = cat["ws"]["semantics"]
    assert ("不承载" in ws_sem) or ("单向" in ws_sem), \
        "WS 只推事件，要写明不承载请求/回执"


def test_sse_stays_not_implemented_with_reason():
    p = _catalog()["sse"]
    assert p["status"] == "not_implemented", "SSE 没实现就是没实现"
    assert ("不支持" in p["semantics"]) or ("未实现" in p["semantics"]), \
        "要把'不支持'说在明面上"
