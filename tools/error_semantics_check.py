#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""判据：**错误码语义**（借鉴竞品 A4）—— 该重试 / 该停手 / 幂等命中。

真跑：临时实例（换端口 + 换数据目录）+ **真 HTTP 错误响应**；码表本身用**真函数调用**验
（不是查源码文本）。

| # | 格 | 断言 |
|---|---|---|
| ① | `/api/error-semantics` 给整张表 | 200；`retryable`/`terminal`/`already` 三组都在；有 `policy` 与 `how_to_use` |
| ② | 默认**停手**（fail-closed） | `retryable_of("TOTALLY_UNKNOWN")` = False |
| ③ | 幂等命中是**成功语义** | `ALREADY_DISPATCHED` ⇒ `retryable=false`；`is_already` = True；`annotate` 补 `done=true` |
| ④ | 临时状态才可重试 | `CELL_EMPTY` / `WAKEUP_FAILED` ⇒ `retryable=true` |
| ⑤ | 结构性拒绝 ⇒ 真响应 `retryable=false` | 真打"不存在任务的重派" ⇒ 404 且 `retryable=false` |
| ⑥ | 目标工作间不存在也 `retryable=false` | 真打不存在工作间 ⇒ 404/`NO_SUCH_WORKSHOP` 带 `retryable=false` |
| ⑦ | **加字段没丢人话** | ⑤⑥ 两个错误响应里 `error` 或 `next` 至少有一个非空 |

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


def _py(expr: str) -> str:
    """在仓库里真跑一小段 Python（验码表的**真函数结果**，不是查文本）。"""
    code = ("import sys; sys.path.insert(0, %r)\n"
            "from agent_community.platform import error_semantics as E\n"
            "print(%s)" % (str(ROOT), expr))
    r = subprocess.run([PY, "-X", "utf8", "-c", code], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=120)
    return (r.stdout or "").strip()


def main() -> int:
    port = _free_port()
    tmpdir = tempfile.mkdtemp(prefix="ac_esem_")
    base = "http://127.0.0.1:%d" % port
    env = dict(os.environ)
    env.update({"AC_PORT": str(port), "AC_DATA_DIR": tmpdir, "PYTHONIOENCODING": "utf-8",
                "AC_ARTIFACT_WATCH_SEC": "3600"})
    srv = subprocess.Popen([PY, "-m", "agent_community.platform.server"], cwd=str(ROOT), env=env,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print("判据：错误码语义（retryable · 借鉴 A4）")
    print("=" * 78)
    try:
        up = False
        for _ in range(80):
            try:
                st, _j = _http(base, "/api/status", timeout=3)
                up = st == 200
            except (urllib.error.URLError, OSError):
                up = False
            if up:
                break
            time.sleep(0.5)
        if not up:
            print("[FAIL] 平台没起来 —— 这次不算跑成")
            return 2

        st1, b1 = _http(base, "/api/error-semantics")
        check("① `/api/error-semantics` 给整张表",
              st1 == 200 and bool((b1 or {}).get("retryable")) and bool((b1 or {}).get("terminal"))
              and bool((b1 or {}).get("already")) and bool((b1 or {}).get("policy"))
              and bool((b1 or {}).get("how_to_use")),
              "HTTP %s keys=%r" % (st1, list(b1 or {})[:6]))

        unk = _py("E.retryable_of('TOTALLY_UNKNOWN_CODE')")
        check("② 默认停手（不认识的码 retryable=False）", unk == "False", "got=%r" % unk)

        al = _py("[E.retryable_of('ALREADY_DISPATCHED'), E.is_already('ALREADY_DISPATCHED'), "
                 "E.annotate({'code':'ALREADY_DONE'}).get('done')]")
        check("③ 幂等命中是成功语义（retryable=False + done=True）",
              al.replace(" ", "") == "[False,True,True]", "got=%r" % al)

        rt = _py("[E.retryable_of('CELL_EMPTY'), E.retryable_of('WAKEUP_FAILED')]")
        check("④ 临时状态才可重试", rt.replace(" ", "") == "[True,True]", "got=%r" % rt)

        # 夹具：建一个空工作间（够打 404 用了）
        wsroot = Path(tmpdir) / "wsroot"
        (wsroot / "_lead").mkdir(parents=True, exist_ok=True)
        (wsroot / "_lead" / "建模.md").write_text("# 建模\n", encoding="utf-8")
        _st, made = _http(base, "/api/workshop", {"name": "错误语义判据间（示例）",
                                                 "hall_content": "x",
                                                 "workshop_path": str(wsroot)})
        wid = str((made or {}).get("workshop_id") or "")

        st5, b5 = _http(base, "/api/workshop/%s/tasks/999/redispatch" % wid, {})
        check("⑤ 结构性拒绝 ⇒ 真响应 retryable=false（不存在任务的重派）",
              st5 == 404 and (b5 or {}).get("retryable") is False,
              "HTTP %s %s" % (st5, json.dumps(b5, ensure_ascii=False)[:180]))

        st6, b6 = _http(base, "/api/workshop/no-such-ws-1/tasks/1/redispatch", {})
        check("⑥ 工作间不存在也 retryable=false",
              st6 in (404, 409) and (b6 or {}).get("retryable") is False,
              "HTTP %s %s" % (st6, json.dumps(b6, ensure_ascii=False)[:180]))

        human5 = bool((b5 or {}).get("error") or (b5 or {}).get("next"))
        human6 = bool((b6 or {}).get("error") or (b6 or {}).get("next"))
        check("⑦ 加字段没丢人话（error/next 至少一个还在）", human5 and human6,
              "5=%r 6=%r" % (human5, human6))
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
