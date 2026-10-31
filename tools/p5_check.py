#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""判据：**P5 入口**（组长列表 / 对接单 / 命名 N+1）八格（`V115` L1~L8）。

真跑：临时实例，真注册两个 harness（一个能写文件、一个只能聊天），真取对接单并比对两次一致性。
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
SLOT = "前端组/员工01"


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
            return int(r.status), raw
    except urllib.error.HTTPError as e:
        return int(e.code), e.read().decode("utf-8", "replace")


def load(raw: str):
    try:
        return json.loads(raw) if raw.strip() else {}
    except ValueError:
        return {"_raw": raw[:300]}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep", action="store_true")
    a = ap.parse_args()

    port = _free_port()
    tmpdir = Path(tempfile.mkdtemp(prefix="ac_p5_"))
    base = "http://127.0.0.1:%d" % port
    wsroot = tmpdir / "wsroot"
    minds = tmpdir / "minds"
    wsroot.mkdir(parents=True, exist_ok=True)
    minds.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env.update({"AC_PORT": str(port), "AC_DATA_DIR": str(tmpdir / "data"),
                "AC_MIND_ROOT": str(minds), "PYTHONIOENCODING": "utf-8"})
    rows, fails = [], []
    proc = None

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
        check("临时平台起来（换端口/数据目录/心智根）", True)

        st, raw = _http(base, "/api/harness/register",
                        {"harness_id": "h_good", "harness_name": "示例Harness-G",
                         "harness_type": "custom", "wakeup_method": "mcp",
                         "tools": [{"name": "write_file"}, {"name": "read_file"}],
                         "ai": {"model_name": "示例模型"}})
        st2, raw2 = _http(base, "/api/harness/register",
                          {"harness_id": "h_chat", "harness_name": "示例Harness-C",
                           "harness_type": "custom", "wakeup_method": "clipboard",
                           "tools": [{"name": "chat"}], "ai": {"model_name": "示例模型"}})
        check("夹具：注册两个 harness（一个能写文件 / 一个只能聊）",
              st == 200 and st2 == 200, "%s / %s" % (st, st2))

        # ① lead_eligible 是算出来的
        tooling = load(_http(base, "/api/harness-tooling")[1])
        by_id = {x["harness_id"]: x for x in (tooling.get("items") or [])}
        check("① `fs_write` + 协议 ⇒ 能当组长；只能聊天 ⇒ 不能（且给原因）",
              by_id.get("h_good", {}).get("lead_eligible") is True
              and by_id.get("h_chat", {}).get("lead_eligible") is False
              and bool((by_id.get("h_chat", {}).get("tooling") or {}).get("fs_write") is not None),
              json.dumps({k: v.get("lead_eligible") for k, v in by_id.items()}, ensure_ascii=False))

        # ② 组长列表：只列能当的 + 明说"不是推荐"
        st, raw = _http(base, "/api/lead-roster?direction=%s" % urllib.parse.quote("编程/后端"))
        roster = load(raw)
        check("② 组长列表只列能当的，并写明「按名字排序，不是推荐」",
              st == 200 and [x["harness_id"] for x in (roster.get("candidates") or [])] == ["h_good"]
              and "不是推荐" in (roster.get("sort_note") or ""),
              json.dumps(roster.get("candidates"), ensure_ascii=False)[:160])

        # ③ 选一个不能当组长的 ⇒ 409 并说清为什么
        st, raw = _http(base, "/api/workshop", {"name": "报销系统", "hall_content": "方向",
                                                "workshop_path": str(wsroot)})
        wid = str(load(raw).get("workshop_id") or "")
        check("夹具：建工作间", bool(wid), raw[:120])
        st, raw = _http(base, "/api/workshop/%s/lead" % wid, {"harness_id": "h_chat"})
        body = load(raw)
        check("③ 选「只能聊天」的当组长 ⇒ 409 NOT_LEAD_ELIGIBLE + why",
              st == 409 and body.get("code") == "NOT_LEAD_ELIGIBLE" and body.get("why"),
              json.dumps(body, ensure_ascii=False)[:180])

        # ④ 选能当的 ⇒ lead_in
        st, raw = _http(base, "/api/workshop/%s/lead" % wid, {"harness_id": "h_good"})
        body = load(raw)
        check("④ 选能当组长的 ⇒ 状态 lead_in", st == 200 and body.get("status") == "lead_in",
              json.dumps(body, ensure_ascii=False)[:160])

        # ⑤ 组长对接单：两次逐字相同 + 两个根写死 + 无占位符
        (wsroot / "_lead").mkdir(parents=True, exist_ok=True)
        (wsroot / "_lead" / "建模.md").write_text("# 建模\n", encoding="utf-8")
        _http(base, "/api/mind/A-01", {})
        _http(base, "/api/workshop/%s/manifest" % wid,
              {"mind_path": str(minds / "A-01"), "entry_points": ["_lead/建模.md"]})
        st, k1 = _http(base, "/api/workshop/%s/lead-kit?format=json&agent_key=A-01" % wid)
        st2, k2 = _http(base, "/api/workshop/%s/lead-kit?format=json&agent_key=A-01" % wid)
        a1, a2 = load(k1), load(k2)
        text = str(a1.get("kit") or "")
        check("⑤ 组长对接单两次逐字相同（纯拼装，不用模型）",
              st == 200 and text and text == str(a2.get("kit") or ""),
              "len=%d" % len(text))
        check("⑥ 对接单里两个根写死、含 Boot Protocol、无占位符",
              str(wsroot) in text and str(minds / "A-01") in text
              and "Boot Protocol" in text
              and not any(x in text for x in ("{{", "}}", "<workshop_path>")),
              text[:120])

        # ⑦ 没要 token ⇒ 不签；要了 ⇒ 只在这一份文本里
        check("⑦ 没要 token 就不签（`has_token=false`）", a1.get("has_token") is False,
              str(a1.get("has_token")))
        st, raw = _http(base, "/api/workshop/%s/lead-kit?format=json&agent_key=A-01&issue_token=1" % wid)
        tok = load(raw)
        check("⑧ 要了 token ⇒ 出现在这一份文本里（凭据行）",
              tok.get("has_token") is True and "凭据:" in str(tok.get("kit") or ""),
              "has_token=%s" % tok.get("has_token"))

        # ⑨ 成员对接单 + 命名 N+1
        txt = ("#回执 v1\n类型: 名单\n工作间: %s\n成员: 组长\n凭据:\n序号: 7\n---\n"
               "岗位: %s | harness: h_good | model: m | 人数: 1\n---\n行数: 1" % (wid, SLOT))
        _http(base, "/api/workshop/%s/intake?source=C&confirm=1" % wid, {"text": txt})
        members = load(_http(base, "/api/workshop/%s/roster" % wid)[1]).get("members") or []
        seq = next((m["member_seq"] for m in members if m["slot_dir"] == SLOT), 0)
        st, raw = _http(base, "/api/workshop/%s/members/%s/kit" % (wid, seq), {})
        mk = load(raw)
        check("⑨ 成员对接单给出的会话名带 N 序号（不复用）",
              st == 200 and mk.get("ok") and str(mk.get("session_label") or "").endswith("%02d" % seq),
              str(mk.get("session_label")))
        st, raw = _http(base, "/api/workshop/%s/next-label?role=%s"
                        % (wid, urllib.parse.quote("员工02")))
        nl = load(raw)
        check("⑩ 下一个会话名是 N+1（且列出已用序号）",
              nl.get("next_seq", 0) > seq and sorted(nl.get("used_seqs") or []) == sorted(
                  m["member_seq"] for m in members),
              json.dumps(nl, ensure_ascii=False)[:160])
    finally:
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
    print("判据：P5 入口（组长列表 / 对接单 / 命名 N+1）—— 临时实例 %s" % base)
    print("=" * 78)
    for name, ok, detail in rows:
        tag = "PASS" if ok else "FAIL"
        print("  %s %s%s" % (tag, name, ("　（%s）" % detail) if not ok else ""))
    print("-" * 78)
    _pass = sum(1 for _n, _o, _d in rows if _o)
    print("合计 %d 格：PASS %d / FAIL %d" % (len(rows), _pass, len(fails)))
    return 0 if not fails else 1


if __name__ == "__main__":
    raise SystemExit(main())
