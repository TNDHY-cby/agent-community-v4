# -*- coding: utf-8 -*-
"""**成员回报里的结构化陈述**契约守卫（V52 §8 欠账："回报模板同步"，2026-10-17 补齐）。

为什么要有这一支：`/api/protocol-brief` 是外端 AI 唯一会读的"怎么配合"文档。
它此前只写了一句「建议写成三段：结论/证据/推理」——**没给字段名、没说写在哪**，
成员照它写也进不了辩论池（解析器要的是 `task-result` body **顶层**的 `claim`）。
本支守卫两件事，**都按真结果判**：

1. **简报里的示例必须真的能被解析器接受**（拿 `debate.parse_statement` 真喂一遍）——
   文档与实现一旦分叉，这里当场红（规则 20：不留两份口径）；
2. **简报里的 URL 必须跟着平台地址解析**（不是写死 18920）—— 陌生人换端口跑时，
   简报里每条 URL 都得是对的（反私人定制：不许预设"只有我这台机器"的端口）。
"""
from __future__ import annotations

import asyncio
import os
import sys

_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
if _ROOT not in sys.path:
    sys.path.append(_ROOT)

from agent_community.platform import debate                                  # noqa: E402
from agent_community.platform.routers import protocol_brief as pb            # noqa: E402


class _Req:
    def __init__(self, host="127.0.0.1:18920"):
        self.headers = {"host": host}


def _brief(host="127.0.0.1:18920"):
    return asyncio.run(pb.protocol_brief(_Req(host)))


def _stanza():
    return _brief()["how_to_behave"]["debate_statements"]


# ── ① 示例必须真的能进辩论池（文档 ↔ 解析器一处真相）────────────────────────
def test_brief_example_is_accepted_by_the_real_parser():
    ex = _stanza()["example"]
    st, err = debate.parse_statement(ex, speaker="示例员工")
    assert st is not None, "简报里的示例连自己的解析器都过不了：%s" % err
    assert st["claim"] == ex["claim"]
    assert len(st["evidence"]) == 2 and all(e["verified"] for e in st["evidence"])
    assert st["reasoning"], "示例要带 reasoning（三段里的第三段）"


# ── ② 简报点名的字段 = 解析器真认的字段 ──────────────────────────────────
def test_brief_field_names_match_the_parser():
    fields = set(_stanza()["fields"])
    assert {"claim", "evidence", "reasoning"} <= fields, fields
    # `conflicts_with_checker` 是真认的键 ⇒ 真喂一遍看它是否被采纳
    body = {"claim": "x", "conflicts_with_checker": True}
    st, _err = debate.parse_statement(body, speaker="A")
    assert st is not None and st["conflicts_with_checker"] is True, \
        "简报里写了 conflicts_with_checker，解析器就必须真认它"


# ── ③ 简报承诺"没证据也能发" ⇒ 真喂一遍（且如实记成 verified=false）────────────
def test_brief_promise_no_evidence_still_works():
    text = str(_stanza()["fields"]["evidence"])
    assert "可以不给" in text, "简报必须说清「没证据也能发」"
    st, _err = debate.parse_statement({"claim": "没有证据的观点"}, speaker="A")
    assert st is not None and st["evidence"] == [], "没证据的陈述要收下（权重 0，但话不能丢）"
    st2, _err2 = debate.parse_statement({"claim": "c", "evidence": ["https://e.example.com/a"]},
                                        speaker="A")
    assert st2["evidence"][0] == {"kind": "url", "ref": "https://e.example.com/a",
                                 "verified": False}, "字符串证据要按简报说的那样归一"


# ── ④ 简报承诺"不带 claim = 普通回报" ⇒ 与收集端判据一致 ─────────────────────
def test_brief_promise_absent_claim_is_plain_report():
    assert "向后兼容" in str(_stanza()["fields"]["claim"])
    st, err = debate.parse_statement({"workshop_id": "w", "result": "干完了"}, speaker="A")
    assert st is None and "claim" in err, "没有 claim 就当普通回报：解析器必须拒绝成陈述"
    # 收集端的闸门也是这条判据（`if str(body.get("claim") or "").strip():`）——同一口径
    import inspect
    from agent_community.platform.routers import harness_messaging as hm
    src = inspect.getsource(hm)
    assert 'body.get("claim")' in src, "收集端必须仍以 claim 是否存在为闸门（否则普通回报会被误收）"


# ── ⑤ 简报 URL 跟着平台地址解析（不许写死 18920）────────────────────────────
def test_brief_urls_follow_the_resolved_platform_address(monkeypatch):
    import agent_community.platform_address as pa
    monkeypatch.setattr(pa, "base_url", lambda *a, **k: "http://127.0.0.1:29371", raising=True)
    b = _brief(host="127.0.0.1:29371")
    assert b["platform"]["url"] == "http://127.0.0.1:29371", \
        "简报里的平台地址必须来自唯一的地址解析点（换端口就跑得对）"
    assert "29371" in b["second_channel"]["endpoint"], b["second_channel"]["endpoint"]
    assert ":18920" not in str(b), "不许再有写死的 18920"


def test_brief_source_has_no_hardcoded_port():
    """源码级兜底：**只查真正的字符串常量**（注释里提到历史不算 —— 那条注释是在解释这次修了什么）。

    用 `ast` 扫 `ast.Constant` 的字符串值，比"正则扫全文"诚实：注释/文档串里的 `:18920`
    不是会跑起来的代码；而写进 `f"http://…:18920"` 的会。
    """
    import ast
    path = os.path.join(_ROOT, "agent_community", "platform", "routers", "protocol_brief.py")
    tree = ast.parse(open(path, encoding="utf-8").read())
    found = [n.value for n in ast.walk(tree)
             if isinstance(n, ast.Constant) and isinstance(n.value, str) and ":18920" in n.value]
    assert not found, "protocol_brief.py 的字符串常量里又出现了写死的 18920：%s" % found
