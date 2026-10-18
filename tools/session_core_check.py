# -*- coding: utf-8 -*-
"""判据：**「建立会话」这条核心链路真能走通**（步骤 4/7/8/9，端到端、真起实例、真跑命令）。

```bash
python tools/session_core_check.py      # 约 10~20 秒
```

## 为什么单列这一条

老板最初骂的是「**唤不醒**」。协议/桥/唤醒脚本已经用矩阵验过了，但**平台侧"把 agent 的会话建立起来"**
（`POST /api/workshop/{ws}/member/{mid}/establish-session`）一直没有**端到端**判据：
单元测试用的是假对象，走不到"用户给一条命令 → 平台真去跑 → 会话建立并自证"。

## 五格判据

| 格 | 判据 |
|---|---|
| ① 登记能带动作脚本 | `POST /api/harness/register` 的 `scripts` 被接受（`new_session`/`rename_session`） |
| ② 工作间与成员 | 建间 + 成员绑到该 harness（`m0`） |
| ③ 会话真建立且**自证** | `success=true`、`all_verified=true`、`steps.4_new_session.verified=true` 且带 `evidence.session` |
| ④ **命令真被执行了** | 我们给的 `NEW_SESSION_CMD` 留下了一行"我真的跑过"的痕迹（不是"平台说跑了"） |
| ⑤ 名字由平台给且**含项目名** | `<ws>-<项目>-<职位>-<成员id>`（老板口径：项目名要给用户看） |
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
FAKE_ACP = ROOT / "agent_community" / "tests" / "_fake_acp_agent.py"


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


USER_CMD = '''# -*- coding: utf-8 -*-
"""模拟"用户提供的那条建会话命令"：打印平台要的 JSON，并留下"我真跑过"的痕迹。"""
import json
import sys
from pathlib import Path

mark = Path(__file__).with_name("user_cmd_calls.log")
name = sys.argv[1] if len(sys.argv) > 1 else "(未传名字)"
with open(mark, "a", encoding="utf-8") as fh:
    fh.write(name + "\\n")
print(json.dumps({"session": {"id": "s-core-1", "name": name, "window": "w-core-1"}},
                 ensure_ascii=False))
'''


USER_RENAME = '''# -*- coding: utf-8 -*-
"""模拟"用户提供的那条重命名命令"：**契约要 `applied_name`**（见 rename_session.tmpl）。

⚠️ 这是判据教给我的一件事：`new_session` 要回 `{"session": {...}}`，
   而 `rename_session` 要回 `{"applied_name": "<新名字>"}` —— 两者的返回形状**不一样**，
   我第一次按 `{"session": ...}` 写，结果第 8 步被判"未核实"（平台是对的）。
"""
import json
import sys
from pathlib import Path

mark = Path(__file__).with_name("user_rename_calls.log")
new_name = sys.argv[1] if len(sys.argv) > 1 else ""
with open(mark, "a", encoding="utf-8") as fh:
    fh.write(new_name + "\\n")
print(json.dumps({"applied_name": new_name, "readback": new_name}, ensure_ascii=False))
'''


def req(base: str, path: str, body=None, timeout=60):
    data = None if body is None else json.dumps(body).encode("utf-8")
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
        # ★服务还没起来时是"拒连"（URLError / ConnectionRefused），**必须兜住**：
        #   第一版没兜 ⇒ Linux 侧判据直接抛 traceback（`wait` 循环还没来得及重试）。
        return 0, {"error": "%s: %s" % (type(e).__name__, str(e)[:120])}


def main() -> int:
    port = free_port()
    base = "http://127.0.0.1:%d" % port
    data_dir = Path(tempfile.mkdtemp(prefix="ac_session_core_"))
    user_cmd = data_dir / "user_cmd.py"
    user_cmd.write_text(USER_CMD, encoding="utf-8")
    user_rename = data_dir / "user_rename.py"
    user_rename.write_text(USER_RENAME, encoding="utf-8")
    marker = data_dir / "user_cmd_calls.log"
    marker_rename = data_dir / "user_rename_calls.log"

    py_fwd = str(sys.executable).replace("\\", "/")
    env = _clean_env(AC_PORT=port, AC_DATA_DIR=data_dir, PYTHONPATH=str(ROOT),
                     PYTHONIOENCODING="utf-8", AC_PYTHON=str(sys.executable))
    proc = subprocess.Popen([sys.executable, "-m", "agent_community.platform.server"],
                            cwd=str(ROOT), env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    fails = 0

    def cell(name: str, ok: bool, detail: str = "") -> None:
        nonlocal fails
        print("   %-32s %-6s %s" % (name, "OK" if ok else "★FAIL", detail))
        fails += 0 if ok else 1

    print("=" * 84)
    print("「建立会话」核心链路判据（实例 %s，数据目录 %s）" % (base, data_dir))
    print("=" * 84)
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

        # ① 登记（acp + 动作脚本）
        # ⚠️ 占位符**必须带引号**：名字里有中文，不加引号时 shell/cmd 的编码会把参数弄坏
        #   ⇒ rename 模板里 `applied != new_name` ⇒ 被判"未核实"（我第一次就这么踩的）。
        cmd_new = '"%s" "%s" "{name}"' % (py_fwd, str(user_cmd).replace("\\", "/"))
        cmd_ren = '"%s" "%s" "{new_name}"' % (py_fwd, str(user_rename).replace("\\", "/"))
        st, body = req(base, "/api/harness/register", {
            "harness_id": "core-acp", "harness_name": "示例Harness-Core",
            "harness_type": "desktop-agent", "wakeup_method": "acp",
            "acp_command": '"%s" "%s"' % (py_fwd, str(FAKE_ACP).replace("\\", "/")),
            "ai": {"model_name": "示例模型"},
            "scripts": {
                "new_session": {"NEW_SESSION_CMD": cmd_new, "foreground": "never"},
                "rename_session": {"RENAME_CMD": cmd_ren, "foreground": "never"},
            },
        })
        cell("① 登记带走动作脚本", st == 200, "HTTP %s %s" % (st, "" if st == 200 else str(body)[:110]))
        if st != 200:
            return 1

        # ② 建工作间 + 成员绑到该 harness
        st, ws = req(base, "/api/workshop", {
            "name": "核心走查任务", "hall_content": "写一个排序函数并自测",
            "members": [{"role": "码农", "harness_ids": ["core-acp"]}],
        })
        ws_id = str((ws or {}).get("workshop_id") or (ws or {}).get("id") or "")
        cell("② 建工作间 + 成员绑 harness", st == 200 and bool(ws_id),
             "HTTP %s ws=%s" % (st, ws_id or "?"))
        if st != 200 or not ws_id:
            return 1

        # ③ 建立会话
        st, out = req(base, "/api/workshop/%s/member/m0/establish-session" % ws_id, {})
        steps = (out or {}).get("steps") or {}
        ns = steps.get("4_new_session") or {}
        ev = (ns.get("evidence") or {}).get("session") or {}
        cell("③ 会话建立且自证", st == 200 and (out or {}).get("success") is True
             and (out or {}).get("all_verified") is True and ns.get("verified") is True
             and bool(ev.get("id")),
             "HTTP %s all_verified=%s session=%s ｜ %s" % (
                 st, (out or {}).get("all_verified"), ev.get("id"),
                 " ".join("%s=%s(%s)" % (k, (v or {}).get("verified"),
                                        str((v or {}).get("error") or "")[:40])
                          for k, v in steps.items())))

        # ④ 命令真的被执行（我方留痕：建会话 + 重命名各一次）
        ran_a = marker.exists() and marker.read_text(encoding="utf-8", errors="replace").strip() != ""
        ran_b = marker_rename.exists() and marker_rename.read_text(
            encoding="utf-8", errors="replace").strip() != ""
        cell("④ 用户命令真被执行（两条）", ran_a and ran_b,
             "建会话 %s / 重命名 %s" % ("有痕迹" if ran_a else "无",
                                       "有痕迹" if ran_b else "无"))

        # ⑤ 名字由平台给且含项目名
        name = str((out or {}).get("name") or "")
        cell("⑤ 名字含项目名（平台给）", ws_id in name and "核心走查任务" in name and "m0" in name, name)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except Exception:                                    # noqa: BLE001
            proc.kill()
        import shutil
        shutil.rmtree(data_dir, ignore_errors=True)

    print("-" * 84)
    print("结论：%s" % ("**「建立会话」核心链路全绿（真跑命令 + 自证 + 平台命名）**"
                      if not fails else "**有 %d 格失败**" % fails))
    return 0 if not fails else 1


if __name__ == "__main__":
    raise SystemExit(main())
