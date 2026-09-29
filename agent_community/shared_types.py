"""
agent_community 共享类型定义 — Single Source of Truth

所有协议消息类型的规范定义。SDK（sdk.py）和 Server（platform/protocol.py）
均从此模块导入，确保类型一致性，杜绝分裂漂移。

protocol.py 中的 Pydantic BaseModel 子类字段定义应与本模块保持同步；
如字段需要 Pydantic 专属特性（validator / Field 约束），在 protocol.py 中覆写。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class SubTaskAssignment:
    """提案中给某个 Agent 分配的子任务"""
    agent_id: str
    task_title: str
    task_description: str
    capability_required: str
    expected_output: str = ""
    priority: int = 0
    estimated_duration: str = ""


@dataclass
class DelegationRequest:
    """Agent 间委托请求"""
    id: str = ""
    from_agent: str = ""
    to_agent: str = ""
    capability_required: str = ""
    task_id: str = ""
    title: str = ""
    description: str = ""
    depends_on: list[str] = field(default_factory=list)
    deadline: Optional[str] = None
    priority: int = 0
    created_at: str = ""


@dataclass
class ReviewRequest:
    """收敛驱动审查循环——发起审查请求"""
    request_id: str = ""
    task_id: str = ""
    delegation_id: str = ""
    from_agent: str = ""
    to_agent: str = ""
    target_type: str = "execution_result"
    content: str = ""
    expected_capability: str = ""
    timeout_seconds: int = 30
    created_at: str = ""
