# -*- coding: utf-8 -*-
"""`platform/session_truth.py`：**真相核对**的**一处真相**（`V102`）。

## 它回答的那个问题

外部 agent 说"我写进你的会话里了"时，**怎么才算真的写进去了**？

2026-10-25 的真事（`V101`）：平台调它的 `session_post`，它每次回 `ok:true / in_session:true / message_id`（每次还都不一样），
平台再用它自己的 `session_messages` 读回、**也找到了口令** —— 结果用户在界面上什么都没有。
去读它自己的 SQLite 才发现 12:58 之后一条都没落库。

⇒ 唯一骗不过的判据：**直接读对方应用自己的存储文件**（只读副本，绝不改人家库）。

## 为什么放在 `platform/` 而不是只留在 `tools/`

界面要用它（「测一次」后自动核对、绿勾），`tools/` 里的脚本是给人手敲的。
一份逻辑放两处 ⇒ 迟早两套口径（规则 20）⇒ 这里做真身，`tools/verify_session_truth.py` 只是它的一层 CLI。

## 三档结论（与 CLI 退出码同义）

| code | 意思 | 不许说成 |
|---|---|---|
| 0 | 库里**有**这句话 | — |
| 1 | 库里**没有**这句话（对方在自报/写到别处） | "核对失败"（那是 2） |
| 2 | **核对不了**（无文件/非 SQLite/表列不对/库被锁） | "库里没有"（那是 1） |

## 可移植性 / 反私人定制（`PRODUCTIZATION.md`）

- 库路径、表名、正文列、时间列、会话列**全部入参**，默认值只是"常见 SQLite 结构"；
- 不写死任何盘符、用户名、应用名；
- 永远读**拷贝**：原库 + `-wal` + `-shm` 三个一起拷到临时目录（WAL 教训见 `_open_copy`）。
"""
from __future__ import annotations

import datetime
import os
import re
import shutil
import sqlite3
import tempfile
import time

#: 默认结构：**常见 SQLite 消息表的形状**（不是某个应用专属；四个名字都能被入参覆盖）
DEFAULTS = {
    "table": "agent_session_message",
    "text_col": "data",
    "time_col": "created_at",
    "session_col": "session_id",
}

#: 口令的形状（`_make_nonce()` 造出来的样子 + 常见写法）：找不到时用它把库里最近的口令捞出来
_NONCE_RE = re.compile(r"(?:收到-|AC-|nonce[：:=]?\s*)[0-9A-Za-z]{4,}")


def _fmt_ts(v) -> str:
    """把毫秒/秒时间戳（或字符串）显示成人能看的时间；认不出来就原样。"""
    try:
        n = float(v)
    except (TypeError, ValueError):
        return str(v)
    if n > 1e11:                                   # 毫秒（Cherry Studio 这类）
        n /= 1000.0
    try:
        return datetime.datetime.fromtimestamp(n).strftime("%m-%d %H:%M:%S")
    except (OverflowError, OSError, ValueError):    # 时间戳坏了不该让整次核对失败
        return str(v)


def _open_copy(db: str) -> sqlite3.Connection:
    """**只读副本**：把库拷到临时目录再读 —— 不锁正在运行的应用，也不会手滑写坏人家库。

    ⚠️⚠️ 2026-10-25 用真实教训换的：SQLite 开了 WAL 时**新写入先进 `库-wal`**，主库文件几十分钟不动。
    只拷 `.sqlite` 会读到**旧快照** ⇒ 把"它写进去了"判成"没写"（假阴性）。
    ⇒ 必须把 `库` + `库-wal` + `库-shm` **三个一起拷**。
    """
    tmpdir = tempfile.mkdtemp(prefix="ac_truth_")
    base = os.path.basename(db)
    dst = os.path.join(tmpdir, base)
    shutil.copy2(db, dst)
    for suffix in ("-wal", "-shm"):
        side = db + suffix
        if os.path.exists(side):
            try:
                shutil.copy2(side, dst + suffix)
            except OSError:            # noqa: BLE001 — 有意静默：-shm 只是索引，拷不到通常仍能读（由 -wal 决定成败）
                # 有意静默：`-shm` 只是索引，拷不到通常仍能读（由 `-wal` 决定成败）；
                # 真读不出来会在 `check()` 的查询阶段如实报 code=2，不会变成"库里没有"。
                pass
    return sqlite3.connect(dst)


def _close_copy(con: sqlite3.Connection) -> None:
    """关连接并删临时目录（拷贝是为了不碰人家库，读完全部清掉）。"""
    path = ""
    try:
        row = con.execute("pragma database_list").fetchone()
        path = str(row[2] or "") if row else ""
    except sqlite3.Error:               # noqa: BLE001 — 有意静默：拿不到路径就少删一个临时文件
        path = ""
    try:
        con.close()
    except sqlite3.Error:               # noqa: BLE001 — 有意静默：连接已坏，关不了不影响结论
        pass
    if path:
        tmpdir = os.path.dirname(path)
        if os.path.basename(tmpdir).startswith("ac_truth_"):
            shutil.rmtree(tmpdir, ignore_errors=True)


def probe(db: str, *, table: str = "", text_col: str = "", time_col: str = "",
          session_col: str = "") -> dict:
    """**保存前先真打开一次**：文件在不在、是不是 SQLite、表/列在不在。

    返回 `{ok, why, tables, columns, rows, last_at}`；不对时 `why` 要能照着改
    （把库里**实际**有的表/列列出来，别让人猜）。
    """
    t = (table or DEFAULTS["table"]).strip()
    tc = (text_col or DEFAULTS["text_col"]).strip()
    tmc = (time_col or DEFAULTS["time_col"]).strip()
    sc = (session_col or DEFAULTS["session_col"]).strip()
    out = {"ok": False, "why": "", "db": db, "tables": [], "columns": [],
           "rows": None, "last_at": "", "table": t, "text_col": tc,
           "time_col": tmc, "session_col": sc}
    if not db:
        out["why"] = "没给库文件路径"
        return out
    if not os.path.isfile(db):
        out["why"] = "找不到这个文件：%s" % db
        return out
    try:
        con = _open_copy(db)
    except (OSError, sqlite3.Error) as e:
        out["why"] = "打不开这个库：%s: %s" % (type(e).__name__, e)
        return out
    try:
        try:
            out["tables"] = [str(r[0]) for r in con.execute(
                "select name from sqlite_master where type='table' order by name")]
        except sqlite3.Error as e:
            out["why"] = ("这不是一个能读的 SQLite 库：%s: %s（平台只支持 SQLite；"
                          "其它存储形态如实不支持）" % (type(e).__name__, e))
            return out
        if t not in out["tables"]:
            out["why"] = ("这张表不在这库里：%s（库里有：%s）"
                          % (t, "、".join(out["tables"][:12]) or "（一张表都没有）"))
            return out
        try:
            out["columns"] = [str(r[1]) for r in con.execute("pragma table_info(%s)" % t)]
        except sqlite3.Error as e:
            out["why"] = "读不出这张表的列：%s: %s" % (type(e).__name__, e)
            return out
        # 正文列 + 时间列**必须有**（核对靠它们）；会话列缺了也能核对，只是不能按会话挑
        missing = [c for c in (tc, tmc) if c not in out["columns"]]
        if missing:
            out["why"] = ("这些列不在这张表里：%s（这张表有：%s）"
                          % ("、".join(missing), "、".join(out["columns"])))
            return out
        out["session_col_ok"] = sc in out["columns"]
        try:
            out["rows"] = int(con.execute("select count(1) from %s" % t).fetchone()[0])
            row = con.execute("select %s from %s order by %s desc limit 1" % (tmc, t, tmc)).fetchone()
            out["last_at"] = _fmt_ts(row[0]) if row else ""
        except sqlite3.Error as e:
            out["why"] = "查不了这张表：%s: %s" % (type(e).__name__, e)
            return out
        out["ok"] = True
        out["why"] = "能读到：%d 条消息%s%s" % (
            out["rows"], ("，最近一条 %s" % out["last_at"]) if out["last_at"] else "",
            "" if out["session_col_ok"] else "（注意：这张表没有 %s 列，核对时不能按会话筛）" % sc)
        return out
    finally:
        _close_copy(con)


def recent_app_messages(db: str, *, since_ms: int = 0, session: str = "",
                        limit: int = 3, table: str = "", text_col: str = "",
                        time_col: str = "", session_col: str = "") -> list:
    """**它自己产出的消息**（不是插件代写的）—— 判"能不能回答"的真凭据（2026-10-25 实测）。

    同一个库里两种行长得不一样：
      · **插件代写**的行：`data` 里只有 `parts`，`model_id` / `stats` 都是空；
      · **应用自己生成**的行：`model_id`（如 `厂商::模型`）与 `stats`（token 计数）**都有值**。
    ⇒ 库里出现了"带 model_id / stats 的新消息"，才算**那个应用真的跑了一轮并说话了**。
    """
    t = (table or DEFAULTS["table"]).strip()
    tc = (text_col or DEFAULTS["text_col"]).strip()
    tmc = (time_col or DEFAULTS["time_col"]).strip()
    sc = (session_col or DEFAULTS["session_col"]).strip()
    try:
        con = _open_copy(db)
    except (OSError, sqlite3.Error):                       # noqa: BLE001 — 有意静默：调用方会拿到空表并如实说"没回答"
        return []
    try:
        cols = [str(r[1]) for r in con.execute("pragma table_info(%s)" % t)]
        if "model_id" not in cols:                         # 没有这列就分不出是谁写的 ⇒ 如实返回空
            return []
        where = ["model_id is not null", "model_id <> ''"]
        args: list = []
        if session and sc in cols:
            where.append("%s = ?" % sc)
            args.append(session)
        if since_ms:
            where.append("%s >= ?" % tmc)
            args.append(int(since_ms))
        sql = ("select %s, %s, %s from %s where %s order by %s desc limit %d"
               % (sc if sc in cols else "''", tmc, tc, t, " and ".join(where), tmc,
                  max(1, int(limit))))
        rows = list(con.execute(sql, args))
        return [{"at": _fmt_ts(ts), "at_ms": int(ts or 0), "session_id": str(sid or ""),
                 "text": str(txt or "")[:600]} for sid, ts, txt in rows]
    except sqlite3.Error:                                  # noqa: BLE001 — 查不了就当没查到（调用方据此说没回答）
        return []
    finally:
        _close_copy(con)


def check(db: str, needle: str, *, table: str = "", text_col: str = "", time_col: str = "",
          session_col: str = "", session: str = "", last: int = 8) -> dict:
    """**核对**：这个库里到底有没有 `needle` 这句话。

    `code`：0=有 / 1=没有 / 2=核对不了（见模块 docstring 的三档表）。
    找不到时额外给 `recent_codes`（库里最近的口令）—— 界面可能把口令**截断显示**，这是 2026-10-25 踩过的坑。
    """
    t = (table or DEFAULTS["table"]).strip()
    tc = (text_col or DEFAULTS["text_col"]).strip()
    tmc = (time_col or DEFAULTS["time_col"]).strip()
    sc = (session_col or DEFAULTS["session_col"]).strip()
    started = time.time()
    res = {"ok": False, "code": 2, "found": False, "db": db, "needle": needle,
           "table": t, "text_col": tc, "matches": [], "tail": [], "recent_codes": [],
           "why": "", "verdict": "", "elapsed_ms": 0}
    if not needle:
        res["why"] = "没有要核对的口令（先点「测一次」拿到口令）"
        res["verdict"] = res["why"]
        res["elapsed_ms"] = int((time.time() - started) * 1000)
        return res
    p = probe(db, table=t, text_col=tc, time_col=tmc, session_col=sc)
    if not p.get("ok"):
        res["why"] = str(p.get("why") or "核对不了")
        res["verdict"] = "核对不了：%s" % res["why"]
        res["tables"] = p.get("tables") or []
        res["elapsed_ms"] = int((time.time() - started) * 1000)
        return res
    # 表里没有会话列时：不按会话筛、也不假装有 id（选择列换成常量空串）
    sc_ok = bool(p.get("session_col_ok", True))
    if not sc_ok:
        session = ""
    sid_expr = sc if sc_ok else "'' as sid"
    try:
        con = _open_copy(db)
    except (OSError, sqlite3.Error) as e:
        res["why"] = "打不开这个库：%s: %s" % (type(e).__name__, e)
        res["verdict"] = "核对不了：%s" % res["why"]
        res["elapsed_ms"] = int((time.time() - started) * 1000)
        return res
    try:
        where, args = ["%s LIKE ?" % tc], ["%" + needle + "%"]
        if session:
            where.append("%s = ?" % sc)
            args.append(session)
        sql = ("select %s, %s, %s from %s where %s order by %s desc"
               % (sid_expr, tmc, tc, t, " and ".join(where), tmc))
        try:
            rows = list(con.execute(sql, args))
        except sqlite3.Error as e:
            res["why"] = "查不了（表 %s / 列 %s 可能不对）：%s: %s" % (t, tc, type(e).__name__, e)
            res["verdict"] = "核对不了：%s" % res["why"]
            res["elapsed_ms"] = int((time.time() - started) * 1000)
            return res
        res["found"] = bool(rows)
        for sid, ts, txt in rows[:5]:
            res["matches"].append({"at": _fmt_ts(ts), "session_id": str(sid or ""),
                                   "excerpt": str(txt or "")[:200].replace("\n", " ")})
        # 最近几条（真·界面来源）+ 找不到时把最近的口令捞出来（防口令被截断误判）
        try:
            tail_sql = ("select %s, %s, %s from %s%s order by %s desc limit %d"
                        % (sid_expr, tmc, tc, t, (" where %s = ?" % sc) if session else "",
                           tmc, max(1, int(last))))
            tail = list(con.execute(tail_sql, ([session] if session else [])))
            for sid, ts, txt in tail:
                res["tail"].append({"at": _fmt_ts(ts), "session_id": str(sid or ""),
                                    "excerpt": str(txt or "")[:160].replace("\n", " ")})
            if not res["found"]:
                codes, seen = [], set()
                for _sid, ts, txt in tail[:12]:
                    for m in _NONCE_RE.finditer(str(txt or "")):
                        code = m.group(0)
                        if code not in seen:
                            seen.add(code)
                            codes.append({"code": code, "at": _fmt_ts(ts)})
                res["recent_codes"] = codes[:6]
        except sqlite3.Error:                    # noqa: BLE001 — 有意静默：只是附加提示
            # 有意静默：这只是"帮你判断口令是不是被截断"的附加提示，
            # 主结论（found/why）已经算好了，不该因为它把结论变成 code=2。
            pass
        if res["found"]:
            res["code"] = 0
            res["ok"] = True
            res["why"] = "库里真有这句（%d 条）" % len(res["matches"])
            res["verdict"] = "这句话真在它的库里"
        else:
            res["code"] = 1
            res["why"] = "库里没有这句"
            res["verdict"] = ("库里没有「%s」这句 —— 它只是「说」发出去了"
                              "（也可能写到了别的应用/别的库）" % needle)
        res["elapsed_ms"] = int((time.time() - started) * 1000)
        return res
    finally:
        _close_copy(con)


def describe_for_user(res: dict) -> dict:
    """把一次核对结果压成**界面照抄的三态**（`state` = ok / missing / unknown）。

    `note` **自带图标**（`✓` / `✗` / `?`）—— 界面直接显示这一句，不必再拼一次（一处真相）。
    """
    code = int((res or {}).get("code", 2))
    state = {0: "ok", 1: "missing"}.get(code, "unknown")
    icon = {"ok": "✓", "missing": "✗", "unknown": "?"}[state]
    note = {
        "ok": "真相核对通过：这句话真在它的库里",
        "missing": "真相核对没过：库里没有这句 —— 它只是「说」发出去了",
        "unknown": "核对不了：%s" % ((res or {}).get("why") or "原因不明"),
    }[state]
    return {"state": state, "icon": icon, "ok": state == "ok",
            "note": "%s %s" % (icon, note),
            "db": (res or {}).get("db", ""), "needle": (res or {}).get("needle", "")}
