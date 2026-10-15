# -*- coding: utf-8 -*-
"""辩论模式**接线层**的守卫（`V52`）：陈述解析与状态归一必须"失败要说话"。

判定内核的守卫在 `test_debate.py`（19 条）；这里守的是**接口层**：
  · 缺 `claim` / 缺 `speaker` ⇒ **明确拒绝**（裁决要指名道姓，不能匿名）；
  · **没有证据的陈述要收下**（它是合法发言），但结构上如实标成"无证据"；
  · 老数据 / 被写坏的 `debate_state` 不许让端点炸（向后兼容）；
  · 陈述有**上限**（防无限增长）。
"""
from __future__ import annotations

import os
import sys

_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
if _ROOT not in sys.path:
    sys.path.append(_ROOT)

from agent_community.platform.routers import workshops_discuss as wd  # noqa: E402


class _WS:
    def __init__(self, debate_state=None, enabled=False, status="review"):
        self.debate_state = debate_state
        self.debate_enabled = enabled
        self.status = status


def test_statement_requires_claim_and_speaker():
    st, err = wd._parse_statement({"speaker": "示例Harness-A"})
    assert st is None and "claim" in err
    st, err = wd._parse_statement({"claim": "我觉得行"})
    assert st is None and "speaker" in err, "匿名陈述必须拒绝（裁决要指名道姓）"


def test_statement_without_evidence_is_accepted_but_marked():
    st, err = wd._parse_statement({"speaker": "A", "claim": "没问题"})
    assert st is not None and err == ""
    assert st["evidence"] == [], "有发言没证据是常态，要收下（裁决里权重 0，但不删原话）"


def test_statement_normalizes_evidence_entries():
    st, _ = wd._parse_statement({
        "speaker": "A", "claim": "X",
        "evidence": [{"kind": "url", "ref": "https://e.example.com/a", "verified": True},
                     {"ref": "https://e.example.com/b"},
                     "https://e.example.com/c",
                     {"kind": "url", "ref": "   "}],   # 空 ref 要丢掉
    })
    assert [e["ref"] for e in st["evidence"]] == ["https://e.example.com/a",
                                                 "https://e.example.com/b",
                                                 "https://e.example.com/c"]
    assert st["evidence"][0]["verified"] is True
    assert st["evidence"][1]["verified"] is False, "没写 verified 就当没核对过（不假装）"


def test_bad_evidence_type_is_rejected_with_reason():
    st, err = wd._parse_statement({"speaker": "A", "claim": "X", "evidence": "一条"})
    assert st is None and "数组" in err


def test_debate_state_tolerates_missing_and_corrupt_data():
    for bad in (None, {}, {"statements": "坏了"}, {"statements": None}):
        st = wd._debate_state(_WS(debate_state=bad))
        assert isinstance(st["statements"], list) and st["round"] == 0
        assert st["prev_evidence"] == [] and st["last_verdict"] == {}


def test_statement_cap_is_bounded():
    """陈述有上限（保留最近 N 条）—— 无限增长会把状态文件撑爆。"""
    assert isinstance(wd._DEBATE_MAX_STATEMENTS, int) and 10 <= wd._DEBATE_MAX_STATEMENTS <= 500
