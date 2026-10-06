# -*- coding: utf-8 -*-
"""看门狗 `--spawn-cmd` 切分单测（2026-10-06 修）。

锁定的是**真实踩到的回归**：本仓库路径含空格（`…（External Agent Community）`），
原先 `str.split()` 会把含空格的路径切两半 → 子进程报
`can't open file 'D:\\DSH工作区1\\外端Agent生产合作社（External'`。
"""
from __future__ import annotations

import importlib.util
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

_WD = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "tools", "watchdog.py")


def _load():
    spec = importlib.util.spec_from_file_location("_wd_under_test", _WD)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


wd = _load()


class TestSplitSpawnCmd:
    def test_plain_module_form_unchanged(self):
        """向后兼容：无引号、无空格的旧写法必须一字不差。"""
        s = "python -m agent_community.platform.server --port 18920 --token T"
        assert wd._split_spawn_cmd(s) == [
            "python", "-m", "agent_community.platform.server", "--port", "18920",
            "--token", "T"]

    def test_quoted_path_with_spaces_stays_one_token(self):
        """带引号且含空格的路径必须保持**单个** token（本次修的正是这条）。"""
        s = r'python "D:\DSH工作区1\外端Agent生产合作社（External Agent Community）\temp\fake_service.py" 18921'
        toks = wd._split_spawn_cmd(s)
        assert len(toks) == 3, toks
        assert toks[1] == (r"D:\DSH工作区1\外端Agent生产合作社（External Agent Community）"
                           r"\temp\fake_service.py")
        assert toks[2] == "18921"

    def test_windows_backslashes_are_not_eaten(self):
        """posix 模式会把 `\\D` 当转义吞掉 —— 必须保住反斜杠。"""
        s = r'python "D:\a\b\c.py" 1'
        assert wd._split_spawn_cmd(s)[1] == r"D:\a\b\c.py"

    def test_unquoted_path_without_spaces_still_works(self):
        s = r"python D:\no_space\x.py 18921"
        assert wd._split_spawn_cmd(s) == ["python", r"D:\no_space\x.py", "18921"]

    def test_single_quoted_path(self):
        s = "python 'D:\\a b\\x.py' 2"
        assert wd._split_spawn_cmd(s)[1] == r"D:\a b\x.py"

    def test_unbalanced_quote_falls_back_without_raising(self):
        """不配对引号不能让看门狗起来就崩 —— 退回朴素切分。"""
        toks = wd._split_spawn_cmd('python "D:\\broken 18921')
        assert toks and toks[0] == "python"

    def test_empty_string(self):
        assert wd._split_spawn_cmd("") == []

    def test_none_is_tolerated(self):
        assert wd._split_spawn_cmd(None) == []
