# -*- coding: utf-8 -*-
"""守卫：**陌生人首跑走查必须包含「自愈」这一关**，且发布面真的带着看门狗。

为什么（2026-10-18）：`deploy/` 的看门狗不在发布面 ⇒ 陌生人包里服务一崩就再也起不来。
补上 `tools/watchdog.py` 之后，如果没人**在首跑判据里**验它，下次照样可能再丢 ——
所以把"包里自带看门狗 + 在全新 clone 里跑得绿"钉进首跑走查，并在这里钉住那条走查本身。

判据（机器可判）：
  ① `tools/first_run_check.py` 里存在 ⑥（包里自带看门狗）与 ⑥b（在全新 clone 里跑自愈判据）；
  ② ⑥ 是对着 **clone 出来的树**跑（不是对 dev）—— 否则"别人下载到的包"根本没被验过；
  ③ 发布面两个工具都在（`tools/watchdog.py`、`tools/check_watchdog.py`）。
"""
from __future__ import annotations

from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]


def test_first_run_check_has_selfheal_steps():
    src = (_ROOT / "tools" / "first_run_check.py").read_text(encoding="utf-8", errors="replace")
    assert "⑥ 包里自带看门狗" in src, "首跑走查没验「包里自带看门狗」"
    assert "⑥b 在全新 clone 里跑自愈判据" in src, "首跑走查没在**全新 clone** 里真跑自愈判据"
    assert 'clone / "tools" / "watchdog.py"' in src.replace("'", '"'), \
        "自愈那一步必须检查 **clone 出来的树**，不能看 dev 副本"
    assert "check_watchdog.py" in src


def test_published_face_ships_watchdog_tools():
    for rel in ("tools/watchdog.py", "tools/check_watchdog.py"):
        assert (_ROOT / rel).exists(), "发布面缺 %s（陌生人就没有自愈能力）" % rel


def test_watchdog_default_mode_is_check_not_daemon():
    """默认（不带参数）必须是**一次性体检**，不能默认常驻 —— 谁误敲一下不该多出一个守护进程。"""
    src = (_ROOT / "tools" / "watchdog.py").read_text(encoding="utf-8", errors="replace")
    assert 'ap.add_argument("--daemon"' in src
    tail = src.split("if __name__", 1)[0][-1200:]
    assert 'if a.daemon:' in tail and "return daemon(" in tail, "缺少 --daemon 分支"
    assert '--check' in src, "缺少 --check（一次性体检）"
