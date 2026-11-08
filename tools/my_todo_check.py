#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""判据：**成员自己该干什么**（`GET …/members/{seq}/my-todo`）。

真跑：临时实例（换端口 + 换 `AC_DATA_DIR`）+ 真工作间 + 真名单 + 真派活 + 真返工。

| # | 格 | 断言 |
|---|---|---|
| ① | 平台活着 | `GET /api/status` 200 |
| ② | 派活后 ⇒ 该交回执（`kind=do` + 端点） | `next.what` 含"回执"、`how` 含 `task-result` |
| ③ | 返工后 ⇒ 改成"改完再交"，且 `next` 先挑它 | `next.task_id` 是返工那条 |
| ④ | 产物到了 ⇒ `wait`（别重复交） | 该任务 `kind=wait` |
| ⑤ | 给我的通知能看到（review 返工写的那条） | `notices` 非空且含"返工" |
| ⑥ | 不存在的成员 / 工作间 ⇒ 404 | `NO_SUCH_MEMBER` / `NO_SUCH_WORKSHOP` |
| ⑦ | 不排分 | 响应里没有 `score`/`rank` |

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
    tmpdir = tempfile.mkdtemp(prefix="ac_mtodo_")
    base = "http://127.0.0.1:%d" % port
    env = dict(os.environ)
    env.update({"AC_PORT": str(port), "AC_DATA_DIR": tmpdir, "PYTHONIOENCODING": "utf-8",
                "AC_ARTIFACT_WATCH_SEC": "3600"})
    srv = subprocess.Popen([PY, "-m", "agent_community.platform.server"], cwd=str(ROOT), env=env,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print("判据：成员自己该干什么（my-todo · 真跑）")
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
        ws_root = ddir / "ws_mt"
        for d in (ws_root / "_lead", ws_root / "前端组", ws_root / S1 / "任务",
                  ws_root / S1 / "产物"):
            d.mkdir(parents=True, exist_ok=True)
        (ws_root / "前端组" / "分工.md").write_text("- 版本: 1\n", encoding="utf-8")
        (ws_root / "_lead" / "建模.md").write_text("# 建模（示例）\n", encoding="utf-8")
        _st, made = _http(base, "/api/workshop", {"name": "成员待办判据间（示例）",
                                                  "hall_content": "x", "workshop_path": str(ws_root)})
        wid = str((made or {}).get("workshop_id") or "")
        mk = _http(base, "/api/mind/A-01", {})[1] or {}
        _http(base, "/api/workshop/%s/manifest" % wid,
              {"mind_path": str(mk.get("mind_path") or ""), "entry_points": ["_lead/建模.md"]})
        txt = ("#回执 v1\n类型: 名单\n工作间: %s\n成员: 组长\n凭据:\n序号: 25\n---\n" % wid
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
              {"member_seq": seq, "title": "成员待办夹具", "goal": "x", "accept": "x"})

        st2, b2 = _http(base, "/api/workshop/%s/members/%s/my-todo" % (wid, seq))
        nxt = (b2 or {}).get("next") or {}
        check("② 派活后 ⇒ 该交回执（do + 端点）",
              st2 == 200 and "回执" in str(nxt.get("what"))
              and "task-result" in str(nxt.get("how")),
              "next=%r" % json.dumps(nxt, ensure_ascii=False)[:200])

        _http(base, "/api/workshop/%s/dispatch" % wid,
              {"member_seq": seq, "title": "第二条", "goal": "x", "accept": "x"})
        _http(base, "/api/workshop/%s/tasks/2/review" % wid,
              {"result": "返工", "evidence_level": "L0", "note": "补一版说明"})
        st3, b3 = _http(base, "/api/workshop/%s/members/%s/my-todo" % (wid, seq))
        nxt3 = (b3 or {}).get("next") or {}
        check("③ 返工后 ⇒ 改成'改完再交'，且 next 先挑它",
              st3 == 200 and nxt3.get("task_id") == "T-002" and "改完再交" in str(nxt3.get("what")),
              "next=%r" % json.dumps(nxt3, ensure_ascii=False)[:200])

        # ④ 这个工位**有两条未结任务**（T-001 issued / T-002 rework）⇒ 平台**不瞎绑**产物，
        #    T-001 仍是 issued、视图照实说"该交回执"（V134 ③ 的歧义保护；"delivered ⇒ wait"
        #    这一支由 test_member_todo::test_03 与 lead/presence 那几套端到端覆盖）
        _http(base, "/api/workshop/%s/scan-inbox" % wid, {})
        (ws_root / S1 / "产物" / "交付.md").write_text("真产物", encoding="utf-8")
        _http(base, "/api/workshop/%s/scan-inbox" % wid, {})
        st4, b4 = _http(base, "/api/workshop/%s/members/%s/my-todo" % (wid, seq))
        row4 = next((r for r in ((b4 or {}).get("tasks") or [])
                     if r.get("task_id") == "T-001"), {})
        _st_ev, b_ev = _http(base, "/api/workshop/%s/inbox-events?limit=10" % wid)
        ev_tids = [str(x.get("task_id") or "") for x in ((b_ev or {}).get("events") or [])]
        check("④ 同工位两条未结任务时，产物不被瞎绑（T-001 仍 issued，视图照实说）",
              st4 == 200 and row4.get("state") == "issued" and row4.get("kind") == "do"
              and not any(ev_tids),
              "row=%r ev_tids=%r" % (json.dumps({k: row4.get(k) for k in ("task_id", "state", "kind")},
                                                ensure_ascii=False), ev_tids))

        ns = (b4 or {}).get("notices") or []
        check("⑤ 给我的通知能看到（review 返工写的那条）",
              bool(ns) and any("返工" in json.dumps(x, ensure_ascii=False) for x in ns),
              "notices=%r" % json.dumps(ns[:1], ensure_ascii=False)[:200])

        st6a, b6a = _http(base, "/api/workshop/%s/members/999/my-todo" % wid)
        st6b, b6b = _http(base, "/api/workshop/no-such-ws-1/members/1/my-todo")
        check("⑥ 不存在的成员 / 工作间 ⇒ 404",
              st6a == 404 and (b6a or {}).get("code") == "NO_SUCH_MEMBER"
              and st6b == 404 and (b6b or {}).get("code") == "NO_SUCH_WORKSHOP",
              "member=%s ws=%s" % (st6a, st6b))

        blob = json.dumps(b4, ensure_ascii=False).lower()
        check("⑦ 不排分（无 score/rank）", "score" not in blob and "rank" not in blob)
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
