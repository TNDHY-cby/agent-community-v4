# -*- coding: utf-8 -*-
"""★桥脚本的**单实例保护**：同一 harness / 同一 inbox 只允许一只桥在跑。

**为什么必须有用例**（2026-10-12 真机实测）：
  平台能给用户**报告**"这个 harness 名下有 N 只在跑桥"，但**拦不住**。
  实测：一次后台启动就出现 2 个活的 `bridge.py` 进程，而两个桥脚本
  （`pending_poll_bridge.py` / `filepoll_harness_bridge.py`）**毫无单实例保护**
  ⇒ 同一批件被两只桥抢领 ⇒ **重复投递 / 丢件 / 心跳互相覆盖**，
  而用户只会看到"有时重复、有时没人取"的玄学现象。

**本用例验的是行为（真起进程），不是"代码里有这几行"** —— 这点很重要：
  我第一版实现（`msvcrt.locking` 前**忘了 `seek(0)`**）在源码上看着完全正确，
  实测却**完全不拦第二只**（追加模式让两只桥锁在**不同字节**上）。
  只做源码断言的测试**永远发现不了这个**。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
import uuid

import pytest

EX = os.path.join("agent_community", "examples")


def _spawn(bridge: str, *extra, work_dir: str, harness_id: str):
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    return subprocess.Popen(
        [sys.executable, "-u", os.path.join(EX, bridge)] + list(extra) + [
            "--harness-id", harness_id, "--interval", "2"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        encoding="utf-8", errors="replace", env=env)


@pytest.fixture()
def workdir(tmp_path):
    d = tmp_path / ("lock_" + uuid.uuid4().hex[:6])
    d.mkdir(parents=True, exist_ok=True)
    yield d
    shutil.rmtree(d, ignore_errors=True)


def test_pending_poll_bridge_refuses_second_instance(workdir):
    """★第二只桥必须**拒绝启动**（退出码非 0 + 说清理由），而不是照样跑起来。"""
    hid = "lock-" + uuid.uuid4().hex[:6]
    first = second = third = None
    try:
        first = _spawn("pending_poll_bridge.py", "--url", "http://127.0.0.1:18920",
                       "--work-dir", str(workdir), work_dir=str(workdir), harness_id=hid)
        time.sleep(3.5)
        assert first.poll() is None, "第一只桥没跑起来（锁实现把它自己拦了？）"

        second = _spawn("pending_poll_bridge.py", "--url", "http://127.0.0.1:18920",
                        "--work-dir", str(workdir), work_dir=str(workdir), harness_id=hid)
        try:
            rc = second.wait(timeout=12)
            out = (second.stdout.read() or "") if second.stdout else ""
        except subprocess.TimeoutExpired:
            rc, out = None, ""
        assert rc is not None, \
            "★第二只桥**没有被拦**（两条桥会抢领同一批件 ⇒ 重复投递）；" \
            "实测这个坑来自 msvcrt.locking 前忘了 seek(0)（追加模式让两只锁在不同字节上）"
        assert rc != 0, "第二只桥应当以非 0 退出码拒绝启动，实得 %r" % rc
        assert ("拒绝重复启动" in out) or ("只应有一只桥" in out), \
            "拒绝时要说清理由（别让用户以为是别的问题）：%s" % out.strip()[-200:]

        assert first.poll() is None, "第一只桥不该被第二只影响"
    finally:
        for p in (first, second, third):
            if p and p.poll() is None:
                p.terminate()
                try:
                    p.wait(timeout=8)
                except Exception:  # noqa: BLE001
                    p.kill()


def test_lock_is_released_when_the_first_bridge_exits(workdir):
    """★反向：前一只退出后，锁必须**自动释放**（不留僵尸锁）。

    这正是"用持锁而不是 pid 文件"的理由：pid 文件在崩溃时会留僵尸锁、
    且 pid 会被系统复用 ⇒ 判错就会误判"已在运行"。
    """
    hid = "lock2-" + uuid.uuid4().hex[:6]
    first = later = None
    try:
        first = _spawn("pending_poll_bridge.py", "--url", "http://127.0.0.1:18920",
                       "--work-dir", str(workdir), work_dir=str(workdir), harness_id=hid)
        time.sleep(3.5)
        assert first.poll() is None
        first.terminate()
        first.wait(timeout=10)
        time.sleep(1.0)

        later = _spawn("pending_poll_bridge.py", "--url", "http://127.0.0.1:18920",
                       "--work-dir", str(workdir), work_dir=str(workdir), harness_id=hid)
        time.sleep(3.5)
        assert later.poll() is None, \
            "★前一只退出后新桥起不来 ⇒ 留下了**僵尸锁**（持锁方案本不该有这个问题）"
    finally:
        for p in (first, later):
            if p and p.poll() is None:
                p.terminate()
                try:
                    p.wait(timeout=8)
                except Exception:  # noqa: BLE001
                    p.kill()


def test_both_bridge_scripts_carry_the_guard_and_seek_first():
    """源码级补充断言：两个桥都带守卫，且**取锁前先 seek(0)**。

    （行为用例已覆盖真实效果；这条只是让"退回错误实现"时**一眼看到原因**。）
    """
    for f in ("pending_poll_bridge.py", "filepoll_harness_bridge.py"):
        src = open(os.path.join(EX, f), encoding="utf-8").read()
        assert "acquire_single_instance" in src, "%s 缺单实例守卫" % f
        assert "import os" in src, "%s 用了 os 但没 import（运行时才炸）" % f
        code = "\n".join(l for l in src.split("\n") if not l.lstrip().startswith("#"))
        i_lock = code.find("locking(")
        i_seek = code.find("f.seek(0)")
        assert i_seek != -1 and i_lock != -1 and i_seek < i_lock, \
            "%s：**取锁前必须 f.seek(0)**（追加模式的指针在 EOF，会让两只桥锁在不同字节上、毫无冲突）" % f


# ══════════════════════════════════════════════════════════════════════════
# ★2026-10-12 补：**平台模板渲染出来的桥**也必须带锁
#
# 为什么补这一组：上面两条只覆盖 `examples/` 里**手写的**两只桥。
# 而用户真正会跑的那只，是 `POST …/bridge/generate` **用模板渲染**出来的 ——
# 实测（渲染 + 真起两只进程）发现 **三个 L1 模板一个都没有锁**，
# 而对外文档/话术已经写着"桥脚本自带单实例锁" ⇒ **文档比代码先说了话**（谎）。
# 只测 examples 的用例**永远不会发现**这件事 ⇒ 判据必须覆盖"用户实际拿到的那只"。
# ══════════════════════════════════════════════════════════════════════════
TMPL_ROOT = os.path.join("agent_community", "bridge_templates")
L1_TEMPLATES = ("file_poll", "pending_poll", "cli_acp")


def _render_params(work: str) -> dict:
    return {
        "HARNESS_ID": "tmpl-lock", "HARNESS_NAME": "tmpl-lock",
        "PLATFORM_URL": "http://127.0.0.1:18920",
        "TRIGGER_CMD": "", "INBOX_DIR": os.path.join(work, "inbox"), "WORK_DIR": work,
        "ACP_COMMAND": "example --acp", "ACP_CWD": work,
        "MODEL_NAME": "example-model", "PROVIDER": "example-provider",
        "DESCRIPTION": "template lock check",
    }


def test_all_l1_templates_carry_the_guard_and_seek_first():
    """三个 L1 模板都必须带锁，且取锁前先 seek(0)（源码级）。"""
    for kind in L1_TEMPLATES:
        p = os.path.join(TMPL_ROOT, kind, "bridge.py.tmpl")
        assert os.path.exists(p), "模板不存在: %s" % p
        src = open(p, encoding="utf-8").read()
        assert "acquire_single_instance" in src and ".bridge.lock" in src, \
            "★模板 %s 没有单实例锁 ⇒ 用户 generate 出来的桥**毫无保护**" % kind
        assert "import os" in src, "模板 %s 用了 os 却没 import（跑起来才炸）" % kind
        code = "\n".join(l for l in src.split("\n") if not l.lstrip().startswith("#"))
        i_lock, i_seek = code.find("locking("), code.find("f.seek(0)")
        assert i_seek != -1 and i_lock != -1 and i_seek < i_lock, \
            "模板 %s：取锁前必须 f.seek(0)（否则两只桥锁在不同字节上、毫无冲突）" % kind


def test_rendered_bridge_refuses_second_instance(workdir):
    """★行为验证：**渲染出来的**桥（用户实际拿到的那只）必须拒绝第二只。

    只做源码断言挡不住"看着对、其实不拦"（第一版就是这么挂的）。
    """
    from agent_community.platform.bridge_factory import generate  # noqa: PLC0415

    hid = "tmpl-lock-" + uuid.uuid4().hex[:6]
    work = os.path.join(str(workdir), "w")
    os.makedirs(work, exist_ok=True)
    bridge = generate("pending_poll", _render_params(work), workdir)
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    cmd = [sys.executable, "-u", str(bridge), "--harness-id", hid,
           "--work-dir", work, "--interval", "2"]
    first = second = None
    try:
        first = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                 text=True, encoding="utf-8", errors="replace", env=env)
        time.sleep(3.5)
        assert first.poll() is None, "渲染出来的桥第一只就没跑起来（锁把自己拦了？）"
        second = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                  text=True, encoding="utf-8", errors="replace", env=env)
        try:
            rc = second.wait(timeout=12)
            out = (second.stdout.read() or "") if second.stdout else ""
        except subprocess.TimeoutExpired:
            rc, out = None, ""
        assert rc is not None and rc != 0, \
            "★模板渲染出来的桥**没有拦第二只**（用户照话术 generate 出来的桥就没有保护）"
        assert "拒绝重复启动" in out, "拒绝时要说清理由：%s" % out.strip()[-200:]
        assert first.poll() is None, "第一只不该被第二只影响"
    finally:
        for p in (first, second):
            if p and p.poll() is None:
                p.terminate()
                try:
                    p.wait(timeout=8)
                except Exception:  # noqa: BLE001
                    p.kill()
