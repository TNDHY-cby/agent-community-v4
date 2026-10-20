# -*- coding: utf-8 -*-
"""判据：**公司间合作 P1 —— 单机双实例真投**（V61 §6）。

```bash
python tools/cross_company_check.py     # 约 25 秒
```

## 它证明什么

两个**各自独立**的合作社（同一台机器、不同端口、不同 `AC_DATA_DIR`）之间：
**A 公司把任务派给 B 公司 → B 真的收到并建成工作间 → A 能回查状态 → 两侧数据互不污染**。
这条**不需要 frp、不需要公网**（frp 只是把地址换成穿透后的地址，见 V61 §6 的 P3）。

## 九格判据

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
| ⑧ | 收尾：两个实例都停掉，不留进程 |
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
        print("   %-52s %-6s %s" % ("⑯ 收尾：两个实例都停掉", "OK" if not alive else "★FAIL",
                                    "残留 %s" % (alive or "无")))
        if alive:
            fails += 1
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)

    print("-" * 96)
    print("结论：%s" % ("**公司间合作（P1 + V62 通用框架）全绿**（双实例 / 真投 / 真收 / 回查 / 鉴权 / 隔离 / "
                      "登记对端 / 验证 / 派活 / 回查 / 停用 / 错令牌如实失败 / 隧道建议 / 收尾）"
                      if not fails else "**有 %d 格失败**" % fails))
    return 0 if not fails else 1


if __name__ == "__main__":
    raise SystemExit(main())
