#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""判据：**跨间交接单**（`V138` §三）—— A 间 → B 间，带凭据移交，不做跨间自由聊天。

真跑：临时实例（换端口 + 换 `AC_DATA_DIR`）+ 两个真工作间 + 真写文件。

| # | 格 | 断言 |
|---|---|---|
| ① | 平台活着 | `GET /api/status` 200 |
| ② | 两边都留痕 | 来源 `_handover_out.md` 与目标 `_handover_in.md` 都有这一行（含交接单号） |
| ③ | 目标间组长被通知 | 目标 `_lead/_notice.md` 有这一行 |
| ④ | 同间拒绝 | 400（同间协作不该走交接单） |
| ⑤ | 目标不存在 | 404 且说清哪个 id |
| ⑥ | 双向可查 | `GET …/handovers` 的 `out`/`in` 各 1 条 |

退出码：0＝全过；1＝有格没过；2＝起不来（如实说没跑成）。
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


def _mkws(base, tmpdir: Path, name: str, sub: str) -> str:
    root = tmpdir / sub
    (root / "_lead").mkdir(parents=True, exist_ok=True)
    (root / "_lead" / "建模.md").write_text("# 建模（示例）\n", encoding="utf-8")
    st, b = _http(base, "/api/workshop", {"name": name, "hall_content": "交接",
                                          "workshop_path": str(root)})
    return str((b or {}).get("workshop_id") or "")


def main() -> int:
    port = _free_port()
    tmpdir = tempfile.mkdtemp(prefix="ac_xhop_")
    base = "http://127.0.0.1:%d" % port
    env = dict(os.environ)
    env.update({"AC_PORT": str(port), "AC_DATA_DIR": tmpdir, "PYTHONIOENCODING": "utf-8",
                "AC_ARTIFACT_WATCH_SEC": "3600"})
    srv = subprocess.Popen([PY, "-m", "agent_community.platform.server"], cwd=str(ROOT), env=env,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print("判据：跨间交接单（V138 §三 · 真跑：两个工作间 + 真写文件）")
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
        check("① 平台活着", True)

        ddir = Path(tmpdir)
        wa = _mkws(base, ddir, "来源间（示例）", "ws_a")
        wb = _mkws(base, ddir, "目标间（示例）", "ws_b")
        st, b = _http(base, "/api/workshop/%s/handover-to-workshop" % wa,
                      {"target_workshop_id": wb, "title": "登录页交给后端做接口",
                       "goal": "接口能通", "accept": "curl 200", "proof": "示例凭据",
                       "note": "前端已定稿"})
        hid = str((b or {}).get("handover_id") or "")
        a_out = (ddir / "ws_a" / "_handover_out.md")
        b_in = (ddir / "ws_b" / "_handover_in.md")
        b_notice = (ddir / "ws_b" / "_lead" / "_notice.md")
        t_out = a_out.read_text(encoding="utf-8") if a_out.is_file() else ""
        t_in = b_in.read_text(encoding="utf-8") if b_in.is_file() else ""
        t_no = b_notice.read_text(encoding="utf-8") if b_notice.is_file() else ""
        check("② 两边都留痕（含交接单号）",
              st == 200 and hid and hid in t_out and hid in t_in,
              "HTTP %s hid=%r out=%r in=%r" % (st, hid, t_out[-80:], t_in[-80:]))
        check("③ 目标间组长被通知", hid in t_no, t_no[-120:])

        st4, b4 = _http(base, "/api/workshop/%s/handover-to-workshop" % wa,
                        {"target_workshop_id": wa, "title": "自己交给自己"})
        check("④ 同间拒绝（同间协作不走交接单）",
              st4 == 400 and "自己" in str((b4 or {}).get("error") or ""),
              "HTTP %s %s" % (st4, json.dumps(b4, ensure_ascii=False)[:140]))

        st5, b5 = _http(base, "/api/workshop/%s/handover-to-workshop" % wa,
                        {"target_workshop_id": "no-such-ws", "title": "x"})
        check("⑤ 目标不存在 ⇒ 404 且说清哪个 id",
              st5 == 404 and "no-such-ws" in str((b5 or {}).get("error") or ""),
              "HTTP %s %s" % (st5, json.dumps(b5, ensure_ascii=False)[:140]))

        st6a, b6a = _http(base, "/api/workshop/%s/handovers" % wa)
        st6b, b6b = _http(base, "/api/workshop/%s/handovers" % wb)
        check("⑥ 双向可查（来源看 out、目标看 in）",
              st6a == 200 and len((b6a or {}).get("out") or []) == 1
              and not (b6a or {}).get("in")
              and st6b == 200 and len((b6b or {}).get("in") or []) == 1
              and not (b6b or {}).get("out"),
              "A(out=%r in=%r) B(in=%r out=%r)"
              % (len((b6a or {}).get("out") or []), len((b6a or {}).get("in") or []),
                 len((b6b or {}).get("in") or []), len((b6b or {}).get("out") or [])))
        # ⑦⑧ 接收方确认（交接单不是"发出去就算"）
        st7, b7 = _http(base, "/api/workshop/%s/handovers" % wb)
        inn0 = (b7 or {}).get("in") or []
        check("⑦ 未确认时：收到方看到 acked=false",
              st7 == 200 and bool(inn0) and inn0[0].get("acked") is False,
              "in=%r" % json.dumps(inn0[:1], ensure_ascii=False)[:200])

        st8, b8 = _http(base, "/api/workshop/%s/handovers/%s/ack" % (wb, hid), {})
        st8b, b8b = _http(base, "/api/workshop/%s/handovers/%s/ack" % (wb, hid), {})
        _st_a, b_a = _http(base, "/api/workshop/%s/handovers" % wa)
        _st_b, b_b = _http(base, "/api/workshop/%s/handovers" % wb)
        out_a = (b_a or {}).get("out") or []
        in_b = (b_b or {}).get("in") or []
        check("⑧ 确认后：两边都标 acked=true，且重复确认幂等",
              st8 == 200 and (b8 or {}).get("acked") is True
              and st8b == 200 and (b8b or {}).get("already") is True
              and bool(out_a) and out_a[0].get("acked") is True
              and bool(in_b) and in_b[0].get("acked") is True,
              "ack=%s already=%r out=%r in=%r"
              % (st8, (b8b or {}).get("already"),
                 out_a[:1], in_b[:1]))

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
