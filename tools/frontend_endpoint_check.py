# -*- coding: utf-8 -*-
"""判据：**前端调用的每个后端端点都真的存在**（路由自省，不查源码字面量）。

## 为什么要它

2026-10-24 前端把「框选输入框」接进界面（`D前端-23`），用的全是 `/api/docking/harness/{hid}/…` 一族。
这类接线最典型的死法不是写错逻辑，而是**端点搬了家/名字改了**，界面按钮点了没反应，
用户以为"功能没做"，其实是 404。

本工具把前端 JS 里出现的 `/api/...` 路径抽出来，和**活的后端路由表**比：
- 后端路由用 `app.routes` 展开（`include_router` 会变成 `_IncludedRouter`，必须展开 `original_router.routes`，
  否则会漏掉一大半端点 —— 2026-10-05 实测踩过）；
- 前端路径里的 `{hid}` / `${ws}` / `{mid}` 与后端的 `{harness_id}` / `{ws}` 不同名，
  所以**按段结构匹配**（参数段一律算 `{}`），不比较参数名；
- 前端常写"前缀 + 拼接"（`'/api/harness/' + id`），这种前缀只要**有真实路由以它开头**就算对。

## 退出码

0 = 全部对得上；1 = 有前端路径后端没有（会列出）；2 = 环境不满足（import 不了 app）。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

FRONTEND = ROOT / "agent_community" / "frontend"

#: 一个"路径段"是不是参数占位符：`{hid}` / `${hid}` / `{x}`
_SEG_PLACEHOLDER = re.compile(r"^(?:\$\{[^}]*\}|\{[^}]*\})$")
#: 字符串里不该出现的字符（出现就说明这不是"一个端点"，而是说明文字）
_JUNK = re.compile(r"[\s=<>\"'`]")


def backend_routes() -> set:
    """活的后端路由表：展开 `_IncludedRouter`，返回 `(method, normalized_path)`。"""
    from agent_community.platform.server import app

    out = set()

    def walk(routes):
        for r in routes or []:
            orig = getattr(r, "original_router", None)
            if orig is not None:                       # include_router 的惰性对象
                walk(getattr(orig, "routes", []) or [])
                continue
            path = getattr(r, "path", None)
            if not path:
                continue
            for m in (getattr(r, "methods", None) or {"GET"}):
                if m in ("HEAD", "OPTIONS"):
                    continue
                out.add((m.upper(), _norm(path)))

    walk(app.routes)
    return out


def _norm(path: str) -> str:
    """规范化：参数段统一成 `{}`、空段丢掉（`/api/x/` 与 `/api/x` 等价）。"""
    segs = [s for s in str(path).split("/") if s]
    return "/" + "/".join("{}" if _SEG_PLACEHOLDER.match(s) else s for s in segs)


def _junk(raw: str) -> bool:
    """明显不是"一个端点"的字符串（带空格/等号/引号 —— 例如被当成说明文字的那句）。"""
    return bool(_JUNK.search(raw or ""))


def frontend_paths() -> list:
    """前端 JS 里出现的 `/api/…` 字面量（含模板串拼接），去重并保留出处。"""
    found = {}
    if not FRONTEND.is_dir():
        return []
    for js in sorted(FRONTEND.rglob("*.js")):
        text = js.read_text(encoding="utf-8", errors="replace")
        for m in re.finditer(r"""['"`](/api/[^'"`\n]+)['"`]""", text):
            raw = m.group(1).split("?")[0]
            if "*" in raw or _junk(raw):
                continue
            found.setdefault(_norm(raw), set()).add(js.name)
    return sorted((p, sorted(v)) for p, v in found.items())


def _segs(path: str) -> list:
    return [s for s in str(path).split("/") if s]


def _seg_match(fe: str, be: str) -> bool:
    """前端这一段能不能对上后端这一段：后端是参数段（`{}`）时吸收任意字面量。"""
    return fe == be or be == "{}"


def _matches(fe_path: str, be_path: str, *, prefix: bool = False) -> bool:
    """结构匹配；`prefix=True` 时允许前端只是"前缀 + 拼接"（如 `/api/attest/harness/` + id）。"""
    fs, bs = _segs(fe_path), _segs(be_path)
    if prefix:
        if len(fs) > len(bs):
            return False
    elif len(fs) != len(bs):
        return False
    return all(_seg_match(f, b) for f, b in zip(fs, bs))


def main() -> int:
    try:
        routes = backend_routes()
    except Exception as e:                             # noqa: BLE001 — import 不起来就如实说
        print("[前端端点判据] 起不来后端 app（%s: %s）—— 环境不满足，不算通过" % (type(e).__name__, e))
        return 2
    paths = {p for _, p in routes}
    fe = frontend_paths()
    print("=" * 100)
    print("前端端点判据：前端调用的路径必须真的挂在后端（后端共 %d 个 (method,path)）" % len(routes))
    print("=" * 100)
    bad = []
    for path, files in fe:
        exact = any(_matches(path, p) for p in paths)
        prefix_ok = any(_matches(path, p, prefix=True) for p in paths)
        if not exact and not prefix_ok:
            bad.append((path, files))
        mark = "OK" if exact else ("OK(前缀)" if prefix_ok else "★FE-MISS")
        print("   %-56s %-8s %s" % (path[:56], mark, ", ".join(files)[:60]))
    print("-" * 100)
    if bad:
        print("结论：**有 %d 条前端路径后端没有** ⇒ 那些按钮点下去必然 404：" % len(bad))
        for p, f in bad:
            print("   · %s   （出现在 %s）" % (p, ", ".join(f)))
        return 1
    print("结论：**前端引用的 %d 个端点后端全都有**（含参数段与前缀拼接写法）" % len(fe))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
