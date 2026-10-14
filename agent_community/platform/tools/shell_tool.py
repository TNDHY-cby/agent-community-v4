"""Shell 命令执行工具。"""

from __future__ import annotations
import asyncio
import os as _os

from ..core.security import _is_dangerous  # V-9 单体拆分：危险命令检测下沉 core/security.py
from ..tool_registry import BaseTool, ToolResult, ToolSchema

#: 本机"跑一条命令"的解释器 —— **平台差异只有这一处**（别再在别处拼 argv）。
#: Windows: PowerShell（`-Command`）；POSIX: `/bin/sh -c`（语义对应）。
#: ★2026-10-14（CI run #11 的 Linux 真因）：原来只有一处 `("-NoProfile","-NonInteractive",
#:   "-Command")` 硬套在两端 —— 在 Linux 上就是 `/bin/sh -NoProfile …`，
#:   sh 把 `-N` 当成自己的选项 ⇒ 直接 `Illegal option -N`、退出码 2
#:   ⇒ **"命令执行"这条能力在 Linux 上等于没有**（而开发机是 Windows，看不见）。
_IS_WINDOWS = _os.name == "nt"
_LAUNCHER = "powershell.exe" if _IS_WINDOWS else "/bin/sh"
_SHELL_LABEL = "PowerShell" if _IS_WINDOWS else "POSIX shell（sh）"


def _shell_argv(command: str) -> list:
    """「执行一条命令」的 argv（按平台给**各自真正能用**的形状）。"""
    if _IS_WINDOWS:
        return ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command]
    return ["/bin/sh", "-c", command]


class ShellExecTool(BaseTool):
    """在本机执行 %s 命令（Windows 上是 PowerShell，Linux/macOS 上是 sh）。""" % _SHELL_LABEL

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name="shell_exec",
            description=(
                "在本机执行 %s 命令。用于文件管理（列出目录、复制/移动文件等）、"
                "系统信息查询（磁盘空间、进程列表、环境变量等）、文本处理（搜索/过滤/统计）、"
                "网络诊断（ping、ipconfig、netstat 等）等非破坏性操作。\n\n"
                "禁止执行：删除文件/目录、格式化磁盘、修改注册表、停止服务、关机/重启等破坏性操作。"
                "超时限制 30 秒。" % _SHELL_LABEL
            ),
            parameters={
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "要执行的命令字符串（本机是 %s）" % _SHELL_LABEL,
                    },
                },
                "required": ["command"],
            },
        )

    async def execute(self, **params) -> ToolResult:
        command = params.get("command", "")
        if not command:
            return ToolResult(tool_name="shell_exec", success=False, content="缺少参数: command")

        # ── 第一道：危险命令黑名单（不可移除的底线）────────────────
        # V-14 起黑名单**同时**作为策略引擎的 builtin.danger 规则存在，这里保留原样
        # 是为了纵深防御：即使策略配置被改坏/被删规则，黑名单仍然拦得住
        # （设计稿 §二「不改现有 _is_dangerous 的黑名单语义」）。
        danger = _is_dangerous(command)
        if danger:
            return ToolResult(
                tool_name="shell_exec",
                success=False,
                content=f"安全拦截: {danger}\n如需执行此操作，请通过本地终端手动处理。",
            )

        # ── 第二道：策略闸门 ALLOW / DENY / ASK（V-14）──────────────
        # 工具层的唯一调用方是 react_loop（平台 AI），故 actor 固定为 platform_ai。
        from ..policy import (
            SHELL_EXEC,
            blocked_message,
            check,
            pending_message,
        )
        ev, pending = check(SHELL_EXEC, target=command)
        if ev.blocked:
            return ToolResult(
                tool_name="shell_exec", success=False,
                content=f"{blocked_message(ev)}\n规则: {ev.rule_id}",
            )
        if ev.needs_approval:
            # ASK：**绝不执行**，把待办 id 交回，等人批
            return ToolResult(
                tool_name="shell_exec", success=False,
                content=pending_message(ev, pending),
            )

        try:
            proc = await asyncio.create_subprocess_exec(
                # ★2026-10-14：原来无条件走 powershell ⇒ Linux 上「命令执行」这条能力等于没有。
                #   平台差异收进 `_shell_argv()` 一处（Windows: PowerShell -Command；
                #   POSIX: `/bin/sh -c`）。**别再在这里内联平台参数**：
                #   上一版就是把 PowerShell 的 `-NoProfile/-NonInteractive` 硬套给 `sh`，
                #   sh 只认它自己的选项 ⇒ `Illegal option -N`（run #11 的 Linux 真因）。
                *_shell_argv(command),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30.0)

            out = stdout.decode("utf-8", errors="replace").strip()
            err = stderr.decode("utf-8", errors="replace").strip()

            if proc.returncode != 0:
                err_detail = f"\nstderr: {err}" if err else ""
                return ToolResult(
                    tool_name="shell_exec",
                    success=False,
                    content=f"命令退出码 {proc.returncode}{err_detail}\n\n输出:\n{out}" if out else f"命令退出码 {proc.returncode}{err_detail}",
                )

            return ToolResult(
                tool_name="shell_exec",
                success=True,
                content=out if out else "(命令执行成功，无输出)",
            )
        except asyncio.TimeoutError:
            return ToolResult(tool_name="shell_exec", success=False, content="命令执行超时（30 秒）")
        except FileNotFoundError:
            # 失败要说话：说清**本机缺的是什么**（别在 Linux 上报"powershell.exe 未找到"）
            return ToolResult(tool_name="shell_exec", success=False,
                              content="%s 未找到（本机没有可用的 %s）" % (_LAUNCHER, _SHELL_LABEL))
        except Exception as e:
            return ToolResult(tool_name="shell_exec", success=False, content=f"执行失败: {type(e).__name__}: {e}")
