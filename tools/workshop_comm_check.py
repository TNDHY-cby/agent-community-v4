#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""判据：**工作间内的日常沟通**（`V138` §二 第一件）—— 说给谁 / 回哪条 / 有没有看到。

老板原话：「工作间的沟通方式还没有做」。

真跑：临时实例（换端口 + 换 `AC_DATA_DIR`）+ 真工作间 + 真 manifest + 真写 `_notice.md`。

| # | 格 | 断言 |
|---|---|---|
| ① | 平台活着 | `GET /api/status` 200 |
| ② | `to=lead` 真通知到组长 | 返回 `notified.slot=_lead`，且 `_lead/_notice.md` 有这一行 |
| ③ | `to=member:<工位>` 真通知到该工位 | 该工位 `_notice.md` 有 |
| ④ | `thread` 指真实消息 | 200 且读回带 `thread` |
| ⑤ | `thread` 指不存在的消息 | 422 `A019`（不静默） |
| ⑥ | `to` 值非法 | 422 `A018` 并给合法取值 |
| ⑦ | 未读：读 → 推进 → 回退被拒 | `unread>=2` → 0，再传小值 `moved=false` |
| ⑧ | 自由聊天没被复活 | `POST …/discuss` 仍 410 |

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
SLOT = "前端组/员工01"
REF = "_lead/建模.md"


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
        except ValueError:   # noqa: BLE001 — 响应不是 JSON 时按原文返回（判据要看真状态码）
            return int(e.code), {"raw": raw[:300]}


def _env(msg_id: str, *, to: str = "", thread: str = "") -> dict:
    e = {"ailp_version": "2.0", "msg_id": msg_id, "msg_type": "PROPOSE",
         "sender": "员工01", "target": "组长", "manifest_ref": REF,
         "payload": {"proposal": "先做接口（判据）", "confidence": 0.8, "basis_ids": [REF]}}
    if to:
        e["to"] = to
    if thread:
        e["thread"] = thread
    return e


def main() -> int:
    port = _free_port()
    tmpdir = tempfile.mkdtemp(prefix="ac_comm_")
    base = "http://127.0.0.1:%d" % port
    env = dict(os.environ)
    env.update({"AC_PORT": str(port), "AC_DATA_DIR": tmpdir, "PYTHONIOENCODING": "utf-8",
                "AC_ARTIFACT_WATCH_SEC": "3600"})
    srv = subprocess.Popen([PY, "-m", "agent_community.platform.server"], cwd=str(ROOT), env=env,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print("判据：工作间内日常沟通（V138 · 真跑：临时实例 + 真 manifest + 真通知）")
    print("=" * 78)
    try:
        up = False
        for _ in range(80):
            try:
                st, _j = _http(base, "/api/status", timeout=3)
                up = st == 200
            except (urllib.error.URLError, OSError):   # noqa: BLE001 — 探活：连不上就是没起来，重试即可
                up = False
            if up:
                break
            time.sleep(0.5)
        if not up:
            print("[FAIL] 平台没起来 —— 这次不算跑成")
            return 2
        check("① 平台活着", True)

        ws_root = Path(tmpdir) / "ws_comm"
        for d in (ws_root / "_lead", ws_root / SLOT, ws_root / SLOT / "任务"):
            d.mkdir(parents=True, exist_ok=True)
        (ws_root / REF).write_text("# 建模（示例）\n", encoding="utf-8")
        (ws_root / "_lead" / "_notice.md").write_text("", encoding="utf-8")
        st_ws, made = _http(base, "/api/workshop", {"name": "沟通判据间（示例）",
                                                    "hall_content": "沟通",
                                                    "workshop_path": str(ws_root)})
        wid = str((made or {}).get("workshop_id") or "")
        mk = _http(base, "/api/mind/A-01", {})[1] or {}
        st_mf, b_mf = _http(base, "/api/workshop/%s/manifest" % wid,
                            {"mind_path": str(mk.get("mind_path") or ""),
                             "entry_points": [REF]})
        st_msg, b_msg = _http(base, "/api/workshop/%s/ailp" % wid, _env("msg-c1", to="lead"))
        ntext = ""
        try:
            ntext = (ws_root / "_lead" / "_notice.md").read_text(encoding="utf-8")
        except FileNotFoundError:   # noqa: BLE001 — 通知文件还没写过＝没有通知，判据照样判它
            pass
        check("② to=lead 真通知到组长（_lead/_notice.md 有这一行）",
              st_msg == 200 and ((b_msg or {}).get("notified") or {}).get("slot") == "_lead"
              and "msg_type" in ntext or (st_msg == 200 and "AILP PROPOSE" in ntext),
              "ws=%s manifest=%s msg=%s notice=%r"
              % (st_ws, st_mf, st_msg, ntext[-120:]))

        st_m2, b_m2 = _http(base, "/api/workshop/%s/ailp" % wid, _env("msg-c2", to="member:" + SLOT))
        mtext = ""
        try:
            mtext = (ws_root / SLOT / "_notice.md").read_text(encoding="utf-8")
        except FileNotFoundError:   # noqa: BLE001 — 通知文件还没写过＝没有通知，判据照样判它
            pass
        check("③ to=member:<工位> 真通知到该工位",
              st_m2 == 200 and "AILP PROPOSE" in mtext,
              "HTTP %s notice=%r" % (st_m2, mtext[-120:]))

        st_t, b_t = _http(base, "/api/workshop/%s/ailp" % wid, _env("msg-c3", thread="msg-c1"))
        st_list, b_list = _http(base, "/api/workshop/%s/ailp?limit=20" % wid)
        got = [x for x in ((b_list or {}).get("items") or []) if x.get("msg_id") == "msg-c3"]
        check("④ thread 指真实消息 ⇒ 存下并读得回来",
              st_t == 200 and bool(got) and (got[0].get("thread") == "msg-c1"),
              "HTTP %s thread=%r" % (st_t, (got or [{}])[0].get("thread")))

        st_bad, b_bad = _http(base, "/api/workshop/%s/ailp" % wid, _env("msg-c4", thread="msg-没有"))
        check("⑤ thread 指不存在的消息 ⇒ 422 A019",
              st_bad == 422 and any(e.get("code") == "A019"
                                    for e in ((b_bad or {}).get("errors") or [])),
              "HTTP %s %s" % (st_bad, json.dumps(b_bad, ensure_ascii=False)[:160]))

        st_to, b_to = _http(base, "/api/workshop/%s/ailp" % wid, _env("msg-c5", to="随便谁"))
        check("⑥ to 值非法 ⇒ 422 A018 并给合法取值",
              st_to == 422 and any(e.get("code") == "A018"
                                   for e in ((b_to or {}).get("errors") or [])),
              "HTTP %s %s" % (st_to, json.dumps(b_to, ensure_ascii=False)[:160]))

        u0 = ((_http(base, "/api/workshop/%s/ailp" % wid)[1] or {}).get("unread") or {})
        st_r, b_r = _http(base, "/api/workshop/%s/ailp/read" % wid, {})
        u1 = (b_r or {}).get("unread") or {}
        st_r2, b_r2 = _http(base, "/api/workshop/%s/ailp/read" % wid, {"seq": 0})
        check("⑦ 未读：读得到 → 推进到 0 → 回退被拒（只前进）",
              int(u0.get("unread", -1)) >= 2 and int(u1.get("unread", -1)) == 0
              and (b_r2 or {}).get("moved") is False,
              "unread=%r → %r backtrack_moved=%r"
              % (u0.get("unread"), u1.get("unread"), (b_r2 or {}).get("moved")))

        st_d, b_d = _http(base, "/api/workshop/%s/discuss" % wid, {"text": "随便聊聊"})
        check("⑧ 自由聊天没被复活（/discuss 仍 410）",
              st_d == 410 and (b_d or {}).get("code") == "FREECHAT_RETIRED",
              "HTTP %s %s" % (st_d, json.dumps(b_d, ensure_ascii=False)[:140]))
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
