"""
v4 MVP 讨论室引擎 —— 收敛驱动审查循环

取代 v3.1 的对等协商投票模式，改为：
1. AI 分析 → 拆解子任务
2. 拓扑排序 → 按依赖层执行
3. 每层执行完 → 审查（跨 Agent 或自审）
4. 不通过 → 修正重试（最多 2 次）
5. 通过 → 下一层，直到完成

5 阶段：create → propose → review → refine → land
"""

from __future__ import annotations
import asyncio
import json
import time
from collections import defaultdict, deque
from datetime import datetime
from typing import Optional, Callable, Awaitable

from .protocol import (
    Task, TaskStatus, DiscussionRoom, DiscussionRoomStatus,
    TaskProposal, SubTaskAssignment, Agreement,
    DelegationRequest, InputSchema, OutputSchema,
    ExecutionContext, ExecuteMessage,
    ReviewRequest, ReviewResponse,
    AgentCard, Message, MessageType,
)


# ── 拓扑排序辅助 ──────────────────────────────

def _topological_sort(
    delegations: list[DelegationRequest],
    dependencies: list[list[str]],
) -> list[list[DelegationRequest]]:
    """Kahn 算法：将委托列表按依赖关系拆分为拓扑层。

    返回: [[layer_0_delegations], [layer_1_delegations], ...]
    每层内的委托可并行执行。
    """
    del_map = {d.id: d for d in delegations}
    in_degree: dict[str, int] = defaultdict(int)
    graph: dict[str, list[str]] = defaultdict(list)

    for dep in dependencies:
        if len(dep) == 2:
            a, b = dep[0], dep[1]
            if a in del_map and b in del_map:
                graph[a].append(b)
                in_degree[b] += 1

    # 初始化：入度为 0 的节点
    queue = deque([d.id for d in delegations if in_degree[d.id] == 0])
    layers: list[list[DelegationRequest]] = []
    processed: set[str] = set()

    while queue:
        layer = []
        for _ in range(len(queue)):
            node_id = queue.popleft()
            if node_id in del_map and node_id not in processed:
                layer.append(del_map[node_id])
                processed.add(node_id)
                for neighbor in graph[node_id]:
                    in_degree[neighbor] -= 1
                    if in_degree[neighbor] == 0:
                        queue.append(neighbor)
        if layer:
            layers.append(layer)

    # 兜底：未入图的孤立节点
    for d in delegations:
        if d.id not in processed:
            if not layers:
                layers.append([])
            layers[0].append(d)

    return layers


# ═══════════════════════════════════════════════════════════════
# DiscussionEngine
# ═══════════════════════════════════════════════════════════════

class DiscussionEngine:
    """收敛驱动审查引擎

    - 接收 AI 分析拆解出的子任务提案
    - 拓扑排序确定执行层
    - 每层执行后触发审查
    - 不通过时修正重试（上限 2 次）
    - 全通过后落地关闭
    """

    MAX_REVIEW_RETRIES = 2           # 单子任务最大审查重试次数
    REVIEW_TIMEOUT_SECONDS = 30      # 单次审查超时
    CACHE_TTL_SECONDS = 60           # 提案缓存 TTL
    MAX_RETRIES_PER_TASK = 5         # 全局重试上限

    def __init__(self):
        self.rooms: dict[str, DiscussionRoom] = {}
        self._proposal_cache: dict[str, tuple[float, TaskProposal]] = {}
        # ↑ key=proposal_id, value=(cached_at_timestamp, proposal)
        self._review_counts: dict[str, int] = defaultdict(int)
        # ↑ key=delegation_id, value=重试次数
        self._global_retries: dict[str, int] = defaultdict(int)
        # ↑ key=task_id, value=全局重试计数

    # ═══════════════════════════════════════════════════════════
    # 阶段 1: create
    # ═══════════════════════════════════════════════════════════

    def create_room(
        self,
        task: Task,
        agent_ids: list[str],
        analysis: dict,
    ) -> DiscussionRoom:
        """根据 AI 分析结果创建讨论室"""
        room = DiscussionRoom(
            task_id=task.id,
            task_title=task.title,
            task_description=task.description,
            participants=agent_ids,
            status=DiscussionRoomStatus.FORMING,
        )
        self.rooms[room.room_id] = room
        return room

    # ═══════════════════════════════════════════════════════════
    # 阶段 2: propose
    # ═══════════════════════════════════════════════════════════

    def propose(
        self,
        room_id: str,
        proposed_by: str,
        assignments: dict[str, SubTaskAssignment],
        dependencies: list[list[str]],
        title: str = "",
        description: str = "",
    ) -> TaskProposal:
        """提交分工提案"""
        room = self.rooms.get(room_id)
        if not room:
            raise ValueError(f"讨论室 {room_id} 不存在")

        proposal = TaskProposal(
            room_id=room_id,
            proposed_by=proposed_by,
            title=title,
            description=description,
            assignments=assignments,
            dependencies=dependencies,
            status="proposed",
        )

        room.proposals.append(proposal)
        room.status = DiscussionRoomStatus.NEGOTIATING

        # 缓存提案
        self._proposal_cache[proposal.proposal_id] = (time.time(), proposal)

        return proposal

    # ═══════════════════════════════════════════════════════════
    # 阶段 3: review — 收敛驱动审查
    # ═══════════════════════════════════════════════════════════

    async def review(
        self,
        task_id: str,
        delegation: DelegationRequest,
        result: "ExecutionResult",
        *,
        review_fn: Callable[[ReviewRequest], Awaitable[ReviewResponse]],
    ) -> tuple[str, ReviewResponse]:
        """审查一次执行结果

        Args:
            delegation: 被审查的委托
            result: 执行结果
            review_fn: 审查回调（异步），签名 (ReviewRequest) → ReviewResponse

        Returns:
            (verdict: "pass"/"fail"/"amend", review_response)
        """

        # 全局重试上线检查
        if self._global_retries[task_id] >= self.MAX_RETRIES_PER_TASK:
            return ("fail", ReviewResponse(
                request_id=f"review-{delegation.id}",
                from_agent="platform",
                verdict="fail",
                feedback=f"全局重试次数已达上限 {self.MAX_RETRIES_PER_TASK}",
                score=0.0,
            ))

        request = ReviewRequest(
            task_id=task_id,
            delegation_id=delegation.id,
            from_agent=delegation.from_agent,
            to_agent=delegation.to_agent,
            target_type="execution_result",
            content=(
                f"标题: {delegation.title}\n"
                f"描述: {delegation.description}\n"
                f"能力要求: {delegation.capability_required}\n"
                f"结果: {result.content[:2000]}\n"
                f"置信度: {result.confidence}\n"
                f"错误: {result.error or '无'}\n"
            ),
            expected_capability=delegation.capability_required,
            timeout_seconds=self.REVIEW_TIMEOUT_SECONDS,
        )

        try:
            review_resp = await review_fn(request)
        except Exception as e:
            review_resp = ReviewResponse(
                request_id=request.request_id,
                from_agent="platform",
                verdict="fail",
                feedback=f"审查异常: {e}",
                score=0.0,
            )

        self._review_counts[delegation.id] += 1
        self._global_retries[task_id] += 1

        return (review_resp.verdict, review_resp)

    # ═══════════════════════════════════════════════════════════
    # 阶段 4: refine — 修正与重试
    # ═══════════════════════════════════════════════════════════

    def can_retry(self, delegation_id: str) -> bool:
        """判断是否还能重试"""
        return self._review_counts[delegation_id] <= self.MAX_REVIEW_RETRIES

    def need_amend(
        self,
        delegation_id: str,
        feedback: str,
    ) -> dict:
        """生成修正指令"""
        return {
            "delegation_id": delegation_id,
            "feedback": feedback,
            "retry_count": self._review_counts.get(delegation_id, 0),
            "max_retries": self.MAX_REVIEW_RETRIES,
        }

    # ═══════════════════════════════════════════════════════════
    # 阶段 5: land — 落地与关闭
    # ═══════════════════════════════════════════════════════════

    def land_proposal(
        self,
        room_id: str,
        proposal_id: str,
        votes: dict[str, str] | None = None,
    ) -> tuple[Agreement, list[DelegationRequest]]:
        """落地提案：生成共识记录 + 委托链"""
        room = self.rooms.get(room_id)
        if not room:
            raise ValueError(f"讨论室 {room_id} 不存在")

        # 找到提案
        proposal = None
        for p in room.proposals:
            if p.proposal_id == proposal_id:
                proposal = p
                break

        if not proposal:
            proposal = room.proposals[-1] if room.proposals else None
        if not proposal:
            raise ValueError(f"提案 {proposal_id} 不存在且无备用提案")

        proposal.status = "accepted"

        # 生成委托链
        delegations: list[DelegationRequest] = []
        for agent_id, assignment in proposal.assignments.items():
            del_req = DelegationRequest(
                from_agent=proposal.proposed_by,
                to_agent=agent_id,
                capability_required=assignment.capability_required,
                task_id=room.task_id,
                title=assignment.task_title,
                description=assignment.task_description,
                expected_output=OutputSchema(
                    content_type="text",
                    required_fields=["result"],
                ),
                depends_on=self._resolve_deps(agent_id, proposal.dependencies, delegations),
                priority=assignment.priority,
            )
            delegations.append(del_req)

        # 拓扑排序委托链
        layers = _topological_sort(delegations, proposal.dependencies)

        # 生成共识
        agreement = Agreement(
            room_id=room_id,
            proposal_id=proposal.proposal_id,
            votes=votes or {},
            vote_count=len(votes) if votes else len(room.participants),
            approve_count=len(votes) if votes else len(room.participants),
            status="approved",
            finalized_at=datetime.now().isoformat(),
        )

        room.agreement = agreement
        room.delegation_chain = delegations
        room.status = DiscussionRoomStatus.DELEGATING

        return agreement, delegations

    def _resolve_deps(
        self,
        agent_id: str,
        dependencies: list[list[str]],
        existing: list[DelegationRequest],
    ) -> list[str]:
        """解析某个 Agent 的依赖：返回它依赖的 delegation_id 列表"""
        deps = []
        for dep in dependencies:
            if len(dep) == 2 and dep[1] == agent_id:
                for d in existing:
                    if d.to_agent == dep[0]:
                        deps.append(d.id)
        return deps

    # ═══════════════════════════════════════════════════════════
    # 执行编排：按拓扑层执行 + 审查
    # ═══════════════════════════════════════════════════════════

    async def execute_layers(
        self,
        task_id: str,
        delegations: list[DelegationRequest],
        dependencies: list[list[str]],
        *,
        execute_fn: Callable[[ExecuteMessage], Awaitable["ExecutionResult"]],
        review_fn: Callable[[ReviewRequest], Awaitable[ReviewResponse]],
        task_title: str = "",
        task_description: str = "",
    ) -> dict[str, "ExecutionResult"]:
        """按拓扑层执行所有委托，每层完成后审查。

        Returns:
            dict[delegation_id → ExecutionResult]
        """
        layers = _topological_sort(delegations, dependencies)
        results: dict[str, "ExecutionResult"] = {}
        parent_outputs: dict[str, str] = {}

        for layer_idx, layer in enumerate(layers):
            # 并行执行当前层
            layer_tasks = []
            for del_req in layer:
                # 补丁9：注入任务原文，供执行方（含 manual 接管通道）理解所属任务
                ctx = ExecutionContext(
                    task_id=task_id,
                    task_title=task_title,
                    task_description=task_description,
                    parent_outputs=dict(parent_outputs),
                    dependency_graph=[[d.id for d in layer]],
                    global_constraints=[],
                )
                exec_msg = ExecuteMessage(
                    delegation=del_req,
                    context=ctx,
                )
                layer_tasks.append(execute_fn(exec_msg))

            layer_results = await asyncio.gather(*layer_tasks, return_exceptions=True)

            # 审查当前层每个结果
            for del_req, raw_result in zip(layer, layer_results):
                if isinstance(raw_result, Exception):
                    from .protocol import ExecutionResult
                    result = ExecutionResult(
                        delegation_id=del_req.id,
                        from_agent=del_req.from_agent,
                        to_agent=del_req.to_agent,
                        ok=False,
                        content="",
                        error=str(raw_result),
                    )
                else:
                    result = raw_result

                # 审查循环
                for _ in range(self.MAX_REVIEW_RETRIES + 1):
                    verdict, review_resp = await self.review(
                        task_id=task_id,
                        delegation=del_req,
                        result=result,
                        review_fn=review_fn,
                    )
                    if verdict == "pass":
                        break
                    elif verdict == "amend":
                        # 重试执行（带修正反馈）
                        ctx = ExecutionContext(
                            task_id=task_id,
                            parent_outputs=dict(parent_outputs),
                            dependency_graph=[[d.id for d in layer]],
                            global_constraints=[f"上次审查反馈: {review_resp.feedback}"],
                        )
                        exec_msg = ExecuteMessage(
                            delegation=del_req,
                            context=ctx,
                            phase="retry",
                            retry_count=self._review_counts[del_req.id],
                        )
                        result = await execute_fn(exec_msg)
                    else:  # fail
                        break

                results[del_req.id] = result
                parent_outputs[del_req.id] = result.content[:500] if result.ok else f"[依赖执行失败: {result.error or '未知错误'}]"

        return results

    # ═══════════════════════════════════════════════════════════
    # 关闭
    # ═══════════════════════════════════════════════════════════

    def close_room(self, room_id: str, success: bool = True):
        """关闭讨论室"""
        room = self.rooms.get(room_id)
        if room:
            room.status = DiscussionRoomStatus.DISSOLVED if success else DiscussionRoomStatus.DEADLOCK
            room.resolved_at = datetime.now().isoformat()

            # 清理缓存（TTL）
            now = time.time()
            expired = [
                pid for pid, (ts, _) in self._proposal_cache.items()
                if now - ts > self.CACHE_TTL_SECONDS
            ]
            for pid in expired:
                self._proposal_cache.pop(pid, None)

            # 清理审查计数
            for key in list(self._review_counts.keys()):
                if not any(d.id == key for d in room.delegation_chain):
                    self._review_counts.pop(key, None)

    def get_room(self, room_id: str) -> Optional[DiscussionRoom]:
        return self.rooms.get(room_id)

    def task_room(self, task_id: str) -> Optional[DiscussionRoom]:
        for room in self.rooms.values():
            if room.task_id == task_id:
                return room
        return None

    def stats(self) -> dict:
        return {
            "active_rooms": sum(
                1 for r in self.rooms.values()
                if r.status not in (DiscussionRoomStatus.DISSOLVED, DiscussionRoomStatus.DEADLOCK)
            ),
            "total_rooms": len(self.rooms),
            "active_reviews": len(self._review_counts),
            "cached_proposals": len(self._proposal_cache),
        }


# ── 全局单例 ─────────────────────────────────

discussion_engine = DiscussionEngine()
