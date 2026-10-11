# -*- coding: utf-8 -*-
"""FE-17 §4：右侧栏「按阶段收敛」的权威判据（`GET /api/workshop/{ws}/sidebar`）。

**契约要点**（老板 2026-10-10 真机测试后的要求，FE-17 §1/§2）：
- 任何时刻"当前该按的"主操作 **≤5**（实测该页原有 58 个不同按钮，老板"不知道怎么按"）；
- 阶段**只由后端 status 决定**，界面不得另算一套（防两套口径漂移）；
- 阶段专属且用不上的按钮**不渲染**（不是置灰 —— 置灰正是"按不了"的来源）；
- **失败要说话**：异常状态给出原因与下一步，不许只给一个灰按钮。

本文件是**纯单测**（不依赖在跑的服务）；端到端由 `temp\\_e2e_sidebar.py` 在真实例上跑。
"""
from __future__ import annotations

from agent_community.platform.routers import workshops_lifecycle as wl


def test_every_stage_has_at_most_five_primary_actions():
    """★核心契约：每个阶段的主操作 ≤5（FE-17 §2 的可验收目标）。"""
    for stage, (primary, more) in wl._FE17_BY_STAGE.items():
        assert len(primary) <= 5, f"阶段 {stage} 主操作 {len(primary)} 个 > 5"


def test_all_referenced_action_ids_are_defined():
    """阶段表引用的动作 id 必须都在动作表里有定义（否则界面拿到空按钮）。"""
    for stage, (primary, more) in wl._FE17_BY_STAGE.items():
        for aid in list(primary) + list(more):
            assert aid in wl._FE17_ACTIONS, f"阶段 {stage} 引用了未定义动作 {aid}"


def test_every_action_states_what_it_does():
    """FE-17 §2：每个动作必须写清"按了会发生什么"（尤其破坏性的）。"""
    for aid, a in wl._FE17_ACTIONS.items():
        assert a.get("label"), f"{aid} 缺 label"
        assert a.get("effects"), f"{aid} 缺 effects（用户看不到按了会发生什么）"
        assert a.get("endpoint"), f"{aid} 缺 endpoint"


def test_stage_ids_and_status_mapping_are_consistent():
    """status→stage 映射的目标阶段必须都在阶段表里定义。"""
    for status, stage in wl._FE17_STATUS_TO_STAGE.items():
        assert stage in wl._FE17_STAGES, f"status={status} 映射到未定义阶段 {stage}"
        assert stage in wl._FE17_BY_STAGE, f"阶段 {stage} 没有按钮白名单"


def test_done_stage_offers_no_primary_action():
    """终态只让看，不引导乱按（老板口径：完成后不该再有一排按钮）。"""
    primary, _ = wl._FE17_BY_STAGE["done"]
    assert primary == [], f"done 阶段不应有主操作，实得 {primary}"


def test_abnormal_statuses_have_explanation():
    """异常状态必须有解释（端点在 blocked/dropped/pending_approval 时给 blocked_reason）。"""
    import inspect
    src = inspect.getsource(wl.workshop_sidebar)
    for st in ("blocked", "dropped", "pending_approval"):
        assert st in src, f"端点应对 {st} 给出说明"
    assert "blocked_reason" in src


def test_sidebar_endpoint_is_registered_and_readonly():
    """端点已挂载，且是 GET（只读）—— 不得用写方法。"""
    routes = [r for r in wl.router.routes
              if getattr(r, "path", "") == "/api/workshop/{ws_id}/sidebar"]
    assert routes, "sidebar 端点未挂载"
    assert all("GET" in getattr(r, "methods", set()) for r in routes), "sidebar 必须是只读 GET"
    src = __import__("inspect").getsource(wl.workshop_sidebar)
    # 无副作用：不得在这个只读端点里落盘/改状态
    for forbidden in ("save_state(", "ws.status =", "write_text"):
        assert forbidden not in src, f"只读端点不得出现 {forbidden}"
