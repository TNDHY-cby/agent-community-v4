#!/usr/bin/env python
"""敏感信息残留扫描 — 提交前自检用。

扫描仓库文本文件中的：
  1. 本地绝对路径（C:\\Users\\...、D:\\... 私有目录）
  2. 真实 harness / 内部工具名称
  3. 疑似真实密钥字面量
  4. 文档元数据块（AIGC 声明等）

命中即视为不通过（退出码 1）。占位值（sk-xxx / sk-your-key / harness-a）在白名单内。

用法：
    python tests/scan_sensitive.py [要扫描的目录，默认仓库根]
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCAN_DIR = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else ROOT

SKIP_DIRS = {".git", "__pycache__", ".pytest_cache", "data", "runtime_logs", ".venv", "venv", "node_modules"}
TEXT_EXT = {".py", ".md", ".json", ".html", ".js", ".css", ".txt", ".tmpl", ".yml", ".yaml", ".toml", ".cfg", ".ini", ".example"}

RULES = [
    ("本地绝对路径-用户目录", re.compile(r"[A-Za-z]:\\+Users\\+", re.I)),
    ("本地绝对路径-私有盘符目录", re.compile(r"[A-Za-z]:\\+(?:DSH|O泡|Marvis|hermes|bridge\\b)", re.I)),
    ("真实harness名-小白龙", re.compile(r"小白龙|BaiLongma", re.I)),
    ("真实harness名-TraeWork", re.compile(r"TraeWork|TRAE SOLO|trae-cn", re.I)),
    ("真实harness名-dsh私有壳", re.compile(r"dsh-web-\d|dsh_harness|dsh_acp_bridge|deepseek-harness-acp")),
    ("疑似真实密钥", re.compile(r"sk-[A-Za-z0-9]{16,}")),
    ("文档元数据块", re.compile(r"AIGC:\s*$", re.M)),
]
ALLOW = [
    re.compile(r"sk-xxx"),
    re.compile(r"sk-your-[a-z-]*key"),
    re.compile(r"sk-you[r]?-key"),
    re.compile(r"harness-a|harness-b"),
    re.compile(r"AC_AI_API_KEY=sk-"),
]


def allowed(line: str) -> bool:
    return any(a.search(line) for a in ALLOW)


def main() -> int:
    hits: list[tuple[str, int, str, str]] = []
    scanned = 0
    for dirpath, dirnames, filenames in os.walk(SCAN_DIR):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fn in filenames:
            ext = Path(fn).suffix.lower()
            if ext not in TEXT_EXT and fn not in {".gitignore", "LICENSE"}:
                continue
            p = Path(dirpath) / fn
            scanned += 1
            try:
                lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
            except Exception:
                continue
            for i, line in enumerate(lines, 1):
                if allowed(line):
                    continue
                for label, rx in RULES:
                    if rx.search(line):
                        hits.append((label, i, str(p.relative_to(SCAN_DIR)), line.strip()[:140]))

    if hits:
        print(f"[FAIL] 扫描 {scanned} 个文件，命中 {len(hits)} 处敏感残留：")
        for label, ln, rel, text in hits:
            print(f"  - [{label}] {rel}:{ln}  {text}")
        return 1
    print(f"[OK] 扫描 {scanned} 个文件，未发现敏感信息残留。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
