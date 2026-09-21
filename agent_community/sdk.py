"""
agent_community — 接入 agent-community-v4 平台的 Agent SDK。

提供两套接入模式：
  - AgentClient:           无状态 Agent 客户端，适合脚本/工具型 Agent
  - StatefulAgentClient:   有状态 Agent 客户端，适合运行中记录信誉/记忆的 Agent

典型用法:

    from agent_community import AgentClient

    async with AgentClient(
        agent_id="my-agent",
        name="My Agent",
        capabilities=["coding", "review"],
        platform_url="http://localhost:8000",
        token=None,  # localhost 免认证
    ) as h:
        @h.on_invite
        async def handle_invite(data):
            print(f"受邀: {data}")

        @h.on_execute
        async def handle_execute(subtask):
            return {"result": "done"}

        @h.on_review
        async def handle_review(data):
            return {"verdict": "approved", "comment": "LGTM"}

        await h.connect()
        await h.idle()
"""

from __future__ import annotations

import asyncio
import json
import logging
import signal
from typing import Optional, Callable, Awaitable, Any
from datetime import datetime

import httpx

# ── 协议类型（从共享类型模块导入，与 agent_community.platform.protocol 同源）──
from agent_community.types import SubTaskAssignment, DelegationRequest, ReviewRequest


# ═══════════════════════════════════════════════════════════════
#  AgentClient — 无状态接入
# ═══════════════════════════════════════════════════════════════

class AgentClient:
    """无状态 Agent 客户端。

    调用 invite/execute_subtask → API 交互 → 关闭。
    适合脚本型 Agent，每次连接不保留上次执行记忆。
    """

    def __init__(
        self,
        agent_id: str,
        name: str,
        capabilities: list[str],
        platform_url: str = "http://localhost:8000",
        token: Optional[str] = None,
        description: str = "",
    ):
        self.agent_id = agent_id
        self.name = name
        self.capabilities = capabilities
        self.platform_url = platform_url.rstrip("/")
        self.token = token
        self.description = description

        self._handlers: dict[str, Callable] = {}
        self._http: Optional[httpx.AsyncClient] = None
        self._connected = False
        self._logger = logging.getLogger(f"agent_client.{agent_id}")

    # ── 注册回调 ──────────────────────────────

    def on_invite(self, fn: Callable[..., Awaitable]) -> Callable:
        """注册受邀回调。回调接收 dict，可返回是否接受。"""
        self._handlers["invite"] = fn
        return fn

    def on_execute(self, fn: Callable[..., Awaitable]) -> Callable:
        """注册执行回调。回调接收 SubTaskAssignment，返回任意结果。"""
        self._handlers["execute"] = fn
        return fn

    def on_review(self, fn: Callable[..., Awaitable]) -> Callable:
        """注册审查回调。回调接收 ReviewRequest，返回 {"verdict": "approved|rejected", "comment": "..."}。"""
        self._handlers["review"] = fn
        return fn

    # ── 连接/断开 ─────────────────────────────

    async def connect(self) -> bool:
        """注册到平台，发送 AgentCard。"""
        self._http = httpx.AsyncClient(
            base_url=self.platform_url,
            timeout=httpx.Timeout(30.0),
        )

        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"

        payload = {
            "agent_id": self.agent_id,
            "name": self.name,
            "capabilities": self.capabilities,
            "endpoints": [
                {
                    "transport": "http",
                    "url": f"{self.platform_url}/harness/{self.agent_id}",
                }
            ],
            "description": self.description or f"SDK Agent: {self.name}",
            "max_delegations": 5,
        }

        try:
            resp = await self._http.post(
                "/api/agents/register",
                json=payload,
                headers=headers,
            )
            if resp.status_code in (200, 201):
                self._connected = True
                self._logger.info("Connected to platform")
                return True
            self._logger.error(f"Connect failed: {resp.status_code} {resp.text}")
            return False
        except Exception as e:
            self._logger.error(f"Connect error: {e}")
            return False

    async def disconnect(self):
        """从平台注销。"""
        if not self._connected or not self._http:
            return

        try:
            headers = {}
            if self.token:
                headers["Authorization"] = f"Bearer {self.token}"
            await self._http.delete(
                f"/api/harness/{self.agent_id}",
                headers=headers,
            )
        except Exception:
            pass
        finally:
            self._connected = False
            if self._http:
                await self._http.aclose()
                self._http = None

    # ── API 调用 ──────────────────────────────

    async def execute_subtask(
        self,
        room_id: str,
        task_title: str,
        task_description: str,
    ) -> dict:
        """提交子任务执行结果（通过讨论室 proposal 端点）。"""
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"

        payload = {
            "agent_id": self.agent_id,
            "title": task_title,
            "description": task_description,
        }
        resp = await self._http.post(
            f"/api/room/{room_id}/proposal",
            json=payload,
            headers=headers,
        )
        resp.raise_for_status()
        return resp.json()

    async def review_subtask(
        self,
        room_id: str,
        subtask_id: str,
        verdict: str,
        comment: str = "",
    ) -> dict:
        """提交审查结果（通过讨论室 vote 端点）。"""
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"

        payload = {
            "agent_id": self.agent_id,
            "verdict": verdict,
            "comment": comment,
        }
        resp = await self._http.post(
            f"/api/room/{room_id}/vote",
            json=payload,
            headers=headers,
        )
        resp.raise_for_status()
        return resp.json()

    # ── 服务模式：保持连接等待任务 ───────────────

    async def idle(self):
        """保持连接，等待任务（长连接 + 心跳）。

        阻塞直到收到 SIGINT/SIGTERM 或调用 close()。
        """
        if not self._connected:
            raise RuntimeError("Not connected. Call connect() first.")

        # 设置信号处理
        stop_event = asyncio.Event()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop = asyncio.get_running_loop()
                loop.add_signal_handler(sig, stop_event.set)
            except NotImplementedError:
                pass  # Windows 不支持 add_signal_handler

        self._logger.info("Idle — waiting for tasks...")
        try:
            while not stop_event.is_set():
                # 心跳
                try:
                    headers = {}
                    if self.token:
                        headers["Authorization"] = f"Bearer {self.token}"
                    await self._http.get(
                        "/api/status",
                        headers=headers,
                    )
                except Exception:
                    self._logger.warning("Heartbeat failed")

                await asyncio.sleep(5)
        finally:
            await self.disconnect()

    # ── 上下文管理器 ─────────────────────────────

    async def __aenter__(self):
        await self.connect()
        return self

    async def __aexit__(self, *args):
        await self.disconnect()


# ═══════════════════════════════════════════════════════════════
#  StatefulAgentClient — 有状态接入
# ═══════════════════════════════════════════════════════════════

class StatefulAgentClient(AgentClient):
    """有状态 Agent 客户端。

    比 AgentClient 多了：
      - 本地 TaskMemory 记录历史任务
      - 本地 CapabilityLedger 跟踪能力得分
      - 断线重连后恢复记忆

    适合需要信誉追踪和长期运行的 Agent（如本地 LLM Agent/工具 Agent）。
    """

    def __init__(
        self,
        agent_id: str,
        name: str,
        capabilities: list[str],
        platform_url: str = "http://localhost:8000",
        token: Optional[str] = None,
        description: str = "",
        memory_path: Optional[str] = None,
    ):
        super().__init__(agent_id, name, capabilities, platform_url, token, description)

        self.memory_path = memory_path
        self.task_memory: list[dict] = []       # 本地执行记录
        self.capability_scores: dict[str, float] = {c: 0.5 for c in capabilities}
        self.session_start = datetime.now()

    def record_task(
        self,
        task_id: str,
        title: str,
        capability: str,
        success: bool,
        score: float = 0.0,
    ):
        """本地记录一次任务执行"""
        entry = {
            "task_id": task_id,
            "title": title,
            "capability": capability,
            "success": success,
            "score": score,
            "timestamp": datetime.now().isoformat(),
        }
        self.task_memory.append(entry)

        # EWMA 更新能力分
        if capability in self.capability_scores:
            alpha = 0.2
            new_score = 1.0 if success else max(0.0, score)
            old = self.capability_scores[capability]
            self.capability_scores[capability] = alpha * new_score + (1 - alpha) * old

    def get_capability_score(self, capability: str) -> float:
        """获取某能力当前得分"""
        return self.capability_scores.get(capability, 0.0)

    async def connect(self) -> bool:
        """连接时附带能力得分。"""
        self._http = httpx.AsyncClient(
            base_url=self.platform_url,
            timeout=httpx.Timeout(30.0),
        )

        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"

        payload = {
            "agent_id": self.agent_id,
            "name": self.name,
            "capabilities": self.capabilities,
            "endpoints": [
                {
                    "transport": "http",
                    "url": f"{self.platform_url}/harness/{self.agent_id}",
                }
            ],
            "description": self.description or f"Stateful Agent: {self.name}",
            "max_delegations": 10,
            "metadata": {
                "type": "stateful",
                "capability_scores": self.capability_scores,
                "task_count": len(self.task_memory),
                "session_start": self.session_start.isoformat(),
            },
        }

        try:
            resp = await self._http.post(
                "/api/agents/register",
                json=payload,
                headers=headers,
            )
            if resp.status_code in (200, 201):
                self._connected = True
                self._logger.info(
                    "StatefulAgentClient connected — %d tasks, scores: %s",
                    len(self.task_memory),
                    {k: round(v, 3) for k, v in self.capability_scores.items()},
                )
                return True
            self._logger.error(f"Connect failed: {resp.status_code}")
            return False
        except Exception as e:
            self._logger.error(f"Connect error: {e}")
            return False
