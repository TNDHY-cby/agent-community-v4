# -*- coding: utf-8 -*-
"""动作脚本运行的**两个实测缺陷**（2026-10-11 修，中文 Windows 上会让"人工模式"整体失效）。

**缺陷一（要命）**：`subprocess.run(..., text=True)` **没给 encoding** ⇒
Python 用区域默认（中文 Windows = **GBK**）解码子进程输出；而动作脚本骨架本身就打印中文 ⇒
读取线程抛 `UnicodeDecodeError: 'gbk' codec can't decode byte …` ⇒ **stdout 变空** ⇒
上层误报"stdout 不是合法 JSON" ⇒ **退出码 10 的人工模式被整体判失败**。
（这不只是测试问题：**"开箱即用"的默认路径在中文机器上就是坏的**。）

**缺陷二**：`run_action` 在检查退出码**之前**就因"stdout 不是合法 JSON"返回错误 ⇒
与 `EXIT_UNVERIFIED=10` 的语义（"成功但无法自证"）正好相反。

本文件用**会打印中文并 exit(10)** 的脚本复现两种情形，锁住修复。
"""
from __future__ import annotations

import sys

import pytest

from agent_community.platform import action_scripts as mod


def _manual_like_script(tmp_path, *, chinese=True, code=10):
    """造一个"像 L1 骨架那样"的脚本：打印中文给人看，然后按约定退出码退出。"""
    p = tmp_path / "manual_script.py"
    msg = "请手动完成这一步：打开对话并改名" if chinese else "manual step required"
    p.write_text(
        "import sys\n"
        "print(%r)\n"
        "sys.exit(%d)\n" % (msg, code),
        encoding="utf-8",
    )
    return p


def test_non_ascii_stdout_is_not_lost(tmp_path):
    """★缺陷一：中文输出必须能读回来（不能因 GBK 解码失败而变空）。"""
    p = tmp_path / "cn.py"
    p.write_text("import json,sys;print(json.dumps({'action':'x','evidence':{'k':1},"
                 "'note':'中文不该丢'}));sys.exit(0)\n", encoding="utf-8")
    r = mod.run_action(p, "new_session", {}, python_exe=sys.executable)
    assert r["ok"] is True, f"中文输出被吞了：{r}"
    assert r["verified"] is True
    assert r["evidence"] == {"k": 1}


def test_exit_10_with_chinese_stdout_is_success_unverified(tmp_path):
    """★两个缺陷合体：退出码 10 + 中文 stdout ⇒ 必须 ok=True / verified=False（人工模式）。"""
    p = _manual_like_script(tmp_path, chinese=True, code=10)
    r = mod.run_action(p, "new_session", {}, python_exe=sys.executable)
    assert r["exit_code"] == 10
    assert r["ok"] is True, f"人工模式（退出码 10）被判失败：{r}"
    assert r["verified"] is False, "人工模式**必须**未核实"
    assert "GBK" not in str(r.get("error", "")).upper()


def test_exit_10_with_no_stdout_is_still_success_unverified(tmp_path):
    """★缺陷二单独锁：连 stdout 都没有（脚本只退出）也必须按 10 的语义算成功未核实。"""
    p = tmp_path / "silent.py"
    p.write_text("import sys\nsys.exit(10)\n", encoding="utf-8")
    r = mod.run_action(p, "new_session", {}, python_exe=sys.executable)
    assert r["exit_code"] == 10
    assert r["ok"] is True, f"退出码 10 不该因 stdout 空就判失败：{r}"
    assert r["verified"] is False


def test_exit_0_with_broken_stdout_still_reported_as_failure(tmp_path):
    """反向断言（别把顺序改回去）：退出码 0 却没给可解析 JSON ⇒ 仍必须**如实报失败**。"""
    p = tmp_path / "broken.py"
    p.write_text("print('done but not json')\n", encoding="utf-8")
    r = mod.run_action(p, "new_session", {}, python_exe=sys.executable)
    assert r["exit_code"] == 0
    assert r["ok"] is False, f"退出码 0 + 无 JSON ⇒ 不能算成功（准则⑤）：{r}"
    assert "JSON" in r["error"]


def test_other_exit_code_is_failure(tmp_path):
    """退出码既非 0 也非 10 ⇒ 失败。"""
    p = tmp_path / "boom.py"
    p.write_text("import sys\nsys.exit(3)\n", encoding="utf-8")
    r = mod.run_action(p, "new_session", {}, python_exe=sys.executable)
    assert r["ok"] is False
    assert r["exit_code"] == 3


def test_run_action_always_decodes_as_utf8(tmp_path):
    """源码级守卫：`subprocess.run` 必须显式指定 encoding（否则中文 Windows 上必然重蹈覆辙）。"""
    import inspect
    src = inspect.getsource(mod.run_action)
    assert "encoding=" in src, \
        "subprocess.run 没给 encoding —— 中文 Windows 上会按 GBK 解码并吞掉输出"


def test_written_manual_skeleton_actually_runs(tmp_path):
    """端到端：平台**自己生成**的 L1 骨架（人工模式）必须跑得通且如实说未核实。

    这条最贴近用户：不填任何命令时，"开箱即用"默认路径就是它。
    """
    p = tmp_path / "new_session.py"
    mod.write_action_script("new_session", {"HARNESS_ID": "h1",
                                            "HARNESS_NAME": "示例Harness-A"}, p)
    r = mod.run_action(p, "new_session", {"name_hint": "w1-项目-码农-m1"},
                       python_exe=sys.executable)
    assert r["ok"] is True, f"平台自带骨架跑失败＝开箱即用坏了：{r}"
    assert r["verified"] is False
    assert r["exit_code"] == 10
