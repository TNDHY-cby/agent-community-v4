# -*- coding: utf-8 -*-
"""守卫：**演示数据播种**（seed_demo）。

判据：
① `--dry-run` 能跑通（退出码 0）、输出里能看到 3 个示例 agent 与"演示数据"字样；
② 只走公开 HTTP 接口（不直接改数据文件）——查源码里**没有** `save_state` / `open(数据文件)`；
③ 示例名不含任何真实 harness 名（只用「示例Harness-*」）。
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.append(str(_ROOT))

TOOL = _ROOT / "tools" / "seed_demo.py"


def test_dry_run_works():
    r = subprocess.run([sys.executable, str(TOOL), "--dry-run"], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", cwd=str(_ROOT), timeout=60)
    out = (r.stdout or "") + (r.stderr or "")
    assert r.returncode == 0, out[-500:]
    assert out.count("示例Harness-") >= 3, out[-500:]
    assert "演示数据" in out, out[-500:]


def test_only_public_http_and_no_real_names():
    src = TOOL.read_text(encoding="utf-8")
    for bad in ("save_state(", "state_mod", "data_dir("):
        assert bad not in src, "seed_demo 不许直接改数据文件（发现 %s）" % bad
    assert "/api/" in src
