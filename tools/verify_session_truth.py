#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""判据：**这条消息到底有没有落进对方自己的会话库**（骗不了人的那种）。

为什么要有它（2026-10-25 真事）：
- 平台调对方的 `session_post`，它回 `ok/in_session/message_id`；
- 平台再用对方的 `session_messages` 读回，**也找到了口令**；
- 但用户在界面上**什么都看不到** —— 因为它读回的是**它自己的缓存**，真实对话库里根本没有这条。

⇒ 唯一骗不过的判据：**直接读对方应用自己的数据库文件**（只读副本，绝不改人家库）。

**一处真相**：逻辑在 `agent_community/platform/session_truth.py`
（对接中心「测一次」之后**自动**跑的是同一份）。本文件只是它的命令行外壳 ——
两边结论不一致就是 bug，别在这儿再写第二套 sqlite 逻辑。

用法：
    python tools/verify_session_truth.py --db "<对方库文件>" --needle 收到-8E7F73
    python tools/verify_session_truth.py --db "<库>" --needle 收到-8E7F73 --session 8d3b2a97 --last 5

退出码：0＝库里真有这句；1＝库里没有（对方在自报）；2＝库打不开/没这张表（如实说）。

⚠️ 通用性：库路径、表名、几个列名都可传参；默认值是**常见 SQLite 消息表结构**（不是某个应用专属），
   换别的应用就换参数 —— **不在代码里写死任何人的路径**。
"""
from __future__ import annotations

import argparse
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
if _ROOT not in sys.path:
    sys.path.append(_ROOT)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True, help="对方应用的本地数据库文件（SQLite）")
    ap.add_argument("--needle", required=True, help="要找的那段文字（口令/核对码）")
    ap.add_argument("--table", default="", help="消息表名（留空＝按常见结构 agent_session_message）")
    ap.add_argument("--text-col", default="", help="存正文的列（留空＝data；有的应用是 searchable_text）")
    ap.add_argument("--time-col", default="", help="时间列（留空＝created_at；毫秒或秒都行，自动判断）")
    ap.add_argument("--session-col", default="", help="会话 id 列名（留空＝session_id）")
    ap.add_argument("--session", default="", help="只看某个会话（可省）")
    ap.add_argument("--last", type=int, default=8, help="顺便打印最后几条（默认 8）")
    a = ap.parse_args()

    from agent_community.platform import session_truth as _st

    res = _st.check(a.db, a.needle, table=a.table, text_col=a.text_col,
                    time_col=a.time_col, session_col=a.session_col,
                    session=a.session, last=a.last)
    print("=" * 78)
    print("真相核对：在 %s 里找 %r" % (os.path.basename(a.db or ""), a.needle))
    print("=" * 78)
    if res["code"] == 0:
        print("[OK] 库里有这条，共 %d 条：" % len(res["matches"]))
        for m in res["matches"]:
            print("   %s | 会话 %s | %s" % (m["at"], (m["session_id"] or "")[:8], m["excerpt"][:90]))
    elif res["code"] == 1:
        print("[FAIL] 库里**没有**这句。")
        codes = res.get("recent_codes") or []
        if codes:
            # ★2026-10-25 我自己踩过：拿到的口令是**截断过的**（界面把「收到-8EF73D」显示成 8E7F73），
            #   于是把"写进去了"误判成"没写"。所以找不到时，把库里最近的口令**打出来让人对**。
            print("   ⇒ 提醒：库里最近的口令是这个（是不是你手上那个被截断了？）：")
            for c in codes:
                print("      %s  %s" % (c["code"], c["at"]))
    else:
        print("[FAIL] 核对不了：%s" % (res.get("why") or "原因不明"))
        if res.get("tables"):
            print("   库里有这些表：%s" % "、".join(res["tables"][:12]))
    if res.get("tail"):
        print("\n该库里最后 %d 条（真·界面来源）：" % len(res["tail"]))
        for t in res["tail"]:
            print("   %s | %s | %s" % (t["at"], (t["session_id"] or "")[:8], t["excerpt"][:80]))
    return int(res["code"])


if __name__ == "__main__":
    raise SystemExit(main())
