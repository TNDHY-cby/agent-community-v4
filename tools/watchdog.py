# -*- coding: utf-8 -*-
"""**平台看门狗（发布面版）**：平台服务挂了就把它拉起来；一条命令可查、可停、可自启。

## 为什么发布面需要它（2026-10-18 真事故）

项目里原本只有 `deploy/watchdog_all.py`，而 **`deploy/` 不在发布面** ⇒ 陌生人下载到的包里
**没有任何自愈能力**：服务一崩（或机器重启），平台就一直打不开，用户只能自己去敲命令。
同一天还实测出两个坑：带空格的解释器路径被拆坏（`D:\Program Files\…`）、
`.cmd` 里往中文路径重定向静默失败 —— 都记在下面。

## 用法

```bash
python tools/watchdog.py --check              # 一次性体检，exit 0=活着 / 1=挂了（并说清为什么）
python tools/watchdog.py --daemon             # 常驻守护（默认 30s 一次）
python tools/watchdog.py --daemon --interval 10
python tools/watchdog.py --status             # 打印"我要守护什么、怎么判活"（不连服务）
```

自启：`python tools/autostart.py enable`（Windows 走启动文件夹；**它优先指向本文件**）。

## 铁律（写死在这里，改之前先想清楚）

1. **零第三方依赖**：只用标准库 —— 它是"环境坏掉时救命"的那只，不能跟被救的东西一起坏；
2. **端口/地址只从一处来**：`agent_community.platform_address` 与 `env_spec`，**本文件不许出现端口字面量**；
3. **只守一件正事**：平台 HTTP 服务。A2A / gRPC 等附加面**默认不守**（要守得显式开，见 `AC_WATCHDOG_EXTRA`）；
4. **防重启风暴**：窗口期内重启次数超限 ⇒ 停下并**大声说明**，不许无限重启把日志刷爆；
5. **单实例锁**：两个看门狗会互相打架（重复启停），所以第二个必须直接退出并说明。
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent.parent
SERVER_MODULE = "agent_community.platform.server"


# ── 一处真相：端口 / 基址 / 数据目录 ────────────────────────────────
def _pa():
    sys.path.insert(0, str(ROOT))
    from agent_community import platform_address
    return platform_address


def base_url() -> str:
    try:
        return _pa().base_url().rstrip("/")
    except Exception:                                        # noqa: BLE001
        env = os.environ.get("AC_BASE_URL") or ""
        return env.rstrip("/")


def data_dir() -> Path:
    sys.path.insert(0, str(ROOT))
    try:
        from agent_community.platform import env_spec
        return Path(env_spec.data_dir())
    except Exception:                                        # noqa: BLE001
        return Path(os.environ.get("AC_DATA_DIR") or (ROOT / "agent_community" / "data"))


def log_path() -> Path:
    p = data_dir() / "logs" / "watchdog.log"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def log(msg: str) -> None:
    line = "[%s] %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg)
    print(line, flush=True)
    try:
        with open(log_path(), "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


# ── 判活 ────────────────────────────────────────────────────────
def probe(timeout: float = 5.0) -> tuple:
    """返回 (alive: bool, detail: str)。**只认真的应答**：HTTP 200 且 JSON 能解析。"""
    url = base_url()
    if not url:
        return False, "算不出平台地址（AC_BASE_URL / AC_PORT 都没配？）"
    token = os.environ.get("AC_SERVER_TOKEN") or os.environ.get("AC_TOKEN") or ""
    req = urllib.request.Request(url + "/api/status")
    if token:
        req.add_header("Authorization", "Bearer " + token)
        req.add_header("X-Agent-Token", token)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status != 200:
                return False, "HTTP %s" % resp.status
            body = json.loads(resp.read().decode("utf-8", "replace"))
        ver = body.get("version") or "?"
        return True, "version=%s" % ver
    except urllib.error.HTTPError as e:
        return False, "HTTP %s（服务在，但 /api/status 不健康）" % e.code
    except Exception as e:                                   # noqa: BLE001
        return False, "连不上：%s: %s" % (type(e).__name__, str(e)[:80])


# ── 起服务 ─────────────────────────────────────────────────────
def server_argv() -> list:
    """启动命令：**解释器与参数都由运行时决定**（不写死端口，走 server 自己的 env 解析）。"""
    return [sys.executable, "-u", "-m", SERVER_MODULE]


def spawn() -> tuple:
    """拉起平台服务，返回 (ok, detail)。失败要说清"为什么"，不许静默。"""
    argv = server_argv()
    try:
        subprocess.Popen(argv, cwd=str(ROOT), env=dict(os.environ),
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True, "已拉起：%s" % " ".join(Path(a).name if i == 0 else a
                                            for i, a in enumerate(argv))
    except Exception as e:                                   # noqa: BLE001
        return False, "拉起失败 %s: %s" % (type(e).__name__, e)


# ── 单实例锁（两个看门狗会互相打架）────────────────────────────
_LOCK = None


def acquire_lock():
    """成功返回 True；已有实例在跑返回 False（并说明）。"""
    global _LOCK
    path = data_dir() / "logs" / "watchdog.pid"
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fh = open(path, "a+", encoding="utf-8")
    except OSError:
        return True                                          # 拿不到锁文件也别阻止运行
    try:
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fh.close()
        return False
    fh.seek(0)
    fh.truncate()
    fh.write(str(os.getpid()))
    fh.flush()
    _LOCK = fh
    return True


# ── 主流程 ─────────────────────────────────────────────────────
def state_file() -> Path:
    """**可读的**看门狗状态（pid / 端口 / 数据目录 / 启动时间）。

    ★2026-10-19（做受控重启端点时发现）：`watchdog.pid` 是**单实例锁**，看门狗用
    `msvcrt.locking` / `fcntl.flock` **独占持有**它 ⇒ Windows 上别的进程去读会直接
    `PermissionError`。于是平台侧"我有没有看门狗"**永远读不到**，只能退化到猜命令行，
    结果把"其实有看门狗"判成"没有"（受控重启会被误拒；`/api/system/lifecycle` 的同名字段也不准）。

    因此**另写一份可读的状态**（锁文件职责不变）：纯文本 JSON、原子替换、失败不影响守护。
    """
    return data_dir() / "logs" / "watchdog.json"


def _state_port() -> str:
    """状态文件里的端口：环境变量优先，否则从 base_url 推断（都拿不到就留空，不编）。"""
    p = str(os.environ.get("AC_PORT") or "").strip()
    if p:
        return p
    try:
        from urllib.parse import urlparse
        return str(urlparse(base_url() or "").port or "")
    except Exception:                                        # noqa: BLE001
        return ""


def write_state_file(interval: float) -> None:
    """写可读状态（失败只记日志，**绝不**影响守护本身）。"""
    try:
        p = state_file()
        p.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "pid": os.getpid(),
            # ★2026-10-19：没设 AC_PORT 时从 base_url 推端口 ——
            #   平台侧 `watchdog_pids(server_port)` 靠它区分"本实例的看门狗"还是"别的实例的"，
            #   留空会让多实例场景分不清（宁可推出来，也不留空）。
            "port": _state_port(),
            "base_url": base_url(),
            "data_dir": str(data_dir()),
            "interval": float(interval),
            "started_at": time.time(),
        }
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, p)                      # 原子替换：读方永远看不到半截文件
    except Exception as e:                       # noqa: BLE001
        log("写状态文件失败（不影响守护）: %s: %s" % (type(e).__name__, e))


def clear_state_file() -> None:
    try:
        p = state_file()
        if p.is_file():
            p.unlink()
    except Exception:                            # noqa: BLE001
        pass


def status_report() -> int:
    print("看门狗（发布面版）会守护：")
    print("  目标：%s/api/status" % (base_url() or "（算不出地址）"))
    print("  判活：HTTP 200 且 JSON 可解析（只认真的应答）")
    print("  动作：挂了就起 `%s`" % " ".join([Path(server_argv()[0]).name] + server_argv()[1:]))
    print("  日志：%s" % log_path())
    print("  附加面：%s" % ("守 " + os.environ.get("AC_WATCHDOG_EXTRA", "") if os.environ.get("AC_WATCHDOG_EXTRA") else "不守（默认只守平台 HTTP）"))
    ok, detail = probe()
    print("  现在：%s（%s）" % ("活着" if ok else "**没活着**", detail))
    return 0 if ok else 1


def daemon(interval: float, window_sec: float, max_restarts: int) -> int:
    if not acquire_lock():
        log("⛔ 已有一只看门狗在跑（同一个数据目录只允许一只）—— 本进程退出")
        return 2
    write_state_file(interval)          # 可读状态：让平台/判据能看见"我在守"（锁文件读不了）
    log("看门狗启动：目标 %s ｜ 间隔 %.0fs ｜ 窗口 %.0fs 内最多重启 %d 次"
        % (base_url() or "（算不出地址）", interval, window_sec, max_restarts))
    attempts: list = []
    while True:
        ok, detail = probe()
        if ok:
            attempts.clear()
        else:
            log("平台没应答（%s）" % detail)
            now = time.time()
            attempts = [t for t in attempts if now - t < window_sec]
            if len(attempts) >= max_restarts:
                log("⛔ %.0fs 内已重启 %d 次仍不健康 —— 停止自动重启，请人工看日志：%s"
                    % (window_sec, len(attempts), log_path()))
                clear_state_file()      # 我停了 ⇒ 别让平台以为还有人在守
                return 3
            sent, sdetail = spawn()
            log(("重启：" if sent else "重启失败：") + sdetail)
            attempts.append(now)
            time.sleep(min(interval, 5.0))
        time.sleep(interval)


def main() -> int:
    ap = argparse.ArgumentParser(description="平台看门狗（发布面版；零第三方依赖）")
    ap.add_argument("--check", action="store_true", help="一次性体检（exit 0=活 / 1=挂）")
    ap.add_argument("--daemon", action="store_true", help="常驻守护")
    ap.add_argument("--status", action="store_true", help="打印守护配置与当前状态")
    ap.add_argument("--interval", type=float, default=30.0)
    ap.add_argument("--window", type=float, default=300.0, help="重启窗口（秒）")
    ap.add_argument("--max-restarts", type=int, default=3, help="窗口内最多重启次数")
    a = ap.parse_args()

    if a.status:
        return status_report()
    if a.daemon:
        return daemon(a.interval, a.window, a.max_restarts)
    ok, detail = probe()
    print(("活着：" if ok else "没活着：") + detail)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
