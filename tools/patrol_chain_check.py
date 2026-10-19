# -*- coding: utf-8 -*-
"""判据：**巡逻员「人工点派」全链**（V56）—— 派得出去 / 拒得合理 / 三类动作 / 三态 / 平台复核。

```bash
python tools/patrol_chain_check.py     # 约 10 秒
```

## 为什么补这一条

`offline_recover_check.py` 验的是**抢救链自动派**；`patrol.py` 有内核单测。
但"**人**在界面上点一下派巡逻员 → 它回报 → 平台复核"这条**人工主路径**没有端到端判据。
它的错法都很难看：派不出去却回 200（假装派了人）、回报不合规却被收下、
或者"它说修好了"就当真（不拿平台自己的判据复核）。

## 十二格判据（真打 HTTP；掉线信号用**真心跳失联**造，不用假数据）

| 格 | 判据 |
|---|---|
| ⓪ | 隔离实例 + 建间（1 巡逻员 + 1 目标成员） |
| ① | 工作间里**没有巡逻员** ⇒ 409 + `how_to_fix`（不假装派了人） |
| ② | 指定的成员**不是巡逻员** ⇒ 400 说清 |
| ③ | 巡逻员**没绑 harness** ⇒ 400 说清（派不出去） |
| ④ | **目标成员不存在** ⇒ 404 |
| ⑤ | 正常派：200 + 返回 `checklist` / `allowed_actions`（正好三类）/ 纯文本指令（无 `**`） |
| ⑥ | **回报不合规五类各 422**：空 `checked`、动作不在三类、`fixed=null` 缺 `why_no_proof`、`fixed=false` 缺 `still_broken_reason`、`need_human` 缺 `user_action` |
| ⑦ | **不采信"我说修好了"**：心跳仍失联 ⇒ `claimed_but_not_verified` 且目标标 `needs_human` |
| ⑧ | `fixed=false` ⇒ `not_fixed` + 人话里带原因 |
| ⑨ | `fixed=null` ⇒ `unverified`（不许当修好） |
| ⑩ | 心跳恢复后再说修好 ⇒ `fixed`，且目标的 `needs_human` 被清掉 |
| ⑪ | 巡逻历史每条带 `status`/`verdict`；`?target_member_id=` 过滤有效 |
| ⑫ | 审计有 `workshop.patrol.dispatch` 与 `workshop.patrol.result` |
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
            return e.code, {"raw": raw[:300]}
    except Exception as e:                                   # noqa: BLE001
        return 0, {"error": "%s: %s" % (type(e).__name__, str(e)[:120])}


def _ok_report(fixed=True, **kw) -> dict:
    body = {
        "checked": [{"what": "窗口在不在", "how": "看会话窗口标题", "result": "在看"},
                    {"what": "进程在不在", "how": "按 harness 名扫进程", "result": "在"}],
        "found": "现场看到它活着，只是心跳断过",
        "tried": [{"action": "reactivate", "result": "让平台重新派了一次激活",
                   "verified": bool(fixed)}],
        "fixed": fixed,
        "evidence": {"after": {"window": "在"}},
    }
    body.update(kw)
    return body


def main() -> int:
    port = free_port()
    base = "http://127.0.0.1:%d" % port
    data_dir = Path(tempfile.mkdtemp(prefix="ac_patrol_"))
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
        print("   %-52s %-6s %s" % (name, "OK" if ok else "★FAIL", detail))
        fails += 0 if ok else 1

    print("=" * 94)
    print("巡逻员「人工点派」全链判据（实例 %s，数据目录 %s）" % (base, data_dir))
    print("=" * 94)
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
        for hid, name in (("pat-p", "示例Harness-Patrol"), ("pat-t", "示例Harness-Target")):
            st, reg = req(base, "/api/harness/register", {
                "harness_id": hid, "harness_name": name, "harness_type": "desktop-agent",
                "wakeup_method": "file_poll", "wakeup_dir": str(inbox),
                "ai": {"model_name": "示例模型"}})
            if st != 200:
                print("★登记失败：%s" % str(reg)[:200])
                return 1
            toks[hid] = str((reg or {}).get("agent_token") or "")
        # m1 = 巡逻员（绑 pat-p），m0 = 目标成员（绑 pat-t）
        st, ws = req(base, "/api/workshop", {
            "name": "巡逻点派走查", "hall_content": "派人到现场看看",
            "members": [{"role": "码农", "harness_ids": ["pat-t"]},
                        {"role": "巡逻员", "harness_ids": ["pat-p"]}],
        })
        ws_id = str((ws or {}).get("workshop_id") or "")
        if st != 200 or not ws_id:
            print("★建间失败：%s" % str(ws)[:300])
            return 1
        cell("⓪ 隔离实例 + 建间（巡逻员 m1 / 目标 m0）", True, "ws=%s" % ws_id)

        # ① 没有巡逻员
        st, ws2 = req(base, "/api/workshop", {
            "name": "无巡逻员", "hall_content": "x",
            "members": [{"role": "码农", "harness_ids": ["pat-t"]}]})
        ws2_id = str((ws2 or {}).get("workshop_id") or "")
        st1, b1 = req(base, "/api/workshop/%s/patrol" % ws2_id, {"target_member_id": "m0"})
        cell("① 没有巡逻员 ⇒ 409 + how_to_fix",
             st1 == 409 and bool((b1 or {}).get("how_to_fix")),
             "HTTP %s ｜ %s" % (st1, str((b1 or {}).get("error"))[:38]))

        # ② 指定非巡逻员
        st2, b2 = req(base, "/api/workshop/%s/patrol" % ws_id,
                      {"target_member_id": "m0", "patrol_member_id": "m0"})
        cell("② 指定非巡逻员 ⇒ 400 说清", st2 == 400 and "不是巡逻员" in str((b2 or {}).get("error")),
             "HTTP %s" % st2)

        # ③ 巡逻员未绑 harness
        st, ws3 = req(base, "/api/workshop", {
            "name": "巡逻员没绑", "hall_content": "x",
            "members": [{"role": "码农", "harness_ids": ["pat-t"]},
                        {"role": "巡逻员", "harness_ids": []}]})
        ws3_id = str((ws3 or {}).get("workshop_id") or "")
        st3, b3 = req(base, "/api/workshop/%s/patrol" % ws3_id,
                      {"target_member_id": "m0", "patrol_member_id": "m1"})
        cell("③ 巡逻员未绑 harness ⇒ 400 说清",
             st3 == 400 and "未绑定 harness" in str((b3 or {}).get("error")),
             "HTTP %s" % st3)

        # ④ 目标不存在
        st4, _b4 = req(base, "/api/workshop/%s/patrol" % ws_id, {"target_member_id": "mX"})
        cell("④ 目标成员不存在 ⇒ 404", st4 == 404, "HTTP %s" % st4)

        # ⑤ 正常派
        st5, b5 = req(base, "/api/workshop/%s/patrol" % ws_id, {"target_member_id": "m0"})
        pid1 = str((b5 or {}).get("patrol_id") or "")
        acts = (b5 or {}).get("allowed_actions") or []
        instr = str((b5 or {}).get("instruction") or "")
        cell("⑤ 正常派：清单 + 正好三类动作 + 纯文本指令",
             st5 == 200 and bool(pid1) and bool((b5 or {}).get("checklist"))
             and acts == ["relaunch_window", "restart_bridge", "reactivate"]
             and "**" not in instr and "不许改代码" in instr,
             "HTTP %s patrol=%s 动作=%d" % (st5, pid1 or "?", len(acts)))

        # ⑥ 回报不合规五类
        bad_cases = [
            ("空 checked", {"checked": [], "fixed": True}),
            ("动作不在三类", _ok_report(tried=[{"action": "delete_data", "result": "x"}])),
            ("fixed=null 缺 why_no_proof", _ok_report(fixed=None)),
            ("fixed=false 缺 still_broken_reason", _ok_report(fixed=False)),
            ("need_human 缺 user_action", _ok_report(fixed=False,
                                                    still_broken_reason="桥还在死",
                                                    need_human=True)),
        ]
        got = []
        for label, body in bad_cases:
            s, b = req(base, "/api/workshop/%s/patrol/%s/result" % (ws_id, pid1), body)
            got.append((label, s, bool((b or {}).get("problems"))))
        cell("⑥ 回报不合规五类各 422（且都给出 problems）",
             all(s == 422 and has for _l, s, has in got),
             " ｜ ".join("%s→%s%s" % (l[:10], s, "" if has else "(无problems)") for l, s, has in got))

        # 造真掉线信号：心跳 interval=1 ⇒ 2s 判 stale
        req(base, "/api/heartbeat", {"harness_id": "pat-t", "status": "busy", "interval": 1},
            {"X-Agent-Token": toks["pat-t"]})
        time.sleep(3)

        # ⑦ 说修好但信号仍在 ⇒ claimed_but_not_verified
        st7, b7 = req(base, "/api/workshop/%s/patrol/%s/result" % (ws_id, pid1),
                      _ok_report(fixed=True))
        v7 = (b7 or {}).get("verdict") or {}
        st_ws, wbody = req(base, "/api/workshop/%s" % ws_id)
        mem0 = next((m for m in (wbody.get("members") or []) if m.get("member_id") == "m0"), {})
        cell("⑦ 说修好但心跳仍失联 ⇒ claimed_but_not_verified",
             st7 == 200 and v7.get("status") == "claimed_but_not_verified"
             and v7.get("fixed") is False and mem0.get("entry_state") == "needs_human",
             "verdict=%s ｜ entry_state=%s" % (v7.get("status"), mem0.get("entry_state")))

        # ⑧ fixed=false ⇒ not_fixed
        st8, b8 = req(base, "/api/workshop/%s/patrol" % ws_id, {"target_member_id": "m0"})
        pid2 = str((b8 or {}).get("patrol_id") or "")
        st8b, b8b = req(base, "/api/workshop/%s/patrol/%s/result" % (ws_id, pid2),
                        _ok_report(fixed=False, still_broken_reason="桥进程还是死的"))
        v8 = (b8b or {}).get("verdict") or {}
        cell("⑧ fixed=false ⇒ not_fixed 且人话带原因",
             st8b == 200 and v8.get("status") == "not_fixed"
             and "桥进程还是死的" in str((b8b or {}).get("human_line") or ""),
             "verdict=%s" % v8.get("status"))

        # ⑨ fixed=null ⇒ unverified
        st9, b9 = req(base, "/api/workshop/%s/patrol" % ws_id, {"target_member_id": "m0"})
        pid3 = str((b9 or {}).get("patrol_id") or "")
        st9b, b9b = req(base, "/api/workshop/%s/patrol/%s/result" % (ws_id, pid3),
                        _ok_report(fixed=None, why_no_proof="窗口标题看不到，没法确认"))
        v9 = (b9b or {}).get("verdict") or {}
        cell("⑨ fixed=null ⇒ unverified（不许当修好）",
             st9b == 200 and v9.get("status") == "unverified" and v9.get("fixed") is False,
             "verdict=%s" % v9.get("status"))

        # ⑩ 心跳恢复后说修好 ⇒ fixed，且清掉 needs_human
        req(base, "/api/heartbeat", {"harness_id": "pat-t", "status": "idle", "interval": 30},
            {"X-Agent-Token": toks["pat-t"]})
        time.sleep(1.5)
        st10, b10 = req(base, "/api/workshop/%s/patrol" % ws_id, {"target_member_id": "m0"})
        pid4 = str((b10 or {}).get("patrol_id") or "")
        st10b, b10b = req(base, "/api/workshop/%s/patrol/%s/result" % (ws_id, pid4),
                          _ok_report(fixed=True))
        v10 = (b10b or {}).get("verdict") or {}
        st_ws2, wbody2 = req(base, "/api/workshop/%s" % ws_id)
        mem0b = next((m for m in (wbody2.get("members") or []) if m.get("member_id") == "m0"), {})
        cell("⑩ 信号消失后说修好 ⇒ fixed 且清掉 needs_human",
             st10b == 200 and v10.get("status") == "fixed" and v10.get("fixed") is True
             and mem0b.get("entry_state") != "needs_human",
             "verdict=%s ｜ entry_state=%s" % (v10.get("status"), mem0b.get("entry_state") or "(空)"))

        # ⑪ 历史与过滤
        st11, b11 = req(base, "/api/workshop/%s/patrols?target_member_id=m0" % ws_id)
        items = (b11 or {}).get("items") or []
        verdicts = [(x.get("verdict") or {}).get("status") for x in items]
        cell("⑪ 历史带结论且过滤有效",
             st11 == 200 and len(items) >= 4 and all(v is not None for v in verdicts)
             and all(str(x.get("target_member_id")) == "m0" for x in items),
             "条数=%d 结论=%s" % (len(items), verdicts))

        # ⑫ 审计
        st12, b12 = req(base, "/api/audit")
        evs = {str(r.get("event") or "") for r in ((b12 or {}).get("events") or [])}
        need = {"workshop.patrol.dispatch", "workshop.patrol.result"}
        cell("⑫ 审计有派单与回报", need.issubset(evs), "缺=%s" % (sorted(need - evs) or "无"))
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except Exception:                                    # noqa: BLE001
            proc.kill()
        import shutil
        shutil.rmtree(data_dir, ignore_errors=True)

    print("-" * 94)
    print("结论：%s" % ("**巡逻员人工点派全链绿（拒得合理 / 三类动作 / 三态 / 平台复核 / 留痕）**"
                      if not fails else "**有 %d 格失败**" % fails))
    return 0 if not fails else 1


if __name__ == "__main__":
    raise SystemExit(main())
