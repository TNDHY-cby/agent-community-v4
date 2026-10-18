# -*- coding: utf-8 -*-
"""守卫：**acp 档核心**（平台真用一条命令拉起 agent + 走通一轮 ACP 对话）。

背景（2026-10-18 核心体检抓到的真缺陷）：Windows 上 Python 子进程默认按 locale（GBK）往 stdout 打中文，
而 ACP 客户端按 UTF-8 读 ⇒ **读线程崩**、`prompt()` 永远空回复，界面上就是"agent 不吭声"。
修法：① 拉起子进程时强制 `PYTHONIOENCODING/PYTHONUTF8`；② 读取端 `errors="replace"`（不合规输出也不许把线程打死）。

本文件用 `tools/acp_core_check.py`（假 agent 对端）做**真跑**判据：
  · ⑦a 客户端真拉起 + initialize/session/new/session/prompt 一轮，回文里要有中文「收到」；
  · ⑦b 生成的 `cli_acp` 桥里注入的命令真被用上（模板占位符替换没丢）。
它比较慢（约 10 秒），但**这条慢得值**——它守住的是"平台能不能真的把 agent 叫起来"。
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
if _ROOT not in sys.path:
    sys.path.append(_ROOT)


def test_acp_core_check_is_green():
    tool = Path(_ROOT) / "tools" / "acp_core_check.py"
    assert tool.exists(), "缺 tools/acp_core_check.py"
    env = {**os.environ}
    env.pop("PYTHONPATH", None)
    r = subprocess.run([sys.executable, str(tool)], cwd=_ROOT, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=300, env=env)
    out = (r.stdout or "") + (r.stderr or "")
    assert r.returncode == 0, "acp 档核心体检有段失败：\n%s" % out[-1200:]
    for kw in ("⑦a 客户端真拉起", "⑦b 生成桥带着这条命令", "acp 档两段全绿"):
        assert kw in out, "输出里缺 '%s'：\n%s" % (kw, out[-800:])
    assert "收到" in out, "一轮对话没拿到中文回复（编码问题回归？）：\n%s" % out[-800:]


def test_fake_acp_agent_is_published_helper():
    """假 agent 是**测试助手**（随 tests 发布），别让 `tools/` 依赖一个不存在的东西。"""
    fake = Path(_ROOT) / "agent_community" / "tests" / "_fake_acp_agent.py"
    assert fake.exists(), "缺 agent_community/tests/_fake_acp_agent.py"
    src = fake.read_text(encoding="utf-8")
    for kw in ("initialize", "session/new", "session/prompt", "agent_message_chunk"):
        assert kw in src, "假 agent 没实现 %s" % kw


def test_acp_bridge_forces_utf8_on_child():
    """守卫源码级契约：拉起子进程时必须强制 UTF-8（Windows GBK 病灶的根因修复点）。"""
    br = Path(_ROOT) / "agent_community" / "platform" / "acp_harness_bridge.py"
    src = br.read_text(encoding="utf-8")
    assert "PYTHONIOENCODING" in src and "PYTHONUTF8" in src, \
        "拉起 ACP 子进程时没强制 UTF-8 —— Windows 上会 GBK 崩读线程"
    assert 'errors="replace"' in src, "读取端没做容错解码（不合规输出会把读线程打死）"
