"""platform/state.py 模块级单元测试。

验证 V-9 拆分后共享状态单例：19 个状态名齐全、类型/默认值正确，
保证 server 双副本与 routers 引用同一组对象（消除 ALLOWED_TOKENS 分裂类问题）。
"""
import pytest

from agent_community.platform.state import (
    tasks,
    discussion_rooms,
    agents,
    assistant_history,
    assistant_history_max,
    workshops,
    interject_store,
    task_state_machine,
    pending_activations,
    pending_tasks,
    pending_bridge_tests,
    _bridge_tests_inflight,
    _wakeup_inflight,
    _WAKEUP_TTL,
    _BRIDGE_TEST_TTL,
    _offline_redispatch,
    _r2_handled_at,
    _R2_HANDLE_GAP,
    pending_pre_register,
)

EXPECTED_STATE_NAMES = [
    "tasks",
    "discussion_rooms",
    "agents",
    "assistant_history",
    "assistant_history_max",
    "workshops",
    "interject_store",
    "task_state_machine",
    "pending_activations",
    "pending_tasks",
    "pending_bridge_tests",
    "_bridge_tests_inflight",
    "_wakeup_inflight",
    "_WAKEUP_TTL",
    "_BRIDGE_TEST_TTL",
    "_offline_redispatch",
    "_r2_handled_at",
    "_R2_HANDLE_GAP",
    "pending_pre_register",
]


def test_shared_state_names_complete():
    import agent_community.platform.state as st

    assert len(EXPECTED_STATE_NAMES) == 19
    missing = [n for n in EXPECTED_STATE_NAMES if not hasattr(st, n)]
    assert not missing, f"缺失共享状态名: {missing}"


def test_state_containers():
    import agent_community.platform.state as st

    for name in [
        "tasks",
        "discussion_rooms",
        "agents",
        "workshops",
        "pending_activations",
        "pending_tasks",
        "pending_bridge_tests",
        "_bridge_tests_inflight",
        "_wakeup_inflight",
        "_offline_redispatch",
        "_r2_handled_at",
        "pending_pre_register",
    ]:
        assert isinstance(getattr(st, name), dict), f"{name} 应为 dict"
    assert isinstance(st.assistant_history, list)


def test_state_defaults():
    assert assistant_history_max == 60
    assert _WAKEUP_TTL == 1800.0
    assert _BRIDGE_TEST_TTL == 600.0
    assert _R2_HANDLE_GAP == 300.0


def test_state_singletons():
    from agent_community.platform.interject_store import InterjectStore
    from agent_community.platform.task_state_machine import TaskStateMachine

    assert isinstance(interject_store, InterjectStore)
    assert isinstance(task_state_machine, TaskStateMachine)
