#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""判据：**通知投递账（V134 ⑧）＋ 档位对照表（⑨）＋ 资源库存在性核对（⑩）**。

真跑：临时实例（换端口 + 换 `AC_DATA_DIR`）+ 真工作间 + 真写文件。

| # | 格 | 断言 |
|---|---|---|
| ① | 平台活着 | `GET /api/status` 200 |
| ② | 产物转组长 ⇒ 通知账有 `written` | `notice-log` 里 `channel=notice_file`、`result=written` |
| ③ | `written` 不许说成已送达 | `labels.written` 里明写"不知道/看没看"，且 `delivered` 计数为 0 |
| ④ | AILP 点名 ⇒ 也进账 | `ref` 是那条 msg_id |
| ⑤ | 档位矩阵 4 档 × 4 轴齐全 | `tier-matrix` 的 `gaps` 为空 |
| ⑥ | 每档每格都有人话 | 每格 `code` 非空且 `meaning` 不是"未定义" |
| ⑦ | 成员按档位出计划 | `tier-plan` 每成员四格都有 code（读矩阵，不另写一套） |
| ⑧ | 资源库存在性真核对 | 登记存在的 ⇒ `exists=true`；登记不存在的 ⇒ `exists=false` + `why` |

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
    tmpdir = tempfile.mkdtemp(prefix="ac_nt_")
    base = "http://127.0.0.1:%d" % port
    env = dict(os.environ)
    env.update({"AC_PORT": str(port), "AC_DATA_DIR": tmpdir, "PYTHONIOENCODING": "utf-8",
                "AC_ARTIFACT_WATCH_SEC": "3600"})
    srv = subprocess.Popen([PY, "-m", "agent_community.platform.server"], cwd=str(ROOT), env=env,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print("判据：通知账 / 档位矩阵 / 资源库存在性（V134 ⑧⑨⑩ · 真跑）")
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

        ws_root = ddir / "ws_nt"
        for d in (ws_root / "_lead", ws_root / S1 / "产物", ws_root / S2 / "产物",
                  ws_root / S1 / "任务", ws_root / S2 / "任务"):
            d.mkdir(parents=True, exist_ok=True)
        (ws_root / "_lead" / "建模.md").write_text("# 建模（示例）\n", encoding="utf-8")
        (ws_root / "前端组" / "分工.md").write_text("- 版本: 1\n", encoding="utf-8")
        _st, made = _http(base, "/api/workshop", {"name": "通知档位判据间（示例）",
                                                  "hall_content": "x",
                                                  "workshop_path": str(ws_root)})
        wid = str((made or {}).get("workshop_id") or "")
        mk = _http(base, "/api/mind/A-01", {})[1] or {}
        _http(base, "/api/workshop/%s/manifest" % wid,
              {"mind_path": str(mk.get("mind_path") or ""), "entry_points": ["_lead/建模.md"]})
        txt = ("#回执 v1\n类型: 名单\n工作间: %s\n成员: 组长\n凭据:\n序号: 9\n---\n" % wid
               + "岗位: %s | harness: 示例Harness-M1 | model: 示例模型 | 人数: 1\n" % S1
               + "岗位: %s | harness: 示例Harness-M2 | model: 示例模型 | 人数: 1\n" % S2
               + "岗位: _lead | harness: 示例Harness-Lead | model: 示例模型 | 人数: 1\n"
               + "---\n行数: 3")
        _http(base, "/api/workshop/%s/intake?source=C&confirm=1" % wid, {"text": txt})
        roster = (_http(base, "/api/workshop/%s/roster" % wid)[1] or {})
        seqs = {str((m or {}).get("slot_dir") or ""): int((m or {}).get("member_seq") or 0)
                for m in (roster.get("members") or [])}
        _http(base, "/api/workshop/%s/members/%s/evidence" % (wid, seqs.get(S1)),
              {"protocol_ok": True, "verified_scope": "判据", "tier": "a_protocol"})
        _http(base, "/api/workshop/%s/members/%s/evidence" % (wid, seqs.get(S2)),
              {"paste_seen": True, "press_ok": True, "rename_seen": True,
               "verified_scope": "判据", "tier": "c_visual"})

        # ② 产物转组长 ⇒ 通知账
        _http(base, "/api/workshop/%s/scan-inbox" % wid, {})
        (ws_root / S1 / "产物" / "交付.md").write_text("真产物", encoding="utf-8")
        _http(base, "/api/workshop/%s/scan-inbox" % wid, {})
        st2, b2 = _http(base, "/api/workshop/%s/notice-log" % wid)
        items = (b2 or {}).get("items") or []
        written = [x for x in items if x.get("result") == "written"
                   and x.get("channel") == "notice_file"]
        check("② 产物转组长 ⇒ 通知账里有 written",
              st2 == 200 and bool(written),
              "HTTP %s items=%r" % (st2, json.dumps(items[-2:], ensure_ascii=False)[:220]))

        # ③ written 不许说成已送达
        labels = (b2 or {}).get("labels") or {}
        counts = (b2 or {}).get("counts") or {}
        check("③ written 明写「不知道对方看没看」且 delivered 计数为 0",
              "不知道" in str(labels.get("written") or "")
              and int(counts.get("delivered", 0)) == 0,
              "labels=%r counts=%r" % (labels, counts))

        # ④ AILP 点名 ⇒ 也进账
        st_t, b_t = _http(base, "/api/workshop/%s/ailp" % wid, {
            "ailp_version": "2.0", "msg_id": "msg-nt-1", "msg_type": "PROPOSE",
            "sender": "员工01", "target": "组长", "manifest_ref": "_lead/建模.md",
            "to": "lead",
            "payload": {"proposal": "先做接口", "confidence": 0.8,
                        "basis_ids": ["_lead/建模.md"]}})
        st4, b4 = _http(base, "/api/workshop/%s/notice-log" % wid)
        refs = [x.get("ref") for x in ((b4 or {}).get("items") or [])]
        check("④ AILP 点名 ⇒ 通知账里能按 msg_id 找到",
              st_t == 200 and "msg-nt-1" in refs,
              "ailp=%s refs=%r" % (st_t, refs))

        # ⑤⑥ 档位矩阵
        st5, b5 = _http(base, "/api/tier-matrix")
        tiers = (b5 or {}).get("tiers") or []
        check("⑤ 档位矩阵：4 档 × 4 轴齐全（gaps 为空）",
              st5 == 200 and len(tiers) >= 4 and not (b5 or {}).get("gaps")
              and all(set((t or {}).get("axes") or {}) == {"reach", "notice", "artifact", "review"}
                      for t in tiers),
              "HTTP %s tiers=%d gaps=%r" % (st5, len(tiers), (b5 or {}).get("gaps")))
        bad = [{"tier": t.get("tier"), "axis": a}
               for t in tiers for a, v in ((t or {}).get("axes") or {}).items()
               if not (v or {}).get("code") or "未定义" in str((v or {}).get("meaning"))]
        check("⑥ 每档每格都有行为码与人话（没有'未定义'）", not bad, "bad=%r" % bad[:4])

        # ⑦ 成员按档位出计划
        st7, b7 = _http(base, "/api/workshop/%s/tier-plan" % wid)
        mrows = (b7 or {}).get("members") or []
        ok7 = st7 == 200 and mrows and all(
            all((v or {}).get("code") for v in ((r or {}).get("axes") or {}).values())
            for r in mrows)
        check("⑦ 成员按档位出四格计划（读矩阵，不另写一套）", bool(ok7),
              "HTTP %s members=%d" % (st7, len(mrows)))

        # ⑧ 资源库存在性真核对
        good = ws_root / S1 / "产物" / "交付.md"
        _http(base, "/api/workshop/%s/resources" % wid,
              {"name": "交付.md", "kind": "file", "path": good.as_posix()})
        _http(base, "/api/workshop/%s/resources" % wid,
              {"name": "不存在.md", "kind": "file",
               "path": (ws_root / "没有这个文件.md").as_posix()})
        st8, b8 = _http(base, "/api/workshop/%s/resources" % wid)
        rs = (b8 or {}).get("resources") or []
        got_good = next((r for r in rs if r.get("name") == "交付.md"), {})
        got_bad = next((r for r in rs if r.get("name") == "不存在.md"), {})
        check("⑧ 资源库存在性真核对（在＝true / 不在＝false + why）",
              st8 == 200 and got_good.get("exists") is True
              and got_bad.get("exists") is False and bool(got_bad.get("why")),
              "good=%r bad=%r" % (got_good.get("exists"), got_bad.get("exists")))
        # ⑨⑩ 收件事件登记进资源库（V134 ⑩ 的最后一块）：登记后 resources 有它且 exists=true；重复扫不重复登记
        # 先派一条活：事件要能"绑任务"，⑪⑫ 才有东西可看（V134 ⑤）
        _http(base, "/api/workshop/%s/dispatch" % wid,
              {"member_seq": seqs.get(S1), "title": "事件绑任务夹具", "goal": "x", "accept": "x"})
        (ws_root / S1 / "产物" / "交付2.md").write_text("真产物2", encoding="utf-8")
        st9, b9 = _http(base, "/api/workshop/%s/scan-inbox" % wid, {"register": True})
        reg = (b9 or {}).get("registered") or {}
        _st_r, b_r = _http(base, "/api/workshop/%s/resources" % wid)
        inbox_rows = [r for r in ((b_r or {}).get("resources") or [])
                      if str(r.get("source") or "") == "inbox"]
        check("⑨ 收件事件能登记进资源库（带 sha256 / source=inbox，且 exists=true）",
              st9 == 200 and bool(reg.get("added"))
              and any(r.get("name") == "交付2.md" and r.get("exists") is True
                      and r.get("sha256") for r in inbox_rows),
              "registered=%r inbox_rows=%d" % (json.dumps(reg, ensure_ascii=False)[:200],
                                               len(inbox_rows)))

        n_before = len(inbox_rows)
        st10b, b10b = _http(base, "/api/workshop/%s/scan-inbox" % wid, {"register": True})
        reg2 = (b10b or {}).get("registered") or {}
        _st_r2, b_r2 = _http(base, "/api/workshop/%s/resources" % wid)
        n_after = len([r for r in ((b_r2 or {}).get("resources") or [])
                       if str(r.get("source") or "") == "inbox"])
        check("⑩ 重复扫不重复登记（同 path+sha256 幂等）",
              st10b == 200 and n_after == n_before and not (reg2.get("added") or []),
              "before=%d after=%d reg2=%r" % (n_before, n_after,
                                              json.dumps(reg2, ensure_ascii=False)[:160]))

        # ⑪⑫ 收件事件 ↔ 任务 ↔ 验收 一处可见（V134 ⑤：原来两套口径）
        st11, b11 = _http(base, "/api/workshop/%s/inbox-events?limit=20" % wid)
        evs = [x for x in ((b11 or {}).get("events") or []) if x.get("file") == "交付2.md"]
        ev = evs[0] if evs else {}
        check("⑪ 事件里能看到它进了哪条任务（task_id/task_state/未验收）",
              st11 == 200 and bool(ev) and bool(ev.get("task_id"))
              and str(ev.get("task_state") or "") in ("issued", "working", "delivered")
              and ev.get("task_reviewed_by") in ("", None),
              "event=%r" % json.dumps({k: ev.get(k) for k in
                                       ("task_id", "task_state", "task_reviewed_by",
                                        "task_tier")}, ensure_ascii=False))

        _st_rv, b_rv = _http(base, "/api/workshop/%s/tasks/%s/review" % (wid, 1),
                             {"result": "通过", "evidence_level": "L0"})
        st12, b12 = _http(base, "/api/workshop/%s/inbox-events?limit=20" % wid)
        evs2 = [x for x in ((b12 or {}).get("events") or []) if x.get("file") == "交付2.md"]
        ev2 = evs2[0] if evs2 else {}
        check("⑫ 验收后同一事件显示谁验收的（task_reviewed_by / state）",
              _st_rv == 200 and bool(ev2) and str(ev2.get("task_state") or "") == "accepted"
              and str(ev2.get("task_reviewed_by") or "") in ("user", "_lead", "platform-review"),
              "review=%s state=%r by=%r resp=%r" % (_st_rv, ev2.get("task_state"),
                                                ev2.get("task_reviewed_by"),
                                                json.dumps(b_rv, ensure_ascii=False)[:160]))

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
