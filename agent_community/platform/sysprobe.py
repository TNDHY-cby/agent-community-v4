# -*- coding: utf-8 -*-
"""platform/sysprobe：**本机事实的跨平台入口**（进程表 / 命令行 / pid 存活 / 端口归属 / 结束进程）。

为什么需要它（2026-10-14）
────────────────────────
这些事此前**一律靠 Windows 专属命令**：
    `Get-CimInstance Win32_Process`（进程表）、`Get-NetTCPConnection`（监听端口）、
    `tasklist`（pid 是否存活）、`netstat -ano`（端口归属）、`taskkill`（结束进程）。
后果不是"报错"，而是**静默降级** —— Linux 上它们恒返回空/False：
    · 「桥在不在跑」永远认不出来 ⇒ `auto-connect` 会**重复起桥**；
    · 「端口被谁占着」永远查不到 ⇒ 界面说"没查到"（用户以为没人占）；
    · 「停下某端口」永远关不掉；
    · 更阴的一层：**测试里"认不出来"类断言恒真 = 假绿**
      （`test_bridge_watch_probe.py` 此前只能整文件 skip，等于 Linux 上没人守）。
⇒ 收成**一处真相**：主路径 **psutil**（Win/Linux/macOS 都有 wheel），
  `PowerShell` / `netstat` / `tasklist` 只作**没有 psutil 时的回落**（老环境不许整块失效）。

纪律
────
1. **只取事实，不做业务判断** —— 判据留在调用方（如 `harness_messaging._match_bridges`）；
2. **每个进程单独 try** —— 取不到某些进程的信息是常态（权限 / 进程恰好退出），
   不许因此整表失败（那会把"少数取不到"变成"一只都看不到"）；
3. **失败要说话** —— `backend()` / `probe_note()` 如实说明现在走哪条路，以及
   **"探不到 ≠ 真没有"**（Linux 非 root 只能看到自己进程的套接字，这是操作系统的限制）。
"""
from __future__ import annotations

import os
import re
import subprocess

#: 进程表里"只认解释器"时用的名字前缀（两个平台的实际进程名形态都覆盖：
#: Windows `python.exe` / POSIX `python3`、`python3.12`）
PYTHON_NAME_PREFIX = "python"


def _psutil():
    """拿到 psutil 模块；没装返回 None（**不抛**：回落路径要能接上）。"""
    try:
        import psutil  # noqa: PLC0415
        return psutil
    except Exception:  # noqa: BLE001
        return None


def has_psutil() -> bool:
    return _psutil() is not None


def backend() -> str:
    """现在走哪条路：`"psutil"` / `"powershell"` / `"none"`。

    ⚠️`"powershell"` 只在 **Windows** 上可能出现（其它平台没有它，也不该假装有）。
    """
    if has_psutil():
        return "psutil"
    if os.name == "nt":
        return "powershell"
    return "none"


def probe_note() -> str:
    """人话说明"现在能探到什么"（供界面/接口如实展示，别让用户以为"真没有"）。"""
    b = backend()
    if b == "psutil":
        return "本机事实走 psutil（跨平台）"
    if b == "powershell":
        return ("没装 psutil：退回 Windows 专属的 PowerShell 探测（能用，但慢且只在 Windows）；"
                "建议 `pip install -r requirements.txt` 补上 psutil")
    return ("**探不到本机进程/端口**：本平台既没有 psutil、当前系统也没有 PowerShell 回落路径。"
            "⚠️注意：这表示**平台看不见**，不等于「桥/端口真的不存在」—— "
            "装上 psutil（`pip install psutil`）即可恢复。")


def _run(argv, timeout: float = 30.0) -> str:
    """跑一条只读命令并取 stdout（失败一律返回空串，由调用方按"没探到"处理）。"""
    try:
        r = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=timeout)
        return r.stdout or ""
    except Exception:  # noqa: BLE001
        return ""


# ── ① 进程表 ────────────────────────────────────────────────────────────────
def iter_processes():
    """产出 `(pid, ppid, name, cmdline)` —— **取进程表的一条真相**。

    psutil 主路径；没有 psutil 时：
      · Windows → `Get-CimInstance Win32_Process` 回落；
      · 其它平台 → **如实返回空**（没有等价命令；这里不许假装有）。
    """
    psutil = _psutil()
    if psutil is not None:
        for p in psutil.process_iter(["pid", "ppid", "name", "cmdline"]):
            try:
                info = p.info or {}
                cmd = info.get("cmdline") or []
                yield (int(info.get("pid") or 0), int(info.get("ppid") or 0),
                       str(info.get("name") or ""),
                       " ".join(str(c) for c in cmd))
            except Exception:  # noqa: BLE001
                continue
        return

    if os.name != "nt":
        return

    import json as _json
    out = _run(["powershell", "-NoProfile", "-Command",
                "Get-CimInstance Win32_Process | "
                "Select-Object ProcessId,ParentProcessId,Name,CommandLine | "
                "ConvertTo-Json -Compress"])
    out = out.strip()
    if not out or out == "null":
        return
    try:
        rows = _json.loads(out)
    except Exception:  # noqa: BLE001
        return
    if isinstance(rows, dict):
        rows = [rows]
    for x in (rows or []):
        try:
            yield (int(x.get("ProcessId") or 0), int(x.get("ParentProcessId") or 0),
                   str(x.get("Name") or ""), str(x.get("CommandLine") or ""))
        except Exception:  # noqa: BLE001
            continue


def find_processes(needles=None, *, name_matches=None, exclude_ppid=None,
                   python_only=False, limit: int = 8) -> list:
    """按命令行线索找进程 → `[{pid, name, cmdline}]`（**只读**，不杀）。

    `needles`：命令行里必须含其中**任意一个**。
      · `needles=None` ＝ **不按命令行过滤**（只按进程名挑；只有"列出全部候选"这类用途才这么传）；
      · `needles=[]` 或全是空白 ⇒ **返回空**（不许把空线索当成"匹配一切"——那是误杀的根源）。
    `name_matches`：进程名的正则（如 `r"python|node"`）；`python_only=True` 是它的常用简写。
    `exclude_ppid`：排除父进程是它的（生产里传自己的 pid ⇒ 不把"探测动作自己的子进程"算进去）。
    `limit`：最多返回几条（调用方通常只需要"有没有、有几个"）。
    """
    if needles is None:
        want = None
    else:
        want = [str(n).strip() for n in needles if str(n or "").strip()]
        if not want:
            return []
    if python_only and not name_matches:
        name_matches = r"^" + re.escape(PYTHON_NAME_PREFIX)
    rx = re.compile(name_matches, re.IGNORECASE) if name_matches else None
    hits = []
    for pid, ppid, name, cmdline in iter_processes():
        if len(hits) >= max(1, int(limit)):
            break
        if not cmdline:
            continue
        if rx is not None and not rx.search(str(name or "")):
            continue
        if exclude_ppid is not None and int(ppid or 0) == int(exclude_ppid):
            continue
        if want is not None and not any(
                n in cmdline or n.replace("\\", "/") in cmdline for n in want):
            continue
        hits.append({"pid": pid, "name": name, "cmdline": cmdline[:220]})
    return hits


def process_cmdline(pid: int) -> str:
    """某个 pid 的完整命令行（取不到返回空串）。"""
    try:
        pid = int(pid)
    except Exception:  # noqa: BLE001
        return ""
    psutil = _psutil()
    if psutil is not None:
        try:
            return " ".join(psutil.Process(pid).cmdline())
        except Exception:  # noqa: BLE001
            return ""
    if os.name == "nt":
        out = _run(["powershell", "-NoProfile", "-Command",
                    f"(Get-CimInstance Win32_Process -Filter \"ProcessId={pid}\").CommandLine"],
                   timeout=20)
        return (out or "").strip()
    # POSIX 无 psutil：/proc 是标准做法（Linux；其它 unix 取不到就如实返回空）
    try:
        with open("/proc/%d/cmdline" % pid, "rb") as f:
            return f.read().decode("utf-8", "replace").replace("\x00", " ").strip()
    except Exception:  # noqa: BLE001
        return ""


def is_pid_alive(pid: int) -> bool:
    """pid 是否存活（本机事实，不猜）。"""
    try:
        pid = int(pid)
    except Exception:  # noqa: BLE001
        return False
    if pid <= 0:
        return False
    psutil = _psutil()
    if psutil is not None:
        try:
            return bool(psutil.pid_exists(pid))
        except Exception:  # noqa: BLE001
            return False
    if os.name == "nt":
        out = _run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], timeout=10)
        return str(pid) in out
    try:
        os.kill(pid, 0)          # 信号 0 = 只做存在性/权限检查，不真发信号
        return True
    except PermissionError:
        return True              # 存在但不属于我（这就是"活着"）
    except Exception:  # noqa: BLE001
        return False


# ── ② 端口 ──────────────────────────────────────────────────────────────────
def _netstat_lines() -> list:
    """Windows `netstat -ano` 的输出行（没有它就返回空）。"""
    if os.name != "nt":
        return []
    return _run(["netstat", "-ano", "-p", "TCP"], timeout=10).splitlines()


def listening_pids(port: int) -> list:
    """谁在监听该端口 → pid 列表（**可能不全**，见 `probe_note()`）。

    ⚠️Linux 非 root 时只能看到**自己**进程的套接字（内核限制）⇒ 不要把这个结果
      说成"没人监听"；调用方要如实说明"可查范围内没有"。
    """
    try:
        port = int(port)
    except Exception:  # noqa: BLE001
        return []
    psutil = _psutil()
    if psutil is not None:
        pids = []
        try:
            conns = psutil.net_connections(kind="tcp")
        except Exception:  # noqa: BLE001 — 权限受限时拿不到全部；退回逐个查监听地址
            conns = []
            try:
                for t in psutil.net_connections(kind="tcp4"):
                    conns.append(t)
            except Exception:  # noqa: BLE001
                conns = []
        for c in conns:
            try:
                if c.status != psutil.CONN_LISTEN:
                    continue
                if not c.laddr or int(getattr(c.laddr, "port", 0)) != port:
                    continue
                if c.pid and int(c.pid) not in pids:
                    pids.append(int(c.pid))
            except Exception:  # noqa: BLE001
                continue
        return pids
    out = []
    pat = re.compile(r":%d\s+\S+\s+LISTENING\s+(\d+)" % port)
    for line in _netstat_lines():
        m = pat.search(line)
        if m:
            pid = int(m.group(1))
            if pid and pid not in out:
                out.append(pid)
    return out


def port_listening(port: int) -> bool:
    """该端口是否有人在监听（**可查范围内**；见 `listening_pids` 的注意事项）。"""
    try:
        port = int(port)
    except Exception:  # noqa: BLE001
        return False
    psutil = _psutil()
    if psutil is not None:
        return bool(listening_pids(port))
    pat = re.compile(r":%d\s+\S+\s+LISTENING" % port)
    return any(pat.search(ln) for ln in _netstat_lines())


def listening_ports(*, local_only: bool = False) -> list:
    """本机所有处于 LISTEN 的 TCP 端口（去重升序）。

    `local_only=True` 时只保留绑定在回环/通配地址上的（界面"本机服务"用）。
    """
    psutil = _psutil()
    if psutil is not None:
        ports = set()
        try:
            conns = psutil.net_connections(kind="tcp")
        except Exception:  # noqa: BLE001
            conns = []
        for c in conns:
            try:
                if c.status != psutil.CONN_LISTEN or not c.laddr:
                    continue
                if local_only:
                    ip = str(getattr(c.laddr, "ip", "") or "")
                    if ip not in ("127.0.0.1", "0.0.0.0", "::", "::1"):
                        continue
                ports.add(int(c.laddr.port))
            except Exception:  # noqa: BLE001
                continue
        return sorted(ports)
    ports = set()
    for line in _netstat_lines():
        if "LISTENING" not in line:
            continue
        m = re.search(r"(\d+\.\d+\.\d+\.\d+|\[::\]|0\.0\.0\.0):(\d+)\s+\S+\s+LISTENING", line)
        if not m:
            continue
        if local_only and m.group(1) not in ("127.0.0.1", "0.0.0.0", "[::]"):
            continue
        ports.add(int(m.group(2)))
    return sorted(ports)


# ── ③ 结束进程（"退出并关闭"要用；**跨平台**）────────────────────────────────
def kill_pid(pid: int, *, tree: bool = False) -> bool:
    """结束进程；`tree=True` 连子进程一起（看门狗会拉起子进程，故有此需求）。

    返回是否**确实已不存活**（不是"命令返回 0"就算成功 —— 失败要说话）。
    """
    try:
        pid = int(pid)
    except Exception:  # noqa: BLE001
        return False
    if pid <= 0:
        return False
    psutil = _psutil()
    if psutil is not None:
        try:
            proc = psutil.Process(pid)
            targets = proc.children(recursive=True) if tree else []
            targets.append(proc)
            for p in targets:
                try:
                    p.terminate()
                except Exception:  # noqa: BLE001
                    pass
            psutil.wait_procs(targets, timeout=5)
            for p in targets:
                try:
                    if p.is_running():
                        p.kill()
                except Exception:  # noqa: BLE001
                    pass
            psutil.wait_procs(targets, timeout=5)
        except Exception:  # noqa: BLE001
            pass
        return not is_pid_alive(pid)
    if os.name == "nt":
        args = ["taskkill", "/F", "/PID", str(pid)]
        if tree:
            args.insert(1, "/T")
        _run(args, timeout=15)
        return not is_pid_alive(pid)
    import signal
    try:
        os.kill(pid, signal.SIGTERM)
        return True
    except Exception:  # noqa: BLE001
        return not is_pid_alive(pid)
