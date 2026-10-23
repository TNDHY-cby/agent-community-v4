# -*- coding: utf-8 -*-
"""判据：**发布面上的链接与许可登记必须是真的**（2026-10-23 补，起因是外部审查报告第 3 轮 P1）。

## 为什么有这个工具

外部审查（v4.35.1，第 3 轮）抓到两条真事故，根子是同一个：**开发机有、发布面没有**。

1. README:498 指向 `THIRD_PARTY_NOTICES.md`（许可登记入口）—— Gitee 上没有这个文件；
2. README:38 指向 `docs/FAQ.md`（"装不上/卡住怎么办"第一站）—— `docs/` 从来没进同步面；
3. 连带后果：仓库自带的 `tools/third_party_check.py` 在**公开树里实跑 FAIL、退出码 1** ——
   陌生人 clone 下来，自带判据就是红的。

原来的 `pre_release_check` 里没有 `third_party_check.py`，也没有任何"README 的链接指向的文件
真的会发布"这种检查 ⇒ "发布前全过"照样能发出一条死链 + 一条红判据的版本。

## 两口模式（默认只看开发副本；`--oss` 才看发布副本）

```bash
python tools/publish_face_check.py                 # 开发副本：链接存在性 + 许可登记（发版前跑）
AC_OSS_DIR=<发布副本目录> python tools/publish_face_check.py --oss   # 再加：这些目标在发布副本里也真的存在（发版后跑）
```

| 格 | 判据 | 红了说明什么 |
|---|---|---|
| ① | 发布面根文档（README / AGENTS / PORTABILITY / PRODUCTIZATION / CLAUDE / 项目完整模型）里的**相对 .md 链接**都能在开发副本找到 | 写了引用但文件没写/改名了 |
| ② | `tools/third_party_check.py` 在**开发副本**里 OK | 带 SPDX 的代码没登记到 `THIRD_PARTY_NOTICES.md` |
| ③ | （`--oss`）① 的每个目标在**发布副本**里也存在 | 文件在开发机有、发布面没有 ⇒ 对外死链 |
| ④ | （`--oss`）`third_party_check.py` 在**发布副本**里也 OK | 公开仓库自带判据是红的（本次事故的原始形态） |

退出码：0=全过；1=有红；2=有核实不了的（例如 `--oss` 时发布副本不存在）。
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent.parent

#: **只存在于发布副本**的文件（开发副本本来就没有）——判据 ① 不拿它们判红，
#: 判据 ③（`--oss`）才逐个验"发布副本里真的有"。改这张表时请同时确认发布副本里确有该文件。
PUBLISH_ONLY = frozenset({
    "CHANGELOG.md", "LICENSE", "CONTRIBUTING.md",
    "docs/首次运行.md", "docs/roadmap.md",
})

#: 发布副本目录：`--oss-dir` > `AC_OSS_DIR` > 空（为空＝不做发布副本侧检查，如实报"没跑成"）。
#: ⚠️ 这里**不写死本机路径**（2026-10-23：第一版写死了 Windows 盘符下的路径 ⇒ Linux 侧判据直接红，
#:    而且违反可移植性规则）。
OSS_ENV = "AC_OSS_DIR"

#: 发布面上的"对外根文档"（README 是入口，其余是规则/模型入口）
FACE_DOCS = ("README.md", "AGENTS.md", "PORTABILITY.md", "PRODUCTIZATION.md",
             "CLAUDE.md", ".cursorrules", "THIRD_PARTY_NOTICES.md",
             "design-docs/项目完整模型.md")

MD_LINK = re.compile(r"\[[^\]]*\]\((?P<t>[^)\s]+)\)")


def _md_targets(path: Path) -> list:
    """取一份文档里的**相对**目标（跳过 http/https/mailto/锚点）。"""
    if not path.is_file():
        return []
    out = []
    for m in MD_LINK.finditer(path.read_text(encoding="utf-8", errors="replace")):
        t = m.group("t").strip()
        if t.startswith(("http://", "https://", "mailto:", "#", "<")):
            continue
        t = t.split("#", 1)[0].strip()
        if t:
            out.append(t)
    return out


def _run_third_party(cwd: Path) -> tuple:
    tool = cwd / "tools" / "third_party_check.py"
    if not tool.is_file():
        return False, "找不到 tools/third_party_check.py"
    r = subprocess.run([sys.executable, str(tool)], cwd=str(cwd), capture_output=True,
                       text=True, encoding="utf-8", errors="replace", timeout=300)
    tail = " / ".join([ln.strip() for ln in ((r.stdout or "") + (r.stderr or "")).splitlines()
                       if ln.strip()][-2:])
    return r.returncode == 0, tail[:220]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--oss", action="store_true", help="同时检查发布副本（发版后跑）")
    ap.add_argument("--oss-dir", default="", help="发布副本目录（默认读环境变量 %s）" % OSS_ENV)
    a = ap.parse_args()
    oss_dir = (a.oss_dir or os.environ.get(OSS_ENV) or "").strip()

    fails, unknown = [], []
    print("=" * 78)
    print("发布面判据 —— 开发副本 %s" % ROOT)
    print("=" * 78)

    # ── ① 根文档里的相对链接：**两者都没有**才算红 ────────────────
    #   为什么不是"开发副本里没有就红"：有些文件是**只存在于发布副本**的发布物件
    #   （见 `PUBLISH_ONLY`），开发副本本来就没有它们（历史分工），判红只会制造假红。
    oss_hint = Path(oss_dir) if oss_dir else None
    missing, oss_only = [], []
    for doc in FACE_DOCS:
        p = ROOT / doc
        if not p.is_file():
            fails.append("① 发布面根文档缺失：%s" % doc)
            continue
        for t in _md_targets(p):
            target = (p.parent / t)
            if target.exists():
                continue
            try:
                rel = target.resolve().relative_to(ROOT.resolve()).as_posix()
            except ValueError:
                continue                                    # 指向仓库外的，不判
            if rel in PUBLISH_ONLY:
                # 发布面独有的文件：开发副本没有是**正常**的；真伪留给 ③（--oss）验
                oss_only.append("%s（被 %s 引用）" % (rel, doc))
            elif oss_hint is not None and (oss_hint / rel).exists():
                oss_only.append("%s（被 %s 引用；发布副本里有）" % (rel, doc))
            else:
                missing.append("%s → %s" % (doc, t))
    if missing:
        fails.append("① 有 %d 处相对链接**开发副本与发布副本都没有**（死链）：%s"
                     % (len(missing), "；".join(missing[:5])))
    else:
        print("  ① 发布面根文档的相对链接全部可达 ✅")
    for note in oss_only[:6]:
        print("     （发布面物件，开发副本没有是正常的）%s" % note)

    # ── ② 许可登记（开发副本）──────────────────────────────────
    ok, why = _run_third_party(ROOT)
    if ok:
        print("  ② third_party_check（开发副本）✅ %s" % why)
    else:
        fails.append("② third_party_check（开发副本）红：%s" % why)

    # ── ③④ 发布副本 ─────────────────────────────────────────
    if a.oss:
        oss = Path(oss_dir) if oss_dir else None
        if oss is None:
            unknown.append("③ 没给发布副本目录（`--oss-dir` 或环境变量 %s）⇒ 只看了一部分" % OSS_ENV)
        elif not oss.is_dir():
            unknown.append("③ 发布副本不存在：%s ⇒ 只看了一部分，不算通过" % oss)
        else:
            print("  ③ 检查发布副本 %s" % oss)
            absent = []
            for doc in FACE_DOCS:
                p = ROOT / doc
                if not p.is_file():
                    absent.append("%s（发布面根文档本身在开发副本就没有）" % doc)
                    continue
                for t in _md_targets(p):
                    rel = (p.parent / t).resolve()
                    try:
                        rel_str = rel.relative_to(ROOT.resolve()).as_posix()
                    except ValueError:
                        continue                                    # 指向仓库外的链接不判
                    if not (oss / rel_str).exists():
                        absent.append("%s（被 %s 引用）" % (rel_str, doc))
            if absent:
                fails.append("③ 这些文件**发布副本没有**（对外就是死链）：%s"
                             % "；".join(sorted(set(absent))[:6]))
            else:
                print("  ③ 根文档引用的目标在发布副本里都在 ✅")
            ok2, why2 = _run_third_party(oss)
            if ok2:
                print("  ④ third_party_check（发布副本）✅ %s" % why2)
            else:
                fails.append("④ third_party_check（发布副本）红（陌生人拿到的仓库自带红判据）：%s" % why2)

    print("-" * 78)
    for u in unknown:
        print("  [没跑成] %s" % u)
    for f in fails:
        print("  [红] %s" % f)
    if fails:
        print("结论：**%d 条红** ⇒ 发布面还不能对外" % len(fails))
        return 1
    if unknown:
        print("结论：**能查的都绿**，但有 %d 项没查成（＝部分）" % len(unknown))
        return 2
    print("结论：**发布面全绿**（链接可达 / 许可登记一致%s）"
          % ("、发布副本也一致" if a.oss else "；发布后加 --oss 复核"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
