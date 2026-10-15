# -*- coding: utf-8 -*-
"""stdio_utf8：把**本进程的标准输出/错误**钉在 UTF-8 —— 全项目**一处真相**。

## 为什么必须有它（2026-10-15：外部用户真跑抓到 → 本机按同样条件复现）

外部朋友按 README 第 2 步跑 `python -m agent_community.cli start --no-gui`：
CLI 起了后台进程、几秒后报「服务启动超时」，日志里只有一句崩溃 ——

```text
UnicodeEncodeError: 'gbk' codec can't encode character '\u21d2' in position 13
  File ".../agent_community/platform/server.py", line 6277, in <module>
    print("[访问] 未配置访问令牌 ⇒ **只有本机能访问**（要开外部访问：设 AC4_TOKEN=…）")
```

**根因**：`cli start` 把 server 的 stdout/stderr **重定向到日志文件**，此时 Python 用的是
**区域编码**（中文 Windows = cp936；英文 Windows = cp1252），启动横幅里的 `⇒`（U+21D2）
编不出去 ⇒ **服务在启动的第一秒就死**。英文 Windows 更狠：**连中文横幅都编不出去**。

⚠️ 这条 bug **有真控制台时不发作**（PEP 528：真控制台走 WriteConsoleW）——
所以开发者本机往往看不到它，它专挑「陌生人的第一次」发作（正是本项目最反对的那类私人定制缺陷）。

**修法**：进程一进来就把 stdout/stderr 钉在 `encoding="utf-8", errors="replace"`。
桥模板 / `platform/procprobe.py` / 示例桥早就各自这么做了（守卫 `tests/test_bridge_encoding_guard.py`），
**平台自己的入口一直没做** —— 本模块就是那一份，别再各写一遍。

**纪律**：尽力而为、**绝不反过来把服务搞崩** —— 流不支持 `reconfigure` 就跳过，不 raise
（例如被别的东西包过的流、或已关闭的流）。
"""
from __future__ import annotations

import sys

#: 钉在什么编码/错误策略上（**一处真相**：要改就改这里，别在调用点各写一套）
STDIO_ENCODING = "utf-8"
STDIO_ERRORS = "replace"


def force_utf8_stdio(*streams) -> list:
    """把 stdout/stderr（以及 `streams` 里额外给的流）钉在 UTF-8；返回**真的改到的流名**。

    幂等、可重复调用；**任何一次失败都不影响调用方**（哪些没改到会体现在返回值里，
    而不会抛异常 —— "自保动作"不该变成新的崩溃点）。
    """
    targets = [("stdout", getattr(sys, "stdout", None)),
               ("stderr", getattr(sys, "stderr", None))]
    targets += [("extra%d" % i, s) for i, s in enumerate(streams)]
    done: list[str] = []
    for name, stream in targets:
        if stream is None:
            continue
        try:
            stream.reconfigure(encoding=STDIO_ENCODING, errors=STDIO_ERRORS)
        except Exception:  # noqa: BLE001 —— 流不支持就跳过（见模块 docstring 的纪律）
            continue
        done.append(name)
    return done
