# -*- coding: utf-8 -*-
"""判据：**辩论模式端到端**（三级讨论里：开开关 → 收陈述 → 跑一轮 → 只追加裁决 → 第二轮不烧钱）。

```bash
python tools/debate_chain_check.py     # 约 8 秒
```

## 为什么补这一条

V52 的内核有 19 条单元守卫，接口层也有静态守卫，但**没有一条端到端判据**把
「陈述怎么进池 → 一轮怎么裁定 → 讨论区被改动了什么」串起来证明。这条链最容易出的错
恰恰是"看起来成功"：裁决写了、但**把别人的原话也改了**，或者**每轮都重新烧一遍 token**。

## 九格判据（真打 HTTP；看平台真实返回与讨论区真实文本）

| 格 | 判据 |
|---|---|
| ⓪ | 隔离实例 + 建间 + 开工 + 派活回报 → 走到**三级讨论（review）** |
| ① | **默认关**：`GET /debate` `enabled=false` |
| ② | 没开就 `run` ⇒ 400，且说清"先开开关"（不许静默不跑） |
| ③ | 开了但**不在 review** ⇒ 400，且把当前状态说出来 |
| ④ | 不合规陈述（缺 claim）⇒ 400 说清；合规陈述 ⇒ 200（**无证据也收**，`evidence_count=0`） |
| ⑤ | 两条**互斥**陈述（结论不同 + 证据不相交）⇒ 一轮 `triggered=true`、结论非空、分歧点非空 |
| ⑥ | **只追加**：跑完后讨论区**只多一条** orchestrator 消息，原有每条消息内容逐字不变 |
| ⑦ | **第二轮不重复烧钱**：不新增陈述再跑 ⇒ `stop=true` 且原因含"无新信息"，轮次递增到 2 |
| ⑧ | 状态与留痕：`round` / `last_verdict` 落盘；审计有 `debate_toggle` / `debate_statement` / `debate_run` |
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
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
            return e.code, {"raw": raw[:400]}
    except Exception as e:                                   # noqa: BLE001
        return 0, {"error": "%s: %s" % (type(e).__name__, str(e)[:120])}


def main() -> int:
    port = free_port()
    base = "http://127.0.0.1:%d" % port
    data_dir = Path(tempfile.mkdtemp(prefix="ac_dbt_"))
    inbox = data_dir / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    env = _clean_env(AC_PORT=port, AC_DATA_DIR=data_dir, PYTHONPATH=str(ROOT),
                     PYTHONIOENCODING="utf-8", AC_PYTHON=str(sys.executable))
    proc = subprocess.Popen([sys.executable, "-m", "agent_community.platform.server"],
                            cwd=str(ROOT), env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    fails = 0

    def cell(name: str, ok: bool, detail: str = "") -> None:
        nonlocal fails
        print("   %-46s %-6s %s" % (name, "OK" if ok else "★FAIL", detail))
        fails += 0 if ok else 1

    print("=" * 90)
    print("辩论模式端到端判据（实例 %s，数据目录 %s）" % (base, data_dir))
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
        st, reg = req(base, "/api/harness/register", {
            "harness_id": "dbt-h", "harness_name": "示例Harness-Dbt",
            "harness_type": "desktop-agent", "wakeup_method": "file_poll",
            "wakeup_dir": str(inbox), "ai": {"model_name": "示例模型"},
        })
        token = str((reg or {}).get("agent_token") or "")
        ws_id = ""
        st, ws = req(base, "/api/workshop", {
            "name": "辩论走查", "hall_content": "评估两种缓存方案，给结论",
            "members": [{"role": "码农", "harness_ids": ["dbt-h"]}],
        })
        ws_id = str((ws or {}).get("workshop_id") or "")
        if st != 200 or not ws_id:
            print("★建间失败：%s" % str(ws)[:300])
            return 1
        # ③ 先在**非 review** 状态下试跑（此时应 400）
        st, sr = req(base, "/api/workshop/%s/debate/run" % ws_id, {})

        req(base, "/api/workshop/%s/start" % ws_id, {})
        files = sorted(inbox.glob("*.json"))
        tid = ""
        if files:
            try:
                tid = str(json.loads(files[-1].read_text(encoding="utf-8")).get("task_id") or "")
            except Exception:                                # noqa: BLE001
                tid = ""
        req(base, "/api/workshop/%s/leader/assign" % ws_id,
            {"member_id": "m0", "instruction": "给出缓存方案结论"})
        files = sorted(inbox.glob("*.json"))
        if files:
            try:
                tid = str(json.loads(files[-1].read_text(encoding="utf-8")).get("task_id") or "")
            except Exception:                                # noqa: BLE001
                tid = ""
        st_rep, _ = req(base, "/api/harness/task-result", {
            "workshop_id": ws_id, "member_id": "m0", "harness_id": "dbt-h",
            "task_id": tid, "ok": True, "result": "方案结论：用 LRU。",
        }, {"X-Agent-Token": token})
        st_rev, rev = req(base, "/api/workshop/%s/review" % ws_id, {})
        st_ws, wbody = req(base, "/api/workshop/%s" % ws_id)
        cell("⓪ 走到三级讨论（review）",
             st_rev == 200 and str((wbody or {}).get("status") or "") == "review",
             "review HTTP %s ｜ ws.status=%s" % (st_rev, (wbody or {}).get("status")))

        # ① 默认关
        st, d0 = req(base, "/api/workshop/%s/debate" % ws_id)
        cell("① 辩论模式默认关", st == 200 and (d0 or {}).get("enabled") is False,
             "enabled=%s" % (d0 or {}).get("enabled"))

        # ② 没开就 run ⇒ 400 且说清
        st_x, xb = req(base, "/api/workshop/%s/debate/run" % ws_id, {})
        cell("② 未开启时 run 被拒且说清原因",
             st_x == 400 and "未开启" in str((xb or {}).get("error") or ""),
             "HTTP %s %s" % (st_x, str((xb or {}).get("error"))[:50]))

        # ③ 非 review 时 run ⇒ 400（用刚建的另一间，仍非 review）
        st_w2, ws2 = req(base, "/api/workshop", {"name": "非三级", "hall_content": "x",
                                                 "members": []})
        ws2_id = str((ws2 or {}).get("workshop_id") or "")
        req(base, "/api/workshop/%s/debate" % ws2_id, {"enabled": True})
        st_y, yb = req(base, "/api/workshop/%s/debate/run" % ws2_id, {})
        cell("③ 不在三级讨论时 run 被拒（状态说出来）",
             st_y == 400 and "三级讨论" in str((yb or {}).get("error") or ""),
             "HTTP %s %s" % (st_y, str((yb or {}).get("error"))[:60]))

        # ④ 收陈述：不合规拒 / 合规收（无证据也收）
        req(base, "/api/workshop/%s/debate" % ws_id, {"enabled": True})
        st_bad, badb = req(base, "/api/workshop/%s/debate/statements" % ws_id, {"speaker": "甲"})
        st_a, sa = req(base, "/api/workshop/%s/debate/statements" % ws_id, {
            "speaker": "甲", "claim": "该用 LRU 缓存",
            "evidence": [{"kind": "url", "ref": "https://docs.example.com/lru",
                          "verified": True}],
            "reasoning": "官方文档说 LRU 命中率高",
        })
        st_b, sb = req(base, "/api/workshop/%s/debate/statements" % ws_id, {
            "speaker": "乙", "claim": "该用 LFU 缓存",
            "evidence": [{"kind": "bench", "ref": "https://bench.example.net/lfu",
                          "verified": True}],
            "reasoning": "我们自己的压测里 LFU 更好",
        })
        st_c, sc = req(base, "/api/workshop/%s/debate/statements" % ws_id, {
            "speaker": "丙", "claim": "我觉得都行（没证据）",
        })
        cell("④ 陈述校验（缺 claim 拒 / 无证据也收）",
             st_bad == 400 and "claim" in str((badb or {}).get("error") or "")
             and st_a == 200 and st_b == 200 and st_c == 200
             and (sc or {}).get("evidence_count") == 0,
             "拒=%s ｜ 甲=%s 乙=%s 丙(无证据)=%s" % (
                 st_bad, st_a, st_b, (sc or {}).get("evidence_count")))

        # ⑤ 跑一轮：互斥 ⇒ 触发、有结论、有分歧
        st_before, wbefore = req(base, "/api/workshop/%s" % ws_id)
        disc_before = list((wbefore or {}).get("discussion") or [])
        st, run1 = req(base, "/api/workshop/%s/debate/run" % ws_id, {"user_asked": True})
        out = (run1 or {}).get("output") or {}
        cell("⑤ 一轮触发且有结论与分歧",
             st == 200 and (run1 or {}).get("triggered") is True
             and bool(out.get("conclusion")) and bool(out.get("disagreements")),
             "triggered=%s 结论=%s 分歧=%d" % (
                 (run1 or {}).get("triggered"), str(out.get("conclusion"))[:28],
                 len(out.get("disagreements") or [])))

        # ⑥ 只追加：讨论区只多一条，原有每条内容逐字不变
        st_after, wafter = req(base, "/api/workshop/%s" % ws_id)
        disc_after = list((wafter or {}).get("discussion") or [])
        grew_one = len(disc_after) == len(disc_before) + 1
        same = all(json.dumps(disc_before[i], ensure_ascii=False, sort_keys=True)
                   == json.dumps(disc_after[i], ensure_ascii=False, sort_keys=True)
                   for i in range(len(disc_before)))
        new_msg = disc_after[-1] if disc_after else {}
        cell("⑥ 只追加一条裁决且原话逐字不变",
             grew_one and same and "辩论裁决" in str(new_msg.get("content") or ""),
             "条数 %d→%d ｜ 原话未变=%s ｜ 新消息 role=%s" % (
                 len(disc_before), len(disc_after), same, new_msg.get("role")))

        # ⑦ 第二轮：不新增陈述 ⇒ 无新信息、轮次递增
        st, run2 = req(base, "/api/workshop/%s/debate/run" % ws_id, {"user_asked": True})
        stop = (run2 or {}).get("stop") or {}
        st_d, d = req(base, "/api/workshop/%s/debate" % ws_id)
        cell("⑦ 第二轮不重复烧钱（无新信息 + 轮次递增）",
             st == 200 and stop.get("stop") is True and "无新信息" in str(stop.get("reason") or "")
             and int((d or {}).get("round") or 0) >= 2,
             "stop=%s ｜ round=%s" % (str(stop.get("reason"))[:22], (d or {}).get("round")))

        # ⑧ 状态与审计留痕
        last = (d or {}).get("last_verdict") or {}
        st_a2, au = req(base, "/api/audit")
        evs = {str(r.get("event") or "") for r in ((au or {}).get("events") or [])}
        need = {"workshop.debate_toggle", "workshop.debate_statement", "workshop.debate_run"}
        cell("⑧ 状态落盘 + 审计三类事件齐全",
             bool(last.get("round")) and need.issubset(evs),
             "last_verdict.round=%s ｜ 审计缺=%s" % (
                 last.get("round"), sorted(need - evs) or "无"))
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except Exception:                                    # noqa: BLE001
            proc.kill()
        import shutil
        shutil.rmtree(data_dir, ignore_errors=True)

    print("-" * 90)
    print("结论：%s" % ("**辩论模式端到端全绿（默认关 / 拒绝说清 / 陈述入池 / 触发裁决 / 只追加 / 不重复烧钱）**"
                      if not fails else "**有 %d 格失败**" % fails))
    return 0 if not fails else 1


if __name__ == "__main__":
    raise SystemExit(main())
