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
    """★② powershell **包裹进程**的命令行里也有该邮箱路径 —— 不算桥（要认解释器）。"""
    from agent_community.platform.routers.harness_messaging import _probe_pid_watching

    p = subprocess.Popen(["powershell", "-NoProfile", "-Command",
                          "Start-Sleep -Seconds 60  # %s" % inbox],
                         stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    try:
        time.sleep(2.0)
        assert p.poll() is None, "被测 powershell 没起来，本用例空转了（退出码 %s）" % p.poll()
        got = _probe_as_if_external(monkeypatch, _probe_pid_watching, inbox)
        assert got == 0, "★把 powershell 包裹进程当成了桥（判据没限定只认解释器）"
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
