"""Shell 命令执行工具。"""

from __future__ import annotations
import asyncio
import re

from ..tool_registry import BaseTool, ToolResult, ToolSchema

# ── 危险命令模式（黑名单） ──────────────────────────────────
_DANGEROUS_PATTERNS: list[re.Pattern] = [
    re.compile(r"\brm\s+-rf\b", re.IGNORECASE),
    re.compile(r"\brmdir\b", re.IGNORECASE),
    re.compile(r"\bdel\s+/[fsq]\b", re.IGNORECASE),
    re.compile(r"\bformat\b", re.IGNORECASE),
    re.compile(r"\bdiskpart\b", re.IGNORECASE),
    re.compile(r"\breg\s+delete\b", re.IGNORECASE),
    re.compile(r"\breg\s+add\b", re.IGNORECASE),
    re.compile(r"\bRemove-Item\s+-Recurse\s+-Force\b", re.IGNORECASE),
    re.compile(r"\bnet\s+stop\b", re.IGNORECASE),
    re.compile(r"\bstop-service\b", re.IGNORECASE),
    re.compile(r"\bshutdown\b", re.IGNORECASE),
    re.compile(r"\brestart-computer\b", re.IGNORECASE),
    re.compile(r"\bstop-computer\b", re.IGNORECASE),
    re.compile(r">\\\\.\\[A-Z]:", re.IGNORECASE),  # 直接写磁盘设备
    re.compile(r":\\Windows\\", re.IGNORECASE),      # 操作系统目录
    re.compile(r":\\Program Files", re.IGNORECASE),  # 程序目录
]


def _is_dangerous(command: str) -> str | None:
    """检查命令是否包含危险操作。返回命中的模式描述或 None。"""
    for pattern in _DANGEROUS_PATTERNS:
        m = pattern.search(command)
        if m:
            return f"检测到危险命令模式: {m.group().strip()}"
    return None


class ShellExecTool(BaseTool):
    """在 Windows 上执行 PowerShell 命令。"""

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name="shell_exec",
            description=(
                "在 Windows 上执行 PowerShell 命令。用于文件管理（列出目录、复制/移动文件等）、"
                "系统信息查询（磁盘空间、进程列表、环境变量等）、文本处理（搜索/过滤/统计）、"
                "网络诊断（ping、ipconfig、netstat 等）等非破坏性操作。\n\n"
                "禁止执行：删除文件/目录、格式化磁盘、修改注册表、停止服务、关机/重启等破坏性操作。"
                "超时限制 30 秒。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "要执行的 PowerShell 命令字符串",
                    },
                },
                "required": ["command"],
            },
        )

    async def execute(self, **params) -> ToolResult:
        command = params.get("command", "")
        if not command:
            return ToolResult(tool_name="shell_exec", success=False, content="缺少参数: command")

        # 安全检查
        danger = _is_dangerous(command)
        if danger:
            return ToolResult(
                tool_name="shell_exec",
                success=False,
                content=f"安全拦截: {danger}\n如需执行此操作，请通过本地终端手动处理。",
            )

        try:
            proc = await asyncio.create_subprocess_exec(
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-Command", command,
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
            return ToolResult(tool_name="shell_exec", success=False, content="powershell.exe 未找到")
        except Exception as e:
            return ToolResult(tool_name="shell_exec", success=False, content=f"执行失败: {type(e).__name__}: {e}")
