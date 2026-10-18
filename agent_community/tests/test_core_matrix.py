# -*- coding: utf-8 -*-
"""守卫：**核心矩阵**（六种协议 × 桥 × 唤醒脚本 × 投递）必须全绿。

老板 2026-10-18：「先把核心做好：各种协议、桥、唤醒脚本」。
这条守卫就是那句话的机器版：真起一个独立实例（换端口 + 换数据目录），
把六种协议各跑五步，任何一格红 ⇒ 这条测试红。

⚠️ 它比较慢（起一次服务 + 六轮 HTTP，约 20–40 秒），但**这条慢得值**：
本文件建立当天就抓出两个真缺陷（话术示例带 `<` 被校验拒、`http_api` 被报"不需要 wakeup_dir"的错）。
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
if _ROOT not in sys.path:
    sys.path.append(_ROOT)


def test_six_protocols_matrix_is_green():
    tool = Path(_ROOT) / "tools" / "protocol_matrix.py"
    assert tool.exists(), "缺 tools/protocol_matrix.py"
    env = {**os.environ}
    env.pop("PYTHONPATH", None)
    r = subprocess.run([sys.executable, str(tool)], cwd=_ROOT, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=600, env=env)
    out = (r.stdout or "") + (r.stderr or "")
    assert r.returncode == 0, "核心矩阵有格失败（详见输出）：\n%s" % out[-1500:]
    for kw in ("六种协议五步全绿", "闭环消费四段全绿",
               "①登记", "②叫醒脚本", "③桥模板", "④生成桥+自检", "⑤投递",
               "⑥a 派活落盘", "⑥b 桥真消费", "⑥c 回报转发", "⑥d 单实例锁"):
        assert kw in out, "矩阵输出里缺 '%s'：\n%s" % (kw, out[-800:])


def test_child_env_drops_outer_ac_vars(monkeypatch):
    """矩阵给子进程**只带白名单环境**：外头的 `AC_*` 一个都不许漏进去。

    真事故（2026-10-18）：矩阵单跑全绿，进了整套回归就红「★实例没起来」——
    因为 Pytest 里有些老用例直接改 `os.environ`（如 `AC_ENV=production`）没还原，
    而矩阵是**子进程**、全盘继承 ⇒ 平台服务按"生产模式缺 Token"启动失败。
    """
    import importlib.util
    monkeypatch.setenv("AC_ENV", "production")
    monkeypatch.setenv("AC_SERVER_TOKEN", "leaked-from-test")
    spec = importlib.util.spec_from_file_location("pm2", Path(_ROOT) / "tools" / "protocol_matrix.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    env = mod.child_env()
    assert "AC_ENV" not in env, "外头的 AC_ENV 漏进了子进程环境"
    assert "AC_SERVER_TOKEN" not in env, "外头的 AC_SERVER_TOKEN 漏进了子进程环境"
    for k in ("AC_PORT", "AC_DATA_DIR", "PYTHONPATH"):
        assert env.get(k), "白名单里必须有 %s" % k
    assert any(k.upper() == "PATH" for k in env), "系统必需项 PATH 被白名单误删了"


def test_matrix_declares_all_six_protocols():
    """矩阵必须覆盖 `register_prompt.SUPPORTED` 的全部协议（少一个就是漏测）。"""
    import importlib.util
    spec = importlib.util.spec_from_file_location("pm", Path(_ROOT) / "tools" / "protocol_matrix.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    from agent_community.platform import register_prompt as rp
    got = [p["method"] for p in mod.protocols()]
    assert sorted(got) == sorted(rp.SUPPORTED), "矩阵协议集与话术支持集不一致：%s vs %s" % (got, rp.SUPPORTED)
    assert len(got) == 6
