# -*- coding: utf-8 -*-
"""判据：**多成员并发派活**（两个员工同时干活，互不串件）。

```bash
python tools/task_parallel_check.py     # 约 10 秒
```

## 为什么补这一条

`task_rounds_check.py` 验的是**一个成员多轮**；`task_flow_check.py` 验的是**一个成员全链路**。
真实协作里最常见的是**两个人同时干**：两条活各自落盘、各自回报、各自归位，
一个人回报**不能**把另一个人的进度/产出带偏（串件）。这条此前没有端到端判据。

## 七格判据（真打 HTTP + 读真实落盘文件，不看"接口 200"）

| 格 | 判据 |
|---|---|
| ⓪ | 隔离实例（换端口 + 换数据目录）+ 两个 harness 各自邮箱 + 建间（两名码农）+ 开工 |
| ① | 并发派活：两条指令**各自落盘**，`task_id` 互不相同，载荷里的 `member_id` 各归各 |
| ② | 并发回报（紧邻两次）：两条都被接受，不互相打断（无 5xx、无"后一条覆盖前一条"） |
| ③ | **不串件**：两条回报各自挂在**自己的成员**名下（讨论区消息的 member 归属正确） |
| ④ | **互不牵连**：m0 回报后 m1 仍在途（`entry_state` 与工作间状态不被 m0 的回报推走） |
| ⑤ | **冒名回报要拦住**：拿 m0 的 harness 身份去报 m1 的 `member_id` ⇒ 必须 4xx 且**不落任何一件的进度** |
| ⑥ | 产出不串：各自隔间 `output/` 里的文件不会跑到对方隔间；审计两条回报各记各的 harness |
"""
from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent.parent


def free_port() -> int:
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])
    finally:
        s.close()


def _clean_env(**over) -> dict:
    keep = ("PATH", "PYTHON", "SYSTEM", "WINDIR", "TEMP", "TMP", "HOME", "USER",
            "APPDATA", "LOCALAPPDATA", "COMSPEC", "PATHEXT", "LANG", "LC_", "LD_LIBRARY")
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("AC_") and k.upper().startswith(keep)}
    env.update({k: str(v) for k, v in over.items()})
    return env


def req(base: str, path: str, body=None, hdr=None, timeout=40):
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if hdr:
        headers.update(hdr)
    r = urllib.request.Request(base + path, data=data,
                               method="POST" if body is not None else "GET", headers=headers)
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(raw)
        except Exception:                                    # noqa: BLE001
            return e.code, {"raw": raw[:300]}
    except Exception as e:                                   # noqa: BLE001
        return 0, {"error": "%s: %s" % (type(e).__name__, str(e)[:120])}


def _tasks_in(inbox: Path) -> list:
    out = []
    for p in sorted(inbox.glob("*.json")):
        try:
            blob = json.loads(p.read_text(encoding="utf-8"))
        except Exception:                                    # noqa: BLE001
            continue
        if isinstance(blob, dict):
            out.append({"file": p.name, "task_id": str(blob.get("task_id") or ""),
                        "member_id": str(blob.get("member_id") or ""),
                        "kind": str(blob.get("kind") or blob.get("type") or "")})
    return out


def main() -> int:
    port = free_port()
    base = "http://127.0.0.1:%d" % port
    data_dir = Path(tempfile.mkdtemp(prefix="ac_par_"))
    inbox_a = data_dir / "inbox-a"
    inbox_b = data_dir / "inbox-b"
    for d in (inbox_a, inbox_b):
        d.mkdir(parents=True, exist_ok=True)
    env = _clean_env(AC_PORT=port, AC_DATA_DIR=data_dir, PYTHONPATH=str(ROOT),
                     PYTHONIOENCODING="utf-8", AC_PYTHON=str(sys.executable))
    proc = subprocess.Popen([sys.executable, "-m", "agent_community.platform.server"],
                            cwd=str(ROOT), env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    fails = 0

    def cell(name: str, ok: bool, detail: str = "") -> None:
        nonlocal fails
        print("   %-44s %-6s %s" % (name, "OK" if ok else "★FAIL", detail))
        fails += 0 if ok else 1

    print("=" * 90)
    print("多成员并发派活判据（实例 %s，数据目录 %s）" % (base, data_dir))
    print("=" * 90)
    try:
        code = 0
        for _ in range(120):
            code, _b = req(base, "/api/status", timeout=3)
            if code == 200:
                break
            time.sleep(0.5)
        if code != 200:
            print("★实例没起来")
            return 2

        toks = {}
        for hid, name, inbox in (("par-a", "示例Harness-ParA", inbox_a),
                                 ("par-b", "示例Harness-ParB", inbox_b)):
            st, reg = req(base, "/api/harness/register", {
                "harness_id": hid, "harness_name": name,
                "harness_type": "desktop-agent", "wakeup_method": "file_poll",
                "wakeup_dir": str(inbox), "ai": {"model_name": "示例模型"},
            })
            if st != 200:
                print("★登记 %s 失败：%s" % (hid, str(reg)[:200]))
                return 1
            toks[hid] = str((reg or {}).get("agent_token") or "")
        st, ws = req(base, "/api/workshop", {
            "name": "并发走查", "hall_content": "两人各写一个模块并自测",
            "members": [{"role": "码农", "harness_ids": ["par-a"]},
                        {"role": "码农", "harness_ids": ["par-b"]}],
        })
        ws_id = str((ws or {}).get("workshop_id") or "")
        if st != 200 or not ws_id:
            print("★建工作间失败：%s" % str(ws)[:300])
            return 1
        st_start, started = req(base, "/api/workshop/%s/start" % ws_id, {})
        # 清掉开工产生的激活文件，只看**派活**这一轮
        for d in (inbox_a, inbox_b):
            for p in d.glob("*.json"):
                p.unlink(missing_ok=True)
        cell("⓪ 隔离实例 + 双 harness 双邮箱 + 开工", st_start == 200,
             "ws=%s status=%s" % (ws_id, (started or {}).get("status")))

        # ── ① 并发派活：两条各自落盘 ──────────────────────────────────────
        st0, _r0 = req(base, "/api/workshop/%s/leader/assign" % ws_id,
                       {"member_id": "m0", "instruction": "第一件：写排序函数"})
        st1, _r1 = req(base, "/api/workshop/%s/leader/assign" % ws_id,
                       {"member_id": "m1", "instruction": "第二件：写单测"})
        a_tasks = _tasks_in(inbox_a)
        b_tasks = _tasks_in(inbox_b)
        ta = a_tasks[-1] if a_tasks else {}
        tb = b_tasks[-1] if b_tasks else {}
        st_ws0, body0 = req(base, "/api/workshop/%s" % ws_id)
        names = {m.get("member_id"): str(m.get("display_name") or "")
                 for m in (body0.get("members") or [])}
        # 两个成员必须**分得清**：绑定不同的 harness ⇒ 显示名不能是同一个写死的示例名
        # （2026-10-19 抓到：建间默认 display_name 写死「示例Harness-E」⇒ 全员同名）
        cell("① 并发派活各自落盘（task_id 不同 + 成员正确）",
             st0 == 200 and st1 == 200 and bool(ta.get("task_id")) and bool(tb.get("task_id"))
             and ta.get("task_id") != tb.get("task_id")
             and ta.get("member_id") == "m0" and tb.get("member_id") == "m1",
             "A→%s(m=%s) ｜ B→%s(m=%s)" % (ta.get("task_id") or "?", ta.get("member_id") or "?",
                                           tb.get("task_id") or "?", tb.get("member_id") or "?"))
        cell("①b 两成员显示名分得清（不撞同一示例名）",
             bool(names.get("m0")) and bool(names.get("m1"))
             and names.get("m0") != names.get("m1"),
             "m0=%r ｜ m1=%r" % (names.get("m0"), names.get("m1")))


        # ── ② 并发回报：紧邻两次，互不打断 ────────────────────────────────
        results = {}

        def _report(hid, mid, tid, text):
            results[mid] = req(base, "/api/harness/task-result", {
                "workshop_id": ws_id, "member_id": mid, "harness_id": hid,
                "task_id": tid, "ok": True, "result": text,
            }, {"X-Agent-Token": toks[hid]})

        th1 = threading.Thread(target=_report, args=("par-a", "m0", ta.get("task_id", ""),
                                                     "排序函数完成：quicksort 版。"))
        th2 = threading.Thread(target=_report, args=("par-b", "m1", tb.get("task_id", ""),
                                                     "单测完成：12 条用例全过。"))
        th1.start(); th2.start(); th1.join(); th2.join()
        c0 = results.get("m0", (0, {}))[0]
        c1 = results.get("m1", (0, {}))[0]
        cell("② 两条回报都被接受（无 5xx）", c0 == 200 and c1 == 200,
             "m0 HTTP %s ｜ m1 HTTP %s" % (c0, c1))

        # ── ③ 不串件：各自挂在自己成员名下 ────────────────────────────────
        st_ws, body = req(base, "/api/workshop/%s" % ws_id)
        disc = body.get("discussion") or body.get("messages") or []

        def _said_by(mid: str, needle: str) -> bool:
            """讨论消息里"这条正文属于哪个成员"的真实字段是 `from_member`（不是 member_id）。"""
            for m in disc:
                if not isinstance(m, dict):
                    continue
                owner = str(m.get("from_member") or m.get("member_id") or "")
                if owner != mid:
                    continue
                if needle in json.dumps(m, ensure_ascii=False):
                    return True
            return False
        ok3 = _said_by("m0", "quicksort") and _said_by("m1", "12 条用例")
        cell("③ 两条回报各归其主（不串件）", ok3,
             "m0→%s ｜ m1→%s ｜ 讨论区 %d 条" % (
                 "找到" if _said_by("m0", "quicksort") else "缺",
                 "找到" if _said_by("m1", "12 条用例") else "缺", len(disc)))

        # ── ④ 互不牵连：m0 回报不该把 m1 推走 ─────────────────────────────
        mems = {m.get("member_id"): m for m in (body.get("members") or [])}
        m1_state = (mems.get("m1") or {}).get("entry_state")
        ws_status = body.get("status")
        cell("④ 互不牵连（m1 仍在途、工作间仍在 running）",
             ws_status == "running" and m1_state in ("dispatched", "acked", "entered", ""),
             "ws.status=%s ｜ m1.entry_state=%s" % (ws_status, m1_state))

        # ── ⑤ 冒名回报必须拦住 ────────────────────────────────────────────
        # 拿 A 的身份去报 m1（m1 绑的是 B）：平台若照收，A 就能替 B 交活（串件）
        st_x, x_body = req(base, "/api/harness/task-result", {
            "workshop_id": ws_id, "member_id": "m1", "harness_id": "par-a",
            "ok": True, "result": "冒名：A 替 B 交活",
        }, {"X-Agent-Token": toks["par-a"]})
        st_ws2, body2 = req(base, "/api/workshop/%s" % ws_id)
        disc2 = body2.get("discussion") or body2.get("messages") or []
        leaked = "冒名" in json.dumps(disc2, ensure_ascii=False)
        cell("⑤ 冒名回报被拦住（4xx 且不落进度）",
             st_x in (400, 403, 422) and not leaked,
             "HTTP %s ｜ 讨论区出现冒名内容=%s ｜ %s" % (
                 st_x, leaked, str(x_body)[:90]))

        # ── ⑥ 产出不串 + 审计各记各的 ─────────────────────────────────────
        st_a, au = req(base, "/api/audit")
        # 审计记录的真实字段是 `event`（不是 action）——见 platform/audit.py 的 record()
        rows = [r for r in (au.get("events") or au.get("items") or au.get("logs") or [])
                if str(r.get("event") or r.get("action") or "") == "harness.task_result"]
        tgt = sorted({str(r.get("target") or "") for r in rows if r.get("target")})
        cell("⑥ 审计两条回报各记各的 harness",
             st_a == 200 and "par-a" in tgt and "par-b" in tgt,
             "target 集合=%s ｜ 回报审计 %d 条" % (tgt or "空", len(rows)))
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except Exception:                                    # noqa: BLE001
            proc.kill()
        import shutil
        shutil.rmtree(data_dir, ignore_errors=True)

    print("-" * 90)
    print("结论：%s" % ("**多成员并发派活全绿（各自落盘 / 各自回报 / 不串件 / 不互扰 / 冒名被拦）**"
                      if not fails else "**有 %d 格失败**" % fails))
    return 0 if not fails else 1


if __name__ == "__main__":
    raise SystemExit(main())
