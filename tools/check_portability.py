# -*- coding: utf-8 -*-
"""tools/check_portability：**可移植性扫描器**（把"可移植"变成可执行的 Definition of Done）。

老板 2026-10-14 的验收层要求：
    「没有 `listen(3000)`、`localhost:3000`、`C:\\Users\\...`、`/Users/...`」
    「换 `PORT=8080`、换数据库 URL、换目录后无需改代码」
    「可以加一个 pre-commit/CI 检查：发现就失败」

## 为什么用 Python 而不是那条 grep

老板给的 grep 在 Windows/PowerShell 上不一定有 `grep`，而且**没法表达白名单**——
没有白名单的扫描器第一天就会被绕过（人人加 `--exclude`），或者被噪声淹没（alert fatigue）。
所以：**命中即失败，但允许"显式申请、写明理由"的例外**：

    在该行（或上一行）写：  # portability: allow(<一句话理由>)

## 判据（扫描对象＝**被 git 跟踪**的文件）

| 规则 | 命中 | 级别 |
|---|---|---|
| 本机绝对路径（`C:\\Users\\…`、`/Users/<名字>`、私有盘符目录） | 一律 | **fail** |
| 写死的本机地址（`localhost:<port>` / `127.0.0.1:<port>`） | 产品代码里 | **fail**（tests/ 与 .md 只 warn） |
| 写死的端口默认值（`default=<数字>`、`"port": <数字>`、`AC_PORT", "<数字>"`） | 产品代码里 | **fail** |
| 真实凭据样式（`sk-` + 长串） | 一律 | **fail** |

用法：
    python tools/check_portability.py            # 全量（CI / 发布前）
    python tools/check_portability.py --strict-md # .md 也按 fail 处理
    python tools/check_portability.py --list      # 只列不判（排障用）
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

ALLOW_RE = re.compile(r"portability:\s*allow\(([^)]*)\)")

RULES = [
    # (规则名, 正则, 级别, 适用说明)
    ("本机绝对路径-Windows用户目录", re.compile(r"[A-Za-z]:\\+Users\\+", re.I), "fail", "一律"),
    ("本机绝对路径-posix用户目录", re.compile(r"/Users/[A-Za-z0-9._-]+/"), "fail", "一律"),
    ("本机绝对路径-私有盘符目录", re.compile(r"[A-Za-z]:\\+(?:DSH|O泡|华为云|Marvis|hermes)", re.I), "fail", "一律"),
    # portability: allow(本文件就是规则定义处，正则字面量必然自命中)
    ("写死本机地址-localhost", re.compile(r"localhost:\d{2,5}"), "fail", "产品代码"),
    ("写死本机地址-127.0.0.1", re.compile(r"127\.0\.0\.1:\d{2,5}"), "fail", "产品代码"),
    ("写死端口默认值", re.compile(r"default\s*=\s*(?:18920|9103|3000|5000|8000|8080|5173)\b"), "fail", "产品代码"),
    ("写死端口-配置字面量", re.compile(r'["\']port["\']\s*:\s*\d{2,5}'), "fail", "产品代码"),
    ("写死端口-环境变量默认", re.compile(r'AC_PORT["\']\s*,\s*["\']\d{2,5}["\']'), "fail", "产品代码"),
    ("真实凭据样式", re.compile(r"\bsk-[A-Za-z0-9]{16,}"), "fail", "一律"),
]

#: 只读扫描，不做风格judgement：这些目录里的命中降级为 warn
WARN_ONLY_DIRS = ("agent_community/tests/", "tests/", "design-docs/", "agent_community/frontend/verify/")
#: 不扫（运行期产物 / 备份 / 生成物）
SKIP_PARTS = ("__pycache__", ".git", "data/", "bridges/", "runtime_logs/", "temp/", "node_modules")
SKIP_SUFFIX = (".bak", ".pyc", ".png", ".jpg", ".log", ".whl")
TEXT_SUFFIX = (".py", ".md", ".js", ".html", ".json", ".yml", ".yaml", ".ps1", ".bat",
               ".ini", ".tmpl", ".sh", ".txt", ".example", ".cfg", ".toml")

#: **dev-only 文件**：它们**不进发布面**（同步面不含它们，见 `PUBLISH_EXCLUDE.md`），
#:  里面写的本机路径本来就是"给本机开发者看的坐标"。
#:  ⚠️ 与 `publish_acv4.ps1` 的同步面保持一致：`AGENTS.md` / `README.md` / `design-docs/` /
#:     `agent_community/` / 根 `tests/` 都要发布 ⇒ **不在**本表里。
DEV_ONLY_FILES = (
    "HANDOVER.md", "PUBLISH_EXCLUDE.md", "工作日志.md", "继承书.md",
    "协作书_后端DSH_前端XiaomiMiMo_20261007.md", "交接书_20261007.md",
    "代码审查差距报告.md", "工作间模式设计V5.md",
)
DEV_ONLY_PREFIXES = ("前端任务书_", "spike_")


#: ★**扫描范围 = 发布面**（与 `publish_acv4.ps1` 的同步面一一对应；2026-10-14 收敛）
#:   为什么按范围而不是"所有被跟踪文件"：dev 仓库里还躺着本机运维脚本
#:   （probe_*.py / start_18920_v26.ps1 / publish_acv4.ps1 自身 / dsh-tooling/ …），
#:   它们**不进发布面**；把它们算进来只会让 CI 天天红 ⇒ 最后人人加 --exclude（判据失效）。
SCAN_SCOPE_DIRS = ("agent_community/", "design-docs/", "tests/")
SCAN_SCOPE_FILES = ("README.md", "AGENTS.md", ".gitignore", "pytest.ini",
                    "requirements.txt", "start.py", ".env.example", "CHANGELOG.md",
                    "CONTRIBUTING.md", "LICENSE", "config.example.json", "harnesses.example.json")


def in_publish_scope(rel: str) -> bool:
    low = rel.replace("\\", "/")
    return low.startswith(SCAN_SCOPE_DIRS) or low in SCAN_SCOPE_FILES


def is_dev_only(rel: str) -> bool:
    low = rel.replace("\\", "/")
    if low in DEV_ONLY_FILES:
        return True
    name = low.rsplit("/", 1)[-1]
    return any(name.startswith(p) for p in DEV_ONLY_PREFIXES)


def tracked_files() -> list[str]:
    out = subprocess.run(["git", "ls-files"], cwd=str(ROOT), capture_output=True)
    files = out.stdout.decode("utf-8", "replace").split("\n")
    if not any(files):
        # 非 git 环境：退化为遍历（CI 里被 clone 出来一定有 git）
        return [str(p.relative_to(ROOT)).replace("\\", "/")
                for p in ROOT.rglob("*") if p.is_file()]
    return [f for f in files if f.strip()]


#: ★**默认值登记处**：这些文件的职责**就是**"登记出厂默认值长什么样"。
#:   · `env_spec.py` 是登记表本身；
#:   · `.env.example` 是它**生成**出来的（`tools/gen_env_docs.py`）。
#:  在这里出现 `http://127.0.0.1:11434` 不是"把环境写死在逻辑里"，而是**出厂默认值** ——
#:  用户改环境变量即可覆盖（`PORTABILITY.md` §1）。判红会逼着人到处写
#:  `# portability: allow(...)`（连生成器都得支持注入注释），属于**判据失效**：
#:  真正要拦的是"产品逻辑里写死地址"，不是"默认值表里写了默认值"。
#:  ⚠️ 但**产品逻辑**里出现同样的地址仍然判红 —— 那不叫默认值，叫写死。
DEFAULTS_REGISTRY = ("agent_community/platform/env_spec.py", ".env.example")


def is_defaults_registry(rel: str) -> bool:
    return rel.replace("\\", "/") in DEFAULTS_REGISTRY


def is_skipped(rel: str) -> bool:
    low = rel.replace("\\", "/")
    if any(s in low for s in SKIP_PARTS):
        return True
    if any(low.endswith(s) for s in SKIP_SUFFIX):
        return True
    return not low.endswith(TEXT_SUFFIX)


def scan() -> tuple[list[dict], list[dict], list[str]]:
    fails, warns = [], []
    skipped_out_of_scope: list[str] = []
    for rel in tracked_files():
        if is_skipped(rel):
            continue
        p = ROOT / rel
        if not p.is_file():
            continue
        try:
            lines = p.read_text(encoding="utf-8", errors="replace").split("\n")
        except OSError:
            continue
        low = rel.replace("\\", "/")
        if is_dev_only(low):          # 明确点名的 dev-only 文件
            continue
        if is_defaults_registry(low):  # 默认值登记处（登记表本体 + 它的生成物）
            skipped_out_of_scope.append(low)
            continue
        if not in_publish_scope(low):  # 不在发布面：本机运维脚本等，不拦
            skipped_out_of_scope.append(low)
            continue
        is_md = low.endswith(".md")
        # 三引号 docstring 跟踪（文档说明里的示例地址 -> 只 warn）
        in_doc = False
        for i, line in enumerate(lines, 1):
            quotes = line.count('"""') + line.count("'''")
            # 单行里成对的三引号（如单行 docstring）也算文档行
            _stripped = line.lstrip()
            was_in_doc = (in_doc or quotes >= 1
                          or _stripped.startswith(('"""', "'''")))
            if quotes % 2 == 1:
                in_doc = not in_doc
            if line.lstrip().startswith("#") and "portability: allow" not in line:
                # 注释行跳过（规则定义、说明文字不算硬编码）
                continue
            # 行尾注释之后的部分不算（代码写完后的说明）
            code_part = line.split("#", 1)[0] if "#" in line else line
            if "#" in line and not ALLOW_RE.search(line):
                line = code_part
            allowed = bool(ALLOW_RE.search(line)) or (i > 1 and bool(ALLOW_RE.search(lines[i - 2])))
            for name, rx, level, scope in RULES:
                if not rx.search(line):
                    continue
                if allowed:
                    continue
                item = {"rule": name, "file": rel, "line": i, "text": line.strip()[:110]}
                # 降级：测试/文档/验收脚本/docstring 里的命中 -> warn
                downgrade = (any(low.startswith(d) for d in WARN_ONLY_DIRS)
                             or (is_md and level == "fail" and scope != "一律")
                             or (was_in_doc and level == "fail" and scope != "一律"))
                if level == "fail" and not downgrade:
                    fails.append(item)
                else:
                    warns.append(item)
                break
    return fails, warns, skipped_out_of_scope


def main() -> int:
    args = set(sys.argv[1:])
    fails, warns, out_of_scope = scan()
    list_only = "--list" in args
    strict_md = "--strict-md" in args
    if strict_md:
        # 把 .md 的 warn 里属于"本机地址"的提到 fail
        move = [w for w in warns if w["file"].endswith(".md") and "本机地址" in w["rule"]]
        warns = [w for w in warns if w not in move]
        fails += move

    print(f"可移植性扫描（范围＝发布面）：fail={len(fails)} warn={len(warns)}"
          f"  跳过（非发布面）={len(out_of_scope)}")
    if warns:
        print("\n-- 警告（测试/文档/验收脚本内部；不阻断）--")
        for w in warns[:15]:
            print(f"   [{w['rule']}] {w['file']}:{w['line']}  {w['text']}")
        if len(warns) > 15:
            print(f"   …另 {len(warns)-15} 条")
    if fails:
        print("\n-- 失败（必须改：产品代码里不许有环境相关硬编码）--")
        for f in fails:
            print(f"   [{f['rule']}] {f['file']}:{f['line']}  {f['text']}")
        print("\n修法：从环境变量读（`agent_community/platform/env_spec.py` 登记 + `platform_address` 解析），"
              "并同步更新 .env.example / README 环境变量表。")
        print("确有理由的例外：在该行写 `# portability: allow(<理由>)`。")
        return 0 if list_only else 1
    print("\n[OK] 没有发现环境相关硬编码。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
