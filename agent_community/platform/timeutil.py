# -*- coding: utf-8 -*-
"""`platform/timeutil`：**时间戳一律带时区**（一处真相）。

## 为什么（`开发任务队列.md` F-1，实测缺陷）

`audit.json` 里的 `2026-11-08T16:28:00` **不带时区**。2026-11-09 查「工作间 20 → 19」时，
只能靠交叉比对（前端实测 23:40、我复核 23:20）才推断它写的是本地时间 ——
**时间戳不能当证据，排障就得靠猜**。`V141` §3.2 把"日志"列进须根层，须根层的基本要求就是
"能被人当事实读"，所以这条必须修。

## 口径（三条，写死在这里，别处不许自己拼）

1. **写**：`now_iso()` ⇒ 本地时间 + 偏移（`2026-11-08T16:28:00+08:00`）；
2. **读**：老记录没有偏移 ⇒ `parse_ts()` 按**本机时区**解释，并**如实标** `tz_known=False`
   （不假装知道，也不抛错）；
3. **给人看**：`describe()` 明说时区；未知时区时明说"时区未知，按本机时区解释"。
"""
from __future__ import annotations

from datetime import datetime, timezone


def now_iso(timespec: str = "seconds") -> str:
    """当前本地时间，**带时区偏移**（`2026-11-08T16:28:00+08:00`）。"""
    return datetime.now().astimezone().isoformat(timespec=timespec)


def parse_ts(value) -> tuple[datetime | None, bool]:
    """解析时间戳 ⇒ `(datetime, tz_known)`。

    - 带 `+08:00` / `Z` ⇒ `tz_known=True`；
    - **不带时区**（历史记录）⇒ 按本机时区补上，`tz_known=False`（如实说不确定）；
    - 解析不了 ⇒ `(None, False)`（不抛错：审计是日志，不能因为一条坏记录让查询崩掉）。
    """
    if value is None:
        return None, False
    s = str(value).strip()
    if not s:
        return None, False
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S", "%Y-%m-%d %H:%M"):
            try:
                dt = datetime.strptime(s, fmt)
                break
            except ValueError:
                continue
        else:
            return None, False
    if dt.tzinfo is None:
        # 老记录：按本机时区解释（**标 tz_known=False**，别装知道）
        return dt.astimezone(), False
    return dt, True


def describe(value) -> str:
    """给人看的一句话（带时区；未知时说清是"按本机时区解释"）。"""
    dt, known = parse_ts(value)
    if dt is None:
        return "时间未知（解析不了：%r）" % (value,)
    off = dt.strftime("%z")
    off = (off[:3] + ":" + off[3:]) if len(off) == 5 else off
    base = dt.strftime("%Y-%m-%d %H:%M:%S")
    if known:
        return "%s %s（UTC%s）" % (base, off, off)
    return "%s（**时区未知**，按本机时区解释 ⇒ 老记录，不能当精确时刻用）" % base


def to_utc_iso(value) -> str:
    """统一到 UTC ISO（跨机对比时用这个，别用本地时间比）。解析不了返回空串。"""
    dt, _known = parse_ts(value)
    if dt is None:
        return ""
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")
