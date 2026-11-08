#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""判据：**组长今天该管谁**（`platform/lead_todo`）—— 催办 + 失联合成一张待办。

真跑：临时实例（换端口 + 换 `AC_DATA_DIR`）+ 真工作间 + 真名单 + 真派活 + 真产物。

| # | 格 | 断言 |
|---|---|---|
| ① | 平台活着 | `GET /api/status` 200 |
| ② | 派活后：没回执进清单（可以等等） | `counts.no_receipt>=1`、`later` 里有它 |
| ③ | 产物到了：待验收（不是 urgent） | `counts.no_review>=1`、在 `later` |
| ④ | 每条都带"怎么做" | 每条都有非空 `ask` 与 `how`（`how` 里含端点名） |
| ⑤ | 没活可管 ⇒ 空清单 | 另一个空工作间：`total=0`、`items=[]` |
| ⑥ | 不排分 | 没有 `score`/`rank` 字段；priority 只在 {now, later} |
| ⑦ | 不存在的工作间 ⇒ 404 | `NO_SUCH_WORKSHOP` |

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


def main() -> int:
    port = _free_port()
    tmpdir = tempfile.mkdtemp(prefix="ac_ltodo_")
    base = "http://127.0.0.1:%d" % port
    env = dict(os.environ)
    env.update({"AC_PORT": str(port), "AC_DATA_DIR": tmpdir, "PYTHONIOENCODING": "utf-8",
                "AC_ARTIFACT_WATCH_SEC": "3600",
                # 阈值压到 0：夹具任务刚建出来就"该管"（只调环境，不改代码）
                "AC_NUDGE_ISSUED_MIN": "0", "AC_NUDGE_DELIVERED_MIN": "0",
                "AC_PRESENCE_SILENT_MIN": "0"})
    srv = subprocess.Popen([PY, "-m", "agent_community.platform.server"], cwd=str(ROOT), env=env,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print("判据：组长今天该管谁（lead-todo · 真跑）")
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
        ws_root = ddir / "ws_lt"
        for d in (ws_root / "_lead", ws_root / "前端组", ws_root / S1 / "任务",
                  ws_root / S1 / "产物"):
            d.mkdir(parents=True, exist_ok=True)
        (ws_root / "前端组" / "分工.md").write_text("- 版本: 1\n", encoding="utf-8")
        (ws_root / "_lead" / "建模.md").write_text("# 建模（示例）\n", encoding="utf-8")
        _st, made = _http(base, "/api/workshop", {"name": "组长待办判据间（示例）",
                                                  "hall_content": "x", "workshop_path": str(ws_root)})
        wid = str((made or {}).get("workshop_id") or "")
        mk = _http(base, "/api/mind/A-01", {})[1] or {}
        _http(base, "/api/workshop/%s/manifest" % wid,
              {"mind_path": str(mk.get("mind_path") or ""), "entry_points": ["_lead/建模.md"]})
        txt = ("#回执 v1\n类型: 名单\n工作间: %s\n成员: 组长\n凭据:\n序号: 19\n---\n" % wid
               + "岗位: %s | harness: 示例Harness-M1 | model: 示例模型 | 人数: 1\n" % S1
               + "岗位: _lead | harness: 示例Harness-Lead | model: 示例模型 | 人数: 1\n"
               + "---\n行数: 2")
        _http(base, "/api/workshop/%s/intake?source=C&confirm=1" % wid, {"text": txt})
        roster = (_http(base, "/api/workshop/%s/roster" % wid)[1] or {})
        seq = {str((m or {}).get("slot_dir") or ""): int((m or {}).get("member_seq") or 0)
               for m in (roster.get("members") or [])}.get(S1, 0)
        _http(base, "/api/workshop/%s/members/%s/evidence" % (wid, seq),
              {"protocol_ok": True, "verified_scope": "判据", "tier": "a_protocol"})
        _http(base, "/api/workshop/%s/dispatch" % wid,
              {"member_seq": seq, "title": "待办夹具任务", "goal": "x", "accept": "x"})

        st2, b2 = _http(base, "/api/workshop/%s/lead-todo" % wid)
        c2 = (b2 or {}).get("counts") or {}
        check("② 派活后：没回执进清单（可以等等）",
              st2 == 200 and int(c2.get("no_receipt") or 0) >= 1
              and any(x.get("kind") == "no_receipt" for x in ((b2 or {}).get("later") or [])),
              "counts=%r" % json.dumps(c2, ensure_ascii=False))

        _http(base, "/api/workshop/%s/scan-inbox" % wid, {})          # 基线
        (ws_root / S1 / "产物" / "交付.md").write_text("真产物", encoding="utf-8")
        _http(base, "/api/workshop/%s/scan-inbox" % wid, {})
        st3, b3 = _http(base, "/api/workshop/%s/lead-todo" % wid)
        c3 = (b3 or {}).get("counts") or {}
        kinds3 = [x.get("kind") for x in ((b3 or {}).get("items") or [])]
        check("③ 产物到了：待验收在清单里（不误报成 urgent）",
              st3 == 200 and int(c3.get("no_review") or 0) >= 1 and "no_review" in kinds3,
              "counts=%r kinds=%r" % (json.dumps(c3, ensure_ascii=False), kinds3))

        items = (b3 or {}).get("items") or []
        bad = [x for x in items if not (x.get("ask") and x.get("how"))]
        check("④ 每条都带'怎么做'（ask + 具体端点）",
              bool(items) and not bad
              and all(any(k in str(x.get("how")) for k in ("/nudge", "/review", "/dispatch", "/task-result"))
                      for x in items),
              "bad=%r" % json.dumps(bad[:2], ensure_ascii=False)[:200])

        # ④b 返工待重交 ⇒ 进"先处理"（被打回又不动，最容易烂）
        _http(base, "/api/workshop/%s/tasks/1/review" % wid,
              {"result": "返工", "evidence_level": "L0", "note": "补一版说明"})
        st4b, b4b = _http(base, "/api/workshop/%s/lead-todo" % wid)
        urg4 = [x.get("kind") for x in ((b4b or {}).get("urgent") or [])]
        check("④b 返工待重交进'先处理'",
              st4b == 200 and int((((b4b or {}).get("counts") or {}).get("awaiting_rework") or 0)) >= 1
              and "awaiting_rework" in urg4,
              "urgent=%r counts=%r" % (urg4, json.dumps((b4b or {}).get("counts"), ensure_ascii=False)))

        # ⑤ 另建一个空工作间
        empty_root = ddir / "ws_empty"
        (empty_root / "_lead").mkdir(parents=True, exist_ok=True)
        (empty_root / "_lead" / "建模.md").write_text("# 建模（示例）\n", encoding="utf-8")
        _st_e, made_e = _http(base, "/api/workshop", {"name": "空待办间（示例）",
                                                      "hall_content": "x",
                                                      "workshop_path": str(empty_root)})
        wid_e = str((made_e or {}).get("workshop_id") or "")
        st5, b5 = _http(base, "/api/workshop/%s/lead-todo" % wid_e)
        check("⑤ 没活可管 ⇒ 空清单（不硬凑）",
              st5 == 200 and int(((b5 or {}).get("counts") or {}).get("total") or 0) == 0
              and not ((b5 or {}).get("items") or []),
              "counts=%r" % json.dumps((b5 or {}).get("counts"), ensure_ascii=False))

        blob = json.dumps(b3, ensure_ascii=False).lower()
        prios = {x.get("priority") for x in items}
        check("⑥ 不排分（无 score/rank；priority 只有两档）",
              "score" not in blob and "rank" not in blob and prios <= {"now", "later"},
              "prios=%r" % prios)

        st7, b7 = _http(base, "/api/workshop/no-such-ws-1/lead-todo")
        check("⑦ 不存在的工作间 ⇒ 404",
              st7 == 404 and (b7 or {}).get("code") == "NO_SUCH_WORKSHOP",
              "HTTP %s" % st7)
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
