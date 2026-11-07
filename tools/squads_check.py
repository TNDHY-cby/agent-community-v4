#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""判据：**小队（模块化工作间）**（`V138` §二 第二件）—— 一间多 小队，不拆间。

老板原话：「小队 模块化工作间根状分级也没开发」。

真跑：临时实例（换端口 + 换 `AC_DATA_DIR`）+ 真工作间 + 真名单 + 真派活。

| # | 格 | 断言 |
|---|---|---|
| ① | 平台活着 | `GET /api/status` 200 |
| ② | 建 小队 落盘 | `squads[]` 有它、成员 `member_seq` 对上 |
| ③ | 同 id 拒绝 | 400 `CELL_INVALID`（不静默覆盖） |
| ④ | 对 小队 派活 | 每个工位各一条任务（`results[].ok`） |
| ⑤ | 账本不新建 | `task_ledger.json` 的键里**没有 squad_id**，只有 `ws+task_seq` |
| ⑥ | 没有"整 小队 验收" | `POST …/squads/{id}/review` 不存在（404/405） |
| ⑦ | 三级视图 | `levels=[workshop,小队,slot]`、未分组工位进 `loose_slots` |

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


def main() -> int:
    port = _free_port()
    tmpdir = tempfile.mkdtemp(prefix="ac_cells_")
    base = "http://127.0.0.1:%d" % port
    env = dict(os.environ)
    env.update({"AC_PORT": str(port), "AC_DATA_DIR": tmpdir, "PYTHONIOENCODING": "utf-8",
                "AC_ARTIFACT_WATCH_SEC": "3600"})
    srv = subprocess.Popen([PY, "-m", "agent_community.platform.server"], cwd=str(ROOT), env=env,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print("判据：**小队** 模块化工作间（V138 · 真跑：临时实例 + 真名单 + 真派活）")
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
        for hid in ("示例Harness-M1", "示例Harness-M2"):
            _http(base, "/api/harness/register", {
                "harness_id": hid, "harness_name": hid, "harness_type": "custom",
                "wakeup_method": "a2a", "api_base_url": "http://127.0.0.1:9/a2a/x",
                "tools": [{"name": "write_file"}], "ai": {"model_name": "示例模型"}})

        ws_root = Path(tmpdir) / "ws_squads"
        for d in (ws_root / "_lead", ws_root / "前端组" / "员工01" / "任务",
                  ws_root / "后端组" / "员工01" / "任务",
                  ws_root / "前端组" / "员工01" / "产物",
                  ws_root / "后端组" / "员工01" / "产物"):
            d.mkdir(parents=True, exist_ok=True)
        (ws_root / "_lead" / "建模.md").write_text("# 建模（示例）\n", encoding="utf-8")
        (ws_root / "前端组" / "分工.md").write_text("- 版本: 1\n", encoding="utf-8")
        _st, made = _http(base, "/api/workshop", {"name": "小队 判据间（示例）",
                                                  "hall_content": "小队",
                                                  "workshop_path": str(ws_root)})
        wid = str((made or {}).get("workshop_id") or "")
        mk = _http(base, "/api/mind/A-01", {})[1] or {}
        _http(base, "/api/workshop/%s/manifest" % wid,
              {"mind_path": str(mk.get("mind_path") or ""), "entry_points": ["_lead/建模.md"]})
        txt = ("#回执 v1\n类型: 名单\n工作间: %s\n成员: 组长\n凭据:\n序号: 7\n---\n" % wid
               + "岗位: %s | harness: 示例Harness-M1 | model: 示例模型 | 人数: 1\n" % S1
               + "岗位: %s | harness: 示例Harness-M2 | model: 示例模型 | 人数: 1\n" % S2
               + "岗位: _lead | harness: 示例Harness-Lead | model: 示例模型 | 人数: 1\n"
               + "---\n行数: 3")
        _http(base, "/api/workshop/%s/intake?source=C&confirm=1" % wid, {"text": txt})
        roster = (_http(base, "/api/workshop/%s/roster" % wid)[1] or {})
        seqs = {str((m or {}).get("slot_dir") or ""): int((m or {}).get("member_seq") or 0)
                for m in (roster.get("members") or [])}
        seq1, seq2 = seqs.get(S1, 0), seqs.get(S2, 0)
        for sq in (seq1, seq2):
            _http(base, "/api/workshop/%s/members/%s/evidence" % (wid, sq),
                  {"protocol_ok": True, "verified_scope": "判据夹具", "tier": "a_protocol"})

        st_c, b_c = _http(base, "/api/workshop/%s/squads" % wid,
                          {"name": "前端模块", "squad_id": "fe", "direction": "前端",
                           "goal": "做页面", "member_seqs": [seq1]})
        st_v, b_v = _http(base, "/api/workshop/%s/squads" % wid)
        fe = next((c for c in ((b_v or {}).get("squads") or []) if c.get("squad_id") == "fe"), {})
        check("② 建 小队 落盘（squads[] 有它、成员对上）",
              st_c == 200 and fe.get("member_count") == 1
              and fe["members"][0]["member_seq"] == seq1,
              "HTTP %s %s" % (st_c, json.dumps(fe, ensure_ascii=False)[:200]))

        st_d, b_d = _http(base, "/api/workshop/%s/squads" % wid,
                          {"name": "重复的", "squad_id": "fe"})
        check("③ 同 id 拒绝（不静默覆盖）",
              st_d == 400 and (b_d or {}).get("code") == "CELL_INVALID",
              "HTTP %s %s" % (st_d, json.dumps(b_d, ensure_ascii=False)[:160]))

        st_dp, b_dp = _http(base, "/api/workshop/%s/dispatch" % wid,
                            {"squad_id": "fe", "title": "小队 任务", "goal": "x", "accept": "x"})
        results = (b_dp or {}).get("results") or []
        check("④ 对 小队 派活 ⇒ 每个工位各一条任务",
              st_dp == 200 and (b_dp or {}).get("squad_id") == "fe"
              and len(results) == 1 and all(r.get("ok") for r in results),
              "HTTP %s %s" % (st_dp, json.dumps(b_dp, ensure_ascii=False)[:260]))

        ledger_p = ddir / "task_ledger.json"
        raw = ledger_p.read_text(encoding="utf-8") if ledger_p.is_file() else "{}"
        check("⑤ 账本不新建（键里没有 squad_id）",
              '"squad_id"' not in raw and ("T-" in raw or "task_seq" in raw),
              raw[:200])

        st_br, b_br = _http(base, "/api/workshop/%s/squads/fe/review" % wid, {"result": "接受"})
        check("⑥ 没有「整 小队 验收」（该路径不存在）",
              st_br in (404, 405), "HTTP %s %s" % (st_br, json.dumps(b_br, ensure_ascii=False)[:120]))

        st7, b7 = _http(base, "/api/workshop/%s/squads" % wid)
        check("⑦ 三级视图（workshop→小队→slot，未分组工位列出）",
              st7 == 200 and (b7 or {}).get("levels") == ["workshop", "squad", "slot"]
              and [x.get("member_seq") for x in ((b7 or {}).get("workshop") or {})
                   .get("loose_slots", [])] == [seq2],
              json.dumps({k: (b7 or {}).get(k) for k in ("levels",)}, ensure_ascii=False)
              + " loose=%r" % ((b7 or {}).get("workshop") or {}).get("loose_slots"))
        # ⑧ 拆分建议（只给事实与理由，不推荐）
        st_sp, b_sp = _http(base, "/api/workshop/%s/split-advice" % wid)
        check("⑧ 拆分建议：给三条件与理由（不替你决定）",
              st_sp == 200 and (b_sp or {}).get("advice") in ("keep", "consider_split")
              and isinstance((b_sp or {}).get("conditions"), dict)
              and bool((b_sp or {}).get("reasons"))
              and "不替你决定" in str((b_sp or {}).get("note")),
              "HTTP %s %s" % (st_sp, json.dumps(b_sp, ensure_ascii=False)[:200]))

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
