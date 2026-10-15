# -*- coding: utf-8 -*-
"""platform/procprobe：**零第三方依赖的本机事实探测**（纯标准库）。

它为什么存在
───────────
`platform/sysprobe.py` 是"本机事实的唯一入口"，但它的**主路径是 psutil**（第三方）。
而**看门狗**（`tools/watchdog.py`、`deploy/watchdog_all.py`）对外的承诺是
「**全程标准库实现、无第三方依赖**」—— 它是"救一个坏掉的环境"的那只手，
那时连依赖都可能装不上。两条约束撞在一起，此前的做法是**看门狗自己就地写一套 Windows 专属实现**
（`powershell` / `netstat` / `tasklist` / `taskkill`），后果不是报错，而是
**在 Linux 上静默降级**：端口被谁占着查不到、僵死进程杀不掉 ⇒ **看门狗在 Linux 上等于没有**。

⇒ 正确的切法是**按约束分层**，而不是按调用方分层（规则 20：一处真相）：

| 层 | 模块 | 依赖 | 谁用 |
|---|---|---|---|
| ① 首选 | `platform/sysprobe.py` | psutil 优先，**允许**第三方 | 平台业务（桥监管 / 退出并关闭 / 系统信息） |
| ② 兜底 | **`platform/procprobe.py`（本模块）** | **纯标准库，零第三方** | 看门狗、部署脚本，以及 `sysprobe` 没有 psutil 时的整层回落 |

设计稿：`design-docs/V50_看门狗stdlib跨平台化与纯标准库进程探测.md`。

纪律
────
1. **只取事实，不做业务判断**（判据留在调用方）；
2. **每个进程单独 try** —— 取不到某些进程是常态（权限 / 恰好退出），不许因此整表失败；
3. **失败要说话** —— `backend()` / `probe_note()` 如实说明走哪条路，
   以及 **"探不到 ≠ 真没有"**（Linux 非 root 只能看到自己进程的套接字，这是内核限制）；
4. **本模块不许 import 任何非标准库**（守卫 `tests/test_procprobe.py::test_pure_stdlib_only`
   用 `sys.stdlib_module_names` 真判），也不许 import 本包内的东西
   —— 看门狗要能用 `spec_from_file_location` **单独**把它加载起来。
"""
from __future__ import annotations

import os
import re
import signal
import subprocess
import sys
import time

# ── 平台判定（模块级常量：扫描器的"平台分支"判据认它们）────────────────────
_IS_WINDOWS = os.name == "nt"
_IS_LINUX = sys.platform.startswith("linux")
_IS_MACOS = sys.platform == "darwin"
_IS_POSIX = os.name == "posix"

#: Linux 的进程/网络事实全在这里；没有它就不是 procfs 那条路
_PROC = "/proc"

#: `/proc/net/tcp{,6}` 里 `local_address` 的**通配/回环**写法（小端十六进制）
_ANY_ADDR = ("00000000", "0" * 32)
_LOOPBACK_ADDR = ("0100007F", "0" * 24 + "01000000")


# ── 后端自述 ────────────────────────────────────────────────────────────────
def backend() -> str:
    """现在走哪条路：`"windows"` / `"procfs"` / `"ps"` / `"none"`。

    ⚠️ `"none"` 是**如实**的一种取值（既没有 Windows 命令、也没有 `/proc`/`ps`）——
    调用方**不许**把它当成"真的没有进程/端口"。
    """
    if _IS_WINDOWS:
        return "windows"
    if _IS_LINUX and os.path.isdir(_PROC):
        return "procfs"
    if _IS_POSIX:
        return "ps"
    return "none"


def probe_note() -> str:
    """人话说明"现在能探到什么"（供界面/日志如实展示，别让人以为"真没有"）。"""
    b = backend()
    if b == "windows":
        return "本机事实走 Windows 自带命令（PowerShell / netstat / tasklist），纯标准库、无第三方依赖"
    if b == "procfs":
        return ("本机事实走 Linux /proc（纯标准库、无第三方依赖）；"
                "⚠️非 root 只能看到**自己**进程的套接字 ⇒ 探不到 ≠ 真没有")
    if b == "ps":
        return ("本机事实走 POSIX 自带命令 ps / lsof / ss（纯标准库、无第三方依赖）；"
                "命令缺失时如实返回空，不假装没有")
    return ("**探不到本机进程/端口**：当前系统没有可用的探测路径"
            "（Windows 命令 / Linux /proc / POSIX ps 都没有）。"
            "⚠️这表示**看不见**，不等于「那个进程/端口真的不存在」。")


# ── 唯一的子进程出口 ────────────────────────────────────────────────────────
def _run(argv, timeout: float = 15.0) -> str:
    """跑一条只读**系统命令**取 stdout（失败一律返回空串，由调用方按"没探到"处理）。

    ⚠️编码：Windows 上子进程 stdout 是**本地 ANSI 代码页**（中文 Windows=cp936、英文=cp1252），
    写死 utf-8 会把中文路径解成替换符；POSIX 上统一 utf-8。两边都 `errors="replace"`，
    保证取到的那部分仍然可用（`netstat` / `tasklist` / `ss` / `lsof` 我们解析的几乎全是数字与 ASCII）。
    """
    try:
        if _IS_WINDOWS:
            r = subprocess.run(argv, capture_output=True, text=True,
                               errors="replace", timeout=timeout)
        else:
            r = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8",
                               errors="replace", timeout=timeout)
        return r.stdout or ""
    except Exception:  # noqa: BLE001
        return ""


#: PowerShell 的 stdout 重定向后**默认不是 UTF-8**（中文 Windows 是 cp936）
#: ⇒ 直接按某一种编码解，命令行动辄上千字符时会**随机解坏**（实测：`ConvertTo-Json`
#: 的整表输出在 char≈91043 处 `Expecting ',' delimiter`，而且长度每次都不一样）。
#: 唯一可靠的做法：**让 PowerShell 自己吐 UTF-8**（2026-10-15 实测连跑 5 次全过）。
_PS_UTF8 = "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; "


def _ps(script: str, timeout: float = 30.0) -> str:
    """跑一段 **PowerShell** 并取 UTF-8 输出（非 Windows 一律返回空串）。

    这是本模块里**唯一**的 PowerShell 出口 —— 想换实现只改这里。
    """
    if not _IS_WINDOWS:
        return ""
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", _PS_UTF8 + script],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
        return r.stdout or ""
    except Exception:  # noqa: BLE001
        return ""


# ── ① 进程表 ────────────────────────────────────────────────────────────────
def _proc_cmdline(pid: int) -> str:
    """Linux `/proc/<pid>/cmdline`（NUL 分隔 → 空格连接）。取不到返回空串。"""
    try:
        with open("%s/%d/cmdline" % (_PROC, pid), "rb") as f:
            raw = f.read()
    except Exception:  # noqa: BLE001
        return ""
    return raw.decode("utf-8", "replace").replace("\x00", " ").strip()


def _iter_procfs():
    """Linux：扫 `/proc/<pid>/{stat,cmdline}`。**每个进程单独 try**（取不到就跳过）。"""
    if not (_IS_LINUX and os.path.isdir(_PROC)):
        return
    try:
        entries = os.listdir(_PROC)
    except Exception:  # noqa: BLE001
        return
    for name in entries:
        if not name.isdigit():
            continue
        pid = int(name)
        try:
            with open("%s/%d/stat" % (_PROC, pid), "rb") as f:
                stat = f.read().decode("utf-8", "replace")
            # 形如 `1234 (comm with spaces) S 1 ...`：comm 可能含空格/括号 ⇒ 取首个 '(' 与末个 ')'
            left = stat.find("(")
            right = stat.rfind(")")
            if left < 0 or right < left:
                continue
            comm = stat[left + 1:right]
            ppid = int(stat[right + 2:].split()[1])
        except Exception:  # noqa: BLE001
            continue
        cmd = _proc_cmdline(pid) or ("[%s]" % comm)
        yield (pid, ppid, comm, cmd)


def _iter_ps():
    """POSIX（macOS 等）：`ps -eo pid=,ppid=,comm=,args=`。"""
    if _IS_WINDOWS:
        return
    out = _run(["ps", "-eo", "pid=,ppid=,comm=,args="], timeout=20)
    for line in out.splitlines():
        parts = line.split(None, 3)
        if len(parts) < 3:
            continue
        try:
            pid = int(parts[0])
            ppid = int(parts[1])
        except ValueError:
            continue
        comm = os.path.basename(parts[2])
        cmd = parts[3].strip() if len(parts) > 3 else parts[2]
        yield (pid, ppid, comm, cmd)


def _iter_windows():
    """Windows：`Get-CimInstance Win32_Process` → JSON（与历史实现同一套命令）。"""
    if not _IS_WINDOWS:
        return
    import json as _json
    out = _ps("Get-CimInstance Win32_Process | "
              "Select-Object ProcessId,ParentProcessId,Name,CommandLine | "
              "ConvertTo-Json -Compress", timeout=30)
    out = (out or "").strip()
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


def iter_processes():
    """产出 `(pid, ppid, name, cmdline)` —— **取进程表的一条真相**（纯标准库）。"""
    b = backend()
    if b == "procfs":
        yield from _iter_procfs()
    elif b == "ps":
        yield from _iter_ps()
    elif b == "windows":
        yield from _iter_windows()


def process_cmdline(pid: int) -> str:
    """某个 pid 的完整命令行（取不到返回空串）。"""
    try:
        pid = int(pid)
    except Exception:  # noqa: BLE001
        return ""
    if pid <= 0:
        return ""
    b = backend()
    if b == "procfs":
        return _proc_cmdline(pid)
    if b == "windows":
        if not _IS_WINDOWS:
            return ""
        out = _ps("(Get-CimInstance Win32_Process -Filter \"ProcessId=%d\").CommandLine" % pid,
                  timeout=20)
        return (out or "").strip()
    if b == "ps":
        if _IS_WINDOWS:
            return ""
        return _run(["ps", "-o", "args=", "-p", str(pid)], timeout=20).strip()
    return ""


def _pid_state(pid: int) -> str:
    """进程状态字符（Linux `/proc/<pid>/stat` 第 3 字段；其它 POSIX 用 `ps -o state=`）。

    取不到返回空串（**"探不到"**，由调用方决定怎么解释）。
    """
    if _IS_LINUX and os.path.isdir(_PROC):
        try:
            with open("%s/%d/stat" % (_PROC, pid), "rb") as f:
                stat = f.read().decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            return ""
        right = stat.rfind(")")
        if right < 0:
            return ""
        fields = stat[right + 2:].split()
        return fields[0] if fields else ""
    if _IS_WINDOWS:
        return ""
    out = _run(["ps", "-o", "state=", "-p", str(pid)], timeout=10)
    return (out or "").strip()[:1]


def is_pid_alive(pid: int) -> bool:
    """pid 是否存活（本机事实，不猜）。

    ⚠️**僵尸（`Z`）不算活着** —— 它已经死了，只是在等父进程收尸。
    Linux 上不处理这一条，`kill_pid()` 会**永远返回 False**：
    进程被 SIGKILL 之后先进入僵尸态，而 `os.kill(pid, 0)` 对它照样成功
    （实测：真 Linux 上 `test_kill_pid_*` 三条守卫全红，Windows 上全绿 —— 因为 Windows 没有僵尸态）。
    """
    try:
        pid = int(pid)
    except Exception:  # noqa: BLE001
        return False
    if pid <= 0:
        return False
    if _IS_WINDOWS:
        out = _run(["tasklist", "/FI", "PID eq %d" % pid, "/NH"], timeout=10)
        return str(pid) in out
    try:
        os.kill(pid, 0)          # 信号 0 = 只做存在性/权限检查，不真发信号
    except PermissionError:
        pass                     # 存在但不属于我（这就是"活着"）
    except Exception:  # noqa: BLE001
        return False
    state = _pid_state(pid)
    if _IS_LINUX and os.path.isdir(_PROC) and not state:
        return False             # /proc 里已经没有它了
    return state != "Z"


# ── ② 端口归属 ──────────────────────────────────────────────────────────────
def _windows_listen_pids(port: int) -> list:
    """Windows：`netstat -ano -p tcp` 里 LISTENING 行的最后一列是 pid。"""
    if not _IS_WINDOWS:
        return []
    out = _run(["netstat", "-ano", "-p", "tcp"], timeout=15)
    pids: list = []
    needle = ":%d" % port
    for line in (out or "").splitlines():
        parts = line.split()
        if len(parts) >= 5 and "LISTEN" in line.upper():
            if needle in parts[1] or needle in parts[2]:
                try:
                    pids.append(int(parts[-1]))
                except ValueError:
                    continue
    return sorted(set(pids))


def _procfs_listen_inodes(port: int):
    """返回 `(inodes, readable)` —— `readable=False` 表示 `/proc/net/tcp*` **读不到**。

    这个区分很重要：**"读不到"绝不能被当成"没人监听"**（那是静默降级的老毛病）。
    """
    inodes: set = set()
    readable = False
    for fn in ("tcp", "tcp6"):
        try:
            with open("%s/net/%s" % (_PROC, fn), "r", encoding="utf-8", errors="replace") as f:
                lines = f.read().splitlines()
        except Exception:  # noqa: BLE001
            continue
        readable = True
        for line in lines[1:]:                       # 第 0 行是表头
            parts = line.split()
            if len(parts) < 10 or parts[3] != "0A":  # 0A = LISTEN
                continue
            try:
                lport = int(parts[1].split(":")[1], 16)
            except Exception:  # noqa: BLE001
                continue
            if lport == port:
                inodes.add(parts[9])                 # 第 9 列 = inode
    return inodes, readable


def _pids_for_socket_inodes(inodes) -> list:
    """按 `socket:[inode]` 反查 pid（扫 `/proc/<pid>/fd/*` 的符号链接）。

    ⚠️非 root 只能读**自己**进程的 fd 目录 ⇒ 结果是"**可查范围内**"的结论。
    """
    if not inodes:
        return []
    want = {"socket:[%s]" % i for i in inodes}
    pids: list = []
    try:
        entries = os.listdir(_PROC)
    except Exception:  # noqa: BLE001
        return []
    for name in entries:
        if not name.isdigit():
            continue
        pid = int(name)
        fd_dir = "%s/%d/fd" % (_PROC, pid)
        try:
            fds = os.listdir(fd_dir)
        except Exception:  # noqa: BLE001
            continue                                 # 权限不足 / 进程恰好退出：常态，跳过
        for fd in fds:
            try:
                target = os.readlink(os.path.join(fd_dir, fd))
            except Exception:  # noqa: BLE001
                continue
            if target in want:
                pids.append(pid)
                break
    return sorted(set(pids))


def _cmd_listen_pids(port: int) -> list:
    """POSIX 的第二个意见：`ss -ltnp` → 再 `lsof -nP -iTCP:<port> -sTCP:LISTEN -t`。"""
    if _IS_WINDOWS:
        return []
    out = _run(["ss", "-ltnp"], timeout=15)
    pids: list = []
    if out:
        rx = re.compile(r":%d\b" % port)
        for line in out.splitlines():
            if not rx.search(line):
                continue
            for m in re.finditer(r"pid=(\d+)", line):
                pids.append(int(m.group(1)))
    if pids:
        return sorted(set(pids))
    out2 = _run(["lsof", "-nP", "-iTCP:%d" % port, "-sTCP:LISTEN", "-t"], timeout=15)
    return sorted({int(x) for x in (out2 or "").split() if x.strip().isdigit()})


def listening_pids(port: int) -> list:
    """谁在监听该端口 → pid 列表（**可能不全**，见 `probe_note()`）。"""
    try:
        port = int(port)
    except Exception:  # noqa: BLE001
        return []
    b = backend()
    if b == "windows":
        return _windows_listen_pids(port)
    if b == "procfs":
        inodes, readable = _procfs_listen_inodes(port)
        pids = _pids_for_socket_inodes(inodes)
        if pids:
            return pids
        if not readable:
            return _cmd_listen_pids(port)            # /proc 读不到：换第二个意见，别直接说"没有"
        return _cmd_listen_pids(port) or []          # 有 inode 却查不到 pid（多半是别人的进程）
    if b == "ps":
        return _cmd_listen_pids(port)
    return []


def _procfs_listen_ports(local_only: bool) -> list:
    """Linux：从 `/proc/net/tcp{,6}` 取所有 LISTEN 的本地端口。"""
    ports: set = set()
    for fn in ("tcp", "tcp6"):
        try:
            with open("%s/net/%s" % (_PROC, fn), "r", encoding="utf-8", errors="replace") as f:
                lines = f.read().splitlines()
        except Exception:  # noqa: BLE001
            continue
        for line in lines[1:]:
            parts = line.split()
            if len(parts) < 4 or parts[3] != "0A":
                continue
            addr, _, port_hex = parts[1].partition(":")
            if local_only and addr not in (_ANY_ADDR + _LOOPBACK_ADDR):
                continue
            try:
                ports.add(int(port_hex, 16))
            except Exception:  # noqa: BLE001
                continue
    return sorted(ports)


def _cmd_listen_ports(local_only: bool) -> list:
    """POSIX 的第二个意见：`lsof -nP -iTCP -sTCP:LISTEN`。"""
    if _IS_WINDOWS:
        return []
    out = _run(["lsof", "-nP", "-iTCP", "-sTCP:LISTEN"], timeout=20)
    ports: set = set()
    for line in (out or "").splitlines():
        if "(LISTEN)" not in line:
            continue
        m = re.search(r"(\S+):(\d+)\s+\(LISTEN\)", line)
        if not m:
            continue
        host, port_s = m.group(1), m.group(2)
        if local_only and host not in ("*", "127.0.0.1", "localhost", "[::1]", "::1"):
            continue
        ports.add(int(port_s))
    return sorted(ports)


def listening_ports(*, local_only: bool = False) -> list:
    """本机所有处于 LISTEN 的 TCP 端口（去重升序）。

    `local_only=True` 时只保留绑定在**回环/通配**地址上的（界面"本机服务"用）。
    """
    b = backend()
    if b == "windows":
        if not _IS_WINDOWS:
            return []
        ports: set = set()
        out = _run(["netstat", "-ano", "-p", "tcp"], timeout=15)
        for line in (out or "").splitlines():
            if "LISTEN" not in line.upper():
                continue
            m = re.search(r"(\d+\.\d+\.\d+\.\d+|\[::\]|0\.0\.0\.0):(\d+)\s", line)
            if not m:
                continue
            if local_only and m.group(1) not in ("127.0.0.1", "0.0.0.0", "[::]"):
                continue
            ports.add(int(m.group(2)))
        return sorted(ports)
    if b == "procfs":
        ports = _procfs_listen_ports(local_only)
        return ports if ports else _cmd_listen_ports(local_only)
    if b == "ps":
        return _cmd_listen_ports(local_only)
    return []


# ── ③ 结束进程 ──────────────────────────────────────────────────────────────
def _descendants(pid: int) -> list:
    """按 ppid 关系收出全部后代（**子进程先杀**，避免它们被重新拉起成孤儿）。"""
    children: dict = {}
    try:
        for p, pp, _n, _c in iter_processes():
            children.setdefault(pp, []).append(p)
    except Exception:  # noqa: BLE001
        return []
    out: list = []
    stack = list(children.get(pid, []))
    while stack:
        cur = stack.pop()
        if cur in out or cur == pid:
            continue
        out.append(cur)
        stack.extend(children.get(cur, []))
    return out


def kill_pid(pid: int, *, tree: bool = False) -> bool:
    """结束进程；`tree=True` 连子进程一起（看门狗会拉起子进程，故有此需求）。

    返回是否**确实已不存活**（不是"命令返回 0"就算成功 —— 失败要说话）。
    纯标准库：Windows 走 `taskkill`，POSIX 走 `os.kill`（SIGTERM → 宽限 → SIGKILL）。
    """
    try:
        pid = int(pid)
    except Exception:  # noqa: BLE001
        return False
    if pid <= 0:
        return False
    if _IS_WINDOWS:
        args = ["taskkill", "/F", "/PID", str(pid)]
        if tree:
            args.insert(1, "/T")
        _run(args, timeout=15)
        return not is_pid_alive(pid)
    if not _IS_POSIX:
        return not is_pid_alive(pid)

    targets = _descendants(pid) if tree else []
    targets.append(pid)
    # 先 SIGTERM（给一个体面退出的机会），子进程先来
    for t in reversed(targets):
        try:
            os.kill(t, signal.SIGTERM)
        except Exception:  # noqa: BLE001
            pass
    deadline = time.time() + 5.0
    while time.time() < deadline:
        if not any(is_pid_alive(t) for t in targets):
            break
        time.sleep(0.1)
    for t in reversed(targets):
        if is_pid_alive(t):
            try:
                os.kill(t, signal.SIGKILL)
            except Exception:  # noqa: BLE001
                pass
    deadline = time.time() + 5.0
    while time.time() < deadline and is_pid_alive(pid):
        time.sleep(0.1)
    return not is_pid_alive(pid)


# ── ④ 自检入口（排障用：把"现在能探到什么"如实打出来）─────────────────────
def main() -> int:
    try:  # 中文打印在 cp1252 控制台会炸（GitHub windows runner 实测）⇒ 先自保
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    rows = list(iter_processes())
    print("procprobe 自检")
    print("  backend    : %s" % backend())
    print("  note       : %s" % probe_note())
    print("  processes  : %d" % len(rows))
    print("  自己的 pid  : %d（在进程表里：%s）"
          % (os.getpid(), any(int(r[0]) == os.getpid() for r in rows)))
    print("  监听端口   : %s" % (listening_ports(local_only=True)[:20],))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
