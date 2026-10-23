# -*- coding: utf-8 -*-
"""判据：**HANDOVER 的「现行继承书」指针必须只有一个、且指向真文件**。

## 为什么有这个工具（2026-10-22 夜场三，踩过的真事故）

交接链的每一轮都只做同一件事：**把上一轮的「🟢 现行继承书」块换成新的**。
问题是——**更早的残留块没人接管**。2026-10-22 夜场二收工时，HANDOVER 顶部同时躺着两条
「现行继承书」：上面那条指向 `…20261019_凌晨场…`（只覆盖到 **v4.15.0**），
下面那条才是新的（覆盖到 v4.35.1）。**新会话从上往下读，先撞到的就是过期那条** ⇒
一开局就用 4.15.0 的坐标干活。这属于"失败不说话"：文件看起来完全正常。

⇒ 本工具把三件事钉成判据（**按真结果判，不查源码文本**）：

| # | 判据 | 红了说明什么 |
|---|---|---|
| ① | `HANDOVER.md` 里 `🟢 **现行继承书` **恰好出现 1 次** | 0 次＝没有入口；≥2 次＝有旧指针在冒充现行 |
| ② | 指针指向的**文件真实存在**（`Path.exists()`） | 知识库被改名/搬家，指针成了死链 |
| ③ | 坐标里的 `dev <sha>` 是**本仓库真有的提交**（`git cat-file -e`） | 手抄 sha 抄错，或写的是别的副本的提交 |

另外两条只**提示不判红**（它们天然滞后，红起来会逼人天天改无关行）：
坐标版本 `vX.Y.Z` 与 `agent_community/__init__.py` 的 `__version__` 不一致；`dev <sha>` 不是当前 HEAD。

## 用法

```bash
python tools/handover_ptr_check.py            # 判据（exit 0=全过 / 1=有红 / 2=只看了一部分）
python tools/handover_ptr_check.py --root <别的副本>   # 对指定副本判（默认本工具所在仓库）
python tools/handover_ptr_check.py --allow-missing     # 没有 HANDOVER 时如实报"本副本不适用"并 exit 0
```

发布副本（开源树）**没有 `HANDOVER.md`**（它含真实本机坐标，dev-only）⇒
不带 `--allow-missing` 时**如实说"没有这个文件，跳过"并 exit 2**（＝只看了一部分，不算通过），
不假装绿；`tools/pre_release_check.py` 调它时带 `--allow-missing`（那里是**不适用**，不是通过）。
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

MARKER = "🟢 **现行继承书"
PTR_RE = re.compile(r"`([^`]*上下文继承书[^`]*\.md)`")
VER_RE = re.compile(r"v(\d+\.\d+\.\d+)")
DEV_RE = re.compile(r"dev[^`\n]{0,12}`([0-9a-f]{7,40})`")


def _git(root: Path, *args: str) -> tuple:
    """跑一条 git；返回 (rc, stdout)。git 不在或不是仓库时 rc=-1。"""
    try:
        r = subprocess.run(["git", "-C", str(root), *args], capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=30)
        return r.returncode, (r.stdout or "").strip()
    except Exception:  # noqa: BLE001 — 没有 git（陌生人的拷贝）是正常情况，下面如实说
        return -1, ""


def _is_repo(root: Path) -> bool:
    """这个目录到底是不是 git 仓库 —— 「没 git」要如实跳过，不能算成「sha 抄错了」。"""
    rc, out = _git(root, "rev-parse", "--is-inside-work-tree")
    return rc == 0 and out.strip() == "true"


def check(root: Path, allow_missing: bool = False) -> int:
    handover = root / "HANDOVER.md"
    print("HANDOVER 指针判据（副本 %s）" % root)
    if not handover.is_file():
        if allow_missing:
            print("  [不适用] 本副本没有 HANDOVER.md（发布副本/克隆都不带它，它含真实本机坐标）")
            print("结论：**这条判据在本副本不适用**（如实说，不是通过、也不是红）")
            return 0
        print("  [跳过] 本副本没有 HANDOVER.md（发布副本/克隆都不带它）⇒ 只看了一部分，不算通过")
        return 2

    text = handover.read_text(encoding="utf-8", errors="replace")
    red = 0

    # ① 现行指针必须恰好一条
    hits = [i + 1 for i, ln in enumerate(text.splitlines()) if MARKER in ln]
    print("  ① 「%s」出现 %d 次（应为 1 次）%s" % (MARKER, len(hits),
                                             "，行号 %s" % hits if hits else ""))
    if len(hits) != 1:
        red += 1
        if len(hits) == 0:
            print("     ✗ 没有现行继承书入口 —— 新会话不知道该读哪份")
        else:
            print("     ✗ 有旧指针在冒充现行（新会话从上往下读，先撞到的是最上面那条）：")
            lines = text.splitlines()
            for ln in hits:
                nxt = lines[ln:ln + 2] if ln < len(lines) else []
                print("        行 %d: %s" % (ln, lines[ln - 1][:100]))
                for extra in nxt:
                    if PTR_RE.search(extra):
                        print("              → %s" % PTR_RE.search(extra).group(1))

    # ② 指针指向的必须是真文件
    target = None
    blocks = text.splitlines()
    if hits:
        head = blocks[hits[0] - 1:hits[0] + 4]
        for ln in head:
            m = PTR_RE.search(ln)
            if m:
                target = m.group(1).strip()
                break
    if target:
        p = Path(target)
        ok = p.is_file()
        print("  ② 指针指向的文件存在：%s → %s" % ("是" if ok else "否", target))
        if not ok:
            red += 1
            print("     ✗ 指针是死链（文件被改名/搬家，或路径手抄错了）")
    else:
        red += 1
        print("  ② ✗ 指针那几行里找不到 `…上下文继承书….md` 的反引号路径")

    # ③ 坐标里的 dev sha 必须是本仓库真有的提交
    m = DEV_RE.search(text)
    in_repo = _is_repo(root)
    if m:
        sha = m.group(1)
        if not in_repo:
            print("  ③ [跳过] 这个目录不是 git 仓库（发布副本/裸拷贝）⇒ 不判 sha")
        else:
            rc, _ = _git(root, "cat-file", "-e", sha + "^{commit}")
            if rc == 0:
                rc2, out2 = _git(root, "log", "-1", "--format=%h %s", "HEAD")
                head_short = out2.split()[0] if out2 else "?"
                note = "" if out2.startswith(sha[:7]) else "（坐标点落后于 HEAD %s，通常正常）" % head_short
                print("  ③ dev `%s` 是本仓库真有的提交 %s" % (sha, note))
            else:
                red += 1
                print("  ③ ✗ dev `%s` 不是本仓库的提交（手抄错了？还是写成了别的副本的 sha？）" % sha)
    else:
        print("  ③ [提示] 坐标里没写 `dev <sha>`，跳过了这条")

    # 提示项：版本号一处真相（只认「当前坐标」那几行里的版本，不认上面"覆盖 v4.23.0 起"这种区间端点）
    ver_file = root / "agent_community" / "__init__.py"
    if ver_file.is_file():
        vm = re.search(r'__version__\s*=\s*"([^"]+)"', ver_file.read_text(encoding="utf-8", errors="replace"))
        real = vm.group(1) if vm else None
        claimed = None
        for ln in text.splitlines():
            if "当前坐标" in ln or "服务 18920" in ln:
                c = VER_RE.search(ln)
                if c:
                    claimed = c
                    break
        if real and claimed and claimed.group(1) != real:
            print("  · 提示：坐标写 v%s，`__init__.py` 是 %s（滞后就顺手改，但不算红）"
                  % (claimed.group(1), real))
        elif real:
            print("  · 版本号与 `__init__.py` 一致（%s）" % real)

    if red:
        print("结论：**%d 条红** ⇒ 先修指针再开工（别让下一个会话拿过期坐标干活）" % red)
        return 1
    print("结论：**全过**（现行指针唯一、指向真文件、dev sha 真存在）")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="HANDOVER「现行继承书」指针判据")
    ap.add_argument("--root", default=None, help="要判的副本根目录（默认本工具所在仓库）")
    ap.add_argument("--check", action="store_true", help="（兼容各工具的 --check 习惯；本工具只读不写）")
    ap.add_argument("--allow-missing", action="store_true",
                    help="没有 HANDOVER.md 时如实报「本副本不适用」并 exit 0"
                         "（给发布副本/CI 用：那里本来就没有这个 dev-only 文件）")
    a = ap.parse_args()
    root = Path(a.root).resolve() if a.root else Path(__file__).resolve().parent.parent
    print("=" * 78)
    return check(root, allow_missing=a.allow_missing)


if __name__ == "__main__":
    raise SystemExit(main())
