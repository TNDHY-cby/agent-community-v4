# -*- coding: utf-8 -*-
"""V41 P3：**"叫醒命令"只能有一处真相**（否则用户配了却不生效）。

**实测过的分叉**（2026-10-11，真机）：
    两处存
      ① 通用桥模板的 `params.TRIGGER_CMD` —— 用户在注册页填的**原始命令**
      ② harness `metadata.wake_wrapper`      —— **包装脚本路径**（桥实际用的是这个）
    只填 ① ⇒ `GET /api/harness/{hid}/wake` 报 `configured=False`、
    桥启动时**拿不到 trigger** ⇒ 用户填了，agent 依然**不会被叫醒**，
    而界面还会明说"未配：桥只会把消息落盘"——自相矛盾。

**修法**：保存模板这个入口**把 ① 自动落到 ②**（同一处真相），用户只填一次。
本文件锁三件事：
1. 模板带 TRIGGER_CMD ⇒ **wake 必须变成已配**，且命令逐字一致；
2. 同步失败**要如实回报**（不许静默）；
3. 模板**没填** TRIGGER_CMD ⇒ 不许凭空造一个 wake 配置。
"""
from __future__ import annotations

import asyncio

import pytest

from agent_community.platform.routers import harness_bridge as hb


class _Info:
    def __init__(self):
        self.metadata = {}


class _Sess:
    def __init__(self):
        self.info = _Info()


class _Req:
    def __init__(self, body):
        self._body = body

    async def json(self):
        return self._body


def run(c):
    return asyncio.run(c)


@pytest.fixture()
def sess(tmp_path, monkeypatch):
    s = _Sess()
    monkeypatch.setattr(hb.harness_manager, "sessions", {"h1": s}, raising=False)
    # 包装脚本写到 tmp，别污染真实 data/runtime
    import agent_community.platform.bridge_wake as bw
    monkeypatch.setattr(bw, "_runtime_root", lambda root: str(tmp_path), raising=False)
    import agent_community.platform.server as srv
    monkeypatch.setattr(srv, "save_state", lambda: None, raising=False)
    return s


def test_trigger_cmd_in_template_becomes_the_one_effective_wake(sess, tmp_path):
    """★核心：模板里填了 TRIGGER_CMD ⇒ 桥真正用的那处（wake_wrapper）必须同步。"""
    res = run(hb.harness_bridge_template_put("h1", _Req({
        "name": "P3桥", "kind": "file_poll",
        "params": {"INBOX_DIR": r"D:\x", "TRIGGER_CMD": "probe.exe --wake"},
    })))
    synced = res.get("wake_synced") or {}
    assert synced.get("path"), f"TRIGGER_CMD 没有同步成包装脚本：{res.get('wake_note')!r}"
    assert synced.get("command") == "probe.exe --wake", synced
    # 关键：harness metadata 里那处（桥实际读的）也必须写上
    got = (sess.info.metadata or {}).get("wake_wrapper") or {}
    assert got.get("path") == synced.get("path")
    assert got.get("command") == "probe.exe --wake"
    assert got.get("source") == "bridge_template.TRIGGER_CMD", "要标出来源，便于回溯为什么配上了"
    assert "已同步" in (res.get("wake_note") or ""), res.get("wake_note")


def test_no_trigger_cmd_does_not_invent_a_wake(sess):
    """反向断言：模板没填 TRIGGER_CMD ⇒ **不许**凭空造一个 wake 配置。"""
    res = run(hb.harness_bridge_template_put("h1", _Req({
        "name": "无叫醒桥", "kind": "file_poll", "params": {"INBOX_DIR": r"D:\x"},
    })))
    assert res.get("wake_synced") == {}
    assert not ((sess.info.metadata or {}).get("wake_wrapper")), "没填却写了 wake 配置 = 假状态"
    assert not res.get("wake_note"), "没填就不该出现同步说明"


def test_sync_failure_is_reported_not_silent(sess, monkeypatch):
    """同步失败**要说话**（准则⑤）：不能静默吞掉，否则又是"看起来配好了"。"""
    import agent_community.platform.bridge_wake as bw
    monkeypatch.setattr(bw, "write_wrapper", lambda *a, **k: {"ok": False, "error": "磁盘写不进去",
                                                              "fix": "检查权限"}, raising=False)
    res = run(hb.harness_bridge_template_put("h1", _Req({
        "name": "P3桥", "kind": "file_poll",
        "params": {"INBOX_DIR": r"D:\x", "TRIGGER_CMD": "probe.exe --wake"},
    })))
    # 存模板本身仍然成功（不因同步失败而丢用户输入），但要把失败如实说出来
    assert res.get("success") is True
    assert res.get("wake_synced", {}).get("error"), "同步失败必须回报"
    assert "同步失败" in (res.get("wake_note") or ""), res.get("wake_note")
