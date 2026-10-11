# -*- coding: utf-8 -*-
"""示例桥脚本的**输出编码自保**（2026-10-11 实测缺陷的回归守卫）。

**缺陷**：`examples/filepoll_harness_bridge.py` / `pending_poll_bridge.py` 会打印 `✓` / `✗`。
在**中文 Windows** 上 stdout 的编码随控制台码页（多为 GBK）⇒ 打印这两个字符抛
`UnicodeEncodeError: 'gbk' codec can't encode character '\u2717'` ⇒ **把桥进程弄死**
（实测：桥起来后一自检就异常，表现为"桥不起作用"）。

**修法**：脚本开头自己 `sys.stdout.reconfigure(encoding="utf-8", errors="replace")`，
不要求用户记住设 `PYTHONIOENCODING`（规则 19：别把环境差异变成用户的坑）。

本文件做**源码级守卫**：不依赖测试机的码页，只断言"自保代码在"且"在打印之前"。
行为验证见提交说明里的实测复现（GBK + 不可达平台 ⇒ 正常打印 ✗ 而不崩）。
"""
from __future__ import annotations

import io
import os

import pytest

SCRIPTS = [
    "agent_community/examples/filepoll_harness_bridge.py",
    "agent_community/examples/pending_poll_bridge.py",
]


def _src(p: str) -> str:
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    with io.open(os.path.join(root, p), encoding="utf-8", errors="replace") as f:
        return f.read()


@pytest.mark.parametrize("p", SCRIPTS)
def test_script_reconfigures_output_encoding(p):
    """脚本必须自己把 stdout/stderr 改成 UTF-8 + 出错不炸。"""
    s = _src(p)
    assert "reconfigure(" in s, f"{p} 缺少输出编码自保（中文 Windows 上会被 ✓/✗ 弄死）"
    assert "errors=\"replace\"" in s or "errors='replace'" in s, \
        f"{p} 的 reconfigure 应带 errors=replace（否则别的字符仍可能炸）"
    assert "sys.stdout" in s and "sys.stderr" in s


@pytest.mark.parametrize("p", SCRIPTS)
def test_reconfigure_runs_before_any_print(p):
    """自保必须发生在**任何打印之前**（否则第一行就炸了）。"""
    s = _src(p)
    rec = s.find("reconfigure(")
    pr = s.find("print(")
    assert rec > 0, f"{p} 没有 reconfigure"
    assert pr > 0, f"{p} 没有任何 print（可疑）"
    assert rec < pr, f"{p} 的 reconfigure 出现在 print 之后 —— 太晚了"


@pytest.mark.parametrize("p", SCRIPTS)
def test_reconfigure_failure_does_not_crash(p):
    """老解释器没有 reconfigure 时，不能因此崩（要有 try/except）。"""
    s = _src(p)
    i = s.find("reconfigure(")
    head = s[max(0, i - 600):i]
    assert "try:" in head, f"{p} 的 reconfigure 没有 try 保护"


def test_scripts_still_carry_the_non_gbk_glyphs():
    """这两个脚本**仍然**打印 ✓/✗（所以自保必需）——若哪天改成纯 ASCII，这条会提醒我们。"""
    s = _src(SCRIPTS[0])
    assert "✓" in s or "✗" in s, "字符没了？那应同步更新本测试的说明（正文注释）"
