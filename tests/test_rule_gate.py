"""RuleGate 规则闸门单元测试 — 纯函数测试，零 LLM 调用。

运行：
    pytest tests/test_rule_gate.py -q
"""

from __future__ import annotations
import pytest

from agent_community.platform.rule_gate import RuleGate


def make_gate(**kwargs) -> RuleGate:
    return RuleGate(**kwargs)


# ── 基本命中 ────────────────────────────────────────────────

@pytest.mark.parametrize("text,kind", [
    ("你好", "greeting"),
    ("在吗", "status"),
    ("什么版本", "version"),
    ("帮助", "help"),
    ("现在几点", "time"),
    ("谢谢", "thanks"),
    ("再见", "bye"),
    ("好的", "ack"),
    ("", "empty"),
    ("。。", "empty"),
])
def test_basic_hits(text, kind):
    gate = make_gate()
    hit = gate.match(text)
    assert hit is not None
    assert hit.kind == kind
    assert hit.reply


# ── 不应误伤（低置信度放行 LLM）────────────────────────────

@pytest.mark.parametrize("text", [
    "你好，帮我分析一下这段代码的逻辑",
    "在吗？帮我看看昨天的日志",
    "请问版本更新后有什么新功能？",
    "谢谢你的配合，接下来我们开始正式任务",
    "帮我写一篇关于时间的文章",
])
def test_no_false_positive(text):
    gate = make_gate()
    assert gate.match(text) is None


# ── 触发词（测试关键词，仅简单回复）────────────────────────

def test_trigger_keyword_hit():
    gate = make_gate(trigger_keywords=["打卡测试"])
    hit = gate.match("打卡测试")
    assert hit is not None
    assert hit.kind == "trigger"
    assert hit.reply == "收到"


def test_trigger_keyword_priority_over_greeting():
    gate = make_gate(trigger_keywords=["你好"])
    hit = gate.match("你好")
    assert hit.kind == "trigger"


def test_trigger_keyword_absent():
    gate = make_gate(trigger_keywords=[])
    assert gate.match("打卡测试") is None


# ── 简单算术（安全 eval）──────────────────────────────────

@pytest.mark.parametrize("expr,expected", [
    ("1+1", "2.0"),
    ("2*3+4", "10.0"),
    ("10/4", "2.5"),
    ("(2+3)*4", "20.0"),
])
def test_math(expr, expected):
    gate = make_gate()
    hit = gate.match(expr)
    assert hit is not None
    assert hit.kind == "math"
    assert hit.reply == expected


@pytest.mark.parametrize("evil", [
    "__import__('os').system('echo hi')",
    "1; import os",
    "open('x')",
    "1+1 if True else 2",
])
def test_math_rejects_evil(evil):
    gate = make_gate()
    # 非纯算术 → 不应命中 math（放行 LLM 或由上层兜底）
    hit = gate.match(evil)
    assert hit is None or hit.kind != "math"


def test_safe_math_direct():
    gate = make_gate()
    assert gate._safe_math("1+2*3") == 7.0
    assert gate._safe_math("__import__('os')") is None


# ── 扩展规则（config patterns）─────────────────────────────

def test_custom_pattern():
    gate = make_gate(patterns={"状态码": "服务正常"})
    hit = gate.match("查一下状态码")
    assert hit is not None
    assert hit.kind == "custom"
    assert hit.reply == "服务正常"


# ── classify 短路 ─────────────────────────────────────────

def test_classify_empty_candidates():
    gate = make_gate()
    hit = gate.match_classify("随便什么任务", [])
    assert hit is not None
    assert hit.data["selected"] == []


def test_classify_single_candidate():
    gate = make_gate()
    cands = [{"id": "h1", "name": "A", "capabilities": ["coding"]}]
    hit = gate.match_classify("写个登录页", cands)
    assert hit is not None
    assert hit.data["selected"] == ["h1"]


def test_classify_capability_keyword():
    gate = make_gate()
    cands = [
        {"id": "h1", "name": "编码员", "capabilities": ["coding"]},
        {"id": "h2", "name": "写手", "capabilities": ["writing"]},
    ]
    hit = gate.match_classify("帮我写代码实现登录功能", cands)
    assert hit is not None
    assert hit.data["selected"] == ["h1"]


def test_classify_multiple_capability_matches_goes_llm():
    gate = make_gate()
    cands = [
        {"id": "h1", "name": "编码员", "capabilities": ["coding"]},
        {"id": "h2", "name": "前端", "capabilities": ["coding", "writing"]},
    ]
    # 两个候选都含 coding → 不短路（放行 LLM，避免误选）
    hit = gate.match_classify("写代码", cands)
    assert hit is None


def test_classify_disabled():
    gate = make_gate(enabled=False)
    assert gate.match_classify("x", [{"id": "h1"}]) is None
    assert gate.match("你好") is None
