# -*- coding: utf-8 -*-
"""守卫：**AI 失败必须说人话**（V69）。

起因（真机）：陌生人第一次按 README 跑，第 4 步看到的是

    ✗ 这次没有产生有效结果（AI 调用失败）:
    [Error: [Errno 11001] getaddrinfo fail

这对用户等于没说 —— 他不知道要干什么。`getaddrinfo fail` 其实只意味着
"模型地址解析不到"（多半没配或填错）。

判据：
  ① 常见技术错误各归一类，且**人话与下一步都不为空**；
  ② 认不出来的错误**如实说认不出来**（kind=unknown），不许编解释；
  ③ `has_bare_tech_error()` 能抓到"只有技术串"的显示文本（防止回退）；
  ④ 翻译层**不绑定任何模型服务商**（源码里 grep 不到具体厂商名/域名）。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.append(str(_ROOT))

from agent_community.platform import ai_error_human as H  # noqa: E402


#: 真机/常见出现过的原文 → 期望归类
CASES = [
    ("[Error: [Errno 11001] getaddrinfo fail", "dns"),
    ("[Error: Name or service not known", "dns"),
    ("[Error: [Errno 111] Connection refused", "conn_refused"),
    ("[Error: read timeout]", "timeout"),
    ("[Error: HTTP 401] {\"error\":\"invalid_api_key\"}", "auth"),
    ("[Error: HTTP 402] Insufficient account balance", "quota"),
    ("[Error: HTTP 403] forbidden", "forbidden"),
    ("[Error: HTTP 404] model_not_found", "not_found"),
    ("[Error: HTTP 429] rate limit exceeded", "rate_limit"),
    ("[Error: HTTP 503] service unavailable", "server_error"),
    ("[Error: api.example.com 解析不到]", "not_configured"),
]


@pytest.mark.parametrize("raw,expect", CASES)
def test_each_kind_has_human_and_fix(raw, expect):
    e = H.explain(raw)
    assert e["kind"] == expect, "%r 该归为 %s，实得 %s" % (raw, expect, e["kind"])
    assert e["human"] and e["human"] != "", "人话不能为空"
    assert e["fix"] and len(e["fix"]) >= 6, "下一步要说清怎么做"
    # 原始文本要保留（排查用），但不该当成人话
    assert e["raw"]


def test_unknown_must_confess():
    """认不出来就如实说，**不许编**。"""
    e = H.explain("[Error: 某个从没见过的东西 went wrong]")
    assert e["kind"] == "unknown"
    assert "没认出来" in e["human"]
    assert "原文" in e["fix"], "认不出时该让用户把原文带给管理员"


def test_empty_is_explicit():
    e = H.explain("")
    assert e["kind"] == "empty" and e["human"]


def test_humanize_is_one_line():
    line = H.humanize("[Error: [Errno 11001] getaddrinfo fail")
    assert "\n" not in line and "——" in line
    assert "解析不到" in line


def test_with_explanation_keeps_raw_at_end():
    md = H.with_explanation("[Error: HTTP 402] insufficient balance")
    assert "原因：" in md and "怎么办：" in md
    # 原始信息必须在最后一行（先人话、后技术，顺序不能反）
    assert md.strip().splitlines()[-1].startswith("  原始信息")


def test_bare_tech_detector_catches_only_tech():
    """判据口径：显示文本里**只有**技术串 ⇒ 抓到；带了人话 ⇒ 抓不到（因为人话里没有那些串）。"""
    assert H.has_bare_tech_error("[Error: [Errno 11001] getaddrinfo fail") is not None
    human = H.humanize("[Error: [Errno 11001] getaddrinfo fail")
    # 人话本身不含技术串（否则说明翻译没起作用）
    assert H.has_bare_tech_error(human) is None, "人话里不该残留技术串：%r" % human


def test_no_vendor_names_in_source():
    """通用性：翻译层不许出现具体模型服务商的名字/域名。"""
    src = Path(H.__file__).read_text(encoding="utf-8")
    for bad in ("openai.com", "anthropic", "deepseek.com", "dashscope", "moonshot",
                "azure", "qwen", "glm-4"):
        assert bad not in src.lower(), "翻译层不该绑定服务商：%s" % bad
