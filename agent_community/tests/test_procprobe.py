# -*- coding: utf-8 -*-
"""`platform/procprobe`：**零第三方依赖的本机事实探测** —— 把它钉住。

它存在的理由（2026-10-15，V50 欠账清偿）
──────────────────────────────────────
看门狗（`tools/watchdog.py` / `deploy/watchdog_all.py`）承诺「**全程标准库、无第三方依赖**」
（它是"救一个坏掉的环境"的那只手，那时连依赖都可能装不上）⇒ **不能引 psutil**，
于是它的进程发现此前是 **Windows 专属**实现 ⇒ **在 Linux 上静默降级**：
"端口被谁占着""那个僵死进程杀不杀得掉"全是空/False ⇒ **看门狗在 Linux 上等于没有**。
本模块是那件事的**唯一**实现（`sysprobe` 没有 psutil 时也整层转向它）。

本文件按**真结果**判（不查源码文本）：
  ① 真拿自己的进程、真开一个端口、真起一个子进程去问它；
  ② 真按**生产调用方式**（`python agent_community/tools/watchdog.py ...`）跑一次看门狗；
  ③ "纯标准库"这条承诺用 `sys.stdlib_module_names` **真判**（不是肉眼看 import 行）。
"""
from __future__ import annotations

import ast
import importlib.util
import os
import socket
import subprocess
import sys
import time

import pytest

_REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
_PROCPROBE_PY = os.path.join(_REPO, "agent_community", "platform", "procprobe.py")
_WATCHDOG_PY = os.path.join(_REPO, "agent_community", "tools", "watchdog.py")

sys.path.insert(0, _REPO)

from agent_community.platform import procprobe  # noqa: E402


def _load_by_path(path: str, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


wd = _load_by_path(_WATCHDOG_PY, "_wd_for_procprobe_tests")


# ── ① "纯标准库"是承诺，不是口号 ────────────────────────────────────────────
def test_pure_stdlib_only():
    """`procprobe.py` 只许 import 标准库（用 `sys.stdlib_module_names` 真判）。

    为什么较真：这条承诺一旦破了（有人顺手 `import psutil`），
    看门狗就再也救不了一个"连依赖都装不上"的环境 —— 而那正是它存在的唯一理由。
    """
    tree = ast.parse(open(_PROCPROBE_PY, encoding="utf-8").read())
    mods = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                mods.add(a.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.level:                       # 相对导入（本包内）也不许有：它要能被单独加载
                pytest.fail("procprobe 里出现了相对导入：level=%d module=%r" % (node.level, node.module))
            if node.module:
                mods.add(node.module.split(".")[0])
    third_party = sorted(m for m in mods if m not in sys.stdlib_module_names)
    assert not third_party, "procprobe 引入了非标准库依赖：%s" % third_party


# ── ② 真结果：进程表 / 端口 / pid 存活 / 结束进程 ────────────────────────────
class TestRealFacts:
    def test_backend_and_note_are_honest(self):
        b = procprobe.backend()
        assert b in ("windows", "procfs", "ps", "none")
        if os.name == "nt":
            assert b == "windows"
        elif sys.platform.startswith("linux"):
            assert b == "procfs", "Linux 上必须走 /proc（这就是'不再瞎'的本体）"
        note = procprobe.probe_note()
        assert note and isinstance(note, str)

    def test_sees_this_very_process(self):
        me = os.getpid()
        rows = list(procprobe.iter_processes())
        assert rows, "一只进程都没取到 —— 探测不可用"
        assert me in {int(r[0]) for r in rows}, "连自己都没取到（%d 不在 %d 条里）" % (me, len(rows))

    def test_cmdline_of_self_and_of_a_child(self):
        assert procprobe.process_cmdline(os.getpid()) != "", "取不到自己的命令行"
        p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        try:
            time.sleep(0.5)
            cl = procprobe.process_cmdline(p.pid)
            assert cl != "", "取不到自己起的子进程的命令行"
            assert "time.sleep" in cl, "命令行内容不对：%r" % cl
        finally:
            if p.poll() is None:
                p.kill()

    def test_finds_the_socket_we_open(self):
        """★真开一个监听端口问它 —— 旧实现（Linux 上恒空）会**当场变红**。"""
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            srv.bind(("127.0.0.1", 0))
            srv.listen(1)
            port = int(srv.getsockname()[1])
            assert procprobe.listening_pids(port) == [os.getpid()] or \
                os.getpid() in procprobe.listening_pids(port), \
                "自己进程在听这个端口，却没认出来：%r" % (procprobe.listening_pids(port),)
            assert port in procprobe.listening_ports(local_only=True), "自己开的端口不在监听列表里"
        finally:
            srv.close()
        for _ in range(10):
            if port not in procprobe.listening_ports():
                break
            time.sleep(0.1)
        assert port not in procprobe.listening_ports(), "端口已关，仍报在监听"

    def test_unused_port_has_no_listener(self):
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.bind(("127.0.0.1", 0))
        port = int(srv.getsockname()[1])
        srv.close()
        assert procprobe.listening_pids(port) == [], "没人听的端口不该报出 pid"

    def test_kill_pid_ends_a_real_child(self):
        p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        try:
            time.sleep(0.5)
            assert procprobe.is_pid_alive(p.pid) is True
            assert procprobe.kill_pid(p.pid) is True, "杀完必须返回「确实已不存活」"
            p.wait(timeout=10)
            assert procprobe.is_pid_alive(p.pid) is False
        finally:
            if p.poll() is None:
                p.kill()

    def test_kill_pid_tree_takes_the_children_too(self):
        """`tree=True` 必须**连子进程一起收掉**（看门狗拉起的 server 会有子进程）。

        判据：真起一个"父进程再起一个孙子"的进程，杀树之后**孙子也必须没了**
        （只看父进程死没死是不够的 —— 孤儿子进程会占着端口，正是看门狗要防的那种）。
        """
        code = ("import subprocess, sys, time\n"
                "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
                "time.sleep(60)\n")
        p = subprocess.Popen([sys.executable, "-c", code])
        try:
            time.sleep(1.2)
            kids = [r[0] for r in procprobe.iter_processes() if int(r[1]) == p.pid]
            if not kids:
                pytest.skip("本机取不到子进程（进程表不含 ppid 关系）：这条判据的前提不成立")
            assert procprobe.kill_pid(p.pid, tree=True) is True
            p.wait(timeout=10)
            for _ in range(20):
                if not any(procprobe.is_pid_alive(k) for k in kids):
                    break
                time.sleep(0.1)
            assert not any(procprobe.is_pid_alive(k) for k in kids), \
                "父进程死了但子进程还活着（孤儿子进程会继续占端口）：%r" % kids
        finally:
            if p.poll() is None:
                p.kill()


# ── ③ 看门狗真的用上了它（生产调用方式实跑）────────────────────────────────
class TestWatchdogUsesIt:
    def test_watchdog_loads_procprobe_standalone(self):
        """看门狗要能在**包不可导入**时单独跑 ⇒ 它是按文件路径加载 procprobe 的。"""
        assert wd.PROBE is not None, "看门狗没加载到 procprobe：%s" % wd.PROBE_NOTE
        assert wd.PROBE_NOTE == ""

    def test_watchdog_listener_lookup_matches_a_real_listener(self):
        """★真开一个监听，问**看门狗自己的** `_listener_pids()` —— 旧实现在 Linux 上恒空。"""
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            srv.bind(("127.0.0.1", 0))
            srv.listen(1)
            port = int(srv.getsockname()[1])
            pids = wd._listener_pids(port)
            assert os.getpid() in pids, "看门狗认不出真在监听的本进程：%r" % (pids,)
            assert wd._pid_alive(os.getpid()) is True
            assert wd._cmdline_of(os.getpid()) != ""
        finally:
            srv.close()

    def test_watchdog_check_ok_for_a_real_listener(self, tmp_path):
        """**按生产调用方式**跑一次：`--check --health tcp` 对一个真在听的端口必须 exit 0。

        这条故意起**子进程**（`python agent_community/tools/watchdog.py ...`），
        因为那正是部署脚本/人的用法：`sys.path[0]` 是脚本目录、包不在路径上 ——
        只有在那种条件下仍然答对，才算"看门狗本身能单独跑"。
        """
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            srv.bind(("127.0.0.1", 0))
            srv.listen(1)
            port = int(srv.getsockname()[1])
            env = {**os.environ, "AC_DATA_DIR": str(tmp_path), "PYTHONIOENCODING": "utf-8"}
            r = subprocess.run([sys.executable, _WATCHDOG_PY, "--check",
                                "--port", str(port), "--health", "tcp"],
                               capture_output=True, text=True, encoding="utf-8",
                               errors="replace", timeout=120, cwd=_REPO, env=env)
            assert r.returncode == 0, "看门狗对自己真在听的端口报了不健康：\n%s\n%s" % (r.stdout, r.stderr)
            assert "OK" in (r.stdout or ""), "输出里没有 OK：%r" % (r.stdout,)
            assert str(os.getpid()) in (r.stdout or ""), \
                "看门狗没认出监听者的 pid（Linux 上旧实现在这里是空的）：%r" % (r.stdout,)
        finally:
            srv.close()

    def test_watchdog_check_fails_for_a_dead_port(self, tmp_path):
        """反面：端口没人听必须 **exit 1**（不许"见谁都绿"）。"""
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.bind(("127.0.0.1", 0))
        port = int(srv.getsockname()[1])
        srv.close()
        env = {**os.environ, "AC_DATA_DIR": str(tmp_path), "PYTHONIOENCODING": "utf-8"}
        r = subprocess.run([sys.executable, _WATCHDOG_PY, "--check",
                            "--port", str(port), "--health", "tcp"],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=120, cwd=_REPO, env=env)
        assert r.returncode == 1, "没人听的端口却报健康：\n%s" % (r.stdout,)
        assert "FAIL" in (r.stdout or "")
