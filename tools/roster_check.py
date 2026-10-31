#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""判据：**slot/member 双层 + 闸门**真机十二格（`V108` §6 / `V112`，P2 批）。

跑在临时实例上（换端口 + 换数据目录），全流程走真 HTTP：

```
名单回执 → 平台真落 slots/members（applied=true）
→ 逐条证据 → 状态阶梯（acked → entered → active，缺项就停在 acked）
→ 闸门自省（start_running 允许 / change_path 锁死且给"怎么做"）
→ 旧数据迁移（映不出来的列进 legacy_unknown，不静默丢）
```

退出码：0＝全过（或用 SKIP 如实说明）；1＝有格没过；2＝起不来。
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
import time
import urllib.error
import urllib.parse
import urllib.request
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


def _roster_text(wid: str, seq: int = 5) -> str:
    return ("#回执 v1\n类型: 名单\n工作间: %s\n成员: 组长\n凭据:\n序号: %d\n---\n"
            "岗位: 前端组/员工01 | harness: h_cursor | model: claude-x | 人数: 2\n"
            "岗位: 后端组/员工01 | harness: h_trae_cn | model: deepseek-v3 | 人数: 1\n"
            "---\n行数: 2" % (wid, seq))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep", action="store_true")
    a = ap.parse_args()

    port = _free_port()
    tmpdir = tempfile.mkdtemp(prefix="ac_roster_")
    base = "http://127.0.0.1:%d" % port
    wsroot = Path(tmpdir) / "wsroot"
    wsroot.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env.update({"AC_PORT": str(port), "AC_DATA_DIR": str(Path(tmpdir) / "data"),
                "PYTHONIOENCODING": "utf-8"})
    rows, fails, skips = [], [], []
    proc = None

    def check(name, ok, detail=""):
        rows.append((name, bool(ok), detail))
        if not ok:
            fails.append("%s（%s）" % (name, detail))

    def skip(name, why):
        rows.append((name, None, why))
        skips.append("%s（%s）" % (name, why))

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
        check("临时平台起来（换端口+换数据目录）", True)
        st, made = _http(base, "/api/workshop", {"name": "示例间", "hall_content": "方向",
                                                 "workshop_path": str(wsroot)})
        wid = str((made or {}).get("workshop_id") or "")
        check("夹具：建工作间", bool(wid), "HTTP %s" % st)
        if not wid:
            raise SystemExit(1)

        # ── 1. 名单回执 → 真落 slots/members ────────────────────────
        st, ok = _http(base, "/api/workshop/%s/intake?source=C&confirm=1" % wid,
                       {"text": _roster_text(wid)})
        check("① 名单回执真落库（applied=true，成员有工位）",
              st == 200 and ok.get("applied") is True and ok.get("result", {}).get("members_created"),
              json.dumps(ok, ensure_ascii=False)[:220])
        st, r = _http(base, "/api/workshop/%s/roster" % wid)
        slots = {s["slot_dir"]: s for s in (r.get("slots") or [])}
        members = r.get("members") or []
        check("② 双层：slot 有 headcount、member 一人一位",
              slots.get("前端组/员工01", {}).get("headcount") == 2 and len(members) == 3,
              "slots=%s members=%d" % (list(slots), len(members)))
        check("③ 主键 (workshop_id, member_seq)：seq 唯一、会话名能区分",
              len({m["member_seq"] for m in members}) == len(members)
              and len({m["session_label"] for m in members}) == len(members),
              str([m["session_label"] for m in members])[:160])
        seq_front = [m["member_seq"] for m in members if m["slot_dir"] == "前端组/员工01"]
        seq_back = [m["member_seq"] for m in members if m["slot_dir"] == "后端组/员工01"][0]

        # ── 2. 证据阶梯 ────────────────────────────────────────────
        st, one = _http(base, "/api/workshop/%s/members/%d/evidence" % (wid, seq_front[0]),
                        {"paste_seen": True})
        check("④ 只打到「已粘贴」⇒ 停在 acked 并说清还缺什么",
              one.get("state") == "acked" and one.get("need", {}).get("missing_evidence"),
              json.dumps(one, ensure_ascii=False)[:220])
        st, three = _http(base, "/api/workshop/%s/members/%d/evidence" % (wid, seq_front[0]),
                          {"press_ok": True, "rename_seen": True})
        check("⑤ 三项齐但没写覆盖范围 ⇒ 仍不许 active",
              three.get("state") in ("acked", "entered"), json.dumps(three, ensure_ascii=False)[:200])
        st, act = _http(base, "/api/workshop/%s/members/%d/evidence" % (wid, seq_front[0]),
                        {"verified_scope": "粘贴+回车+改名，未验证它能回话"})
        check("⑥ 补上 verified_scope ⇒ active（而另一个工位不受影响）",
              act.get("state") == "active", json.dumps(act, ensure_ascii=False)[:220])
        st, r2 = _http(base, "/api/workshop/%s/roster" % wid)
        states = {m["member_seq"]: m["state"] for m in (r2.get("members") or [])}
        check("⑦ 证据不串位（另一个工位还是 pending）",
              states.get(seq_front[1]) == "pending" and states.get(seq_back) == "pending",
              json.dumps(states, ensure_ascii=False))

        # ── 3. 闸门 ───────────────────────────────────────────────
        st, g1 = _http(base, "/api/workshop/%s/gate/start_running" % wid)
        check("⑧ 有一个 active ⇒ start_running 放行", g1.get("allowed") is True,
              json.dumps(g1, ensure_ascii=False)[:160])
        st, g2 = _http(base, "/api/workshop/%s/gate/dispatch?member_seq=%d" % (wid, seq_back))
        check("⑨ 给未接上的工位派活 ⇒ 拦住（四要素齐全、how 可照做）",
              g2.get("allowed") is False and set((g2.get("four") or {})) == {"who", "what", "where", "how"}
              and all((g2.get("four") or {}).values()),
              json.dumps(g2, ensure_ascii=False)[:240])
        st, g3 = _http(base, "/api/workshop/%s/gate/fill_direction" % wid)
        check("⑩ 方向空 ⇒ 闸门给「怎么做」（不是空白 409）",
              g3.get("allowed") is False and "方向" in json.dumps(g3, ensure_ascii=False),
              json.dumps(g3, ensure_ascii=False)[:200])

        # ── 4. 状态回执 + 旧数据迁移 ───────────────────────────────
        txt = ("#回执 v1\n类型: 状态\n工作间: %s\n成员: 组长\n凭据:\n序号: 6\n---\n"
               "状态: 后端组/员工01 | 新: blocked | 原因: 凭据缺失\n---\n行数: 1" % wid)
        st, st_ok = _http(base, "/api/workshop/%s/intake?source=C&confirm=1" % wid, {"text": txt})
        check("⑪ 状态回执真改 member（blocked）",
              st_ok.get("applied") is True and st_ok.get("result", {}).get("applied"),
              json.dumps(st_ok, ensure_ascii=False)[:220])
        st, mig = _http(base, "/api/workshop/%s/roster/migrate" % wid, {})
        check("⑫ 旧数据迁移：有 legacy_unknown 字段（映不出来的要列出来）",
              st == 200 and isinstance((mig or {}).get("legacy_unknown"), list),
              json.dumps(mig, ensure_ascii=False)[:200])
    finally:
        if proc is not None and not a.keep:
            try:
                proc.terminate()
                proc.wait(timeout=10)
            except Exception:      # noqa: BLE001 — 有意静默：收尾杀不掉不影响已得出的结论
                try:
                    proc.kill()
                except Exception:  # noqa: BLE001 — 有意静默：同上
                    pass
        if not a.keep:
            shutil.rmtree(tmpdir, ignore_errors=True)

    print("=" * 78)
    print("判据：v5 slot/member 双层 + 闸门（P2）—— 临时实例 %s" % base)
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
