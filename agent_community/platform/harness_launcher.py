"""harness 进程启动器 v1

让平台 Agent 学会「出去启动 harness」的能力模块。

背景：平台此前只有三类消息投递（HTTP 回调 / file_poll 写 inbox / clipboard），
全部要求 harness 侧程序已在线；server.py 没有任何 subprocess/Popen/os.startfile
启动 harness 进程的代码。本模块补齐该缺口：

1. 检查 harness 是否在线（harness_manager.sessions 状态）
2. 不在线且注册信息含 acp_command 时，用 subprocess 拉起进程
3. 轮询等待上线（默认 60s），返回最终状态
4. 启动历史写入 data/harness_launch_log.json

acp_command 支持两种形态：
- "cmd /c ..." 或 "cmd.exe /c ..."：原样作为 shell 命令执行
- 其他：按命令行解析（支持引号包裹的 exe 路径）后直接执行
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

from .protocol import HarnessInfo, HarnessStatus

# 启动历史记录文件（与 server.py 的 DATA_DIR 对齐）
DATA_DIR = Path(__file__).resolve().parents[1] / "data"
LAUNCH_LOG = DATA_DIR / "harness_launch_log.json"

# 已拉起的进程句柄（防止被 GC 回收导致进程被杀）
_procs: dict[str, subprocess.Popen] = {}

# 并发启动锁
_launching: set[str] = set()


def _is_online(harness_id: str) -> bool:
    """通过 harness_manager.sessions 判断 harness 当前是否在线。"""
    try:
        from .harness_adapter import harness_manager
        sess = harness_manager.sessions.get(harness_id)
        return bool(sess and sess.status == HarnessStatus.ONLINE)
    except Exception:
        return False


def _mask_cmd(cmd: str) -> str:
    """V-10 修复：启动日志脱敏，只记录可执行文件首段与参数数量，不落完整命令行。"""
    cmd = (cmd or "").strip()
    parts = cmd.split()
    if not parts:
        return "<empty>"
    head = parts[0].strip('"')
    extra = len(parts) - 1
    return f"{head} [+{extra} args]" if extra else head


def _record_launch(harness_id: str, ok: bool, detail: str):
    """记录启动历史到 LAUNCH_LOG。"""
    try:
        entries = []
        if LAUNCH_LOG.exists():
            try:
                entries = json.loads(LAUNCH_LOG.read_text(encoding="utf-8"))
            except Exception:
                entries = []
        entries.append({
            "harness_id": harness_id,
            "ts": datetime.now().isoformat(),
            "ok": ok,
            "detail": detail[:500],
        })
        entries = entries[-100:]  # 只保留最近 100 条
        LAUNCH_LOG.parent.mkdir(parents=True, exist_ok=True)
        LAUNCH_LOG.write_text(
            json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception:
        pass


def _build_command(acp_command: str, acp_cwd: str) -> list[str]:
    """把 acp_command 解析成 Popen 用的 argv。

    - "cmd /c ..." / "cmd.exe /c ..."：原样保留（需要 shell 语义）
    - 其他：用 shlex 解析成 argv（Windows 上同时兼容 posix 解析结果）
    """
    cmd = acp_command.strip()
    lowered = cmd.lower()
    if lowered.startswith("cmd ") or lowered.startswith("cmd.exe "):
        return [cmd]  # 交给 shell=True 的 Popen
    if sys.platform == "win32":
        try:
            return shlex.split(cmd, posix=True)
        except Exception:
            return cmd.split()
    return shlex.split(cmd)


# V-4 修复：launch 端二次校验（防旧数据/绕过注册校验），黑名单与 server.py 对齐
_BLOCKED_EXES = {
    "cmd", "cmd.exe", "powershell", "powershell.exe", "pwsh", "pwsh.exe",
    "wscript", "wscript.exe", "cscript", "cscript.exe", "mshta", "mshta.exe",
    "rundll32", "rundll32.exe", "regsvr32", "regsvr32.exe",
    "forfiles", "certutil", "bitsadmin", "msiexec",
}
_BLOCKED_CHARS = (";", "|", "&", "<", ">", "`", "$")


def _validate_acp_command(acp_command: str) -> tuple[bool, str]:
    """RCE 防护：只接受「可执行文件 + 参数」形态，拒绝解释器包装与 shell 元字符。"""
    cmd = (acp_command or "").strip()
    if not cmd:
        return False, "acp_command 不能为空"
    if len(cmd) > 1024:
        return False, "acp_command 过长"
    for ch in _BLOCKED_CHARS:
        if ch in cmd:
            return False, f"acp_command 包含禁止的 shell 元字符: {ch}"
    first = cmd.split()[0].strip('"')
    if not first:
        return False, "acp_command 缺少可执行文件"
    exe = os.path.basename(first).lower()
    if exe in _BLOCKED_EXES:
        return False, f"acp_command 禁止使用解释器包装: {exe}"
    return True, ""


def launch_harness(harness_id: str, info: HarnessInfo) -> tuple[bool, str]:
    """按注册信息启动 harness 进程，返回 (是否成功, 说明)。"""
    if harness_id in _launching:
        return False, f"harness {harness_id} 正在启动中"
    cmd = (info.acp_command or "").strip()
    cwd = (info.acp_cwd or "").strip()
    if not cmd:
        return False, f"harness {harness_id} 未配置 acp_command，平台无法拉起（需先注册启动命令）"
    # V-4 修复：launch 端二次校验，拒绝解释器包装 / shell 元字符
    _ok, _err = _validate_acp_command(cmd)
    if not _ok:
        _record_launch(harness_id, False, f"acp_command 校验拒绝: {_err} cmd={_mask_cmd(cmd)}")
        return False, f"acp_command 校验拒绝: {_err}（如确需复杂启动命令，请改用注册脚本/封装 exe 作为首段可执行文件）"

    _launching.add(harness_id)
    try:
        argv = _build_command(cmd, cwd)
        kwargs: dict = {}
        if cwd:
            kwargs["cwd"] = cwd
        if sys.platform == "win32":
            kwargs["creationflags"] = (
                subprocess.CREATE_NEW_PROCESS_GROUP
                | subprocess.DETACHED_PROCESS
            )
            kwargs["shell"] = argv[0].lower().startswith("cmd ")
        else:
            kwargs["start_new_session"] = True

        proc = subprocess.Popen(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **kwargs)
        _procs[harness_id] = proc
        _record_launch(harness_id, True, f"launched pid={proc.pid} cmd={_mask_cmd(cmd)} cwd={cwd}")
        return True, f"已拉起 harness {harness_id}（pid={proc.pid}）"
    except Exception as e:
        _record_launch(harness_id, False, f"launch failed: {e} cmd={_mask_cmd(cmd)} cwd={cwd}")
        return False, f"启动 harness {harness_id} 失败: {e}"
    finally:
        _launching.discard(harness_id)


async def ensure_harness_online(
    harness_id: str,
    timeout: float = 60.0,
    poll_interval: float = 2.0,
) -> tuple[bool, str]:
    """确保 harness 在线：不在线且有 acp_command 时自动拉起并等待上线。

    Returns: (是否在线/是否成功拉起, 说明)
    """
    import asyncio

    if _is_online(harness_id):
        return True, f"harness {harness_id} 已在线"

    # 从注册表拿到 info
    try:
        from .harness_adapter import harness_manager
        info = None
        sess = harness_manager.sessions.get(harness_id)
        if sess and sess.info:
            info = sess.info
        if info is None:
            # 从持久化文件兜底读取
            data_file = DATA_DIR / "harnesses.json"
            if data_file.exists():
                raw = json.loads(data_file.read_text(encoding="utf-8"))
                if harness_id in raw:
                    info = HarnessInfo(**raw[harness_id])
    except Exception:
        info = None

    if info is None:
        return False, f"harness {harness_id} 未注册，无法启动"

    ok, msg = launch_harness(harness_id, info)
    if not ok:
        return False, msg

    # 轮询等待上线
    waited = 0.0
    while waited < timeout:
        await asyncio.sleep(poll_interval)
        waited += poll_interval
        if _is_online(harness_id):
            return True, f"harness {harness_id} 已启动并上线（等待 {waited:.0f}s）"
    return False, f"harness {harness_id} 已拉起进程，但 {timeout:.0f}s 内未收到上线心跳（进程可能启动失败或桥未连）"


def get_launch_log() -> list[dict]:
    """返回启动历史（供 API / 调试使用）。"""
    if not LAUNCH_LOG.exists():
        return []
    try:
        return json.loads(LAUNCH_LOG.read_text(encoding="utf-8"))
    except Exception:
        return []
