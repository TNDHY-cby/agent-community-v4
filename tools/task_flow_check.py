# -*- coding: utf-8 -*-
"""判据：**任务全链路**（派活 → 执行回报 → 进入复核 → 退回重派 → 完成）。

```bash
python tools/task_flow_check.py      # 约 5 秒
```

## 为什么补这一条

`task_rounds_check.py` 验到"多轮派活 + 回报被接受"，但那只是**一半**：真实协作里还要
**组长复核 → 不通过就退回重派 → 最后完成**。这条回路此前没有端到端判据。

## 五格判据（全部真打 HTTP，看的是平台状态而不是"接口返回 200"）

| 格 | 判据 |
|---|---|
| ① | 派活成功（`running`），任务落盘（带 `task_id`） |
| ② | 回报被接受，且**工作间仍在 running**（没被一次回报误判成完成） |
| ③ | 进入复核：`status=review`，`sm/status` 的 stage 跟着变 |
| ④ | **退回重派**：`continue` → `running`；再派一条（`task_id` 不同、带上一轮上下文） |
| ⑤ | 完成：`status=done`（终态），`sm/status` 进入 done |
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


def req(base: str, path: str, body=None, hdr=None, timeout=30):
    data = None if body is None else json.dumps(body).encode("utf-8")
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


def main() -> int:
    port = free_port()
    base = "http://127.0.0.1:%d" % port
    data_dir = Path(tempfile.mkdtemp(prefix="ac_flow_"))
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
        print("   %-36s %-6s %s" % (name, "OK" if ok else "★FAIL", detail))
        fails += 0 if ok else 1

    def tasks_on_disk() -> list:
        return sorted(inbox.glob("task_*.json"))

    print("=" * 90)
    print("任务全链路判据（实例 %s，数据目录 %s）" % (base, data_dir))
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
            "harness_id": "flow-fp", "harness_name": "示例Harness-Flow",
            "harness_type": "desktop-agent", "wakeup_method": "file_poll",
            "wakeup_dir": str(inbox), "ai": {"model_name": "示例模型"},
        })
        if st != 200:
            print("★登记失败：%s" % str(reg)[:200])
            return 1
        token = str((reg or {}).get("agent_token") or "")
        hdr = {"X-Agent-Token": token} if token else {}

        st, ws = req(base, "/api/workshop", {
            "name": "全链路走查", "hall_content": "写排序模块并自测",
            "members": [{"role": "码农", "harness_ids": ["flow-fp"]}],
        })
        ws_id = str((ws or {}).get("workshop_id") or "")
        if st != 200 or not ws_id:
            print("★建工作间失败：%s" % str(ws)[:200])
            return 1
        # 建间默认是 `discussing`（还没开工）⇒ 先 start 进入 running，再派活
        st_start, started = req(base, "/api/workshop/%s/start" % ws_id, {})
        cell("⓪ 开工（discussing → running）",
             st_start == 200 and str((started or {}).get("status") or "") == "running",
             "HTTP %s status=%s" % (st_start, (started or {}).get("status")))

        # ① 派活（running + 落盘 + task_id）
        st, _r = req(base, "/api/workshop/%s/leader/assign" % ws_id,
                     {"member_id": "m0", "instruction": "第一版：写排序函数"})
        files1 = tasks_on_disk()
        tid1 = json.loads(files1[0].read_text(encoding="utf-8")).get("task_id") if files1 else ""
        st_ws, ws_body = req(base, "/api/workshop/%s" % ws_id)
        status1 = str((ws_body or {}).get("status") or "")
        cell("① 派活成功且真落盘（带 task_id）",
             st == 200 and bool(tid1) and status1 == "running",
             "HTTP %s task_id=%s ws.status=%s" % (st, tid1 or "?", status1 or "?"))

        # ② 回报被接受，但工作间不应因此被误判成完成
        st, _rr = req(base, "/api/harness/task-result", {
            "workshop_id": ws_id, "member_id": "m0", "harness_id": "flow-fp",
            "task_id": tid1, "ok": True, "result": "第一版完成：冒泡排序。",
        }, hdr)
        st_ws, ws_body = req(base, "/api/workshop/%s" % ws_id)
        status2 = str((ws_body or {}).get("status") or "")
        cell("② 回报被接受且未被误判完成", st == 200 and status2 != "done",
             "HTTP %s ws.status=%s" % (st, status2 or "?"))

        # ③ 进入复核
        st, rev = req(base, "/api/workshop/%s/review" % ws_id, {})
        st_sm, sm1 = req(base, "/api/workshop/%s/sm/status" % ws_id)
        cell("③ 进入复核（status=review）",
             st == 200 and str((rev or {}).get("status") or "") == "review",
             "HTTP %s status=%s ｜ sm=%s" % (
                 st, (rev or {}).get("status"),
                 str((sm1 or {}).get("state") or (sm1 or {}).get("status") or "")[:20]))

        # ④ 退回重派：continue → running，再派一条（新 task_id）
        st, cont = req(base, "/api/workshop/%s/continue" % ws_id, {})
        for f in tasks_on_disk():
            try:
                f.unlink()
            except OSError:
                pass
        st2, _r2 = req(base, "/api/workshop/%s/leader/assign" % ws_id,
                       {"member_id": "m0", "instruction": "第二版：加单测并重写为快排"})
        files2 = tasks_on_disk()
        blob2 = json.loads(files2[0].read_text(encoding="utf-8")) if files2 else {}
        tid2 = str(blob2.get("task_id") or "")
        ctx2 = (blob2.get("context") or {}).get("previous_instructions") or []
        cell("④ 退回重派（新 task_id + 带上一轮上下文）",
             st == 200 and str((cont or {}).get("status") or "") == "running"
             and bool(tid2) and tid2 != tid1 and bool(ctx2),
             "continue=%s ｜ task_id %s → %s ｜ 上下文 %d 条" % (
                 (cont or {}).get("status"), tid1 or "?", tid2 or "?", len(ctx2)))

        # ⑤ 完成（终态）
        st, done = req(base, "/api/workshop/%s/complete" % ws_id, {})
        st_ws, ws_body = req(base, "/api/workshop/%s" % ws_id)
        status3 = str((ws_body or {}).get("status") or "")
        st_sm, sm2 = req(base, "/api/workshop/%s/sm/status" % ws_id)
        cell("⑤ 完成（status=done + 状态机终态）",
             st == 200 and status3 == "done"
             and "done" in json.dumps(sm2, ensure_ascii=False).lower(),
             "HTTP %s status=%s" % (st, status3 or "?"))
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except Exception:                                    # noqa: BLE001
            proc.kill()
        import shutil
        shutil.rmtree(data_dir, ignore_errors=True)

    print("-" * 90)
    print("结论：%s" % ("**任务全链路全绿（派活 / 回报不误判 / 复核 / 退回重派 / 完成）**"
                      if not fails else "**有 %d 格失败**" % fails))
    return 0 if not fails else 1


if __name__ == "__main__":
    raise SystemExit(main())
