"""ReAct 循环引擎 — Thought → Action → Observation。

核心流程：
  1. 组装 messages（system + user + 历史 tool 往返）
  2. 调用 LLM（带 tools 参数）
  3. 有 tool_calls → 逐个执行 → 结果追加到 messages → 回到步骤 2
  4. 纯文本 content → 循环结束，返回最终回复
  5. 达到 max_steps → 强制终止并要求 LLM 总结
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Callable, Awaitable, Optional

from .tool_registry import ToolRegistry, ToolResult
from .ai_provider import AIProvider, ChatResponse
from .ai_external import run_ai_call


@dataclass
class LoopStep:
    """ReAct 单步记录。"""
    step: int
    tool_calls: list[dict]           # 本轮的 tool_calls（可能多个）
    tool_results: list[ToolResult]   # 对应的执行结果
    thinking_content: str = ""       # LLM 在工具调用中附带的思考文本（如有）


@dataclass
class LoopResult:
    """ReAct 循环的最终结果。"""
    final_answer: str
    steps: list[LoopStep] = field(default_factory=list)
    step_count: int = 0


# 回调类型：每步通知外部
StepCallback = Callable[[LoopStep], Awaitable[None]]


class ReActLoop:
    """Thought → Action → Observation 循环引擎。

    用法:
        loop = ReActLoop(registry, provider, max_steps=10, on_step=callback)
        result = await loop.run(system_prompt, user_message)
        print(result.final_answer)
    """

    def __init__(
        self,
        tool_registry: ToolRegistry,
        provider: AIProvider,
        max_steps: int = 10,
        on_step: Optional[StepCallback] = None,
    ):
        self.tool_registry = tool_registry
        self.provider = provider
        self.max_steps = max_steps
        self.on_step = on_step

    async def run(
        self,
        system: str,
        user: str,
        history: Optional[list[dict]] = None,
    ) -> LoopResult:
        """执行 ReAct 循环，返回最终结果。

        history: 可选，之前轮次的对话消息（role/content），插入 system 之后、user 之前，
                用于持续会话场景。
        """
        steps: list[LoopStep] = []

        # 初始化 messages
        messages: list[dict] = [
            {"role": "system", "content": self._build_system_prompt(system)},
        ]
        if history:
            messages.extend(list(history))
        messages.append({"role": "user", "content": user})

        tools = self.tool_registry.get_openai_schemas()

        for i in range(self.max_steps):
            # 调用 LLM
            resp: ChatResponse = await run_ai_call(
                self.provider.chat_with_tools(messages, tools),
                label=f"react.step{i + 1}",
            )

            # 情况1: LLM 返回纯文本 → 结束
            if resp.content is not None:
                return LoopResult(
                    final_answer=resp.content,
                    steps=steps,
                    step_count=i,
                )

            # 情况2: LLM 返回 tool_calls → 执行工具
            if resp.tool_calls:
                tool_results: list[ToolResult] = []
                thinking = ""

                # 收集 assistant 消息中的思考内容（如有）
                # 某些模型在 tool_calls 前会输出 thinking

                # 逐个执行工具调用
                for tc in resp.tool_calls:
                    result = await self.tool_registry.execute(
                        name=tc["name"],
                        tool_call_id=tc["id"],
                        params=tc.get("arguments", {}),
                    )
                    tool_results.append(result)

                step = LoopStep(
                    step=i + 1,
                    tool_calls=resp.tool_calls,
                    tool_results=tool_results,
                    thinking_content=thinking,
                )
                steps.append(step)

                # 通知外部（回调）
                if self.on_step:
                    await self.on_step(step)

                # 将 assistant 消息（含 tool_calls）追加到 messages
                assistant_msg = {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": tc["id"],
                            "type": "function",
                            "function": {
                                "name": tc["name"],
                                "arguments": _to_json_string(tc.get("arguments", {})),
                            },
                        }
                        for tc in resp.tool_calls
                    ],
                }
                messages.append(assistant_msg)

                # 将 tool 结果追加到 messages
                for tc, result in zip(resp.tool_calls, tool_results):
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "content": result.content,
                    })
            else:
                # 异常情况：既无 content 也无 tool_calls
                return LoopResult(
                    final_answer="[内部错误: LLM 返回了空的 content 和 tool_calls]",
                    steps=steps,
                    step_count=i,
                )

        # 达到最大步数 → 强制要求 LLM 总结
        messages.append({
            "role": "user",
            "content": "已达到最大执行步数限制。请根据上述工具调用结果，直接给出最终答案，不要再调用工具。",
        })
        resp = await run_ai_call(
            self.provider.chat_with_tools(messages, []),  # 不带 tools 强制纯文本
            label="react.final_summary",
        )
        final = resp.content if resp.content else "[达到最大步数限制，但未能获得最终答案]"

        return LoopResult(
            final_answer=final,
            steps=steps,
            step_count=len(steps),
        )

    def _build_system_prompt(self, base_system: str) -> str:
        """在原有 system prompt 基础上追加工具使用规范。"""
        tool_names = self.tool_registry.list_names()
        tools_desc = "、".join(tool_names) if tool_names else "（无）"

        tool_notice = (
            f"\n\n## 可用工具\n"
            f"你拥有以下工具的调用能力：{tools_desc}。\n"
            f"使用规则：\n"
            f"1. 需要获取信息或执行操作时，先调用对应工具\n"
            f"2. 工具执行结果会返回给你，你可以据此继续分析或调用其他工具\n"
            f"3. 所有工具调用完成后，给出最终答案\n"
            f"4. 工具调用失败时，尝试其他方式或如实告知用户"
        )

        return base_system + tool_notice


def _to_json_string(obj: dict) -> str:
    """将 dict 转为 JSON 字符串，兼容 API 格式要求。"""
    import json
    return json.dumps(obj, ensure_ascii=False)
