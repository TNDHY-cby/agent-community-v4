# -*- coding: utf-8 -*-
"""守卫：闸门**红的时候必须能诊断**（2026-10-17 两次"红过但没抓到细节"换来的）。

判据（真跑）：
  ① `failed_cases()` 能从真 pytest 输出里抓出失败用例名（不是查源码文本）；
  ② 判据日志**落盘**且默认不在仓库里（`AC_GATE_LOGS` 可覆盖）；
  ③ 自证仍然有效（迷你套件 绿→0 / 红→1）。
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


def _tool():
    p = Path(_ROOT) / "tools" / "dual_platform_check.py"
    spec = importlib.util.spec_from_file_location("dpc_diag", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_failed_cases_extracts_names():
    m = _tool()
    text = ("...F...\nFAILED agent_community/tests/test_a.py::test_x - AssertionError: boom\n"
            "ERROR agent_community/tests/test_b.py::test_y\n2 failed, 1 passed in 3.2s\n")
    got = m.failed_cases(text)
    assert any("test_x" in g for g in got), got
    assert any("test_y" in g for g in got), got
    assert m.failed_cases("") == []


def test_failed_cases_is_bounded():
    """红一大片时也不许刷屏（最多 12 条）。"""
    m = _tool()
    text = "\n".join("FAILED a/test_%d.py::t" % i for i in range(50))
    assert len(m.failed_cases(text)) <= 12


def test_log_dir_defaults_outside_repo(tmp_path, monkeypatch):
    m = _tool()
    d = m._log_dir()
    assert Path(_ROOT).resolve() not in d.resolve().parents, "日志不许默认落进仓库"
    monkeypatch.setenv("AC_GATE_LOGS", str(tmp_path / "logs"))
    d2 = m._log_dir()
    assert d2 == tmp_path / "logs" and d2.exists()
    p = m._save_log("x.log", "hello")
    assert p.read_text(encoding="utf-8") == "hello"


def test_selftest_still_proves_gate_can_go_green_and_red():
    """判据自己的自证（拿真 pytest 跑）：绿→0、红→1。"""
    r = subprocess.run([sys.executable, str(Path(_ROOT) / "tools" / "dual_platform_check.py"),
                        "--selftest"], cwd=_ROOT, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=300)
    out = (r.stdout or "") + (r.stderr or "")
    assert r.returncode == 0, out[-500:]
    assert "判据会绿也会红" in out, out[-500:]
