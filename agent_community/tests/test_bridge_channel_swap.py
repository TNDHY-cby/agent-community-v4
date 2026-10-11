# -*- coding: utf-8 -*-
"""V36 §3.3：换通道时**桥要跟着换**（停用后不许再显示"已架接"；新建后要绑到新会话）。

老板场景（V28 §1）：「用户不满意 / agent 上下文过长老化 ⇒ 换成员的对话」。
本文件锁三条：

1. **停用通道** ⇒ `bridge_ref.state` 变 `retired`（旧会话已断，界面不许再冒充"已架接"）；
   但**保留**历史字段（模板名 / 旧会话名 / 停用时间），可回溯 —— 停用≠删除；
2. **新建通道** ⇒ 按同一模板**重新架桥**并绑到**新会话**；
3. **失败要说话**：原先没架过桥的成员，如实回 `attempted=False`，**不假装重架成功**。

端到端那条由 `temp\\_e2e_b3_channel_bridge.py` 在真实例上跑（含换通道全程）。
"""
from __future__ import annotations

import inspect

from agent_community.platform.routers import workshops_session as wsess


def test_retire_marks_bridge_retired_keeps_history():
    """停用通道必须把桥标成 retired，并保留可回溯的信息（源码级断言）。

    为什么用源码断言：`retire_channel(ws, m)` 需要真实 WorkshopMember + 磁盘归档，
    纯单测里起全套不划算；**行为**由端到端用例覆盖（实测 state: attached → retired）。
    这里只锁"这段代码存在且语义写对"，防止以后有人重构时把它删掉。
    """
    src = inspect.getsource(wsess.retire_channel)
    assert 'bridge_ref' in src, "停用通道时没有处理 bridge_ref（旧会话断了却仍显示已架接）"
    assert '"retired"' in src or "'retired'" in src, "应把 state 置为 retired"
    for keep in ("retired_at", "retired_session_name", "retired_channel_seq"):
        assert keep in src, f"应保留可回溯字段 {keep}（停用≠删除）"
    # 断了就不该再声称自证过
    assert '"verified"] = False' in src or '["verified"] = False' in src


def test_establish_reattaches_bridge():
    """新建通道必须重架桥（否则新会话没有桥、收不到活 —— V28 §2 规则 4）。"""
    src = inspect.getsource(wsess.establish_channel)
    assert "attach_bridge_core" in src, "换通道后没有重架桥"
    assert "do_establish=False" in src, "重架时不应再建一次会话（会话已由 establish_one 建好）"
    assert "bridge_reattach" in src, "重架结果必须如实返回（失败要说话）"


def test_attach_core_exists_before_callers():
    """`attach_bridge_core` 必须定义在调用方之前（模块加载期解析函数名不影响，
    但它必须是模块级可复用的、而不是端点内联的一段）。"""
    assert hasattr(wsess, "attach_bridge_core"), "架接核心未抽成模块级函数"
    src = inspect.getsource(wsess.attach_bridge_core)
    # 核心不认识 HTTP：错误用 code 表达，由端点映射
    assert '"code"' in src, "核心应回 code 供端点映射 HTTP 状态"
    # 失败不许写 attached
    assert '"state": "attached" if ok else "none"' in src or 'state": "attached" if ok else "none"' in src


def test_attach_endpoint_delegates_to_core():
    """端点应是薄壳：委托给核心，且仍保留路径字段拦截（安全口径不能因重构丢失）。"""
    src = inspect.getsource(wsess.member_bridge_attach)
    assert "attach_bridge_core" in src, "端点没有委托给核心"
    for banned in ("out_dir", "target_dir", "path", "dir", "workspace_dir", "bridge_path"):
        assert banned in src, f"端点应显式拦截路径字段 {banned}"
