# -*- coding: utf-8 -*-
"""守卫：**版本号与更新日志必须一致**（`agent_community.__version__` == CHANGELOG 最新条目）。

## 为什么有它（2026-10-18 真事故）

v4.8.60 发布时，我用一条内联 PowerShell 命令改版本号——**PowerShell 把 Python 代码吞坏了**，
命令报 SyntaxError 但发布脚本照样跑完并把代码推上去了 ⇒ 结果是：
**CHANGELOG 里写着 4.8.60，而 `agent_community.__version__` 还是 4.8.59**，
`/api/status` 也就一直报旧版本。这种"看起来成功了"的死状态，正是「失败要说话」要掐掉的东西。

判据（机器可判）：CHANGELOG 第一个 `## [x.y.z]` 必须等于 `agent_community.__version__`。
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.append(str(_ROOT))


def _changelog_versions() -> list:
    """CHANGELOG 里的版本号（**从新到旧**）。

    ⚠️ `CHANGELOG.md` 只在**发布面**存在（dev 副本没有它）⇒ 本文件在 dev 上跑时跳过版本比对，
    真正的强制点在**发布脚本**里的版本号闸门（`publish_acv4.ps1` 步骤 1.85），它在 oss 副本里生效。
    """
    path = _ROOT / "CHANGELOG.md"
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8", errors="replace")
    return re.findall(r"^##\s*\[(\d+\.\d+\.\d+)\]", text, re.M)


def test_version_matches_changelog_head():
    import agent_community
    versions = _changelog_versions()
    if not versions:
        pytest.skip("本副本没有 CHANGELOG.md（dev 副本）——由发布脚本里的版本号闸门在 oss 侧强制")
    newest = versions[0]
    assert agent_community.__version__ == newest, (
        "版本号不一致：`agent_community.__version__=%s`，但 CHANGELOG 最新是 %s。"
        "（发布时改版本号失败但代码照推 —— 2026-10-18 真发生过一次）"
        % (agent_community.__version__, newest))


def test_changelog_versions_are_descending_and_unique():
    versions = _changelog_versions()
    if not versions:
        pytest.skip("本副本没有 CHANGELOG.md（dev 副本）")
    assert len(versions) == len(set(versions)), "CHANGELOG 有重复版本号：%s" % versions[:5]
    def key(v):
        return tuple(int(x) for x in v.split("."))
    for a, b in zip(versions, versions[1:]):
        assert key(a) > key(b), "CHANGELOG 版本号不是从新到旧：%s 之后是 %s" % (a, b)


def test_publish_script_checks_version(monkeypatch=None):
    """发布脚本必须**自己**核对版本号（不能只靠人记得改）——没有这一条就还会重犯。

    ⚠️ 2026-10-21（恢复 GitHub 后 CI 真跑才发现）：`publish_acv4.ps1` 是**开发副本独有的发布工具**，
    **不在发布面**（公开仓库里没有它）⇒ 在 CI / 陌生人 clone 里这条要**跳过**，而不是报"缺文件"。
    在开发副本里它必须存在且带版本号闸门（这道检查仍然有效）。
    """
    ps1 = _ROOT / "publish_acv4.ps1"
    if not ps1.exists():
        pytest.skip("本副本没有 publish_acv4.ps1（发布副本/CI）；版本闸门在开发副本里生效")
    src = ps1.read_text(encoding="utf-8", errors="replace")
    assert "CHANGELOG" in src and "__version__" in src, \
        "发布脚本没有核对 `__version__` 与 CHANGELOG 版本号（应加一道闸）"
