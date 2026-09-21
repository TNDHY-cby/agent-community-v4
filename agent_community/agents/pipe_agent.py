"""Pipe Agent 基类 — 通过文件系统与平台通信的通用 Agent

设计原则：
1. 独立运行，不依赖 server 进程（通过文件系统通信）
2. 内置完整的广播→举手→协商→委托生命周期处理
3. LLM 通过回调函数注入，支持任意后端

通信机制：
- 平台 → Agent: PIPE_DIR/to_agent/{agent_id}/*.json（平台写入）
- Agent → 平台: PIPE_DIR/from_main/*.json（Agent 写入，平台轮询读取）

消息格式（与 platform/protocol.py 对齐）：
  请求: {"message_id", "message_type", "task_id", "room_id", "payload"}
  响应: {"request_id", "from_agent", "message_type", "content", "payload", "ok"}
"""

from __future__ import annotations
import asyncio
import json
import sys
import time
import uuid
from pathlib import Path
from typing import Callable, Awaitable, Optional

import httpx


# ── LLM 回调类型 ────────────────────────────────────────────────
# 注入 LLM 能力的回调函数签名
LLMCallback = Callable[[str], Awaitable[str]]
"""async def my_llm(prompt: str) -> str: ..."""


# ── PipeAgent 基类 ──────────────────────────────────────────────

class PipeAgent:
    """通过文件管道与平台通信的通用 Agent 基类。

    子类只需注入 llm_callback 即可获得完整的广播→举手→协商→委托能力。
    """

    def __init__(
        self,
        agent_id: str,
        name: str,
        capabilities: list[str],
        description: str,
        pipe_dir: str | Path,
        server_url: str = "http://127.0.0.1:9103",
        llm_callback: Optional[LLMCallback] = None,
        hand_raise_threshold: float = 0.5,
    ):
        self.agent_id = agent_id
        self.name = name
        self.capabilities = capabilities
        self.description = description
        self.server_url = server_url.rstrip("/")
        self.pipe_dir = Path(pipe_dir)
        self.to_agent_dir = self.pipe_dir / "to_agent" / agent_id
        self.from_main_dir = self.pipe_dir / "from_main"

        # LLM 回调（子类注入）
        self._llm = llm_callback or self._default_llm

        # 举手阈值（0~1），agent 自我评估能力匹配度低于此值则 pass
        self.hand_raise_threshold = hand_raise_threshold

        # 确保目录存在
        self.to_agent_dir.mkdir(parents=True, exist_ok=True)
        self.from_main_dir.mkdir(parents=True, exist_ok=True)

        # 已处理的消息 ID（去重）
        self._processed_ids: set[str] = set()

    # ── 默认 LLM（无 AI 时返回简单回复）────────────────────────

    async def _default_llm(self, prompt: str) -> str:
        """无 LLM 注入时的兜底：返回空白回复表示不参与"""
        return "pass"

    # ── HTTP 客户端 ──────────────────────────────────────────────

    def _http(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=30.0)

    # ── 注册 ────────────────────────────────────────────────────

    async def register(self):
        """向平台注册 AgentCard"""
        card = {
            "agent_id": self.agent_id,
            "name": self.name,
            "version": "1.0",
            "endpoints": [
                {
                    "transport": "pipe",
                    "url": str(self.pipe_dir),
                    "priority": 0,
                    "metadata": {},
                }
            ],
            "capabilities": self.capabilities,
            "description": self.description,
            "software": {"name": "PipeAgent", "version": "1.0"},
            "max_delegations": 3,
        }
        try:
            async with self._http() as client:
                r = await client.post(
                    f"{self.server_url}/api/agents/register",
                    json=card,
                )
                if r.status_code == 200:
                    print(f"[{self.name}] 注册成功")
                    return True
                else:
                    print(f"[{self.name}] 注册失败: HTTP {r.status_code}")
                    return False
        except Exception as e:
            print(f"[{self.name}] 注册失败: {e}")
            return False

    # ── 主循环 ──────────────────────────────────────────────────

    async def run(self, poll_interval: float = 2.0):
        """主循环：轮询消息 → 处理 → 响应"""
        print(f"[{self.name}] 开始轮询 {self.to_agent_dir}")
        while True:
            try:
                await self._poll_and_process()
            except Exception as e:
                print(f"[{self.name}] 轮询异常: {e}")
            await asyncio.sleep(poll_interval)

    async def _poll_and_process(self):
        """扫描 to_agent 目录并处理所有消息"""
        if not self.to_agent_dir.exists():
            return

        files = sorted(self.to_agent_dir.glob("*.json"))
        for f in files:
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                msg_id = data.get("message_id", "")
                f.unlink()  # 消费后删除

                if msg_id in self._processed_ids:
                    continue
                self._processed_ids.add(msg_id)
                # 只保留最近 1000 条
                if len(self._processed_ids) > 1000:
                    self._processed_ids = set(list(self._processed_ids)[-500:])

                # 异步处理（不阻塞轮询）
                asyncio.create_task(self._handle_message(msg_id, data))
            except Exception as e:
                print(f"[{self.name}] 处理消息失败: {e}")
                try:
                    f.unlink()
                except Exception:
                    pass

    # ── 消息路由 ────────────────────────────────────────────────

    async def _handle_message(self, msg_id: str, data: dict):
        """根据消息类型路由到对应处理器"""
        msg_type = data.get("message_type", "")
        task_id = data.get("task_id", "")
        room_id = data.get("room_id", "")
        payload = data.get("payload", {})

        if msg_type == "broadcast":
            await self._on_broadcast(msg_id, task_id, payload)
        elif msg_type == "negotiation":
            await self._on_negotiation(msg_id, task_id, room_id, payload)
        elif msg_type == "delegation":
            await self._on_delegation(msg_id, task_id, payload)
        else:
            print(f"[{self.name}] 未知消息类型: {msg_type}")

    # ── 广播处理 ────────────────────────────────────────────────

    async def _on_broadcast(self, msg_id: str, task_id: str, payload: dict):
        """收到广播 → 自我评估 → 举手或 pass"""
        command = payload.get("command", payload.get("content", ""))
        prompt = payload.get("prompt", "")

        # 构建自我评估 prompt
        eval_prompt = (
            f"你是一个名为「{self.name}」的 AI Agent，具备以下能力：{', '.join(self.capabilities)}。\n"
            f"你的描述：{self.description}\n\n"
            f"现在收到一条任务广播：\n"
            f"任务ID: {task_id}\n"
            f"任务内容: {command}\n"
            f"详细说明: {prompt}\n\n"
            f"请评估你是否能参与此任务。如果能，请回复你擅长的部分和提议的角色（50字以内）。"
            f"如果不能，请只回复「pass」。"
        )

        try:
            response = await self._llm(eval_prompt)
        except Exception as e:
            print(f"[{self.name}] LLM 评估失败: {e}")
            return

        response = response.strip()
        is_pass = "pass" in response.lower()[:20] and len(response) < 20

        if is_pass:
            print(f"[{self.name}] 放弃举手: {command[:50]}")
            return

        # 举手
        hand_content = response[:200] if len(response) > 200 else response
        print(f"[{self.name}] 举手参与: {hand_content[:80]}")

        resp = {
            "request_id": msg_id,
            "from_agent": self.agent_id,
            "message_type": "broadcast",
            "content": f"🙋 {self.name} 举手参与",
            "payload": {
                "hand_raise": True,
                "capability_claim": hand_content,
                "proposed_role": self.description[:50],
                "agent_name": self.name,
                "task_id": task_id,
            },
            "ok": True,
        }
        self._write_response(msg_id, resp)

    # ── 协商处理 ────────────────────────────────────────────────

    async def _on_negotiation(self, msg_id: str, task_id: str, room_id: str, payload: dict):
        """进入讨论室 → 生成分工提案或回复"""
        context = payload.get("context", "")
        participants = payload.get("participants", [])

        parts = [f"你已进入讨论室 {room_id}。"]
        if context:
            parts.append(context)

        negotiate_prompt = "\n".join(parts) + (
            f"\n\n你是 Agent「{self.name}」(ID: {self.agent_id})，"
            f"具备能力：{', '.join(self.capabilities)}。\n"
            f"请基于你的能力，提出一个分工提案（JSON 格式）：\n"
            f'{{"action":"propose","title":"你的提案标题","content":"你的分工思路。说明各 Agent 分别做什么、为什么这样分工、依赖关系如何。",'
            f'"assignments":{{"agent_id":{{"task_title":"子任务标题","task_description":"详细描述",'
            f'"capability_required":"所需能力","expected_output":"期望输出"}}}},"dependencies":[["前置agent","后置agent"]]}}\n'
            f"如果已有提案但你不同意，可以用 action=amend 或 action=object。"
        )

        try:
            response = await self._llm(negotiate_prompt)
        except Exception as e:
            print(f"[{self.name}] 协商 LLM 调用失败: {e}")
            response = json.dumps({
                "action": "speak",
                "content": f"{self.name} 参与协商但无法生成详细提案。"
            })

        print(f"[{self.name}] 协商回复: {response[:120]}")

        resp = {
            "request_id": msg_id,
            "from_agent": self.agent_id,
            "message_type": "negotiation",
            "content": response,
            "payload": {
                "task_id": task_id,
                "room_id": room_id,
            },
            "ok": True,
        }
        self._write_response(msg_id, resp)

    # ── 委托处理 ────────────────────────────────────────────────

    async def _on_delegation(self, msg_id: str, task_id: str, payload: dict):
        """收到委托 → 执行 → 汇报结果"""
        delegation_id = payload.get("delegation_id", msg_id)
        title = payload.get("title", "")
        description = payload.get("description", "")
        prompt_text = payload.get("prompt", "")

        exec_prompt = (
            f"你收到一个委托任务，请立即执行：\n"
            f"委托ID: {delegation_id}\n"
            f"任务标题: {title}\n"
            f"任务描述: {description}\n"
            f"详细说明: {prompt_text}\n\n"
            f"作为「{self.name}」，你的能力：{', '.join(self.capabilities)}。\n"
            f"请给出你对此任务的最佳回应。"
        )

        try:
            result = await self._llm(exec_prompt)
        except Exception as e:
            result = f"执行失败: {e}"

        print(f"[{self.name}] 委托执行完成: {title[:60]}")

        resp = {
            "request_id": msg_id,
            "from_agent": self.agent_id,
            "message_type": "delegation",
            "content": result,
            "payload": {
                "delegation_id": delegation_id,
                "task_id": task_id,
                "ok": True,
            },
            "ok": True,
        }
        self._write_response(msg_id, resp)

    # ── 响应写入 ────────────────────────────────────────────────

    def _write_response(self, request_id: str, data: dict):
        """写入响应到 PIPE_DIR/from_main/"""
        resp_path = self.from_main_dir / f"{request_id}.json"
        try:
            resp_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        except Exception as e:
            print(f"[{self.name}] 写入响应失败: {e}")


# ═══════════════════════════════════════════════════════════════
# 独立运行入口（无 LLM 的简单 Agent）
# ═══════════════════════════════════════════════════════════════

async def _main():
    """示例：使用默认 LLM（pass-only）启动 Agent"""
    import argparse
    import os

    parser = argparse.ArgumentParser(description="Pipe Agent")
    parser.add_argument("--agent-id", default="pipe-demo", help="Agent ID")
    parser.add_argument("--name", default="PipeDemo", help="Agent 名称")
    parser.add_argument("--server-url", default="http://127.0.0.1:9103", help="平台 URL")
    parser.add_argument("--pipe-dir", default="", help="Pipe 目录（默认 TEMP/agent_community_pipe）")
    args = parser.parse_args()

    pipe_dir = args.pipe_dir or os.path.join(
        os.environ.get("TEMP", str(Path.home() / "AppData" / "Local" / "Temp")),
        "agent_community_pipe",
    )

    agent = PipeAgent(
        agent_id=args.agent_id,
        name=args.name,
        capabilities=["general", "conversation"],
        description="一个简单的 Pipe Agent 示例",
        pipe_dir=pipe_dir,
        server_url=args.server_url,
    )

    if not await agent.register():
        print("注册失败，退出")
        sys.exit(1)

    await agent.run()


if __name__ == "__main__":
    asyncio.run(_main())
