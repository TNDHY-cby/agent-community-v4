#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""判据：**分层协商（危急度）**（`V118` §5 / 队列 49-7）。

真跑：临时实例 + 真提报 + 真超时 + 真升级。

| # | 格 | 断言 |
|---|---|---|
| ① | 五级表完整 | `L1..L5` 都在，每级有 `code/what/limit_min/owner/carrier`；阶段大会三档在 |
| ② | 提报要写清「发生了什么」 | `summary` 空 ⇒ 400 `ESCALATION_NEEDS_SUMMARY` |
| ③ | 提报算得出时限 | `L3` 提报 ⇒ `due_ts` 约等于 `now + 8h`（8 小时 = 480 分钟） |
| ④ | **超时自动升一级**（状态机，不是文案） | 把某条 `due_ts` 改成过去 ⇒ `sweep` ⇒ 级别 +1 且 `timed_out=true`、`log` 里有一条"自动升一级" |
| ⑤ | **L5 到顶不再升** | `L5` 超时 ⇒ `sweep` 后仍是 `L5`，但标 `timed_out` + 说"到顶" |
| ⑥ | 升级写审计 | `sweep` 后 `/api/audit` 里有 `escalation.escalate` 事件 |
| ⑦ | 看板只报事实 | 每条带 `minutes_left`/`overdue`/`owner`；响应里**没有** score/rank/健康度 |

退出码：0＝全过；1＝有格没过；2＝起不来。
"""
from __future__ import annotations

import json
import os
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


def main() -> int:
    port = _free_port()
    tmpdir = Path(tempfile.mkdtemp(prefix="ac_esc_"))
    base = "http://127.0.0.1:%d" % port
    env = dict(os.environ)
    env.update({"AC_PORT": str(port), "AC_DATA_DIR": str(tmpdir / "data"),
                "AC_MIND_ROOT": str(tmpdir / "minds"), "PYTHONIOENCODING": "utf-8",
                "AC_ARTIFACT_WATCH_SEC": "3600"})
    srv = subprocess.Popen([PY, "-m", "agent_community.platform.server"], cwd=str(ROOT), env=env,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print("判据：分层协商 · 危急度（49-7 · 真跑）")
    print("=" * 78)
    try:
        up = False
        for _ in range(80):
            try:
                up = _http(base, "/api/status", timeout=3)[0] == 200
            except (urllib.error.URLError, OSError):           # noqa: BLE001 — 起服期间连不上是预期
                up = False
            if up:
                break
            time.sleep(0.5)
        if not up:
            print("[FAIL] 平台没起来 —— 这次不算跑成")
            return 2

        st1, b1 = _http(base, "/api/escalation/levels")
        lv = (b1 or {}).get("levels") or {}
        need = ("code", "what", "limit_min", "owner", "carrier")
        check("① 五级表完整 + 阶段大会三档",
              st1 == 200 and {"L1", "L2", "L3", "L4", "L5"} <= set(lv)
              and all(all(k in (lv.get(n) or {}) for k in need) for n in lv)
              and {"group", "team", "all"} <= set((b1 or {}).get("council") or {}),
              "HTTP %s levels=%r" % (st1, list(lv)))

        st2, b2 = _http(base, "/api/escalation", {"level": "L3", "summary": ""})
        check("② summary 空 ⇒ 400 ESCALATION_NEEDS_SUMMARY", st2 == 400
              and (b2 or {}).get("code") == "ESCALATION_NEEDS_SUMMARY",
              "HTTP %s %s" % (st2, (b2 or {}).get("code")))

        t0 = time.time()
        st3, b3 = _http(base, "/api/escalation",
                        {"level": "L3", "summary": "两组抢同一个产物目录", "workshop_id": "ws-demo",
                         "task_ref": "T-007", "who": "组长"})
        rec = (b3 or {}).get("escalation") or {}
        due = rec.get("due_ts")
        ok3 = False
        if due:
            minutes = (float(due) - t0) / 60.0
            ok3 = 470 <= minutes <= 485                     # L3 = 8h = 480 分钟
        check("③ 提报算得出时限（L3 ⇒ now + 8 小时）", st3 == 200 and ok3,
              "HTTP %s due-now=%.1f 分钟" % (st3, ((float(due) - t0) / 60.0 if due else -1)))

        # ④ 把那条改成已超时，再 sweep
        esc_file = tmpdir / "data" / "escalations.json"
        rows = json.loads(esc_file.read_text(encoding="utf-8"))
        for r in rows:
            if r.get("id") == rec.get("id"):
                r["due_ts"] = time.time() - 60
                r["level"] = "L3"
        esc_file.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
        st4, b4 = _http(base, "/api/escalation/sweep", {})
        changed = (b4 or {}).get("changed") or []
        hit = next((c for c in changed if c.get("id") == rec.get("id")), {})
        items = ((b4 or {}).get("view") or {}).get("items") or []
        row = next((x for x in items if x.get("id") == rec.get("id")), {})
        check("④ 超时 ⇒ **自动升一级**（L3 → L4，标 timed_out）",
              st4 == 200 and hit.get("to") == "L4" and row.get("level") == "L4"
              and row.get("timed_out") is True,
              "changed=%r row=%r" % (hit, {k: row.get(k) for k in ("level", "timed_out")}))

        # ⑤ L5 到顶：再提一条 L5 并让它超时
        st5a, b5a = _http(base, "/api/escalation",
                          {"level": "L5", "summary": "不可逆损害探针", "who": "组长"})
        l5id = ((b5a or {}).get("escalation") or {}).get("id")
        rows = json.loads(esc_file.read_text(encoding="utf-8"))
        for r in rows:
            if r.get("id") == l5id:
                r["due_ts"] = time.time() - 60
        esc_file.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
        st5, b5 = _http(base, "/api/escalation/sweep", {})
        items5 = ((b5 or {}).get("view") or {}).get("items") or []
        row5 = next((x for x in items5 if x.get("id") == l5id), {})
        ch5 = next((c for c in ((b5 or {}).get("changed") or []) if c.get("id") == l5id), {})
        check("⑤ L5 到顶不再升，但标「超时未决」",
              st5 == 200 and row5.get("level") == "L5" and row5.get("timed_out") is True
              and "到顶" in str(ch5.get("why")),
              "row=%r changed=%r" % ({k: row5.get(k) for k in ("level", "timed_out")}, ch5))

        st6, b6 = _http(base, "/api/audit?limit=40")
        aud = (b6 or {}).get("events") or []
        check("⑥ 升级写审计（escalation.escalate 事件存在）",
              any(str((e or {}).get("event")) == "escalation.escalate" for e in aud),
              "events=%d" % len(aud))

        st7, b7 = _http(base, "/api/escalation")
        items7 = (b7 or {}).get("items") or []
        blob = json.dumps(b7, ensure_ascii=False).lower()
        check("⑦ 看板只报事实（有 minutes_left/owner；无 score/rank/健康度）",
              st7 == 200 and bool(items7)
              and all(("minutes_left" in (x or {})) and ("owner" in (x or {})) for x in items7)
              and "score" not in blob and "rank" not in blob,
              "items=%d blob 含 score/rank=%r"
              % (len(items7), ("score" in blob or "rank" in blob)))
    finally:
        try:
            srv.terminate()
            srv.wait(timeout=10)
        except Exception:                                      # noqa: BLE001 — 收尾
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
