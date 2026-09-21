"""Agent 协作协议 v3.1 — 讨论室机制

设计原则：
1. Agent 自主选择接入方式（多型 Transport）
2. Agent 间通过结构化契约协作，而非自然语言广播
3. 能力不足时内建委托机制，Agent 必须主动寻人
4. 广播后拉入讨论室协商分工，达成共识后再各自执行
"""

from __future__ import annotations
from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field
from datetime import datetime
from uuid import uuid4


# ── Transport（多型通用）───────────────────────────────────────

class TransportType(str, Enum):
    WS = "ws"
    HTTP = "http"
    SSE = "sse"
    PIPE = "pipe"
    GRPC = "grpc"


# ── 消息类型 ──────────────────────────────────────────────────

class MessageType(str, Enum):
    CHAT = "chat"
    DELEGATION = "delegation"
    DELEGATION_ACCEPT = "delegation_accept"
    DELEGATION_REJECT = "delegation_reject"
    PROGRESS = "progress"
    RESULT = "result"
    REVIEW = "review"
    QUERY = "query"
    EVENT = "event"
    SYSTEM = "system"

    # ── 讨论室消息类型（v3.1 新增）─────────────────────────
    ROOM_CREATED = "room_created"           # 讨论室已创建，邀请 Agent 加入
    ROOM_JOIN = "room_join"                 # Agent 加入讨论室
    ROOM_LEAVE = "room_leave"               # Agent 离开
    ROOM_MESSAGE = "room_message"           # 普通讨论发言
    PROPOSAL_SUBMIT = "proposal_submit"     # 提交分工提案
    PROPOSAL_AMEND = "proposal_amend"        # 修改/反驳提案
    VOTE_CAST = "vote_cast"                 # 投票
    CONSENSUS_REACHED = "consensus_reached"  # 达成共识
    CONSENSUS_FAILED = "consensus_failed"    # 未达成共识
    ROOM_EVENT = "room_event"               # 讨论室通用事件


# ── 统一 API 响应信封 ─────────────────────────────────────────

class APIError(BaseModel):
    code: str
    message: str

class APIResponse(BaseModel):
    ok: bool
    data: Optional[dict] = None
    error: Optional[APIError] = None


# ── Agent 身份卡（多型 Transport）─────────────────────────────

class AgentEndpoint(BaseModel):
    transport: TransportType
    url: str
    priority: int = 0
    metadata: dict = Field(default_factory=dict)

class LifecycleMode(str, Enum):
    """Agent 生命周期模式（v4 MVP 新增）"""
    STATELESS = "stateless"   # 无状态：每次调用独立，无需维持会话
    STATEFUL = "stateful"     # 有状态：维持长连接会话，需心跳保活


class AgentCard(BaseModel):
    agent_id: str
    name: str
    version: str = "1.0"
    lifecycle_mode: LifecycleMode = LifecycleMode.STATELESS  # v4 新增
    endpoints: list[AgentEndpoint]
    capabilities: list[str] = Field(default_factory=list)
    description: str = ""
    software: dict = Field(default_factory=dict)
    max_delegations: int = 3
    registered_at: str = Field(default_factory=lambda: datetime.now().isoformat())


# ── 广播举手 ──────────────────────────────────────────────────

class BroadcastHandRaise(BaseModel):
    """Agent 在收到广播任务后举手声明参与意愿"""
    task_id: str
    agent_id: str
    agent_name: str
    capability_claim: str                # 声称自己能贡献的能力
    proposed_role: str = ""              # 提议自己承担的角色
    confidence: float = 1.0              # 自信度 0~1
    raised_at: str = Field(default_factory=lambda: datetime.now().isoformat())


# ── 讨论室（v3.1 核心新增）────────────────────────────────────

class DiscussionRoomStatus(str, Enum):
    FORMING = "forming"             # 正在拉人进讨论室
    NEGOTIATING = "negotiating"     # 协商分工中
    VOTING = "voting"               # 投票中
    CONSENSUS = "consensus"         # 已达成共识
    DELEGATING = "delegating"       # 共识落地为委托，各自执行
    DISSOLVED = "dissolved"         # 讨论室解散
    DEADLOCK = "deadlock"           # 僵局（协商失败）


class DiscMessageType(str, Enum):
    """讨论室内消息子类型"""
    SPEAK = "speak"                 # 普通发言
    PROPOSE = "propose"             # 提出分工提案
    AMEND = "amend"                 # 修改提案
    OBJECT = "object"               # 反对/反驳
    CLARIFY = "clarify"             # 请求澄清
    VOTE = "vote"                   # 投票表态


class DiscussionMessage(BaseModel):
    """讨论室内的一条消息"""
    id: str = Field(default_factory=lambda: uuid4().hex[:12])
    room_id: str
    agent_id: str
    agent_name: str
    msg_type: DiscMessageType = DiscMessageType.SPEAK
    content: str
    ref_proposal_id: str = ""           # 引用的提案 ID（修正/反驳时用）
    timestamp: str = Field(default_factory=lambda: datetime.now().isoformat())


class TaskProposal(BaseModel):
    """分工提案：谁做什么、依赖关系"""
    proposal_id: str = Field(default_factory=lambda: uuid4().hex[:12])
    room_id: str
    proposed_by: str                    # 提案者 agent_id
    title: str                          # 提案标题
    description: str = ""               # 总体思路说明

    # 分工表: agent_id → SubTaskAssignment
    assignments: dict[str, SubTaskAssignment] = Field(default_factory=dict)

    # 执行顺序依赖: (agent_a, agent_b) 表示 a 必须在 b 之前完成
    dependencies: list[list[str]] = Field(default_factory=list)

    status: str = "proposed"            # proposed / amended / accepted / rejected
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now().isoformat())


class SubTaskAssignment(BaseModel):
    """提案中给某个 Agent 分配的子任务
    
    字段定义与 agent_community.types.SubTaskAssignment 保持同步 — Single Source of Truth。
    """
    agent_id: str
    task_title: str
    task_description: str
    capability_required: str            # 所需能力
    expected_output: str = ""           # 期望输出格式描述
    priority: int = 0
    estimated_duration: str = ""        # 预估耗时


class Agreement(BaseModel):
    """投票/共识结果"""
    agreement_id: str = Field(default_factory=lambda: uuid4().hex[:12])
    room_id: str
    proposal_id: str                    # 最终被接受的提案
    votes: dict[str, str] = Field(default_factory=dict)   # agent_id → "approve" / "reject" / "abstain"
    vote_count: int = 0
    approve_count: int = 0
    reject_count: int = 0
    status: str = "pending"             # pending / approved / rejected
    finalized_at: str = ""


class DiscussionRoom(BaseModel):
    """讨论室"""
    room_id: str = Field(default_factory=lambda: uuid4().hex[:8])
    task_id: str                        # 所属任务
    task_title: str = ""
    task_description: str = ""

    # 举手进入讨论室的 Agent
    participants: list[str] = Field(default_factory=list)   # agent_id 列表

    # 讨论历史
    messages: list[DiscussionMessage] = Field(default_factory=list)

    # 分工提案列表
    proposals: list[TaskProposal] = Field(default_factory=list)

    # 共识
    agreement: Optional[Agreement] = None

    # 共识落地后的委托列表
    delegation_chain: list[DelegationRequest] = Field(default_factory=list)

    status: DiscussionRoomStatus = DiscussionRoomStatus.FORMING
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat())
    resolved_at: str = ""


# ── 结构化委托契约 ────────────────────────────────────────────

class InputSchema(BaseModel):
    content_type: str = "text"
    data: dict = Field(default_factory=dict)
    files: list[str] = Field(default_factory=list)

class OutputSchema(BaseModel):
    content_type: str = "text"
    required_fields: list[str] = Field(default_factory=list)
    max_length: Optional[int] = None

class DelegationRequest(BaseModel):
    """Agent 间委托请求。

    字段定义与 agent_community.types.DelegationRequest 保持同步 — Single Source of Truth。
    """
    id: str = Field(default_factory=lambda: uuid4().hex[:12])
    from_agent: str
    to_agent: str
    capability_required: str
    task_id: str
    title: str
    description: str
    input: InputSchema = Field(default_factory=InputSchema)
    expected_output: OutputSchema = Field(default_factory=OutputSchema)
    depends_on: list[str] = Field(default_factory=list)  # 依赖的其他 delegation id
    deadline: Optional[str] = None
    priority: int = 0
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat())

class DelegationResponse(BaseModel):
    delegation_id: str
    from_agent: str
    to_agent: str
    accepted: bool
    reason: str = ""
    estimated_cost: str = ""
    need_clarification: bool = False
    questions: list[str] = Field(default_factory=list)


# ── 进度与结果汇报 ─────────────────────────────────────────────

class ProgressReport(BaseModel):
    delegation_id: str
    from_agent: str
    to_agent: str
    percent: float = 0.0
    message: str = ""
    timestamp: str = Field(default_factory=lambda: datetime.now().isoformat())

class ExecutionResult(BaseModel):
    delegation_id: str
    from_agent: str
    to_agent: str
    ok: bool
    content: str
    structured_data: dict = Field(default_factory=dict)
    artifacts: list[str] = Field(default_factory=list)
    error: Optional[str] = None
    confidence: float = 1.0
    duration_ms: int = 0
    timestamp: str = Field(default_factory=lambda: datetime.now().isoformat())

class ReviewResult(BaseModel):
    delegation_id: str
    from_agent: str
    to_agent: str
    accepted: bool
    feedback: str = ""
    score: float = 0.0


# ── 消息信封 ──────────────────────────────────────────────────

class Message(BaseModel):
    id: str = Field(default_factory=lambda: uuid4().hex[:12])
    type: MessageType = MessageType.CHAT
    from_agent: str = "unknown"
    to_agent: str = "broadcast"
    task_id: Optional[str] = None
    room_id: Optional[str] = None       # v3.1 新增：关联讨论室
    delegation_id: Optional[str] = None
    content: str = ""
    payload: dict = Field(default_factory=dict)
    timestamp: str = Field(default_factory=lambda: datetime.now().isoformat())


# ── 任务状态机 ─────────────────────────────────────────────────

class TaskStatus(str, Enum):
    CREATED = "created"
    BROADCASTING = "broadcasting"       # 正在广播，等待举手
    IN_DISCUSSION = "in_discussion"     # 讨论室协商中
    DELEGATING = "delegating"
    EXECUTING = "executing"
    COMPLETED = "completed"
    FAILED = "failed"

class Task(BaseModel):
    id: str = Field(default_factory=lambda: uuid4().hex[:8])
    title: str
    description: str
    status: TaskStatus = TaskStatus.CREATED
    created_by: str = "user"
    room_id: str = ""                   # v3.1 新增：关联讨论室
    hand_raises: list[BroadcastHandRaise] = Field(default_factory=list)
    messages: list[Message] = Field(default_factory=list)
    delegations: list[DelegationRequest] = Field(default_factory=list)
    delegation_results: dict[str, ExecutionResult] = Field(default_factory=dict)
    result: Optional[str] = None
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now().isoformat())
    completed_at: Optional[str] = None


# ── 能力查询 ──────────────────────────────────────────────────

class CapabilityQuery(BaseModel):
    from_agent: str
    capability: str
    min_count: int = 1
    exclude: list[str] = Field(default_factory=list)
    prefer_transports: list[TransportType] = Field(default_factory=list)

class CapabilityResult(BaseModel):
    capability: str
    agents: list[AgentCard]
    count: int


# ── Pipe 消息格式 ─────────────────────────────────────────────

class PipeRequest(BaseModel):
    message_id: str
    message_type: str
    task_id: str = ""
    room_id: str = ""                   # v3.1 新增
    payload: dict = Field(default_factory=dict)

class PipeResponse(BaseModel):
    request_id: str
    from_agent: str
    message_type: str
    content: str = ""
    payload: dict = Field(default_factory=dict)
    ok: bool = True
    error: Optional[str] = None


# ═══════════════════════════════════════════════════════════════
# Harness 外部接入协议（v3.2 新增）
# ═══════════════════════════════════════════════════════════════

class HarnessTool(BaseModel):
    """Harness 声明的一个工具"""
    name: str
    description: str = ""
    parameters: dict = Field(default_factory=dict)  # JSON Schema
    capability_tag: str = ""      # 映射到平台能力标签 (coding / file_ops / browser ...)


class HarnessAI(BaseModel):
    """Harness 内封装的 AI 模型信息"""
    model_name: str               # 模型名（如 claude-sonnet-4 / qwen3-vl-8b）
    provider: str = ""            # 提供商（anthropic / openai / ollama / ...）
    version: str = ""
    capabilities: list[str] = Field(default_factory=list)  # 平台能力标签
    max_tokens: int = 8192
    can_see: bool = False         # 有无视觉能力
    can_code: bool = False
    can_browse: bool = False
    can_file_ops: bool = False
    description: str = ""


class AIProviderType(str, Enum):
    """AI Provider 类型（v6 新增 — 通用 AI 接入层）"""
    OPENAI = "openai"                    # OpenAI 兼容 API（DeepSeek / OpenAI / Claude / Gemini 等）
    OLLAMA = "ollama"                    # 本地 Ollama
    HTTP_CALLBACK = "http_callback"      # HTTP 回调方式


class AIProviderConfig(BaseModel):
    """AI Provider 配置（v6 新增）"""
    type: AIProviderType = AIProviderType.OPENAI
    base_url: str = "https://api.deepseek.com"
    api_key: str = ""
    model: str = "deepseek-chat"
    callback_url: str = ""               # HTTP 回调地址（http_callback 类型专用）


class WakeupMethod(str, Enum):
    """Harness 接入/激活方式（平台如何拉起该 harness 的新对话）"""
    HTTP = "http"            # HTTP 回调：平台 POST 到 callback_url/wakeup_url
    FILE_POLL = "file_poll"  # 文件轮询：写入 wakeup_dir，Harness 侧脚本检测
    CLIPBOARD = "clipboard"  # 剪贴板桥接：写入剪贴板，用户手动粘贴到 Harness 会话
    ACP = "acp"              # ACP（Agent Client Protocol）：平台 spawn 子进程走 JSON-RPC stdio
    HTTP_API = "http_api"    # HTTP API：平台 POST 到 harness 自带的 HTTP API（如示例 3721 /message），harness 自主处理


class WakeupMessage(BaseModel):
    """平台对 Harness 发送的唤醒消息"""
    id: str = Field(default_factory=lambda: uuid4().hex[:12])
    task_id: str
    task_title: str
    task_description: str = ""
    participants_count: int = 0          # 已举手人数
    participants: list[str] = Field(default_factory=list)
    required_capabilities: list[str] = Field(default_factory=list)
    sent_at: str = Field(default_factory=lambda: datetime.now().isoformat())
    wakeup_method: WakeupMethod = WakeupMethod.CLIPBOARD


class HarnessInfo(BaseModel):
    """Harness 向平台声明的自身信息"""
    harness_id: str               # 唯一标识，如 "harness-example-001"
    harness_name: str             # 人类可读名，如 "Trae Work CN"
    harness_version: str = "1.0"
    harness_type: str = ""        # ide-agent / ollama-wrapper / api-proxy / custom

    # 接入方式（声明自己能用的 transport）
    transports: list[TransportType] = Field(default_factory=lambda: [TransportType.HTTP])
    callback_url: str = ""        # HTTP 回调地址（如果有）

    # ── 接入/激活配置（平台如何拉起该 harness 的新对话）────────
    wakeup_method: WakeupMethod = WakeupMethod.CLIPBOARD   # 接入/激活方式
    wakeup_url: str = ""           # HTTP 回调激活地址
    wakeup_dir: str = ""           # 文件轮询监听目录（如 Trae CN 的轮询目录）
    acp_command: str = ""          # ACP 激活命令（如 dsh：node --import tsx ... acp-demo ...）
    acp_cwd: str = ""              # ACP 命令的工作目录

    # ── HTTP API 唤醒（http_api 类，如 harness 自带 HTTP API）──
    api_base_url: str = ""         # harness 自带 HTTP API 地址，如 http://127.0.0.1:3721
    api_message_path: str = ""     # 消息推送端点，如 /message
    api_outbox_dir: str = ""       # 回报目录（harness 处理后写回报的目录）

    # ── 喊人专员声明（v5 新增）──────────────────────────────
    can_wake: bool = False                     # 声明"我能当喊人专员"
    waking_models: list[str] = Field(default_factory=list)   # 我有的 AI 模型名称
    waking_protocols: list[str] = Field(default_factory=list)  # 支持的通信协议: http_callback / websocket / file_poll

    # ── 桥坐标与测试状态（平台侧登记）────────────────────────
    # 对象建好桥 → 平台测试桥功能 → 测试通过后对象告知桥文件路径 → 平台记录到这里并持久化。
    bridge_dir: str = ""        # 桥文件所在文件夹路径（对象告知的"桥坐标"）
    bridge_status: str = ""     # 桥状态：""未登记 / "reported"已告知路径 / "tested"测试通过

    # 封装的 AI
    ai: HarnessAI = Field(default_factory=HarnessAI)

    # 可用工具
    tools: list[HarnessTool] = Field(default_factory=list)

    # 元信息
    description: str = ""
    metadata: dict = Field(default_factory=dict)


class HarnessStatus(str, Enum):
    ONLINE = "online"
    OFFLINE = "offline"
    BUSY = "busy"
    ERROR = "error"


class HarnessSession(BaseModel):
    """平台侧记录的 Harness 会话"""
    harness_id: str
    info: HarnessInfo
    agent_id: str = ""            # 映射到的平台 Agent ID
    status: HarnessStatus = HarnessStatus.ONLINE
    transport: TransportType = TransportType.HTTP
    metadata: dict = Field(default_factory=dict)
    last_heartbeat: str = Field(default_factory=lambda: datetime.now().isoformat())
    registered_at: str = Field(default_factory=lambda: datetime.now().isoformat())
    message_count: int = 0


class HarnessMessageType(str, Enum):
    """Harness ↔ 平台 桥接消息类型（类型安全枚举）"""
    CHAT = "chat"
    BROADCAST = "broadcast"
    DELEGATION = "delegation"
    EXECUTE = "execute"
    REVIEW = "review"
    RESULT = "result"
    PROGRESS = "progress"
    HEARTBEAT = "heartbeat"
    ROOM_MESSAGE = "room_message"
    CANCEL = "cancel"
    ERROR = "error"
    STATUS_QUERY = "status_query"
    STATUS_REPORT = "status_report"
    INVITE = "invite"
    INVITE_REPLY = "invite_reply"
    REVIEW_REPLY = "review_reply"


class HarnessMessage(BaseModel):
    """Harness ↔ 平台 之间的桥接消息"""
    id: str = Field(default_factory=lambda: uuid4().hex[:12])
    harness_id: str
    direction: str = "to_harness"                      # to_harness / from_harness
    msg_type: HarnessMessageType = HarnessMessageType.CHAT
    content: str = ""
    task_id: str = ""
    room_id: str = ""
    delegation_id: str = ""
    payload: dict = Field(default_factory=dict)
    timestamp: str = Field(default_factory=lambda: datetime.now().isoformat())


# ═══════════════════════════════════════════════════════════════
# v4 MVP 新增：审查、执行上下文、记忆系统
# ═══════════════════════════════════════════════════════════════

class ReviewRequest(BaseModel):
    """收敛驱动审查循环——发起审查请求
    
    字段定义与 agent_community.types.ReviewRequest 保持同步 — Single Source of Truth。
    """
    request_id: str = Field(default_factory=lambda: uuid4().hex[:12])
    task_id: str
    delegation_id: str
    from_agent: str                      # 审查发起方
    to_agent: str                        # 被审查方
    target_type: str = "execution_result" # execution_result / proposal / plan
    content: str                         # 待审查内容
    expected_capability: str = ""        # 期望的能力标签（用于匹配审查者）
    timeout_seconds: int = 30            # 审查超时
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat())


class ReviewResponse(BaseModel):
    """审查结果"""
    request_id: str
    from_agent: str
    verdict: str = "pass"               # pass / fail / amend
    feedback: str = ""
    score: float = 0.0                  # 0.0~1.0
    amendments: list[str] = Field(default_factory=list)
    timestamp: str = Field(default_factory=lambda: datetime.now().isoformat())


class ExecutionContext(BaseModel):
    """执行上下文——注入 Harness 时的完整背景信息"""
    task_id: str
    task_title: str
    task_description: str = ""
    delegation_id: str = ""
    parent_outputs: dict[str, str] = Field(default_factory=dict)
    # ↑ key=dependency_delegation_id, value=前序Agent的输出摘要

    dependency_graph: list[list[str]] = Field(default_factory=list)
    # ↑ 简化依赖图: [["delegation_a","delegation_b"], ["delegation_c"]] 表示拓扑层

    global_constraints: list[str] = Field(default_factory=list)
    # ↑ 全局约束（如"所有输出使用英文""函数需带类型签名"）

    timestamp: str = Field(default_factory=lambda: datetime.now().isoformat())


class ExecuteMessage(BaseModel):
    """执行消息——平台发给 Harness/Agent 的完整执行指令"""
    id: str = Field(default_factory=lambda: uuid4().hex[:12])
    delegation: DelegationRequest
    context: ExecutionContext = Field(default_factory=ExecutionContext)
    phase: str = "execute"              # execute / retry / review_feedback
    retry_count: int = 0
    timestamp: str = Field(default_factory=lambda: datetime.now().isoformat())


class HistoricalTask(BaseModel):
    """历史任务——用于记忆系统的向量检索与经验回放"""
    task_id: str
    title: str
    description: str = ""
    capabilities_used: list[str] = Field(default_factory=list)
    agent_executions: dict[str, str] = Field(default_factory=dict)
    # ↑ agent_id → 结果摘要

    review_verdicts: dict[str, str] = Field(default_factory=dict)
    # ↑ delegation_id → "pass"/"fail"

    quality_score: float = 0.0          # 综合质量评分
    duration_ms: int = 0
    completed_at: str = ""
    embedding: Optional[list[float]] = None   # 向量嵌入（内存中）
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat())


class MemoryStats(BaseModel):
    """记忆系统统计"""
    total_tasks: int = 0
    total_capabilities: int = 0
    avg_quality_score: float = 0.0
    top_capabilities: list[tuple[str, int]] = Field(default_factory=list)
    # ↑ (capability_tag, usage_count)

    recent_success_rate: float = 0.0    # 最近 50 次成功率
    last_updated: str = ""


class CapabilityLedger(BaseModel):
    """能力信誉账本——记录每个 Agent 每种能力的表现"""
    agent_id: str
    capability: str
    total_tasks: int = 0
    success_count: int = 0
    avg_score: float = 0.0              # 审查评分均值
    avg_duration_ms: int = 0
    last_used_at: str = ""
    reputation: float = 1.0             # 综合信誉分（0.0~1.0）
    updated_at: str = Field(default_factory=lambda: datetime.now().isoformat())
