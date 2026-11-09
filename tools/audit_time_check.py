#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""判据：**审计时间戳带时区**（开发任务队列 F-1）。

## 为什么要它

`audit.json` 里的 `2026-11-08T16:28:00` **不带时区**。查「工作间 20 → 19」时，
只能靠交叉比对（前端 23:40 实测、后端 23:20 复核）才推断它写的是本地时间 ——
**时间戳不能单独当证据，排障就得靠猜**。修完要有判据盯着，不然会退回去。

| # | 格 | 断言 |
|---|---|---|
| ① | 新写的审计**带时区偏移** | 真起实例建工作间 ⇒ `GET /api/audit` 里最新一条 `ts` 形如 `…+08:00` 或 `Z` |
| ② | **老记录不炸**：无时区的历史 `ts` 仍能解析 | `parse_ts("2026-11-08T16:28:00")` ⇒ 有值且 `tz_known=False`（**如实说不确定**） |
| ③ | 跨时区**同一瞬间**能认出来 | `2026-11-08T16:28:00+08:00` 与 `2026-11-08T08:28:00+00:00` 解析后是同一 UTC 时刻 |
| ④ | 审计接口按时间倒序 | 连写两条事件 ⇒ 返回里新事件在前（真读，不信内存顺序） |
| ⑤ | 给人看的话**说清时区** | 带偏移的描述含 `UTC+08:00`；老记录明说"时区未知，按本机时区解释" |

退出码：0＝全过；1＝有格没过；2＝起不来。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))
PY = sys.executable

ROWS: list = []
TZ_RE = re.compile(r"(?:[+-]\d{2}:\d{2}|Z)$")


def check(label: str, ok: bool, detail: str = "") -> None:
    ROWS.append((label, bool(ok), detail))
    print("  %s %s%s" % ("PASS" if ok else "FAIL", label,
                        ("　（" + detail + "）") if detail and not ok else ""))


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def _http(base: str, path: str, body=None, timeout: float = 30):
    path = urllib.parse.quote(path, safe="/?=&:,.~@!$'()*+;")
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    h = {"Content-Type": "application/json"} if data else {}
    req = urllib.request.Request(base + path, data=data, headers=h,
                                 method="POST" if data is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return int(r.status), json.loads(r.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            return int(e.code), json.loads(raw or "{}")
        except ValueError:
            return int(e.code), {"raw": raw[:300]}


def _py(expr: str) -> str:
    """在仓库里真跑一小段 Python（验 `timeutil` 的**真函数结果**）。"""
    code = ("import sys; sys.path.insert(0, %r)\n"
            "from agent_community.platform import timeutil as T\n"
            "print(%s)" % (str(ROOT), expr))
    r = subprocess.run([PY, "-X", "utf8", "-c", code], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=120)
    return (r.stdout or "").strip()


def main() -> int:
    port = _free_port()
    tmpdir = tempfile.mkdtemp(prefix="ac_audit_tz_")
    base = "http://127.0.0.1:%d" % port
    env = dict(os.environ)
    env.update({"AC_PORT": str(port), "AC_DATA_DIR": tmpdir, "PYTHONIOENCODING": "utf-8",
                "AC_ARTIFACT_WATCH_SEC": "3600"})
    srv = subprocess.Popen([PY, "-m", "agent_community.platform.server"], cwd=str(ROOT), env=env,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print("判据：审计时间戳带时区（F-1 · 真跑）")
    print("=" * 78)
    try:
        up = False
        for _ in range(80):
            try:
                up = _http(base, "/api/status", timeout=3)[0] == 200
            except (urllib.error.URLError, OSError):           # noqa: BLE001 — 起服期间连不上是预期（循环重试）
                up = False
            if up:
                break
            time.sleep(0.5)
        if not up:
            print("[FAIL] 平台没起来 —— 这次不算跑成")
            return 2

        # 造两条审计：建两个工作间（每次 create 都写 workshop.create）
        ddir = Path(tmpdir)
        ws1 = ddir / "ws_tz1"
        ws2 = ddir / "ws_tz2"
        for w in (ws1, ws2):
            (w / "_lead").mkdir(parents=True, exist_ok=True)
            (w / "_lead" / "建模.md").write_text("# 建模\n", encoding="utf-8")
        _http(base, "/api/workshop", {"name": "时区判据间一（示例）", "hall_content": "x",
                                      "workshop_path": str(ws1)})
        time.sleep(1.1)                     # 跨秒，保证两条 ts 不同
        _http(base, "/api/workshop", {"name": "时区判据间二（示例）", "hall_content": "x",
                                      "workshop_path": str(ws2)})

        st1, b1 = _http(base, "/api/audit?limit=20")
        events = (b1 or {}).get("events") or (b1 if isinstance(b1, list) else [])
        creates = [e for e in events if str((e or {}).get("event")) == "workshop.create"]
        ts_list = [str((e or {}).get("ts") or "") for e in creates]
        check("① 新写的审计带时区偏移（…+08:00 或 Z）",
              st1 == 200 and bool(ts_list) and all(TZ_RE.search(t) for t in ts_list),
              "HTTP %s ts=%r" % (st1, ts_list[:3]))

        # ② 老记录（无时区）不炸，且如实标「时区未知」
        old = _py("T.parse_ts('2026-11-08T16:28:00')[1]")
        oldval = _py("T.parse_ts('2026-11-08T16:28:00')[0] is not None")
        check("② 老记录（无时区）能解析，且 tz_known=False（不假装知道）",
              old == "False" and oldval == "True", "tz_known=%r parsed=%r" % (old, oldval))

        # ③ 跨时区同一瞬间
        same = _py("T.to_utc_iso('2026-11-08T16:28:00+08:00') == "
                   "T.to_utc_iso('2026-11-08T08:28:00+00:00')")
        check("③ 跨时区同一瞬间能认出来（+08:00 与 UTC 同刻相等）", same == "True", "got=%r" % same)

        # ④ 按时间倒序（新事件在前）
        order_ok = False
        if len(ts_list) >= 2:
            from datetime import datetime as _dt
            parsed = []
            for t in ts_list:
                try:
                    parsed.append(_dt.fromisoformat(t.replace("Z", "+00:00")))
                except ValueError:
                    parsed.append(None)
            order_ok = all(parsed[i] is not None and parsed[i + 1] is not None
                           and parsed[i] >= parsed[i + 1] for i in range(len(parsed) - 1))
        check("④ 审计接口按时间倒序（新的在前，且能真的比较）", order_ok, "ts=%r" % ts_list[:3])

        # ⑤ 人看的话说清时区
        d_new = _py("T.describe('2026-11-08T16:28:00+08:00')")
        d_old = _py("T.describe('2026-11-08T16:28:00')")
        check("⑤ 描述能看出时区（带偏移含 UTC 偏移；老记录明说时区未知）",
              "UTC" in d_new and "时区未知" in d_old, "new=%r old=%r" % (d_new, d_old))
    finally:
        try:
            srv.terminate()
            srv.wait(timeout=10)
        except Exception:                                      # noqa: BLE001 — 收尾：进程没起来就杀掉
            srv.kill()
        shutil.rmtree(tmpdir, ignore_errors=True)

    print("-" * 78)
    passed = sum(1 for _l, ok, _d in ROWS if ok)
    print("合计 %d 格：PASS %d / FAIL %d" % (len(ROWS), passed, len(ROWS) - passed))
    for lb, ok, d in ROWS:
        if not ok:
            print("  FAIL:", lb, "|", str(d)[:240])
    return 0 if passed == len(ROWS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
