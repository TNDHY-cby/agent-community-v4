# -*- coding: utf-8 -*-
"""守卫：**README / CLI 文案 / CHANGELOG 三者不许和代码打架**（2026-10-21 外部审查报告驱动）。

起因：一份外部审查（对 v4.15.8 的公开仓库）抓到四类硬伤，全是"代码改了、文档没跟"：

1. README 教用户 `cli start --ai-provider …`，而 `start` **根本没有这些参数** ⇒ 照抄就报
   `No such option`（一级：用户第一步就失败）；
2. CLI 源码与提示里还有裸 `agent-community task-result …` 这类命令，而**本仓库不是 pip 包**
   （无 pyproject/setup，无 console_scripts）⇒ 那条命令不存在（一级）；
3. `CHANGELOG.md` 顶部有个孤立 H1 `# 4.8.26`，按"首个版本标题"读会得出错误版本（二级）；
4. `AGENTS.md` 首屏相对链接指向 `HANDOVER.md`，而**公开仓库里没有这个文件**（二级，死链）。

判据都是**真跑/真读**：跑 `cli start --help` 拿真实参数表，再与 README 逐个比对。
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.append(str(_ROOT))

CLI = _ROOT / "agent_community" / "cli.py"
README = _ROOT / "README.md"
AGENTS = _ROOT / "AGENTS.md"
CHANGELOG = _ROOT / "CHANGELOG.md"

#: 裸命令（"agent-community xxx"，不是 agent_community 包/模块）
BARE_CMD = re.compile(r"(?<![\w_.-])agent-community\s+\w")
#: 完整参数名（**必须以字母数字结尾、后面不能再跟 `-`/`*`**：`--ai-*` 这种通配写法不算具体参数）
OPT = re.compile(r"--[a-z][a-z0-9-]*[a-z0-9](?![\w*-])")


def _start_help() -> str:
    """真跑 `cli start --help`（**参数表的唯一真相**）。"""
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    r = subprocess.run([sys.executable, "-m", "agent_community.cli", "start", "--help"],
                       cwd=str(_ROOT), capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=120, env=env)
    assert r.returncode == 0, "cli start --help 跑不起来：%s%s" % (r.stdout, r.stderr)
    return r.stdout


def test_cli_source_has_no_bare_agent_community_command():
    """CLI 源码里不许再有裸 `agent-community <cmd>`（本仓库没有 pip 入口）。"""
    src = CLI.read_text(encoding="utf-8", errors="replace")
    hits = BARE_CMD.findall(src)
    assert not hits, "cli.py 里还有 %d 处裸 command 文案：%s" % (len(hits), hits[:5])


def test_cli_help_has_no_bare_agent_community_command():
    help_text = _start_help()
    assert not BARE_CMD.search(help_text), "CLI 帮助里出现裸 agent-community 命令"


def test_readme_cli_start_options_exist_for_real():
    """README 里凡是写在 `cli start` 旁边的 `--option`，都必须在真实 `--help` 里存在。"""
    help_text = _start_help()
    real = set(OPT.findall(help_text))
    text = README.read_text(encoding="utf-8", errors="replace")
    bad = []
    for line in text.splitlines():
        if "cli start" not in line:
            continue
        for opt in OPT.findall(line):
            if opt not in real:
                bad.append((opt, line.strip()[:90]))
    assert not bad, "README 教了不存在的参数：%s" % bad[:5]


def test_readme_does_not_promise_ai_cli_flags():
    """`--ai-*` 这组参数（早期版本有、现已取消）不许再出现在 README。"""
    text = README.read_text(encoding="utf-8", errors="replace")
    hits = re.findall(r"--ai-(?:provider|model|api-key|base-url)", text)
    assert not hits, "README 还写着已取消的 %d 个 --ai-* 参数" % len(hits)


def test_changelog_top_version_heading_is_the_current_one():
    """CHANGELOG 第一个版本标题必须是 `## [当前版本]`，不许有孤立 H1 抢在前面。"""
    if not CHANGELOG.exists():
        pytest.skip("本副本没有 CHANGELOG.md（开发副本）")
    text = CHANGELOG.read_text(encoding="utf-8", errors="replace")
    first = None
    for line in text.splitlines():
        m = re.match(r"^(#{1,3})\s*\[?(\d+\.\d+\.\d+)\]?", line.strip())
        if m:
            first = (m.group(1), m.group(2), line.strip())
            break
    assert first is not None, "CHANGELOG 里找不到版本标题"
    hashes, version, raw = first
    assert hashes == "##", "CHANGELOG 第一个版本标题应为 `## [x.y.z]`，实际是 %r" % raw
    import agent_community
    assert version == agent_community.__version__, \
        "CHANGELOG 首个版本 %s ≠ __version__ %s" % (version, agent_community.__version__)


def test_agents_md_does_not_relatively_link_dev_only_handover():
    """`AGENTS.md` 公开可用 ⇒ 不许用相对链接指向只在开发副本里存在的 HANDOVER.md。"""
    text = AGENTS.read_text(encoding="utf-8", errors="replace")
    assert "](./HANDOVER.md)" not in text, "AGENTS.md 里有指向 HANDOVER.md 的相对链接（公开树会 404）"


# ── ⑦ README 首屏的「当前版本」行必须等于 __version__ ─────────────────────
def test_readme_version_line_matches_version():
    """README 里「当前版本：`x.y.z`」必须等于 `__version__`。

    ★2026-10-21（外部审查 P1）：v4.15.9 发版时改了代码/CHANGELOG，**唯独忘了这一行**，
      6 条守卫全绿却让用户第一眼看到错版本 —— 典型的"守卫全绿但事实是错的"。
    """
    import agent_community

    text = README.read_text(encoding="utf-8", errors="replace")
    m = re.search(r"当前版本[:：]\s*`?(\d+\.\d+\.\d+)`?", text)
    assert m, "README 里找不到「当前版本：`x.y.z`」这一行（它是读者判断版本的第一入口）"
    assert m.group(1) == agent_community.__version__, \
        "README 版本行 %s ≠ __version__ %s（发版时忘了回改）" % (m.group(1), agent_community.__version__)


# ── ⑧ Python 源码不许带 UTF-8 BOM（requirements.txt 早有这条纪律）──────────
def test_python_sources_have_no_bom():
    """`agent_community/**/*.py`、`tools/*.py`、`tests/*.py` 首字节不许是 BOM。

    起因：`test_ai_error_speaks.py` 带 `EF BB BF`，`ast.parse` 直接报
    `invalid non-printable character U+FEFF`（扫描器/静态审查全被它绊住）。
    项目对 `requirements.txt` 早有同样的纪律，**源码也该一样**。
    """
    bad = []
    for root in (agent_community_pkg := _ROOT / "agent_community", _ROOT / "tools", _ROOT / "tests"):
        for p in root.rglob("*.py"):
            if ".bak" in p.name or "__pycache__" in p.parts:
                continue
            try:
                with p.open("rb") as f:
                    if f.read(3) == b"\xef\xbb\xbf":
                        bad.append(str(p.relative_to(_ROOT)))
            except Exception:                                 # noqa: BLE001
                continue
    assert not bad, "这些 .py 带 BOM（去掉它）：%s" % bad[:8]
