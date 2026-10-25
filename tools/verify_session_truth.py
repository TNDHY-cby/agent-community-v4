#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""判据：**这条消息到底有没有落进对方自己的会话库**（骗不了人的那种）。

为什么要有它（2026-10-25 真事）：
- 平台调对方的 `session_post`，它回 `ok/in_session/message_id`；
- 平台再用对方的 `session_messages` 读回，**也找到了口令**；
- 但用户在 Cherry Studio 界面上**什么都看不到** —— 因为它读回的是**它自己的缓存**，
  真实的对话库（SQLite）里根本没有这条。

⇒ 唯一骗不过的判据：**直接读对方应用自己的数据库文件**（只读副本，绝不改人家库）。

用法：
    python tools/verify_session_truth.py --db "<对方库文件>" --needle 8E7F73
    python tools/verify_session_truth.py --db "<库>" --needle 8E7F73 --session 8d3b2a97 --last 5

退出码：0＝库里真有这句；1＝库里没有（对方在自报）；2＝库打不开/没这张表（如实说）。

⚠️ 通用性：库文件路径、表名、文本列都可传参；默认给的是本机 Cherry Studio 的结构，
   换别的应用就换参数 —— **不在代码里写死任何人的路径**。
"""
from __future__ import annotations

import argparse
import datetime
import os
import shutil
import sqlite3
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def _open_copy(db: str) -> sqlite3.Connection:
    """**只读副本**：先把库拷到临时目录再读，避免锁住正在运行的应用、也避免手滑写坏。"""
    tmp = os.path.join(tempfile.mkdtemp(), "truth.sqlite")
    shutil.copy2(db, tmp)
    return sqlite3.connect(tmp)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True, help="对方应用的本地数据库文件（SQLite）")
    ap.add_argument("--needle", required=True, help="要找的那段文字（口令/核对码）")
    ap.add_argument("--table", default="agent_session_message", help="消息表名（默认 Cherry Studio 的）")
    ap.add_argument("--text-col", default="data", help="存正文的列（有的应用是 searchable_text）")
    ap.add_argument("--time-col", default="created_at", help="时间列（毫秒或秒都行，自动判断）")
    ap.add_argument("--session-col", default="session_id", help="会话 id 列名")
    ap.add_argument("--session", default="", help="只看某个会话（可省）")
    ap.add_argument("--last", type=int, default=8, help="顺便打印最后几条（默认 8）")
    a = ap.parse_args()

    if not os.path.isfile(a.db):
        print("[FAIL] 找不到数据库文件：%s" % a.db)
        return 2
    try:
        con = _open_copy(a.db)
    except Exception as e:                                     # noqa: BLE001 — 打不开要如实说
        print("[FAIL] 打不开数据库：%s: %s" % (type(e).__name__, e))
        return 2

    def _ts(v) -> str:
        try:
            n = float(v)
        except Exception:                                      # noqa: BLE001
            return str(v)
        if n > 1e11:                                            # 毫秒
            n /= 1000.0
        return datetime.datetime.fromtimestamp(n).strftime("%m-%d %H:%M:%S")

    where, args = [f"{a.text_col} LIKE ?"], ["%" + a.needle + "%"]
    if a.session:
        where.append(f"{a.session_col} = ?")
        args.append(a.session)
    sql = ("select %s, %s, %s from %s where %s order by %s desc"
           % (a.session_col, a.time_col, a.text_col, a.table, " and ".join(where), a.time_col))
    try:
        rows = list(con.execute(sql, args))
    except Exception as e:                                     # noqa: BLE001 — 表/列不对要如实说
        print("[FAIL] 查不了（表 %s / 列 %s 可能不对）：%s: %s" % (a.table, a.text_col, type(e).__name__, e))
        return 2

    print("=" * 78)
    print("真相核对：在 %s 里找 %r" % (os.path.basename(a.db), a.needle))
    print("=" * 78)
    if rows:
        print("[OK] 库里有这条，共 %d 条：" % len(rows))
        for sid, ts, txt in rows[:5]:
            print("   %s | 会话 %s | %s" % (_ts(ts), str(sid)[:8], str(txt)[:90].replace("\n", " ")))
    else:
        print("[FAIL] 库里**没有**这句 —— 对方的“写进去了/读回也对上了”是它自己的缓存，不是真实会话。")

    try:
        tail_sql = ("select %s, %s, %s from %s%s order by %s desc limit %d"
                    % (a.session_col, a.time_col, a.text_col, a.table,
                       (" where " + a.session_col + " = ?") if a.session else "",
                       a.time_col, max(1, a.last)))
        tail = list(con.execute(tail_sql, ([a.session] if a.session else [])))
    except Exception:                                          # noqa: BLE001 — 打不出来不影响结论
        tail = []
    if tail:
        print("\n该库里最后 %d 条（真·界面来源）：" % len(tail))
        for sid, ts, txt in tail:
            print("   %s | %s | %s" % (_ts(ts), str(sid)[:8], str(txt)[:80].replace("\n", " ")))
    return 0 if rows else 1


if __name__ == "__main__":
    raise SystemExit(main())
