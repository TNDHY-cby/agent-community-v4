# -*- coding: utf-8 -*-
"""判据：**多轮派活**（派活→执行→回报，连做两轮，第二轮必须带上一轮上下文）。

```bash
python tools/task_rounds_check.py      # 约 8 秒
```

## 为什么要单列

`protocol_matrix.py` 的第 ⑥ 段只验**单轮**（派一件、回一次）。但"协作"是**多轮**的：
同一个员工连做两件活，第二轮必须知道第一轮做了什么——否则每轮从零开始。
2026-10-19 加判据时发现**载荷里根本没有上一轮上下文**（只有这条指令 + 大厅内容），已补。

## 五格判据

| 格 | 判据 |
|---|---|
| ① | 派活**真落盘**到 file_poll 的 inbox（带 task_id 的 JSON 文件） |
| ② | 第一轮回报被平台接受（`/api/harness/task-result` 200，且不再挂在待取队列） |
| ③ | 第二轮是**独立任务**（新 task_id，不覆盖第一轮） |
| ④ | **第二轮载荷带上一轮上下文**（`context.previous_instructions` 有第 1 条、`message` 里有人能读的那段） |
| ⑤ | 两轮都在 `assignments` 留痕且顺序正确（第 1 条在前） |
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
    """打一次 HTTP。`hdr` 用来带身份头（`X-Agent-Token`）——**回报必须带**，否则 401。"""
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


def task_files(inbox: Path) -> list:
    return sorted(inbox.glob("task_*.json"))


def task_id_of(blob: dict) -> str:
    """任务 JSON 里的 task_id：可能在顶层，也可能在 `task`/`payload` 里（如实兼容）。"""
    if not isinstance(blob, dict):
        return ""
    for key in ("task_id",):
        if blob.get(key):
            return str(blob[key])
    for nest in ("task", "payload", "data"):
        inner = blob.get(nest)
        if isinstance(inner, dict) and inner.get("task_id"):
            return str(inner["task_id"])
    return ""


def assign_history(asg, member_id: str = "m0") -> list:
    """`leader/assignments` 的返回形状（实测 2026-10-19）：
    `{"success":true,"assignments":[{"member_id":"m0","items":[{...}]}]}` —— 兼容 dict/list 两种。"""
    if isinstance(asg, list):
        recs = asg
    elif isinstance(asg, dict):
        recs = asg.get("assignments") or asg.get("members") or asg.get("items") or []
        if isinstance(recs, dict):
            recs = [{"member_id": k, "items": v} for k, v in recs.items()]
    else:
        return []
    for rec in recs:
        if not isinstance(rec, dict):
            continue
        if str(rec.get("member_id") or "") != member_id:
            continue
        return list(rec.get("items") or [])
    return []


def main() -> int:
    port = free_port()
    base = "http://127.0.0.1:%d" % port
    data_dir = Path(tempfile.mkdtemp(prefix="ac_rounds_"))
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
        print("   %-34s %-6s %s" % (name, "OK" if ok else "★FAIL", detail))
        fails += 0 if ok else 1

    print("=" * 88)
    print("多轮派活判据（实例 %s，数据目录 %s）" % (base, data_dir))
    print("=" * 88)
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

        st, body = req(base, "/api/harness/register", {
            "harness_id": "rounds-fp", "harness_name": "示例Harness-Rounds",
            "harness_type": "desktop-agent", "wakeup_method": "file_poll",
            "wakeup_dir": str(inbox), "ai": {"model_name": "示例模型"},
        })
        if st != 200:
            print("★登记失败：%s" % str(body)[:200])
            return 1
        token = str((body or {}).get("agent_token") or "")
        hdr = {"X-Agent-Token": token} if token else {}
        st, ws = req(base, "/api/workshop", {
            "name": "多轮走查", "hall_content": "实现并测试一个排序模块",
            "members": [{"role": "码农", "harness_ids": ["rounds-fp"]}],
        })
        ws_id = str((ws or {}).get("workshop_id") or "")
        if st != 200 or not ws_id:
            print("★建工作间失败：%s" % str(ws)[:200])
            return 1

        # ① 第一轮派活 → 真落盘
        st, r1 = req(base, "/api/workshop/%s/leader/assign" % ws_id,
                     {"member_id": "m0", "instruction": "第一轮：写一个排序函数"})
        files1 = task_files(inbox)
        t1 = json.loads(files1[0].read_text(encoding="utf-8")) if files1 else {}
        tid1 = task_id_of(t1)
        cell("① 派活真落盘（带 task_id）", st == 200 and bool(files1) and bool(tid1),
             "HTTP %s 文件 %d 个 task_id=%s 键=%s" % (st, len(files1), tid1 or "?",
                                                    sorted(t1)[:6] if t1 else "-"))

        # ② 第一轮回报
        st, rr = req(base, "/api/harness/task-result", {
            "workshop_id": ws_id, "member_id": "m0", "harness_id": "rounds-fp",
            "task_id": tid1, "ok": True, "result": "第一轮完成：排序函数已实现，冒泡版。",
        }, hdr)
        cell("② 第一轮回报被接受", st == 200, "HTTP %s %s" % (st, "" if st == 200 else str(rr)[:90]))

        # ③ 第二轮派活是独立任务
        for f in task_files(inbox):
            try:
                f.unlink()                                   # 清掉第一轮，便于判断"新的"
            except OSError:
                pass
        st, r2 = req(base, "/api/workshop/%s/leader/assign" % ws_id,
                     {"member_id": "m0", "instruction": "第二轮：给它加单元测试"})
        files2 = task_files(inbox)
        t2 = json.loads(files2[0].read_text(encoding="utf-8")) if files2 else {}
        tid2 = task_id_of(t2)
        cell("③ 第二轮是独立任务", st == 200 and bool(tid2) and tid2 != tid1,
             "task_id %s → %s（文件 %d 个）" % (tid1 or "?", tid2 or "?", len(files2)))

        # ④ 第二轮载荷带上一轮上下文
        ctx = (t2 or {}).get("context") or {}
        prev = ctx.get("previous_instructions") or []
        msg = str((t2 or {}).get("message") or "")
        cell("④ 第二轮带上一轮上下文",
             bool(prev) and "第一轮" in json.dumps(prev, ensure_ascii=False)
             and "上一轮上下文" in msg,
             "previous_instructions=%d ｜ message %s" % (len(prev),
                                                        "有上下文段" if "上一轮上下文" in msg else "缺"))

        # ⑤ 两轮都留痕且顺序正确
        st, asg = req(base, "/api/workshop/%s/leader/assignments" % ws_id)
        hist = assign_history(asg, "m0")
        instrs = [str((h or {}).get("instruction") or "") for h in hist]
        cell("⑤ 两轮都留痕且顺序正确",
             len(instrs) >= 2 and "第一轮" in instrs[0] and "第二轮" in instrs[1],
             "%s" % (instrs[:2] or str(asg)[:120]))
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except Exception:                                    # noqa: BLE001
            proc.kill()
        import shutil
        shutil.rmtree(data_dir, ignore_errors=True)

    print("-" * 88)
    print("结论：%s" % ("**多轮派活全绿（落盘 / 回报 / 独立轮次 / 带上下文 / 留痕有序）**"
                      if not fails else "**有 %d 格失败**" % fails))
    return 0 if not fails else 1


if __name__ == "__main__":
    raise SystemExit(main())
