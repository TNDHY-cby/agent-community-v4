# -*- coding: utf-8 -*-
"""`platform/sysprobe`：**本机事实的跨平台入口** —— 把它钉住，别退回"只有 Windows 能用"。

背景（2026-10-14）：进程表 / 端口归属 / pid 存活 / 结束进程 这四件事此前一律靠
Windows 专属命令（`Get-CimInstance` / `Get-NetTCPConnection` / `tasklist` / `netstat -ano` /
`taskkill`）⇒ **Linux 上静默降级**（恒返回空/False）：
    · 「桥在不在跑」认不出来 ⇒ auto-connect 重复起桥；
    · 「端口被谁占着」查不到、连"退出并关闭"都关不掉；
    · 而且**测试里"认不出来"类断言恒真 = 假绿**（那几个文件此前只能整文件 skip）。

本文件按**真结果**判（不查源码文本）：
  ① 真拿自己的进程/真开一个端口去问它，看答得对不对；
  ② **证明"有 psutil 时绝不回退去 shell"** —— 把回落通道（`platform/procprobe.py` 的
     `_run` / `_ps`，所有 PowerShell/netstat/tasklist/ss/lsof 都经它）整个封死，
     主路径仍必须工作（这条是"跨平台"的真正判据：不是"能跑"，是"不依赖那个平台专属的东西"）；
  ③ **没有 psutil 时回落层自己也必须答得对**（2026-10-15 加，V50）——
     否则"装个 psutil 就好了"会变成产品的前提条件，而看门狗那一族**根本不能装 psutil**。
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import time

import pytest

from agent_community.platform import sysprobe


def test_backend_is_honest_about_what_it_can_see():
    """`backend()` / `probe_note()` 必须如实说明现在走哪条路（失败要说话）。

    ★2026-10-15（V50）：取值域从 `("psutil","powershell","none")` 扩成下面这组 ——
      回落实现搬进了 `platform/procprobe.py`，非 Windows 上不再报 `"none"`
      （那正是"Linux 上等于没有"的老毛病，现在有 `/proc` / `ps` 两条真路可走）。
    """
    b = sysprobe.backend()
    assert b in ("psutil", "windows", "procfs", "ps", "none")
    if sysprobe.has_psutil():
        assert b == "psutil"
    elif os.name == "nt":
        assert b == "windows", "Windows 上没有 psutil 时应当走本机命令那条路"
    else:
        assert b in ("procfs", "ps"), (
            "非 Windows 上没有 psutil 时必须**有一条真能用的路**（Linux /proc 或 POSIX ps）；"
            "报 %r 说明又退回'静默降级'了" % b)
    note = sysprobe.probe_note()
    assert note and isinstance(note, str)
    if b == "none":
        assert "psutil" in note, "探不到时必须说清怎么恢复（装 psutil），别让人以为'真没有'"


def test_iter_processes_sees_this_very_process():
    """进程表里必须有**我们自己**（这是"真取到了进程表"的最小证据，两平台都成立）。"""
    me = os.getpid()
    rows = list(sysprobe.iter_processes())
    assert rows, "一只进程都没取到 —— 探测根本不可用（回归到 Linux 上的旧毛病了）"
    pids = {int(r[0]) for r in rows}
    assert me in pids, "连自己都没取到（%d 不在 %d 条里）" % (me, len(rows))


def test_no_shell_fallback_when_psutil_is_present(monkeypatch):
    """★核心判据：**有 psutil 时，绝不许回退去 shell 那套命令**。

    做法：把回落层的**唯一子进程出口** `procprobe._run`（PowerShell / netstat / tasklist /
    ss / lsof 全经它）和它上面的 PowerShell 出口 `procprobe._ps` 一起封死，
    主路径仍必须照常工作 —— 否则说明某条路偷偷退回了"平台专属"的实现，
    而那正是"Linux 上等于没有"的成因。

    ★2026-10-15（V50）：回落实现已从本模块搬进 `platform/procprobe.py` ⇒ **封的对象跟着搬**。
      只封 `sysprobe._run` 会变成"封一条已经不存在的通道"：守卫看着还是绿，其实一点牙都没有。
    """
    if not sysprobe.has_psutil():
        pytest.skip("本机没有 psutil：这条判据的前提不成立（此时回落是**有意的**）")

    from agent_community.platform import procprobe

    def _boom(*a, **k):
        raise AssertionError("有 psutil 却回退去 shell 了：%r %r" % (a[:1], k))

    monkeypatch.setattr(procprobe, "_run", _boom, raising=True)
    monkeypatch.setattr(procprobe, "_ps", _boom, raising=True)

    rows = list(sysprobe.iter_processes())
    assert any(int(r[0]) == os.getpid() for r in rows), "进程表不该因为封死回落就变空"
    assert sysprobe.is_pid_alive(os.getpid()) is True
    assert sysprobe.process_cmdline(os.getpid()) != ""
    assert isinstance(sysprobe.listening_ports(), list)
    assert isinstance(sysprobe.listening_pids(1), list)


def test_listening_ports_and_pids_see_a_real_socket_we_open():
    """真开一个监听端口，问它"谁在听/都有哪些端口在听" —— 两个平台都必须答对。

    这是"跨平台"的硬判据：Linux 上旧实现恒返回空，这条会当场变红。
    """
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        port = int(srv.getsockname()[1])
        assert sysprobe.port_listening(port) is True, "自己开的监听端口都不认，端口探测不可用"
        assert port in sysprobe.listening_ports(local_only=True), \
            "自己开的端口不在监听列表里"
        pids = sysprobe.listening_pids(port)
        # ⚠️Linux 非 root 只能看到**自己**进程的套接字 —— 我们是自己进程，所以这里必定看得到
        assert os.getpid() in pids, "自己进程在听这个端口，却没认出来：%r" % (pids,)
        assert sysprobe.port_listening(port + 1) is False or port + 1 == port, \
            "没人在听的端口不该报 True"
    finally:
        srv.close()
    # 关掉之后应当不再算"在听"（允许极短的回收窗口）
    for _ in range(10):
        if not sysprobe.port_listening(port):
            break
        time.sleep(0.1)
    assert not sysprobe.port_listening(port), "端口已关，仍报在监听"


def test_kill_pid_actually_ends_a_child_and_reports_the_truth():
    """结束进程：真起一个子进程 → 杀掉 → 必须**真的没了**（不是"命令返回 0"）。"""
    p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        time.sleep(0.5)
        assert sysprobe.is_pid_alive(p.pid) is True
        assert sysprobe.process_cmdline(p.pid) != "", "取不到自己起的子进程的命令行"
        assert sysprobe.kill_pid(p.pid) is True, "杀完必须返回「确实已不存活」"
        p.wait(timeout=10)
        assert sysprobe.is_pid_alive(p.pid) is False
    finally:
        if p.poll() is None:
            p.kill()


def test_find_processes_does_not_treat_empty_needles_as_match_all():
    """空线索**绝不**等于"匹配一切"（那是误杀的根源：曾把平台自己杀掉）。"""
    assert sysprobe.find_processes([]) == []
    assert sysprobe.find_processes(["", "   "]) == []
    # needles=None 才是显式的"只按进程名挑"
    rows = sysprobe.find_processes(None, name_matches=r"python", limit=5)
    assert isinstance(rows, list)


class TestStdlibTierWithoutPsutil:
    """★2026-10-15（V50）：**没有 psutil 时，这一层也必须答得对**（真结果，不看源码）。

    这条为什么重要：看门狗（`tools/watchdog.py` / `deploy/watchdog_all.py`）承诺
    "**全程标准库、无第三方依赖**"——它是"救一个坏掉的环境"的那只手，那时连依赖都装不上
    ⇒ 它**不能**靠"装个 psutil 就好了"。所以"Linux 上等于没有"这件事必须在**没有 psutil**
    的前提下被钉死，而不是靠 CI 里恰好装了 psutil 蒙过去。
    """

    @pytest.fixture
    def no_psutil(self, monkeypatch):
        monkeypatch.setattr(sysprobe, "_psutil", lambda: None)
        assert sysprobe.has_psutil() is False
        assert sysprobe.backend() in ("windows", "procfs", "ps"), \
            "没有 psutil 时必须有一条真能用的回落路（不能是 none）"

    def test_iter_processes_sees_this_very_process(self, no_psutil):
        me = os.getpid()
        rows = list(sysprobe.iter_processes())
        assert rows, "没有 psutil 时进程表是空的 —— 这就是'Linux 上等于没有'的老毛病"
        assert me in {int(r[0]) for r in rows}, "连自己都没取到：%d 不在 %d 条里" % (me, len(rows))

    def test_cmdline_and_pid_alive(self, no_psutil):
        assert sysprobe.process_cmdline(os.getpid()) != "", "取不到自己的命令行"
        assert sysprobe.is_pid_alive(os.getpid()) is True
        assert sysprobe.is_pid_alive(999999999) is False

    def test_finds_the_socket_we_open(self, no_psutil):
        """★真开一个监听端口去问它 —— 旧实现（Linux 上恒空）会**当场变红**。"""
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            srv.bind(("127.0.0.1", 0))
            srv.listen(1)
            port = int(srv.getsockname()[1])
            assert sysprobe.port_listening(port) is True, "自己开的监听端口都不认"
            assert port in sysprobe.listening_ports(local_only=True), "自己开的端口不在监听列表里"
            assert os.getpid() in sysprobe.listening_pids(port), \
                "自己进程在听这个端口，却没认出来：%r" % (sysprobe.listening_pids(port),)
        finally:
            srv.close()
        for _ in range(10):
            if not sysprobe.port_listening(port):
                break
            time.sleep(0.1)
        assert not sysprobe.port_listening(port), "端口已关，仍报在监听"

    def test_kills_a_real_child(self, no_psutil):
        p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        try:
            time.sleep(0.5)
            assert sysprobe.is_pid_alive(p.pid) is True
            assert sysprobe.kill_pid(p.pid) is True, "杀完必须返回「确实已不存活」"
            p.wait(timeout=10)
            assert sysprobe.is_pid_alive(p.pid) is False
        finally:
            if p.poll() is None:
                p.kill()
