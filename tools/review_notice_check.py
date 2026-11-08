#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""判据：**验收结果通知到成员**（`POST …/tasks/{seq}/review` → 成员 `_notice.md`）。

真跑：临时实例（换端口 + 换 `AC_DATA_DIR`）+ 真工作间 + 真名单 + 真派活 + 真验收。

| # | 格 | 断言 |
|---|---|---|
| ① | 平台活着 | `GET /api/status` 200 |
| ② | 返工 ⇒ 成员收到通知（写进他的 `_notice.md`） | 文件里有任务号与"改"字 |
| ③ | 通知账记了 `written`（`ref=review:T-00x`） | 通知账里有这条 |
| ④ | 通知带组长原话与验收人 | 正文含 note 与署名 |
| ⑤ | 通过 ⇒ 也通知（换了措辞） | 文件里出现"验收通过" |
| ⑥ | 响应里有 `notify` 字段（不假成功） | `notify.written` 为真且带 `file` |

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
    tmpdir = tempfile.mkdtemp(prefix="ac_rvn_")
    base = "http://127.0.0.1:%d" % port
    env = dict(os.environ)
    env.update({"AC_PORT": str(port), "AC_DATA_DIR": tmpdir, "PYTHONIOENCODING": "utf-8",
                "AC_ARTIFACT_WATCH_SEC": "3600"})
    srv = subprocess.Popen([PY, "-m", "agent_community.platform.server"], cwd=str(ROOT), env=env,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print("判据：验收结果通知到成员（review → _notice.md）")
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
        ws_root = ddir / "ws_rvn"
        for d in (ws_root / "_lead", ws_root / "前端组", ws_root / S1 / "任务",
                  ws_root / S1 / "产物"):
            d.mkdir(parents=True, exist_ok=True)
        (ws_root / "前端组" / "分工.md").write_text("- 版本: 1\n", encoding="utf-8")
        (ws_root / "_lead" / "建模.md").write_text("# 建模（示例）\n", encoding="utf-8")
        _st, made = _http(base, "/api/workshop", {"name": "验收通知判据间（示例）",
                                                  "hall_content": "x", "workshop_path": str(ws_root)})
        wid = str((made or {}).get("workshop_id") or "")
        mk = _http(base, "/api/mind/A-01", {})[1] or {}
        _http(base, "/api/workshop/%s/manifest" % wid,
              {"mind_path": str(mk.get("mind_path") or ""), "entry_points": ["_lead/建模.md"]})
        txt = ("#回执 v1\n类型: 名单\n工作间: %s\n成员: 组长\n凭据:\n序号: 23\n---\n" % wid
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
              {"member_seq": seq, "title": "验收通知夹具", "goal": "x", "accept": "x"})

        notice = ws_root / S1 / "_notice.md"
        before = notice.read_text(encoding="utf-8") if notice.is_file() else ""

        st2, b2 = _http(base, "/api/workshop/%s/tasks/1/review" % wid,
                        {"result": "返工", "evidence_level": "L0", "note": "按钮点不动，补个用例"})
        after = notice.read_text(encoding="utf-8") if notice.is_file() else ""
        added = after[len(before):] if after.startswith(before) else after
        check("② 返工 ⇒ 成员收到通知（写进他的 _notice.md）",
              st2 == 200 and "T-001" in added and "返工" in added,
              "added=%r" % added[-140:])

        _st_lg, b_lg = _http(base, "/api/workshop/%s/notice-log" % wid)
        rows = (b_lg or {}).get("items") or []
        check("③ 通知账记了 written（ref=review:T-001）",
              any(x.get("result") == "written" and str(x.get("ref") or "") == "review:T-001"
                  for x in rows),
              "rows=%r" % json.dumps([{k: x.get(k) for k in ("result", "ref")} for x in rows],
                                     ensure_ascii=False)[:200])

        check("④ 通知带组长原话与验收人",
              "按钮点不动" in added and ("user" in added or "验收人" in added),
              "added=%r" % added[-160:])

        # ⑤⑥ 换一条**新任务**验另一种结论（返工后那条要等成员重交才能再验收 —— 这是状态机的正确行为）
        _http(base, "/api/workshop/%s/dispatch" % wid,
              {"member_seq": seq, "title": "验收通知夹具2", "goal": "x", "accept": "x"})
        before2 = notice.read_text(encoding="utf-8") if notice.is_file() else ""
        st6, b6 = _http(base, "/api/workshop/%s/tasks/2/review" % wid,
                        {"result": "拒收", "evidence_level": "L0", "note": "方向不对"})
        after2 = notice.read_text(encoding="utf-8") if notice.is_file() else ""
        added2 = after2[len(before2):] if after2.startswith(before2) else after2
        check("⑤ 另一种结论（拒收）也通知到，措辞跟着变",
              st6 == 200 and "拒收通知" in added2 and "T-002" in added2,
              "st=%s added=%r" % (st6, added2[-140:]))

        nt = (b6 or {}).get("notify") or {}
        check("⑥ 响应里有 notify 字段（written + file，不假成功）",
              bool(nt.get("written")) and bool(nt.get("file")),
              "notify=%r" % json.dumps(nt, ensure_ascii=False)[:200])

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
