---
AIGC:
    Label: "1"
    ContentProducer: 001191440300708461136T1XGW3
    ProduceID: fee6cb6926e215051ee1772520098e59_98d4b50293b911f1bafa525400287e28
    ReservedCode1: flTvY1Hh5vovSbJGDlDFciYGkufafy05ZNEzsauEyspBlHgepM0yIgXFIDhnhg4pM3VqcgmpvfRnWxBneEVMSuE5/u+4UWDoJfCAlaOX7X+0oEKwJ+CilEv9g2h3is0Cmoj9ON6cvVSPFmWs9jpIridoBYMXrIGwIADcf1IbEXmMovS8juCLKHcucRA=
    ContentPropagator: 001191440300708461136T1XGW3
    PropagateID: fee6cb6926e215051ee1772520098e59_98d4b50293b911f1bafa525400287e28
    ReservedCode2: flTvY1Hh5vovSbJGDlDFciYGkufafy05ZNEzsauEyspBlHgepM0yIgXFIDhnhg4pM3VqcgmpvfRnWxBneEVMSuE5/u+4UWDoJfCAlaOX7X+0oEKwJ+CilEv9g2h3is0Cmoj9ON6cvVSPFmWs9jpIridoBYMXrIGwIADcf1IbEXmMovS8juCLKHcucRA=
---

# Harness 执行联动框架设计

> 日期：2026-08-09 | 状态：设计阶段
> 背景：agent-community-v4 讨论室提案通过后，缺少"让事情真正被做起来"的执行环节

---

## 1. 概述

### 1.1 问题

当前平台流程：

```
用户发需求 → Orchestrator 分析 → 讨论室协商 → 提交提案 → 投票通过 → [断点]
```

提案通过后状态变为 `delegating`，但没有真正派 Agent 执行。Harness 里有 4 个占位 Agent（code / research / writer / review），但提案中的分工从未触达它们。

### 1.2 目标

补上讨论室的"下半场"——提案通过后自动触发执行联动：

```
提案通过 → Orchestrator 拆 Task → 匹配 Agent → 串行执行 → 收结果 → 回讨论室
```

### 1.3 约束

- **不做 DAG 循环、多 Agent 路由、heartbeat、$ref**
- **只做单 Agent 串行**：`depends_on=[]` + `trigger=immediate`
- **不做新建代码框架**：在现有 server.py / orchestrator.py 基础上增补
- **充分利用已有设施**：`_ai_direct_response`、`harness_adapter`、现有代理执行路径

---

## 2. 架构

### 2.1 新增模块

```
agent_community/platform/
├── task_orchestrator.py   ← 新增：提案→Task拆解+执行编排
└── execution_monitor.py   ← 新增：单Task执行监控
```

### 2.2 角色关系

```
                         ┌──────────────────┐
   提案通过              │ Orchestrator     │
   (accepted)  ────────→ │ (已有)           │
                         │ 触发 task_orch   │
                         └────────┬─────────┘
                                  │
                    ┌─────────────▼─────────────┐
                    │ task_orchestrator.py       │
                    │                            │
                    │  1. 读提案 assignments      │
                    │  2. LLM 拆 Task（可选）     │
                    │  3. 匹配 Harness Agent      │
                    │  4. 串行派发执行            │
                    │  5. 收集结果 → 汇总        │
                    └─────────────┬─────────────┘
                                  │
              ┌───────────────────┼───────────────────┐
              ▼                   ▼                   ▼
     ┌─────────────┐     ┌─────────────┐     ┌─────────────┐
     │ har_agent0  │     │ har_agent1  │     │ har_agent2  │
     │ (code)      │     │ (research)  │     │ (writer)    │
     └─────────────┘     └─────────────┘     └─────────────┘
```

### 2.3 数据流

```
TaskProposal.assignments[]
  → TaskPlan[]  (LLM增强版：任务描述 → 可执行 prompt)
  → TaskExecution (逐个执行)
    → HarnessMessage(EXECUTE_TASK)  →  Harness Agent
    → 返回结果
  → ExecutionSummary (汇总)
  → RoomMessage(TYPE_RESULT)  →  讨论室
```

---

## 3. 核心设计

### 3.1 TaskPlan（执行计划）

```python
@dataclass
class TaskPlan:
    """单个执行步骤（比 DelegationRequest 更轻量、更面向 LLM 执行）"""
    id: str                    # plan-xxx
    agent_id: str              # 目标 Agent
    title: str                 # 步骤标题
    prompt: str                # 发给 Agent 的执行 prompt
    expected_output: str       # 期望产出描述
    depends_on: list[str]      # 依赖（当前始终 []）
    trigger: str               # 触发条件（始终 "immediate"）
    timeout_seconds: int       # 超时（默认 120）
    status: str                # pending / running / completed / failed
    result: Optional[str]      # 执行结果
    error: Optional[str]       # 错误信息
```

### 3.2 提案 → TaskPlan 转换

**简单模式**（不用 LLM）：
- 直接把 `assignment.description` 作为 `prompt`
- 把 `assignment.agent_id` 作为 `agent_id`

**AI 增强模式**（可选，复杂任务时启用）：
- 调用 AI 把 `task_description + discussion context` 拆为可执行步骤序列
- 好处：处理`"帮我写一个 Web 爬虫并生成报告"`这种多步骤任务

默认走**简单模式**，用户可选择开启 AI 增强。

### 3.3 Agent 匹配策略

```
assignment.agent_id  →  Harness Agent  lookup
        │
        ├─ 找到 → 直接使用
        ├─ 未找到 → capacity 模糊匹配
        └─ 无匹配 → Orchestrator 用 AI 直答兜底
```

### 3.4 串行执行引擎

```python
async def execute_sequential(task_plans, bridge):
    for plan in task_plans:
        plan.status = "running"
        # 通过 Harness Bridge 派发
        result = await bridge.execute_task(
            agent_id=plan.agent_id,
            prompt=plan.prompt,
            timeout=plan.timeout_seconds,
        )
        if result.ok:
            plan.status = "completed"
            plan.result = result.content
        else:
            plan.status = "failed"
            plan.error = result.error
    return task_plans
```

### 3.5 结果汇总

```python
def summarize(task_plans, room_context):
    """收集所有 TaskPlan 结果，生成讨论室消息"""
    parts = []
    for plan in task_plans:
        if plan.status == "completed":
            parts.append(f"## {plan.title}\n{plan.result[:500]}")
        else:
            parts.append(f"## {plan.title}\n[FAILED] {plan.error}")
    return "\n\n".join(parts)
```

---

## 4. 实施步骤

| 步骤 | 内容 | 影响文件 | 预估 |
|------|------|----------|------|
| **Step 1** | `protocol.py` 补 TaskPlan + ExecutionSummary 模型 | protocol.py | 小 |
| **Step 2** | 写 `task_orchestrator.py`：提案→TaskPlan 转换 + 串行执行 + 汇总 | task_orchestrator.py（新建） | 中 |
| **Step 3** | `harness_adapter.py` 补 `execute_task()` 方法 | harness_adapter.py | 小 |
| **Step 4** | `server.py` 在提案通过处挂入执行联动钩子 | server.py | 中 |
| **Step 5** | 端到端测试：用户发指令 → 提案通过 → Agent 执行 → 结果回讨论室 | 全链路 | 中 |

---

## 5. 关键代码变更

### 5.1 protocol.py 新增

```python
from dataclasses import dataclass, field
from enum import Enum

class TaskPlanStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"

@dataclass
class TaskPlan:
    id: str
    agent_id: str
    title: str
    prompt: str
    expected_output: str = ""
    depends_on: list = field(default_factory=list)
    trigger: str = "immediate"
    timeout_seconds: int = 120
    status: str = TaskPlanStatus.PENDING
    result: Optional[str] = None
    error: Optional[str] = None

@dataclass
class ExecutionReport:
    """一次执行联动的完整报告"""
    task_id: str
    room_id: str
    plans: list[TaskPlan]
    all_completed: bool
    completed_count: int
    failed_count: int
    summary: str
    started_at: str
    ended_at: str
```

### 5.2 server.py 挂载点

```python
# 在提案 accept 处理中新增：
if proposal.status == "accepted":
    room.status = DiscussionRoomStatus.DELEGATING
    # ← 新增：触发执行联动
    asyncio.create_task(
        _execute_proposal_tasks(room, proposal, task, harness_mgr)
    )
```

### 5.3 task_orchestrator.py 核心接口

```python
class TaskOrchestrator:
    async def execute_proposal(
        self,
        proposal: TaskProposal,
        room: DiscussionRoom,
        task: Task,
        harness_mgr,
        ai_provider=None,  # AI 增强模式可选
    ) -> ExecutionReport:
        """提案执行入口：拆 Task → 执行 → 汇总 → 回讨论室"""
        ...
```

---

## 6. 与原流程的关系

| 环节 | 原有 | 新增 |
|------|------|------|
| 提案通过 | → delegating，发 system 消息 | → 触发 `TaskOrchestrator.execute_proposal()` |
| DelegationRequest | 通过讨论室协商生成 | **保留不动**，TaskPlan 是新增的轻量执行视图 |
| _execute_harness_delegation | 已有，但从未被触发 | TaskOrchestrator 复用其 bridge 通信链路 |
| 结果回讨论室 | 无 | 发 RoomMessage 回对应讨论室 |

---

## 7. 后续扩展（本次不做）

- [ ] 并行执行（depends_on 拓扑排序）
- [ ] 执行超时重试
- [ ] AI 增强模式（LLM 拆任务）
- [ ] 执行日志持久化
- [ ] TaskPlan 与 DelegationRequest 合二为一（设计债）
*（内容由AI生成，仅供参考）*
