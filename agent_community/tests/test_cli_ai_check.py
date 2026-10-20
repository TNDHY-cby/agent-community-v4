# -*- coding: utf-8 -*-
"""守卫：**CLI 在需要 AI 之前就说清 AI 状态**（V70）。

起因：陌生人是**提交完任务**才看到 `getaddrinfo fail` —— 那是"失败后才解释"。
改成：
 ① `cli task` 提交**之前**先打一行 AI 状态（只读缓存，不烧钱）；
 ② 新增 `cli ai-check` 一键自检（真跑一次极小探针；10 分钟内不重复花钱）；
 ③ 未配置/验不通时，必须给出「原因 + 怎么办」，不许只丢技术串。

判据按**真行为**（跑真 CLI、打真端点），不查源码文本。
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.append(str(_ROOT))


def free_port() -> int:
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])
    finally:
        s.close()


def _env(**over) -> dict:
    keep = ("PATH", "PYTHON", "SYSTEM", "WINDIR", "TEMP", "TMP", "HOME", "USER",
            "APPDATA", "LOCALAPPDATA", "COMSPEC", "PATHEXT")
    e = {k: v for k, v in os.environ.items()
         if not k.startswith("AC_") and not k.startswith("AC4_") and k.upper().startswith(keep)}
    e.update({k: str(v) for k, v in over.items()})
    return e


def _run_cli(args: list[str], data_dir: Path, port: int, timeout: float = 90.0):
    env = _env(AC_PORT=port, AC_DATA_DIR=str(data_dir), PYTHONPATH=str(_ROOT),
               PYTHONIOENCODING="utf-8", AC_PYTHON=sys.executable)
    return subprocess.run([sys.executable, "-m", "agent_community.cli", *args],
                          cwd=str(_ROOT), env=env, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout)


@pytest.fixture()
def server(tmp_path):
    """起一个真实例（未配 AI），并保证收尾停掉。"""
    port = free_port()
    env = _env(AC_PORT=port, AC_DATA_DIR=str(tmp_path / "data"), PYTHONPATH=str(_ROOT),
               PYTHONIOENCODING="utf-8", AC_PYTHON=sys.executable)
    p = subprocess.Popen([sys.executable, "-m", "agent_community.platform.server"],
                         cwd=str(_ROOT), env=env, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)
    base = "http://127.0.0.1:%d" % port
    for _ in range(120):
        try:
            with urllib.request.urlopen(base + "/api/status", timeout=2) as r:
                if r.status == 200:
                    break
        except Exception:                                    # noqa: BLE001
            pass
        time.sleep(0.5)
    yield port, tmp_path / "data"
    try:
        p.terminate()
        p.wait(timeout=15)
    except Exception:                                        # noqa: BLE001
        try:
            p.kill()
        except Exception:                                    # noqa: BLE001
            pass


def test_ai_check_says_how_to_fix_when_unconfigured(server):
    """未配 AI ⇒ 说清「原因」与「怎么办」，而不是只丢一个 HTTP 码。"""
    port, data = server
    r = _run_cli(["ai-check"], data, port)
    out = (r.stdout or "") + (r.stderr or "")
    assert r.returncode == 1, "验不通时该是非零退出码：%s" % out[:300]
    assert "原因：" in out, "必须说原因：%s" % out[:300]
    assert "怎么办：" in out, "必须说怎么办：%s" % out[:300]
    assert "没有 AI 也能用平台" in out, "要说清没 AI 也不影响平台本身：%s" % out[:300]


def test_task_prints_ai_status_before_submitting(server):
    """`cli task` **提交之前**就要打一行 AI 状态（不是提交后才解释）。"""
    port, data = server
    r = _run_cli(["task", "写一个排序函数"], data, port)
    out = (r.stdout or "") + (r.stderr or "")
    assert "AI 状态：" in out, "提交前该有一行 AI 状态：%s" % out[:400]
    # 顺序：AI 状态那行必须出现在「任务已提交」之前
    i_ai = out.find("AI 状态：")
    i_submit = out.find("任务已提交")
    assert i_ai != -1 and i_submit != -1
    assert i_ai < i_submit, "AI 状态必须在提交之前打印（实际顺序反了）"


def test_ai_check_without_running_service_is_explicit(tmp_path):
    """服务没起时，`ai-check` 也要说清下一步（提示先 start），不许静默失败。"""
    port = free_port()                        # 没有任何服务在听
    r = _run_cli(["ai-check"], tmp_path, port, timeout=60)
    out = (r.stdout or "") + (r.stderr or "")
    assert r.returncode == 1
    assert "服务未运行" in out or "连不上本机平台" in out, out[:300]
