# -*- coding: utf-8 -*-
"""`tools/insert_changelog_entry.py` 的守卫：**版本号与 CHANGELOG 必须同笔**。

背景（2026-10-15 真实事故）：发布流程原本是「先撤回 CHANGELOG（为了让预检"工作树必须干净"过）
→ 提交 → 事后补」，于是**提交那一刻**版本已升、CHANGELOG 还停在上一个版本 ⇒
CI 的 `test_changelog_mentions_current_version` 两个测试作业全红，而我本地全绿。
⇒ 把"插条目"变成发布流程的一部分（幂等 + 保住换行风格），本文件守它。
"""
from __future__ import annotations

import importlib.util
import os
import sys

import pytest

_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
if _ROOT not in sys.path:
    sys.path.append(_ROOT)

_spec = importlib.util.spec_from_file_location(
    "insert_changelog_entry", os.path.join(_ROOT, "tools", "insert_changelog_entry.py"))
ice = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ice)

HEAD = "# 更新日志（CHANGELOG）\n\n本项目遵循 [语义化版本]。\n\n## [4.8.7] - 2026-10-15\n\n旧内容\n"
ENTRY = "## [4.8.8] - 2026-10-15\n\n**新内容**：xxx\n"


def test_inserts_before_the_first_version_heading():
    out, changed = ice.insert(HEAD, ENTRY, "\n")
    assert changed is True
    assert out.index("## [4.8.8]") < out.index("## [4.8.7]"), "新条目必须在最上面"
    assert out.startswith("# 更新日志（CHANGELOG）"), "文件头不能被挪走"
    assert "[4.8.7]" in out, "旧条目不能丢"


def test_is_idempotent():
    once, _ = ice.insert(HEAD, ENTRY, "\n")
    twice, changed = ice.insert(once, ENTRY, "\n")
    assert changed is False, "同一版本插两次（发布脚本可重复跑）"
    assert twice == once
    assert twice.count("## [4.8.8]") == 1


def test_preserves_crlf_style():
    crlf = HEAD.replace("\n", "\r\n")
    out, changed = ice.insert(crlf, ENTRY, "\r\n")
    assert changed is True
    body = out[out.index("## [4.8.8]"):]
    assert "\r\n" in body
    # 不许把整个文件的行尾改掉（那会造出巨大的假 diff）
    assert "\n\n" not in out.replace("\r\n", ""), "混入了裸 LF"


def test_entry_without_version_heading_is_rejected():
    with pytest.raises(SystemExit):
        ice.entry_version("**没有标题行的条目**\n")


def test_changelog_without_any_version_heading_appends():
    out, changed = ice.insert("# 更新日志\n\n（空）\n", ENTRY, "\n")
    assert changed is True and "## [4.8.8]" in out


def test_changelog_has_version_matches_what_ci_checks():
    """CI 的判据就是「CHANGELOG 文本里出现 `[当前版本]`」—— 这里用同一口径判。"""
    assert ice.changelog_has_version(HEAD, "4.8.7") is True
    assert ice.changelog_has_version(HEAD, "4.8.8") is False
