"""watchdog.py — 外端Agent生产合作社（External Agent Community） v4 平台进程看门狗（部署层小工具，R3，2026-09-08）
守护 18920 server 进程与受管桥（桥由 server lifespan 自动拉起，守护 server 即间接守护桥）。

模式：
  python agent_community/tools/watchdog.py --check                # 单次健康检查（exit 0=健康 / 1=异常）
  python agent_community/tools/watchdog.py --daemon               # 循环守护（默认 30s 间隔，异常时按原命令行重启）
  python agent_community/tools/watchdog.py --daemon --port 18920 --interval 30

行为要点：
  1) 健康判定 = TCP 可达 && GET /api/status 返回 {"status":"running"}；
  2) 检测到服务不可达：先查端口监听进程——若存在且命令行确为本平台 server（僵死），杀后重启；
     若无监听进程：若存在 GUI 壳（命令行含 gui 且含同端口）→ 只守护绝不重复拉起（防双 server 冲突）；
     否则按“原命令行”（上次 spawn 记录 > 现有 server 命令行 > 默认 -m agent_community.platform.server）拉起并轮询健康；
  3) 日志写 <项目根>/agent_community/data/logs/watchdog.log（目录不存在自动创建）；
  4) 全程标准库实现，无第三方依赖，不侵入 server.py 主逻辑。
"""
import argparse
import json
import logging
import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_ROOT / "agent_community" / "data"
LOG_DIR = DATA_DIR / "logs"
DEFAULT_PORT = 18920
DEFAULT_INTERVAL = 30.0
HEALTH_URL = "http://127.0.0.1:{port}/api/status"

logger = logging.getLogger("watchdog")


# ── 基础探测 ──────────────────────────────────────────────────
def _port_open(port: int, timeout: float = 3.0) -> bool:
    """TCP 层端口可达性。"""
    try:
        with socket.create_connection(("127.0.0.1", int(port)), timeout=timeout):
            return True
    except OSError:
        return False


def _http_health(port: int, timeout: float = 5.0) -> tuple[bool, str]:
    """HTTP 健康检查：GET /api/status，要求 JSON status == running。"""
    try:
        with urllib.request.urlopen(HEALTH_URL.format(port=port), timeout=timeout) as resp:
            if resp.status != 200:
                return False, f"HTTP {resp.status}"
            body = json.loads(resp.read().decode("utf-8", errors="replace"))
            if body.get("status") == "running":
                return True, "running"
            return False, f"status={body.get('status')!r}"
    except Exception as e:  # noqa: BLE001 — 探测失败一律视为不健康
        return False, f"{type(e).__name__}: {e}"


def probe(port: int, http_timeout: float = 5.0) -> tuple[bool, str]:
    """一次完整健康判定：TCP + HTTP。"""
    if not _port_open(port):
        return False, "TCP 不可达（无进程监听该端口）"
    ok, detail = _http_health(port, http_timeout)
    return ok, f"HTTP /api/status: {detail}"


# ── 进程发现 ──────────────────────────────────────────────────
def _run_powershell(script: str, timeout: float = 15.0) -> str:
    try:
        p = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, text=True, timeout=timeout,
        )
        return (p.stdout or "").strip() + ("\n" + (p.stderr or "").strip() if p.stderr else "")
    except Exception as e:  # noqa: BLE001
        return f"__ERR__{e}"


def _listener_pids(port: int) -> list[int]:
    """返回监听该端口的 PID 列表（netstat -ano 解析，兼容中文系统）。"""
    pids: list[int] = []
    try:
        out = subprocess.run(
            ["netstat", "-ano", "-p", "tcp"], capture_output=True, text=True, timeout=10.0,
        ).stdout or ""
        needle = f":{port}"
        for line in out.splitlines():
            parts = line.split()
            if len(parts) >= 5 and "LISTEN" in line.upper():
                if needle in parts[1] or needle in parts[2]:
                    try:
                        pids.append(int(parts[-1]))
                    except ValueError:
                        continue
    except Exception as e:  # noqa: BLE001
        logger.warning("netstat 解析失败: %s", e)
    return sorted(set(pids))


def _pid_alive(pid: int) -> bool:
    """PID 是否存活（tasklist 单 PID 过滤）。"""
    try:
        out = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
            capture_output=True, text=True, timeout=10.0,
        ).stdout or ""
        return "INFO: No tasks" not in out and str(pid) in out
    except Exception:  # noqa: BLE001
        return False


def _cmdline_of(pid: int) -> str:
    """取指定 PID 的命令行（WMI）。"""
    if not pid:
        return ""
    out = _run_powershell(
        f"$p = Get-CimInstance Win32_Process -Filter \"ProcessId={pid}\" -ErrorAction SilentlyContinue; if ($p) {{ $p.CommandLine }}"
    )
    if out.startswith("__ERR__"):
        return ""
    return out.strip()


def _is_server_cmdline(cmdline: str, port: int) -> bool:
    """命令行是否指向本平台 server（platform.server / server.py + 本端口）。"""
    cl = (cmdline or "").lower()
    if "agent_community.platform.server" not in cl and "platform\\server.py" not in cl and "platform/server.py" not in cl:
        return False
    return True


# ── GUI 壳共存检测（缓存 300s，避免每轮都起 PowerShell）───────────
_gui_cache: dict = {"ts": 0.0, "found": False}


def _gui_shell_exists(port: int, cache_seconds: float = 300.0) -> bool:
    now = time.time()
    if now - _gui_cache["ts"] < cache_seconds:
        return _gui_cache["found"]
    found = False
    out = _run_powershell(
        "Get-CimInstance Win32_Process -Filter \"Name like 'python%'\" -ErrorAction SilentlyContinue "
        "| Where-Object { $_.CommandLine -match 'gui' } "
        f"| Where-Object {{ $_.CommandLine -match '18920|{port}' }} | Select-Object -First 1 ProcessId"
    )
    if out and not out.startswith("__ERR__") and out.strip():
        found = True
        logger.info("检测到 GUI 壳进程（PID %s），进入只守护模式：server 消失时仅告警、不重复拉起", out.strip())
    _gui_cache.update(ts=now, found=found)
    return found


# ── 拉起 / 重启 ───────────────────────────────────────────────
_spawned: dict = {"cmd": None, "pid": None}  # 本 watchdog 上次拉起的命令与 PID（重启时优先复用“原命令行”）


def _default_server_cmd(port: int, demo: bool = False) -> list[str]:
    cmd = [sys.executable, "-m", "agent_community.platform.server", "--port", str(port)]
    if demo:
        cmd.append("--demo")
    return cmd


def spawn_server(port: int, cmd_override: list[str] | None = None, demo: bool = False) -> tuple[bool, str]:
    """按原命令行拉起 server（cwd=项目根，输出重定向到 data/logs/server_stdout.log）。"""
    cmd = list(cmd_override or _spawned.get("cmd") or _default_server_cmd(port, demo=demo))
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    stdout_path = LOG_DIR / "server_stdout.log"
    kwargs = {"cwd": str(PROJECT_ROOT)}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW | getattr(subprocess, "DETACHED_PROCESS", 0)
    with open(stdout_path, "a", encoding="utf-8") as logf:
        try:
            p = subprocess.Popen(cmd, stdout=logf, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, **kwargs)
        except Exception as e:  # noqa: BLE001
            logger.error("拉起失败: %s | cmd=%s", e, cmd)
            return False, f"spawn 失败: {e}"
    _spawned.update(cmd=cmd, pid=p.pid)
    logger.info("已按原命令行拉起 server，PID=%s cmd=%s", p.pid, " ".join(str(c) for c in cmd))
    return True, f"spawned pid={p.pid}"


def _recover(port: int, demo: bool, dry_run: bool = False) -> str:
    """不可达后的恢复决策与执行。dry_run=True 时只报告拟动作（供 --check 语义提示，不真正拉起）。"""
    listeners = _listener_pids(port)
    for pid in listeners:
        if _pid_alive(pid) and _is_server_cmdline(_cmdline_of(pid), port):
            logger.error("端口 %s 被本平台 server 进程 PID=%s 监听但 HTTP 不健康（疑似僵死）。dry_run=%s",
                         port, pid, dry_run)
            if not dry_run:
                _kill_pid(pid)
                time.sleep(2.0)
                ok, note = spawn_server(port, demo=demo)
                return f"僵死 server PID={pid} 已重启: {note}" if ok else note
            return f"僵死 server PID={pid}（dry_run，不动作）"
    if _gui_shell_exists(port):
        logger.warning("GUI 壳在守护中，server 消失只记录不拉起（防双 server 冲突）")
        return "GUI 壳存在，只守护不拉起"
    if not dry_run:
        ok, note = spawn_server(port, demo=demo)
        return f"已重新拉起: {note}" if ok else note
    return "服务不可达（dry_run，拟按默认命令行拉起）"


def _kill_pid(pid: int) -> None:
    try:
        subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True, text=True, timeout=10.0)
    except Exception as e:  # noqa: BLE001
        logger.warning("kill PID=%s 失败: %s", pid, e)


# ── 单次检查 / 守护循环 ────────────────────────────────────────
def check_once(port: int, verbose: bool = True) -> int:
    """单次检查：健康返回 0；不健康返回 1。默认不触发恢复（--check 只报告）。"""
    ok, detail = probe(port)
    listeners = _listener_pids(port)
    alive = [pid for pid in listeners if _pid_alive(pid)]
    if ok:
        if verbose:
            print(f"[watchdog] OK  port={port} detail={detail} listener_pids={alive}")
        return 0
    if verbose:
        reason = _recover(port, demo=False, dry_run=True)
        print(f"[watchdog] FAIL port={port} detail={detail} listener_pids={alive}")
        print(f"[watchdog] recover-plan: {reason}")
    return 1


def daemon_loop(port: int, interval: float, demo: bool = False) -> None:
    logger.info("看门狗启动：port=%s interval=%ss demo=%s 日志=%s", port, interval, demo, LOG_DIR / "watchdog.log")
    consecutive_fail = 0
    while True:
        ok, detail = probe(port)
        if ok:
            consecutive_fail = 0
            logger.debug("健康 OK：%s", detail)
        else:
            consecutive_fail += 1
            logger.warning("健康检查失败(%d)：%s —— 触发恢复流程", consecutive_fail, detail)
            try:
                action = _recover(port, demo=demo, dry_run=False)
                logger.info("恢复动作结果：%s", action)
                # 拉起后轮询健康（最多 60s，每 5s 一次）
                if "已重新拉起" in action or "已重启" in action:
                    waited = 0
                    while waited < 60:
                        time.sleep(5.0)
                        waited += 5
                        ok2, detail2 = probe(port)
                        if ok2:
                            logger.info("重启后健康确认：%s", detail2)
                            break
                    else:
                        logger.error("重启后 60s 内仍未健康：%s", detail2)
            except Exception as e:  # noqa: BLE001
                logger.exception("恢复流程异常（不阻塞下一轮）: %s", e)
        time.sleep(interval)


# ── 入口 ──────────────────────────────────────────────────────
def main() -> int:
    ap = argparse.ArgumentParser(description="外端Agent生产合作社（External Agent Community） v4 平台进程看门狗（R3 部署层工具）")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"监听端口（默认 {DEFAULT_PORT}）")
    ap.add_argument("--check", action="store_true", help="单次健康检查（不触发恢复，exit 0=健康/1=异常）")
    ap.add_argument("--daemon", action="store_true", help="循环守护模式")
    ap.add_argument("--interval", type=float, default=DEFAULT_INTERVAL, help="守护轮询间隔秒（默认 30）")
    ap.add_argument("--demo", action="store_true", help="拉起时附带 --demo（跳过 Harness 真实连通检查）")
    ap.add_argument("--spawn-cmd", default="", help="拉起用的命令模板（默认: <python> -m agent_community.platform.server --port <port>）")
    args = ap.parse_args()

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s | %(levelname)s | %(message)s")
    fh = logging.FileHandler(LOG_DIR / "watchdog.log", encoding="utf-8")
    fh.setFormatter(fmt)
    logger.addHandler(fh)
    logger.setLevel(logging.DEBUG)

    if args.spawn_cmd:
        # 允许形如: python -m agent_community.platform.server --port 18920 / 或 "python|模块式"
        tokens = args.spawn_cmd.split()
        if tokens and tokens[0].lower() in ("python", "py"):
            tokens[0] = sys.executable
        _spawned["cmd"] = tokens

    if args.check:
        return check_once(args.port)
    if args.daemon:
        daemon_loop(args.port, args.interval, demo=args.demo)
        return 0
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
