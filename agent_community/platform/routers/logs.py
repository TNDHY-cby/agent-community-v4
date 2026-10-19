"""platform/routers/logs：**只读日志尾端点**（V59，2026-10-19）。

设计稿：`design-docs/V59_只读日志尾端点设计.md`

     GET /api/logs/tail?name=server&lines=50&since=0

为什么要有它：界面（启动过场的片尾字幕 / 将来的"最近发生了什么"）需要显示**真实发生的事**，
但**不能**给浏览器一个"读任意文件"的口子，也不该把日志路径暴露出去。

四条硬边界（做不到就不做）：
  1. **只白名单**：`name` 只认 `server/watchdog/a2a/grpc` 四个固定名字，其余一律 400（含任何路径字符）；
  2. **只读**：只 `open(..., "r")`；不建、不截断、不清理；
  3. **只尾部**：最多 200 行；大文件**从尾部回溯读**，不全量载入（并在响应里如实标 `partial`）；
  4. **去敏**：凭据样式片段替换成 `***`，并**如实报去敏了几处**（`redacted`）。
"""
from __future__ import annotations

import os
import re
from pathlib import Path

from fastapi import APIRouter, Request

from ..audit import audit_log as _audit_log

router = APIRouter()

#: 白名单：**名字 → 候选文件名/通配**（不是路径！任何路径字符都不接受）
_WHITELIST = {
    "server": ("server_stdout.log", "server.log"),
    "watchdog": ("watchdog.log",),
    "a2a": ("a2a_9104.log", "a2a_*.log"),
    "grpc": ("grpc_9105.log", "grpc_*.log"),
}
MAX_LINES = 200
DEFAULT_LINES = 50
#: 超过这个大小就只读尾部（不统计总行数，如实标 partial），避免把内存拖死
_TAIL_BYTES = 8 * 1024 * 1024

#: 去敏规则（保持键名可读，只把值替换成 ***）
_REDACT_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9_\-]{8,}"),
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._\-]{8,}"),
    re.compile(r"(?i)\b(token|password|passwd|secret|api[_\-]?key)\s*[=:]\s*[\"']?[^\s\"',}]{6,}"),
    re.compile(r"(?i)\bX-Agent-Token\s*[:=]\s*\S+"),
    re.compile(r"(?i)\bAC4_TOKEN\s*=\s*\S+"),
)


def _logs_dir() -> Path:
    """日志目录（**跟数据目录走**：`<AC_DATA_DIR>/logs`；不写死仓库路径）。"""
    base = Path(os.environ.get("AC_DATA_DIR") or (Path(__file__).resolve().parents[2] / "data"))
    return base / "logs"


def _resolve(name: str) -> tuple[Path | None, str]:
    """白名单名 → 实际文件。返回 `(路径, 错误说明)`；名字非法/找不到都给说明。"""
    key = str(name or "").strip().lower()
    if key not in _WHITELIST:
        return None, ("只支持这四个名字：%s（不接受路径，`name` 不是文件路径）"
                      % "、".join(sorted(_WHITELIST)))
    d = _logs_dir()
    for pat in _WHITELIST[key]:
        cands = sorted(d.glob(pat), key=lambda p: p.stat().st_mtime if p.is_file() else 0,
                       reverse=True)
        for p in cands:
            try:
                if p.is_file():
                    return p, ""
            except OSError:
                continue
    return None, ("日志文件还不存在：`%s`（目录 %s）—— 服务刚起或该组件还没写过日志，"
                  "这是如实结果，不是错误" % (key, d))


def _redact(text: str) -> tuple[str, int]:
    hits = 0

    def _sub(m: "re.Match") -> str:
        nonlocal hits
        hits += 1
        s = m.group(0)
        # 保留 "key=" 这类前缀，只把值换掉（便于人读"哪个凭据被遮了"）
        if "=" in s or ":" in s:
            sep = "=" if "=" in s else ":"
            head = s.split(sep, 1)[0]
            return head + sep + "***"
        return "***"

    out = text
    for pat in _REDACT_PATTERNS:
        out = pat.sub(_sub, out)
    return out, hits


def _read_tail(path: Path, n: int, since: int) -> dict:
    """读尾部 `n` 行（或 `since` 之后的新行）。大文件只读尾部，如实标 `partial`。

    返回 `{"lines", "total_lines", "next_since", "truncated", "partial"}`；
    `lines` 里每项是 `{"n": 行号(从1开始), "text": 文本}` —— 行号用于前端"追尾去重"。
    """
    size = path.stat().st_size
    partial = size > _TAIL_BYTES
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        if partial:
            fh.seek(size - _TAIL_BYTES)
            fh.readline()                      # 丢掉可能被截断的首行
            chunk = fh.read()
            text_lines = chunk.splitlines()
            # 大文件：拿不到绝对行号 ⇒ 用尾部相对编号，并如实标 partial
            base_no = None
            total = None
        else:
            text_lines = fh.read().splitlines()
            base_no = 1
            total = len(text_lines)

    numbered = []
    for i, t in enumerate(text_lines):
        no = (base_no + i) if base_no is not None else (i + 1)
        numbered.append({"n": no, "text": t})

    if since:
        picked = [x for x in numbered if x["n"] > since][:n]
        truncated = False
    else:
        picked = numbered[-n:]
        truncated = len(numbered) > n

    next_since = picked[-1]["n"] if picked else (since or (numbered[-1]["n"] if numbered else 0))
    return {"lines": picked, "total_lines": total, "next_since": next_since,
            "truncated": truncated, "partial": partial}


@router.get("/api/logs/tail")
async def logs_tail(request: Request, name: str = "server", lines: int = DEFAULT_LINES,
                    since: int = 0):
    """**只读**取日志尾部（白名单 + 上限 200 行 + 去敏 + 只本机）。"""
    from ..server import Utf8JSONResponse            # 与其它路由一致的中文安全响应
    from .system import origin_allowed               # 复用一处真相：本机来源判定

    ok, why = origin_allowed(request)
    if not ok:
        return Utf8JSONResponse({"success": False, "error": f"拒绝读取日志：{why}"},
                                status_code=403)

    try:
        want = int(lines)
    except (TypeError, ValueError):
        want = DEFAULT_LINES
    want = max(1, min(want, MAX_LINES))
    capped = False

    path, err = _resolve(name)
    if path is None:
        # 名字非法 = 400（**不返回任何文件内容**）；文件不存在 = 404（如实说"还没写过日志"）
        status = 400 if "只支持这四个名字" in err else 404
        return Utf8JSONResponse({"success": False, "error": err,
                                 "allowed": sorted(_WHITELIST)}, status_code=status)

    try:
        raw_info = _read_tail(path, want, int(since or 0))
    except OSError as e:
        return Utf8JSONResponse({
            "success": False,
            "error": "读日志失败（只读打开失败）：%s: %s" % (type(e).__name__, e),
            "file": str(path),
        }, status_code=500)

    redacted_total = 0
    out_lines = []
    for item in raw_info["lines"]:
        text, hits = _redact(item["text"])
        redacted_total += hits
        out_lines.append({"n": item["n"], "text": text})

    try:
        _audit_log.record("system.logs_tail", actor="local-ui", target=str(name),
                          detail="lines=%d since=%d 取回=%d 去敏=%d partial=%s"
                                 % (want, int(since or 0), len(out_lines), redacted_total,
                                    raw_info["partial"]))
    except Exception as e:                                     # noqa: BLE001
        print("[logs] 审计写入失败（不影响读取）: %s: %s" % (type(e).__name__, e), flush=True)

    return {
        "success": True,
        "name": str(name),
        "file": str(path),
        "lines": out_lines,
        "total_lines": raw_info["total_lines"],
        "next_since": raw_info["next_since"],
        "truncated": bool(raw_info["truncated"] or capped),
        "partial": bool(raw_info["partial"]),
        "redacted": redacted_total,
        "max_lines": MAX_LINES,
        "note": ("只读、只白名单、只尾部；`file` 只给本机调用方看，界面不要显示它。"
                 + ("（文件较大，只读了尾部，未统计总行数）" if raw_info["partial"] else "")),
    }
