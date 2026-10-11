# -*- coding: utf-8 -*-
"""platform/routers/system：平台自身的生命周期（V27）。

**`POST /api/system/shutdown`** —— 老板 2026-10-09 要求的「退出并关闭」：
把**看门狗与各服务按正确顺序**停掉。

### 为什么顺序这么重要（V27 §1）
看门狗的职责就是"服务不在就拉起来"。若先停服务、最后停看门狗，
每停一个服务它都会**立刻拉回来** ⇒ 用户看到"点了退出，服务还在"——
典型的**假成功**（准则⑤）。故顺序固定为：
    看门狗 → A2A → grpc → **platform（最后）**
且每一步都**核对端口真的不再监听**，没关掉的进 `still_running` **如实返回**。

### 为什么要有口令 / 来源校验（V27 §3.1）
平台认证是「**本机免 token**、非本机才要 token」（为了界面不用登录），
且此前**没有 Origin 校验** ⇒ 浏览器里任何网页都能朝本机的 shutdown 地址发**普通表单 POST**
（简单请求，不触发预检、CORS 挡不住"发出去"）⇒ 有人塞个隐形表单就能把关停调起来。
故双层防护：
  1. **口令**：`deploy/.env` 的 `AC4_SHUTDOWN_PIN`（留空 = 不校验），请求头 `X-Shutdown-Pin`；
  2. **来源**：`Origin`/`Referer` 只接受本机（无该头视为本机命令行调用）。

### 不做什么
不删数据、不动工作间、不停外端 harness 的会话；也**不假装能替用户关浏览器标签**。
"""
from __future__ import annotations

import asyncio
import os
import re
import subprocess
import sys
import time
from pathlib import Path

from fastapi import APIRouter, Request

router = APIRouter()

# 视为"本机来源"的 Origin/Referer 前缀
_LOCAL_ORIGINS = ("http://127.0.0.1", "http://localhost", "http://[::1]",
                  "https://127.0.0.1", "https://localhost")

_REPO_ROOT = Path(__file__).resolve().parents[3]


def _read_env_file_pin() -> str:
    """从 `deploy/.env` 读 `AC4_SHUTDOWN_PIN`（运维改文件即可，不必动环境变量）。"""
    for rel in ("deploy/.env", ".env"):
        p = _REPO_ROOT / rel
        if not p.is_file():
            continue
        try:
            for line in p.read_text(encoding="utf-8-sig", errors="replace").splitlines():
                s = line.strip()
                if s.startswith("#") or "=" not in s:
                    continue
                k, _, v = s.partition("=")
                if k.strip() == "AC4_SHUTDOWN_PIN":
                    return v.strip().strip('"').strip("'")
        except Exception:  # noqa: BLE001
            continue
    return ""


def shutdown_pin() -> str:
    """当前生效的关闭口令（环境变量优先，其次 `deploy/.env`）；空 = 不校验口令。"""
    return (os.environ.get("AC4_SHUTDOWN_PIN") or _read_env_file_pin()).strip()


def origin_allowed(request: Request) -> tuple[bool, str]:
    """来源校验：只接受**本机来源**；没有 Origin/Referer 的（命令行/服务端调用）视为本机。"""
    origin = request.headers.get("Origin") or request.headers.get("Referer") or ""
    if not origin:
        return True, "no-origin"
    if origin.startswith(_LOCAL_ORIGINS):
        return True, "local-origin"
    return False, f"来源不是本机：{origin[:80]}"


def port_pids(port: int) -> list[int]:
    """谁在监听该端口（用 netstat 解析，不引第三方依赖）。"""
    try:
        out = subprocess.run(["netstat", "-ano", "-p", "TCP"], capture_output=True, text=True,
                             timeout=10).stdout
    except Exception:  # noqa: BLE001
        return []
    pids: list[int] = []
    pat = re.compile(r":%d\s+\S+\s+LISTENING\s+(\d+)" % port)
    for line in out.splitlines():
        m = pat.search(line)
        if m:
            pid = int(m.group(1))
            if pid and pid not in pids:
                pids.append(pid)
    return pids


def port_listening(port: int) -> bool:
    try:
        out = subprocess.run(["netstat", "-ano", "-p", "TCP"], capture_output=True, text=True,
                             timeout=10).stdout
        return any(re.search(r":%d\s+\S+\s+LISTENING" % port, ln) for ln in out.splitlines())
    except Exception:  # noqa: BLE001
        return False


def watchdog_pid_file() -> Path:
    """本实例的看门狗 pid 记录（由**快捷启动**在拉起看门狗时写入，单一事实源）。"""
    return _REPO_ROOT / "agent_community" / "data" / "runtime" / "watchdog.pid"


def watchdog_pids(server_port: int | None = None) -> list[int]:
    """找出**本实例**的看门狗进程。

    ⚠️ 为什么不能"命令行含 watchdog 就杀"（2026-10-09 设计时发现的隐患）：
    本机可以同时跑多个实例（生产 18920 + 隔离实例）。若按关键字盲杀，
    **在隔离实例上点一次"退出并关闭"，会把生产实例的看门狗一起杀掉**。
    ⇒ 判据按优先级收紧：
      1. **优先**读 `data/runtime/watchdog.pid`（快捷启动写入的**本实例**记录）——
         还要求该 pid 活着**且**命令行确实含 `watchdog`（双条件，防止 pid 被复用）；
      2. 没有记录时，退化为"命令行含 `watchdog` **且**含本实例端口号"（`watchdog_autostart` 那类
         守护进程的命令行里带 `--port <端口>`）；
      3. 都不匹配 ⇒ **一个都不杀**（宁可不杀，也不误杀别人的实例）。
    返回值可能为空 —— 调用方**必须如实说明"没找到本实例的看门狗记录"**，不许假装已关。
    """
    # 1) 本实例记录优先
    try:
        f = watchdog_pid_file()
        if f.is_file():
            pid = int((f.read_text(encoding="utf-8", errors="replace").strip() or "0").split()[0])
            if pid > 0 and _pid_alive(pid) and _cmdline_has(pid, "watchdog"):
                return [pid]
    except Exception:  # noqa: BLE001
        pass
    # 2) 退化：命令行含 watchdog 且含本实例端口
    if not server_port:
        return []
    needle = str(server_port)
    out = _powershell_pids(
        "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'watchdog' "
        f"-and $_.CommandLine -match '{needle}' }} | Select-Object -ExpandProperty ProcessId")
    return out


def _powershell_pids(script: str) -> list[int]:
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                             capture_output=True, text=True, timeout=20).stdout
        return [int(x) for x in re.findall(r"\d+", out)]
    except Exception:  # noqa: BLE001
        return []


def _cmdline_has(pid: int, needle: str) -> bool:
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command",
                              f"(Get-CimInstance Win32_Process -Filter \"ProcessId={pid}\").CommandLine"],
                             capture_output=True, text=True, timeout=20).stdout
        return needle.lower() in (out or "").lower()
    except Exception:  # noqa: BLE001
        return False


def kill_pid(pid: int, *, tree: bool = False) -> bool:
    """结束进程（`taskkill`）。tree=True 时连同子进程一起（看门狗会拉起子进程）。"""
    args = ["taskkill", "/F", "/PID", str(pid)]
    if tree:
        args.insert(1, "/T")
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=15)
        return r.returncode == 0
    except Exception:  # noqa: BLE001
        return False


def _stop_port(target: str, port: int, report: dict) -> bool:
    """停掉监听某端口的进程，并**核对端口真的不再监听**（没停掉就如实记下来）。

    ⚠️ 护栏（2026-10-09 设计时发现的第二个隐患）：本机可同时跑多个实例，而隔离实例的
    A2A/grpc 端口**默认值与生产相同**（9104/9105）⇒ 若照着"谁在听这个端口就杀谁"，
    在隔离实例点一次关停就会**杀掉生产的 A2A/grpc**。
    故只杀**能确认属于本项目**的进程（命令行含 `agent_community`）；
    端口被"外人的进程"占着时**不杀**，如实记进 `still_running` 并说明原因。
    """
    pids = port_pids(port)
    if not pids:
        report["detail"][target] = f"本就没有在监听（{port}）"
        return True
    ours = [p for p in pids if _cmdline_has(p, "agent_community")]
    foreign = [p for p in pids if p not in ours]
    if not ours:
        report["still_running"].append(f"{target}({port})")
        report["detail"][target] = (f"未关停：端口 {port} 被**非本项目进程**占用（pid {foreign}）"
                                    f"，为避免误杀别人的服务，平台不动它")
        report["warnings"] = report.get("warnings", []) + [
            f"{target}({port}) 被非本项目进程占用，未关闭（原因见 detail）"]
        return False
    for pid in ours:
        kill_pid(pid, tree=True)
    for _ in range(20):                      # 最多等 2s
        if not port_listening(port):
            break
        time.sleep(0.1)
    if port_listening(port):
        report["still_running"].append(f"{target}({port})")
        report["detail"][target] = f"未关掉：端口 {port} 仍在监听（pid {ours}）"
        return False
    report["stopped"].append(target)
    report["detail"][target] = f"已关闭（{port}，原 pid {ours}）"
    return True


def _schedule_self_exit(delay: float = 0.8) -> None:
    """**先让响应发出去**，再结束本进程（否则界面只会看到连接被掐断）。"""
    def _bye() -> None:
        try:
            sys.stdout.flush()
        except Exception:  # noqa: BLE001
            pass
        os._exit(0)
    try:
        asyncio.get_event_loop().call_later(delay, _bye)
    except Exception:  # noqa: BLE001
        _bye()


def shutdown_targets(server_port: int, a2a_port: int, grpc_port: int) -> dict:
    """按 V27 §1 的顺序关停，返回**诚实**的结果（没关掉的进 `still_running`）。"""
    report: dict = {"success": True, "stopped": [], "still_running": [], "detail": {}}

    # ① 看门狗**必须最先停** —— 否则它会把后面停掉的服务又拉起来
    wds = watchdog_pids(server_port)
    if not wds:
        report["detail"]["watchdog"] = (
            "未找到本实例的看门狗记录（`data/runtime/watchdog.pid` 不存在、"
            "也没有命令行含本实例端口的看门狗）—— 本次未关任何看门狗")
        report["warnings"] = report.get("warnings", []) + [
            "没有找到本实例的看门狗：如果你是从旧的自启方式启动的，请手动停它，"
            "否则它可能会把刚关掉的服务再拉起来。"]
    else:
        for pid in wds:
            kill_pid(pid, tree=True)
        time.sleep(0.5)
        left = [p for p in wds if _pid_alive(p)]
        if left:
            report["still_running"].append("watchdog")
            report["detail"]["watchdog"] = f"未关掉：pid {left} 仍在"
        else:
            report["stopped"].append("watchdog")
            report["detail"]["watchdog"] = f"已关闭（pid {wds}）"

    # ② A2A → ③ grpc
    _stop_port("a2a", a2a_port, report)
    _stop_port("grpc", grpc_port, report)

    # ④ 最后停自己
    report["stopped"].append("platform")
    report["detail"]["platform"] = f"正在关闭本进程（{server_port}）——响应返回后退出"
    _schedule_self_exit()
    return report


def _pid_alive(pid: int) -> bool:
    try:
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True,
                             text=True, timeout=10).stdout
        return str(pid) in out
    except Exception:  # noqa: BLE001
        return False


@router.get("/api/system/lifecycle")
async def system_lifecycle(request: Request):
    """只读：平台生命周期现状（界面用它决定按钮文案/是否可用，**不猜**）。"""
    server_port = request.url.port or int(os.environ.get("AC4_SERVER_PORT", "18920") or 18920)
    a2a_port = int(os.environ.get("AC4_A2A_PORT", "9104") or 9104)
    grpc_port = int(os.environ.get("AC4_GRPC_PORT", "9105") or 9105)
    pin = shutdown_pin()
    return {
        "server_port": server_port,
        "a2a_port": a2a_port,
        "grpc_port": grpc_port,
        "watchdog_running": bool(watchdog_pids()),
        "a2a_listening": port_listening(a2a_port),
        "grpc_listening": port_listening(grpc_port),
        "pin_required": bool(pin),
        "note": ("口令留空时只做来源校验与界面二次确认（`AC4_SHUTDOWN_PIN` 设了就必须要口令）"),
    }


@router.post("/api/system/shutdown")
async def system_shutdown(request: Request):
    """「退出并关闭」：**看门狗 → A2A → grpc → platform**，逐项如实回报。

    校验：来源只允许本机；`AC4_SHUTDOWN_PIN` 非空时必须带 `X-Shutdown-Pin`。
    """
    from ..server import Utf8JSONResponse      # 与本项目其它路由一致的中文安全响应

    ok, why = origin_allowed(request)
    if not ok:
        return Utf8JSONResponse({"success": False, "error": f"拒绝关停：{why}"}, status_code=403)

    pin = shutdown_pin()
    if pin:
        got = (request.headers.get("X-Shutdown-Pin") or "").strip()
        if got != pin:
            return Utf8JSONResponse(
                {"success": False,
                 "error": "关闭口令不正确（在 deploy/.env 的 AC4_SHUTDOWN_PIN 里配置；界面会提示你输入）",
                 "pin_required": True},
                status_code=403)

    server_port = request.url.port or int(os.environ.get("AC4_SERVER_PORT", "18920") or 18920)
    a2a_port = int(os.environ.get("AC4_A2A_PORT", "9104") or 9104)
    grpc_port = int(os.environ.get("AC4_GRPC_PORT", "9105") or 9105)

    try:
        from ..audit_log import record as _audit_record
        _audit_record("system.shutdown", actor="local-ui",
                      detail=f"pin={'set' if pin else 'none'} ports={server_port}/{a2a_port}/{grpc_port}")
    except Exception:  # noqa: BLE001
        pass

    report = shutdown_targets(server_port, a2a_port, grpc_port)
    report["message"] = ("正在关闭：看门狗 → A2A → grpc → 平台本体。"
                         + ("⚠ 有项目没关掉，见 still_running。" if report["still_running"]
                            else "全部已关闭，可以关掉这个页面了。"))
    return report
