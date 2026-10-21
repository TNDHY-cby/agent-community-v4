# -*- coding: utf-8 -*-
"""产物统一出口（V75 P1-4）：`GET /api/artifact?path=…`。

一个口子取所有产物（工作间文件、任务结果里登记的路径），跨 local / Docker / 远端都一样。
安全边界见模块头部说明：只认数据目录这个根 + 规范化后仍在根内 + 只读 + 非回环要令牌。
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse

router = APIRouter()

#: 单文件上限（防止有人拿它当网盘；超了明确说，不偷偷截断）
MAX_BYTES = 64 * 1024 * 1024


def _roots() -> list:
    """允许的根目录（**唯一来源**：数据目录）。"""
    from ..env_spec import data_dir

    return [Path(data_dir()).resolve()]


@router.get("/api/artifact")
async def get_artifact(request: Request, path: str = ""):
    """按路径取一个产物文件（只读）。"""
    from ..server import Utf8JSONResponse

    raw = (path or "").strip()
    if not raw:
        return Utf8JSONResponse({"error": "path 必填（可以是数据目录内的相对路径或绝对路径）"},
                                status_code=400)

    try:
        target = Path(raw).expanduser()
        if not target.is_absolute():
            target = _roots()[0] / target
        target = target.resolve()
    except Exception as e:                                  # noqa: BLE001
        return Utf8JSONResponse({"error": "路径解析失败：%s" % str(e)[:120]}, status_code=400)

    inside = False
    for root in _roots():
        try:
            target.relative_to(root)
            inside = True
            break
        except ValueError:
            continue
    if not inside:
        return Utf8JSONResponse(
            {"error": "路径不在允许的目录内（只允许数据目录下的文件）",
             "how_to_fix": "传数据目录内的相对路径，例如 workshops/<间>/结果.md"},
            status_code=403)

    if not target.exists():
        return Utf8JSONResponse({"error": "文件不存在：%s" % str(target)}, status_code=404)
    if target.is_dir():
        return Utf8JSONResponse({"error": "这是目录不是文件；要列目录请用 /api/workshops 一类的端点"},
                                status_code=400)
    size = target.stat().st_size
    if size > MAX_BYTES:
        return Utf8JSONResponse(
            {"error": "文件过大（%.1f MB > 上限 %.0f MB）" % (size / 1048576, MAX_BYTES / 1048576),
             "how_to_fix": "直接在服务器文件系统里取，或让产出方切成小块"},
            status_code=413)

    return FileResponse(str(target), filename=target.name)
