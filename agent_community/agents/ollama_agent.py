"""Ollama Agent — 基于 PipeAgent 的本地 LLM Agent

通过 Ollama 本地推理服务（默认 http://127.0.0.1:11434）提供 LLM 能力，
完整参与广播→举手→协商→委托全流程。

用法：
    python ollama_agent.py --server-url http://127.0.0.1:9103 --ollama-host http://127.0.0.1:11434 --ollama-model qwen2.5:7b
"""

from __future__ import annotations
import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

import httpx

from .pipe_agent import PipeAgent


class OllamaAgent(PipeAgent):
    """基于 Ollama 本地 LLM 的 Pipe Agent"""

    def __init__(
        self,
        agent_id: str,
        name: str,
        capabilities: list[str],
        description: str,
        pipe_dir: str | Path,
        server_url: str = "http://127.0.0.1:9103",
        ollama_host: str = "http://127.0.0.1:11434",
        ollama_model: str = "qwen2.5:7b",
        temperature: float = 0.7,
        max_tokens: int = 2048,
    ):
        self.ollama_host = ollama_host.rstrip("/")
        self.ollama_model = ollama_model
        self.temperature = temperature
        self.max_tokens = max_tokens

        # 注入 Ollama LLM 回调
        super().__init__(
            agent_id=agent_id,
            name=name,
            capabilities=capabilities,
            description=description,
            pipe_dir=pipe_dir,
            server_url=server_url,
            llm_callback=self._ollama_llm,
        )

    async def _ollama_llm(self, prompt: str) -> str:
        """调用 Ollama API 生成回复"""
        url = f"{self.ollama_host}/api/generate"
        payload = {
            "model": self.ollama_model,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": self.temperature,
                "num_predict": self.max_tokens,
            },
        }
        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                r = await client.post(url, json=payload)
                if r.status_code == 200:
                    data = r.json()
                    return data.get("response", "").strip()
                else:
                    print(f"[{self.name}] Ollama 错误: HTTP {r.status_code}")
                    return "pass"
        except Exception as e:
            print(f"[{self.name}] Ollama 调用失败: {e}")
            return "pass"

    @classmethod
    def detect_models(cls, ollama_host: str) -> list[str]:
        """检测本地 Ollama 可用模型"""
        try:
            r = httpx.get(f"{ollama_host.rstrip('/')}/api/tags", timeout=10.0)
            if r.status_code == 200:
                return [m["name"] for m in r.json().get("models", [])]
        except Exception:
            pass
        return []

    @classmethod
    def select_model(cls, ollama_host: str, preferred: str = "qwen2.5:7b") -> str:
        """自动选择可用模型：优先 preferred，否则取第一个"""
        models = cls.detect_models(ollama_host)
        if not models:
            return preferred

        # 精确匹配
        if preferred in models:
            return preferred

        # 前缀匹配（如 qwen2.5:7b 匹配 qwen2.5:latest）
        base = preferred.split(":")[0]
        for m in models:
            if m.startswith(base):
                return m

        # 回退到第一个
        return models[0]


async def _main():
    parser = argparse.ArgumentParser(description="Ollama Pipe Agent")
    parser.add_argument("--agent-id", default="ollama-agent", help="Agent ID")
    parser.add_argument("--name", default="Ollama Agent", help="Agent 名称")
    parser.add_argument("--server-url", default="http://127.0.0.1:9103", help="平台 URL")
    parser.add_argument("--ollama-host", default="http://127.0.0.1:11434", help="Ollama 服务地址")
    parser.add_argument("--ollama-model", default="qwen2.5:7b", help="Ollama 模型名")
    parser.add_argument("--pipe-dir", default="", help="Pipe 目录（默认 TEMP/agent_community_pipe）")
    args = parser.parse_args()

    pipe_dir = args.pipe_dir or os.path.join(
        os.environ.get("TEMP", str(Path.home() / "AppData" / "Local" / "Temp")),
        "agent_community_pipe",
    )

    # 自动选择模型
    model = OllamaAgent.select_model(args.ollama_host, args.ollama_model)
    print(f"[INFO] 使用 Ollama 模型: {model}")

    agent = OllamaAgent(
        agent_id=args.agent_id,
        name=args.name,
        capabilities=["reasoning", "text_generation", "analysis"],
        description="基于 Ollama 本地 LLM 的通用 Agent，支持推理、文本生成和分析",
        pipe_dir=pipe_dir,
        server_url=args.server_url,
        ollama_host=args.ollama_host,
        ollama_model=model,
    )

    if not await agent.register():
        print("注册失败，退出")
        sys.exit(1)

    await agent.run()


if __name__ == "__main__":
    asyncio.run(_main())
