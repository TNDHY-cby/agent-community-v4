# -*- coding: utf-8 -*-
"""示例桥脚本的**输出编码自保**（2026-10-11 实测缺陷的回归守卫）。

**缺陷**：`examples/filepoll_harness_bridge.py` / `pending_poll_bridge.py` 会打印 `✓` / `✗`。
在**中文 Windows** 上 stdout 的编码随控制台码页（多为 GBK）⇒ 打印这两个字符抛
`UnicodeEncodeError: 'gbk' codec can't encode character '\u2717'` ⇒ **把桥进程弄死**
（实测：桥起来后一自检就异常，表现为"桥不起作用"）。

**修法**：脚本开头自己 `sys.stdout.reconfigure(encoding="utf-8", errors="replace")`，
不要求用户记住设 `PYTHONIOENCODING`（规则 19：别把环境差异变成用户的坑）。

**★2026-10-14 扩面（CI run #11 逼出来的第二条同类缺陷）**：
上面只守了 `examples/` 两个脚本，**平台渲染给用户的三个桥模板一个都没守** ——
而它们同样是"桥"、同样会 print 中文（cli_acp 21 处 / file_poll 26 处 / pending_poll 37 处）。
用户的 stdout 一旦被**重定向到文件**（验证间、看门狗、nohup、计划任务……），
Python 就按**系统 ANSI 代码页**定编码：英文 Windows 是 **cp1252**，
**连一个汉字都编不出来** ⇒ `UnicodeEncodeError` ⇒ 桥进程当场死，
而用户只看到"桥不取件/不回话"。实测（run #11 的 windows runner）同一个坑还把
**测试里的假 agent** 弄死过：它只打印了一行含中文成员目录（`验证员_v0`）的路径，
就在写回报之前崩了 ⇒ 接入自检永远 `no_reply`（本机 cp936 完全复现不出来）。

本文件做两层守卫：
  ① **源码级**（不依赖测试机码页）：自保代码在、且在**任何 print 之前**、有 try 保护；
  ② **行为级**（真渲染模板 + 真在 cp1252 下跑一遍）：见 `test_rendered_template_...`。
"""
from __future__ import annotations

import io
import os
import re
import subprocess
import sys

import pytest

SCRIPTS = [
    "agent_community/examples/filepoll_harness_bridge.py",
    "agent_community/examples/pending_poll_bridge.py",
    # ★2026-10-14：平台渲染给用户的桥模板（此前完全没守）
    "agent_community/bridge_templates/file_poll/bridge.py.tmpl",
    "agent_community/bridge_templates/pending_poll/bridge.py.tmpl",
    "agent_community/bridge_templates/cli_acp/bridge.py.tmpl",
]


def _src(p: str) -> str:
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    with io.open(os.path.join(root, p), encoding="utf-8", errors="replace") as f:
        return f.read()


@pytest.mark.parametrize("p", SCRIPTS)
def test_script_reconfigures_output_encoding(p):
    """脚本必须自己把 stdout/stderr 改成 UTF-8 + 出错不炸。"""
    s = _src(p)
    assert "reconfigure(" in s, f"{p} 缺少输出编码自保（中文 Windows 上会被 ✓/✗ 弄死）"
    assert "errors=\"replace\"" in s or "errors='replace'" in s, \
        f"{p} 的 reconfigure 应带 errors=replace（否则别的字符仍可能炸）"
    assert "sys.stdout" in s and "sys.stderr" in s


@pytest.mark.parametrize("p", SCRIPTS)
def test_reconfigure_runs_before_any_print(p):
    """自保必须发生在**任何打印之前**（否则第一行就炸了）。"""
    s = _src(p)
    rec = s.find("reconfigure(")
    pr = s.find("print(")
    assert rec > 0, f"{p} 没有 reconfigure"
    assert pr > 0, f"{p} 没有任何 print（可疑）"
    assert rec < pr, f"{p} 的 reconfigure 出现在 print 之后 —— 太晚了"


@pytest.mark.parametrize("p", SCRIPTS)
def test_reconfigure_failure_does_not_crash(p):
    """老解释器没有 reconfigure 时，不能因此崩（要有 try/except）。"""
    s = _src(p)
    i = s.find("reconfigure(")
    head = s[max(0, i - 600):i]
    assert "try:" in head, f"{p} 的 reconfigure 没有 try 保护"


def test_scripts_still_carry_the_non_gbk_glyphs():
    """这两个脚本**仍然**打印 ✓/✗（所以自保必需）——若哪天改成纯 ASCII，这条会提醒我们。"""
    s = _src(SCRIPTS[0])
    assert "✓" in s or "✗" in s, "字符没了？那应同步更新本测试的说明（正文注释）"


# ── ★行为级守卫（2026-10-14：按**真结果**判，不按源码文本判）──────────────────
#
# 判据为什么用 `--help`：
#   · 它在**任何平台上都秒退**（不起常驻桥、不碰网络、不留进程），
#   · 而 argparse 一定会把**中文描述**打到 stdout ⇒ **必然经过"输出编码"这一步**。
# 实证（本机 2026-10-14 实测，加自保前 / 加自保后）：
#   旧模板：rc=1，stdout 里是 `UnicodeEncodeError: 'charmap' codec ...`（桥在启动打印时死）
#   新模板：rc=0，中文正常打出来。
# ⇒ 这条守卫**有牙**：谁把那段自保删了，它立刻红。
_RENDER_PARAMS = {
    "file_poll": {"HARNESS_ID": "示例Harness-A", "INBOX_DIR": "/tmp/ac-inbox",
                  "TRIGGER_CMD": "", "PLATFORM_URL": "http://127.0.0.1:1",
                  "AGENT_TOKEN": "sk-your-agent-token"},
    "pending_poll": {"HARNESS_ID": "示例Harness-A", "WORK_DIR": "/tmp/ac-work",
                     "TRIGGER_CMD": "", "PLATFORM_URL": "http://127.0.0.1:1",
                     "AGENT_TOKEN": "sk-your-agent-token"},
    "cli_acp": {"HARNESS_ID": "示例Harness-A", "ACP_COMMAND": "python -m your_agent",
                "ACP_CWD": "/tmp", "MODEL_NAME": "示例模型", "PROVIDER": "openai",
                "DESCRIPTION": "示例接入", "AGENT_TOKEN": "sk-your-agent-token"},
}


@pytest.mark.parametrize("name", sorted(_RENDER_PARAMS))
def test_rendered_template_survives_a_non_utf8_console(name, tmp_path):
    """★真渲染 + 真在 cp1252 下跑：桥必须**活着把中文打出来**（不许被编码弄死）。

    `cp1252` = 英文 Windows 的 ANSI 代码页，也就是 GitHub windows runner 上
    子进程 stdout 的默认编码（实测 run #11）。它编不出汉字 —— 这正是缺陷的生存条件。
    """
    from agent_community.platform import bridge_factory
    text = bridge_factory.render(name, dict(_RENDER_PARAMS[name]))
    p = tmp_path / "bridge.py"
    p.write_text(text, encoding="utf-8")

    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    env = dict(os.environ, PYTHONIOENCODING="cp1252")
    # cli_acp 模板会 import 平台模块 ⇒ 给它项目根（与用户真实运行环境同口径）
    env["PYTHONPATH"] = root + os.pathsep + env.get("PYTHONPATH", "")
    r = subprocess.run([sys.executable, str(p), "--help"], capture_output=True,
                       env=env, timeout=120, cwd=str(tmp_path))
    out = (r.stdout + r.stderr).decode("utf-8", "replace")

    assert "UnicodeEncodeError" not in out, (
        "桥在 cp1252（英文 Windows 的默认 stdout 编码）下被自己的中文打印弄死了 —— "
        "用户那边看到的是「桥不取件/不回话」。修法：脚本开头 "
        "`sys.stdout.reconfigure(encoding=\"utf-8\", errors=\"replace\")`。\n"
        "实测输出尾部：%r" % out[-400:])
    assert r.returncode == 0, "渲染出来的桥跑 `--help` 就退非 0：rc=%s\n输出尾部：%r" % (
        r.returncode, out[-400:])
    assert re.search(r"[\u4e00-\u9fa5]", out), \
        "中文没打出来 —— 本守卫的判据失效了（假设 argparse 会打中文说明）：%r" % out[-300:]
