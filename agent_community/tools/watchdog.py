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

★2026-10-15（V50）跨平台化
──────────────────────────
本文件**不能引 psutil**（承诺"全程标准库、无第三方依赖"——它是"救一个坏掉的环境"的那只手，
那时连依赖都可能装不上），所以它的进程发现此前是**Windows 专属**实现
（`powershell` / `netstat` / `tasklist` / `taskkill`）⇒ **在 Linux 上静默降级**：
端口被谁占着查不到、僵死进程杀不掉 ⇒ **看门狗在 Linux 上等于没有**。

修法：把"零第三方依赖的本机事实探测"收进一个纯标准库模块
`agent_community/platform/procprobe.py`（Windows 走本机命令，Linux 走 `/proc`，其它 POSIX 走 `ps`/`lsof`/`ss`），
本文件**按文件路径**把它加载进来 —— 用**文件路径**而不是 `import agent_community...`，
是为了让看门狗**在包不可导入时也能单独跑**（`python agent_community/tools/watchdog.py` 的
`sys.path[0]` 是脚本目录，包并不一定在路径上）。
设计稿：`design-docs/V50_看门狗stdlib跨平台化与纯标准库进程探测.md`。
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
DATA_DIR = Path(os.environ.get("AC_DATA_DIR") or (PROJECT_ROOT / "agent_community" / "data"))
LOG_DIR = DATA_DIR / "logs"
# ★2026-10-13：与平台同源（原来这里与 server 各自写死一套默认端口）。
#   ⚠️ 这个文件会被**单独加载**（`tests/test_watchdog_spawn_cmd.py` 用 spec_from_file_location，
#      没有父包）⇒ 必须用**带兜底的绝对导入**，相对导入会 ImportError。
try:  # pragma: no cover - 兜底分支只在不作为包加载时走
    from agent_community.platform_address import DEFAULT_PORT  # type: ignore
except Exception:  # noqa: BLE001
    DEFAULT_PORT = int(os.environ.get("AC_PORT") or 18920)
DEFAULT_INTERVAL = 30.0
HEALTH_URL = "http://127.0.0.1:{port}/api/status"

# 健康模式（V19 决策点②）：http = 历史行为（TCP + /api/status）；tcp = 只判 TCP。
# 默认 http ⇒ **18920 现有路径行为零变化**；`--health tcp` 由 main() 按参数设置。
HEALTH_MODES = ("http", "tcp")
_HEALTH_MODE = "http"

logger = logging.getLogger("watchdog")


# ── 本机事实探测层：纯标准库的 procprobe（**按文件路径加载**，见文件头 ★）──────
def _load_procprobe():
    """加载 `agent_community/platform/procprobe.py`；拿不到返回 None（**失败要说话**）。

    为什么不 `import`：看门狗要能在"包不在 sys.path 上"时单独跑
    （`python agent_community\\tools\\watchdog.py`、或被人拷出去救火）。
    """
    path = PROJECT_ROOT / "agent_community" / "platform" / "procprobe.py"
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("_ac_procprobe_standalone", path)
        if spec is None or spec.loader is None:
            raise ImportError("spec_from_file_location 返回空（路径：%s）" % path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    except Exception as e:  # noqa: BLE001
        global _PROBE_ERROR
        _PROBE_ERROR = "%s: %s" % (type(e).__name__, e)
        return None


_PROBE_ERROR = ""
PROBE = _load_procprobe()
#: 探测层不可用时的**如实说明**（别让"查不到进程"看起来像"真的没有进程"）
PROBE_NOTE = ("" if PROBE is not None else
              "⚠️本机事实探测不可用（procprobe 加载失败：%s）—— 看门狗仍会判 TCP/HTTP 健康，"
              "但**查不到端口归属、也杀不掉僵死进程**（探不到 ≠ 真没有）" % _PROBE_ERROR)


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


def probe(port: int, http_timeout: float = 5.0, mode: str | None = None) -> tuple[bool, str]:
    """一次健康判定（`mode` 缺省取全局 `--health` 设置）。

    - `http`（默认 = 历史行为）：TCP + `GET /api/status`（要求 `status=="running"`）；
    - `tcp`（V19 决策点②，`--health tcp`）：**只判 TCP 可达** —— 给**没有 HTTP 端点**的目标用
      （如 gRPC 网关 9105）。没有这个模式的话，对它的健康**恒为 false**，看门狗会
      **每轮都重拉**、反复抢端口（V19 稿 §2.1 记录的前置缺陷）。
    """
    if not _port_open(port):
        return False, "TCP 不可达（无进程监听该端口）"
    m = (mode or _HEALTH_MODE) or "http"
    if m == "tcp":
        return True, "TCP 可达（--health tcp 模式）"
    ok, detail = _http_health(port, http_timeout)
    return ok, f"HTTP /api/status: {detail}"


# ── 进程发现（一律走 procprobe；**本节不再有任何平台专属命令**）──────────────
def _listener_pids(port: int) -> list[int]:
    """返回监听该端口的 PID 列表（跨平台：Windows 本机命令 / Linux `/proc` / POSIX `ss`·`lsof`）。"""
    if PROBE is None:
        return []
    try:
        return list(PROBE.listening_pids(int(port)))
    except Exception as e:  # noqa: BLE001
        logger.warning("查端口归属失败 port=%s: %s", port, e)
        return []


def _pid_alive(pid: int) -> bool:
    """PID 是否存活（跨平台）。"""
    if PROBE is None:
        return False
    try:
        return bool(PROBE.is_pid_alive(pid))
    except Exception:  # noqa: BLE001
        return False


def _cmdline_of(pid: int) -> str:
    """取指定 PID 的命令行（跨平台；取不到返回空串）。"""
    if not pid or PROBE is None:
        return ""
    try:
        return (PROBE.process_cmdline(pid) or "").strip()
    except Exception:  # noqa: BLE001
        return ""


def _is_server_cmdline(cmdline: str, port: int) -> bool:
    """命令行是否指向本平台 server（platform.server / server.py）。"""
    cl = (cmdline or "").lower()
    if "agent_community.platform.server" not in cl and "platform\\server.py" not in cl and "platform/server.py" not in cl:
        return False
    return True


# ── GUI 壳共存检测（缓存 300s，避免每轮都扫进程表）───────────────
_gui_cache: dict = {"ts": 0.0, "found": False}


def _looks_like_gui_shell(name: str, cmdline: str, port: int) -> bool:
    """**纯判据**：这一行进程是不是"守着 `port` 的 GUI 壳"。

    抽成纯函数是为了**可喂合成行**（守卫 `test_watchdog_health.py` 直接喂），
    也为了把"必须按本守护自己的 port 过滤"这条钉在**行为**上而不是源码文本上
    （V19 缺陷②：正则里硬编码 18920 ⇒ 平台 GUI 一在跑，9105 守护就误入"只守护模式"、
    网关挂了也不拉）。
    """
    low = (cmdline or "").lower()
    if "gui" not in low:
        return False
    if str(int(port)) not in low:
        return False
    return str(name or "").lower().startswith("python")


def _gui_shell_exists(port: int, cache_seconds: float = 300.0) -> bool:
    now = time.time()
    if now - _gui_cache["ts"] < cache_seconds:
        return _gui_cache["found"]
    found = False
    if PROBE is not None:
        try:
            for pid, _ppid, name, cmdline in PROBE.iter_processes():
                if _looks_like_gui_shell(name, cmdline, port):
                    found = True
                    logger.info("检测到 GUI 壳进程（PID %s），进入只守护模式：server 消失时仅告警、不重复拉起", pid)
                    break
        except Exception as e:  # noqa: BLE001
            logger.warning("GUI 壳检测失败（当作没有壳处理）: %s", e)
    elif PROBE_NOTE:
        logger.warning("%s", PROBE_NOTE)
    _gui_cache.update(ts=now, found=found)
    return found


# ── 拉起 / 重启 ───────────────────────────────────────────────
_spawned: dict = {"cmd": None, "pid": None}  # 本 watchdog 上次拉起的命令与 PID（重启时优先复用“原命令行”）


def _split_spawn_cmd(s: str) -> list[str]:
    """切分 `--spawn-cmd` 模板。支持**带引号、路径含空格**的写法。

    ⚠️ 2026-10-06 修：原先直接用 `str.split()` 按空白切分，于是
    `--spawn-cmd "python D:\\my work\\agent-community\\x.py 18921"`
    会把含空格的路径切成两半，子进程报
    `python.exe: can't open file 'D:\\my work\\agent'`。
    **本仓库路径本身含空格**（`…（External Agent Community）`），所以这不是纸上风险：
    用路径当拉起目标的场景必踩（实测：子进程秒退，看门狗反复重试）。
    修法：用 shlex 尊重引号，但取 `posix=False`（否则反斜杠会被当转义吞掉，
    Windows 路径 `D:\\dir\\sub` 会变成 `dir\\sub`），再手工剥掉成对的外层引号。
    无引号且无空格的旧写法行为完全不变（向后兼容）。
    """
    try:
        import shlex
        toks = shlex.split(s or "", posix=False)
    except Exception:  # noqa: BLE001 — shlex 遇到不配对引号会抛，退回朴素切分
        return (s or "").split()
    out = []
    for t in toks:
        if len(t) >= 2 and t[0] == t[-1] and t[0] in ('"', "'"):
            t = t[1:-1]
        out.append(t)
    return out


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
    """结束进程（跨平台；`tree=True` —— server 会带子进程，别留下孤儿）。"""
    if PROBE is None:
        logger.warning("杀进程失败 PID=%s：%s", pid, PROBE_NOTE or "本机事实探测不可用")
        return
    try:
        ok = PROBE.kill_pid(int(pid), tree=True)
    except Exception as e:  # noqa: BLE001
        logger.warning("kill PID=%s 失败: %s", pid, e)
        return
    if not ok:
        logger.warning("kill PID=%s 后它**仍然存活**（命令返回不等于已结束）", pid)


# ── 单次检查 / 守护循环 ────────────────────────────────────────
def check_once(port: int, verbose: bool = True) -> int:
    """单次检查：健康返回 0；不健康返回 1。默认不触发恢复（--check 只报告）。"""
    ok, detail = probe(port)
    listeners = _listener_pids(port)
    alive = [pid for pid in listeners if _pid_alive(pid)]
    if ok:
        if verbose:
            print(f"[watchdog] OK  port={port} detail={detail} listener_pids={alive}")
            if PROBE_NOTE:
                print(f"[watchdog] note: {PROBE_NOTE}")
        return 0
    if verbose:
        reason = _recover(port, demo=False, dry_run=True)
        print(f"[watchdog] FAIL port={port} detail={detail} listener_pids={alive}")
        print(f"[watchdog] recover-plan: {reason}")
        if PROBE_NOTE:
            print(f"[watchdog] note: {PROBE_NOTE}")
    return 1


def daemon_loop(port: int, interval: float, demo: bool = False) -> None:
    logger.info("看门狗启动：port=%s interval=%ss demo=%s 日志=%s", port, interval, demo, LOG_DIR / "watchdog.log")
    if PROBE_NOTE:
        logger.warning("%s", PROBE_NOTE)
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
    ap.add_argument("--health", choices=list(HEALTH_MODES), default="http",
                    help="健康判据：http=TCP+/api/status（默认，18920 现行为）；"
                         "tcp=只判 TCP（给没有 HTTP 端点的目标用，如 gRPC 网关 9105）")
    ap.add_argument("--probe-info", action="store_true",
                    help="只打印「本机事实探测」走哪条路（排障用；不连服务）")
    args = ap.parse_args()

    global _HEALTH_MODE
    _HEALTH_MODE = args.health

    if args.probe_info:
        if PROBE is None:
            print(PROBE_NOTE)
            return 1
        print("backend=%s" % PROBE.backend())
        print("note   =%s" % PROBE.probe_note())
        return 0

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s | %(levelname)s | %(message)s")
    fh = logging.FileHandler(LOG_DIR / "watchdog.log", encoding="utf-8")
    fh.setFormatter(fmt)
    logger.addHandler(fh)
    logger.setLevel(logging.DEBUG)

    if args.spawn_cmd:
        # 允许形如: python -m agent_community.platform.server --port 18920
        # 也允许带引号、且**路径含空格**的写法（本仓库路径就含空格，见下）
        tokens = _split_spawn_cmd(args.spawn_cmd)
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
