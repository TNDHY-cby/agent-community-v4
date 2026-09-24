"""通用 AI 接入层 — 让平台 Agent 通过标准接口调用任意 AI 后端

设计原则：
1. AIProvider 抽象基类定义统一接口，平台 Agent 不依赖具体后端
2. 首批支持 OpenAI 兼容系、Ollama 本地、HTTP 回调三种
3. 用户拿到平台后只需配 API Key 或换 provider 类型，无需改代码

用法：
    from platform.ai_provider import create_ai_provider

    provider = create_ai_provider("openai")
    # 或从环境变量自动选择:
    provider = create_ai_provider()

    # 通用对话
    reply = await provider.chat("你是助手", "帮我分析这段代码")

    # 举手判断
    result = await provider.classify(
        query="分析用户需求文档",
        candidates=[{"id": "h1", "name": "Trae CN", "capabilities": ["coding"]}],
        context="需要前端开发能力",
    )
"""

from __future__ import annotations
import json
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional


# ═══════════════════════════════════════════════════════════════
# 共享数据结构
# ═══════════════════════════════════════════════════════════════

@dataclass
class ChatResponse:
    """chat_with_tools 的返回结构。
    - content   不为 None 表示 LLM 给出了最终文本回复
    - tool_calls 不为 None 表示 LLM 要求调用工具
    二者互斥：每次返回要么是最终答案，要么是工具调用请求。
    """
    content: str | None = None
    tool_calls: list[dict] | None = None
    # tool_calls 每项: {"id": "call_xxx", "name": "tool_name", "arguments": {...}}


# ═══════════════════════════════════════════════════════════════
# 抽象基类
# ═══════════════════════════════════════════════════════════════

class AIProvider(ABC):
    """AI 后端抽象基类。

    所有 AI 后端必须实现 chat 和 classify 两个核心方法。
    """

    @abstractmethod
    async def chat(self, system_prompt: str, user_message: str) -> str:
        """通用对话：发送 system prompt + user message，返回 AI 回复文本。

        Args:
            system_prompt: 系统提示（角色设定、行为约束等）
            user_message: 用户消息

        Returns:
            AI 回复的纯文本内容
        """
        ...

    @abstractmethod
    async def classify(
        self,
        query: str,
        candidates: list[dict],
        context: str = "",
    ) -> dict:
        """举手判断专用：输入任务描述 + 候选 Harness 列表，输出选中结果。

        Args:
            query: 任务描述（用户下发的命令）
            candidates: 候选 Harness 列表，每项含 id / name / capabilities 等
            context: 额外上下文（如已有在线 Harness 总数等）

        Returns:
            {"selected": ["harness_id_1", "harness_id_2"], "reason": "选择理由"}
        """
        ...

    @abstractmethod
    async def chat_with_tools(
        self,
        messages: list[dict],
        tools: list[dict],
    ) -> ChatResponse:
        """带工具调用的对话接口。

        Args:
            messages: 消息列表，格式同 OpenAI messages。可能包含多轮 tool 角色消息。
            tools: OpenAI 格式的 tools 数组。

        Returns:
            ChatResponse — content 为最终文本，或 tool_calls 为工具调用请求（二者互斥）。
        """
        ...

    @property
    def provider_type(self) -> str:
        """返回 provider 类型标识，子类应覆盖。"""
        return self.__class__.__name__


# ═══════════════════════════════════════════════════════════════
# OpenAI 兼容 Provider（DeepSeek / OpenAI / Claude / Gemini 等）
# ═══════════════════════════════════════════════════════════════

class OpenAICompatibleProvider(AIProvider):
    """适配所有 OpenAI 兼容 API。

    配置方式（优先级：构造参数 > 环境变量 > 默认值）：
    - base_url: 构造参数 > AC_AI_BASE_URL > https://api.deepseek.com
    - api_key:  构造参数 > AC_AI_API_KEY > ""
    - model:    构造参数 > AC_AI_MODEL > deepseek-chat
    """

    def __init__(
        self,
        base_url: str = "",
        api_key: str = "",
        model: str = "",
        reasoning_effort: str = "",
    ):
        self.base_url = (
            base_url
            or os.environ.get("AC_AI_BASE_URL", "")
            or "https://api.deepseek.com"
        )
        self.api_key = (
            api_key
            or os.environ.get("AC_AI_API_KEY", "")
            or ""
        )
        self.model = (
            model
            or os.environ.get("AC_AI_MODEL", "")
            or "deepseek-v4-flash"
        )
        # 思考强度（类 DSH reasoningEfforts）：off / low / medium / high / max
        # 优先级：构造参数 > 环境变量 AC_AI_REASONING_EFFORT > config > 默认 off
        self.reasoning_effort = (
            reasoning_effort
            or os.environ.get("AC_AI_REASONING_EFFORT", "")
            or ""
        )
        if not self.reasoning_effort:
            try:
                from ..config import load_config as _lc
                self.reasoning_effort = str(_lc().get("ai_reasoning_effort", "off"))
            except Exception:
                self.reasoning_effort = "off"
        self.reasoning_effort = self.reasoning_effort.strip().lower()

        # 延迟导入 httpx
        self._client = None

    @property
    def provider_type(self) -> str:
        return "openai"

    def _apply_thinking(self, body: dict) -> dict:
        """按思考强度档位注入 DeepSeek 思考模式参数。

        - off: 不注入（不思考；其它 OpenAI 兼容后端无感知）
        - low:  thinking enabled + reasoning_effort low
        - medium: thinking enabled + reasoning_effort high（官方映射 medium→high）
        - high:  thinking enabled + reasoning_effort high
        - max:   thinking enabled + reasoning_effort max
        """
        eff = self.reasoning_effort
        if eff in ("", "off"):
            return body
        mapping = {
            "low": "low",
            "medium": "high",
            "high": "high",
            "max": "max",
        }
        api_effort = mapping.get(eff)
        if api_effort is None:
            return body
        body["thinking"] = {"type": "enabled"}
        body["reasoning_effort"] = api_effort
        return body

    def _get_client(self):
        if self._client is None:
            import httpx
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(60.0),
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
            )
        return self._client

    async def chat(self, system_prompt: str, user_message: str) -> str:
        client = self._get_client()
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            "temperature": 0.1,
            "max_tokens": 2048,
        }
        self._apply_thinking(body)
        try:
            r = await client.post(f"{self.base_url}/v1/chat/completions", json=body)
            if r.status_code != 200:
                return f"[Error: HTTP {r.status_code}] {r.text[:300]}"
            data = r.json()
            return data["choices"][0]["message"]["content"]
        except Exception as e:
            return f"[Error: {e}]"

    async def classify(
        self,
        query: str,
        candidates: list[dict],
        context: str = "",
    ) -> dict:
        """让 AI 判断哪些候选 Harness 应该举手参与任务。"""
        candidates_text = "\n".join(
            f"  - ID: {c['id']}, 名称: {c.get('name', c['id'])}, "
            f"能力: {', '.join(c.get('capabilities', []))}"
            for c in candidates
        )

        system_prompt = (
            "你是一个任务分发器。根据任务描述和候选 Agent 的能力，判断哪些 Agent 适合参与该任务。\n"
            "规则：\n"
            "1. 只选择能胜任此任务的 Agent（能力匹配）\n"
            "2. 如果没有合适的，返回空列表\n"
            "3. 必须严格按 JSON 格式回复，不要附加任何解释"
        )

        user_message = (
            f"【任务描述】\n{query}\n\n"
            f"【上下文】\n{context}\n\n"
            f"【候选 Agent 列表】\n{candidates_text}\n\n"
            f"请判断哪些 Agent 应举手参与。回复格式：\n"
            f'{{"selected": ["id1", "id2"], "reason": "选择理由（50字以内）"}}'
        )

        reply = await self.chat(system_prompt, user_message)

        # 解析 JSON
        try:
            # 尝试从回复中提取 JSON
            start = reply.find("{")
            end = reply.rfind("}") + 1
            if start >= 0 and end > start:
                data = json.loads(reply[start:end])
                return {
                    "selected": data.get("selected", []),
                    "reason": data.get("reason", reply[:100]),
                }
        except (json.JSONDecodeError, Exception):
            pass

        return {"selected": [], "reason": f"无法解析 AI 回复: {reply[:100]}"}

    async def chat_with_tools(
        self,
        messages: list[dict],
        tools: list[dict],
    ) -> ChatResponse:
        """OpenAI 兼容的带工具调用对话。"""
        client = self._get_client()
        body = {
            "model": self.model,
            "messages": messages,
            "tools": tools,
            "temperature": 0.1,
            "max_tokens": 2048,
        }
        self._apply_thinking(body)
        try:
            r = await client.post(f"{self.base_url}/v1/chat/completions", json=body)
            if r.status_code != 200:
                return ChatResponse(
                    content=f"[Error: HTTP {r.status_code}] {r.text[:300]}",
                )
            data = r.json()
            choice = data["choices"][0]
            msg = choice.get("message", {})

            # 检查 tool_calls
            raw_tool_calls = msg.get("tool_calls")
            if raw_tool_calls:
                parsed = []
                for tc in raw_tool_calls:
                    func = tc.get("function", {})
                    args_str = func.get("arguments", "{}")
                    try:
                        arguments = json.loads(args_str)
                    except json.JSONDecodeError:
                        arguments = {"_raw": args_str}
                    parsed.append({
                        "id": tc.get("id", ""),
                        "name": func.get("name", ""),
                        "arguments": arguments,
                    })
                return ChatResponse(tool_calls=parsed)

            # 纯文本回复
            return ChatResponse(content=msg.get("content", ""))
        except Exception as e:
            return ChatResponse(content=f"[Error: {e}]")

    async def close(self):
        if self._client:
            await self._client.aclose()
            self._client = None


# ═══════════════════════════════════════════════════════════════
# Ollama 本地 Provider
# ═══════════════════════════════════════════════════════════════

class OllamaProvider(AIProvider):
    """本地 Ollama 适配器。

    配置方式（优先级：构造参数 > 环境变量 > 默认值）：
    - host:  构造参数 > AC_OLLAMA_HOST > http://localhost:11434
    - model: 构造参数 > AC_OLLAMA_MODEL > qwen2.5:7b
    """

    def __init__(
        self,
        host: str = "",
        model: str = "",
    ):
        self.host = (
            host
            or os.environ.get("AC_OLLAMA_HOST", "")
            or "http://localhost:11434"
        )
        self.model = (
            model
            or os.environ.get("AC_OLLAMA_MODEL", "")
            or "qwen2.5:7b"
        )
        self._client = None

    @property
    def provider_type(self) -> str:
        return "ollama"

    def _get_client(self):
        if self._client is None:
            import httpx
            self._client = httpx.AsyncClient(timeout=httpx.Timeout(120.0))
        return self._client

    async def chat(self, system_prompt: str, user_message: str) -> str:
        client = self._get_client()
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            "stream": False,
            "options": {"temperature": 0.1},
        }
        try:
            r = await client.post(f"{self.host}/api/chat", json=body)
            if r.status_code != 200:
                return f"[Error: HTTP {r.status_code}] {r.text[:300]}"
            data = r.json()
            return data["message"]["content"]
        except Exception as e:
            return f"[Error: {e}]"

    async def classify(
        self,
        query: str,
        candidates: list[dict],
        context: str = "",
    ) -> dict:
        candidates_text = "\n".join(
            f"  - ID: {c['id']}, 名称: {c.get('name', c['id'])}, "
            f"能力: {', '.join(c.get('capabilities', []))}"
            for c in candidates
        )

        system_prompt = (
            "你是一个任务分发器。根据任务描述和候选 Agent 的能力，判断哪些 Agent 适合参与该任务。\n"
            "只选择能力匹配的 Agent，以 JSON 格式回复："
            '{"selected": ["id1"], "reason": "..."}'
        )

        user_message = (
            f"任务: {query}\n"
            f"上下文: {context}\n"
            f"候选:\n{candidates_text}"
        )

        reply = await self.chat(system_prompt, user_message)

        try:
            start = reply.find("{")
            end = reply.rfind("}") + 1
            if start >= 0 and end > start:
                data = json.loads(reply[start:end])
                return {
                    "selected": data.get("selected", []),
                    "reason": data.get("reason", reply[:100]),
                }
        except (json.JSONDecodeError, Exception):
            pass

        return {"selected": [], "reason": f"无法解析 AI 回复: {reply[:100]}"}

    async def chat_with_tools(
        self,
        messages: list[dict],
        tools: list[dict],
    ) -> ChatResponse:
        """Ollama 本地模型不支持原生 function calling，降级为纯文本对话。"""
        reply = await self.chat("", messages[-1].get("content", ""))
        return ChatResponse(content=reply)

    async def close(self):
        if self._client:
            await self._client.aclose()
            self._client = None


# ═══════════════════════════════════════════════════════════════
# HTTP 回调 Provider
# ═══════════════════════════════════════════════════════════════

class HTTPCallbackProvider(AIProvider):
    """HTTP 回调方式 — 向外部 Harness 的 callback_url POST 请求，让对方 AI 做判断。

    配置方式：
    - callback_url: 构造参数 > AC_HTTP_CALLBACK_URL > ""
    """

    def __init__(self, callback_url: str = ""):
        self.callback_url = (
            callback_url
            or os.environ.get("AC_HTTP_CALLBACK_URL", "")
            or ""
        )
        self._client = None

    @property
    def provider_type(self) -> str:
        return "http_callback"

    def _get_client(self):
        if self._client is None:
            import httpx
            self._client = httpx.AsyncClient(timeout=httpx.Timeout(60.0))
        return self._client

    async def chat(self, system_prompt: str, user_message: str) -> str:
        if not self.callback_url:
            return "[Error: HTTPCallbackProvider 未配置 callback_url]"
        client = self._get_client()
        body = {
            "type": "chat",
            "system_prompt": system_prompt,
            "user_message": user_message,
        }
        try:
            r = await client.post(self.callback_url, json=body)
            if r.status_code != 200:
                return f"[Error: HTTP {r.status_code}] {r.text[:300]}"
            return r.text
        except Exception as e:
            return f"[Error: {e}]"

    async def classify(
        self,
        query: str,
        candidates: list[dict],
        context: str = "",
    ) -> dict:
        if not self.callback_url:
            return {"selected": [], "reason": "HTTPCallbackProvider 未配置 callback_url"}

        client = self._get_client()
        body = {
            "type": "classify",
            "query": query,
            "candidates": candidates,
            "context": context,
            "message": (
                f"【举手判断任务】\n"
                f"任务: {query}\n"
                f"上下文: {context}\n"
                f"候选: {json.dumps(candidates, ensure_ascii=False)}\n\n"
                f'请回复 JSON: {{"selected": ["id1"], "reason": "..."}}'
            ),
        }
        try:
            r = await client.post(self.callback_url, json=body)
            if r.status_code != 200:
                return {"selected": [], "reason": f"HTTP {r.status_code}"}

            reply = r.text
            try:
                start = reply.find("{")
                end = reply.rfind("}") + 1
                if start >= 0 and end > start:
                    data = json.loads(reply[start:end])
                    return {
                        "selected": data.get("selected", []),
                        "reason": data.get("reason", reply[:100]),
                    }
            except (json.JSONDecodeError, Exception):
                pass

            return {"selected": [], "reason": reply[:100]}
        except Exception as e:
            return {"selected": [], "reason": str(e)}

    async def chat_with_tools(
        self,
        messages: list[dict],
        tools: list[dict],
    ) -> ChatResponse:
        """HTTP 回调 Provider 不支持原生 function calling，降级为纯文本对话。"""
        reply = await self.chat("", messages[-1].get("content", ""))
        return ChatResponse(content=reply)

    async def close(self):
        if self._client:
            await self._client.aclose()
            self._client = None


# ═══════════════════════════════════════════════════════════════
# Fake Provider（测试注入，零真实 API 调用）
# ═══════════════════════════════════════════════════════════════

class FakeProvider(AIProvider):
    """测试用假 Provider：按输入 hash 映射固定回复文本，不产生任何真实调用。

    配置：
    - AC_FAKE_REPLY: 固定回复模板（含 {hash} 占位符），默认 "【fake】{hash}"
    - classify 返回空选择 + 固定 reason
    - chat_with_tools 返回纯文本 content
    """

    def __init__(self, reply_template: str = ""):
        self.reply_template = (
            reply_template
            or os.environ.get("AC_FAKE_REPLY", "")
            or "【fake】{hash}"
        )

    @property
    def provider_type(self) -> str:
        return "fake"

    def _hash(self, text: str) -> str:
        import hashlib
        return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]

    def _reply(self, text: str) -> str:
        return self.reply_template.format(hash=self._hash(text))

    async def chat(self, system_prompt: str, user_message: str) -> str:
        print(f"[fake-provider] chat len={len(user_message)}", flush=True)
        return self._reply(user_message)

    async def classify(
        self,
        query: str,
        candidates: list[dict],
        context: str = "",
    ) -> dict:
        print(f"[fake-provider] classify candidates={len(candidates or [])}", flush=True)
        return {"selected": [], "reason": "fake provider 默认不举手"}

    async def chat_with_tools(
        self,
        messages: list[dict],
        tools: list[dict],
    ) -> ChatResponse:
        last = messages[-1].get("content", "") if messages else ""
        print(f"[fake-provider] chat_with_tools tools={len(tools or [])}", flush=True)
        return ChatResponse(content=self._reply(last))

    async def close(self):
        pass


# ═══════════════════════════════════════════════════════════════
# GateProvider（规则闸门包装：命中即短路，未命中转发底层）
# ═══════════════════════════════════════════════════════════════

class GateProvider(AIProvider):
    """在任意 AIProvider 外层包装规则闸门。

    - chat: RuleGate.match 命中 → 直接返回 reply；未命中 → 转发底层
    - classify: RuleGate.match_classify 命中 → 直接返回 data；未命中 → 转发底层
    - chat_with_tools: 取最后一条 user 消息做规则匹配，命中 → 直接返回 content；
      未命中 → 转发底层
    """

    def __init__(self, inner: AIProvider, gate=None):
        self._inner = inner
        if gate is None:
            from .rule_gate import get_default_gate
            gate = get_default_gate()
        self._gate = gate

    @property
    def provider_type(self) -> str:
        return self._inner.provider_type

    async def chat(self, system_prompt: str, user_message: str) -> str:
        hit = self._gate.match(user_message, {"system_prompt": system_prompt})
        if hit is not None and hit.reply is not None:
            return hit.reply
        return await self._inner.chat(system_prompt, user_message)

    async def classify(
        self,
        query: str,
        candidates: list[dict],
        context: str = "",
    ) -> dict:
        hit = self._gate.match_classify(query, candidates, context)
        if hit is not None and hit.data is not None:
            return hit.data
        return await self._inner.classify(query, candidates, context)

    async def chat_with_tools(
        self,
        messages: list[dict],
        tools: list[dict],
    ) -> ChatResponse:
        last_user = ""
        for m in reversed(messages or []):
            if m.get("role") == "user" and isinstance(m.get("content"), str):
                last_user = m["content"]
                break
        hit = self._gate.match(last_user, {})
        if hit is not None and hit.reply is not None:
            return ChatResponse(content=hit.reply)
        return await self._inner.chat_with_tools(messages, tools)

    async def close(self):
        await self._inner.close()


# ═══════════════════════════════════════════════════════════════
# CacheProvider（缓存包装：相同输入复用历史回复，零 API 调用）
# ═══════════════════════════════════════════════════════════════

class CacheProvider(AIProvider):
    """缓存层：只对纯函数类 chat 生效（无工具、无状态副作用）。

    - chat: 命中缓存直接返回；未命中调底层后写缓存
    - classify / chat_with_tools: 含状态副作用，一律不缓存，直接转发
    """

    def __init__(self, inner: AIProvider, cache=None):
        self._inner = inner
        if cache is None:
            from .ai_cache import get_default_cache
            cache = get_default_cache()
        self._cache = cache

    @property
    def provider_type(self) -> str:
        return self._inner.provider_type

    async def chat(self, system_prompt: str, user_message: str) -> str:
        key = self._cache.make_key(system_prompt, user_message, self._inner.provider_type)
        cached = self._cache.get(key)
        if cached is not None:
            print("[ai-cache] hit", flush=True)
            return cached
        reply = await self._inner.chat(system_prompt, user_message)
        # 错误回复（[Error: ...]）不缓存
        if not reply.startswith("[Error:"):
            self._cache.set(key, reply)
        return reply

    async def classify(
        self,
        query: str,
        candidates: list[dict],
        context: str = "",
    ) -> dict:
        return await self._inner.classify(query, candidates, context)

    async def chat_with_tools(
        self,
        messages: list[dict],
        tools: list[dict],
    ) -> ChatResponse:
        return await self._inner.chat_with_tools(messages, tools)

    async def close(self):
        await self._inner.close()


# ═══════════════════════════════════════════════════════════════
# UsageProvider（预算计数 + 熔断包装：保护 API 额度）
# ═══════════════════════════════════════════════════════════════

class UsageProvider(AIProvider):
    """用量层：包裹在真实 Provider 外层，每次调用记录并做预算熔断。

    - 熔断生效时：chat 返回降级提示；classify 返回空选择；chat_with_tools 返回降级 content
    - 正常时：转发底层并 record 估算 token / 费用
    """

    def __init__(self, inner: AIProvider, usage=None):
        self._inner = inner
        if usage is None:
            from .ai_usage import get_default_usage
            usage = get_default_usage()
        self._usage = usage

    @property
    def provider_type(self) -> str:
        return self._inner.provider_type

    def _blocked_reply(self) -> str:
        return (
            "[预算熔断] 今日或本月 API 额度已用尽，AI 调用已暂停。"
            "可在 config 调高 AC_BUDGET_DAILY / AC_BUDGET_MONTHLY 后重启恢复。"
        )

    async def chat(self, system_prompt: str, user_message: str) -> str:
        if self._usage.blocked():
            print("[ai-usage] blocked chat", flush=True)
            return self._blocked_reply()
        reply = await self._inner.chat(system_prompt, user_message)
        if not reply.startswith("[Error:"):
            self._usage.record(
                self._inner.provider_type,
                system_prompt + user_message,
                reply,
            )
        return reply

    async def classify(
        self,
        query: str,
        candidates: list[dict],
        context: str = "",
    ) -> dict:
        if self._usage.blocked():
            print("[ai-usage] blocked classify", flush=True)
            return {"selected": [], "reason": "预算熔断，暂停 AI 判断"}
        result = await self._inner.classify(query, candidates, context)
        self._usage.record(self._inner.provider_type, query + context, str(result))
        return result

    async def chat_with_tools(
        self,
        messages: list[dict],
        tools: list[dict],
    ) -> ChatResponse:
        if self._usage.blocked():
            print("[ai-usage] blocked chat_with_tools", flush=True)
            return ChatResponse(content=self._blocked_reply())
        resp = await self._inner.chat_with_tools(messages, tools)
        text = resp.content or ""
        if not text.startswith("[Error:"):
            self._usage.record(
                self._inner.provider_type,
                "".join(m.get("content", "") for m in (messages or []) if isinstance(m.get("content"), str)),
                text,
            )
        return resp

    async def close(self):
        await self._inner.close()


# ═══════════════════════════════════════════════════════════════
# 工厂函数
# ═══════════════════════════════════════════════════════════════

def create_ai_provider(
    provider_type: str = "",
    **kwargs,
) -> AIProvider:
    """创建 AI Provider 实例。

    优先级：provider_type 参数 > 环境变量 AC_AI_PROVIDER > 默认 "openai"

    Args:
        provider_type: "openai" / "ollama" / "http_callback"。留空则从环境变量读取。
        **kwargs: 传递给具体 Provider 构造函数的参数
            - openai: base_url, api_key, model
            - ollama: host, model
            - http_callback: callback_url

    Returns:
        AIProvider 实例
    """
    if not provider_type:
        provider_type = os.environ.get("AC_AI_PROVIDER", "openai")

    provider_type = provider_type.lower().strip()

    # 外部接管（manual）/ 纯规则降级（off）：延迟导入，避免模块循环依赖
    if provider_type in ("local", "auto"):
        # 内 AI 直连本机推理端点（llama.cpp / Ollama 等）；不可用时按需回退
        try:
            from . import local_ai as _lai

            _t = _lai.detect()
            if _t:
                _base = _t["base"][:-3] if _t["base"].endswith("/v1") else _t["base"]
                print(f"[ai-provider] 内 AI 已切本机端点: {_t['backend']} "
                      f"{_t['model']} @ {_base}", flush=True)
                return OpenAICompatibleProvider(
                    base_url=_base, api_key="local", model=_t["model"])
        except Exception as _le:
            print(f"[ai-provider] 本机端点探测失败: {str(_le)[:100]}", flush=True)
        if provider_type == "local":
            raise RuntimeError("本机无可用推理端点，无法启用 ai_mode=local")
        provider_type = "openai"

    if provider_type in ("manual", "off"):
        from .ai_external import create_external_provider

        return create_external_provider(
            provider_type,
            timeout_s=kwargs.get("manual_timeout"),
        )

    if provider_type == "fake":
        inner = FakeProvider(reply_template=kwargs.get("reply_template", ""))
    elif provider_type in ("openai", "deepseek", "zhipu", "qwen", "moonshot"):
        inner: AIProvider = OpenAICompatibleProvider(
            base_url=kwargs.get("base_url", ""),
            api_key=kwargs.get("api_key", ""),
            model=kwargs.get("model", ""),
            reasoning_effort=kwargs.get("reasoning_effort", ""),
        )
    elif provider_type == "ollama":
        inner = OllamaProvider(
            host=kwargs.get("host", ""),
            model=kwargs.get("model", ""),
        )
    elif provider_type == "http_callback":
        inner = HTTPCallbackProvider(
            callback_url=kwargs.get("callback_url", ""),
        )
    else:
        raise ValueError(
            f"不支持的 AI Provider 类型: {provider_type}。"
            f"支持: openai / ollama / http_callback / fake"
        )

    # 统一叠加链（自外向内）：规则闸门 → 缓存 → 用量熔断 → 真实后端
    # 顺序说明：Gate 最外（规则可判一律零成本）；Cache 次之（缓存命中零调用）；
    # Usage 贴近真实后端（只对真实花销计数与熔断）
    # 开关：config 的 ai_cache_enabled / ai_usage_enabled，可用环境变量 AC_AI_CACHE_ENABLED / AC_AI_USAGE_ENABLED 覆盖
    if kwargs.get("no_gate"):
        return inner
    cache_enabled = True
    usage_enabled = True
    try:
        from ..config import load_config as _lc
        _cfg = _lc()
        cache_enabled = bool(_cfg.get("ai_cache_enabled", True))
        usage_enabled = bool(_cfg.get("ai_usage_enabled", True))
    except Exception:
        pass
    cache_enabled = os.environ.get("AC_AI_CACHE_ENABLED", "1" if cache_enabled else "0") == "1"
    usage_enabled = os.environ.get("AC_AI_USAGE_ENABLED", "1" if usage_enabled else "0") == "1"

    wrapped: AIProvider = inner
    if usage_enabled:
        wrapped = UsageProvider(wrapped)
    if cache_enabled:
        wrapped = CacheProvider(wrapped)
    return GateProvider(wrapped)
