# -*- coding: utf-8 -*-
"""tools/gen_entry.py —— 生成桌面上那**两个入口文件**（V72）。

## 为什么生成而不是手写

`启动合作社.vbs` / `启动合作社.cmd` 必须是 **GBK + CRLF**（wscript 与 cmd 都按系统 ANSI
代码页读脚本；存成 UTF-8，中文提示就变乱码——本项目已被这条咬过多次）。
而"编辑器里好改、好审"的是 UTF-8 模板。⇒ **模板进仓库（UTF-8），生成物也进仓库（GBK）**，
来源唯一：改模板 → 跑本脚本 → 生成物跟着变。

    python tools/gen_entry.py            # 生成/更新两个入口
    python tools/gen_entry.py --check    # 只校验：生成物与模板一致（不一致退 1）

两个入口：
  · `启动合作社.vbs`  ← `tools/launcher/entry.vbs.tmpl`      （桌面快捷方式指向它，**无窗口**）
  · `启动合作社.cmd`  ← `tools/launcher/entry-console.cmd.tmpl`（有输出，排障用）
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

REPO = Path(__file__).resolve().parents[1]
TMPL_DIR = REPO / "tools" / "launcher"

#: 生成物 → 模板
PAIRS = (
    ("启动合作社.vbs", "entry.vbs.tmpl"),
    ("启动合作社.cmd", "entry-console.cmd.tmpl"),
)


def render(tmpl: Path) -> bytes:
    """模板（UTF-8）→ 生成物字节（**GBK + CRLF**）。不写 BOM：BOM 在 cmd/vbs 里会变可见字符。"""
    text = tmpl.read_text(encoding="utf-8")
    text = text.replace("\r\n", "\n").replace("\n", "\r\n")
    return text.encode("gbk", errors="replace")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="生成启动入口（GBK+CRLF）")
    ap.add_argument("--check", action="store_true", help="只校验生成物是否与模板一致")
    args = ap.parse_args(argv)

    bad = 0
    for out_name, tmpl_name in PAIRS:
        tmpl = TMPL_DIR / tmpl_name
        out = REPO / out_name
        if not tmpl.exists():
            print("✗ 缺模板：%s" % tmpl)
            bad += 1
            continue
        want = render(tmpl)
        if args.check:
            got = out.read_bytes() if out.exists() else b""
            if got != want:
                print("✗ %s 与模板不一致（跑一次 `python tools/gen_entry.py`）" % out_name)
                bad += 1
            else:
                print("✓ %s 与模板一致（%d 字节）" % (out_name, len(got)))
            continue
        out.write_bytes(want)
        print("写入 %s（%d 字节，GBK/CRLF）" % (out_name, len(want)))
    return 0 if not bad else 1


if __name__ == "__main__":
    raise SystemExit(main())
