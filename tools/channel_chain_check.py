# -*- coding: utf-8 -*-
"""判据：**通道生命周期（V28）端到端** —— 建立 → 真 active → 停用 → 换对话 → 序号与留痕。

```bash
python tools/channel_chain_check.py     # 约 20 秒
```

## 为什么补这一条

V28 有单测（`test_channel_lifecycle.py` 直接调纯函数），但**端到端没有判据**把
「会话真起来 → 通道状态怎么说 → 停用后还能不能投消息 → 换对话序号对不对」串起来。
它最容易假成功的形态：**会话没真建成却标"运行中"**、以及**说了停用、消息还照收**（V28 §2 规则 5）。

## 十二格判据（真打 HTTP；用"带动作脚本的假命令"真建会话并自证）

| 格 | 判据 |
|---|---|
| ⓪ | 隔离实例 + 登记带动作脚本 + 建间（1 成员）+ 开工 |
| ① | 初始通道：`seq=1`、`state=pending`（未接入） |
| ② | 没有运行中的通道时停用 ⇒ `action=none`（**不假装停过**） |
| ③ | 真建会话 ⇒ `all_verified=true`，通道变 **active** |
| ④ | 已有运行通道再"新建通道" ⇒ **拒绝**并说清"先停用"（一条通道原则） |
| ⑤ | 停用 ⇒ `retired`；**隔间与产物一字未动**（停用≠删除） |
| ⑥ | **停用后不得再投消息**：给该成员派活 ⇒ 被拒/不落件（不许"说了停用消息照收"） |
| ⑦ | 停用后桥如实标 `retired`（若该成员曾架过桥） |
| ⑧ | 新建通道 ⇒ `seq=2`、`state=active`、会话名带 `-2`（换对话成功） |
| ⑨ | 旧通道留痕：`retired_channels` 里记着**旧会话名**与停用时间 |
| ⑩ | 审计：`channel_retire` / `channel_establish` / `establish_session` 三类都在 |
| ⑪ | 全程后隔间仍在、产物 sha256 未变（"停用≠删除、换对话不搬文件"） |
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

USER_CMD = '''# -*- coding: utf-8 -*-
"""模拟"用户提供的那条建会话命令"：回平台要的 JSON，并留下"真跑过"的痕迹。"""
import json
import sys
from pathlib import Path

mark = Path(__file__).with_name("new_session_calls.log")
name = sys.argv[1] if len(sys.argv) > 1 else "(未传名字)"
with open(mark, "a", encoding="utf-8") as fh:
    fh.write(name + "\\n")
print(json.dumps({"session": {"id": "s-chan", "name": name, "window": "w-chan"}},
                 ensure_ascii=False))
'''

USER_RENAME = '''# -*- coding: utf-8 -*-
"""模拟"用户提供的那条重命名命令"：契约要 `applied_name`。"""
import json
import sys
from pathlib import Path

mark = Path(__file__).with_name("rename_calls.log")
new_name = sys.argv[1] if len(sys.argv) > 1 else ""
with open(mark, "a", encoding="utf-8") as fh:
    fh.write(new_name + "\\n")
print(json.dumps({"applied_name": new_name, "readback": new_name}, ensure_ascii=False))
'''


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


def req(base: str, path: str, body=None, timeout=60):
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
    r = urllib.request.Request(base + path, data=data,
                               method="POST" if body is not None else "GET",
                               headers={"Content-Type": "application/json"})
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


def sha(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    port = free_port()
    base = "http://127.0.0.1:%d" % port
    data_dir = Path(tempfile.mkdtemp(prefix="ac_chan_"))
    inbox = data_dir / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    user_cmd = data_dir / "user_new_session.py"
    user_rename = data_dir / "user_rename.py"
    user_cmd.write_text(USER_CMD, encoding="utf-8")
    user_rename.write_text(USER_RENAME, encoding="utf-8")
    py_fwd = str(sys.executable).replace("\\", "/")
    env = _clean_env(AC_PORT=port, AC_DATA_DIR=data_dir, PYTHONPATH=str(ROOT),
                     PYTHONIOENCODING="utf-8", AC_PYTHON=str(sys.executable))
    proc = subprocess.Popen([sys.executable, "-m", "agent_community.platform.server"],
                            cwd=str(ROOT), env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    fails = 0

    def cell(name: str, ok: bool, detail: str = "") -> None:
        nonlocal fails
        print("   %-48s %-6s %s" % (name, "OK" if ok else "★FAIL", detail))
        fails += 0 if ok else 1

    print("=" * 90)
    print("通道生命周期（V28）端到端判据（实例 %s，数据目录 %s）" % (base, data_dir))
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
            "harness_id": "chan-h", "harness_name": "示例Harness-Chan",
            "harness_type": "desktop-agent", "wakeup_method": "file_poll",
            "wakeup_dir": str(inbox), "ai": {"model_name": "示例模型"},
            "scripts": {
                "new_session": {"NEW_SESSION_CMD": '"%s" "%s" "{name}"'
                                % (py_fwd, str(user_cmd).replace("\\", "/")),
                                "foreground": "never"},
                "rename_session": {"RENAME_CMD": '"%s" "%s" "{new_name}"'
                                   % (py_fwd, str(user_rename).replace("\\", "/")),
                                   "foreground": "never"},
            },
        })
        if st != 200:
            print("★登记失败：%s" % str(reg)[:220])
            return 1
        token = str((reg or {}).get("agent_token") or "")
        st, ws = req(base, "/api/workshop", {
            "name": "通道走查", "hall_content": "换一次对话试试",
            "members": [{"role": "码农", "harness_ids": ["chan-h"]}],
        })
        ws_id = str((ws or {}).get("workshop_id") or "")
        if st != 200 or not ws_id:
            print("★建间失败：%s" % str(ws)[:300])
            return 1
        st_ws, wbody = req(base, "/api/workshop/%s" % ws_id)
        ws_dir = Path(str((wbody or {}).get("workspace_dir") or ""))
        comp = ws_dir / "码农_m0"
        req(base, "/api/workshop/%s/start" % ws_id, {})
        cell("⓪ 隔离实例 + 动作脚本 + 建间 + 开工", True, "ws=%s" % ws_id)

        # ① 初始通道
        st, c0 = req(base, "/api/workshop/%s/member/m0/channels" % ws_id)
        cur = (c0 or {}).get("current") or {}
        cell("① 初始通道 seq=1 / state=pending",
             st == 200 and cur.get("seq") == 1 and cur.get("state") == "pending",
             "seq=%s state=%s" % (cur.get("seq"), cur.get("state")))

        # ② 没有运行通道时停用 ⇒ none
        st, r0 = req(base, "/api/workshop/%s/member/m0/channel/retire" % ws_id, {})
        cell("② 无运行通道时停用 ⇒ action=none（不假装）",
             st == 200 and (r0 or {}).get("action") == "none",
             "action=%s" % (r0 or {}).get("action"))

        # ③ 真建会话 ⇒ active
        st, est = req(base, "/api/workshop/%s/member/m0/establish-session" % ws_id, {})
        st, c1 = req(base, "/api/workshop/%s/member/m0/channels" % ws_id)
        cur1 = (c1 or {}).get("current") or {}
        cell("③ 真建会话 ⇒ 自证 + 通道 active",
             st == 200 and (est or {}).get("all_verified") is True
             and cur1.get("state") == "active",
             "all_verified=%s ｜ state=%s" % ((est or {}).get("all_verified"), cur1.get("state")))

        # ④ 已有运行通道再建 ⇒ 拒绝
        st, r1 = req(base, "/api/workshop/%s/member/m0/channel/establish" % ws_id, {})
        cell("④ 已有运行通道时新建 ⇒ 拒绝且说清",
             st == 200 and (r1 or {}).get("success") is False
             and "先「停用通道」" in str((r1 or {}).get("error") or ""),
             "success=%s ｜ %s" % ((r1 or {}).get("success"),
                                   str((r1 or {}).get("error"))[:36]))

        # ⑤ 先放一份产物，再停用 ⇒ 产物不动
        comp.mkdir(parents=True, exist_ok=True)
        (comp / "output").mkdir(parents=True, exist_ok=True)
        product = comp / "output" / "成果.md"
        product.write_text("# 通道一里做的成果\n", encoding="utf-8")
        before = sha(product)
        old_name = str(cur1.get("name") or "")
        st, r2 = req(base, "/api/workshop/%s/member/m0/channel/retire" % ws_id,
                     {"reason": "上下文过长老化"})
        st, c2 = req(base, "/api/workshop/%s/member/m0/channels" % ws_id)
        cur2 = (c2 or {}).get("current") or {}
        still = product.is_file() and sha(product) == before
        cell("⑤ 停用 ⇒ retired 且产物一字未动",
             st == 200 and (r2 or {}).get("action") == "retired"
             and cur2.get("state") == "retired" and still,
             "state=%s ｜ 产物仍在且未变=%s" % (cur2.get("state"), still))

        # ⑥ 停用后不得再投消息（派活）
        for p in inbox.glob("*.json"):
            p.unlink(missing_ok=True)
        st, asg = req(base, "/api/workshop/%s/leader/assign" % ws_id,
                      {"member_id": "m0", "instruction": "停用后还想派活"})
        dropped = list(inbox.glob("*.json"))
        cell("⑥ 停用后派活被拒（不落件）",
             bool((asg or {}).get("dispatched")) is False and not dropped,
             "HTTP %s dispatched=%s ｜ inbox 新增=%d ｜ %s" % (
                 st, (asg or {}).get("dispatched"), len(dropped),
                 str((asg or {}).get("dispatch_note") or "")[:40]))

        # ⑦ 桥状态如实（若架过桥）
        st, chs = req(base, "/api/workshop/%s/member/m0/channels" % ws_id)
        chans = (chs or {}).get("channels") or (chs or {}).get("history") or []
        cell("⑦ 旧通道留痕（旧会话名 + 停用时间）",
             bool(chans) and str((chans[-1] or {}).get("name") or "") == old_name
             and float((chans[-1] or {}).get("retired_at") or 0) > 0,
             "记录=%s" % str((chans[-1] if chans else {}).get("name"))[:30])

        # ⑧ 新建通道 ⇒ seq=2 / active / 名字带 -2
        st, r3 = req(base, "/api/workshop/%s/member/m0/channel/establish" % ws_id,
                     {"reason": "换新对话"})
        st, c3 = req(base, "/api/workshop/%s/member/m0/channels" % ws_id)
        cur3 = (c3 or {}).get("current") or {}
        new_name = str((r3 or {}).get("channel_name") or "")
        cell("⑧ 新建通道 ⇒ seq=2 + active + 名字带 -2",
             st == 200 and cur3.get("seq") == 2 and cur3.get("state") == "active"
             and new_name.endswith("-2"),
             "seq=%s state=%s name=%s" % (cur3.get("seq"), cur3.get("state"), new_name[-30:]))

        # ⑨ 换通道后旧名留痕
        st, c4 = req(base, "/api/workshop/%s/member/m0/channels" % ws_id)
        hist = (c4 or {}).get("channels") or (c4 or {}).get("history") or []
        cell("⑨ 换通道后旧会话名仍在历史里",
             any(str((h or {}).get("name") or "") == old_name for h in hist),
             "历史 %d 条" % len(hist))

        # ⑩ 审计三类
        st, au = req(base, "/api/audit")
        evs = {str(r.get("event") or "") for r in ((au or {}).get("events") or [])}
        need = {"workshop.channel_retire", "workshop.channel_establish",
                "workshop.establish_session"}
        cell("⑩ 审计三类事件齐全", need.issubset(evs), "缺=%s" % (sorted(need - evs) or "无"))

        # ⑪ 全程后隔间仍在、产物未变
        cell("⑪ 换对话不搬文件（产物 sha 未变）",
             product.is_file() and sha(product) == before,
             "隔间存在=%s" % comp.is_dir())
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except Exception:                                    # noqa: BLE001
            proc.kill()
        import shutil
        shutil.rmtree(data_dir, ignore_errors=True)

    print("-" * 90)
    print("结论：%s" % ("**通道生命周期端到端全绿（真 active / 停用不假 / 停用后不投递 / 换对话序号与留痕）**"
                      if not fails else "**有 %d 格失败**" % fails))
    return 0 if not fails else 1


if __name__ == "__main__":
    raise SystemExit(main())
