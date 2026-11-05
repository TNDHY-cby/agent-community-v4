# -*- coding: utf-8 -*-
"""判据：**公司间合作** —— 单机双实例真投 + 跨组织证据采信（V61 §6 / V130 §5）。

```bash
python tools/cross_company_check.py     # 约 30 秒
```

## 它证明什么

两个**各自独立**的合作社（同一台机器、不同端口、不同 `AC_DATA_DIR`）之间：
**A 公司把任务派给 B 公司 → B 真的收到并建成工作间 → A 能回查状态 → 两侧数据互不污染**。
这条**不需要 frp、不需要公网**（frp 只是把地址换成穿透后的地址，见 V61 §6 的 P3）。

## 二十格（V61~V64）

| 格 | 判据 |
|---|---|
| ⓪ | B 实例用**独立数据目录 + 独立端口**起来，B 的**配置文件也在自己目录里**（V61 隔离前提） |
| ① | B 侧签发 agent_token（'a2a 入站' 的凭据来源） |
| ② | B 的 a2a 服务起来（Agent Card 可读；读卡不需要凭据） |
| ③ | **A 侧真投**：`a2a_client.send_task` ⇒ 返回任务对象 |
| ④ | **B 侧真收**：B 平台上出现对应工作间（名字含任务标题） |
| ⑤ | **回查**：`tasks/get` 能按 taskId 查回来 |
| ⑥ | **不带 token ⇒ 401**；**错 token ⇒ 401/403**（不许裸奔） |
| ⑦ | **隔离**：A 的数据目录里**没有** B 的工作间 id |
| ⑧~⑳ | 平台业务端点链：登记对端 / 验证 / 派活 / 回查 / 停用 / 错令牌如实失败 / 隧道建议 / 外部任务可见 / 回执刷新 / 产物清单 / 断线如实标不可达 / 收尾 |

## ㉑~㉖：跨组织证据采信（`V130` §5，2026-11-04 老板拍板）

| # | 格 | 断言 |
|---|---|---|
| ㉑ | 自证 ≠ 采信 | 只给"我干完了" ⇒ 采信态 `accepted_self`，**验收不给过**（`PEER_NOT_REVIEWED`） |
| ㉒ | 复核通过 | 对端给产物 + sha256，本侧**真重算一致** ⇒ `accepted_peer`，账本有 `peer_checked_at`，此后验收可过 |
| ㉓ | 复核不通过 | 故意给错 sha256 ⇒ 采信态停在 `accepted_self` + `peer_diff` 列出不一致文件 |
| ㉔ | 凭据不存在 | `L1` 但 `evidence_ref` 指向的路径不存在 ⇒ `L1_PROOF_MISSING`，按 `L4` 算 |
| ㉕ | 挂争议后不可验收 | 两方主张冲突 ⇒ `disputed` 且**从可验收里摘出**（记录不删） |
| ㉖ | 争议有留痕 | `dispute` 含发起方/双方主张/待裁对象；**只读视图**能查到 |
"""
from __future__ import annotations

import hashlib
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
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def free_port() -> int:
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])
    finally:
        s.close()


def _env(**over) -> dict:
    keep = ("PATH", "PYTHON", "SYSTEM", "WINDIR", "TEMP", "TMP", "HOME", "USER",
            "APPDATA", "LOCALAPPDATA", "COMSPEC", "PATHEXT", "LANG", "LC_", "LD_LIBRARY")
    e = {k: v for k, v in os.environ.items()
         if not k.startswith("AC_") and not k.startswith("AC4_") and k.upper().startswith(keep)}
    e.update({k: str(v) for k, v in over.items()})
    return e


def req(base: str, path: str, body=None, token: str = "", timeout=30):
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
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
            return e.code, {"raw": raw[:200]}
    except Exception as e:                                   # noqa: BLE001
        return 0, {"error": "%s: %s" % (type(e).__name__, str(e)[:120])}


def main() -> int:
    fails = 0

    def cell(name: str, ok: bool, detail: str = "") -> None:
        nonlocal fails
        print("   %-52s %-6s %s" % (name, "OK" if ok else "★FAIL", detail))
        fails += 0 if ok else 1

    tmp = Path(tempfile.mkdtemp(prefix="ac_cross_"))
    dir_a, dir_b = tmp / "company_a", tmp / "company_b"
    dir_a.mkdir(parents=True, exist_ok=True)
    dir_b.mkdir(parents=True, exist_ok=True)
    port_b = free_port()
    a2a_port = free_port()
    base_b = "http://127.0.0.1:%d" % port_b
    a2a_url = "http://127.0.0.1:%d/" % a2a_port
    procs: list[subprocess.Popen] = []
    print("=" * 96)
    print("公司间合作 P1（A 目录存根 / B 实例 %s + A2A %s）" % (base_b, a2a_url))
    print("=" * 96)
    try:
        # ⓪ B 实例（独立数据目录 + 独立端口）
        env_b = _env(AC_PORT=port_b, AC_DATA_DIR=str(dir_b), PYTHONPATH=str(ROOT),
                     PYTHONIOENCODING="utf-8", AC_PYTHON=sys.executable)
        procs.append(subprocess.Popen([sys.executable, "-m", "agent_community.platform.server"],
                                      cwd=str(ROOT), env=env_b,
                                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        up = False
        for _ in range(120):
            st, _b = req(base_b, "/api/status", timeout=3)
            if st == 200:
                up = True
                break
            time.sleep(0.5)
        cfg_b = dir_b / "config" / "config.json"
        cell("⓪ B 实例起来（独立端口 + 独立数据目录 + 配置在 B 目录里）", up and cfg_b.is_file(),
             "port=%d ｜ config=%s" % (port_b, "有" if cfg_b.is_file() else "缺"))
        if not up:
            return 2

        # ① 签发 token（B 侧）
        st_r, reg = req(base_b, "/api/harness/register", {
            "harness_id": "co-b-harness", "harness_name": "示例Harness-B公司",
            "harness_type": "desktop-agent", "wakeup_method": "file_poll",
            "wakeup_dir": str(dir_b / "inbox"), "ai": {"model_name": "示例模型"}})
        token = str((reg or {}).get("agent_token") or "")
        cell("① B 侧签发 agent_token", st_r == 200 and bool(token), "token=%s…" % token[:8])

        # ② B 的 a2a 服务
        env_a2a = _env(AC_PORT=port_b, AC_BASE_URL=base_b, AC4_TOKEN=token, AC_DATA_DIR=str(dir_b),
                       PYTHONPATH=str(ROOT), PYTHONIOENCODING="utf-8")
        procs.append(subprocess.Popen(
            [sys.executable, "-m", "agent_community.a2a_server",
             "--host", "127.0.0.1", "--port", str(a2a_port)],
            cwd=str(ROOT), env=env_a2a, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        card_ok = False
        for _ in range(60):
            st, card = req(a2a_url.rstrip("/"), "/.well-known/agent-card.json", timeout=3)
            if st == 200 and isinstance(card, dict):
                card_ok = True
                break
            time.sleep(0.5)
        cell("② B 的 a2a 服务起来（Agent Card 可读）", card_ok,
             "card=%s" % ("有" if card_ok else "缺"))

        # ③ A 侧真投
        from agent_community import a2a_client
        title = "跨公司任务：把登录页的错误提示统一口径 #%d" % (int(time.time()) % 10000)
        sent = {}
        try:
            sent = a2a_client.send_task(a2a_url, title, token, timeout=60)
        except Exception as e:                               # noqa: BLE001
            sent = {"error": "%s: %s" % (type(e).__name__, str(e)[:120])}
        task_id = ""
        if isinstance(sent, dict):
            task_id = str(sent.get("id") or (sent.get("task") or {}).get("id") or "")
        cell("③ A 侧真投（tasks/send）", bool(task_id), "task=%s" % (task_id or str(sent)[:80]))

        # ④ B 侧真收（出现工作间）
        found = ""
        for _ in range(40):
            st, wslist = req(base_b, "/api/workshops")
            items = (wslist or {}).get("workshops") or (wslist if isinstance(wslist, list) else [])
            for w in items or []:
                if not isinstance(w, dict):
                    continue
                name = str(w.get("name") or "")
                if title[:10] in name:
                    found = str(w.get("workshop_id") or w.get("ws_id") or "")
                    break
            if found:
                break
            time.sleep(0.5)
        cell("④ B 侧真收（平台上出现对应工作间）", bool(found), "ws=%s" % (found or "未出现"))

        # ⑤ 回查
        got = {}
        try:
            got = a2a_client.get_task(a2a_url, task_id, token, timeout=30) if task_id else {}
        except Exception as e:                               # noqa: BLE001
            got = {"error": str(e)[:100]}
        cell("⑤ tasks/get 能回查", bool(task_id) and isinstance(got, dict) and bool(got),
             "回查键=%s" % list(got)[:5])

        # ⑥ 鉴权：不带 token / 错 token
        st_no, _ = req(a2a_url.rstrip("/"), "/", {"jsonrpc": "2.0", "id": "1",
                                                  "method": "tasks/get", "params": {"taskId": "x"}})
        st_bad, _ = req(a2a_url.rstrip("/"), "/", {"jsonrpc": "2.0", "id": "2",
                                                    "method": "tasks/get", "params": {"taskId": "x"}},
                        token="wrong-token-000")
        cell("⑥ 无凭据 401 / 错凭据 401 或 403（不许裸奔）",
             st_no == 401 and st_bad in (401, 403), "无凭据=%s ｜ 错凭据=%s" % (st_no, st_bad))

        # ⑦ 隔离：A 的目录里没有 B 的工作间
        a_leak = False
        if found and dir_a.is_dir():
            for p in dir_a.rglob("*.json"):
                try:
                    if found in p.read_text(encoding="utf-8", errors="replace"):
                        a_leak = True
                        break
                except Exception:                            # noqa: BLE001
                    continue
        cell("⑦ 隔离：A 的数据目录里没有 B 的工作间", not a_leak,
             "泄漏=%s" % ("是" if a_leak else "否"))

        # ══ V62：走**平台业务端点**的通用框架链（登记/验证/派活/回查/停用/错令牌）══
        port_a = free_port()
        base_a = "http://127.0.0.1:%d" % port_a
        env_a = _env(AC_PORT=port_a, AC_DATA_DIR=str(dir_a), PYTHONPATH=str(ROOT),
                     PYTHONIOENCODING="utf-8", AC_PYTHON=sys.executable)
        procs.append(subprocess.Popen([sys.executable, "-m", "agent_community.platform.server"],
                                      cwd=str(ROOT), env=env_a,
                                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        up_a = False
        for _ in range(120):
            st, _b = req(base_a, "/api/status", timeout=3)
            if st == 200:
                up_a = True
                break
            time.sleep(0.5)
        cell("⑧ A 实例起来（我方视角，独立数据目录）", up_a, "port=%d" % port_a)
        if not up_a:
            return 2

        st, b = req(base_a, "/api/peers", {"display_name": "公司 B（示例）",
                                           "base_url": a2a_url, "token": token,
                                           "note": "同机第二个实例"}, token="")
        peer = (b or {}).get("peer", {}) if isinstance(b, dict) else {}
        listed = req(base_a, "/api/peers")[1]
        items = (listed or {}).get("peers") or []
        only_masked = all(("token_enc" not in x) and ("token" not in x) for x in items)
        cell("⑨ 登记对端公司（列表只回打码令牌）",
             st == 200 and peer.get("peer_id") and only_masked,
             "peer=%s ｜ 打码=%s" % (peer.get("peer_id"), peer.get("token_masked")))

        st, vb = req(base_a, "/api/peers/%s/verify" % peer.get("peer_id"), {}, token="")
        cell("⑩ 验证对端（读 Agent Card，不烧 token）",
             st == 200 and (vb or {}).get("ok") is True,
             str((vb or {}).get("detail"))[:60])

        title2 = "跨公司派活（平台端点）：统一错误提示口径 #%d" % (int(time.time()) % 10000)
        st, db = req(base_a, "/api/peers/%s/dispatch" % peer.get("peer_id"), {"title": title2})
        tid2 = str((db or {}).get("task_id") or "")
        got_ws = ""
        for _ in range(40):
            st_w, wslist2 = req(base_b, "/api/workshops")
            items2 = (wslist2 or {}).get("workshops") or []
            for w in items2 or []:
                if isinstance(w, dict) and title2[:10] in str(w.get("name") or ""):
                    got_ws = str(w.get("workshop_id") or "")
                    break
            if got_ws:
                break
            time.sleep(0.5)
        cell("⑪ 派活成功且对端真收到（对端出现工作间）",
             st == 200 and bool(tid2) and bool(got_ws),
             "task=%s ｜ 对端 ws=%s" % (tid2 or "?", got_ws or "未出现"))

        st, tb = req(base_a, "/api/peers/%s/tasks/%s" % (peer.get("peer_id"), tid2))
        cell("⑫ 回查对端任务状态", st == 200 and bool((tb or {}).get("task")),
             "HTTP %s" % st)

        req(base_a, "/api/peers/%s/enabled" % peer.get("peer_id"), {"enabled": False})
        st_off, offb = req(base_a, "/api/peers/%s/dispatch" % peer.get("peer_id"),
                           {"title": "停用后不该派出去"})
        cell("⑬ 停用后派活被拒（如实说怎么开）",
             st_off == 403 and bool((offb or {}).get("how_to_fix")),
             "HTTP %s" % st_off)

        st_bad_peer, bad_peer = req(base_a, "/api/peers", {
            "display_name": "坏令牌公司", "base_url": a2a_url, "token": "your-wrong-token"})
        bad_id = str(((bad_peer or {}).get("peer") or {}).get("peer_id") or "")
        st_bd, bdb = req(base_a, "/api/peers/%s/dispatch" % bad_id, {"title": "应当被拒"})
        after = req(base_a, "/api/peers")[1]
        bad_rec = next((x for x in ((after or {}).get("peers") or [])
                        if x.get("peer_id") == bad_id), {})
        cell("⑭ 错令牌派活 ⇒ 如实失败，且本地索引不写成功",
             st_bd in (401, 403, 502) and int(bad_rec.get("tasks_count") or 0) == 0,
             "HTTP %s ｜ 索引条数=%s" % (st_bd, bad_rec.get("tasks_count")))

        st, adv = req(base_a, "/api/peers/tunnel-advice")
        suggest = (adv or {}).get("suggest") or []
        must = (adv or {}).get("must_not_expose") or []
        cell("⑮ 隧道建议：给三类办法 + 明确「别暴露管理端点」",
             st == 200 and len(suggest) >= 3 and bool(must)
             and str((adv or {}).get("reachable_from_outside")) in ("likely", "unlikely", "unknown"),
             "建议 %d 条 ｜ 禁暴露 %d 条 ｜ 判断=%s" % (len(suggest), len(must),
                                                     (adv or {}).get("reachable_from_outside")))

        # ══ V63：外部任务进本侧视野 ══
        # ⚠️ ⑬ 刚把这个对端**停用**过（那格就是要验"停用后派活被拒"）⇒ 这里派活前必须先启用回来，
        #    否则 ⑯ 拿到的是 403、没有 peer_task_id（第一版就踩了：pt=? status=None）。
        req(base_a, "/api/peers/%s/enabled" % peer.get("peer_id"), {"enabled": True})
        st, pb = req(base_a, "/api/peers/%s/dispatch" % peer.get("peer_id"),
                     {"title": title2, "workshop_id": "ws_demo_001"})
        pt_id = str((pb or {}).get("peer_task_id") or "")
        tid3 = str((pb or {}).get("task_id") or "")     # 本次派活的对端任务号（别拿 ⑪ 那次的比）
        st, lb = req(base_a, "/api/peer-tasks?peer_id=%s" % peer.get("peer_id"))
        listed_pt = (lb or {}).get("tasks") or []
        mine = next((x for x in listed_pt if x.get("pt_id") == pt_id), {})
        cell("⑯ 派活后本侧能看到这条外部任务（status=sent + 对端任务号）",
             st == 200 and bool(pt_id) and mine.get("status") == "sent"
             and bool(tid3) and mine.get("remote_task_id") == tid3,
             "pt=%s ｜ status=%s ｜ remote=%s" % (pt_id or "?", mine.get("status"),
                                                  mine.get("remote_task_id")))

        st, rb = req(base_a, "/api/peer-tasks/%s/refresh" % pt_id, {})
        t2 = (rb or {}).get("task") or {}
        cell("⑰ 刷新回执 ⇒ 状态更新 + 记下核对时间 + 时间线追加",
             st == 200 and t2.get("status") in ("sent", "working", "done", "failed")
             and bool((t2.get("last_check") or {}).get("at"))
             and len(t2.get("timeline") or []) >= 2,
             "status=%s ｜ 时间线 %d 条" % (t2.get("status"), len(t2.get("timeline") or [])))

        # ══ V64：对端交了什么（产物清单）—— 必须放在"停掉对端"那格**之前** ══
        # ⚠️ 必须针对**本格这条任务**对应的对端工作间造产物（第一版造在 ⑪ 那个工作间 ⇒ 查不到，白红一次）：
        #    对端工作间的 id 就等于 a2a 的 task_id（`origin_task_id` 同值）。
        art_ok = False
        detail_art = "未测到"
        try:
            # ★2026-10-31 修判据（**既有假红**，在 8dc159d 上同样红，非本次引入）：
            #   原注写「对端工作间的 id 就等于 a2a 的 task_id」——这条**早就不成立**了。
            #   实测：`pb.task_id` 是对端任务号（如 5d3fbe9a），而对端真工作间 id 是另一个值（如 7d10818e），
            #   拿任务号去 `POST /api/workshop/<id>/members` 必然 404 ⇒ 加不上成员 ⇒ 造不出产物 ⇒ 本格恒红。
            #   现在照 ⑪ 的做法**按标题在对端列表里搜真工作间 id**；搜不到才退回 task_id，并如实标出来源。
            ws_b, ws_src = "", ""
            for _ in range(20):
                _st, wl = req(base_b, "/api/workshops")
                for w in (wl or {}).get("workshops") or []:
                    if isinstance(w, dict) and title2[:10] in str(w.get("name") or ""):
                        ws_b, ws_src = str(w.get("workshop_id") or ""), "按标题搜到"
                        break
                if ws_b:
                    break
                time.sleep(0.3)
            if not ws_b:
                ws_b, ws_src = tid3 or "", "退回 task_id（没按标题搜到工作间）"
            st_m, _mb = req(base_b, "/api/workshop/%s/members" % ws_b,
                            {"members": [{"role": "码农", "harness_ids": []}]})
            st_w, wbody = req(base_b, "/api/workshop/%s" % ws_b)
            wdir = Path(str((wbody or {}).get("workspace_dir") or ""))
            members = (wbody or {}).get("members") or []
            made = ""
            if wdir.is_dir() and members:
                m = members[0]
                outdir = wdir / ("%s_%s" % (m.get("role"), m.get("member_id"))) / "output"
                outdir.mkdir(parents=True, exist_ok=True)
                (outdir / "报告.md").write_text("# 对端交付\n这是对端员工写的报告。\n", encoding="utf-8")
                made = str(outdir.name)
            st_o, ob = req(base_b, "/api/workshop/%s/outputs" % ws_b)
            st_r2, rb2 = req(base_a, "/api/peer-tasks/%s/refresh" % pt_id, {})
            arts = ((rb2 or {}).get("task") or {}).get("artifacts") or []
            art_ok = bool(arts) and all(a.get("owner") == "remote" for a in arts)
            detail_art = ("产物 %d 个 ｜ 对端ws=%s(%s) 加成员=%s 成员=%d 目录=%s ｜ 对端outputs=%s/%s ｜ %s" %
                          (len(arts), ws_b or "?", ws_src, st_m, len(members), made or "无",
                           st_o, (ob or {}).get("total_files"), (arts[0].get("name") if arts else "无")))
        except Exception as e:                               # noqa: BLE001
            detail_art = "异常：%s: %s" % (type(e).__name__, str(e)[:80])
        cell("⑱ 对端交了什么：回执带产物清单（只清单、标 remote、不下载）", art_ok, detail_art)

        st, wb = req(base_a, "/api/peer-tasks?workshop_id=ws_demo_001")
        only = (wb or {}).get("tasks") or []
        st2, wb2 = req(base_a, "/api/peer-tasks?workshop_id=ws_other")
        other = (wb2 or {}).get("tasks") or []
        # 把对端的 a2a 停掉 ⇒ 再刷新必须如实变 unreachable（**不许留在 working 假装还在跑**）
        try:
            procs[1].terminate()
            procs[1].wait(timeout=15)
        except Exception:                                    # noqa: BLE001
            pass
        time.sleep(1.0)
        st, nb = req(base_a, "/api/peer-tasks/%s/refresh" % pt_id, {})
        t3 = (nb or {}).get("task") or {}
        cell("⑲ 按工作间过滤生效；对端停掉后刷新 ⇒ unreachable（不假装在跑）",
             len(only) == 1 and not other and t3.get("status") == "unreachable",
             "匹配 %d 条 ｜ 其它间 %d 条 ｜ 断线后状态=%s" % (len(only), len(other), t3.get("status")))

        # ══ V130：跨组织证据采信 —— 20 → 26 格（2026-11-04 老板拍板）══
        # 夹具：A 侧真建一个工作间 + 名单 + 派活三条 ⇒ 账本里 T-001~T-003。
        # 然后走**真 HTTP 端点**：peer-delivery（交付方自证）→ peer-review（本侧重算）→ dispute → review（落闸）。
        ws_a = dir_a / "ws_a"
        slot_a = "前端组/员工01"
        for d in (ws_a / "_lead", ws_a / "前端组", ws_a / slot_a / "任务", ws_a / slot_a / "产物"):
            d.mkdir(parents=True, exist_ok=True)
        (ws_a / "_lead" / "建模.md").write_text("# 建模\n", encoding="utf-8")
        (ws_a / "前端组" / "分工.md").write_text("- 版本: 1\n", encoding="utf-8")
        (ws_a / "前端组" / "_notice.md").write_text("", encoding="utf-8")
        st, made_a = req(base_a, "/api/workshop", {"name": "跨组织验收间（示例）",
                                                  "hall_content": "方向",
                                                  "workshop_path": str(ws_a)})
        wid_a = str((made_a or {}).get("workshop_id") or "")
        mk = req(base_a, "/api/mind/A-01", {})[1] or {}
        req(base_a, "/api/workshop/%s/manifest" % wid_a,
            {"mind_path": str(mk.get("mind_path") or ""), "entry_points": ["_lead/建模.md"]})
        txt_a = ("#回执 v1\n类型: 名单\n工作间: %s\n成员: 组长\n凭据:\n序号: 5\n---\n"
                 "岗位: %s | harness: h_x | model: m | 人数: 1\n---\n行数: 1" % (wid_a, slot_a))
        req(base_a, "/api/workshop/%s/intake?source=C&confirm=1" % wid_a, {"text": txt_a})
        roster_a = req(base_a, "/api/workshop/%s/roster" % wid_a)[1] or {}
        seq_a = ((roster_a.get("members") or [{}])[0] or {}).get("member_seq")
        req(base_a, "/api/workshop/%s/members/%s/evidence" % (wid_a, seq_a),
            {"paste_seen": True, "press_ok": True, "rename_seen": True,
             "verified_scope": "夹具：三项齐"})
        tseqs = []
        for i in (1, 2, 3):
            _st, dsp = req(base_a, "/api/workshop/%s/dispatch" % wid_a,
                           {"member_seq": seq_a, "title": "跨组织验收用 %d" % i, "goal": "验收"})
            tseqs.append(int((dsp or {}).get("task_seq") or i))

        # ㉑ 自证 ≠ 采信：只给"我干完了"，验收必须被拒
        req(base_a, "/api/workshop/%s/tasks/%d/peer-delivery" % (wid_a, tseqs[0]),
            {"source": "manual", "result": "我做完了", "artifacts": []})
        st_self, rv_self = req(base_a, "/api/workshop/%s/tasks/%d/review" % (wid_a, tseqs[0]),
                               {"result": "通过", "evidence_level": "L4"})
        cell("㉑ 自证 ≠ 采信（accepted_self 不给过验收）",
             st_self == 400 and (rv_self or {}).get("code") == "PEER_NOT_REVIEWED"
             and (rv_self or {}).get("peer_accepted") == "accepted_self",
             "HTTP %s ｜ %s" % (st_self, (rv_self or {}).get("code")))

        # ㉒ 复核通过：本侧真重算，与"对端给的 sha256"一致
        #    ① 先从 B 侧真工作间里取它交的产物（⑱ 造的那份）② A 侧工位写**同样字节**
        #    ③ 对端清单里的 sha256 = B 侧文件的真 hash ④ 本侧平台自己扫盘重算
        b_ws2, b_dir = "", None
        for _ in range(20):
            _st, wl2 = req(base_b, "/api/workshops")
            for w in (wl2 or {}).get("workshops") or []:
                if isinstance(w, dict) and title2[:10] in str(w.get("name") or ""):
                    b_ws2 = str(w.get("workshop_id") or "")
                    break
            if b_ws2:
                break
            time.sleep(0.3)
        if b_ws2:
            _st, wbody2 = req(base_b, "/api/workshop/%s" % b_ws2)
            b_dir = Path(str((wbody2 or {}).get("workspace_dir") or ""))
        b_file = next(iter(b_dir.rglob("报告.md")), None) if b_dir and b_dir.is_dir() else None
        peer_sha, peer_bytes, peer_rel = "", 0, "码农_9/报告.md"
        detail_22 = "B 侧没找到可复核的产物（工作间=%s）" % (b_ws2 or "未找到")
        if b_file and b_file.is_file():
            data = b_file.read_bytes()
            peer_sha, peer_bytes = hashlib.sha256(data).hexdigest(), len(data)
            (ws_a / slot_a / "产物" / "报告.md").write_bytes(data)     # 两边各算一次同一份文件
            peer_files = [{"name": peer_rel, "sha256": peer_sha, "bytes": peer_bytes}]
            req(base_a, "/api/workshop/%s/tasks/%d/peer-delivery" % (wid_a, tseqs[0]),
                {"source": "a2a", "external_task_id": tid3, "result": "交付报告.md",
                 "artifacts": peer_files})
            st_pr, pr = req(base_a, "/api/workshop/%s/tasks/%d/peer-review" % (wid_a, tseqs[0]),
                            {"peer_files": peer_files, "checked_by": "验收人（示例）"})
            gv = req(base_a, "/api/workshop/%s/tasks/%d/peer-review" % (wid_a, tseqs[0]))[1] or {}
            _st, rv_ok = req(base_a, "/api/workshop/%s/tasks/%d/review" % (wid_a, tseqs[0]),
                             {"result": "通过", "evidence_level": "L0"})
            detail_22 = ("对端 sha=%s… ｜ 采信=%s ｜ peer_checked_at=%s ｜ 复核后验收=%s"
                         % (peer_sha[:8] or "?", (pr or {}).get("accept_state"),
                            "有" if gv.get("peer_checked_at") else "缺",
                            (rv_ok or {}).get("state") or (rv_ok or {}).get("code")))
            cell("㉒ 复核通过 ⇒ accepted_peer + peer_checked_at（此后验收可过）",
                 st_pr == 200 and (pr or {}).get("accept_state") == "accepted_peer"
                 and bool(gv.get("peer_checked_at")) and (rv_ok or {}).get("state") == "accepted",
                 detail_22)
        else:
            cell("㉒ 复核通过 ⇒ accepted_peer + peer_checked_at（此后验收可过）", False, detail_22)

        # ㉓ 复核不通过：故意给错 sha256
        wrong = "0" * 64
        req(base_a, "/api/workshop/%s/tasks/%d/peer-delivery" % (wid_a, tseqs[1]),
            {"source": "a2a", "result": "我交了 v2",
             "artifacts": [{"name": peer_rel, "sha256": wrong, "bytes": peer_bytes}]})
        st_pr2, pr2 = req(base_a, "/api/workshop/%s/tasks/%d/peer-review" % (wid_a, tseqs[1]),
                          {"peer_files": [{"name": peer_rel, "sha256": wrong, "bytes": peer_bytes}]})
        diff2 = (pr2 or {}).get("diff") or {}
        bad = [f for f in (diff2.get("files") or []) if f.get("status") != "match"]
        cell("㉓ 复核不通过 ⇒ 停在 accepted_self + peer_diff 列出不一致文件",
             st_pr2 == 200 and (pr2 or {}).get("accept_state") == "accepted_self"
             and bool(bad) and bad[0].get("status") == "sha_mismatch",
             "采信=%s ｜ 差异=%s" % ((pr2 or {}).get("accept_state"),
                                    [(f.get("key"), f.get("status")) for f in bad][:3]))

        # ㉔ 凭据不存在：L1 + 指向不存在的路径 ⇒ 按 L4 算
        ghost = str(dir_a / "不存在的凭据.db")
        st_l1, rv_l1 = req(base_a, "/api/workshop/%s/tasks/%d/review" % (wid_a, tseqs[1]),
                           {"result": "通过", "evidence_level": "L1", "evidence_ref": ghost})
        cell("㉔ 凭据路径不存在 ⇒ L1_PROOF_MISSING（按 L4 算）",
             st_l1 == 400 and (rv_l1 or {}).get("code") == "L1_PROOF_MISSING"
             and "L4" in str((rv_l1 or {}).get("error") or ""),
             "HTTP %s ｜ %s" % (st_l1, (rv_l1 or {}).get("code")))

        # ㉕ / ㉖ 挂争议：两方主张冲突 ⇒ disputed、从可验收摘下、留痕可查
        req(base_a, "/api/workshop/%s/tasks/%d/peer-delivery" % (wid_a, tseqs[2]),
            {"source": "a2a", "result": "我交了 v2"})
        st_dp, dp = req(base_a, "/api/workshop/%s/tasks/%d/dispute" % (wid_a, tseqs[2]),
                        {"raised_by": "接收方",
                         "claims": [{"by": "交付方", "claim": "我交的就是 v2，sha256 是 0…"},
                                    {"by": "接收方", "claim": "本侧重算 sha256 对不上，是另一个文件"}],
                         "adjudicator": "组长（示例）", "why": "两边哈希不一致，平台不判谁对"})
        st_rv5, rv5 = req(base_a, "/api/workshop/%s/tasks/%d/review" % (wid_a, tseqs[2]),
                          {"result": "通过", "evidence_level": "L4"})
        tasks_a = req(base_a, "/api/workshop/%s/tasks" % wid_a)[1] or {}
        still = [t for t in (tasks_a.get("tasks") or [])
                 if int(t.get("task_seq") or 0) == tseqs[2]]
        cell("㉕ 挂争议 ⇒ disputed + 从可验收里摘出（记录不删）",
             st_dp == 200 and (dp or {}).get("accept_state") == "disputed"
             and (dp or {}).get("passable") is False and (rv5 or {}).get("code") == "DISPUTE_OPEN"
             and bool(still),
             "采信=%s ｜ 验收=%s ｜ 账本里这条还在=%s"
             % ((dp or {}).get("accept_state"), (rv5 or {}).get("code"), bool(still)))

        gd = req(base_a, "/api/workshop/%s/tasks/%d/peer-review" % (wid_a, tseqs[2]))[1] or {}
        dis = gd.get("dispute") or {}
        cell("㉖ 争议有留痕（发起方 / 双方主张 / 待裁对象，只读视图查得到）",
             bool(dis) and dis.get("raised_by") == "接收方"
             and len(dis.get("claims") or []) == 2 and bool(dis.get("adjudicator")),
             "发起=%s ｜ 主张=%d 条 ｜ 待裁=%s"
             % (dis.get("raised_by"), len(dis.get("claims") or []), dis.get("adjudicator")))
    finally:
        for p in reversed(procs):
            try:
                p.terminate()
                p.wait(timeout=15)
            except Exception:                                # noqa: BLE001
                try:
                    p.kill()
                except Exception:                            # noqa: BLE001
                    pass
        alive = [p.pid for p in procs if p.poll() is None]
        print("   %-52s %-6s %s" % ("⑳ 收尾：两个实例都停掉", "OK" if not alive else "★FAIL",
                                    "残留 %s" % (alive or "无")))
        if alive:
            fails += 1
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)

    print("-" * 96)
    print("结论：%s" % ("**公司间合作（P1 + V62 通用框架 + V63 外部任务 + V64 产物清单 + "
                      "V130 跨组织证据采信）全绿**"
                      "（双实例 / 真投 / 真收 / 回查 / 鉴权 / 隔离 / 登记对端 / 验证 / 派活 / 回查 / 停用 / "
                      "错令牌如实失败 / 隧道建议 / 外部任务可见 / 回执刷新 / **对端交了什么** / "
                      "断线如实标不可达 / **自证≠采信 / 复核通过 / 复核不通过 / 凭据不存在 / "
                      "争议冻结 / 争议留痕** / 收尾）"
                      if not fails else "**有 %d 格失败**" % fails))
    return 0 if not fails else 1


if __name__ == "__main__":
    raise SystemExit(main())
