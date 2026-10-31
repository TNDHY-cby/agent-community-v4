#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""判据：**外端回执真的收得下、拒得明、留得全**（`V110` / P1 批，12 格）。

跑在临时实例上（换端口 + 换数据目录），三档各走一遍真流程：

```
A 档：签 token → POST /intake?source=A（真 HTTP、真 Authorization 头）
B 档：真写 <workshop_path>/_meta/_inbox/*.rcpt → POST /intake/poll（真文件、真移动）
C 档：不带 confirm 必须被拒 → 带 confirm 收下（L4 留痕）
坏单：整单拒（422）+ 流水里查得到 + 原文留在 AC_DATA_DIR
序号：重复同内容 ⇒ duplicate；回跳 ⇒ 409
```

退出码：0＝全过（或如实 SKIP）；1＝有格没过；2＝起不来。
"""
from __future__ import annotations

import argparse
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

_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
if _ROOT not in sys.path:
    sys.path.append(_ROOT)
PY = sys.executable

GOOD = """#回执 v1
类型: 名单
工作间: {ws}
成员: 组长
凭据:
序号: {seq}
---
岗位: 前端组/员工01 | harness: h_cursor | model: claude-x | 人数: 1
---
行数: 1"""

BAD = """#回执 v1
类型: 名单
工作间: {ws}
成员: 组长
凭据:
序号: {seq}
---
岗位: 前端组/员工01 | harness: h_cursor | mdl: claude-x | 人数: 1
---
行数: 1"""


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def _http(base: str, path: str, body=None, method: str = "", token: str = "", timeout: float = 30):
    # ⚠️ 路径里会有中文（成员名）⇒ 必须转义，否则 urllib 报 ascii 编码错（2026-10-26 真踩）
    path = urllib.parse.quote(path, safe="/?&=%:")
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    headers = {"Content-Type": "application/json"} if data else {}
    if token:
        headers["Authorization"] = "Bearer " + token
    req = urllib.request.Request(base + path, data=data, headers=headers,
                                 method=method or ("POST" if data is not None else "GET"))
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8", "replace")
            return int(r.status), (json.loads(raw) if raw.strip() else {})
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            return int(e.code), (json.loads(raw) if raw.strip() else {})
        except ValueError:
            return int(e.code), {"raw": raw[:300]}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep", action="store_true")
    a = ap.parse_args()

    port = _free_port()
    tmpdir = tempfile.mkdtemp(prefix="ac_receipt_")
    base = "http://127.0.0.1:%d" % port
    wsroot = Path(tmpdir) / "wsroot"
    wsroot.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env.update({"AC_PORT": str(port), "AC_DATA_DIR": str(Path(tmpdir) / "data"),
                "PYTHONIOENCODING": "utf-8"})
    rows, fails, skips = [], [], []
    proc = None

    def check(name, ok, detail=""):
        rows.append((name, bool(ok), detail))
        if not ok:
            fails.append("%s（%s）" % (name, detail))

    def skip(name, why):
        rows.append((name, None, why))
        skips.append("%s（%s）" % (name, why))

    try:
        proc = subprocess.Popen([PY, "-m", "agent_community.platform.server"], cwd=_ROOT, env=env,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        up = False
        for _ in range(60):
            try:
                st, _d = _http(base, "/api/status", timeout=3)
                up = st == 200
            except OSError:
                up = False
            if up:
                break
            time.sleep(0.5)
        if not up:
            print("[FAIL] 临时平台起不来（%s）" % base)
            return 2
        check("临时平台起来（换端口+换数据目录）", True)

        st, made = _http(base, "/api/workshop", {"name": "示例间", "hall_content": "方向",
                                                 "workshop_path": str(wsroot)})
        wid = str((made or {}).get("workshop_id") or "")
        check("夹具：建一个工作间（带 workshop_path）", bool(wid),
              "HTTP %s %s" % (st, json.dumps(made, ensure_ascii=False)[:160]))
        if not wid:
            raise SystemExit(1)

        # ── A 档：签 token → 真 HTTP 带 Bearer ─────────────────────
        st, tk = _http(base, "/api/workshop/%s/members/%s/token" % (wid, "组长"), {})
        token = str((tk or {}).get("token") or "")
        check("A① 签得出 token（且只回指纹）", st == 200 and bool(token),
              "HTTP %s %s" % (st, json.dumps(tk, ensure_ascii=False)[:160]))
        st, noauth = _http(base, "/api/workshop/%s/intake?source=A" % wid,
                           {"text": GOOD.format(ws=wid, seq=1)})
        check("A② 不带 token ⇒ 401（fail-closed）", st == 401, "HTTP %s" % st)
        st, okA = _http(base, "/api/workshop/%s/intake?source=A" % wid,
                        {"text": GOOD.format(ws=wid, seq=1)}, token=token)
        check("A③ 带 token 收下（P2：名单真落库，applied=true）",
              st == 200 and bool(okA.get("ok")) and okA.get("applied") is True,
              "HTTP %s %s" % (st, json.dumps(okA, ensure_ascii=False)[:200]))
        st, dup = _http(base, "/api/workshop/%s/intake?source=A" % wid,
                        {"text": GOOD.format(ws=wid, seq=1)}, token=token)
        check("A④ 同一份重发 ⇒ 幂等 duplicate（不重复记账）",
              st == 200 and dup.get("duplicate") is True, json.dumps(dup, ensure_ascii=False)[:160])
        st, back = _http(base, "/api/workshop/%s/intake?source=A" % wid,
                         {"text": GOOD.format(ws=wid, seq=1).replace("claude-x", "claude-y")},
                         token=token)
        check("A⑤ 同序号不同内容 ⇒ 409（R010 并给下一个序号）",
              st == 409 and "RECEIPT_R010" in json.dumps(back, ensure_ascii=False),
              json.dumps(back, ensure_ascii=False)[:200])
        _http(base, "/api/workshop/%s/members/%s/revoke" % (wid, "组长"), {})
        st, rv = _http(base, "/api/workshop/%s/intake?source=A" % wid,
                       {"text": GOOD.format(ws=wid, seq=2)}, token=token)
        check("A⑥ 吊销后立刻 401（TOKEN_REVOKED）",
              st == 401 and rv.get("code") == "TOKEN_REVOKED", json.dumps(rv, ensure_ascii=False)[:160])

        # ── C 档：人工粘贴必须确认 ────────────────────────────────
        st, needc = _http(base, "/api/workshop/%s/intake?source=C" % wid,
                          {"text": GOOD.format(ws=wid, seq=3)})
        check("C① 没打勾 ⇒ 403 NEED_HUMAN_CONFIRM", st == 403 and
              needc.get("code") == "NEED_HUMAN_CONFIRM", json.dumps(needc, ensure_ascii=False)[:160])
        st, okC = _http(base, "/api/workshop/%s/intake?source=C&confirm=1" % wid,
                        {"text": GOOD.format(ws=wid, seq=3)})
        check("C② 打勾后收下（记 L4）", st == 200 and bool(okC.get("ok")),
              json.dumps(okC, ensure_ascii=False)[:160])

        # ── 坏单：整单拒 + 流水 + 原文 ────────────────────────────
        st, bad = _http(base, "/api/workshop/%s/intake?source=C&confirm=1" % wid,
                        {"text": BAD.format(ws=wid, seq=4)})
        first = ((bad.get("detail") or {}).get("errors") or [{}])[0]
        check("坏① 整单拒（422 + 指到行/字段 + fix）",
              st == 422 and bool(first.get("code")) and bool(first.get("fix")),
              json.dumps(bad, ensure_ascii=False)[:200])
        st, flow = _http(base, "/api/workshop/%s/receipts?limit=10" % wid)
        kinds = [i.get("kind") for i in (flow.get("items") or [])]
        check("坏② 流水里查得到被拒的那单（含原文路径）",
              "rejected" in kinds and any(i.get("raw") for i in flow.get("items") or []),
              json.dumps(flow.get("counts"), ensure_ascii=False))
        rawdir = Path(tmpdir) / "data" / "receipts"
        raws = list(rawdir.rglob("*.rcpt")) if rawdir.is_dir() else []
        check("坏③ 原文真的落盘（AC_DATA_DIR 下，换目录跟着走）",
              bool(raws) and any("mdl: claude-x" in p.read_text(encoding="utf-8",
                                                               errors="replace") for p in raws),
              "共 %d 份原文" % len(raws))

        # ── B 档：真写文件 → poll → 真移动 ───────────────────────
        st, det = _http(base, "/api/workshop/%s" % wid)
        wsd = Path(str((det or {}).get("workspace_dir") or (det or {}).get("workshop_path")
                       or wsroot))
        inbox = wsd / "_meta" / "_inbox"
        inbox.mkdir(parents=True, exist_ok=True)
        (inbox / "0009-组长.rcpt").write_text(GOOD.format(ws=wid, seq=9), encoding="utf-8")
        st, poll = _http(base, "/api/workshop/%s/intake/poll" % wid, {})
        items = (poll.get("processed") or [])
        check("B① 扫到并收下（位置即凭据）",
              st == 200 and bool(items) and items[0].get("ok") is True,
              json.dumps(poll, ensure_ascii=False)[:200])
        check("B② 成功后文件被移进 _done/ 并留 result.json",
              (inbox / "_done" / "0009-组长.rcpt").is_file()
              and (inbox / "_done" / "0009-组长.rcpt.result.json").is_file(),
              "inbox=%s" % inbox.as_posix())
        (inbox / "0010.rcpt").write_text("#回执 v9\n", encoding="utf-8")
        st, poll2 = _http(base, "/api/workshop/%s/intake/poll" % wid, {})
        check("B③ 坏件留在原地 + _fail/<name>.err 写明原因",
              (inbox / "0010.rcpt").is_file() and (inbox / "_fail" / "0010.rcpt.err").is_file(),
              json.dumps(poll2, ensure_ascii=False)[:200])
    finally:
        if proc is not None and not a.keep:
            try:
                proc.terminate()
                proc.wait(timeout=10)
            except Exception:      # noqa: BLE001 — 有意静默：收尾杀不掉不影响已得出的结论
                try:
                    proc.kill()
                except Exception:  # noqa: BLE001 — 有意静默：同上
                    pass
        if not a.keep:
            shutil.rmtree(tmpdir, ignore_errors=True)

    print("=" * 78)
    print("判据：外端回执（V110 / P1）三档真收 —— 临时实例 %s" % base)
    print("=" * 78)
    for name, ok, detail in rows:
        tag = "SKIP" if ok is None else ("PASS" if ok else "FAIL")
        print("  %s %s%s" % (tag, name, ("　（%s）" % detail) if ok is not True else ""))
    print("-" * 78)
    _pass = sum(1 for _n, _o, _d in rows if _o is True)
    print("合计 %d 格：PASS %d / FAIL %d / SKIP %d" % (len(rows), _pass, len(fails), len(skips)))
    return 0 if not fails else 1


if __name__ == "__main__":
    raise SystemExit(main())
