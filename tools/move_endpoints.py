# -*- coding: utf-8 -*-
"""tools/move_endpoints.py —— 把 `server.py` 里的**一组 `@app.*` 端点整块搬进新 router**。

## 为什么有它

`server.py` 曾经 6,500+ 行、51 个 `@app.*`。拆它是审计点名的长期债，但**手工搬家很容易搬错**：
漏惰性 import（`NameError`）、相对导入少一层、装饰器忘改、注册顺序变了。
本工具把"搬家"变成**可重复、可复核**的动作，并自带三条保险：

1. **函数体原样搬**（不改一行逻辑）；
2. **惰性 import 自动补齐**（对每个 handler 算出它用到、且确实存在于 `server` 命名空间的名字）；
3. **拒绝搬有 `global` 语句的 handler**（那种搬了会改变语义，必须人工来）。

## 用法

```bash
# 先干跑看清单
python tools/move_endpoints.py --module wakeup_heartbeat.py --title "唤醒与心跳一族" \
    --paths /api/wakeup/trigger,/api/wakeup/mutual,/api/wakeup/status,/api/wakeup/response,/api/heartbeat,/api/heartbeat/stale \
    --dry-run

# 确认后真搬（自动备份 server.py）
python tools/move_endpoints.py --module wakeup_heartbeat.py --title "唤醒与心跳一族" --paths ...
```

搬完请务必：`python -m compileall` → `python -m pytest agent_community/tests/test_no_undefined_calls.py` →
全量回归 → **重启服务真打**（端点搬家不改契约，判据是"同样的请求同样的响应"）。
"""
from __future__ import annotations

import argparse
import ast
import shutil
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent.parent
SERVER = ROOT / "agent_community" / "platform" / "server.py"
ROUTERS = ROOT / "agent_community" / "platform" / "routers"

HEADER = '''# -*- coding: utf-8 -*-
"""{title}（router 端点组）

## 端点（{count} 个）

```
{listing}
```

## 为什么在 `routers/` 里

2026-10-21 从 `server.py` 整块搬出（审计点名"巨无霸"结构债）。**函数体原样搬，行为不变**；
`server` 里的全局在**函数体内惰性 import**（模块级 import 会与 `server` 形成循环，见 `routers/docking.py`）；
注册走 `app.include_router`。

搬出工具：`python tools/move_endpoints.py`（可复用，别手工搬）。
"""
from __future__ import annotations

import json

from fastapi import APIRouter, Request

router = APIRouter()

'''


def _dec_path(node) -> str | None:
    for dec in getattr(node, "decorator_list", []):
        if isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute):
            if getattr(dec.func.value, "id", "") == "app" and dec.args:
                if isinstance(dec.args[0], ast.Constant):
                    return dec.args[0].value
    return None


def _dec_method(node) -> str:
    """`@app.post(...)` → `POST`（只用于打印，方便人核对）。"""
    for dec in getattr(node, "decorator_list", []):
        if isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute):
            if getattr(dec.func.value, "id", "") == "app":
                return dec.func.attr.upper()
    return "?"


#: 这些名字不该从 `server` 里 import —— 它们属于标准库/框架，模块头直接 import 更干净
#: （⚠️ 第一版没排除，于是 `from ..server import app, Request, time, uuid4` 全冒出来了）
STDLIB = {
    "Request": "from fastapi import Request",
    "json": "import json",
    "time": "import time",
    "asyncio": "import asyncio",
    "os": "import os",
    "re": "import re",
    "uuid4": "from uuid import uuid4",
    "datetime": "from datetime import datetime",
    "logging": "import logging",
}


def _needed(node, server_mod, extra: set) -> list:
    params = {a.arg for a in node.args.args} | {a.arg for a in node.args.kwonlyargs}
    params |= {a.arg for a in getattr(node.args, "posonlyargs", [])}
    assigned, used = set(), set()
    # ⚠️ 只走**函数体**，别走装饰器 —— 装饰器里的 `app.get` 会让 `app` 被算成"用到的全局"
    for stmt in node.body:
        for n in ast.walk(stmt):
            if isinstance(n, ast.Name):
                (assigned if isinstance(n.ctx, ast.Store) else used).add(n.id)
            elif isinstance(n, ast.ImportFrom):
                assigned |= {al.asname or al.name for al in n.names}
            elif isinstance(n, ast.Import):
                assigned |= {(al.asname or al.name).split(".")[0] for al in n.names}
            elif isinstance(n, ast.comprehension):
                assigned |= {t.id for t in ast.walk(n.target) if isinstance(t, ast.Name)}
            elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n is not node:
                assigned.add(n.name)
                assigned |= {a.arg for a in n.args.args}
    import builtins
    names = used - assigned - params - set(dir(builtins))
    return sorted(x for x in names if hasattr(server_mod, x) or x in extra)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="把一组 @app.* 端点搬进新 router")
    ap.add_argument("--module", required=True, help="目标文件名（放在 platform/routers/ 下，如 wakeup_heartbeat.py）")
    ap.add_argument("--title", required=True, help="这组的中文名（写进模块 docstring）")
    ap.add_argument("--paths", required=True, help="逗号分隔的路径列表（顺序即注册顺序）")
    ap.add_argument("--extra-globals", default="", help="额外当作 server 全局的名字（逗号分隔，一般不用）")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    paths = [p.strip() for p in args.paths.split(",") if p.strip()]
    extra = {x.strip() for x in args.extra_globals.split(",") if x.strip()}
    target = ROUTERS / args.module
    if target.exists():
        print("[FAIL] 目标已存在：%s（先确认是不是搬过了）" % target.relative_to(ROOT))
        return 1

    sys.path.insert(0, str(ROOT))
    import agent_community.platform.server as server_mod  # noqa: E402

    src = SERVER.read_text(encoding="utf-8")
    lines = src.splitlines()
    tree = ast.parse(src)

    picked: dict = {}
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        p = _dec_path(node)
        if p in paths:
            # ⚠️ 同一个路径可能有多个方法（`POST /api/heartbeat` 与 `GET /api/heartbeat`）——
            #    第一版按路径做 dict key，直接把 POST 那条**覆盖丢了**（干跑时数出 6 而非 7）。
            picked.setdefault(p, []).append(node)
    missing = [p for p in paths if p not in picked]
    if missing:
        print("[FAIL] 这些路径没找到 handler：%s" % missing)
        return 1

    chunks, report = [], []
    stdlib_needed: set = set()
    for p in paths:                                     # 保持调用方给的顺序
        for node in sorted(picked[p], key=lambda n: n.lineno):
            if any(isinstance(n, (ast.Global, ast.Nonlocal)) for n in ast.walk(node)):
                print("[FAIL] %s 里有 global/nonlocal 语句 —— 搬家会改变语义，请人工处理" % p)
                return 1
            start = node.lineno - len(node.decorator_list)
            body = lines[start - 1:node.end_lineno]
            for i, l in enumerate(body):
                if l.lstrip().startswith("@app."):
                    body[i] = l.replace("@app.", "@router.", 1)
            need = _needed(node, server_mod, extra)
            # 标准库/框架的名字走模块头 import，其余（server 的全局）走函数内惰性 import
            for name in list(need):
                if name in STDLIB:
                    stdlib_needed.add(STDLIB[name])
                    need.remove(name)
            if need:
                first = node.body[0]
                is_doc = (isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)
                          and isinstance(first.value.value, str))
                rel = (first.end_lineno - start + 1) if is_doc else 2
                body.insert(rel, "    from ..server import " + ", ".join(need))
            chunks.append("\n".join(body))
            report.append((p, start, node.end_lineno, need))
            print("  %-36s 行 %d-%d  惰性 import: %s"
                  % ("%s %s" % (_dec_method(node), p), start, node.end_lineno,
                     ", ".join(need) or "（无）"))

    total = len(chunks)
    listing = "\n".join(
        "%s %s" % (_dec_method(n), p) for p in paths
        for n in sorted(picked[p], key=lambda x: x.lineno))
    header = HEADER.format(title=args.title, count=total, listing=listing)
    for imp in sorted(stdlib_needed):
        header = header.replace("import json\n", "import json\n" + imp + "\n", 1)

    if args.dry_run:
        print("\n[dry-run] 会写出 %s（%d 个端点），并从 server.py 删除对应块。未落盘。" % (target.name, total))
        return 0

    bak = ROOT / "temp" / ("server.py.bak_%s_%s" % (args.module.replace('.py', ''), time.strftime("%Y%m%d_%H%M%S")))
    shutil.copy2(SERVER, bak)
    target.write_text(header + "\n\n".join(chunks) + "\n", encoding="utf-8")

    for _p, start, end, _n in sorted(report, key=lambda r: r[1], reverse=True):
        del lines[start - 1:end]
    text = "\n".join(lines) + "\n"
    anchor = "app.include_router(_rooms_discuss_router)"
    reg = ("from .routers.%s import router as _%s_router\napp.include_router(_%s_router)"
           "  # %s（2026-10-21 从 server.py 整块搬出）"
           % (args.module.replace(".py", ""), args.module.replace(".py", ""),
              args.module.replace(".py", ""), args.title))
    if anchor in text:
        text = text.replace(anchor, anchor + "\n" + reg, 1)
    else:
        print("[WARN] 没找到注册锚点，请手工在 server.py 里加 include_router")
    SERVER.write_text(text, encoding="utf-8")

    print("\n[OK] 写出 %s（%d 个端点）；server.py %d → %d 行；备份 %s"
          % (target.relative_to(ROOT), total, len(src.splitlines()),
             len(text.splitlines()), bak.name))
    print("  下一步：compileall → test_no_undefined_calls → 全量回归 → 重启真打")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
