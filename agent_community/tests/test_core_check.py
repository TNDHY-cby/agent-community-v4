# -*- coding: utf-8 -*-
"""守卫：**核心体检一条命令**（`tools/core_check.py`）必须在、可规划、且被规则源引用。

为什么不在这里再跑一遍核心体检：它的两项子检查**已有各自的真跑守卫**
（`test_core_matrix.py` 跑六协议矩阵+闭环、`test_acp_core.py` 跑 acp 真拉起），
在这里重复跑只会让整套回归多花 1.5 分钟。**判据要真，但不要重复真。**

本文件守三件事（都能机器判）：
  ① `core_check.py --plan` 列出三项（协议/闭环、acp、桥产物落点）且 exit 0；
  ② `pre_release_check.py` 把它接进了**全量**模式（漏接 ⇒ 发布判据里就没这项）；
  ③ 规则源提到它（`AGENTS.md` / `PORTABILITY.md`）——规则 20：一条命令只有一处真相。
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
if _ROOT not in sys.path:
    sys.path.append(_ROOT)


def test_core_check_plan_lists_three_items():
    tool = Path(_ROOT) / "tools" / "core_check.py"
    assert tool.exists(), "缺 tools/core_check.py"
    r = subprocess.run([sys.executable, str(tool), "--plan"], cwd=_ROOT, capture_output=True,
                       text=True, encoding="utf-8", errors="replace", timeout=120)
    out = (r.stdout or "") + (r.stderr or "")
    assert r.returncode == 0, out[-500:]
    for kw in ("六协议矩阵", "acp 真拉起", "桥产物落点", "退出码"):
        assert kw in out, "core_check --plan 少了 '%s'：\n%s" % (kw, out)


def test_pre_release_wires_core_check_in_full_mode():
    src = (Path(_ROOT) / "tools" / "pre_release_check.py").read_text(encoding="utf-8")
    assert "core_check.py" in src, "发布前一条命令没接核心体检"
    # 只该在 full 模式（`if not fast:` 块里）出现
    full_block = src.split("if not fast:", 1)[-1].split("items += [", 1)[0]
    assert "core_check.py" in full_block, "核心体检应只挂在全量模式（--fast 跳过大件）"


def test_rule_sources_mention_core_check():
    for rel in ("AGENTS.md", "PORTABILITY.md"):
        doc = (Path(_ROOT) / rel).read_text(encoding="utf-8", errors="replace")
        assert "core_check" in doc, "%s 没提到核心体检一条命令（规则 20）" % rel
