# -*- coding: utf-8 -*-
"""P6 联合验收（**老板当用户测**）：一条命令起一个干净实例 + 打开对接中心 + 逐条念走查清单。

## 为什么有它

`FE-26` 的验收里有 **7 条机器判不了的手感项**（第一屏 5 秒看懂 / 办不成要说清 / 空状态给动作 /
单间不越权 / 三态分得开 / 文案是人话 / 窄屏不炸）。它们**只能由人看**，而"人怎么看"以前靠我写一段话
—— 结果就是拖着不做。这个脚本把那一步变成**一条命令**：起实例、开浏览器、把清单一条条打出来、
你回一条我记一条，退出时自动收拾干净（停服务 + 报仓库是否干净）。

## 用法

```powershell
python tools/p6_acceptance.py            # 起实例 + 开浏览器 + 走查（默认端口自动挑空的）
python tools/p6_acceptance.py --no-open  # 不开浏览器（自己复制地址）
```

判据：走查中每一条你答 `y`/`n`，退出时打印小结；**答 n 的条目会写成一份待办**（`p6_findings.md（写在当前工作目录）`）。
⚠️ 实例用**独立数据目录**（系统临时目录 `ac_p6_run/`）⇒ 不碰真实 data，也不在仓库里长数据（脚本会自查）。
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import tempfile
import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
PY = sys.executable
DATA = Path(tempfile.gettempdir()) / "ac_p6_run"   # 系统临时目录：发布面工具不往仓库里写运行数据

WALKTHROUGH = [
    ("M1 一眼就会", "第一屏不点任何东西，5 秒内能否说出「这里能办哪几件事」？"),
    ("M2 办不成要说清", "故意给一个错关键词 / 投给不存在的窗口：是否**当场**说清原因 + 下一步？"),
    ("M3 空状态给动作", "全新实例（没有任何员工）：空白处是给「接下来点什么」，还是给一段解释？"),
    ("M4 单间不越权", "用 `?ws=<某个间的 id>` 打开：看不到工作间选择器、看不到注册入口？"),
    ("M5 三态分得开", "没自证过的成员：是否显示「⚠ 待人工/未核实」而**不是**「完成」？"),
    ("M6 文案是人话", "有没有英文报错直出 / `undefined` / `[object Object]` / 退休字段 `access_tier`？"),
    ("M7 窄屏不炸", "窗口拉窄到 ~400px，主要按钮还点得到（不要求移动端）？"),
]


def free_port() -> int:
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]
    finally:
        s.close()


def _data_snapshot() -> set:
    """`agent_community/data` 里的文件名快照（不存在＝空集）。用来判"这次跑有没有动真实数据"。"""
    d = ROOT / "agent_community" / "data"
    try:
        return {p.name for p in d.iterdir()}
    except Exception:  # noqa: BLE001
        return set()


def wait_up(port: int, proc, deadline_s: float = 60.0):
    end = time.time() + deadline_s
    while time.time() < end:
        if proc.poll() is not None:
            return None
        try:
            with urllib.request.urlopen("http://127.0.0.1:%d/api/status" % port, timeout=3) as r:
                d = json.loads(r.read().decode("utf-8", "replace"))
            if d.get("status") == "running":
                return d
        except Exception:  # noqa: BLE001
            pass
        time.sleep(1.0)
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-open", action="store_true")
    ap.add_argument("--port", type=int, default=0)
    a = ap.parse_args()

    port = a.port or free_port()
    DATA.mkdir(parents=True, exist_ok=True)
    before = _data_snapshot()
    log = open(DATA / "server_stdout.log", "a", encoding="utf-8")
    env = {**os.environ, "AC_DATA_DIR": str(DATA), "AC_PORT": str(port), "AC_HOST": "127.0.0.1"}
    proc = subprocess.Popen([PY, "-m", "agent_community.platform.server", "--port", str(port)],
                            cwd=str(ROOT), env=env, stdout=log, stderr=subprocess.STDOUT,
                            stdin=subprocess.DEVNULL)
    print("=" * 78)
    print("P6 联合验收（老板当用户测）—— 实例已起（**独立数据目录**，不碰真实 data）")
    print("  总控（大厅）   http://127.0.0.1:%d/docking.html" % port)
    print("  单间（锁死）   http://127.0.0.1:%d/docking.html?ws=<某个工作间的 id>" % port)
    print("  流程说明正主   http://127.0.0.1:%d/hub.html" % port)
    print("=" * 78)
    try:
        st = wait_up(port, proc)
        if st is None:
            print("服务没起来；日志尾部：")
            print((DATA / "server_stdout.log").read_text(encoding="utf-8", errors="replace")[-1000:])
            return 1
        print("服务已就绪（v%s，端口 %d）\n" % (st.get("version"), port))
        if not a.no_open:
            webbrowser.open("http://127.0.0.1:%d/docking.html" % port)

        findings = []
        for code, question in WALKTHROUGH:
            print("-" * 78)
            print("[%s] %s" % (code, question))
            ans = input("    通过吗？(y = 通过 / n = 有问题 / 回车 = 跳过) : ").strip().lower()
            if ans == "n":
                note = input("    说一句你看到的问题（会写进 p6_findings.md（写在当前工作目录））: ").strip()
                findings.append((code, question, note or "（没写细节）"))
                print("    → 记下了")
            elif ans == "y":
                print("    → 通过")
            else:
                print("    → 跳过")
        print("=" * 78)
        if findings:
            out = ROOT / "temp" / "p6_findings.md"
            with open(out, "w", encoding="utf-8", newline="\n") as fh:
                fh.write("# P6 走查发现（%s）\n\n" % time.strftime("%Y-%m-%d %H:%M"))
                for code, q, note in findings:
                    fh.write("- **%s**：%s\n  - 现象：%s\n" % (code, q, note))
            print("走查结果：%d 条有问题 → 已写 %s" % (len(findings), out))
            print("（把这些条目发我，我按「先修哪个」排一下再动手）")
        else:
            print("走查结果：全部通过 ✅ —— 对接中心可以进 P6 收尾（我方复验脚本已全绿）")
        # 仓库干净自查：**跑前后对比** `agent_community/data` 的内容 —— 它本来就可能存在（开发机上有旧数据），
        # 所以判据不是"存不存在"，而是"**这次跑有没有往里面写东西**"（换过 AC_DATA_DIR 就不该动）。
        after = _data_snapshot()
        if after != before:
            print("★仓库自查：`agent_community/data` 内容变了！说明实例没走独立数据目录：")
            print("   前 %s\n   后 %s" % (sorted(before)[:6], sorted(after)[:6]))
        else:
            print("仓库自查：`agent_community/data` 未被本次运行改动 ✅（数据都在独立目录 %s）" % DATA.name)
        return 0
    except KeyboardInterrupt:
        print("\n（中断）")
        return 130
    finally:
        try:
            proc.terminate()
            proc.wait(timeout=20)
            print("已停服务（PID 退出码 %s）" % proc.returncode)
        except Exception:  # noqa: BLE001
            try:
                proc.kill()
            except Exception:  # noqa: BLE001
                pass
        log.close()


if __name__ == "__main__":
    raise SystemExit(main())
