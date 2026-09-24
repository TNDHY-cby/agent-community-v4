"""内置调度大脑 — Orchestrator（v4）

核心职责：
  1. 接收用户任务 → AI 分析需求、拆解为子任务 Proposals + Assignments
  2. 匹配 Agent/Harness（能力匹配 + 信誉加权）
  3. 注入 discussion_engine 创建讨论室
  4. 驱动 propose → review → refine → land 循环
  5. 按拓扑层执行 + 审查，收敛后汇总

与 v3.1 的区别：废除广播/举手/投票模式，
改为 AI 分析拆解 → 信誉匹配 → 收敛驱动审查。
"""

from __future__ import annotations
import json
import asyncio
from datetime import datetime
from uuid import uuid4
from typing import Optional, Callable, Awaitable

import httpx

from .ai_external import run_ai_call

from .protocol import (
    AgentCard, AgentEndpoint, Message, MessageType, Task, TaskStatus,
    DiscussionRoom, DiscussionRoomStatus,
    SubTaskAssignment, TaskProposal,
    DelegationRequest, ReviewRequest, ReviewResponse,
    HistoricalTask, ExecutionContext, ExecuteMessage,
    HarnessMessage, TransportType,
)


class Orchestrator:
    """平台内置调度大脑（v4 收敛驱动）。

    作为特殊平台 Agent 注册，拥有全读权限 + 讨论室引擎 + 记忆系统调用权限。
    不暴露 HTTP 端点，所有逻辑内聚在平台服务进程中。
    """

    # ── 身份信息 ─────────────────────────────────
    AGENT_ID = "orchestrator"
    AGENT_NAME = "Orchestrator"
    AGENT_DESC = (
        "平台内置调度大脑：AI 分析拆解、信誉匹配 Agent/Harness、"
        "驱动收敛审查循环（propose→review→refine→land）"
    )

    # ── 匹配配置 ─────────────────────────────────
    DEFAULT_REPUTATION_THRESHOLD = 0.3   # 信誉分低于此值不匹配
    MIN_AGENTS_FOR_TASK = 1              # 最少匹配 Agent 数
    WEAK_MATCH_FALLBACK = True           # 严格匹配失败时降级为弱匹配

    def __init__(self):
        self._task_counter = 0
        self._stats = {
            "tasks_analyzed": 0,
            "rooms_created": 0,
            "consensus_reached": 0,
            "executions_completed": 0,
        }

    # ═══════════════════════════════════════════════════════════════
    #  公开入口
    # ═══════════════════════════════════════════════════════════════

    def as_agent_card(self) -> AgentCard:
        return AgentCard(
            agent_id=self.AGENT_ID,
            name=self.AGENT_NAME,
            version="2.0",
            endpoints=[
                AgentEndpoint(
                    transport=TransportType.HTTP,
                    url="builtin://orchestrator",
                ),
            ],
            capabilities=[
                "task_analysis",
                "agent_matching",
                "workflow_orchestration",
                "review_driving",
            ],
            description=self.AGENT_DESC,
            max_delegations=99,
        )

    async def handle_task(
        self,
        task: Task,
        *,
        ai: "AIProvider",
        agents: dict,                # agent_id → AgentCard
        harness_mgr,                 # HarnessManager 实例
        discussion_engine,           # DiscussionEngine 实例
        task_memory,                 # TaskMemory 实例
        capability_ledger,           # CapabilityLedgerManager 实例
        bcast: Callable[..., Awaitable],
        append_hall: Callable,
    ) -> dict:
        """处理新任务的 v4 编排流程。

        流程：AI 分析拆解 → 信誉匹配 → 创建讨论室 → propose → land → 返回委托链

        Returns:
            {"room_id": ..., "participants": [...], "analysis": ..., "delegations": [...]}
        """
        self._task_counter += 1
        self._stats["tasks_analyzed"] += 1

        # 步骤 1: AI 分析任务
        analysis = await self._analyze_task_full(ai, task, agents, harness_mgr=harness_mgr)
        if not analysis:
            return {"error": "AI 分析任务失败，无法自动拆解"}

        # 步骤 2: 信誉匹配 Agent → 生成 Assignments
        assignments = self._build_assignments(analysis, agents, capability_ledger, harness_mgr=harness_mgr)
        if not assignments:
            return {"error": "没有匹配到合适的 Agent"}

        # 步骤 3: 创建讨论室
        agent_ids = list(assignments.keys())
        room = discussion_engine.create_room(
            task=task,
            agent_ids=agent_ids,
            analysis=analysis,
        )
        self._stats["rooms_created"] += 1

        # 步骤 4: 提交提案
        dependencies = analysis.get("dependencies", [])
        proposal = discussion_engine.propose(
            room_id=room.room_id,
            proposed_by=self.AGENT_ID,
            assignments=assignments,
            dependencies=dependencies,
            title=task.title or analysis.get("task_summary", "自动拆解任务"),
            description=task.description,
        )

        # 步骤 5: 落地提案 → 生成委托链
        agreement, delegations = discussion_engine.land_proposal(
            room_id=room.room_id,
            proposal_id=proposal.proposal_id,
        )

        self._stats["consensus_reached"] += 1

        # 步骤 6: 按拓扑层执行委托链
        async def execute_fn(exec_msg):
            """将 ExecuteMessage 发送到对应 Agent 的 Harness 执行。

            v4.1: 双 Harness 在线时走直连 HTTP POST，跳过平台桥接队列。
            """
            from .protocol import ExecutionResult as ER
            agent_id = exec_msg.delegation.to_agent
            from_agent = exec_msg.delegation.from_agent

            # v4.1: 双 Harness 直连优化
            to_hid = harness_mgr.id_to_harness.get(agent_id)
            from_hid = (
                harness_mgr.id_to_harness.get(from_agent)
                if from_agent != self.AGENT_ID
                else None
            )
            if from_hid and to_hid and from_hid != to_hid:
                to_sess = harness_mgr.sessions.get(to_hid)
                if to_sess and to_sess.info.callback_url:
                    try:
                        hm_msg = HarnessMessage(
                            harness_id=to_hid,
                            direction="to_harness",
                            msg_type="execute",
                            content=(
                                f"【直接委托 - 来自 {from_hid}】\n"
                                f"标题: {exec_msg.delegation.title}\n"
                                f"描述: {exec_msg.delegation.description}\n"
                                f"所需能力: {exec_msg.delegation.capability_required}\n"
                                f"前序产出: {json.dumps(exec_msg.context.parent_outputs, ensure_ascii=False)}\n"
                            ),
                            task_id=exec_msg.delegation.task_id,
                            delegation_id=exec_msg.delegation.id,
                        )
                        async with httpx.AsyncClient(timeout=300.0) as c:
                            r = await c.post(
                                to_sess.info.callback_url,
                                json=hm_msg.model_dump(),
                            )
                        if r.status_code == 200:
                            data = r.json()
                            content = data.get("result", data.get("content", json.dumps(data)))
                            confidence = 1.0
                            try:
                                inner = json.loads(content) if isinstance(content, str) else content
                                if isinstance(inner, dict):
                                    content = json.dumps(inner, ensure_ascii=False)
                                    if "confidence" in inner:
                                        confidence = float(inner["confidence"])
                            except (json.JSONDecodeError, Exception):
                                pass
                            return ER(
                                delegation_id=exec_msg.delegation.id,
                                from_agent=from_agent,
                                to_agent=agent_id,
                                ok=True, content=content, error=None,
                                confidence=confidence,
                            )
                        return ER(
                            delegation_id=exec_msg.delegation.id,
                            from_agent=from_agent,
                            to_agent=agent_id,
                            ok=False, content="",
                            error=f"直连失败 HTTP {r.status_code}", confidence=0.0,
                        )
                    except Exception as e:
                        # 直连失败回退到桥接路径
                        pass

            bridge = harness_mgr.get_bridge_by_agent(agent_id)
            if bridge:
                try:
                    hm = await bridge.execute_subtask(exec_msg)
                    ok = hm.msg_type != "error"
                    error = None
                    content = hm.content or ""
                    confidence = 1.0
                    try:
                        data = json.loads(content)
                        if isinstance(data, dict):
                            content = json.dumps(data, ensure_ascii=False)
                            if "error" in data:
                                ok = False
                                error = data["error"]
                            if "confidence" in data:
                                confidence = float(data["confidence"])
                    except (json.JSONDecodeError, Exception):
                        pass
                    return ER(
                        delegation_id=exec_msg.delegation.id,
                        from_agent=exec_msg.delegation.from_agent,
                        to_agent=agent_id,
                        ok=ok,
                        content=content,
                        error=error,
                        confidence=confidence,
                    )
                except Exception as e:
                    return ER(
                        delegation_id=exec_msg.delegation.id,
                        from_agent=exec_msg.delegation.from_agent,
                        to_agent=agent_id,
                        ok=False, content="",
                        error=f"Harness 执行异常: {e}", confidence=0.0,
                    )
            return ER(
                delegation_id=exec_msg.delegation.id,
                from_agent=exec_msg.delegation.from_agent,
                to_agent=agent_id,
                ok=False, content="",
                error=f"Agent {agent_id} 未注册 Harness 桥接", confidence=0.0,
            )

        async def review_fn(review_req):
            """发送审查请求到非执行者的 Agent。

            v4.1: 双 Harness 在线时走直连 HTTP POST。
            """
            from .protocol import ReviewResponse as RR
            executor_id = review_req.to_agent
            reviewer_id = None
            for aid in agent_ids:
                if aid != executor_id:
                    reviewer_id = aid
                    break
            if not reviewer_id:
                return RR(
                    request_id=review_req.request_id,
                    from_agent=self.AGENT_ID, verdict="pass",
                    feedback="无其他 Agent 可审查，默认通过", score=0.0,
                )

            # v4.1: 双 Harness 直连审查优化
            reviewer_hid = harness_mgr.id_to_harness.get(reviewer_id)
            executor_hid = harness_mgr.id_to_harness.get(executor_id)
            if reviewer_hid and executor_hid and reviewer_hid != executor_hid:
                reviewer_sess = harness_mgr.sessions.get(reviewer_hid)
                if reviewer_sess and reviewer_sess.info.callback_url:
                    try:
                        hm_msg = HarnessMessage(
                            harness_id=reviewer_hid,
                            direction="to_harness",
                            msg_type="review",
                            content=(
                                f"【直接审查 - 审查来自 {executor_hid} 的执行结果】\n"
                                f"审查对象: {review_req.target_type}\n"
                                f"期望能力: {review_req.expected_capability}\n"
                                f"待审查内容:\n{review_req.content}"
                            ),
                            task_id=review_req.task_id,
                            delegation_id=review_req.delegation_id,
                        )
                        async with httpx.AsyncClient(timeout=review_req.timeout_seconds) as c:
                            r = await c.post(
                                reviewer_sess.info.callback_url,
                                json=hm_msg.model_dump(),
                            )
                        if r.status_code == 200:
                            data = r.json()
                            content = data.get("result", data.get("content", "{}"))
                            try:
                                inner = json.loads(content) if isinstance(content, str) else content
                                return RR(
                                    request_id=review_req.request_id,
                                    from_agent=reviewer_id,
                                    verdict=inner.get("verdict", "pass"),
                                    feedback=inner.get("feedback", content[:200]),
                                    score=float(inner.get("score", 0.0)),
                                )
                            except (json.JSONDecodeError, Exception):
                                return RR(
                                    request_id=review_req.request_id,
                                    from_agent=reviewer_id, verdict="pass",
                                    feedback=content[:200] or "直连审查返回格式异常", score=0.5,
                                )
                    except Exception:
                        pass  # 直连失败回退到桥接路径

            bridge = harness_mgr.get_bridge_by_agent(reviewer_id)
            if bridge:
                try:
                    hm = await bridge.review_subtask(review_req)
                    content = hm.content or "{}"
                    try:
                        data = json.loads(content)
                        return RR(
                            request_id=review_req.request_id,
                            from_agent=reviewer_id,
                            verdict=data.get("verdict", "pass"),
                            feedback=data.get("feedback", hm.content[:200]),
                            score=float(data.get("score", 0.0)),
                        )
                    except (json.JSONDecodeError, Exception):
                        return RR(
                            request_id=review_req.request_id,
                            from_agent=reviewer_id, verdict="pass",
                            feedback=hm.content[:200] or "审查者无明确反馈", score=0.5,
                        )
                except Exception as e:
                    return RR(
                        request_id=review_req.request_id,
                        from_agent=reviewer_id, verdict="pass",
                        feedback=f"审查异常: {e}", score=0.0,
                    )
            return RR(
                request_id=review_req.request_id,
                from_agent=reviewer_id, verdict="pass",
                feedback=f"Agent {reviewer_id} 未注册 Harness 桥接", score=0.0,
            )

        results = await discussion_engine.execute_layers(
            task_id=task.id,
            delegations=delegations,
            dependencies=dependencies,
            execute_fn=execute_fn,
            review_fn=review_fn,
            # 补丁9：透传任务原文，避免执行方只看到子任务元描述而产出空转套话
            task_title=task.title,
            task_description=task.description,
        )

        self._stats["executions_completed"] += sum(1 for r in results.values() if r.ok)

        # 合成执行摘要
        exec_summary = "\n".join(
            f"  [{r.to_agent}] {'PASS' if r.ok else 'FAIL'}: {r.content[:100]}"
            for r in results.values()
        )

        # 计算实际拓扑层数
        from .discussion_engine import _topological_sort
        actual_layers = _topological_sort(delegations, dependencies)

        # 广播事件
        room_msg = Message(
            type=MessageType.EVENT,
            from_agent=self.AGENT_ID,
            content=f"任务已拆解为 {len(delegations)} 个子任务，"
                     f"指派 {len(assignments)} 位 Agent（分 {len(actual_layers)} 层执行，{sum(1 for r in results.values() if r.ok)}/{len(results)} 通过）",
            task_id=task.id,
            room_id=room.room_id,
            payload={
                "event": "task_decomposed",
                "room": room.model_dump(),
                "delegations": [d.model_dump() for d in delegations],
                "analysis": analysis,
                "exec_summary": exec_summary,
                "results": {k: v.model_dump() for k, v in results.items()},
            },
        )
        append_hall(room_msg)
        await bcast(room_msg)

        # 写入任务级记忆
        task_memory.add(HistoricalTask(
            task_id=task.id,
            title=task.title,
            description=task.description,
            capabilities_used=list(analysis.get("required_capabilities", [])),
            quality_score=0.0,
        ))

        ok_count = sum(1 for r in results.values() if r.ok)
        return {
            "room_id": room.room_id,
            "participants": agent_ids,
            "delegations": [d.model_dump() for d in delegations],
            "analysis": analysis,
            "results": {k: v.model_dump() for k, v in results.items()},
            "exec_summary": exec_summary,
            "ok_count": ok_count,
            "total_count": len(results),
        }

    # ═══════════════════════════════════════════════════════════════
    #  步骤 1: AI 分析（v4 — 完整拆解）
    # ═══════════════════════════════════════════════════════════════

    @staticmethod
    def _filter_online_agents(agents: dict, harness_mgr) -> dict:
        """仅保留在线 Agent（harness- 前缀需 session 为 ONLINE），离线 harness 不参与拆解/委托。"""
        result = {}
        for aid, card in agents.items():
            if not aid.startswith("harness-"):
                result[aid] = card
                continue
            if harness_mgr is None:
                continue
            _hid = harness_mgr.id_to_harness.get(aid) or aid[len("harness-"):]
            _sess = harness_mgr.sessions.get(_hid)
            print(f"[Orchestrator] filter aid={aid!r} _hid={_hid!r} sess={'None' if _sess is None else getattr(_sess.status,'value',_sess.status)} sessions_keys={list(harness_mgr.sessions.keys())}", flush=True)
            if not _sess:
                continue
            try:
                from .protocol import HarnessStatus
                _ok = (_sess.status == HarnessStatus.ONLINE)
                if _ok:
                    result[aid] = card
            except Exception as _e:
                print(f"[Orchestrator] filter EXC aid={aid!r} err={_e!r}", flush=True)
                continue
        return result

    async def _analyze_task_full(
        self,
        ai,
        task: Task,
        agents: dict,
        harness_mgr=None,
    ) -> Optional[dict]:
        """AI 分析任务 → 输出结构化拆解方案"""
        agents = self._filter_online_agents(agents, harness_mgr)
        # 精简 Agent 卡片：description 截断、capabilities 限量，防止拆解请求撑爆小上下文模型（llama -c 4096）
        agent_list = [
            {
                "id": aid,
                "name": card.name,
                "capabilities": (card.capabilities or [])[:8],
                "description": str(card.description or "")[:80],
            }
            for aid, card in agents.items()
            if aid != self.AGENT_ID
        ]

        system = (
            "你是任务调度专家。分析用户任务，输出 JSON 拆解方案。\n"
            "规则：\n"
            "1. 提取任务需要的能力（required_capabilities）\n"
            "2. 将任务拆解为子任务（subtasks），每个子任务绑定一个能力\n"
            "3. 标注子任务间依赖（dependencies：[from, to]）\n"
            "4. 为每个子任务匹配最合适的 Agent ID\n"
            "5. 只输出 JSON，不要加解释"
        )

        user = (
            f"用户任务: {task.title}\n{task.description}\n\n"
            f"在线 Agent 列表:\n{json.dumps(agent_list, ensure_ascii=False, indent=2)}\n\n"
            "输出 JSON:\n"
            '{\n'
            '  "task_summary": "任务一句话总结",\n'
            '  "required_capabilities": ["cap1","cap2"],\n'
            '  "complexity": "simple|medium|complex",\n'
            '  "subtasks": [\n'
            '    {"agent_id": "xxx", "title": "子任务名", "description": "说明", "capability": "能力标签", "priority": 5}\n'
            '  ],\n'
            '  "dependencies": [["from_id","to_id"]],\n'
            '  "reasoning": "拆解理由（一句话）"\n'
            '}'
        )

        try:
            # 等待上限按 ai_mode 动态取值（manual→外部回写窗口；local→本机推理窗口；remote→云端上限）。
            # 历史缺陷：此处曾固定传入 60 秒等待上限，导致 local/manual 模式下拆解调用被提前取消。
            reply = await run_ai_call(
                ai.chat(system, user),
                mode_timeout=True,
                label=f"orchestrator.analyze task={getattr(task, 'id', '')}",
            )
            json_start = reply.find("{")
            json_end = reply.rfind("}") + 1
            if json_start >= 0 and json_end > json_start:
                parsed = json.loads(reply[json_start:json_end])
                print(f"[Orchestrator] AI 分析返回: {json.dumps(parsed, ensure_ascii=False)[:2000]}", flush=True)
                return parsed
        except (json.JSONDecodeError, Exception) as e:
            print(f"[Orchestrator] 任务分析失败: {e}")

        return None

    # ═══════════════════════════════════════════════════════════════
    #  步骤 2: 信誉匹配 → 生成 Assignments
    # ═══════════════════════════════════════════════════════════════

    def _build_assignments(
        self,
        analysis: dict,
        agents: dict,
        capability_ledger,
        harness_mgr=None,
    ) -> dict[str, SubTaskAssignment]:
        """根据 AI 分析 + 信誉分生成 SubTaskAssignment 字典。

        优先用 AI 推荐的 agent_id；若推荐不在线或无信誉，回退到信誉最高者。
        """
        agents = self._filter_online_agents(agents, harness_mgr)
        assignments: dict[str, SubTaskAssignment] = {}
        subtasks = analysis.get("subtasks", [])
        print(f"[Orchestrator] _build_assignments agents={ {aid: (c.name, c.capabilities) for aid, c in agents.items()} }", flush=True)

        for st in subtasks:
            agent_id = st.get("agent_id", "")
            capability = st.get("capability", "general")

            # AI 常返回纯名称（如"测试甲"）而非规范 agent_id（如"harness-测试甲"），
            # 先做名称归一化：card.name / harness 后缀 / 直接前缀 三种形式均映射回 agents 的 key。
            if agent_id not in agents:
                for aid, card in agents.items():
                    if aid == self.AGENT_ID:
                        continue
                    if card.name == agent_id or aid == agent_id or aid[len("harness-"):] == agent_id:
                        agent_id = aid
                        break

            # 如果 AI 推荐的 Agent 不存在或信誉太低，用信誉最高者替代
            if agent_id not in agents:
                best_agents = capability_ledger.get_best_agents(
                    capability, min_reputation=self.DEFAULT_REPUTATION_THRESHOLD, top_k=1
                )
                if best_agents:
                    agent_id = best_agents[0][0]
                else:
                    # 弱匹配兜底：找有同名能力的任意 Agent
                    for aid, card in agents.items():
                        if aid != self.AGENT_ID and capability in card.capabilities:
                            agent_id = aid
                            break

            if not agent_id or agent_id not in agents:
                continue

            # 避免重复指派同一个 Agent 算两次
            if agent_id in assignments:
                # 追加子任务描述
                prev = assignments[agent_id]
                assignment = SubTaskAssignment(
                    agent_id=agent_id,
                    task_title=f"{prev.task_title} + {st.get('title', '')}",
                    task_description=f"{prev.task_description}\n---\n{st.get('description', '')}",
                    capability_required=prev.capability_required,
                    priority=max(prev.priority, st.get("priority", 5)),
                )
            else:
                assignment = SubTaskAssignment(
                    agent_id=agent_id,
                    task_title=st.get("title", "未命名子任务"),
                    task_description=st.get("description", ""),
                    capability_required=capability,
                    priority=st.get("priority", 5),
                )

            assignments[agent_id] = assignment

        return assignments

    # ═══════════════════════════════════════════════════════════════
    #  辅助: 按信誉排序匹配
    # ═══════════════════════════════════════════════════════════════

    def ranked_match(
        self,
        required_capabilities: list[str],
        agents: dict,
        capability_ledger,
    ) -> list[tuple[str, float]]:
        """按所需能力 + 信誉分对 Agent 排序。

        Returns: [(agent_id, weighted_score), ...] 降序
        """
        scored = []
        for aid, card in agents.items():
            if aid == self.AGENT_ID:
                continue
            agent_caps = set(card.capabilities)
            req_caps = set(required_capabilities)
            overlap = agent_caps & req_caps
            if not overlap:
                continue

            # 能力匹配分 = 覆盖比例
            match_score = len(overlap) / len(req_caps) if req_caps else 0.0

            # 信誉分 = 各能力分均值
            reps = [
                capability_ledger.get_reputation(aid, cap)
                for cap in overlap
            ]
            avg_rep = sum(reps) / len(reps) if reps else 0.5

            # 加权：匹配分 0.6 + 信誉分 0.4
            weighted = match_score * 0.6 + avg_rep * 0.4
            scored.append((aid, round(weighted, 3)))

        scored.sort(key=lambda x: x[1], reverse=True)
        return scored

    # ═══════════════════════════════════════════════════════════════
    #  执行后回调：更新信誉
    # ═══════════════════════════════════════════════════════════════

    def record_execution(
        self,
        agent_id: str,
        capability: str,
        success: bool,
        score: float = 0.0,
        duration_ms: int = 0,
        capability_ledger=None,
    ):
        """记录一次执行结果，更新信誉分"""
        if capability_ledger:
            capability_ledger.record(
                agent_id=agent_id,
                capability=capability,
                success=success,
                score=score,
                duration_ms=duration_ms,
            )
        if success:
            self._stats["executions_completed"] += 1

    # ═══════════════════════════════════════════════════════════════
    #  统计
    # ═══════════════════════════════════════════════════════════════

    @property
    def stats(self) -> dict:
        return dict(self._stats)


# 全局单例
orchestrator = Orchestrator()
