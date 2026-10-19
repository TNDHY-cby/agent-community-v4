# -*- coding: utf-8 -*-
"""判据：**掉线恢复链端到端**（有活却掉线 → 自动抢救 → 派巡逻员 → 回报 → 平台复核 → 复验）。

```bash
python tools/offline_recover_check.py     # 约 25 秒
```

## 为什么补这一条

`member_offline` / `patrol` 只有**单元**级守卫（喂合成成员对象），端到端这一环此前没有判据。
单元测试里最容易漏的正是**跨模块的真实契约**（例如：巡逻回报的"平台复核"到底拿哪份数据、
拿到的是 dict 还是 list）——这类错在单测里会因为异常兜底而**照样通过**。

## 七格判据（真打 HTTP，看平台状态与留痕，不看"接口 200"）

| 格 | 判据 |
|---|---|
| ⓪ | 隔离实例起来 + 两个 harness 登记 + 建间 + 开工（换端口 + 换数据目录） |
| ① | 有活：派活成功，成员 `entry_state` 进入在途态 |
| ② | **空闲是正常态**：没活的成员 `verdict=idle`，不进抢救（不打扰） |
| ③ | **掉线可见**：心跳失联后 `offline-report` 判 `verdict=rescue` 且带原因 |
| ④ | **自动抢救**：`recover` 真跑一轮 → 试调起失败 → **派了巡逻员**（留痕） |
| ⑤ | **不放过假修好**：巡逻员说修好了、但心跳仍失联 ⇒ `claimed_but_not_verified` |
| ⑥ | **真修好要能确认**：心跳恢复后巡逻员说修好 ⇒ `fixed`，成员回到不喊人状态 |
| ⑦ | **留痕有序**：巡逻历史两条各带结论，审计有 `workshop.patrol.result` |

⑤⑥ 是这条链的**价值所在**：只验"派得出去"很容易假绿——真正会出错的是**复核口径**。
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
                               method="POST" if body is not None else "GET",
                               headers=headers)
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
        return 0, {"error": "%s: %s" % (type(e).__name__, str(e)[:100])}


def _row(rep: dict, member_id: str) -> dict:
    return next((x for x in (rep.get("members") or []) if x.get("member_id") == member_id), {})


#: 合规的巡逻回报（`checked` 非空、动作在三类里、fixed 自证）
def _report(fixed: bool, *, reason: str = "") -> dict:
    body = {
        "checked": [{"what": "窗口在不在", "how": "看会话窗口标题", "result": "在"},
                    {"what": "进程在不在", "how": "按 harness 名扫进程", "result": "在"},
                    {"what": "桥心跳", "how": "看最后上报时间", "result": "刚上报"}],
        "found": "现场看到它其实活着，只是心跳断过",
        "tried": [{"action": "reactivate", "result": "已让平台重新派一次激活",
                   "verified": fixed}],
        "fixed": fixed,
        "evidence": {"after": {"heartbeat": "刚上报", "window": "在"}},
    }
    if fixed:
        body["why_no_proof"] = ""
    else:
        body["still_broken_reason"] = reason or "心跳仍然没回来"
    return body


def main() -> int:
    port = free_port()
    base = "http://127.0.0.1:%d" % port
    data_dir = Path(tempfile.mkdtemp(prefix="ac_offline_"))
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
        print("   %-40s %-6s %s" % (name, "OK" if ok else "★FAIL", detail))
        fails += 0 if ok else 1

    print("=" * 90)
    print("掉线恢复链判据（实例 %s，数据目录 %s）" % (base, data_dir))
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

        # ── ⓪ 登记两个 harness（码农 + 巡逻员）─────────────────────────────
        tokens = {}
        for hid, name in (("off-worker", "示例Harness-OffWorker"),
                          ("off-patrol", "示例Harness-OffPatrol")):
            st, reg = req(base, "/api/harness/register", {
                "harness_id": hid, "harness_name": name,
                "harness_type": "desktop-agent", "wakeup_method": "file_poll",
                "wakeup_dir": str(inbox), "ai": {"model_name": "示例模型"},
            })
            if st != 200:
                print("★登记 %s 失败：%s" % (hid, str(reg)[:200]))
                return 1
            tokens[hid] = str((reg or {}).get("agent_token") or "")
        st, ws = req(base, "/api/workshop", {
            "name": "掉线恢复走查", "hall_content": "写一个排序模块并自测",
            "members": [{"role": "码农", "harness_ids": ["off-worker"]},
                        {"role": "巡逻员", "harness_ids": ["off-patrol"]},
                        {"role": "记录员", "harness_ids": ["off-worker"]}],
        })
        ws_id = str((ws or {}).get("workshop_id") or "")
        if st != 200 or not ws_id:
            print("★建工作间失败：%s" % str(ws)[:300])
            return 1
        st_start, started = req(base, "/api/workshop/%s/start" % ws_id, {})
        cell("⓪ 隔离实例 + 双 harness + 开工", st_start == 200,
             "ws=%s status=%s" % (ws_id, (started or {}).get("status")))

        # ── ① 有活（只给 m0 派，m2 保持空闲）──────────────────────────────
        st, _r = req(base, "/api/workshop/%s/leader/assign" % ws_id,
                     {"member_id": "m0", "instruction": "写排序函数并自测"})
        st_ws, body = req(base, "/api/workshop/%s" % ws_id)
        mem0 = next((m for m in (body.get("members") or []) if m.get("member_id") == "m0"), {})
        cell("① 派活成功且在途（m0）",
             st == 200 and bool(mem0.get("entry_state")),
             "HTTP %s entry_state=%s" % (st, mem0.get("entry_state") or "（空）"))

        # ── ② 空闲是正常态：**没有开工**的工作间里，成员不算有活、不被打扰 ──────
        # 口径说明（2026-10-19 踩正）：开工会给全员派激活 ⇒ `entry_state=dispatched`
        # 属"在途激活"，判有活不算错；所以这一格验的是**真的没有在途事**的情形。
        st_w2, ws2 = req(base, "/api/workshop", {
            "name": "空闲走查", "hall_content": "还没开工",
            "members": [{"role": "记录员", "harness_ids": ["off-worker"]}],
        })
        ws2_id = str((ws2 or {}).get("workshop_id") or "")
        st, rep0 = req(base, "/api/workshop/%s/offline-report" % ws2_id)
        m2 = _row(rep0, "m0")
        cell("② 空闲成员不打扰（未开工 ⇒ idle）",
             st == 200 and m2.get("verdict") == "idle" and not m2.get("offline"),
             "verdict=%s offline=%s pending_work=%s" % (
                 m2.get("verdict"), m2.get("offline"), m2.get("pending_work_why") or "-"))

        # ── ③ 掉线可见：m0 心跳失联（interval=1 ⇒ 2s 判 stale）─────────────
        hdr = {"X-Agent-Token": tokens.get("off-worker", "")}
        st_hb, hb = req(base, "/api/heartbeat", {
            "harness_id": "off-worker", "status": "busy", "interval": 1}, hdr)
        stale = False
        for _ in range(20):
            time.sleep(0.6)
            _s, hs = req(base, "/api/heartbeat")
            stale = "off-worker" in json.dumps(hs, ensure_ascii=False)
            st, rep1 = req(base, "/api/workshop/%s/offline-report" % ws_id)
            m0 = _row(rep1, "m0")
            if m0.get("verdict") == "rescue":
                break
        cell("③ 掉线可见（m0=rescue 且带原因）",
             st == 200 and m0.get("verdict") == "rescue" and bool(m0.get("reasons")),
             "heartbeat=%s ｜ verdict=%s ｜ 原因=%s" % (
                 st_hb, m0.get("verdict"), "；".join(m0.get("reasons") or [])[:70]))

        # ── ④ 自动抢救：试调起失败 ⇒ 派巡逻员（留痕）──────────────────────
        st, rec1 = req(base, "/api/workshop/%s/member/m0/recover" % ws_id, {})
        patrol1 = (rec1 or {}).get("patrol") or {}
        pid1 = str(patrol1.get("patrol_id") or "")
        cell("④ 自动抢救派出了巡逻员",
             st == 200 and rec1.get("action") in ("retry", "awaiting_patrol")
             and bool(patrol1.get("dispatched")) and bool(pid1),
             "action=%s patrol=%s" % (rec1.get("action"), pid1 or str(patrol1)[:60]))

        # ── ⑤ 不放过假修好：心跳仍失联时"我说修好了"不算 ──────────────────
        st, res1 = req(base, "/api/workshop/%s/patrol/%s/result" % (ws_id, pid1),
                       _report(True), hdr)
        v1 = (res1 or {}).get("verdict") or {}
        st_ws, body = req(base, "/api/workshop/%s" % ws_id)
        mem0 = next((m for m in (body.get("members") or []) if m.get("member_id") == "m0"), {})
        cell("⑤ 假修好被拦住（claimed_but_not_verified）",
             st == 200 and v1.get("status") == "claimed_but_not_verified"
             and v1.get("fixed") is False and mem0.get("entry_state") == "needs_human",
             "verdict=%s entry_state=%s" % (v1.get("status"), mem0.get("entry_state")))

        # ── ⑥ 真修好要能确认：心跳恢复 + 巡逻员回报 fixed=true ⇒ fixed ─────
        req(base, "/api/heartbeat", {"harness_id": "off-worker", "status": "idle",
                                     "interval": 30}, hdr)
        time.sleep(0.5)
        st, disp2 = req(base, "/api/workshop/%s/patrol" % ws_id,
                        {"target_member_id": "m0"}, hdr)
        pid2 = str((disp2 or {}).get("patrol_id") or "")
        st, res2 = req(base, "/api/workshop/%s/patrol/%s/result" % (ws_id, pid2),
                       _report(True), hdr)
        v2 = (res2 or {}).get("verdict") or {}
        st_ws, body = req(base, "/api/workshop/%s" % ws_id)
        mem0 = next((m for m in (body.get("members") or []) if m.get("member_id") == "m0"), {})
        cell("⑥ 真修好被确认（verdict=fixed + 不再喊人）",
             st == 200 and v2.get("status") == "fixed" and v2.get("fixed") is True
             and mem0.get("entry_state") != "needs_human",
             "verdict=%s ｜ entry_state=%s ｜ offline_still=%s" % (
                 v2.get("status"), mem0.get("entry_state") or "（空）",
                 (res2 or {}).get("offline_still")))

        # ── ⑦ 留痕有序 ───────────────────────────────────────────────────
        st, lst = req(base, "/api/workshop/%s/patrols?target_member_id=m0" % ws_id)
        items = (lst or {}).get("items") or []
        verdicts = [((x.get("verdict") or {}).get("status")) for x in items]
        st_a, au = req(base, "/api/audit?target=%s" % ws_id)
        blob = json.dumps(au, ensure_ascii=False)
        cell("⑦ 巡逻历史 + 审计留痕",
             st == 200 and len(items) >= 2 and all(v is not None for v in verdicts)
             and "workshop.patrol.result" in blob,
             "条数=%d 结论=%s 审计=%s" % (len(items), verdicts,
                                          "有" if "workshop.patrol.result" in blob else "缺"))
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except Exception:                                    # noqa: BLE001
            proc.kill()
        import shutil
        shutil.rmtree(data_dir, ignore_errors=True)

    print("-" * 90)
    print("结论：%s" % ("**掉线恢复链全绿（不打扰空闲 / 掉线可见 / 派巡逻员 / 假修好不放过 / 真修好能确认）**"
                      if not fails else "**有 %d 格失败**" % fails))
    return 0 if not fails else 1


if __name__ == "__main__":
    raise SystemExit(main())
