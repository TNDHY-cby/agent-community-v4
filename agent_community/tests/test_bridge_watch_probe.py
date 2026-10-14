# -*- coding: utf-8 -*-
r"""★「该不该起桥」的判据必须准 —— **误判"已有桥" = 静默不启动**。

**为什么这个判据最危险**（2026-10-12）：`auto-connect` 用 `_probe_pid_watching(inbox)`
判断"是不是已经有桥在看着这个邮箱"，**若误判为"有"就会跳过起桥** ⇒
**桥根本没起、界面却看着正常** —— 本项目最怕的死状态。
原判据有三个毛病（同一家族，我这轮连续栽过）：
  ① **不检查是不是桥**：任何命令行里提到该邮箱路径的进程（测试脚本、`python -c`）
     都会被当成"已有桥在看着"；
  ② **不认解释器**：powershell/cmd 包裹进程的命令行同样含路径；
  ③ 与 `bridge_instances`（`bridges_running` 的计数）**两套口径**，迟早打架。

**不对称性（判据设计原则，值得记）**：
  · **少认**（把真桥漏了）→ 会再起一只 → **单实例锁会拦下并说明理由**（可见、可恢复）；
  · **多认**（把别人当桥） → **静默不启动** → 用户无从发现。
  ⇒ 宁可少认也不能多认。

⚠️**测试设计的两个坑（都实测踩到过）**：
  1. `cmd /c` + 嵌套双引号会把路径连引号一起传给程序 ⇒ 报
     `'…\python.exe' 不是内部或外部命令`，**进程根本没起来** —— 而断言 `== 0` **照样通过**，
     **空转还不报警**。⇒ 每个用例都必须**先断言进程真的活着**。
  2. `_probe_pids_watching` 排除 `ParentProcessId == 本进程`（生产里本进程是平台，
     排除的是"探测自己起的子进程"）；测试里直接 Popen 的子进程父进程就是本进程 ⇒ 被排除、空转。
     ⇒ 调用前把"本进程 pid"临时伪装成不存在的 pid，模拟"桥是**外部**启动的"。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
import uuid

import pytest

# ★2026-10-14：**撤掉"只在 Windows 上跑"的 skip** —— 这才是把探测换成 psutil 的真收益。
#   原先探测只有 PowerShell 一条路 ⇒ Linux 上恒返回 0：
#     · 「认不出来」类断言在 Linux 上**恒真 = 假绿**（恰好等于期望值）；
#     · 「认得出来」那条则必红。
#   ⇒ 上一轮只能整文件 `skipif(platform != "win32")`（= Linux 上这套判据**没人守**）。
#   现在取进程表走 `_process_rows()`（psutil 主路径，PowerShell 只作无 psutil 时的回落）
#   ⇒ 两个平台都能真跑，**这些用例在两边都必须通过**（不再有 skip）。
#   判据（`_match_bridges`）另有不依赖真进程的合成行用例，见本文件末尾。
PY = sys.executable
EX = os.path.join("agent_community", "examples")


def _probe_as_if_external(monkeypatch, fn, *args):
    """调用判据，但**把"本进程 pid"伪装成不存在的 pid** —— 模拟"桥是外部启动的"。

    （`_probe_pids_watching` 会排除 `ParentProcessId == 本进程`；生产里本进程是平台，
      排除掉的是"探测动作自己起的子进程"。测试直接 Popen 的子进程父进程正是本进程，
      不伪装就会被排除 ⇒ 测试空转。）
    """
    with monkeypatch.context() as m:
        m.setattr(os, "getpid", lambda: 1)
        return fn(*args)


@pytest.fixture()
def inbox(tmp_path):
    d = tmp_path / ("inbox_" + uuid.uuid4().hex[:6])
    d.mkdir(parents=True, exist_ok=True)
    yield str(d)
    shutil.rmtree(d, ignore_errors=True)


def test_non_bridge_process_mentioning_inbox_is_not_a_bridge(inbox, tmp_path, monkeypatch):
    """★① 一个 `python -c` 只是**提到**该邮箱路径 —— 不算桥（原判据会误判成"已有桥"）。"""
    from agent_community.platform.routers.harness_messaging import _probe_pid_watching

    # ⚠️必须**单行**（带 \n 会让 cmd 断行）；且命令行里必须真的出现该邮箱路径
    code = ("import time; print('i am not a bridge'); time.sleep(60)  # inbox=%s" % inbox)
    p = subprocess.Popen([PY, "-c", code],
                         stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    try:
        time.sleep(1.5)
        # ★先证明进程真的活着（否则断言 == 0 是空转通过）
        assert p.poll() is None, "被测进程没起来，本用例就空转了（退出码 %s）" % p.poll()
        got = _probe_as_if_external(monkeypatch, _probe_pid_watching, inbox)
        assert got == 0, (
            "★把一个只是提到邮箱路径的 `python -c` 当成了桥 ⇒ auto-connect 会"
            "**跳过起桥**、静默失败（原判据的毛病：不检查是不是桥）")
    finally:
        if p.poll() is None:
            p.terminate()
            try:
                p.wait(timeout=8)
            except Exception:  # noqa: BLE001
                p.kill()


def test_wrapper_process_mentioning_inbox_is_not_a_bridge(inbox, tmp_path, monkeypatch):
    """★② **包裹进程**的命令行里也有该邮箱路径 —— 不算桥（要认解释器）。

    ⚠️ 2026-10-14 修（Linux CI 的第二个拦路虎）：
      原实现写死 `["powershell", ...]`。**Linux 上没有 powershell** ⇒
      `subprocess.Popen` 直接 `FileNotFoundError` ⇒ 本用例在 `test-linux` 作业里必红。
      现在**按平台**挑一个"非解释器的外壳进程"：
        Windows → `powershell -Command "Start-Sleep 60 # <inbox>"`
        POSIX   → `sh -c "sleep 60 # <inbox>"`
      两者语义完全相同（一个命令行里含邮箱路径、但不是 Python 解释器），
      被测的判据（"只认解释器"）两边都验得到。
    """
    from agent_community.platform.routers.harness_messaging import _probe_pid_watching

    if sys.platform == "win32":
        argv = ["powershell", "-NoProfile", "-Command",
                "Start-Sleep -Seconds 60  # %s" % inbox]
        who = "powershell"
    else:
        argv = ["sh", "-c", "sleep 60  # %s" % inbox]
        who = "sh"

    p = subprocess.Popen(argv, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    try:
        time.sleep(2.0)
        assert p.poll() is None, "被测 %s 没起来，本用例空转了（退出码 %s）" % (who, p.poll())
        got = _probe_as_if_external(monkeypatch, _probe_pid_watching, inbox)
        assert got == 0, "★把 %s 包裹进程当成了桥（判据没限定只认解释器）" % who
    finally:
        if p.poll() is None:
            p.terminate()
            try:
                p.wait(timeout=8)
            except Exception:  # noqa: BLE001
                p.kill()


def test_real_bridge_watching_the_inbox_is_detected(inbox, tmp_path, monkeypatch):
    """★③ 反向：**真的**有桥在看着这个邮箱时，必须**认得出来**（否则会重复起桥）。"""
    from agent_community.platform.routers.harness_messaging import _probe_pid_watching

    script = os.path.join(EX, "filepoll_harness_bridge.py")
    assert os.path.isfile(script), "桥脚本不存在：%s" % script
    # ⚠️用**相对**脚本路径：桥命令行里要出现该邮箱路径（`--inbox`），
    #   且命令行不能因路径含非 ASCII 而出现编码差异。
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    p = subprocess.Popen([PY, "-u", script,
                          "--harness-id", "watch-" + uuid.uuid4().hex[:6],
                          "--inbox", inbox, "--platform", "http://127.0.0.1:18920"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT,
                         env=env, cwd=os.getcwd())
    try:
        time.sleep(3.0)
        assert p.poll() is None, "桥没起来（退出码 %s），本用例空转" % p.poll()
        got = _probe_as_if_external(monkeypatch, _probe_pid_watching, inbox)
        assert got != 0, (
            "★真桥在跑却认不出来 ⇒ auto-connect 会**再起一只**（会被单实例锁拦下，"
            "用户看到'拒绝重复启动' —— 能恢复，但判据本身不准）")
    finally:
        if p.poll() is None:
            p.terminate()
            try:
                p.wait(timeout=8)
            except Exception:  # noqa: BLE001
                p.kill()


# ── 判据本身（`_match_bridges`）：合成进程表 ⇒ **不依赖真进程、两个平台都覆盖** ──────
#
# 为什么要单独测判据（2026-10-14，把探测换成 psutil 时补）：
#   · 上面三条用例要真起进程，慢、且受平台影响；
#   · 而"哪一行算桥"这件事本身是**纯判断**，可以喂合成行 ——
#     这样 **POSIX 上进程名是 `python3.12`** 这类只在 Linux 出现的形态，在开发机（Windows）
#     上也能被守住（否则又变成"只有 CI 才验得到"）。

def _row(pid, ppid, name, cmdline):
    return (pid, ppid, name, cmdline)


def test_match_bridges_accepts_a_real_bridge_on_either_platform():
    """真桥的两种"进程名形态"都要认：Windows `python.exe` / POSIX `python3.12`。"""
    from agent_community.platform.routers.harness_messaging import _match_bridges

    inbox = "/tmp/inbox_abc"
    rows = [
        _row(11, 1, "python.exe", "python -u bridge.py --inbox /tmp/inbox_abc"),
        _row(12, 1, "python3.12", "python3 -u /x/bridge.py --inbox /tmp/inbox_abc"),
    ]
    got = _match_bridges(rows, inbox, me=999)
    assert [h["pid"] for h in got] == [11, 12], (
        "★进程名只认 `python*` 前缀：POSIX 上是 `python3.12`，写死 `python.exe` 就又成了"
        "「只有 Windows 能过」的判据：%r" % got)


def test_match_bridges_rejects_wrappers_and_mere_mentions():
    """只提到线索、或被外壳进程包着 —— 都不算桥（多认的后果是**静默不启动**）。"""
    from agent_community.platform.routers.harness_messaging import _match_bridges

    inbox = "/tmp/inbox_abc"
    rows = [
        # 外壳进程（它的命令行里也有脚本路径 + 邮箱）
        _row(21, 1, "powershell.exe", "powershell -Command ... bridge.py ... /tmp/inbox_abc"),
        _row(22, 1, "sh", "sh -c 'python bridge.py --inbox /tmp/inbox_abc'"),
        # 只是"提到"线索，但根本没在跑桥脚本
        _row(23, 1, "python3.12", "python3 -c import time; # /tmp/inbox_abc"),
        # 命令行里连 bridge.py 都没有
        _row(24, 1, "python.exe", "python -m http.server --directory /tmp/inbox_abc"),
    ]
    assert _match_bridges(rows, inbox, me=999) == [], "多认 ⇒ auto-connect 静默不起桥"


def test_match_bridges_excludes_the_probe_process_own_children():
    """`ppid == 本进程` 必须排除（生产里本进程=平台，排除的是"探测动作自己起的子进程"）。"""
    from agent_community.platform.routers.harness_messaging import _match_bridges

    inbox = "/tmp/inbox_abc"
    rows = [_row(31, 4242, "python.exe", "python bridge.py --inbox /tmp/inbox_abc")]
    assert _match_bridges(rows, inbox, me=4242) == [], "自己的子进程不该算成桥"
    assert len(_match_bridges(rows, inbox, me=1)) == 1, "父不是本进程时应当认出来"


def test_match_bridges_tries_the_forward_slash_variant_and_ignores_empty_needle():
    """线索**原样**用 + 额外试正斜杠变体；空线索直接返回空（不许拿空串匹配一切）。"""
    from agent_community.platform.routers.harness_messaging import _match_bridges

    win_inbox = r"D:\mail\box1"
    rows = [_row(41, 1, "python.exe", "python bridge.py --inbox D:/mail/box1")]
    assert [h["pid"] for h in _match_bridges(rows, win_inbox, me=999)] == [41], \
        "进程命令行里出现的是正斜杠变体，也要能命中"
    assert _match_bridges(rows, "", me=999) == [], "空线索不许匹配一切"
    assert _match_bridges(rows, "   ", me=999) == [], "空白线索同上"
