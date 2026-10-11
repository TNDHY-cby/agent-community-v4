# -*- coding: utf-8 -*-
"""2026-10-11 实测抓到的**三个接入阻断缺陷**（都让"照模板做"必然失败）。

**背景**：老板按新话术把模板发给外端 agent，agent 回"它什么接口都没有"。
逐项核查发现**三处真缺陷**，且都不是 agent 的问题：

1. **`auto-connect` 对 acp / http 直接 500**
   `UnboundLocalError: cannot access local variable '_already_pid'`
   —— 我前一天加"已有桥就别重复起"时，把 `_already_pid = …` 只写进了 FILE_POLL 分支，
   却在函数末尾共用 ⇒ 走 acp / http 等分支时变量不存在 ⇒ **500**。

2. **`wakeup_method=mcp` 注册必然 500**
   `NameError: name '_maybe_pull_mcp_tools' is not defined`
   —— "半截功能"：注释写着"动态拉取并持久化"，**函数从来没被写过**。

3. **模板把"桥型名"当 `wakeup_method` 填**
   模板给 A~E 的映射写的是 `cli_acp` / `pending_poll`，而 `HarnessInfo.wakeup_method`
   只认 `http / file_poll / clipboard / acp / http_api / mcp` ⇒ 照填**注册被 400 拒**。

★**测试设计说明（重要）**：这三条**都用"不触发落盘"的方式测** ——
  ① 源码顺序（静态）；
  ② `_maybe_pull_mcp_tools` 是**纯函数**，可直接喂它各种坏输入；
  ③ 词汇表（纯常量）。
  为什么不调生产入口：`save_state()` 会写 `agent_community/data/*`，
  即使某一项序列化失败、其余项也照写（实测踩到，被 `guard_real_data_dir` 拦下）。
  **测分支逻辑不该有任何落盘** —— 这也是 conftest 那条守卫存在的意义。
"""
from __future__ import annotations

import asyncio
import inspect

import pytest

from agent_community.platform.protocol import HarnessInfo, WakeupMethod
from agent_community.platform.routers import harness_messaging as hm
from agent_community.platform.routers import harness_register as hr


def run(c):
    return asyncio.run(c)


# ── 缺陷 1：跨分支共用的局部变量必须预先声明（源码级，零副作用）───────────────
def test_already_pid_is_initialized_before_branches():
    """★`_already_pid` 的初始化必须出现在**第一个分支之前**。

    否则：FILE_POLL 之外的任何分支走到函数末尾都会 `UnboundLocalError` ⇒ 接口 500。
    （实测复现过：acp / http 都 500。）
    """
    src = inspect.getsource(hm.harness_auto_connect)
    i_init = src.find("_already_pid = 0")
    i_first_branch = src.find("if wm == WakeupMethod.HTTP_API")
    assert i_init > 0, "没有在分支之前初始化 `_already_pid`"
    assert i_first_branch > 0, "找不到第一个分支（函数结构变了？请看源码再改本测试）"
    assert i_init < i_first_branch, \
        "`_already_pid` 又在分支里赋值了 —— 走别的分支会 UnboundLocalError（实测 500）"


def test_already_pid_keeps_a_default_value():
    """反向守卫：必须保留**默认值初始化**那一处。

    ⚠️ 曾经的写法是"只在 FILE_POLL 分支里赋值" ⇒ 其他分支 UnboundLocalError（实测 500）。
    正解是**两处**：分支前给默认值 + 分支里取真实 pid。
    （我一度断言"只许一处赋值"，被源码纠正 —— 断言要写事实，不要写我以为的。）
    """
    src = inspect.getsource(hm.harness_auto_connect)
    assert "_already_pid = 0" in src, \
        "缺少默认值初始化；只有分支内赋值的话，acp/http 等分支又会 UnboundLocalError"


# ── 缺陷 2：`_maybe_pull_mcp_tools` 必须存在，且失败不许崩（纯函数）──────────
def test_mcp_tool_puller_exists_and_is_async():
    """★这个函数曾经**完全不存在** ⇒ 任何 mcp 注册都 500。"""
    assert hasattr(hr, "_maybe_pull_mcp_tools"), \
        "register_harness 会 await 它；不存在则 mcp 注册必 500（实测过）"
    assert inspect.iscoroutinefunction(hr._maybe_pull_mcp_tools), "它必须是 async（调用处 await）"


@pytest.mark.parametrize("case,url", [
    ("没有 api_base_url", ""),
    ("URL 非法", "不是个网址"),
    ("不可达（拉取必失败）", "http://127.0.0.1:9/mcp"),   # 9/tcp 基本必然拒连
])
def test_mcp_tool_puller_degrades_without_crashing(case, url):
    """拉不到就**原样返回 + 打日志**，绝不让注册 500（失败要说话，不是要崩）。"""
    info = HarnessInfo(harness_id="mcp-degrade", harness_name="降级探针",
                       harness_type="ide-agent", wakeup_method=WakeupMethod.MCP,
                       api_base_url=url, acp_command="probe.exe --x",
                       ai={"model_name": "unknown"})
    out = run(hr._maybe_pull_mcp_tools(info))
    assert out is info, f"{case}: 应原样返回同一个 info 对象"
    assert isinstance(getattr(info, "tools", None), list), f"{case}: tools 应保持为列表"


def test_mcp_tool_puller_survives_broken_client(monkeypatch):
    """连 MCP 客户端本身出问题也要降级，不许把注册带崩。"""
    def _boom(*a, **k):
        raise RuntimeError("客户端炸了")

    import agent_community.platform.mcp_client as mc
    monkeypatch.setattr(mc, "validate_mcp_url", lambda url: (True, ""), raising=False)
    monkeypatch.setattr(mc, "mcp_list_tools", _boom, raising=False)
    info = HarnessInfo(harness_id="mcp-boom", harness_name="炸探针",
                       harness_type="ide-agent", wakeup_method=WakeupMethod.MCP,
                       api_base_url="http://127.0.0.1:9/mcp", acp_command="probe.exe --x",
                       ai={"model_name": "unknown"})
    out = run(hr._maybe_pull_mcp_tools(info))
    assert out is info, "客户端异常时必须原样返回（注册继续、工具表留空）"


# ── 缺陷 3：词汇表钉死（纯常量）─────────────────────────────────────────────
def test_wakeup_method_vocabulary_is_pinned():
    """`wakeup_method` 只认这 6 个；桥型名（cli_acp/pending_poll/script_framework）**不是**它。

    模板曾把 A~E 映射成桥型名 ⇒ 外端 agent 照填**注册被 400 拒**
    （报错只说 "Input should be ..."，不告诉你桥型该放哪）。
    """
    legal = {m.value for m in WakeupMethod}
    assert legal == {"http", "file_poll", "clipboard", "acp", "http_api", "mcp"}, legal
    for wrong in ("cli_acp", "pending_poll", "script_framework"):
        assert wrong not in legal, f"{wrong} 是桥型名，不该被当成 wakeup_method（模板曾这么写 ⇒ 400）"


def test_bridge_kinds_are_a_separate_vocabulary():
    """桥型与 `wakeup_method` 是**两套词**，但**不是完全不相交** —— 要说准：

    · 共有：`file_poll`（桥型 file_poll ↔ wakeup_method file_poll，**同义**，正常）；
    · **只属于桥型**：`cli_acp` / `pending_poll` / `script_framework`
      —— 这三个是模板曾错填进 `wakeup_method` 的那批，**必须不在合法值里**。
    （我第一版写成"两套词无交集"，被实测纠正：file_poll 是共有的。
      教训同上：断言要写**事实**，不要写我以为的。）
    """
    from agent_community.platform import bridge_catalog as bc
    kinds = {k["kind"] for k in bc.list_kinds()}
    legal = {m.value for m in WakeupMethod}
    assert "cli_acp" in kinds and "pending_poll" in kinds and "script_framework" in kinds
    for only_kind in ("cli_acp", "pending_poll", "script_framework"):
        assert only_kind not in legal, \
            f"{only_kind} 是桥型名、不是 wakeup_method —— 模板曾混用，导致注册被 400 拒"
    assert "file_poll" in kinds and "file_poll" in legal, \
        "file_poll 两套词都有（同义共用），这一点也要锁住，免得以后有人误以为它只在一边"
