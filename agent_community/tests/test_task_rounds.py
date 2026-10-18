# -*- coding: utf-8 -*-
"""守卫：**多轮派活**（`tools/task_rounds_check.py`）——协作不是"派一次、回一次"。

判据（都能机器判）：
  ① 判据工具真跑到全绿（五格：落盘 / 回报 / 独立轮次 / **带上一轮上下文** / 留痕有序）；
  ② 派活载荷**必须有可对账的 `task_id`**（2026-10-19 之前没有 ⇒ 回报指不回具体哪次派活）；
  ③ 派活载荷**必须携带上一轮上下文**（`context.previous_instructions` + 人能读的那段）；
  ④ 派活记录里要存 `task_id`（否则平台侧也无法对账）。
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
TOOL = _ROOT / "tools" / "task_rounds_check.py"
COMMON = _ROOT / "agent_community" / "platform" / "routers" / "workshops_common.py"
ASSIGN = _ROOT / "agent_community" / "platform" / "routers" / "workshops_assign.py"


def _clean_env(**over) -> dict:
    keep = ("PATH", "PYTHON", "SYSTEM", "WINDIR", "TEMP", "TMP", "HOME", "USER",
            "APPDATA", "LOCALAPPDATA", "COMSPEC", "PATHEXT", "LANG", "LC_", "LD_LIBRARY")
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("AC_") and k.upper().startswith(keep)}
    env.update({k: str(v) for k, v in over.items()})
    return env


def test_multi_round_judgement_is_green():
    assert TOOL.exists(), "缺 tools/task_rounds_check.py"
    r = subprocess.run([sys.executable, str(TOOL)], cwd=str(_ROOT), capture_output=True,
                       text=True, encoding="utf-8", errors="replace", timeout=600,
                       env=_clean_env())
    out = (r.stdout or "") + (r.stderr or "")
    assert r.returncode == 0, "多轮派活判据有格失败：\n%s" % out[-1500:]
    for kw in ("① 派活真落盘", "② 第一轮回报被接受", "③ 第二轮是独立任务",
               "④ 第二轮带上一轮上下文", "⑤ 两轮都留痕且顺序正确", "多轮派活全绿"):
        assert kw in out, "判据输出缺 '%s'：\n%s" % (kw, out[-900:])


def test_payload_has_task_id_and_previous_context():
    src = COMMON.read_text(encoding="utf-8", errors="replace")
    assert '"task_id": "t-"' in src, "派活载荷没有可对账的 task_id"
    assert "_leader_recent_context" in src, "缺少「上一轮上下文」的构造"
    assert "previous_instructions" in src, "载荷没带上一轮指令"
    assert "上一轮上下文" in src, "人读的载荷里没有上下文段（naive agent 看不到）"
    body = ASSIGN.read_text(encoding="utf-8", errors="replace")
    assert '"task_id": str((payload or {}).get("task_id")' in body, \
        "派活记录里没存 task_id（平台侧无法对账）"
