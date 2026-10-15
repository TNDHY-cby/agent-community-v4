# -*- coding: utf-8 -*-
"""`tools/check_portability` 的**报告**也要守（2026-10-15）。

## 为什么值得单独守

判据（fail 档）此前已经有守卫（`test_portability_gate_passes`）。本文件守的是**另一半**：
**报告不许虚报、也不许吓人**。

背景：本扫描器长期只打印一个 `warn=97` 加一屏行号，**从不解释这些命中是什么**。
而实测逐条看过之后，那 97 条里**没有一条是产品面硬编码** —— 它们是：
测试夹具的"陷阱串"、说明历史修复的 docstring、**可被环境变量覆盖**的 dev 验收脚本兜底。
一个不解释的"97"会让人以为产品千疮百孔 ⇒ 下一个人就会去加 `--exclude` ⇒ **判据失效**
（这正是本项目在 `check_productization` 第一版上学过的：**噪音会让守卫被绕过**）。

所以本文件按真结果钉两条：

1. **报出来的数字必须等于真实条数**（不许虚报/漏报）；
2. **非源码产物不参与扫描**（`.txt` 报告产物拿"源码硬编码"的尺子量属于判据错位）。
"""
from __future__ import annotations

import os
import re
import subprocess
import sys

_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
if _ROOT not in sys.path:
    sys.path.append(_ROOT)
if os.path.join(_ROOT, "tools") not in sys.path:
    sys.path.append(os.path.join(_ROOT, "tools"))

import check_portability as cp  # noqa: E402


def test_report_counts_match_reality():
    """★报告里印出来的 `fail=/warn=` 必须与 `scan()` 的真实结果一致（不虚报、不漏报）。"""
    fails, warns, _skipped = cp.scan()
    r = subprocess.run([sys.executable, os.path.join(_ROOT, "tools", "check_portability.py")],
                       cwd=_ROOT, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    assert r.returncode == 0, "扫描器本体红了：\n" + (r.stdout or "")[-1500:]
    m = re.search(r"fail=(\d+)\s+warn=(\d+)", r.stdout or "")
    assert m, "报告里没有 `fail=… warn=…`（判据被改了？）：\n" + (r.stdout or "")[:800]
    assert int(m.group(1)) == len(fails), \
        "报告的 fail 数与真实不符：报告 %s / 实际 %d" % (m.group(1), len(fails))
    assert int(m.group(2)) == len(warns), \
        "报告的 warn 数与真实不符：报告 %s / 实际 %d" % (m.group(2), len(warns))


def test_warn_report_explains_itself():
    """警告区必须**说清"这些不是产品面硬编码"**，并按位置分组（否则 97 条就是个吓人的数字）。"""
    r = subprocess.run([sys.executable, os.path.join(_ROOT, "tools", "check_portability.py")],
                       cwd=_ROOT, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    out = r.stdout or ""
    fails, warns, _ = cp.scan()
    if not warns:
        return                                  # 没有警告就没什么要解释的
    assert "不阻断" in out, "警告区没有说清'不阻断'：\n" + out[:800]
    assert "按位置" in out and "按规则" in out, "警告区没有分组汇总：\n" + out[:800]
    # 每一条命中都必须带 `where`（分组就靠它；丢了就退化成"甩一屏行号"）
    missing = [w for w in warns if not w.get("where")]
    assert not missing, "有命中缺 where 字段（分组会退化）：%r" % missing[:3]


def test_non_source_artifacts_are_not_scanned():
    """`.txt` **产物/报告**不参与扫描；`.txt` 白名单（`requirements.txt`）照扫。"""
    assert cp.is_skipped("tests/offline_assert_report.txt") is True, \
        "验证报告产物被当成源码扫了（判据错位）"
    assert cp.is_skipped("requirements.txt") is False, \
        "requirements.txt 是发布面白名单，必须照扫"
    assert cp.is_skipped("agent_community/platform/server.py") is False


def test_product_logic_hardcoding_still_fails():
    """**反向证明判据没被"降噪"降没了**：产品逻辑里写死本机地址仍然必须是 fail。"""
    sample = "BASE = 'http://127.0.0.1:18999'"     # 不在 tests/ 也不在 docstring 里
    hit = [name for name, rx, level, scope in cp.RULES
           if rx.search(sample) and level == "fail"]
    assert hit, "规则表里已经没有能拦住产品逻辑写死地址的 fail 规则了：%r" % (sample,)


def test_fallback_walk_is_reported(monkeypatch):
    """★git 不可用时扫描器会退化成**整树遍历** ⇒ 扫描面变了，**必须说出来**。

    实测踩到（2026-10-15）：同一份仓库，Windows `warn=96`、WSL `warn=121`，
    差别全来自"WSL 的 git 因 `safe.directory` 拒绝工作"。两个环境数字对不上、
    却**都不说自己扫了什么** —— 这正是"看着像同一件事、其实不是"的陷阱。
    """
    class _Stub:
        class _R:
            stdout = b""

        @staticmethod
        def run(*_a, **_k):
            return _Stub._R()

    monkeypatch.setattr(cp, "subprocess", _Stub)
    monkeypatch.setattr(cp, "_USED_FALLBACK_WALK", False)
    files = cp.tracked_files()
    assert files, "退化分支也要能扫到文件（否则等于静默不扫）"
    assert cp._USED_FALLBACK_WALK is True, "走了退化分支却没留标记 —— 报告里就不会说"
