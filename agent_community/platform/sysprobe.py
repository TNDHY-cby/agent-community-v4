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
  **没有 psutil 时整层转向 `platform/procprobe.py`**（纯标准库、零第三方依赖）。

两层为什么这样分（2026-10-15，V50）
──────────────────────────────────
`procprobe` 不是"给看门狗专用的一份副本"，而是**回落实现本身**：
  · 平台侧：本模块 = psutil 优先 + 没有 psutil 时整层转 procprobe；
  · 看门狗侧（`tools/watchdog.py` / `deploy/watchdog_all.py`）：承诺"**全程标准库、无第三方依赖**"
    （它是"救一个坏掉的环境"的那只手，那时连依赖都可能装不上）⇒ **不能引 psutil**，
    于是直接用 procprobe。
⇒ 同一个事实（谁在监听某端口）**只有一份实现**（规则 20：一处真相），两条约束都满足。
不改层的话，看门狗在 Linux 上等于没有（端口查不到、僵死进程杀不掉）。
设计稿：`design-docs/V50_看门狗stdlib跨平台化与纯标准库进程探测.md`。

纪律
────
1. **只取事实，不做业务判断** —— 判据留在调用方（如 `harness_messaging._match_bridges`）；
2. **每个进程单独 try** —— 取不到某些进程的信息是常态（权限 / 进程恰好退出），
   不许因此整表失败（那会把"少数取不到"变成"一只都看不到"）；
3. **失败要说话** —— `backend()` / `probe_note()` 如实说明现在走哪条路，以及
   **"探不到 ≠ 真没有"**（Linux 非 root 只能看到自己进程的套接字，这是操作系统的限制）。
"""
from __future__ import annotations

import re

from . import procprobe as _pp

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
    """现在走哪条路：`"psutil"` / `"windows"` / `"procfs"` / `"ps"` / `"none"`。

    没有 psutil 时**如实报回落层自己走的路**（`procprobe.backend()`）——
    `"none"` 表示"**平台看不见**"，不等于"真的没有进程/端口"。
    """
    if has_psutil():
        return "psutil"
    return _pp.backend()


def probe_note() -> str:
    """人话说明"现在能探到什么"（供界面/接口如实展示，别让用户以为"真没有"）。"""
    if has_psutil():
        return "本机事实走 psutil（跨平台）"
    return ("没装 psutil：退回**纯标准库**探测 —— " + _pp.probe_note()
            + "。建议 `pip install -r requirements.txt` 补上 psutil（更快、更全）。")


# ── ① 进程表 ────────────────────────────────────────────────────────────────
def iter_processes():
    """产出 `(pid, ppid, name, cmdline)` —— **取进程表的一条真相**。

    psutil 主路径；没有 psutil 时整层转 `procprobe`（Windows 走 PowerShell，
    Linux 走 `/proc`，其它 POSIX 走 `ps`）——**任何平台上都不再是"恒空"**。
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
    yield from _pp.iter_processes()


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
    return _pp.process_cmdline(pid)


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
    return _pp.is_pid_alive(pid)


# ── ② 端口 ──────────────────────────────────────────────────────────────────
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
    if psutil is None:
        return _pp.listening_pids(port)
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


def port_listening(port: int) -> bool:
    """该端口是否有人在监听（**可查范围内**；见 `listening_pids` 的注意事项）。"""
    try:
        port = int(port)
    except Exception:  # noqa: BLE001
        return False
    psutil = _psutil()
    if psutil is not None:
        return bool(listening_pids(port))
    # 没有 psutil：**不逐 pid 反查**（那要看 `/proc/<pid>/fd`，非 root 只能看到自己）
    # 而是直接问"监听表里有没有这个端口" —— 这个判据在 Linux 上对所有进程都成立。
    return port in _pp.listening_ports()


def listening_ports(*, local_only: bool = False) -> list:
    """本机所有处于 LISTEN 的 TCP 端口（去重升序）。

    `local_only=True` 时只保留绑定在回环/通配地址上的（界面"本机服务"用）。
    """
    psutil = _psutil()
    if psutil is None:
        return _pp.listening_ports(local_only=local_only)
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
    if psutil is None:
        return _pp.kill_pid(pid, tree=tree)
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
