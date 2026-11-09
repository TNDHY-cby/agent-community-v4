#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""判据：**派活幂等**（借鉴竞品 A2）—— 同工位 + 同内容 + 还在进行中 ⇒ 不新建第二条。

真跑：临时实例 + 真工作间 + 真名单 + 真派活（**看账本条数**，不信接口自报）。

| # | 格 | 断言 |
|---|---|---|
| ① | 平台活着 + 夹具齐 | 建间/名单/证据/首次派活都成 |
| ② | **同内容重派 ⇒ ALREADY_DISPATCHED** | `duplicate=true` + 同一个 `task_seq` + `done=true`（A4 语义） |
| ③ | **账本真的没多一条** | 任务列表里 issued 的条数仍是 1 |
| ④ | `force: true` ⇒ 真的再派一条 | 新 `task_seq` 更大，issued 变 2 |
| ⑤ | 内容变了 ⇒ 算新活 | 改一个字再派 ⇒ 新任务（不为 0） |
| ⑥ | 不同工位同内容 ⇒ 不算重复 | 派给另一个工位 ⇒ 新任务 |
| ⑦ | 指纹**不含时间**：隔 1.1 秒再派仍然判重 | 跨秒仍 `duplicate=true` |

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
S1 = "前端组/员工01"
S2 = "后端组/员工01"


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


def _issued(base: str, wid: str) -> int:
    tasks = (_http(base, "/api/workshop/%s/tasks" % wid)[1] or {}).get("tasks") or []
    return len([t for t in tasks if str(t.get("state") or "") in ("issued", "working")])


def main() -> int:
    port = _free_port()
    tmpdir = tempfile.mkdtemp(prefix="ac_idem_")
    base = "http://127.0.0.1:%d" % port
    env = dict(os.environ)
    env.update({"AC_PORT": str(port), "AC_DATA_DIR": tmpdir, "PYTHONIOENCODING": "utf-8",
                "AC_ARTIFACT_WATCH_SEC": "3600"})
    srv = subprocess.Popen([PY, "-m", "agent_community.platform.server"], cwd=str(ROOT), env=env,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print("判据：派活幂等（A2 · 真跑）")
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

        ddir = Path(tmpdir)
        (ddir / "lead_inbox").mkdir(parents=True, exist_ok=True)
        _http(base, "/api/harness/register", {
            "harness_id": "示例Harness-Lead", "harness_name": "示例Harness-Lead",
            "harness_type": "custom", "wakeup_method": "file_poll",
            "wakeup_dir": str(ddir / "lead_inbox"),
            "tools": [{"name": "write_file"}], "ai": {"model_name": "示例模型"}})
        for hid in ("示例Harness-M1", "示例Harness-M2"):
            _http(base, "/api/harness/register", {
                "harness_id": hid, "harness_name": hid, "harness_type": "custom",
                "wakeup_method": "a2a", "api_base_url": "http://127.0.0.1:9/a2a/x",
                "tools": [{"name": "write_file"}], "ai": {"model_name": "示例模型"}})
        ws_root = ddir / "ws_idem"
        for d in (ws_root / "_lead", ws_root / "前端组", ws_root / "后端组",
                  ws_root / S1 / "任务", ws_root / S1 / "产物",
                  ws_root / S2 / "任务", ws_root / S2 / "产物"):
            d.mkdir(parents=True, exist_ok=True)
        (ws_root / "前端组" / "分工.md").write_text("- 版本: 1\n", encoding="utf-8")
        (ws_root / "后端组" / "分工.md").write_text("- 版本: 1\n", encoding="utf-8")
        (ws_root / "_lead" / "建模.md").write_text("# 建模（示例）\n", encoding="utf-8")
        _st, made = _http(base, "/api/workshop", {"name": "派活幂等判据间（示例）",
                                                 "hall_content": "x",
                                                 "workshop_path": str(ws_root)})
        wid = str((made or {}).get("workshop_id") or "")
        mk = _http(base, "/api/mind/A-01", {})[1] or {}
        _http(base, "/api/workshop/%s/manifest" % wid,
              {"mind_path": str(mk.get("mind_path") or ""), "entry_points": ["_lead/建模.md"]})
        txt = ("#回执 v1\n类型: 名单\n工作间: %s\n成员: 组长\n凭据:\n序号: 33\n---\n" % wid
               + "岗位: %s | harness: 示例Harness-M1 | model: 示例模型 | 人数: 1\n" % S1
               + "岗位: %s | harness: 示例Harness-M2 | model: 示例模型 | 人数: 1\n" % S2
               + "岗位: _lead | harness: 示例Harness-Lead | model: 示例模型 | 人数: 1\n"
               + "---\n行数: 3")
        _http(base, "/api/workshop/%s/intake?source=C&confirm=1" % wid, {"text": txt})
        roster = (_http(base, "/api/workshop/%s/roster" % wid)[1] or {})
        seqs = {str((m or {}).get("slot_dir") or ""): int((m or {}).get("member_seq") or 0)
                for m in (roster.get("members") or [])}
        for slot in (S1, S2):
            _http(base, "/api/workshop/%s/members/%s/evidence" % (wid, seqs.get(slot)),
                  {"protocol_ok": True, "verified_scope": "判据", "tier": "a_protocol"})

        body = {"member_seq": seqs.get(S1), "title": "做一个本地记账工具",
                "goal": "三列：日期/金额/备注", "accept": "能记账即可"}
        st1, b1 = _http(base, "/api/workshop/%s/dispatch" % wid, body)
        t1 = int((b1 or {}).get("task_seq") or 0)
        check("① 夹具齐 + 首次派活成功", st1 == 200 and t1 > 0, "task_seq=%r raw=%r" % (t1, str(b1)[:120]))

        st2, b2 = _http(base, "/api/workshop/%s/dispatch" % wid, body)
        check("② 同内容重派 ⇒ ALREADY_DISPATCHED（同一个 task_seq + done=true）",
              st2 == 200 and (b2 or {}).get("duplicate") is True
              and int((b2 or {}).get("task_seq") or 0) == t1
              and (b2 or {}).get("done") is True and (b2 or {}).get("retryable") is False,
              "resp=%r" % json.dumps(b2, ensure_ascii=False)[:220])

        check("③ 账本真的没多一条（issued 仍 1）", _issued(base, wid) == 1, "issued=%d" % _issued(base, wid))

        st4, b4 = _http(base, "/api/workshop/%s/dispatch" % wid, dict(body, force=True))
        t4 = int((b4 or {}).get("task_seq") or 0)
        check("④ force:true ⇒ 真的再派一条", st4 == 200 and t4 > t1 and _issued(base, wid) == 2,
              "new=%r issued=%d" % (t4, _issued(base, wid)))

        st5, b5 = _http(base, "/api/workshop/%s/dispatch" % wid,
                        dict(body, title="做一个本地记账工具（改）"))
        check("⑤ 内容变了 ⇒ 算新活", st5 == 200 and int((b5 or {}).get("task_seq") or 0) > t4,
              "new=%r" % (b5 or {}).get("task_seq"))

        st6, b6 = _http(base, "/api/workshop/%s/dispatch" % wid, dict(body, member_seq=seqs.get(S2)))
        check("⑥ 不同工位同内容 ⇒ 不算重复",
              st6 == 200 and not (b6 or {}).get("duplicate") and int((b6 or {}).get("task_seq") or 0) > 0,
              "resp=%r" % json.dumps({k: (b6 or {}).get(k) for k in ("duplicate", "task_seq")},
                                     ensure_ascii=False))

        time.sleep(1.1)                       # ★跨秒：指纹里绝不能有时间的另一道保险
        st7, b7 = _http(base, "/api/workshop/%s/dispatch" % wid,
                        dict(body, title="做一个本地记账工具（改）"))
        check("⑦ 跨秒重派仍判重（指纹不含时间）",
              st7 == 200 and (b7 or {}).get("duplicate") is True,
              "resp=%r" % json.dumps({k: (b7 or {}).get(k) for k in ("duplicate", "task_seq")},
                                     ensure_ascii=False))
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
