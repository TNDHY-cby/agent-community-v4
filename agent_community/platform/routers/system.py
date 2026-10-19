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
    """谁在监听该端口（★2026-10-14：改用 `platform/sysprobe`，**跨平台**）。

    原实现解析 `netstat -ano -p TCP`（Windows 专属语法）⇒ **Linux 上恒返回空**：
    「端口被谁占着」查不到，连带 `_stop_port`（靠它找 pid）**永远停不掉**
    —— 用户点"退出并关闭"却关不掉，还看不到原因。

    ⚠️Linux 非 root 时只能看到**自己**进程的套接字（内核限制）⇒ 空结果只代表
    "**可查范围内没有**"，调用方要如实这么说（见 `sysprobe.probe_note()`）。
    """
    from .. import sysprobe
    return sysprobe.listening_pids(port)


def port_listening(port: int) -> bool:
    """该端口是否有人在监听（跨平台；注意事项同上）。"""
    from .. import sysprobe
    return sysprobe.port_listening(port)


def watchdog_pid_file() -> Path:
    """本实例的看门狗 pid 记录（**由 `tools/watchdog.py` 写**，单一事实源）。

    ★2026-10-19 修（做受控重启端点时发现）：这里原先写死
      `<仓库>/agent_community/data/runtime/watchdog.pid`，而**写方**
      `tools/watchdog.py` 写的是 `<AC_DATA_DIR>/logs/watchdog.pid` ——
      **路径不同、还不认 AC_DATA_DIR** ⇒ 读的这一侧**永远读不到**对方写的记录。
      后果比"读不到"更糟：隔离实例（换了 AC_DATA_DIR）会读到**生产实例**的旧记录，
      误判"我有看门狗"，然后允许重启自己 —— 而那只看门狗救的是生产实例，
      **隔离实例就此永久宕机**（最坏的假成功）。
      ⇒ 现在与写方**同一口径**：数据落点认 `AC_DATA_DIR`，文件名 `logs/watchdog.pid`。
    """
    base = Path(os.environ.get("AC_DATA_DIR") or (_REPO_ROOT / "agent_community" / "data"))
    return base / "logs" / "watchdog.pid"



def watchdog_state() -> dict:
    """读看门狗写下的**可读状态** `<AC_DATA_DIR>/logs/watchdog.json`；读不到返回 `{}`。

    ★为什么需要它：`watchdog.pid` 是单实例锁，看门狗**独占持有**它
    （Windows `msvcrt.locking`）⇒ 别的进程**读不了**（PermissionError）。
    2026-10-19 做受控重启端点时撞上：平台读不到 pid，就以为"没有看门狗"，
    于是把本来能安全重启的实例误拒（或更糟：读到别的实例的记录而误放行）。
    """
    try:
        import json as _json
        import os as _os
        base = Path(_os.environ.get("AC_DATA_DIR") or (_REPO_ROOT / "agent_community" / "data"))
        p = base / "logs" / "watchdog.json"
        return _json.loads(p.read_text(encoding="utf-8")) or {}
    except Exception:                                        # noqa: BLE001
        return {}


def watchdog_pids(server_port: int | None = None) -> list[int]:
    """找出**本实例**的看门狗进程。

    ⚠️ 为什么不能"命令行含 watchdog 就杀"（2026-10-09 设计时发现的隐患）：
    本机可以同时跑多个实例（生产 18920 + 隔离实例）。若按关键字盲杀，
    **在隔离实例上点一次"退出并关闭"，会把生产实例的看门狗一起杀掉**。
    ⇒ 判据按优先级收紧：
      1. **优先**读 `<AC_DATA_DIR>/logs/watchdog.pid`（`tools/watchdog.py` 写的**本实例**记录，
         2026-10-19 起与写方同一口径）——还要求该 pid 活着**且**命令行确实含 `watchdog`（双条件，防止 pid 被复用）；

      2. 没有记录时，退化为"命令行含 `watchdog` **且**含本实例端口号"（`watchdog_autostart` 那类
         守护进程的命令行里带 `--port <端口>`）；
      3. 都不匹配 ⇒ **一个都不杀**（宁可不杀，也不误杀别人的实例）。
    返回值可能为空 —— 调用方**必须如实说明"没找到本实例的看门狗记录"**，不许假装已关。
    """
    # 0) **可读状态优先**（2026-10-19 起）：锁文件读不了，JSON 才是可靠来源；
    #    而且它**带端口** ⇒ 多实例不会互相误认（这是比"命令行含端口"更准的判据）。
    _st = watchdog_state()
    _pid = int((_st or {}).get("pid") or 0)
    if _pid > 0 and _pid_alive(_pid) and _cmdline_has(_pid, "watchdog"):
        _stat_port = str((_st or {}).get("port") or "")
        if not server_port or not _stat_port or _stat_port == str(server_port):
            return [_pid]
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
    from .. import sysprobe
    needle = str(server_port)
    out = []
    for h in sysprobe.find_processes(["watchdog"], exclude_ppid=os.getpid(), limit=50):
        cmd = str(h.get("cmdline") or "")
        pid = int(h.get("pid") or 0)
        if pid > 0 and needle in cmd and pid not in out:
            out.append(pid)          # 端口号也在命令行里 ⇒ 是本实例那只
    return out


def _cmdline_has(pid: int, needle: str) -> bool:
    """该 pid 的命令行里是否含某关键词（★跨平台取命令行，见 `sysprobe.process_cmdline`）。"""
    from .. import sysprobe
    return str(needle or "").lower() in sysprobe.process_cmdline(pid).lower()


def kill_pid(pid: int, *, tree: bool = False) -> bool:
    """结束进程；`tree=True` 时连同子进程一起（看门狗会拉起子进程）。

    ★2026-10-14：跨平台（Windows `taskkill /T /F`；POSIX 信号 + 子进程）。
    返回**是否确实已不存活**（不是"命令返回 0 就算成功"——失败要说话）。
    """
    from .. import sysprobe
    return sysprobe.kill_pid(pid, tree=tree)


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
            "未找到本实例的看门狗记录（`<AC_DATA_DIR>/logs/watchdog.pid` 不存在、"
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


@router.post("/api/admin/restart")
async def admin_restart(request: Request):
    """**受控重启**（V58，老板 2026-10-19「最优解就行」拍板）。

    四条闸门（缺一不可，逐条如实拒绝 —— 宁可拒绝，也不把平台弄成永久宕机）：
      ① 来源必须是本机（无 Origin 的命令行调用也算本机）；
      ② 配了 `AC4_SHUTDOWN_PIN` ⇒ 必须带对 `X-Shutdown-Pin`；
      ③ **必须真有看门狗**（按实例端口认）：没有它，重启＝**永久宕机**；
      ④ body 必须显式 `{"confirm": true}`（重启会短暂中断在跑的工作间）。

    行为：**先让响应发出去**，0.8s 后本进程退出；看门狗按**原命令行**（含 token）重生，
    鉴权口径不变。返回里如实给出看门狗 pid 与预计时间。
    """
    from ..server import Utf8JSONResponse

    ok, why = origin_allowed(request)
    if not ok:
        return Utf8JSONResponse({"success": False, "error": f"拒绝重启：{why}"}, status_code=403)

    pin = shutdown_pin()
    if pin:
        got = (request.headers.get("X-Shutdown-Pin") or "").strip()
        if got != pin:
            return Utf8JSONResponse(
                {"success": False, "pin_required": True,
                 "error": "重启口令不正确（在 deploy/.env 的 AC4_SHUTDOWN_PIN 里配置）"},
                status_code=403)

    try:
        body = await request.json()
    except Exception:                                     # noqa: BLE001
        body = {}
    if not isinstance(body, dict):
        body = {}
    if body.get("confirm") is not True:
        return Utf8JSONResponse({
            "success": False,
            "error": "重启会让在跑的工作间短暂中断，必须显式确认：body 带 {\"confirm\": true}",
            "how_to_fix": "POST /api/admin/restart  body: {\"confirm\": true}",
        }, status_code=400)

    server_port = request.url.port or int(os.environ.get("AC4_SERVER_PORT", "18920") or 18920)
    wds = watchdog_pids(server_port)
    if not wds:
        # ★这是本端点最重要的一条：**没有看门狗就不许重启**。
        #   否则"点一下重启"= 平台永久消失，用户只会看到一个打不开的页面（最坏的假状态）。
        return Utf8JSONResponse({
            "success": False,
            "error": "没有看门狗在守这个实例 —— 现在重启，平台不会自己回来（会永久宕机）",
            "server_port": server_port,
            "how_to_fix": "先起看门狗：python tools/watchdog.py --daemon --port %d" % server_port,
        }, status_code=409)

    try:
        # ★2026-10-19 修：原写 `from ..audit_log import record` —— **该模块不存在**
        #   （平台只有 `platform/audit.py`，单例叫 `audit_log`）⇒ ImportError 被下面的
        #   except 静默吞掉 ⇒ 重启/关停**从来没写过审计**。现在用真实模块，且失败要出声。
        from ..audit import audit_log as _audit
        _audit.record("system.restart", actor="local-ui",
                      detail=f"port={server_port} watchdog_pids={wds} pin={'set' if pin else 'none'}")
    except Exception as _e:                               # noqa: BLE001
        print(f"[system] 重启审计写入失败（重启照常进行）: {type(_e).__name__}: {_e}", flush=True)

    _schedule_self_exit(0.8)
    return {
        "success": True,
        "restart_scheduled": True,
        "eta_seconds": 1,
        "server_port": server_port,
        "watchdog_pids": wds,
        "how": "已安排重启：看门狗将在 ≤30s 内按**原命令行**（含 token）把平台拉回来；"
               "期间本页面的接口会短暂连不上，属正常。",
        "note": "本端点不会自己 exec 新进程（会丢参数、且可能与看门狗抢端口）—— 拉回交给唯一的看门狗。",
    }


def _pid_alive(pid: int) -> bool:
    """pid 是否存活（★跨平台，见 `sysprobe.is_pid_alive`；原实现只有 Windows 的 tasklist）。"""
    from .. import sysprobe
    return sysprobe.is_pid_alive(pid)


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
        # 同上面 restart 那处：`platform/audit_log` 不存在，改用真实单例（失败要出声，不静默）
        from ..audit import audit_log as _audit
        _audit.record("system.shutdown", actor="local-ui",
                      detail=f"pin={'set' if pin else 'none'} ports={server_port}/{a2a_port}/{grpc_port}")
    except Exception as _e:                               # noqa: BLE001
        print(f"[system] 关停审计写入失败（关停照常进行）: {type(_e).__name__}: {_e}", flush=True)

    report = shutdown_targets(server_port, a2a_port, grpc_port)
    report["message"] = ("正在关闭：看门狗 → A2A → grpc → 平台本体。"
                         + ("⚠ 有项目没关掉，见 still_running。" if report["still_running"]
                            else "全部已关闭，可以关掉这个页面了。"))
    return report
