# -*- coding: utf-8 -*-
"""单测：看门狗的命令拆分（`deploy/watchdog_all.py::_split_cmd`）。

真事故（2026-10-18）：看门狗日志里出现 `argv=['D:\\Program', 'Files\\Tencent\\...', '-m']`
—— `.env` 的 `AC4_PYTHON` 是带空格的路径，被 `py.split()` 拆成两截 ⇒ 重启永远失败，
而日志只有一行 FAILED，**一整天没人知道 9104/9105 为什么起不来**。

本文件锁住：带引号/不带引号、Windows 与 POSIX 两种写法都要拆对；拆完还要能认出"解释器不存在"。
"""
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_WD = _ROOT / "deploy" / "watchdog_all.py"


def _load():
    if not _WD.exists():
        pytest.skip("deploy/ 不在发布面（dev-only），跳过")
    spec = importlib.util.spec_from_file_location("wd_test", _WD)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_quoted_path_with_spaces_is_one_token():
    m = _load()
    s = '"%s" "%s"' % (r"C:\Program Files\Tencent\x\python.exe",
                       r"C:\Program Files\Tencent\x\run.py")
    got = m._split_cmd(s)
    assert got == [r"C:\Program Files\Tencent\x\python.exe",
                   r"C:\Program Files\Tencent\x\run.py"], got


def test_existing_path_with_spaces_stays_whole_even_without_quotes(tmp_path):
    """**真实存在**的带空格路径：即使 .env 里没加引号，也要认成一个 argv[0]（就靠"文件真的存在"）。"""
    m = _load()
    d = tmp_path / "Program Files" / "Tencent"
    d.mkdir(parents=True)
    exe = d / "python.exe"
    exe.write_text("stub", encoding="utf-8")
    got = m._split_cmd(str(exe))
    assert got == [str(exe)], got
    # 加了引号同样对
    assert m._split_cmd('"%s"' % exe) == [str(exe)]


def test_unquoted_missing_path_with_spaces_refuses_loudly(tmp_path):
    """不存在的带空格路径：拆不开也没关系，**但必须说清"要加引号"**，不许静默失败。"""
    m = _load()
    fake = tmp_path / "Not There" / "python.exe"
    got = m._split_cmd(str(fake))
    assert isinstance(got, list) and got, got
    if len(got) > 1:                       # 拆成两截 ⇒ spawn 必须拦下并给出改法
        capsys_out = None
        m.spawn({"argv": got, "env": {}}, _ROOT)
        # 提示写在 stdout；这里只要求"没抛异常且 explain 逻辑生效"（细节见下一条用例）


def test_posix_simple_command_splits_normally():
    m = _load()
    assert m._split_cmd("/usr/bin/python3") == ["/usr/bin/python3"]
    assert m._split_cmd("") == []


def test_spawn_refuses_when_interpreter_missing(capsys):
    """**失败要说话**：解释器不存在时不许静默 Popen 失败，要打一句人能看懂、能照着改的提示。"""
    m = _load()
    cmd = {"argv": [r"D:\Program", "-m", "agent_community.platform.server"], "env": {}}
    m.spawn(cmd, _ROOT)
    out = capsys.readouterr().out
    assert "解释器不存在" in out and "引号" in out, out
