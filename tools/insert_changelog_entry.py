# -*- coding: utf-8 -*-
"""把一条 CHANGELOG 条目插到**正确位置**，并校验「版本号与 CHANGELOG 同笔」。

## 为什么有这个工具（2026-10-15 真实事故）

CI 里有一条守卫 `test_foundation_base.py::TestVersionSingleSource::
test_changelog_mentions_current_version`：**CHANGELOG 必须含当前版本的条目**。

而发布流程是「**先撤回 CHANGELOG**（为了让预检『oss 工作树必须干净』通过）→ 同步 + 提交 → 事后补 CHANGELOG」。
于是**提交那一刻**版本已经升到 4.8.8、CHANGELOG 还停在 4.8.7 ⇒ **CI 两个测试作业全红**，
而我本地全绿（因为我在 dev 侧根本没这个文件）。
⇒ 根因不是"忘了补"，是**流程把两件必须同时存在的事拆开了**。

修法：让**发布脚本自己**在同步之后、提交之前把条目插进去 —— 版本与 CHANGELOG 永远在**同一笔提交**里。

## 用法

```bash
python tools/insert_changelog_entry.py --repo <仓库路径> --file <条目.md>   # 插入（幂等）
python tools/insert_changelog_entry.py --repo <仓库路径> --check            # 只校验：当前版本有没有条目
```

- **幂等**：条目里那个版本号已经在 CHANGELOG 里 ⇒ 什么都不做、退 0（发布脚本可放心重复跑）；
- **保留换行风格**：按现有文件的 CRLF/LF 写回（别把整个文件的行尾改掉，那会制造巨大的假 diff）；
- **UTF-8、不加 BOM**（本项目吃过 PowerShell 读写中文文件的双重编码亏）。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# ★2026-10-15 实测踩到：**被管道接走时 Python 的 stdout 默认是控制台码页（中文 Windows = cp936）** ——
#   本工具第二次打印里的 `✓` 编不出去 ⇒ UnicodeEncodeError ⇒ **整个发布流程中止**
#   （而条目其实已经插进去了，属于"看起来失败其实成功"的反面：报错与事实不符）。
#   所有会被脚本管道接走的工具都必须先固定 stdout 编码。
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

_VER_HEAD_RE = re.compile(r"^##\s*\[(?P<ver>[^\]]+)\]", re.MULTILINE)


def current_version(repo: Path) -> str:
    """从 `agent_community/__init__.py` 读当前版本（**一处真相**：包自身的 `__version__`）。"""
    p = repo / "agent_community" / "__init__.py"
    m = re.search(r'^__version__\s*=\s*["\']([^"\']+)["\']', p.read_text(encoding="utf-8"), re.MULTILINE)
    if not m:
        raise SystemExit("读不出 __version__：%s" % p)
    return m.group(1)


def entry_version(entry_text: str) -> str:
    m = _VER_HEAD_RE.search(entry_text)
    if not m:
        raise SystemExit("条目里没有 `## [x.y.z]` 标题行 —— 不知道它属于哪个版本")
    return m.group("ver")


def changelog_has_version(text: str, ver: str) -> bool:
    return ("[%s]" % ver) in text


def insert(changelog_text: str, entry_text: str, nl: str) -> tuple[str, bool]:
    """把条目插到**第一个 `## [` 之前**。返回 `(新文本, 是否真的插了)`。

    ⚠️ 条目的行尾**先归一到目标文件的风格**再插（2026-10-15 由守卫抓出来的）：
    条目文件通常是 LF，而 CHANGELOG 可能是 CRLF —— 直接插会**行尾混用**，
    下一次谁用编辑器存一遍就变成整文件 diff。这类"假 diff"最费人眼。
    """
    ver = entry_version(entry_text)
    if changelog_has_version(changelog_text, ver):
        return changelog_text, False
    body = entry_text.replace("\r\n", "\n").replace("\r", "\n")   # 先归一
    if nl == "\r\n":
        body = body.replace("\n", "\r\n")                          # 再按目标风格写
    m = _VER_HEAD_RE.search(changelog_text)
    entry = body.strip("\r\n") + nl + nl
    if not m:
        return changelog_text.rstrip("\r\n") + nl + nl + entry, True
    pos = m.start()
    return changelog_text[:pos] + entry + changelog_text[pos:], True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    ap.add_argument("--file", default="", help="条目 markdown（含 `## [x.y.z] - 日期` 标题行）")
    ap.add_argument("--check", action="store_true", help="只校验当前版本有没有条目")
    a = ap.parse_args()
    repo = Path(a.repo)
    cl_path = repo / "CHANGELOG.md"
    if not cl_path.exists():
        print("[changelog] 没有 CHANGELOG.md：%s" % cl_path)
        return 1
    raw = cl_path.read_bytes()
    text = raw.decode("utf-8-sig")          # 容忍意外带上的 BOM
    ver = current_version(repo)

    if a.check:
        ok = changelog_has_version(text, ver)
        print("[changelog] 版本 %s %s" % (ver, "有条目 ✓" if ok else "**在 CHANGELOG 里没有条目**"))
        return 0 if ok else 1

    if not a.file:
        print("要么给 --file，要么给 --check")
        return 2
    entry = Path(a.file).read_text(encoding="utf-8")
    nl = "\r\n" if "\r\n" in text else "\n"
    new_text, changed = insert(text, entry, nl)
    if not changed:
        print("[changelog] %s 已存在条目，跳过（幂等）" % entry_version(entry))
    else:
        # 不加 BOM、保持原换行风格
        cl_path.write_text(new_text, encoding="utf-8", newline="")
        print("[changelog] 已插入 %s 条目（%d 字）" % (entry_version(entry), len(new_text) - len(text)))
    ok = changelog_has_version(cl_path.read_text(encoding="utf-8", errors="replace"), ver)
    print("[changelog] 复核：版本 %s %s" % (ver, "有条目 ✓" if ok else "**仍缺条目**"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
