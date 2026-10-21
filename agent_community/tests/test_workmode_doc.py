# -*- coding: utf-8 -*-
"""守卫：**工作模式（老板 2026-10-21 拍的七条）必须在 `AGENTS.md` 里**。

背景：老板原话「以后都把连续的任务放的任务列表里（插件），不要停下来，除非遇到分岔口」，
并要求"这个工作模式写入合作社开发模式里面"。同一份口径现在有三处：
  ① preset「合作社开发模式」的 persona（每步都在上下文里，七条极简）；
  ② 技能 `ac-project-dev` 的 `references/workmode.md`（详细）；
  ③ 本仓库 `AGENTS.md` 的「工作模式」一节（给任何进这个仓库的 agent 看）。
②③ 在仓库/Git 之外（技能与 preset 都在用户目录），**CI 管不到**；本条只守 ③，
防止有人"整理文档"时把它删掉——删了就等于这条规矩对后来的 agent 不存在了。
"""
from __future__ import annotations

import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]

#: 七条的关键词（缺一条就说明被删/被改写）
RULES = ["任务列表驱动", "判据必须是真的", "判据不许写死位置", "失败要说话",
         "一次性脚本进 `tools/`", "每版收尾固定", "不粉饰"]


def test_agents_md_has_workmode_section():
    text = (_ROOT / "AGENTS.md").read_text(encoding="utf-8", errors="replace")
    assert "## 工作模式（**老板 2026-10-21 拍板**" in text, \
        "AGENTS.md 里找不到「工作模式」一节（老板 2026-10-21 拍板的那七条）"
    missing = [r for r in RULES if r not in text]
    assert not missing, "工作模式七条里缺了这些：%s" % missing


def test_workmode_points_to_detailed_doc():
    """摘要必须指向详细版，否则新人只看到七行口号、不知道怎么判。"""
    text = (_ROOT / "AGENTS.md").read_text(encoding="utf-8", errors="replace")
    assert "references/workmode.md" in text, "AGENTS.md 的工作模式一节没指向详细版 references/workmode.md"


def test_workmode_table_is_a_table():
    """七条必须是**表格**（每条都带"一句话判据"）——纯列条口号没有约束力。"""
    text = (_ROOT / "AGENTS.md").read_text(encoding="utf-8", errors="replace")
    start = text.find("## 工作模式（**老板 2026-10-21 拍板**")
    assert start > 0
    section = text[start:text.find("\n## ", start + 10)]
    rows = [l for l in section.splitlines() if re.match(r"^\|\s*[①-⑦]\s*\|", l)]
    assert len(rows) == 7, "工作模式表应有 7 行（①~⑦），实际 %d 行" % len(rows)
