# -*- coding: utf-8 -*-
"""spawn：**起子进程的平台差异，只有这一处**。

老板 2026-10-14 的 CI 把它逼出来了 —— Linux 上"一键注册"直接 500：

    {"error": "桥启动失败: module 'subprocess' has no attribute 'CREATE_NEW_PROCESS_GROUP'"}

`CREATE_NEW_PROCESS_GROUP` / `DETACHED_PROCESS` / `CREATE_NO_WINDOW` **只存在于 Windows**
（`subprocess` 在其它平台上根本没有这些属性）⇒ 无条件写它们 = 平台专属代码伪装成通用代码。
实测这份代码里散在 **3 处**（`bridge_supervisor` / `routers/harness_messaging` /
`local_ai` 里还写成了硬编码 `0x8 | 0x200`），另有两处已自行做了平台分支
（`harness_launcher` / `cli`）—— 同一件事五套写法（规则 20）。

判据：**"让子进程脱离父进程/不进同一进程组"在两个平台上的表达方式不同**：
  · Windows：`creationflags = CREATE_NEW_PROCESS_GROUP [| DETACHED_PROCESS]`
  · POSIX  ：`start_new_session=True`（等价语义：新会话/新进程组，父死不被带走）
所以不要在各处 `if sys.platform == ...`，用这里的 `spawn_kwargs()` 拼 kwargs。
"""
from __future__ import annotations

import os
import subprocess

#: ★**不要**在别处读 `os.name` 来判断本模块的行为；也不要 monkeypatch `os.name`——
#:   那是**全局副作用**：CPython 3.11 的 `pathlib.Path.__new__` 是实例化时读 `os.name`
#:   来决定造 Posix/WindowsPath 的，改一下全局就会让别处造出 `WindowsPath`，
#:   逃出去之后在 Linux 上炸成 `NotImplementedError: cannot instantiate 'WindowsPath'`
#:   （实测：pytest 自己的失败报告就会中招，整场变 INTERNALERROR）。
#:   需要测试另一个平台分支，就 patch **这个开关**。
_IS_WINDOWS = os.name == "nt"


def spawn_kwargs(*, detach: bool = True) -> dict:
    """返回 `subprocess.Popen(..., **spawn_kwargs())` 用的平台相关 kwargs。

    `detach=True`（默认）＝让子进程**独立成组/会话**（父进程退出时不被连带杀掉）。
    ⚠️ 一律用 `getattr(subprocess, ...)` 兜底：即使将来在某个平台缺属性，也只是少个标志，
    **不会把功能整个打成 500**。
    """
    if _IS_WINDOWS:
        if not detach:
            return {}
        flags = 0
        flags |= getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        flags |= getattr(subprocess, "DETACHED_PROCESS", 0)
        return {"creationflags": flags} if flags else {}
    return {"start_new_session": True} if detach else {}
