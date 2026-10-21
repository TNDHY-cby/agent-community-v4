# -*- coding: utf-8 -*-
"""守卫：**静默吞异常只减不增**（外部审查 P3-7 的落法）。"""

# 背景：审计点名生产代码里有一批 `except …: pass`（失败既不记日志也不返回标记），
# 与项目准则「失败要说话」有张力。但一把梭全改风险高于收益（很多是刻意的兜底）。
# ⇒ 本轮先立闸门：**现有数量记基线，新增必须带 `# noqa: BLE001`（或真的记日志）**。
# 清理存量按域分批做，每批都要有自己的判据。
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.append(str(_ROOT))

TOOL = _ROOT / "tools" / "silent_except_report.py"
BASELINE = _ROOT / "tools" / "silent_except_baseline.json"


def test_baseline_file_exists_and_parses():
    assert BASELINE.exists(), "缺基线文件（先跑 `python tools/silent_except_report.py --write`）"
    data = json.loads(BASELINE.read_text(encoding="utf-8"))
    assert isinstance(data.get("total"), int) and data["total"] >= 0
    assert isinstance(data.get("files"), dict)
    assert sum(data["files"].values()) == data["total"], "基线的 total 与 files 合计不一致"


def test_silent_except_does_not_grow():
    """真跑工具：静默吞异常**不许比基线多**。"""
    r = subprocess.run([sys.executable, str(TOOL), "--check"], cwd=str(_ROOT),
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300)
    out = (r.stdout or "") + (r.stderr or "")
    assert r.returncode == 0, ("静默吞异常变多了（新写的 `except: pass` 请加 "
                              "`# noqa: BLE001 — 有意静默：<理由>`，或改成 logger.debug 记录）：\n" + out[-800:])
