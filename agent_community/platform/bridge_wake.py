# -*- coding: utf-8 -*-
"""platform/bridge_wake：**给一个已接入的 harness 配上"叫醒命令"**（含包装脚本）。

设计稿：`V38_陌生人自服务架桥设计.md` §3 第 2 步（"你填"）+ 第 3 步（自检）
老板 2026-10-11 的真事故：**桥在跑、消息送到了，但 agent 不会被叫醒** —— 因为
生成桥里的 `TRIGGER_CMD` 是空的，而**没人问过用户"怎么叫醒它"**。

**为什么要包一层脚本（实测教训）**
──────────────────────────────────
`--trigger "C:\\Program Files\\x\\app.exe" --arg` 这种**带空格又带参数**的命令行，
传给桥时会被拆成多个 argv ⇒ `argparse: unrecognized arguments` ⇒ **桥根本起不来**。
⇒ 本模块不把裸命令塞参数，而是**在平台的 runtime 目录里写一个包装脚本**
（`wake_<harness_id>.cmd` / `.sh`），把用户给的命令原样写进去；
桥只需要拿这个脚本的路径 ⇒ **没有引号地狱**。

**安全边界（与 connect-artifact 同纪律）**
- **不接受请求体里的路径**（防任意文件写入）；包装脚本只写在**平台自己的 runtime 目录**；
- 用户给的"命令"只当作**脚本内容**（一行可执行命令），会做基本校验（非空、禁换行注入多行）。
"""
from __future__ import annotations

import os
import re
import time
from typing import Any

#: 包装脚本落点（平台自己的目录；不接收外部路径）。
#: ★2026-10-12 改为**相对平台真实数据目录**：`<DATA_DIR>/runtime/wake`。
#:   原来写死成 `agent_community/data/runtime/wake`（一个**仓库布局**的相对路径），
#:   于是：① 测试里 DATA_DIR 被重定向到 tmp，它却照样写进**真仓库**
#:        （被 conftest 的"污染真实数据"守卫抓到）；
#:        ② 部署形态一变（数据目录搬走）就会写到错地方。
#:   与项目其它落盘点（审计/状态/桥日志）**同一口径**：只认 `server.DATA_DIR`。
RUNTIME_SUBDIR = os.path.join("runtime", "wake")

_SAFE_ID = re.compile(r"[^A-Za-z0-9_.\-]")


def _data_root() -> str:
    """平台真实数据目录（唯一真相是 `server.DATA_DIR`）。"""
    try:
        from .server import DATA_DIR as _DD
        return str(_DD)
    except Exception:  # noqa: BLE001
        # 兜底：仅在 server 不可用时退回包内 data（正常运行时不会走到）
        return os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")


def _runtime_root(root: str = "") -> str:
    """包装脚本目录 = `<平台真实数据目录>/runtime/wake`。

    ⚠️`root` 参数**只为兼容旧调用方而保留，故意不再使用**：
    它曾让"仓库布局"成为落点依据 ⇒ 测试把 DATA_DIR 重定向到 tmp 时，
    脚本照样写进**真仓库**（实测被抓）；部署形态一变也会写错地方。
    **落点只认 `server.DATA_DIR`（与审计/状态/桥日志同一口径）。**
    """
    return os.path.join(_data_root(), RUNTIME_SUBDIR)


def safe_name(harness_id: str) -> str:
    """harness_id 进文件名前先转义（防路径穿越）。

    ⚠️ 实测教训：只替换 `/` `\\` **还不够**——`../../evil` 会变成 `.._.._evil`，
    虽然**已经不构成穿越**（没有路径分隔符），但文件名里留着 `..` 容易被误读、
    也可能在某些工具里引发歧义 ⇒ **把连续的点也压掉**。
    """
    s = _SAFE_ID.sub("_", str(harness_id or "").strip())
    s = re.sub(r"\.{2,}", "_", s)
    s = s.strip("._")
    return s or "harness"


def validate_command(cmd: str) -> dict:
    """校验用户给的"叫醒命令"（**失败要说话**：说清哪不对、怎么改）。"""
    c = (cmd or "").strip()
    if not c:
        return {"ok": False, "reason": "叫醒命令不能为空", "fix": "填一条能在你机器上把 agent 拉起来的命令"}
    if "\n" in c or "\r" in c:
        return {"ok": False, "reason": "叫醒命令不能包含换行（会被当成多条命令）",
                "fix": "只填**一条**命令；需要多步就自己写一个脚本，然后把脚本路径填进来"}
    if len(c) > 900:
        return {"ok": False, "reason": "叫醒命令过长（超过 900 字）",
                "fix": "写一个脚本，把脚本路径填进来即可"}
    return {"ok": True}


def write_wrapper(root: str, harness_id: str, command: str) -> dict:
    """把"叫醒命令"写成一个**包装脚本**，返回它的路径。

    返回 `{ok, path, kind, error}`；脚本落在平台 runtime 目录里（**不接受外部路径**）。
    """
    v = validate_command(command)
    if not v.get("ok"):
        return {"ok": False, "error": v["reason"], "fix": v.get("fix", "")}
    is_win = os.name == "nt"
    name = "wake_%s.%s" % (safe_name(harness_id), "cmd" if is_win else "sh")
    d = _runtime_root(root)
    try:
        os.makedirs(d, exist_ok=True)
        path = os.path.join(d, name)
        if is_win:
            body = "@echo off\r\nREM 平台生成的「叫醒命令」包装脚本（勿手改；改就在界面里改）\r\n" \
                   "REM harness_id: %s\r\nREM 生成于: %s\r\n%s\r\n" % (
                       harness_id, time.strftime("%Y-%m-%d %H:%M:%S"), command.strip())
            # 包装脚本本身用 ASCII + GBK 安全内容：命令原样写入（用户自己保证可执行）
            with open(path, "w", encoding="gbk", errors="replace", newline="") as f:
                f.write(body)
        else:
            body = "#!/bin/sh\n# 平台生成的「叫醒命令」包装脚本\n# harness_id: %s\n%s\n" % (
                harness_id, command.strip())
            with open(path, "w", encoding="utf-8") as f:
                f.write(body)
            os.chmod(path, 0o755)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}
    return {"ok": True, "path": path, "kind": "cmd" if is_win else "sh"}


def read_wrapper(root: str, harness_id: str) -> dict:
    """读回已配的包装脚本（给界面显示"现在配的是什么"）。"""
    for ext in ("cmd", "sh"):
        p = os.path.join(_runtime_root(root), "wake_%s.%s" % (safe_name(harness_id), ext))
        if os.path.isfile(p):
            try:
                raw = open(p, "r", encoding="gbk" if ext == "cmd" else "utf-8",
                           errors="replace").read()
            except Exception as e:  # noqa: BLE001
                return {"configured": True, "path": p, "error": str(e)}
            # 取最后一行非注释、非空的内容 = 用户那条命令
            cmds = [ln.strip() for ln in raw.splitlines()
                    if ln.strip() and not ln.strip().startswith(("REM", "#", "@echo", "::"))]
            return {"configured": True, "path": p, "command": cmds[-1] if cmds else ""}
    return {"configured": False, "path": "", "command": ""}
