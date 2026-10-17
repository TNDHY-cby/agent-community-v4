# -*- coding: utf-8 -*-
"""`tools/dual_platform_check.py` 守卫：**CI 替代口径本身要有守卫**（2026-10-17 老板拍板）。

## 为什么有这个文件

老板 2026-10-17：「**GitHub CI 停更**」「更新 Gitee」「**本机双平台真跑替代**」。
⇒ 判据从"云上的两个作业"变成"**本机一条命令**"。判据搬家了，**守卫必须跟着搬**，否则：
  · 工具被改坏（比如把"只跑成一侧"的退出码 2 改成 0）没人发现；
  · 规则源（`AGENTS.md`/`PORTABILITY.md`/`PRODUCTIZATION.md`/`项目完整模型.md`）里那句口径
    被某一处改掉、其余三处还写着"CI 双平台"—— 那正是规则 20 明令禁止的**两份口径**。

判据（都按**真结果**判）：
  ① 工具存在、能 `--plan`，且计划里**两侧都在**、把**退出码语义**写清楚；
  ② 工具**如实**：拿不到另一侧时必须给 **2**（"部分"），绝不给 0 —— 用 `--local-only` 真跑一次验证；
  ③ Linux 侧运行器是 **POSIX**（shebang + LF，没有 CRLF），且**不写死任何本机路径**；
  ④ **四个规则源里都提到这条口径**（`dual_platform_check`），一处不落。
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
TOOL = _ROOT / "tools" / "dual_platform_check.py"
LINUX_RUNNER = _ROOT / "tools" / "run_suite_in_repo.sh"
RULE_SOURCES = ("AGENTS.md", "PORTABILITY.md", "PRODUCTIZATION.md",
                os.path.join("design-docs", "项目完整模型.md"))


def _run(args, timeout=900):
    return subprocess.run([sys.executable, str(TOOL), *args], cwd=str(_ROOT),
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=timeout)


def test_tool_exists_and_plan_names_both_sides():
    assert TOOL.exists(), "双平台判据工具不见了：%s" % TOOL
    r = _run(["--plan"])
    out = (r.stdout or "") + (r.stderr or "")
    assert r.returncode == 0, "--plan 不该失败：\n%s" % out[-600:]
    assert "Windows" in out and "Linux" in out, "计划里必须**两侧**都写出来：\n%s" % out
    assert re.search(r"0\s*=\s*两侧都绿", out) and re.search(r"2\s*=\s*只跑成了一侧", out), \
        "退出码语义必须写清（尤其 2＝只跑成一侧＝不算通过）：\n%s" % out
    assert "AC_LINUX_PYTHON" in out, "本机适配要能被发现（AC_LINUX_PYTHON / AC_LINUX_WSL_DISTRO）"


def test_tool_is_honest_when_only_one_side_ran():
    """★判据**自己会绿也会红** —— 真跑工具的 `--selftest`（拿迷你套件跑真 pytest，几秒钟）。

    ⚠️ 别在这里跑 `--local-only`（那是整套回归，4 分钟）—— 守卫不该变成长测。
    """
    r = _run(["--selftest"], timeout=300)
    out = (r.stdout or "") + (r.stderr or "")
    assert r.returncode == 0, "工具自证失败（判据不可信）：\n%s" % out[-800:]
    assert "迷你套件（通过）" in out and "迷你套件（失败）" in out, \
        "自证必须真的跑了两种情形：\n%s" % out[-400:]


def test_plan_documents_the_honest_partial_exit_code():
    """`--plan` 里必须写清 **2 ＝ 只跑成一侧 ＝ 不算通过**（这条是"如实说话"的关键）。"""
    r = _run(["--plan"])
    out = (r.stdout or "") + (r.stderr or "")
    assert r.returncode == 0
    assert "2=只跑成了一侧" in out or "2 = 只跑成了一侧" in out, \
        "计划里没写清退出码 2 的语义：\n%s" % out


def test_linux_runner_is_posix_and_portable():
    assert LINUX_RUNNER.exists(), "Linux 侧运行器不见了：%s" % LINUX_RUNNER
    raw = LINUX_RUNNER.read_bytes()
    assert raw.startswith(b"#!/usr/bin/env bash"), "缺 POSIX shebang"
    assert b"\r\n" not in raw, "有 CRLF —— 在 Linux 上会报 `bash: $'\\r': command not found`"
    text = raw.decode("utf-8")
    for bad in (r"[A-Za-z]:\\", "/home/", "/Users/", "C:\\"):
        assert bad not in text, "Linux 运行器里写死了本机路径（%s）—— 本机适配只能走 .env/环境变量" % bad
    assert "PATH=" in text and "dirname" in text, \
        "必须把解释器所在 bin 放进 PATH（否则用例里 spawn python 的会假失败，实测 4 条）"


def test_rule_sources_all_carry_the_new_verdict():
    """规则 20：判据口径搬了家，**四个规则源一次全改**，不许只改一处。"""
    missing = []
    for rel in RULE_SOURCES:
        p = _ROOT / rel
        assert p.exists(), "规则源不见了：%s" % rel
        if "dual_platform_check" not in p.read_text(encoding="utf-8", errors="replace"):
            missing.append(rel)
    assert not missing, "这些规则源没跟上新口径（规则 20：一次全改）：%s" % missing


def test_gitee_templates_exist_and_are_published():
    """Gitee 是**唯一主仓库** ⇒ 它的 issue/PR 模板必须①在仓库里、②进发布同步面。

    （Gitee 认 `.gitee/ISSUE_TEMPLATE/*.md` 与 `.gitee/PULL_REQUEST_TEMPLATE.md`；
    不同步＝"Gitee 侧配置只存在于开发机"，同 requirements.txt 那类老教训。）
    """
    need = [_ROOT / ".gitee" / "ISSUE_TEMPLATE" / "bug_report.md",
            _ROOT / ".gitee" / "ISSUE_TEMPLATE" / "feature_request.md",
            _ROOT / ".gitee" / "PULL_REQUEST_TEMPLATE.md"]
    missing = [str(p.relative_to(_ROOT)) for p in need if not p.exists()]
    assert not missing, "Gitee 侧模板缺件：%s" % missing
    publish = _ROOT / "publish_acv4.ps1"
    if not publish.exists():
        return          # 发布副本里没有这个 dev-only 脚本（同 frontend_handover 守卫的处理）
    src = publish.read_text(encoding="utf-8", errors="replace")
    assert ".gitee" in src, "发布脚本没同步 `.gitee/` —— Gitee 侧模板不会出现在公开仓库里"


def test_pre_release_single_command_exists():
    """发布前判据必须有一条**单命令入口**（`tools/pre_release_check.py`），且规则源指向它。

    起因（2026-10-17）：发布前要跑五类判据，散在五处，**靠人记就会漏**（我漏过"自己的工作树没提交"、
    "CHANGELOG 写了真实路径"）。合成一条命令后，`--plan` 能自证它覆盖了什么。
    """
    tool = _ROOT / "tools" / "pre_release_check.py"
    assert tool.exists(), "缺 tools/pre_release_check.py（发布前判据没有单命令入口）"
    r = subprocess.run([sys.executable, str(tool), "--plan"], cwd=str(_ROOT),
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
    out = (r.stdout or "") + (r.stderr or "")
    assert r.returncode == 0, "--plan 不该失败：\n%s" % out[-500:]
    for kw in ("对外预检", "交接区守卫", "双平台", "首跑", "check_portability"):
        assert kw in out, "--plan 里少了这一类判据：%s\n%s" % (kw, out)
    assert "2=有项没跑成" in out or "2＝有项没跑成" in out or "部分" in out, \
        "退出码语义要写清（2＝部分不算通过）"
    for rel in ("PORTABILITY.md", "PRODUCTIZATION.md", "AGENTS.md"):
        doc = (_ROOT / rel).read_text(encoding="utf-8", errors="replace")
        assert "pre_release_check" in doc, "%s 没提到这条单命令入口（规则 20）" % rel
