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
    for kw in ("六种协议五步全绿", "①登记", "②叫醒脚本", "③桥模板", "④生成桥+自检", "⑤投递"):
        assert kw in out, "矩阵输出里缺 '%s'：\n%s" % (kw, out[-800:])


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
