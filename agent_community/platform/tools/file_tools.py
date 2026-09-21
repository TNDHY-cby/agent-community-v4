"""文件读写工具。"""

from __future__ import annotations
from pathlib import Path

from ..tool_registry import BaseTool, ToolResult, ToolSchema

# ── 敏感路径黑名单（防止误读密钥/配置） ─────────────────────
_SENSITIVE_PATTERNS = {".env", ".git", ".svn", ".ssh", ".aws", ".kube", "credentials", "secrets", "id_rsa", "id_ed25519"}


def _is_sensitive(path: str) -> bool:
    """检查路径是否命中敏感模式。"""
    lower = path.lower()
    return any(p in lower for p in _SENSITIVE_PATTERNS)


# ═══════════════════════════════════════════════════════════════
# read_file
# ═══════════════════════════════════════════════════════════════

class ReadFileTool(BaseTool):
    """按绝对路径读取文本文件内容。"""

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name="read_file",
            description="读取指定文本文件的内容。支持 .txt .md .py .json .yaml .html .css .js .csv .log 等纯文本格式。不支持二进制文件（PDF/DOCX/XLSX/图片/音视频等）。返回文件内容文本。",
            parameters={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "文件的绝对路径，如 D:\\Documents\\笔记.md",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "最大读取行数，默认 500。超长文件建议分页。",
                    },
                },
                "required": ["path"],
            },
        )

    async def execute(self, **params) -> ToolResult:
        path = params.get("path", "")
        limit = params.get("limit", 500)

        if not path:
            return ToolResult(tool_name="read_file", success=False, content="缺少参数: path")

        if _is_sensitive(path):
            return ToolResult(tool_name="read_file", success=False, content="安全限制: 无法读取敏感路径（可能包含密钥或配置）")

        p = Path(path)
        if not p.exists():
            return ToolResult(tool_name="read_file", success=False, content=f"文件不存在: {path}")
        if not p.is_file():
            return ToolResult(tool_name="read_file", success=False, content=f"路径不是文件: {path}")

        # 检查二进制扩展名
        binary_exts = {".pdf", ".docx", ".xlsx", ".pptx", ".png", ".jpg", ".jpeg",
                       ".gif", ".mp3", ".mp4", ".zip", ".exe", ".dll", ".pyd", ".so",
                       ".bin", ".dat", ".db", ".sqlite", ".sqlite3", ".ico", ".woff",
                       ".woff2", ".ttf", ".otf", ".webp", ".bmp", ".tiff", ".mov", ".avi",
                       ".mkv", ".rar", ".7z", ".gz", ".tar"}
        if p.suffix.lower() in binary_exts:
            return ToolResult(tool_name="read_file", success=False, content=f"不支持二进制文件类型: {p.suffix}")

        try:
            lines = []
            with open(p, "r", encoding="utf-8", errors="replace") as f:
                for i, line in enumerate(f):
                    if i >= limit:
                        lines.append(f"\n... (文件超过 {limit} 行，后续内容已截断)")
                        break
                    lines.append(line.rstrip("\n"))
            content = "\n".join(lines)
            return ToolResult(
                tool_name="read_file",
                success=True,
                content=f"文件: {path}\n共 {min(len(lines), limit)} 行\n---\n{content}",
            )
        except UnicodeDecodeError:
            return ToolResult(tool_name="read_file", success=False, content=f"文件不是 UTF-8 编码文本: {path}")
        except PermissionError:
            return ToolResult(tool_name="read_file", success=False, content=f"无权限读取: {path}")
        except Exception as e:
            return ToolResult(tool_name="read_file", success=False, content=f"读取失败: {type(e).__name__}: {e}")


# ═══════════════════════════════════════════════════════════════
# write_file
# ═══════════════════════════════════════════════════════════════

class WriteFileTool(BaseTool):
    """创建或覆盖文本文件。自动创建父目录。"""

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name="write_file",
            description="创建或覆盖文本文件。会自动创建不存在的父目录。注意：如果文件已存在，将被覆盖。仅支持纯文本内容（UTF-8 编码）。",
            parameters={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "文件绝对路径，如 D:\\output\\report.txt",
                    },
                    "content": {
                        "type": "string",
                        "description": "要写入文件的文本内容",
                    },
                },
                "required": ["path", "content"],
            },
        )

    async def execute(self, **params) -> ToolResult:
        path = params.get("path", "")
        content = params.get("content", "")

        if not path:
            return ToolResult(tool_name="write_file", success=False, content="缺少参数: path")
        if content is None:
            return ToolResult(tool_name="write_file", success=False, content="缺少参数: content")

        # 禁止写入系统关键目录
        p = Path(path).resolve()
        forbidden_prefixes = [
            Path("C:/Windows"),
            Path("C:/Program Files"),
            Path("C:/Program Files (x86)"),
        ]
        for prefix in forbidden_prefixes:
            try:
                p.relative_to(prefix)
                return ToolResult(tool_name="write_file", success=False, content=f"安全限制: 禁止写入系统目录 {prefix}")
            except ValueError:
                continue

        if _is_sensitive(path):
            return ToolResult(tool_name="write_file", success=False, content="安全限制: 无法写入敏感路径")

        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            with open(p, "w", encoding="utf-8") as f:
                f.write(content)
            return ToolResult(
                tool_name="write_file",
                success=True,
                content=f"写入成功: {path}\n大小: {len(content)} 字符",
            )
        except PermissionError:
            return ToolResult(tool_name="write_file", success=False, content=f"无权限写入: {path}")
        except Exception as e:
            return ToolResult(tool_name="write_file", success=False, content=f"写入失败: {type(e).__name__}: {e}")
