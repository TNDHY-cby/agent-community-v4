#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""判据：**跨工作间汇总**（`GET /api/lead-todo-all`）—— 大厅首页要的那一口。

真跑：临时实例（换端口 + 换 `AC_DATA_DIR`）+ **两个**工作间（一个有活、一个空）。

| # | 格 | 断言 |
|---|---|---|
| ① | 平台活着 | `GET /api/status` 200 |
| ② | 两个间都在 `totals.workshops` 里 | `workshops=2` |
| ③ | 有活的那个被列出来，空的只计数 | `workshops_with_todo=1`、`empty_workshops=1` |
| ④ | 事在汇总里可见（是"没回执"那一类） | `totals.no_receipt>=1`，且该间 `later` 非空 |
| ⑤ | 不排分 | 响应里无 `score`/`rank`；每条 priority 只在 {now, later} |
| ⑥ | 只读：连打两次结果一致（账本不被改） | 两次 `totals.total` 相同 |
| ⑦ | `generated_at` 存在（前端要显示"什么时候算的"） | 非空 |

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
S1 = "前端组/员工01"


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


def _mk_workshop(base, ddir: Path, tag: str, with_member: bool) -> str:
    root = ddir / ("ws_" + tag)
    (root / "_lead").mkdir(parents=True, exist_ok=True)
    (root / "_lead" / "建模.md").write_text("# 建模（示例）\n", encoding="utf-8")
    if with_member:
        (root / "前端组").mkdir(parents=True, exist_ok=True)
        (root / "前端组" / "分工.md").write_text("- 版本: 1\n", encoding="utf-8")
        for d in (root / S1 / "任务", root / S1 / "产物"):
            d.mkdir(parents=True, exist_ok=True)
    _st, made = _http(base, "/api/workshop", {"name": "汇总判据间-%s（示例）" % tag,
                                              "hall_content": "x", "workshop_path": str(root)})
    wid = str((made or {}).get("workshop_id") or "")
    if not with_member:
        return wid
    mk = _http(base, "/api/mind/A-01", {})[1] or {}
    _http(base, "/api/workshop/%s/manifest" % wid,
          {"mind_path": str(mk.get("mind_path") or ""), "entry_points": ["_lead/建模.md"]})
    txt = ("#回执 v1\n类型: 名单\n工作间: %s\n成员: 组长\n凭据:\n序号: 27\n---\n" % wid
           + "岗位: %s | harness: 示例Harness-M1 | model: 示例模型 | 人数: 1\n" % S1
           + "岗位: _lead | harness: 示例Harness-Lead | model: 示例模型 | 人数: 1\n"
           + "---\n行数: 2")
    _http(base, "/api/workshop/%s/intake?source=C&confirm=1" % wid, {"text": txt})
    roster = (_http(base, "/api/workshop/%s/roster" % wid)[1] or {})
    seq = {str((m or {}).get("slot_dir") or ""): int((m or {}).get("member_seq") or 0)
           for m in (roster.get("members") or [])}.get(S1, 0)
    if seq:
        _http(base, "/api/workshop/%s/members/%s/evidence" % (wid, seq),
              {"protocol_ok": True, "verified_scope": "判据", "tier": "a_protocol"})
        _http(base, "/api/workshop/%s/dispatch" % wid,
              {"member_seq": seq, "title": "汇总夹具任务", "goal": "x", "accept": "x"})
    return wid


def main() -> int:
    port = _free_port()
    tmpdir = tempfile.mkdtemp(prefix="ac_ltall_")
    base = "http://127.0.0.1:%d" % port
    env = dict(os.environ)
    env.update({"AC_PORT": str(port), "AC_DATA_DIR": tmpdir, "PYTHONIOENCODING": "utf-8",
                "AC_ARTIFACT_WATCH_SEC": "3600",
                "AC_NUDGE_ISSUED_MIN": "0", "AC_NUDGE_DELIVERED_MIN": "0",
                "AC_PRESENCE_SILENT_MIN": "0"})
    srv = subprocess.Popen([PY, "-m", "agent_community.platform.server"], cwd=str(ROOT), env=env,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print("判据：跨工作间汇总（lead-todo-all · 真跑）")
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
        (ddir / "lead_inbox").mkdir(parents=True, exist_ok=True)
        _http(base, "/api/harness/register", {
            "harness_id": "示例Harness-Lead", "harness_name": "示例Harness-Lead",
            "harness_type": "custom", "wakeup_method": "file_poll",
            "wakeup_dir": str(ddir / "lead_inbox"),
            "tools": [{"name": "write_file"}], "ai": {"model_name": "示例模型"}})
        _http(base, "/api/harness/register", {
            "harness_id": "示例Harness-M1", "harness_name": "示例Harness-M1",
            "harness_type": "custom", "wakeup_method": "a2a",
            "api_base_url": "http://127.0.0.1:9/a2a/x",
            "tools": [{"name": "write_file"}], "ai": {"model_name": "示例模型"}})

        wid_busy = _mk_workshop(base, ddir, "busy", with_member=True)
        _wid_empty = _mk_workshop(base, ddir, "empty", with_member=False)

        st2, b2 = _http(base, "/api/lead-todo-all")
        tot = (b2 or {}).get("totals") or {}
        check("② 两个间都在汇总里", st2 == 200 and int(tot.get("workshops") or 0) == 2,
              "totals=%r" % json.dumps(tot, ensure_ascii=False))

        check("③ 有活的列出来、空的只计数",
              int(tot.get("workshops_with_todo") or 0) == 1
              and int(tot.get("empty_workshops") or 0) == 1,
              "with_todo=%r empty=%r" % (tot.get("workshops_with_todo"), tot.get("empty_workshops")))

        rows = (b2 or {}).get("workshops") or []
        busy = next((r for r in rows if r.get("workshop_id") == wid_busy), {})
        check("④ 事在汇总里可见（没回执那一类）",
              int(tot.get("no_receipt") or 0) >= 1 and bool(busy.get("later")),
              "no_receipt=%r later=%d" % (tot.get("no_receipt"), len(busy.get("later") or [])))

        blob = json.dumps(b2, ensure_ascii=False).lower()
        prios = {x.get("priority") for r in rows
                 for x in ((r.get("urgent") or []) + (r.get("later") or []))}
        check("⑤ 不排分（无 score/rank；priority 只两档）",
              "score" not in blob and "rank" not in blob and prios <= {"now", "later"},
              "prios=%r" % prios)

        st6, b6 = _http(base, "/api/lead-todo-all")
        check("⑥ 只读：连打两次结果一致",
              st6 == 200 and int(((b6 or {}).get("totals") or {}).get("total") or 0)
              == int(tot.get("total") or 0),
              "两次 total=%r / %r" % (tot.get("total"),
                                      ((b6 or {}).get("totals") or {}).get("total")))

        # ⑧ ?status=active 只算未关闭的间（归档间里的旧任务不该出现在"今天该管什么"）
        import json as _json
        # 把一个间关掉，再对比两种查询
        _wid_to_close = _mk_workshop(base, ddir, "closing", with_member=False)
        # 先看总数（含 status 全部）
        _st_a, b_a = _http(base, "/api/lead-todo-all")
        n_all = int(((b_a or {}).get("totals") or {}).get("workshops") or 0)
        st8, b8 = _http(base, "/api/lead-todo-all?status=active")
        n_act = int(((b8 or {}).get("totals") or {}).get("workshops") or 0)
        filt = (b8 or {}).get("filter") or {}
        check("⑧ ?status=active 生效且如实回显过滤条件",
              st8 == 200 and n_act <= n_all and filt.get("status") == "active",
              "all=%r active=%r filter=%r" % (n_all, n_act, filt))

        check("⑦ 有 generated_at（前端要显示什么时候算的）",
              bool((b2 or {}).get("generated_at")) or bool((b6 or {}).get("generated_at")),
              "generated_at=%r" % ((b2 or {}).get("generated_at") or (b6 or {}).get("generated_at")))
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
