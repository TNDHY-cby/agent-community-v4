# -*- coding: utf-8 -*-
"""`platform/bridge_wake.py`：给已接入的 harness 配「叫醒命令」（含包装脚本）。

老板 2026-10-11 的真实缺口：**桥在跑、消息送到了，但 agent 不会被叫醒** ——
因为生成桥里的 `TRIGGER_CMD` 是空的。本模块把"配这一项"变成一次可控的写操作。

两条实测教训写进了实现，本文件锁住它们：
1. ★**带空格/带参数的触发命令会拆坏 argv** ⇒ **必须包一层脚本**（不把裸命令塞参数）；
2. ★**不接受请求体里的路径**（防任意文件写入）⇒ 包装脚本只写在平台自己的 runtime 目录。
"""
from __future__ import annotations

import os

from agent_community.platform import bridge_wake as bw


def test_wrapper_written_and_read_back(tmp_path):
    cmd = '"D:\\示例\\agent.exe" --acp'
    r = bw.write_wrapper(str(tmp_path), "示例Harness-A", cmd)
    assert r["ok"] is True
    assert os.path.isfile(r["path"])
    back = bw.read_wrapper(str(tmp_path), "示例Harness-A")
    assert back["configured"] is True
    assert "--acp" in back["command"], back


def test_empty_command_rejected_with_fix():
    v = bw.validate_command("")
    assert v["ok"] is False
    assert v["reason"] and v["fix"], "失败要说话：既说原因也给怎么改"


def test_multiline_command_rejected():
    """多行会被当成多条命令执行 ⇒ 拒绝，并给出正确做法。"""
    v = bw.validate_command("a\nb")
    assert v["ok"] is False
    assert "换行" in v["reason"]
    assert "脚本" in v["fix"]


def test_too_long_command_rejected():
    v = bw.validate_command("x" * 1000)
    assert v["ok"] is False
    assert "过长" in v["reason"]


def test_harness_id_cannot_escape_the_runtime_dir(tmp_path):
    """★安全：harness_id 里的 `../` 不得让它写到别处。"""
    r = bw.write_wrapper(str(tmp_path), "../../evil", "echo hi")
    assert r["ok"] is True
    p = os.path.abspath(r["path"])
    root = os.path.abspath(str(tmp_path))
    assert p.startswith(root), "包装脚本必须落在平台 runtime 目录内"
    base = os.path.basename(p)
    assert ".." not in base and "/" not in base and "\\" not in base


def test_read_wrapper_reports_not_configured(tmp_path):
    back = bw.read_wrapper(str(tmp_path), "没配过的harness")
    assert back["configured"] is False
    assert back["path"] == ""


def test_wrapper_lands_in_platform_runtime_not_user_path(tmp_path):
    """落点由平台派生（`agent_community/data/runtime/wake`），不是调用方给的路径。"""
    r = bw.write_wrapper(str(tmp_path), "h1", "echo hi")
    assert os.path.join("runtime", "wake") in r["path"].replace("/", os.sep)
