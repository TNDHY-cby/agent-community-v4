# -*- coding: utf-8 -*-
"""tools/fix_doc_split.py —— 把**一份大文档按一级标题拆成「主索引 + 分册」**（可复用）。

## 为什么有它

老板 2026-10-21 两句话定了文档纪律：「**上下文过长，得分文件、做索引**」
「**项目完整建模里面不应该存在旧数据旧资料，防止污染建模**」。
当天我先用一次性脚本拆了《项目完整模型》（701 行 → 主索引 + 12 分册），
又拆了《上下文继承书》（468 行 → 主索引 + 5 分册）。**一次性脚本在 `temp/` 里，下次没人找得到**
（审计也点名过"证据与工具脚本该在 `tools/`"）——所以把它固化到这里。

## 用法

```bash
# 先干跑，看会切出哪些分册、各多少行
python tools/fix_doc_split.py --src design-docs/某大文档.md --out-dir design-docs/model \
    --index-line "| {name} | {title} |" --dry-run

# 真拆（自带备份；主文件**不会**被清空，只把切出来的段落写成分册，并由你决定主文件怎么改）
python tools/fix_doc_split.py --src ... --out-dir ... --index-line "..." --write-index
```

## 它做什么 / 不做什么

- **做**：按 `## ` 一级标题切片；每个分册头部写"摘自哪份、返回索引"的链接；生成一段索引表
  （表格行模板由 `--index-line` 给，`{name}` = 文件名、`{title}` = 标题、`{lines}` = 行数）。
- **不做**：**不动主文件正文**（怎么改主文件是人的决定：留哪几段、写什么五分钟版）。
  主文件改法请参考 `design-docs/项目完整模型.md` 的现状（五分钟版 + 分册索引 + 维护约定）。

## 与守卫的关系

拆完请让 `agent_community/tests/test_model_docs.py` 过：主文件 ≤200 行、每分册 ≤400 行、
分册不许成孤儿（每个都要被主文件引用）、分册里不许出现历史污染标记。
"""
from __future__ import annotations

import argparse
import io
import re
import shutil
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def split_sections(lines: list) -> list:
    """按 `## ` 标题切片 → [(标题, 起行(0-based), 止行(0-based 不含))]。"""
    heads = [(i, l.strip()) for i, l in enumerate(lines) if re.match(r"^## ", l)]
    out = []
    for idx, (start, title) in enumerate(heads):
        end = heads[idx + 1][0] if idx + 1 < len(heads) else len(lines)
        out.append((title, start, end))
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="把大文档按一级标题拆成分册 + 生成索引表")
    ap.add_argument("--src", required=True, help="源文档（相对仓库根或绝对路径）")
    ap.add_argument("--out-dir", required=True, help="分册输出目录（如 design-docs/model）")
    ap.add_argument("--prefix", default="", help="分册文件名前缀（默认按序号 01_ 02_ …）")
    ap.add_argument("--index-line", default="| [{name}]({path}) | {title} | {lines} 行 |",
                    help="索引表格行模板，可用 {name} {title} {lines} {path}")
    ap.add_argument("--dry-run", action="store_true", help="只打印计划，不落盘")
    ap.add_argument("--write-index", action="store_true", help="把索引表打印到 stdout（便于粘进主文件）")
    args = ap.parse_args(argv)

    root = Path(__file__).resolve().parent.parent
    src = Path(args.src)
    if not src.is_absolute():
        src = root / src
    assert src.exists(), "源文档不存在：%s" % src
    out_dir = Path(args.out_dir)
    if not out_dir.is_absolute():
        out_dir = root / out_dir

    lines = io.open(src, encoding="utf-8").read().splitlines()
    sections = split_sections(lines)
    if not sections:
        print("[FAIL] 没找到任何 `## ` 标题，无从切分")
        return 1

    if not args.dry_run:
        bak = src.with_name(src.name + ".bak_split_%s" % time.strftime("%Y%m%d_%H%M%S"))
        shutil.copy2(src, bak)
        out_dir.mkdir(parents=True, exist_ok=True)
        print("备份：", bak.name)

    table = []
    print("源：%s（%d 行，%d 个一级标题）" % (src.name, len(lines), len(sections)))
    for i, (title, start, end) in enumerate(sections, 1):
        body = "\n".join(lines[start:end]).rstrip() + "\n"
        clean = re.sub(r"^##\s+", "", title).strip()
        # 文件名：去掉"0. "这种序号与首尾标点，中文保留（第一版留下 `…干活_.md` 的尾巴，难看）
        slug = re.sub(r"^\d+\s*[.、)]\s*", "", clean)
        slug = re.sub(r"[^\w\u4e00-\u9fff]+", "_", slug).strip("_")[:40] or ("part%02d" % i)
        fname = "%s%02d_%s.md" % (args.prefix, i, slug)
        row = args.index_line.format(name=fname.replace(".md", ""), title=clean,
                                     lines=len(body.splitlines()),
                                     path="%s/%s" % (out_dir.relative_to(root).as_posix(), fname))
        table.append(row)
        print("  %-46s %4d 行  ← %s" % (fname, len(body.splitlines()), clean))
        if args.dry_run:
            continue
        head = ("# %s\n\n> 摘自《[%s](%s)》· 拆自 %s（老板：上下文过长要分文件做索引；"
                "**只放当前口径，不记历史**）。\n> 历史沿革看 `CHANGELOG.md`。\n\n"
                % (clean, src.name, "../" + src.name if out_dir != src.parent else src.name,
                   time.strftime("%Y-%m-%d")))
        (out_dir / fname).write_text(head + body, encoding="utf-8")

    if args.write_index:
        print("\n--- 索引表（粘进主文件）---")
        print("\n".join(table))
    if args.dry_run:
        print("\n[dry-run] 未落盘。去掉 --dry-run 才写。")
    else:
        print("\n[OK] 分册已写到 %s；**主文件正文未动**，请按"
              "「五分钟版 + 分册索引 + 维护约定」自己改主文件。" % out_dir.relative_to(root))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
