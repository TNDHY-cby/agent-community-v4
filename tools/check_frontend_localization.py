# -*- coding: utf-8 -*-
"""把「前端里的本机坐标」按**产品 / 验收脚本**分列，并区分「真本机路径」与「通用占位」。

判据（老板 2026-10-17 转述的：「项目必须在拉取陌生人电脑中也能跑，不能私人定制」）：
  · **通用占位**（`C:\\path\\to\\your\\…`、`<你的路径>`、测试夹具 `D:\\ws_root`）= 允许；
  · **真本机路径**（含盘符 + 具体机器痕迹：`DSH工作区` / `Users\\<名>` / `Desktop` / `Programs` / `AppData` / 中文目录名）= **产品里 FAIL**；
  · **写死端口**（`127.0.0.1:<数字>` / `localhost:<数字>`）：产品里出现就是 WARN（默认值可改则放行），验收脚本里 WARN。
"""
import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
FE = Path(__file__).resolve().parent.parent / "agent_community" / "frontend"

#: ★验收脚本要不要一起算"不合格"：现在**先只报不拦**（它们是开发工具，不跑在产品路径上）；
#: MiMo 按 fe29 模式收严、`verify` 侧归零后，把这里改成 True —— **守卫会自动要求你改**
#: （`tests/test_frontend_localization.py` 里有一条"归零了却没打开严格档就红"）。
STRICT_VERIFY = False

#: 盘符路径：**前面不能紧跟字母**（否则 `http://`、`https://` 里的 `p:`/`s:` 会被误当盘符）；
#: ⚠️ JS 源码里反斜杠是转义的（`C:\\\\Users\\\\`），所以分隔符要允许**连续多个** `\`
DRIVE = re.compile(r"(?<![A-Za-z])[A-Za-z]:[\\/]+[^\s'\"）)<>]*")
LOOP = re.compile(r"(?:127\.0\.0\.1|localhost):(\d+)")

#: 出现这些片段 ⇒ 认为它是"某台具体机器的路径"（不是通用占位）
REAL_MARKERS = ("DSH工作区", "Users\\", "Users/", "Desktop", "Programs", "AppData",
                "合作社", "\\Temp\\", "OneDrive", "外端Agent")

#: 占位词：路径里带这些字样＝**通用占位**（允许，不算本机路径）
PLACEHOLDER_HINTS = ("你的", "你的目录", "示例", "虚拟", "<", "your", "xxx", "占位", "示例Harness")


def is_real_path(s: str) -> bool:
    if any(h in s for h in PLACEHOLDER_HINTS):
        return False
    if any(m in s for m in REAL_MARKERS):
        return True
    # 盘符后面出现中文字符 ⇒ 基本可以断定是具体目录名（占位符不会用中文）
    tail = s[2:]
    return any("\u4e00" <= ch <= "\u9fff" for ch in tail)


def scan() -> list:
    rows = []
    for p in sorted(FE.rglob("*")):
        if not p.is_file() or p.suffix.lower() not in (".js", ".html", ".css"):
            continue
        rel = p.relative_to(FE).as_posix()
        kind = "verify" if rel.startswith("verify/") else "product"
        for no, line in enumerate(p.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            for m in DRIVE.finditer(line):
                hit = m.group(0)[:70]
                rows.append({"file": rel, "kind": kind, "line": no, "type": "path",
                             "real": is_real_path(hit), "text": hit})
            for m in LOOP.finditer(line):
                rows.append({"file": rel, "kind": kind, "line": no, "type": "port",
                             "real": True, "text": m.group(0)})
    return rows


def main() -> int:
    rows = scan()
    prod_real = [r for r in rows if r["kind"] == "product" and r["type"] == "path" and r["real"]]
    prod_port = [r for r in rows if r["kind"] == "product" and r["type"] == "port"]
    ver_real = [r for r in rows if r["kind"] == "verify" and r["type"] == "path" and r["real"]]
    ver_any = [r for r in rows if r["kind"] == "verify"]

    print("=" * 78)
    print("前端本机坐标体检（产品 vs 验收脚本）")
    print("=" * 78)
    print("产品文件：真本机路径 %d 处 / 写死端口 %d 处" % (len(prod_real), len(prod_port)))
    for r in prod_real[:10]:
        print("   ★%-28s:%d  %s" % (r["file"], r["line"], r["text"]))
    for r in prod_port[:6]:
        print("   ~%-28s:%d  %s（默认值/可改则放行）" % (r["file"], r["line"], r["text"]))
    print("验收脚本：涉及 %d 个文件 / 其中真本机路径 %d 处" % (len({r['file'] for r in ver_any}), len(ver_real)))
    for r in ver_real[:8]:
        print("   %-30s:%d  %s" % (r["file"], r["line"], r["text"]))

    print("-" * 78)
    if prod_real:
        print("结论：**产品里有 %d 处本机路径 ⇒ 不合格**（陌生人电脑上会指向不存在的盘）" % len(prod_real))
        return 1
    if ver_real and STRICT_VERIFY:
        print("结论：**验收脚本里有 %d 处本机路径 ⇒ 不合格（严格档已开）**" % len(ver_real))
        return 1
    print("结论：产品侧合格（无本机路径）；验收脚本侧 %d 处待收严（不阻塞产品，但同一把尺%s）"
          % (len(ver_real), "，严格档已开" if STRICT_VERIFY else "，严格档未开"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
