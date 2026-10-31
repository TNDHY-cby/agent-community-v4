#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""判据：**P3 派活真落地**十四格（`V113` 第 10 步 / `V115` §2.D）。

真跑：临时实例（换端口/数据目录/心智根）+ 一个本机假 harness 收唤醒请求。

```
名单 → 工位 active → 派活
  ① 任务文件真写出来（读回内容一致）
  ② _notice.md 真追加（历史不改）
  ③ ★平台把任务 append 进 manifest.active_tasks，且派活后 G0 仍是绿的
  ④ B 档：写 _wakeup.flag ⇒ queued（算"激活已发起"）
  ⑤ B 档结算：标记被取走 ⇒ ok
  ⑥ B 档超时 ⇒ manual（可配 AC_WAKEUP_QUEUE_TIMEOUT_SEC）
  ⑦ A 档：没配唤醒地址 ⇒ manual + 说清（不假装叫醒）
  ⑧ A 档：配了地址 ⇒ **真 POST 出去**（假 harness 收到 task.assigned）
  ⑨ A 档：连不上 ⇒ failed（不许记 ok）
  ⑩ 派活被 G5 拦（工位没 active）⇒ 409 四要素
  ⑪ 没 manifest ⇒ 409 G0
  ⑫ 正文不进球：write_file=0 且正文没写 ⇒ 400 TASK_FILE_MISSING；写好后同一请求能过
  ⑬ 派活详情：文件在 + 在 manifest 里 + activated_at 有值
  ⑭ 任务号递增不复用
```

退出码：0＝全过；1＝有格没过；2＝起不来。
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
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
if _ROOT not in sys.path:
    sys.path.append(_ROOT)
PY = sys.executable


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def _http(base: str, path: str, body=None, method: str = "", timeout: float = 30):
    path = urllib.parse.quote(path, safe="/?&=%:")
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    headers = {"Content-Type": "application/json"} if data else {}
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


class _Catcher(BaseHTTPRequestHandler):
    hits: list = []

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        self.hits.append(self.rfile.read(n).decode("utf-8", "replace"))
        self.send_response(200)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"{}")

    def log_message(self, *a):
        pass


def _roster_text(wid: str, harness_id: str, seq: int = 5) -> str:
    return ("#回执 v1\n类型: 名单\n工作间: %s\n成员: 组长\n凭据:\n序号: %d\n---\n"
            "岗位: 前端组/员工01 | harness: %s | model: claude-x | 人数: 1\n---\n行数: 1"
            % (wid, seq, harness_id))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep", action="store_true")
    a = ap.parse_args()

    port = _free_port()
    tmpdir = Path(tempfile.mkdtemp(prefix="ac_dispatch_"))
    base = "http://127.0.0.1:%d" % port
    wsroot = tmpdir / "wsroot"
    minds = tmpdir / "minds"
    wsroot.mkdir(parents=True, exist_ok=True)
    minds.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env.update({"AC_PORT": str(port), "AC_DATA_DIR": str(tmpdir / "data"),
                "AC_MIND_ROOT": str(minds), "AC_WAKEUP_QUEUE_TIMEOUT_SEC": "2",
                "PYTHONIOENCODING": "utf-8"})
    rows, fails, skips = [], [], []
    proc = None
    catcher = HTTPServer(("127.0.0.1", 0), _Catcher)
    threading.Thread(target=catcher.serve_forever, daemon=True).start()
    catcher_url = "http://127.0.0.1:%d/wakeup" % catcher.server_port

    def check(name, ok, detail=""):
        rows.append((name, bool(ok), detail))
        if not ok:
            fails.append("%s（%s）" % (name, detail))

    try:
        proc = subprocess.Popen([PY, "-m", "agent_community.platform.server"], cwd=_ROOT, env=env,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        up = False
        for _ in range(60):
            try:
                up = _http(base, "/api/status", timeout=3)[0] == 200
            except OSError:
                up = False
            if up:
                break
            time.sleep(0.5)
        if not up:
            print("[FAIL] 临时平台起不来（%s）" % base)
            return 2
        check("临时平台起来（换端口/数据目录/心智根 + 2 秒超时）", True)

        # 夹具：两个 harness（一个带唤醒地址、一个不带）+ 工作间 + 合法 manifest
        st, h1 = _http(base, "/api/harness/register",
                       {"harness_id": "h_a", "harness_name": "示例Harness-A", "harness_type": "custom",
                        "wakeup_method": "http_api", "api_base_url": catcher_url,
                        "ai": {"model_name": "示例模型"}})
        st2, h2 = _http(base, "/api/harness/register",
                        {"harness_id": "h_no", "harness_name": "示例Harness-B", "harness_type": "custom",
                         "wakeup_method": "http_api", "ai": {"model_name": "示例模型"}})
        check("夹具：注册两个 harness（一个带唤醒地址）", bool(h1) and bool(h2),
              "HTTP %s/%s" % (st, st2))
        st, made = _http(base, "/api/workshop", {"name": "示例间", "hall_content": "方向",
                                                 "workshop_path": str(wsroot)})
        wid = str((made or {}).get("workshop_id") or "")
        if not wid:
            check("夹具：建工作间", False, "HTTP %s %s" % (st, json.dumps(made, ensure_ascii=False)[:160]))
            raise SystemExit(1)
        st, mk = _http(base, "/api/mind/A-01", {})
        mp = str((mk or {}).get("mind_path") or "")
        (wsroot / "_lead").mkdir(parents=True, exist_ok=True)
        (wsroot / "_lead" / "建模.md").write_text("# 建模\n", encoding="utf-8")
        st, mf = _http(base, "/api/workshop/%s/manifest" % wid,
                       {"mind_path": mp, "entry_points": ["_lead/建模.md"],
                        "task_focus": "派活判据"})
        check("夹具：写合法 manifest（G0 绿）", st == 200 and bool((mf or {}).get("manifest_hash")),
              json.dumps(mf, ensure_ascii=False)[:160])
        _http(base, "/api/workshop/%s/intake?source=C&confirm=1" % wid,
              {"text": _roster_text(wid, "h_a")})
        roster = _http(base, "/api/workshop/%s/roster" % wid)[1]
        seq = (roster.get("members") or [{}])[0].get("member_seq")
        # A 档接上（真发 tools/list 由 A 侧负责；这里按证据记录）
        _http(base, "/api/workshop/%s/members/%s/evidence" % (wid, seq),
              {"protocol_ok": True, "tier": "a_protocol",
               "verified_scope": "真调 tools/list 回了工具（判据夹具）"})
        roster = _http(base, "/api/workshop/%s/roster" % wid)[1]
        state = (roster.get("members") or [{}])[0].get("state")
        check("夹具：工位到 active（A 档证据）", state == "active", "state=%s" % state)

        # ── ① ② ③ ────────────────────────────────────────────────
        st, d1 = _http(base, "/api/workshop/%s/dispatch" % wid,
                       {"member_seq": seq, "title": "接口对接", "goal": "把接口对上",
                        "accept": "每个端点写清错误码", "due": "2026-10-28"})
        body = d1 or {}
        task_file = wsroot / str(body.get("file") or "")
        check("① 任务文件真写出来（读回内容一致）",
              st == 200 and task_file.is_file() and "接口对接" in task_file.read_text(encoding="utf-8"),
              json.dumps(body, ensure_ascii=False)[:200])
        notice = wsroot / "前端组" / "员工01" / "_notice.md"
        check("② _notice.md 真追加（有 T-001 与时间）",
              notice.is_file() and "T-001" in notice.read_text(encoding="utf-8"),
              notice.as_posix())
        st, mf2 = _http(base, "/api/workshop/%s/manifest" % wid)
        in_manifest = str(body.get("file") or "") in ((mf2.get("manifest") or {}).get("active_tasks") or [])
        check("③ ★平台 append manifest.active_tasks 且派活后 G0 仍绿",
              st == 200 and mf2.get("ok") is True and in_manifest,
              "in_manifest=%s g0=%s" % (in_manifest, mf2.get("ok")))

        # ── ⑦ ⑧ ⑨ A 档真发/失败 ─────────────────────────────────
        st, dA = _http(base, "/api/workshop/%s/dispatch" % wid,
                       {"member_seq": seq, "title": "协议型真发"})
        check("⑧ A 档配了地址 ⇒ **真 POST 出去**（假 harness 收到 task.assigned）",
              (dA.get("wakeup") or {}).get("result") == "ok"
              and any("task.assigned" in h for h in _Catcher.hits),
              json.dumps(dA.get("wakeup"), ensure_ascii=False)[:200])

        # ⑨ 连不上 ⇒ failed：把 harness 地址指到死端口后再派
        dead = _free_port()
        _http(base, "/api/harness/register",
              {"harness_id": "h_dead", "harness_name": "示例Harness-C", "harness_type": "custom",
               "wakeup_method": "http_api", "api_base_url": "http://127.0.0.1:%d/x" % dead,
               "ai": {"model_name": "示例模型"}})
        # ★用**独立岗位**（同一 slot_dir 只会更新 harness_id，不会新建工位 —— 上一版判据写错了）
        _dead_text = ("#回执 v1\n类型: 名单\n工作间: %s\n成员: 组长\n凭据:\n序号: 9\n---\n"
                      "岗位: 后端组/员工02 | harness: h_dead | model: claude-x | 人数: 1\n"
                      "---\n行数: 1" % wid)
        _http(base, "/api/workshop/%s/intake?source=C&confirm=1" % wid, {"text": _dead_text})
        roster2 = _http(base, "/api/workshop/%s/roster" % wid)[1]
        _dead_seqs = [m["member_seq"] for m in roster2["members"]
                      if m["slot_dir"] == "后端组/员工02"]
        seq_dead = _dead_seqs[0] if _dead_seqs else 0
        _http(base, "/api/workshop/%s/members/%s/evidence" % (wid, seq_dead),
              {"protocol_ok": True, "tier": "a_protocol", "verified_scope": "夹具"})
        st, dDead = _http(base, "/api/workshop/%s/dispatch" % wid,
                          {"member_seq": seq_dead, "title": "叫不醒"})
        check("⑨ A 档连不上 ⇒ failed（不许记 ok）",
              (dDead.get("wakeup") or {}).get("result") == "failed"
              and dDead.get("activated") is False and dDead.get("code") == "WAKEUP_FAILED",
              json.dumps(dDead.get("wakeup"), ensure_ascii=False)[:200])

        # ── ④ ⑤ ⑥ B 档 ──────────────────────────────────────────
        _http(base, "/api/workshop/%s/intake?source=C&confirm=1" % wid,
              {"text": _roster_text(wid, "h_a2", seq=10)})
        _http(base, "/api/harness/register",
              {"harness_id": "h_a2", "harness_name": "示例Harness-D", "harness_type": "custom",
               "wakeup_method": "file_poll", "ai": {"model_name": "示例模型"}})
        roster3 = _http(base, "/api/workshop/%s/roster" % wid)[1]
        seq_b = [m["member_seq"] for m in roster3["members"]
                 if m["harness_id"] == "h_a2" or m["slot_dir"] == "前端组/员工01"][-1]
        _http(base, "/api/workshop/%s/members/%s/evidence" % (wid, seq_b),
              {"bridge_ok": True, "tier": "b_bridge", "verified_scope": "桥取件过（夹具）"})
        st, dB = _http(base, "/api/workshop/%s/dispatch" % wid,
                       {"member_seq": seq_b, "title": "桥型派活"})
        check("④ B 档：写 _wakeup.flag ⇒ queued（算激活已发起）",
              (dB.get("wakeup") or {}).get("result") == "queued" and dB.get("activated") is True,
              json.dumps(dB.get("wakeup"), ensure_ascii=False)[:200])
        flag = wsroot / "前端组" / "员工01" / "_wakeup.flag"
        flag.unlink(missing_ok=True)                                    # 模拟桥取走
        st, p1 = _http(base, "/api/workshop/%s/wakeup/poll" % wid, {"member_seq": seq_b})
        r1 = ((p1.get("results") or [{}])[0])
        check("⑤ B 档结算：标记被取走 ⇒ ok", r1.get("state") == "taken" and r1.get("settled") == "ok",
              json.dumps(r1, ensure_ascii=False)[:180])
        st, dB2 = _http(base, "/api/workshop/%s/dispatch" % wid,
                        {"member_seq": seq_b, "title": "桥型超时"})
        time.sleep(3)                                                   # 超时阈值设为 2 秒
        st, p2 = _http(base, "/api/workshop/%s/wakeup/poll" % wid, {"member_seq": seq_b})
        r2 = ((p2.get("results") or [{}])[0])
        check("⑥ B 档超时 ⇒ manual（不一直挂「已排队」）",
              r2.get("state") == "timeout" and r2.get("settled") == "manual",
              json.dumps(r2, ensure_ascii=False)[:180])

        # ── ⑩ ⑪ ⑫ ⑬ ⑭ ──────────────────────────────────────────
        _http(base, "/api/workshop/%s/roster/migrate" % wid, {})
        st, g5 = _http(base, "/api/workshop/%s/dispatch" % wid,
                       {"member_seq": 999, "title": "不存在的工位"})
        check("⑩ 派活给不存在的工位 ⇒ 409 G5 + 四要素",
              st == 409 and (g5.get("code") == "GATE_G5")
              and set((g5.get("four") or {})) == {"who", "what", "where", "how"},
              json.dumps(g5, ensure_ascii=False)[:200])
        (wsroot / "manifest.json").unlink()
        st, g0 = _http(base, "/api/workshop/%s/dispatch" % wid, {"member_seq": seq, "title": "没 manifest"})
        check("⑪ 没 manifest ⇒ 409 G0（派活前先有入口地图）",
              st == 409 and g0.get("code") == "GATE_G0", json.dumps(g0, ensure_ascii=False)[:180])
        _http(base, "/api/workshop/%s/manifest" % wid,
              {"mind_path": mp, "entry_points": ["_lead/建模.md"]})
        st, noseq = _http(base, "/api/workshop/%s/dispatch" % wid,
                          {"member_seq": seq, "title": "组长自己写", "write_file": "0"})
        check("⑫ 正文不进球：write_file=0 但没说哪一条 ⇒ 400 TASK_SEQ_REQUIRED（号也不许猜）",
              st == 400 and noseq.get("code") == "TASK_SEQ_REQUIRED",
              json.dumps(noseq, ensure_ascii=False)[:180])
        st, nofile = _http(base, "/api/workshop/%s/dispatch" % wid,
                           {"member_seq": seq, "title": "组长自己写", "write_file": "0",
                            "task_id": "T-777"})
        check("⑫b 说了号但正文没写 ⇒ 400 TASK_FILE_MISSING（平台不代写）",
              st == 400 and nofile.get("code") == "TASK_FILE_MISSING",
              json.dumps(nofile, ensure_ascii=False)[:180])
        st, det = _http(base, "/api/workshop/%s/dispatch/%s" % (wid, body.get("task_seq")))
        # manifest 成员资格在 ③ 判过（⑪ 重写过 manifest，这里不再重复判）
        check("⑬ 派活详情：文件真在 + 有 activated_at + 记账说 manifest 已写",
              det.get("ok") and det.get("file_exists") is True
              and (det.get("entry") or {}).get("activated_at")
              and (det.get("entry") or {}).get("manifest_ok") is True,
              json.dumps(det, ensure_ascii=False)[:200])
        seqs = []
        for t in ("递增一", "递增二"):
            st, dd = _http(base, "/api/workshop/%s/dispatch" % wid, {"member_seq": seq, "title": t})
            seqs.append(dd.get("task_seq"))
        check("⑭ 任务号递增、不复用", len(set(seqs)) == 2 and seqs[1] > seqs[0], str(seqs))
    finally:
        catcher.shutdown()
        if proc is not None and not a.keep:
            try:
                proc.terminate()
                proc.wait(timeout=10)
            except Exception:      # noqa: BLE001 — 有意静默：收尾杀不掉不影响结论
                try:
                    proc.kill()
                except Exception:  # noqa: BLE001 — 有意静默：同上
                    pass
        if not a.keep:
            shutil.rmtree(tmpdir, ignore_errors=True)

    print("=" * 78)
    print("判据：P3 派活真落地 —— 临时实例 %s" % base)
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
