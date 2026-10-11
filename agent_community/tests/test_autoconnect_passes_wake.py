# -*- coding: utf-8 -*-
"""auto-connect 必须把「叫醒命令」传给桥（否则"平台自动起桥"那条路唤不醒 agent）。

**为什么写这条**（2026-10-11）：老板的 agent 唤不醒，根因是桥的触发命令为空。
V38 第 2 步加了 `POST /api/harness/{hid}/wake` 让用户配；**但光配不传等于没配** ——
`auto-connect` 起桥时必须把包装脚本路径当 `--trigger` 传进去。

⚠️ 我写这段时**连栽两次**（都因为猜变量名）：
   ① 用了 auto-connect 的局部变量 `_pkg`（模块级不存在）；
   ② 又猜 `os` / `_os` 哪个在模块级（实测两个都不是）。
   ⇒ 两次都被 `try/except` 吞掉 ⇒ **trigger 永远为空、静默失效**（最坏的那种 bug）。
   所以这里做**行为级**断言：真配一条命令，看 helper 能不能取到那条路径。
"""
from __future__ import annotations

import inspect
import os

from agent_community.platform import bridge_wake as bw
from agent_community.platform.routers import harness_messaging as hm


def _repo_root_of_helper() -> str:
    """helper 内部按 `__file__` 推算 <repo>；测试也用同一算法，保证指向同一处。"""
    return os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(hm.__file__)))))


def test_wake_trigger_reads_configured_wrapper():
    """★行为级：配过 ⇒ 取到**存在的绝对路径**；没配 ⇒ 空串（且不抛异常）。"""
    hid = "回归探针-wake"
    r = bw.write_wrapper(_repo_root_of_helper(), hid, "cmd /c echo hi")
    assert r["ok"] is True, r
    try:
        got = hm._wake_trigger_for(hid)
        assert got == r["path"], "helper 必须取到刚写的包装脚本路径"
        assert os.path.isabs(got) and os.path.isfile(got)
        assert hm._wake_trigger_for("从来没配过的harness") == "", "没配 ⇒ 空串"
    finally:
        try:
            os.remove(r["path"])
        except OSError:
            pass


def test_auto_connect_passes_trigger_to_bridge():
    """源码级：**两条**分支（file_poll 与兜底 pending_poll）都要传 `--trigger`。

    只传一条 ⇒ 走另一条路的用户仍唤不醒（等于没修）。
    """
    src = inspect.getsource(hm.harness_auto_connect)
    assert "_wake_trigger_for(hid)" in src, "auto-connect 没有取叫醒命令"
    assert src.count('"--trigger"') >= 2, \
        "file_poll 与 pending_poll 两条分支都应传 --trigger"


def test_helper_never_raises_on_broken_input():
    """容错：拿不到就叫醒不了，但**不能让 auto-connect 崩**。"""
    assert hm._wake_trigger_for("") == ""
    assert hm._wake_trigger_for("../../怪名字") == ""
