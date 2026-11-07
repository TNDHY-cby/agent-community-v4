# -*- coding: utf-8 -*-
"""判据（D后端-71）：监控室「会话观察窗」界面 —— **真浏览器 + 真 DOM**，API 用桩数据。

## 为什么要它

后端给了 `watch_kind` / `session_watchable` / `session_why` 与 `GET /api/monitor/{hid}/session`，
但界面有三条硬要求（D后端-71 §3），只查源码文本判不出来：

1. 两种格子视觉上区分（画面 vs 会话）；2. `capable=false` 必须显示 `why`，**不许显示空会话**；
3. 会话里它说"对得上/已完成"**不算平台核过**（要钉在格子上）。

## 怎么跑

```bash
python tools/monitor_session_ui_check.py              # 起静态服务 + 真浏览器跑 9 格
python tools/monitor_session_ui_check.py --keep-shot  # 保留截图并打印路径
AC_MONITOR_UI_CHROME=<chrome.exe>  # 指定浏览器（不给就在 ms-playwright 缓存里挑最新的）
AC_NODE=<node.exe>                 # 指定 node（不给就 which node，再找 DSH 自带 node）
```

退出码：0 = 9 格全过；1 = 有格没过；2 = 环境不满足（没 node / 没 playwright / 没浏览器）——
**环境不满足如实报 exit 2，不算通过**。
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "agent_community" / "frontend"
CJS = Path(__file__).resolve().parent / "monitor_session_ui_check.cjs"

#: 找 node：环境变量 > DSH 自带 > PATH。
#: ⚠️ 顺序有讲究：本机 PATH 里的 node v22 加载 playwright-core 会**直接崩**（0xC0000005，
#:    rc=-1073741819、输出全空）——所以要先挑能用的，并且**真探测**过再用（见 _node_ok）。
NODE_CANDIDATES = [
    os.environ.get("AC_NODE", ""),
    # ⚠️ 用 `expanduser` 而不是写死用户名：这是**前端这台机器**的 DSH 自带 node，
    #    写死 `C:\Users\<名字>` 换个人就跑不了（发布面可移植性扫描会直接判红）。
    os.path.expanduser(
        r"~\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\node\bin\node.exe"),
    shutil.which("node") or "",
]

#: playwright-core 的可能位置（本机常见两处；也可用 AC_PW_NODE_PATH 指定）
PW_CANDIDATES = [
    os.environ.get("AC_PW_NODE_PATH", ""),
    r"D:\Cache\npm\_npx\31e32ef8478fbf80\node_modules",
    r"D:\Cache\npm\_npx\dea9475797100b20\node_modules",
]


def _node_ok(node: str, node_path: str) -> tuple:
    """真探一次：这个 node 能不能 require playwright-core。返回 (ok, 原因)。"""
    env = dict(os.environ, NODE_PATH=node_path)
    try:
        r = subprocess.run([node, "-e", "require('playwright-core');console.log('ok')"],
                           env=env, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=60)
    except Exception as e:                                     # noqa: BLE001 — 探不通就是不通，原因要留
        return False, "%s: %s" % (type(e).__name__, e)
    if "ok" in (r.stdout or ""):
        return True, ""
    return False, "rc=%s stdout=%r stderr=%r" % (r.returncode, (r.stdout or "")[:80], (r.stderr or "")[:120])


def _pick(cands):
    for c in cands:
        if c and Path(c).exists():
            return c
    return ""


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class _Quiet(SimpleHTTPRequestHandler):
    def log_message(self, *a):  # noqa: D401 — 静音：判据只要结果
        return


def main() -> int:
    ap = argparse.ArgumentParser(description="监控室会话观察窗界面判据（9 格）")
    ap.add_argument("--keep-shot", action="store_true", help="保留截图并打印路径")
    a = ap.parse_args()

    node_path = _pick(PW_CANDIDATES)
    if not node_path:
        print("环境不满足：找不到 playwright-core 的 node_modules —— 不算通过（设 AC_PW_NODE_PATH）", file=sys.stderr)
        return 2
    node, why_fail = "", []
    for cand in NODE_CANDIDATES:
        if not cand or not Path(cand).exists():
            continue
        ok, why = _node_ok(cand, node_path)
        if ok:
            node = cand
            break
        why_fail.append("%s → %s" % (cand, why))
    if not node:
        print("环境不满足：没有能加载 playwright-core 的 node —— **不算通过**", file=sys.stderr)
        for line in why_fail:
            print("  · " + line, file=sys.stderr)
        return 2
    print("用 node：%s" % node)

    port = _free_port()
    handler = partial(_Quiet, directory=str(FRONTEND))
    httpd = ThreadingHTTPServer(("127.0.0.1", port), handler)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()

    shot = Path(tempfile.gettempdir()) / "monitor_session_ui_check.png"
    env = dict(os.environ, NODE_PATH=node_path)
    try:
        r = subprocess.run([node, str(CJS), "http://127.0.0.1:%d" % port, str(shot)],
                           cwd=str(ROOT), env=env, capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=180)
    except subprocess.TimeoutExpired:
        print("超时：浏览器 180 秒没跑完 —— 不算通过", file=sys.stderr)
        httpd.shutdown()
        return 2
    except Exception as e:                                     # noqa: BLE001 — 环境问题要说清，不静默
        print("跑判据时出错：%s: %s —— 不算通过" % (type(e).__name__, e), file=sys.stderr)
        httpd.shutdown()
        return 2
    finally:
        httpd.shutdown()

    out = (r.stdout or "") + (("\n[stderr] " + r.stderr) if (r.stderr or "").strip() else "")
    print(out.strip())
    if a.keep_shot and shot.exists():
        print("截图保留在：%s" % shot)
    elif shot.exists():
        try:
            shot.unlink()
        except OSError:  # noqa: BLE001 — 删不掉就留着，不影响判据
            pass
    if r.returncode == 0:
        return 0
    if r.returncode == 2:
        print("（脚本异常或环境不满足：按**不算通过**处理）", file=sys.stderr)
        return 2
    if r.returncode != 0 and not (r.stdout or "").strip():
        print("子进程没有输出就退出了（rc=%s）—— 环境问题，不算通过；上面已打印原因" % r.returncode,
              file=sys.stderr)
        return 2
    return 1


if __name__ == "__main__":
    sys.exit(main())
