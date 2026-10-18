# -*- coding: utf-8 -*-
"""守卫：`design-docs/索引.md` 不许漂移（老板 2026-10-18 问「历史设计稿在哪里看」的答案）。

判据（真跑，不查源码文本）：
  ① `python tools/gen_design_index.py --check` ⇒ 0（索引与文件一致）；
  ② **每一份非 `.bak` 的 `.md` 都在索引里**（新增设计稿忘了重新生成 ⇒ 红）；
  ③ 索引里每一条的**文件真的存在**（删了文档没重生成 ⇒ 红）；
  ④ 状态解析别乱推断：正文里出现"拍板"但**头部没有状态行**的文档，必须标「无状态行」。
"""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
if _ROOT not in sys.path:
    sys.path.append(_ROOT)

DOCS = Path(_ROOT) / "design-docs"
INDEX = DOCS / "索引.md"


def _gen():
    p = Path(_ROOT) / "tools" / "gen_design_index.py"
    spec = importlib.util.spec_from_file_location("gdi", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_index_is_not_drifted():
    r = subprocess.run([sys.executable, str(Path(_ROOT) / "tools" / "gen_design_index.py"), "--check"],
                       cwd=_ROOT, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=180)
    out = (r.stdout or "") + (r.stderr or "")
    assert r.returncode == 0, "索引漂移了，跑 `python tools/gen_design_index.py` 重新生成：\n%s" % out[-500:]


def test_every_design_doc_is_listed():
    assert INDEX.exists(), "design-docs/索引.md 不见了"
    text = INDEX.read_text(encoding="utf-8", errors="replace")
    missing = [p.name for p in DOCS.glob("*.md")
               if ".bak" not in p.name and p.name != INDEX.name and "(%s)" % p.name not in text]
    assert not missing, "这些设计稿没进索引（重新生成）：%s" % missing[:8]


def test_no_ghost_entries():
    """索引里提到的文件必须真的存在（删了文档却没重生成 ⇒ 红）。"""
    import re
    text = INDEX.read_text(encoding="utf-8", errors="replace")
    refs = set(re.findall(r"\]\(([^)]+\.md)\)", text))
    ghosts = [r for r in refs if not (DOCS / r).exists()]
    assert not ghosts, "索引指向了不存在的文件：%s" % ghosts[:8]


def test_status_comes_from_status_line_not_body():
    """正文里提到"拍板"但**头部没有状态行** ⇒ 必须写「无状态行」（我第一版就误标过 V13）。"""
    m = _gen()
    body_only = "# 某设计稿\n\n正文第一段：这个功能等拍板后再写码。\n"      # 无「状态」字样行
    assert m._status_of(body_only) == "无状态行"
    with_status = "# 某设计稿\n\n> 状态：**待老板拍板**（未写码）\n"
    assert m._status_of(with_status) == "待拍板"
    landed = "# 某设计稿\n\n> 状态：**已拍板（老板 2026-10-17）并已落地 v9.9.9**\n"
    assert m._status_of(landed) == "已拍板并落地"
