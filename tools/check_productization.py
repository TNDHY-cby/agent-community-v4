# -*- coding: utf-8 -*-
"""tools/check_productization：**反私人定制扫描器**（把"这产品不是只给我一个人用的"变成可执行判据）。

老板 2026-10-11 / 10-15 反复强调（原话）：
    「**以后再这样搞私人定制我们就死了**」
    「**完善开发框架，以后的开发也要注意产品不是私人订制的**（我说不清楚）」

"说不清楚"的部分由本文件代他说清楚：**私人定制 = 产品预设了只有"我这台机器/我这个人/我这个场景"
才成立的东西**。它和"可移植性"（端口/路径/凭据）是一族，但更宽 —— 本扫描器补的是
`check_portability.py` **扫不到**的那几类（那一个只管硬编码坐标）。

## 六种形态 → 各自的检查器

| # | 形态（人话） | 谁在管 |
|---|---|---|
| 1 | 写死我这台机器的坐标（端口/IP/路径/用户名/凭据） | `tools/check_portability.py`（**fail**） |
| 2 | 代码里出现真实 harness 名 / 私有产品名（用户会照抄） | `agent_community/tests/test_bridge_catalog.py::test_onboarding_assets_carry_no_private_names`（**fail**） |
| 3 | **平台专属能力伪装成通用能力**（只有 Windows 能跑的实现，Linux 上静默失效） | **本文件规则 A** |
| 4 | **开箱默认里塞假数据**（看起来配好了、其实必然失败；或未渲染的模板占位） | **本文件规则 B / C** |
| 5 | **只在我这台机器绿**（编码/换行/大小写/平台差异导致别人那儿红） | CI 双平台 + `tests/test_bridge_encoding_guard.py` 等（**fail**） |
| 6 | **用户第一次跑就撞到**（入口命令不可用、失败不说话、文档承诺了却没实现） | **`tools/first_run_check.py`** + `tests/test_cli_contract.py`（**fail**） |

## 判据（规则 A/B/C）与"显式申请例外"

命中即 **fail**；确有理由的例外必须在该行（或上一行）写：

    # productization: allow(<一句话理由>)

例外要**写得出理由**，否则就该修 —— 这条是给"确实只有某个平台有这个概念"的情况留的口子
（例：窗口激活、系统回收站）。

用法：
    python tools/check_productization.py            # 全量（CI / 发布前 / pytest 也会调）
    python tools/check_productization.py --list     # 只列不判（排障用）
"""
from __future__ import annotations

import ast
import re
import subprocess
import sys
from pathlib import Path

# ── 中文 print 在 cp1252 控制台会炸（GitHub windows runner 实测）⇒ 先自保 ──────
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "agent_community"

# ── 规则 A：平台专属命令 ────────────────────────────────────────────────────
# 这些命令**只在 Windows 存在**（或语义完全不同）。用它们必须满足其一：
#   ① 走 `platform/sysprobe.py`（跨平台取事实的唯一入口）；
#   ② 就在 sysprobe 里（它是回落实现的所在地）；
#   ③ 有**明确的平台分支**（本行或上几行出现 os.name / sys.platform 判断）；
#   ④ 显式申请例外（`# productization: allow(...)`）。
PLATFORM_ONLY_CMDS = ("powershell", "pwsh", "netstat", "tasklist", "taskkill",
                      "Get-CimInstance", "Get-NetTCPConnection", "Get-Process",
                      "Stop-Process", "Set-Clipboard", "Microsoft.VisualBasic")

#: 允许出现平台专属命令的文件（**每个都要有理由**）
ALLOWED_FILES = {
    "sysprobe.py": "跨平台事实的唯一入口：这里是回落实现（psutil 优先，PowerShell 仅在 Windows 兜底）",
    "shell_tool.py": "命令执行工具：argv 按平台拼（POSIX 走 /bin/sh -c），见 _shell_argv()",
}

#: 允许出现平台专属命令的**用户可见能力**（Windows 独有概念，已在文档里如实标注"非 Windows 不支持"）
ALLOWED_CAPABILITIES = ("_activate_windows_by_pids", "_trash_bridge_dir", "_set_clipboard")

# ── 规则 B：开箱默认里的"假数据" ─────────────────────────────────────────────
# 判据：默认配置/静态表里不许出现"看起来能用、其实必然失败"的东西。
FAKE_DEFAULT_PATTERNS = (
    (re.compile(r"\{\{[^}]{2,}\}\}"), "未渲染的模板占位符（用户会看到 {{…}}，像项目没做完）"),
    (re.compile(r"example-web-bridge|example-harness-\d"), "示例 harness 被当成真实默认项"),
)

# ── 规则 C：被当成"已配置"的占位值 ───────────────────────────────────────────
# `api.example.com` / `example-model-*` 是保留示例域名与占位模型名：
# 它们**可以**作为兜底默认，但不许被**当成"用户已配好 AI"**（v4.8.0 实测事故）。
PLACEHOLDER_VALUES = ("api.example.com", "example-model-flash")

SKIP_SCAN_DIRS = (".git", "__pycache__", "node_modules", ".pytest_cache", "bridges", "data")


def _tracked_py() -> list:
    """扫描面 = **被 git 跟踪**的 .py（发布面即真相；dev-only 杂物不算）。"""
    try:
        out = subprocess.run(["git", "-C", str(ROOT), "ls-files", "*.py"],
                             capture_output=True, text=True, encoding="utf-8",
                             errors="replace", timeout=60)
        files = [ROOT / line.strip() for line in (out.stdout or "").splitlines() if line.strip()]
        if files:
            return files
    except Exception:  # noqa: BLE001
        pass
    return [p for p in PKG.rglob("*.py")
            if not any(part in SKIP_SCAN_DIRS for part in p.parts)]


def _has_platform_branch(lines: list, idx: int, back: int = 6) -> bool:
    window = "\n".join(lines[max(0, idx - back):idx + 1])
    return any(t in window for t in ("os.name", "sys.platform", "_IS_WINDOWS", "platform.system"))


def _allowed(line: str) -> bool:
    return "productization: allow(" in line


def _platform_only_in_call(node, cmds) -> list:
    """这个 `subprocess.*` 调用的参数里，有没有平台专属命令？返回命中的命令名。

    ★**只看真正的调用点**（AST），不扫注释/文档串 —— 第一版用正则扫全文，
    结果把"注释里提到 powershell"和"合法测试"全报了出来（52 条噪音）：
    那种扫描器第一天就会被绕过（人人加例外）或者被忽略（alert fatigue）。
    """
    hits = []
    for arg in list(getattr(node, "args", [])) + [k.value for k in getattr(node, "keywords", [])]:
        pieces = []
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            pieces.append(arg.value)
        elif isinstance(arg, (ast.List, ast.Tuple, ast.Set)):
            for e in arg.elts:
                if isinstance(e, ast.Constant) and isinstance(e.value, str):
                    pieces.append(e.value)
        for p in pieces:
            low = p.lower()
            for c in cmds:
                if c.lower() in low and c not in hits:
                    hits.append(c)
    return hits


def _is_subprocess_call(node) -> bool:
    f = node.func
    name = ""
    if isinstance(f, ast.Attribute):
        name = f.attr
        base = f.value
        if isinstance(base, ast.Name) and base.id not in ("subprocess", "_sp", "sp", "_subprocess"):
            return False
    elif isinstance(f, ast.Name):
        name = f.id
    return name in ("run", "Popen", "call", "check_call", "check_output")


def _docstring_positions(tree) -> set:
    """AST 里所有 docstring 节点的行号（**说明文字不算"用户会看到的假数据"**）。"""
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", None) or []
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
                    and isinstance(body[0].value.value, str):
                out.add(getattr(body[0].value, "lineno", 0))
    return out


def _enclosing_function_has_platform_branch(tree, node, text: str) -> bool:
    """这个调用所在的**函数**里有没有平台分支（`os.name` / `sys.platform` / `_IS_WINDOWS`）。

    ★为什么要看**整个函数**而不是几行：本项目里"非 Windows 就如实返回不支持"的写法是
      **函数开头一句 early-return**，后面才是平台专属实现 —— 只看附近 6 行会把它误判成没保护。
    """
    parents = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[child] = parent
    cur = node
    while cur is not None:
        cur = parents.get(cur)
        if isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef)):
            seg = ast.get_source_segment(text, cur) or ""
            return any(t in seg for t in ("os.name", "sys.platform", "_IS_WINDOWS",
                                          "platform.system"))
    return False


def _fake_string_literals(tree) -> list:
    """规则 B（AST 版）：字符串字面量里出现**未渲染的模板占位** `{{名字}}`。

    只认"像占位符"的形状（字母/下划线/中文），避免把 `f"{{字面大括号}}"` 这类转义误报。
    """
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            for m in re.finditer(r"\{\{([A-Za-z_\u4e00-\u9fa5][\w\u4e00-\u9fa5]*)\}\}", node.value):
                out.append((getattr(node, "lineno", 0), m.group(0)))
    return out


def scan() -> tuple:
    """返回 `(fails, warnings, report)`；每条是 `文件:行: 说明`。"""
    import ast

    fails: list = []
    warns: list = []
    report: list = []

    for py in _tracked_py():
        if not py.is_file():
            continue
        rel = str(py.relative_to(ROOT)).replace("\\", "/")
        if any(part in SKIP_SCAN_DIRS for part in py.parts):
            continue
        if py.name == "check_productization.py":
            continue
        text = py.read_text(encoding="utf-8", errors="replace")
        lines = text.split("\n")
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue

        # ── 规则 A（AST）：平台专属命令只能出现在 sysprobe / 有平台分支 / 显式申请例外之处 ──
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not _is_subprocess_call(node):
                continue
            hits = _platform_only_in_call(node, PLATFORM_ONLY_CMDS)
            if not hits:
                continue
            ln = getattr(node, "lineno", 0)
            if (py.name in ALLOWED_FILES
                    or _allowed(lines[ln - 1] if ln and ln <= len(lines) else "")
                    or _allowed(lines[ln - 2] if ln >= 2 else "")
                    or _has_platform_branch(lines, ln - 1)
                    or _enclosing_function_has_platform_branch(tree, node, text)):
                continue
            fails.append("%s:%d  [平台专属命令 %s] %s —— 改走 platform/sysprobe 或加平台分支；"
                         "确实只有该平台有这个概念就写 `# productization: allow(理由)`"
                         % (rel, ln, ",".join(hits), (lines[ln - 1].strip() if ln else "")[:80]))

        # ── 规则 B（AST）：未渲染的模板占位 ──
        _docs = _docstring_positions(tree)
        for ln, token in _fake_string_literals(tree):
            if ln in _docs:
                continue                    # 文档串里讲模板语法，不算"开箱假数据"
            if "/tests/" in rel and "assert" in (lines[ln - 1] if ln <= len(lines) else ""):
                continue
            if _allowed(lines[ln - 1] if ln else ""):
                continue
            fails.append("%s:%d  [开箱假数据] 未渲染的模板占位 %s —— 用户会看到 {{…}}，像项目没做完"
                         % (rel, ln, token))

    # ── 报告：平台专属 skip（不判死，但让人看见"这块在别的平台没守"）──────────
    for py in _tracked_py():
        p = str(py).replace("\\", "/")
        if not py.is_file() or "/tests" not in p:
            continue
        try:
            for i, line in enumerate(py.read_text(encoding="utf-8", errors="replace").split("\n"), 1):
                s = line.strip()
                if s.startswith("#") or "skipif" not in line:
                    continue
                if any(t in line for t in ("win32", "linux", "darwin")):
                    report.append("%s:%d  %s" % (str(py.relative_to(ROOT)).replace("\\", "/"),
                                                 i, s[:100]))
        except Exception:  # noqa: BLE001
            continue

    return fails, warns, report


def main() -> int:
    only_list = "--list" in sys.argv
    fails, warns, report = scan()
    print("反私人定制扫描（范围＝被 git 跟踪的 .py）")
    print("-" * 66)
    for f in fails:
        print("  FAIL " + f)
    for w in warns:
        print("  warn " + w)
    if report:
        print("\n平台专属 skip（**不判死**，但每一条都意味着「这块在别的平台没守」）：")
        for r in report:
            print("  · " + r)
    print("-" * 66)
    print("fail=%d warn=%d 平台专属 skip=%d" % (len(fails), len(warns), len(report)))
    if only_list:
        return 0
    if fails:
        print("\n⇒ 不合格：上面这些点就是「私人定制」的入口 —— 换台机器/换个人就会露馅。")
        print("  判据与六种形态见 PRODUCTIZATION.md；例外请写 `# productization: allow(理由)`。")
        return 1
    print("OK：没有发现「只对我这台机器成立」的实现。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
