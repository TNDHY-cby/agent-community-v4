# -*- coding: utf-8 -*-
"""tools/first_run_check：**陌生人首次运行走查**（把"装好就能用"变成可执行验收）。

老板 2026-10-15：
    「**全部改好，我朋友去 GitHub 下载来测试**」
    「完善开发框架，以后的开发也要注意产品不是私人订制的」

⇒ 判据不再是"我这台能跑"，而是**换一个目录 + 换一个端口 + 换一个数据目录、不改一行代码，
照 README 那五步能走完**。本工具就是这条判据的执行体（CI 与发布前都跑）。

## 它做什么（全部按真结果，不读源码）

1. 把仓库 clone 到一个**全新目录**（只取已提交内容 —— 与"朋友从 GitHub 下载"等价）；
2. 用**换过的端口 + 换过的数据目录**起服务（`AC_PORT` / `AC_DATA_DIR`）；
3. 照 README 跑：`cli task` → `cli task-result` → `cli stop`；
4. 断言：任务提交成功、`task-result` 能给出结果**或**说清"为什么暂时没有"、
   停服务后端口释放、**仓库保持干净**（不许在仓库里长出 `agent_community/data/`）。

## 它**不**做什么（如实说明）

- **不测 `pip install`**：那需要外网与独立解释器，由 CI 的两个作业真跑（干净 Linux + Windows）。
  本工具假设当前解释器已装好依赖（因此它能离线、快速地在开发机上跑）。
- 不测 GUI（无图形环境本来就起不来）；GUI 的兜底由 `tests/test_cli_contract.py` 守。

用法：
    python tools/first_run_check.py                    # 源 = 当前仓库（CI 里就是它）
    python tools/first_run_check.py --src <发布副本>    # dev 里应指向**发布副本**（见下）
    python tools/first_run_check.py --keep             # 跑完不删临时目录（排障用）

⚠️**源要选对**（第一次真跑就踩到）：本工具 clone 的是**已提交内容**。
   · 陌生人从 GitHub 拿到的 = **发布副本（oss）** 的已提交内容；
   · dev 副本的 HEAD 常常是旧的（发布走的是 `publish_acv4.ps1` → 在 oss 里提交）。
   ⇒ 在 dev 里做"发布前验收"时必须 `--src <发布副本路径>`，否则测的是 dev 的旧提交
     （实测：不加 --src 会拿到旧 CLI，`cli task` 照样 400 —— 那是**假红**，会误导人以为是产品坏了）。
   路径不写死：由 `--src` / `AC_SRC` 传入。
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
import time
import urllib.request
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

ROOT = Path(__file__).resolve().parents[1]
FAILS: list = []


def step(title: str, ok: bool, detail: str = "") -> None:
    print("%s %s%s" % ("✓" if ok else "✗", title, ("  —— " + detail) if detail else ""))
    if not ok:
        FAILS.append(title)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def http(port: int, path: str):
    try:
        with urllib.request.urlopen("http://127.0.0.1:%d%s" % (port, path), timeout=10) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except Exception as e:  # noqa: BLE001
        return -1, str(e)


def main() -> int:
    ap = argparse.ArgumentParser(description="陌生人首次运行走查")
    ap.add_argument("--port", type=int, default=0, help="用哪个端口（默认自动挑空闲端口）")
    ap.add_argument("--src", default="", help="clone 源（默认＝当前仓库；dev 里请指向发布副本）")
    ap.add_argument("--keep", action="store_true", help="保留临时目录（排障）")
    args = ap.parse_args()
    port = args.port or free_port()
    src = Path(args.src or os.environ.get("AC_SRC") or ROOT).resolve()
    if not (src / ".git").exists():
        print("源不是 git 仓库：%s（本工具只取**已提交内容**）" % src)
        return 2
    print("clone 源：%s" % src)

    tmp = Path(tempfile.mkdtemp(prefix="ac_first_run_"))
    clone = tmp / "clone"
    data = tmp / "data"
    home = tmp / "home"                     # ★隔离 HOME：PID 文件/配置都落在临时目录，
    home.mkdir(parents=True, exist_ok=True)  #   绝不动开发机上那份 ~/.agent_community/
    env = {**os.environ, "AC_PORT": str(port), "AC_DATA_DIR": str(data),
           "HOME": str(home), "USERPROFILE": str(home), "PYTHONIOENCODING": "utf-8"}
    env.pop("AC_VERIFY_NO_START_BRIDGE", None)
    srv = None
    try:
        r = subprocess.run(["git", "clone", "--quiet", str(src), str(clone)],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        step("全新 clone（与「朋友从 GitHub 下载」等价）", r.returncode == 0, (r.stderr or "")[-160:])
        if r.returncode != 0:
            return 1

        def cli(*a):
            return subprocess.run([sys.executable, "-m", "agent_community.cli", *a],
                                  cwd=str(clone), env=env, capture_output=True, text=True,
                                  encoding="utf-8", errors="replace", timeout=180)

        # ★照 README 第 2 步：用 `cli start --no-gui` 起（它才会写 PID 文件 ⇒ `cli stop` 才管用；
        #   直接起 server 会让第 5 步假红，且与"陌生人实际怎么做"不一致）
        p_start = cli("start", "--no-gui", "--port", str(port))
        ready = False
        for _ in range(30):
            if http(port, "/api/status")[0] == 200:
                ready = True
                break
            time.sleep(1)
        step("① 换端口 + 换数据目录起服务（不改一行代码）", ready,
             ((p_start.stdout or "") + (p_start.stderr or "")).strip()[-160:])
        if not ready:
            return 1

        out = (lambda p: (p.stdout or "") + (p.stderr or ""))(cli("task", "首次运行走查：写一个排序函数"))
        task_id = next((t for t in out.split() if len(t) == 8 and t.isalnum()), "")
        step("② README 第 3 步 `cli task` 提交成功", "任务已提交" in out and bool(task_id),
             out.strip()[:160])

        res_out = ""
        if task_id:
            for _ in range(12):
                time.sleep(3)
                p = cli("task-result", task_id)
                res_out = (p.stdout or "") + (p.stderr or "")
                if "结果:" in res_out or "没有产生有效结果" in res_out:
                    break
                if "暂无结果" in res_out and "进行中" not in res_out:
                    break
            step("③ README 第 4 步 `task-result` 给出结果**或**说清为什么没有",
                 ("结果:" in res_out) or ("没有产生有效结果" in res_out) or ("暂无结果" in res_out),
                 res_out.strip()[:200])
            step("③b 不许出现「空原因」的错误（`[Error: ]`）",
                 "[Error: ]" not in res_out and "[Error:]" not in res_out, res_out.strip()[:160])
            # ★③c（V69）：失败要**说人话** —— 只给 `[Errno 11001] getaddrinfo fail`
            #   等于没告诉陌生人"要干什么"。所以失败时必须同时出现「原因」与「怎么办」。
            #   这一格防的是：以后有人把翻译层去掉、又退回裸技术串。
            if "没有产生有效结果" in res_out:
                step("③c AI 失败时说人话（有「原因：」和「怎么办：」，不只给技术串）",
                     ("原因：" in res_out) and ("怎么办：" in res_out), res_out.strip()[:220])

        p = cli("stop")
        time.sleep(3)
        step("④ README 第 5 步 `cli stop` 停掉且端口释放",
             http(port, "/api/status")[0] != 200, (p.stdout or "").strip()[:120])

        repo_data = clone / "agent_community" / "data"
        step("⑤ 仓库保持干净（换过 AC_DATA_DIR 后不许在仓库里长出 data/）",
             not repo_data.is_dir(),
             str(sorted(x.name for x in repo_data.iterdir()))[:160] if repo_data.is_dir() else "")
        step("⑤b 数据落在 AC_DATA_DIR 下（不在仓库里）",
             data.is_dir() and any(data.iterdir()) if data.is_dir() else False,
             "（%s）" % (sorted(p.name for p in data.iterdir())[:6] if data.is_dir() else "目录不存在"))

        # ⑥ **自愈**（2026-10-18 新增）：陌生人拿到包里"服务崩了能自己回来"吗？
        #    为什么必须在这一层验：`tools/watchdog.py` 是**发布面**的东西 —— 只有对着**全新 clone** 跑，
        #    才能证明"别人下载到的那个包"真的有自愈能力（在 dev 里验等于只验了我这台机器）。
        wd = clone / "tools" / "watchdog.py"
        cell_theirs = clone / "tools" / "check_watchdog.py"
        if not wd.exists():
            step("⑥ 包里自带看门狗（tools/watchdog.py）", False, "发布面缺这个文件 ⇒ 陌生人没有自愈能力")
        else:
            step("⑥ 包里自带看门狗（tools/watchdog.py）", True, "")
            if cell_theirs.exists():
                r6 = subprocess.run([sys.executable, str(cell_theirs)], cwd=str(clone), env=env,
                                    capture_output=True, text=True, encoding="utf-8",
                                    errors="replace", timeout=300)
                out6 = ((r6.stdout or "") + (r6.stderr or "")).strip()
                verdict = ([ln.strip() for ln in out6.splitlines() if "结论" in ln]
                           or out6.splitlines()[-1:])
                step("⑥b 在全新 clone 里跑自愈判据（能起 + 崩了能再起）", r6.returncode == 0,
                     " ".join(verdict)[:200])
            else:
                step("⑥b 在全新 clone 里跑自愈判据", False, "发布面缺 tools/check_watchdog.py")
        # ⑤c **凭据体检**（V86，2026-10-22 新增）：陌生人拿到包，能不能自己查"我填的 key 到底行不行"？
        #    为什么在这一层验：`tools/credential_check.py` 是**发布面**的东西 ——
        #    只有对着**全新 clone**（空数据目录、没体检过）跑，才能证明"别人下载到的那份"真能体检，
        #    而且要验证它**不撒谎**：没凭据就说没凭据，不许假装"全部通过"。
        ccl = clone / "tools" / "credential_check.py"
        if not ccl.exists():
            step("⑤c 包里自带凭据体检（tools/credential_check.py）", False,
                 "发布面缺这个文件 ⇒ 陌生人没法自己判断 key 填对没有")
        else:
            r5c = subprocess.run([sys.executable, str(ccl), "--no-record", "--json"],
                                 cwd=str(clone), env=env, capture_output=True, text=True,
                                 encoding="utf-8", errors="replace", timeout=180)
            out5c = (r5c.stdout or "").strip()
            try:
                # ⚠️ 这里**只取开头那个 JSON 对象**，不要求整段输出都是 JSON：
                #    被判据的对象是"别人下载到的那个包"，它可能是**旧一点的版本**
                #    （v4.33.0 的 CLI 会在 JSON 后面再打一行"结论"）。
                #    要求"纯 JSON"会把自己锁死：发布前判据跑在旧发布面上 ⇒ 永远红 ⇒ 永远发不出去。
                #    真正要防的是"跑不起来 / 没有机器可读结果"，不是"末尾多一行人话"。
                _j, _end = json.JSONDecoder().raw_decode(out5c)
                ok5c = isinstance(_j.get("summary"), dict) and isinstance(_j.get("found"), list)
                detail5c = "找到 %s 处（弱值 %s / 桥产物明文 %s / 未扫成 %s）%s" % (
                    _j["summary"].get("found"), _j["summary"].get("weak"),
                    _j["summary"].get("plaintext_in_bridge"), _j["summary"].get("skipped"),
                    "（输出末尾还有非 JSON 内容，已忽略）" if out5c[_end:].strip() else "")
            except Exception as _e5c:            # noqa: BLE001 — 输出里根本没有 JSON 就是要报的结论
                ok5c, detail5c = False, "输出里没有可解析的 JSON（%s）：%s" % (_e5c, out5c[:120])
            step("⑤c 包里自带凭据体检（只给位置与指纹，绝不出明文）", ok5c, detail5c)
    finally:
        if srv is not None:
            try:
                srv.terminate()
                srv.wait(timeout=10)
            except Exception:  # noqa: BLE001
                try:
                    srv.kill()
                except Exception:  # noqa: BLE001
                    pass
        if args.keep:
            print("（临时目录保留：%s）" % tmp)
        else:
            shutil.rmtree(tmp, ignore_errors=True)

    print("-" * 66)
    if FAILS:
        print("⇒ 首次运行走查**未通过**：%s" % FAILS)
        print("  判据与六种「私人定制」形态见 PRODUCTIZATION.md")
        return 1
    print("OK：陌生人照 README 五步能走完，且仓库保持干净。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
