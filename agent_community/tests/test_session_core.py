# -*- coding: utf-8 -*-
"""守卫：**「建立会话」核心链路**（`tools/session_core_check.py`）必须真绿。

老板最初骂的是「**唤不醒**」。这条链路是平台对用户最核心的承诺：
"我在登记时给你一条命令 → 平台真去跑它 → 会话建立并**自证** → 名字由平台按项目给"。

本文件守四件事：
  ① 判据工具存在且**真跑到全绿**（五格：登记带脚本 / 建间绑成员 / 会话自证 / 命令真跑 / 平台命名）；
  ② 判据是**端到端**的（真起实例、真打 HTTP、真跑我们给的命令）——不是假对象单测；
  ③ 动作脚本框架跑命令**必须显式 UTF-8**（否则中文会话名变乱码、rename 永远"未核实"）；
  ④ 五格里的"命令真被执行"必须有**我方留痕**（不许只信平台自述）。
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
TOOL = _ROOT / "tools" / "session_core_check.py"
FRAMEWORK = _ROOT / "agent_community" / "bridge_templates" / "script_framework" / "_common.py"


def _clean_env(**over) -> dict:
    """白名单环境（同 protocol_matrix / update）：别让 pytest 里的 AC_* 污染子进程。"""
    keep = ("PATH", "PYTHON", "SYSTEM", "WINDIR", "TEMP", "TMP", "HOME", "USER",
            "APPDATA", "LOCALAPPDATA", "COMSPEC", "PATHEXT", "LANG", "LC_", "LD_LIBRARY")
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("AC_") and k.upper().startswith(keep)}
    env.update({k: str(v) for k, v in over.items()})
    return env


def test_session_core_chain_is_green():
    assert TOOL.exists(), "缺 tools/session_core_check.py"
    r = subprocess.run([sys.executable, str(TOOL)], cwd=str(_ROOT), capture_output=True,
                       text=True, encoding="utf-8", errors="replace", timeout=600,
                       env=_clean_env())
    out = (r.stdout or "") + (r.stderr or "")
    assert r.returncode == 0, "「建立会话」核心链路有格失败：\n%s" % out[-1500:]
    for kw in ("① 登记带走动作脚本", "② 建工作间 + 成员绑 harness", "③ 会话建立且自证",
               "④ 用户命令真被执行", "⑤ 名字含项目名", "核心链路全绿"):
        assert kw in out, "判据输出缺 '%s'：\n%s" % (kw, out[-900:])


def test_action_framework_runs_commands_in_utf8():
    """真缺陷回归：`_common.run_command` 曾经没给 encoding ⇒ 中文会话名乱码 ⇒ rename 判不通过。"""
    src = FRAMEWORK.read_text(encoding="utf-8", errors="replace")
    assert 'encoding="utf-8"' in src, "动作脚本跑命令没显式 UTF-8（中文名会乱码）"
    assert "PYTHONIOENCODING" in src, "没给子进程 PYTHONIOENCODING=utf-8"
    assert 'errors="replace"' in src, "读取端没做容错解码"


def test_session_check_is_end_to_end_not_mocked():
    src = TOOL.read_text(encoding="utf-8", errors="replace")
    assert "/api/workshop" in src and "/api/harness/register" in src, "判据必须真打 HTTP（不是假对象）"
    assert "establish-session" in src, "判据没打到建立会话端点"
    assert "user_cmd_calls.log" in src, "缺「命令真被执行」的我方留痕"
    assert "subprocess.Popen" in src, "判据必须真起实例"
