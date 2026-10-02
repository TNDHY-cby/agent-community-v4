#!/usr/bin/env python
"""敏感信息残留扫描 — 提交前自检用。

扫描仓库文本文件中的：
  1. 本地绝对路径（C:\\Users\\...、D:\\... 私有目录）
  2. 真实 harness / 内部工具名称
  3. 疑似真实密钥字面量
  4. 文档元数据块（AIGC 声明等）

命中即视为不通过（退出码 1）。占位值（sk-xxx / sk-your-key / harness-a）在白名单内。

用法：
    python tests/scan_sensitive.py [要扫描的目录，默认仓库根] [--secrets-only]

--secrets-only（发布闸门用）：
    仅【疑似真实密钥】导致退出码 1 —— 真凭据绝不允许发布；
    路径 / 真名 / 元数据类命中仅告警，交人工判断。
    默认（不带本参数）行为与历史一致：任何命中都返回 1。

修改记录 2026-10-02：
  - 修 UnicodeEncodeError：Windows 控制台默认 GBK，打印 ✅ 会直接崩，导致扫描跑不完整
  - 跳过本文件自身（它会匹配到自己源码里的正则字面量，产生 3 条假命中）
  - 白名单补上脱敏测试夹具 sk-abcdefghijklmnop（test_security / test_smoke 用）
  - 新增 --secrets-only 分级模式
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

# Windows 控制台默认 GBK；不修的话打印 ✅ 会 UnicodeEncodeError 崩在半路。
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # pragma: no cover - 极老的解释器/被重定向的 stdout
    pass

ARGS = [a for a in sys.argv[1:] if not a.startswith("--")]
FLAGS = {a for a in sys.argv[1:] if a.startswith("--")}
SECRETS_ONLY = "--secrets-only" in FLAGS

ROOT = Path(__file__).resolve().parents[1]
SELF = Path(__file__).resolve()
SCAN_DIR = Path(ARGS[0]).resolve() if ARGS else ROOT

SKIP_DIRS = {".git", "__pycache__", ".pytest_cache", "data", "runtime_logs", ".venv", "venv", "node_modules"}
TEXT_EXT = {".py", ".md", ".json", ".html", ".js", ".css", ".txt", ".tmpl", ".yml", ".yaml", ".toml", ".cfg", ".ini", ".example"}

# 每条规则带 severity：
#   secret —— 真凭据，必须拦截（任何模式下都返回 1）
#   policy —— 路径 / 真名 / 元数据，需人工判断；--secrets-only 下仅告警
RULES = [
    ("本地绝对路径-用户目录", re.compile(r"[A-Za-z]:\\+Users\\+", re.I), "policy"),
    ("本地绝对路径-私有盘符目录", re.compile(r"[A-Za-z]:\\+(?:DSH|O泡|Marvis|hermes|bridge\\b)", re.I), "policy"),
    ("真实harness名-my_harness", re.compile(r"my_harness|BaiLongma", re.I), "policy"),
    ("真实harness名-TraeWork", re.compile(r"TraeWork|TRAE SOLO|trae-cn", re.I), "policy"),
    ("真实harness名-dsh私有壳", re.compile(r"dsh-web-\d|dsh_harness|dsh_acp_bridge|deepseek-harness-acp"), "policy"),
    ("疑似真实密钥", re.compile(r"sk-[A-Za-z0-9]{16,}"), "secret"),
    ("文档元数据块", re.compile(r"AIGC:\s*$", re.M), "policy"),
]
ALLOW = [
    re.compile(r"sk-xxx"),
    re.compile(r"sk-your-[a-z-]*key"),
    re.compile(r"sk-you[r]?-key"),
    re.compile(r"harness-a|harness-b"),
    re.compile(r"AC_AI_API_KEY=sk-"),
    # 脱敏测试夹具（tests/test_security.py、tests/test_smoke.py）
    re.compile(r"sk-abcdefghijklmnop"),
]


def allowed(line: str) -> bool:
    return any(a.search(line) for a in ALLOW)


def main() -> int:
    hits: list[tuple[str, str, int, str, str]] = []
    scanned = 0
    for dirpath, dirnames, filenames in os.walk(SCAN_DIR):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fn in filenames:
            ext = Path(fn).suffix.lower()
            if ext not in TEXT_EXT and fn not in {".gitignore", "LICENSE"}:
                continue
            p = Path(dirpath) / fn
            # 跳过自身：本文件源码里就写着这些正则字面量，否则必然自我命中
            if p.resolve() == SELF:
                continue
            scanned += 1
            try:
                lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
            except Exception:
                continue
            for i, line in enumerate(lines, 1):
                if allowed(line):
                    continue
                for label, rx, severity in RULES:
                    if rx.search(line):
                        hits.append((severity, label, i, str(p.relative_to(SCAN_DIR)), line.strip()[:140]))

    secrets = [h for h in hits if h[0] == "secret"]
    policy = [h for h in hits if h[0] != "secret"]

    MAX_POLICY_LINES = 20

    def dump(rows: list[tuple[str, str, int, str, str]], limit: int | None = None) -> None:
        shown = rows if limit is None else rows[:limit]
        for _sev, label, ln, rel, text in shown:
            print(f"  - [{label}] {rel}:{ln}  {text}")
        if limit is not None and len(rows) > limit:
            print(f"  ... 另有 {len(rows) - limit} 处同类残留未列出（直接跑 tests/scan_sensitive.py 看全量）")

    if secrets:
        print(f"[FAIL] 扫描 {scanned} 个文件：{len(secrets)} 处疑似真实凭据（必须处理）")
        dump(secrets)
    if policy:
        if SECRETS_ONLY:
            print(f"[WARN] 另有 {len(policy)} 处策略类残留（本地路径 / 真实名 / 元数据），未拦截，请人工判断：")
            dump(policy, MAX_POLICY_LINES)
        else:
            print(f"[FAIL] 另有 {len(policy)} 处策略类残留（本地路径 / 真实名 / 元数据）：")
            dump(policy)

    if secrets or (policy and not SECRETS_ONLY):
        return 1
    if policy:
        print(f"[OK] 扫描 {scanned} 个文件：无真实凭据（{len(policy)} 处策略类残留已告警放行）。")
        return 0
    print(f"[OK] 扫描 {scanned} 个文件，未发现敏感信息残留。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
