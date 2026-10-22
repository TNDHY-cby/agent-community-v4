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


#: ★D5：只放行**产物子树**。数据目录下的 `tasks.json` / `config` / `policy.json` / `audit.json`
#: 这些是**平台状态**，不属于"产物"，任何外部可达的读口都不该能取它们。
ARTIFACT_SUBDIRS = ("workshops", "output", "temp", "artifacts", "bridges")


def _is_under(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _roots() -> list:
    """允许的根目录：数据目录下的产物子树（**唯一来源**：数据目录 + 上表）。"""
    from ..env_spec import data_dir

    root = Path(data_dir()).resolve()
    return [root / sub for sub in ARTIFACT_SUBDIRS]


@router.get("/api/artifact")
async def get_artifact(request: Request, path: str = ""):
    """按路径取一个产物文件（只读）。"""
    from ..server import Utf8JSONResponse

    raw = (path or "").strip()
    if not raw:
        return Utf8JSONResponse({"error": "path 必填（可以是数据目录内的相对路径或绝对路径）"},
                                status_code=400)

    try:
        given = Path(raw).expanduser()
        # 相对路径：在**每个产物子树**里都试着找一次（`workshops/x.md` 与 `output/x.md` 都合法）
        if given.is_absolute():
            candidates = [given.resolve()]
        else:
            # 先按"数据目录相对"找（`workshops/x.md` ⇒ `data/workshops/x.md`），
            # 再按各产物子树找（允许写 `output/x.md` 这种"子树内相对"）
            from ..env_spec import data_dir as _dd

            candidates = [(Path(_dd()).resolve() / given).resolve()]
            candidates += [(root / given).resolve() for root in _roots()]
    except Exception as e:                                  # noqa: BLE001
        return Utf8JSONResponse({"error": "路径解析失败：%s" % str(e)[:120]}, status_code=400)

    # ★D5：**存在但不在产物子树**的文件（tasks.json / config/ / policy.json…）⇒ 403，
    #   而不是 404 —— 404 会让人以为"平台没这个文件"，其实是我们**拒绝**给。
    for cand in candidates:
        if cand.exists() and not any(_is_under(cand, root) for root in _roots()):
            return Utf8JSONResponse(
                {"error": "这个文件不属于产物（它在平台状态/配置区，不在产物子树里）",
                 "how_to_fix": "产物只放 workshops/ output/ temp/ artifacts/ bridges/ 下；"
                               "平台状态请查对应 API（如 /api/tasks、/api/config-status）"},
                status_code=403)

    inside, target = False, None
    for cand in candidates:
        for root in _roots():
            try:
                cand.relative_to(root)
            except ValueError:
                continue
            inside = True
            if target is None or cand.exists():
                target = cand
            break
        if target is not None and target.exists():
            break
    if not inside or target is None:
        return Utf8JSONResponse(
            {"error": "路径不在允许的目录内（只允许产物子树：%s）" % "、".join(ARTIFACT_SUBDIRS),
             "how_to_fix": "传产物子树内的相对路径，例如 workshops/<间>/结果.md 或 output/x.txt"},
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
