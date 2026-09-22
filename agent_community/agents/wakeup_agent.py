"""WakeupAgent v6 — 通用 AI 接入层版

v6 改造：
- 不再绑定本地 Ollama
- 构造函数接收 ai_provider: AIProvider（统一 AI 能力接口）
- _classify_hands 通过 self.ai_provider.classify() 做举手判断
- 支持三种后端：OpenAI 兼容系 / Ollama 本地 / HTTP 回调
- 启动入口从环境变量 AC_AI_PROVIDER 决定使用哪个 provider
"""

from __future__ import annotations
import asyncio
import json
import os
import sys
from pathlib import Path

from .pipe_agent import PipeAgent
from ..platform.ai_provider import (
    AIProvider, create_ai_provider,
    OpenAICompatibleProvider, OllamaProvider, HTTPCallbackProvider,
)
from ..platform.ai_external import run_ai_call


class WakeupAgent(PipeAgent):
    """唤醒 Agent — 使用通用 AI 接入层进行举手判断。

    通过文件管道接收平台的广播/唤醒任务消息，使用注入的 ai_provider
    进行 AI 举手判断、任务分发和 Harness 选择。

    AI 能力完全由 ai_provider 接口解耦，支持 OpenAI / Ollama / HTTP 回调。
    """

    def __init__(
        self,
        ai_provider: AIProvider,
        agent_id: str = "wakeup-agent",
        name: str = "WakeupAgent",
        pipe_dir: str | Path = "",
        server_url: str = "http://127.0.0.1:9103",
    ):
        # 使用 ai_provider.classify 作为 LLM 回调
        async def _llm_callback(prompt: str) -> str:
            reply = await run_ai_call(
                ai_provider.chat(
                    system_prompt=(
                        "你是 外端Agent生产合作社（External Agent Community） 平台的唤醒判断 Agent。"
                        "根据广播的任务描述，判断你是否应举手参与。"
                        "回复格式：如果参与，描述你的能力和角色（50字以内）；否则回复 pass。"
                    ),
                    user_message=prompt,
                ),
                label="wakeup.llm_callback",
            )
            return reply

        super().__init__(
            agent_id=agent_id,
            name=name,
            capabilities=["wakeup", "notification", "routing", "waker_selection", "ai_classification"],
            description="唤醒 Agent — 通用 AI 接入层，支持 OpenAI/Ollama/HTTP 回调多种后端",
            pipe_dir=pipe_dir,
            server_url=server_url,
            llm_callback=_llm_callback,
            hand_raise_threshold=0.5,
        )
        self.ai_provider = ai_provider
        self._harness_cache: dict[str, dict] = {}

    # ── 消息路由 ────────────────────────────────────────────────

    async def _handle_message(self, msg_id: str, data: dict):
        """路由扩展：新增 wakeup_task / classify_hands / wakeup_result 消息类型"""
        msg_type = data.get("message_type", "")
        task_id = data.get("task_id", "")
        room_id = data.get("room_id", "")
        payload = data.get("payload", {})

        if msg_type == "wakeup_task":
            await self._on_wakeup_task(msg_id, task_id, payload)
        elif msg_type == "classify_hands":
            await self._on_classify_hands(msg_id, task_id, payload)
        elif msg_type == "wakeup_result":
            await self._on_wakeup_result(msg_id, task_id, payload)
        elif msg_type == "mutual_wakeup":
            await self._on_mutual_wakeup(msg_id, task_id, payload)
        elif msg_type == "broadcast":
            await self._on_broadcast(msg_id, task_id, payload)
        elif msg_type == "negotiation":
            await self._on_negotiation(msg_id, task_id, room_id, payload)
        elif msg_type == "delegation":
            await self._on_delegation(msg_id, task_id, payload)
        else:
            print(f"[{self.name}] 未知消息类型: {msg_type}")

    # ── wakeup_task：唤醒通知 ────────────────────────────────────

    async def _on_wakeup_task(self, msg_id: str, task_id: str, payload: dict):
        """收到平台的唤醒任务通知。记录日志并回执确认。"""
        command = payload.get("command", "")
        harnesses = payload.get("harnesses", [])

        print(
            f"[{self.name}] 唤醒任务通知: task={task_id} "
            f"command={command[:80]} harnesses={len(harnesses)}"
        )

        resp = {
            "request_id": msg_id,
            "from_agent": self.agent_id,
            "message_type": "wakeup_task",
            "content": f"唤醒任务 {task_id} 已收到，共 {len(harnesses)} 个 Harness",
            "payload": {"task_id": task_id, "ack": True, "provider": self.ai_provider.provider_type},
            "ok": True,
        }
        self._write_response(msg_id, resp)

    # ── classify_hands：AI 举手判断 ─────────────────────────────

    async def _on_classify_hands(self, msg_id: str, task_id: str, payload: dict):
        """使用 ai_provider.classify() 判断哪些 Harness 应举手参与任务。

        payload 必须包含:
            - command: str           # 任务描述
            - harnesses: list[dict]  # 候选 Harness 列表 [{id, name, capabilities, ...}]
            - context: str (可选)    # 额外上下文
        """
        command = payload.get("command", "")
        harnesses = payload.get("harnesses", [])
        context = payload.get("context", "")

        if not harnesses:
            resp = {
                "request_id": msg_id,
                "from_agent": self.agent_id,
                "message_type": "classify_hands",
                "content": "无候选 Harness",
                "payload": {"task_id": task_id, "selected": [], "reason": "无候选 Harness"},
                "ok": True,
            }
            self._write_response(msg_id, resp)
            return

        # 构建 candidates 列表供 ai_provider.classify 使用
        candidates = []
        for h in harnesses:
            candidates.append({
                "id": h.get("harness_id", h.get("id", "")),
                "name": h.get("harness_name", h.get("name", "")),
                "capabilities": h.get("capabilities", []),
                "ai_model": h.get("ai_model", ""),
            })

        print(
            f"[{self.name}] AI 举手判断: task={task_id} "
            f"command={command[:80]} candidates={len(candidates)} "
            f"provider={self.ai_provider.provider_type}"
        )

        try:
            result = await run_ai_call(
                self.ai_provider.classify(
                    query=command,
                    candidates=candidates,
                    context=context,
                ),
                label="wakeup.classify",
            )
        except Exception as e:
            print(f"[{self.name}] classify 调用失败: {e}")
            result = {"selected": [], "reason": f"AI Provider 调用失败: {e}"}

        selected = result.get("selected", [])
        reason = result.get("reason", "")

        print(
            f"[{self.name}] 举手判断结果: selected={selected} reason={reason[:80]}"
        )

        resp = {
            "request_id": msg_id,
            "from_agent": self.agent_id,
            "message_type": "classify_hands",
            "content": f"举手判断完成: 选中 {len(selected)} 个 Harness",
            "payload": {
                "task_id": task_id,
                "selected": selected,
                "reason": reason,
                "provider": self.ai_provider.provider_type,
            },
            "ok": True,
        }
        self._write_response(msg_id, resp)

    # ── wakeup_result：唤醒结果汇总 ─────────────────────────────

    async def _on_wakeup_result(self, msg_id: str, task_id: str, payload: dict):
        """接收平台的唤醒结果汇总，记录举手详情。"""
        hands = payload.get("hands", [])
        rejected = payload.get("rejected", [])
        timeouts = payload.get("timeouts", [])
        errors = payload.get("errors", [])

        print(
            f"[{self.name}] 唤醒结果汇总: task={task_id} "
            f"举手={len(hands)} 拒绝={len(rejected)} "
            f"超时={len(timeouts)} 错误={len(errors)}"
        )

        if hands:
            for h in hands:
                print(
                    f"  -- {h.get('harness_name', '?')}: "
                    f"{h.get('capability_claim', '')[:80]} "
                    f"(model={h.get('model_used', '?')})"
                )

        resp = {
            "request_id": msg_id,
            "from_agent": self.agent_id,
            "message_type": "wakeup_result",
            "content": (
                f"唤醒完成: task={task_id} 举手={len(hands)} "
                f"拒绝={len(rejected)} 超时={len(timeouts)} 错误={len(errors)}"
            ),
            "payload": payload,
            "ok": True,
        }
        self._write_response(msg_id, resp)

    # ── mutual_wakeup：跨 Harness 互唤 ──────────────────────────

    async def _on_mutual_wakeup(self, msg_id: str, task_id: str, payload: dict):
        """处理跨 Harness 互唤请求。"""
        from_id = payload.get("from_harness_id", "")
        target_ids = payload.get("target_harness_ids", [])

        print(
            f"[{self.name}] 互唤请求: from={from_id} "
            f"targets={target_ids} task={task_id}"
        )

        resp = {
            "request_id": msg_id,
            "from_agent": self.agent_id,
            "message_type": "mutual_wakeup",
            "content": f"互唤请求 {task_id} 已记录",
            "payload": {
                "task_id": task_id,
                "from_harness_id": from_id,
                "target_count": len(target_ids),
                "ack": True,
            },
            "ok": True,
        }
        self._write_response(msg_id, resp)

    # ── broadcast：WakeupAgent 也做举手判断 ──────────────────────

    async def _on_broadcast(self, msg_id: str, task_id: str, payload: dict):
        """广播消息：使用 ai_provider 判断是否举手参与。"""
        command = payload.get("command", payload.get("prompt", ""))

        print(f"[{self.name}] 收到广播: {command[:80]}")

        # 用 ai_provider.chat 做举手判断
        prompt = (
            f"【新任务广播】\n"
            f"任务ID: {task_id}\n"
            f"任务内容: {command}\n\n"
            f"请评估你是否能参与此任务。如果能，请回复你擅长的部分和提议的角色（50字以内）。"
            f"如果不能，请回复「pass」。"
        )

        reply = await self._llm(prompt)
        is_pass = "pass" in reply.lower()[:20] and len(reply) < 20

        if is_pass:
            print(f"[{self.name}] 不参与任务 {task_id}")
            return

        print(f"[{self.name}] 举手参与任务 {task_id}: {reply[:80]}")

        resp = {
            "request_id": msg_id,
            "from_agent": self.agent_id,
            "message_type": "broadcast",
            "content": reply[:200],
            "payload": {
                "task_id": task_id,
                "hand_raised": True,
                "capability_claim": reply[:200],
            },
            "ok": True,
        }
        self._write_response(msg_id, resp)

    # ── 公共方法：分类举手 ───────────────────────────────────────

    async def classify_hands(
        self,
        task_id: str,
        command: str,
        harnesses: list[dict],
        context: str = "",
    ) -> dict:
        """对外暴露的举手分类方法。

        Args:
            task_id: 任务 ID
            command: 任务描述
            harnesses: Harness 列表
            context: 额外上下文

        Returns:
            {"selected": ["harness_id_1"], "reason": "...", "provider": "..."}
        """
        return await self._classify_hands(task_id, command, harnesses, context)

    async def _classify_hands(
        self,
        task_id: str,
        command: str,
        harnesses: list[dict],
        context: str = "",
    ) -> dict:
        """使用 self.ai_provider.classify() 做 AI 举手判断。

        不再硬编码 Ollama，由注入的 ai_provider 统一处理。
        """
        if not harnesses:
            return {"selected": [], "reason": "无候选 Harness", "provider": self.ai_provider.provider_type}

        candidates = []
        for h in harnesses:
            candidates.append({
                "id": h.get("harness_id", h.get("id", "")),
                "name": h.get("harness_name", h.get("name", "")),
                "capabilities": h.get("capabilities", []),
                "ai_model": h.get("ai_model", ""),
            })

        try:
            result = await run_ai_call(
                self.ai_provider.classify(
                    query=command,
                    candidates=candidates,
                    context=context,
                ),
                label="wakeup.classify",
            )
        except Exception as e:
            result = {"selected": [], "reason": f"AI Provider 异常: {e}"}

        result["provider"] = self.ai_provider.provider_type
        return result


# ═══════════════════════════════════════════════════════════════
# 独立运行入口
# ═══════════════════════════════════════════════════════════════

async def _main():
    import argparse

    parser = argparse.ArgumentParser(description="WakeupAgent v6 — 通用 AI 接入层")
    parser.add_argument(
        "--ai-provider", default="",
        help="AI Provider 类型: openai / ollama / http_callback（默认从 AC_AI_PROVIDER 环境变量读取）"
    )
    parser.add_argument("--ai-model", default="", help="AI 模型名（如 deepseek-chat / qwen2.5:7b）")
    parser.add_argument("--ai-api-key", default="", help="AI API Key")
    parser.add_argument("--ai-base-url", default="", help="AI API Base URL")
    parser.add_argument("--ai-callback-url", default="", help="HTTP 回调 URL（http_callback 类型专用）")
    parser.add_argument("--server-url", default="http://127.0.0.1:9103", help="平台 URL")
    parser.add_argument("--pipe-dir", default="", help="Pipe 目录")

    args = parser.parse_args()

    # 从环境变量或命令行参数决定 provider 类型
    provider_type = args.ai_provider or os.environ.get("AC_AI_PROVIDER", "openai")

    # 创建 AI Provider
    ai_provider = create_ai_provider(
        provider_type=provider_type,
        base_url=args.ai_base_url,
        api_key=args.ai_api_key,
        model=args.ai_model,
        host=args.ai_base_url,  # Ollama 用
        callback_url=args.ai_callback_url,
    )

    print(f"[WakeupAgent v6] 使用 AI Provider: {ai_provider.provider_type}")
    if hasattr(ai_provider, "model"):
        print(f"  模型: {ai_provider.model}")
    if hasattr(ai_provider, "base_url"):
        print(f"  Base URL: {ai_provider.base_url}")

    # Pipe 目录
    pipe_dir = args.pipe_dir or os.path.join(
        os.environ.get("TEMP", str(Path.home() / "AppData" / "Local" / "Temp")),
        "agent_community_pipe",
    )

    agent = WakeupAgent(
        ai_provider=ai_provider,
        pipe_dir=pipe_dir,
        server_url=args.server_url,
    )

    if not await agent.register():
        print("WakeupAgent 注册失败，退出")
        sys.exit(1)

    print(f"[WakeupAgent v6] 启动完成")
    print(f"  Agent ID: {agent.agent_id}")
    print(f"  AI Provider: {ai_provider.provider_type}")
    await agent.run()


if __name__ == "__main__":
    asyncio.run(_main())
