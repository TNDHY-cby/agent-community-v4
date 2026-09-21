#!/usr/bin/env python3
"""waker_with_deepseek.py — 独立示例脚本

演示启动 WakeupAgent 并配置 DeepSeek API，运行完整的 wakeup 流程。

用法：
    # 使用环境变量配置
    export AC_AI_PROVIDER=openai
    export AC_AI_API_KEY=sk-your-deepseek-key
    export AC_AI_MODEL=deepseek-chat
    python examples/waker_with_deepseek.py

    # 使用命令行参数
    python examples/waker_with_deepseek.py \
        --ai-provider openai \
        --ai-api-key sk-your-deepseek-key \
        --ai-model deepseek-chat

    # 使用 OpenAI
    python examples/waker_with_deepseek.py \
        --ai-provider openai \
        --ai-api-key sk-your-openai-key \
        --ai-model gpt-4o \
        --ai-base-url https://api.openai.com

    # 使用本地 Ollama
    python examples/waker_with_deepseek.py \
        --ai-provider ollama \
        --ai-model qwen2.5:7b
"""

from __future__ import annotations
import asyncio
import json
import os
import sys
from pathlib import Path

# 添加 agent-community-v4 到 path
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from platform.ai_provider import create_ai_provider, AIProvider
from agents.wakeup_agent import WakeupAgent


async def test_ai_provider(provider: AIProvider):
    """测试 AI Provider 的基本功能"""
    print("\n" + "=" * 60)
    print("测试 AI Provider 基础功能")
    print("=" * 60)

    # 测试 chat
    print(f"\n[1] chat 测试 ({provider.provider_type})...")
    reply = await provider.chat(
        system_prompt="你是一个简洁的助手，回答不超过20字。",
        user_message="1+1等于几？",
    )
    print(f"  回复: {reply[:200]}")

    # 测试 classify
    print(f"\n[2] classify 测试 ({provider.provider_type})...")
    result = await provider.classify(
        query="帮我写一个 Python Web 爬虫",
        candidates=[
            {"id": "agent-1", "name": "CodeAgent", "capabilities": ["coding", "python"]},
            {"id": "agent-2", "name": "BrowserAgent", "capabilities": ["browser", "scraping"]},
            {"id": "agent-3", "name": "DocAgent", "capabilities": ["writing", "documentation"]},
        ],
        context="需要从网页抓取数据并保存为 CSV",
    )
    print(f"  选中: {result.get('selected', [])}")
    print(f"  理由: {result.get('reason', '')[:200]}")

    print("\n[OK] AI Provider 基础功能测试通过")


async def test_wakeup_flow(provider: AIProvider):
    """测试完整的 wakeup 流程"""
    print("\n" + "=" * 60)
    print("测试 Wakeup Agent 完整流程")
    print("=" * 60)

    pipe_dir = Path(os.environ.get("TEMP", ".")) / "agent_community_pipe_test"
    pipe_dir.mkdir(parents=True, exist_ok=True)

    agent = WakeupAgent(
        ai_provider=provider,
        agent_id="wakeup-agent-test",
        name="WakeupAgent-Test",
        pipe_dir=pipe_dir,
        server_url="http://127.0.0.1:9103",
    )

    # 测试 _classify_hands
    print(f"\n[1] 举手分类测试...")
    harnesses = [
        {
            "harness_id": "示例Harness-D",
            "harness_name": "示例 IDE Agent",
            "capabilities": ["coding", "file_ops", "browser"],
            "ai_model": "your-model",
        },
        {
            "harness_id": "cursor-001",
            "harness_name": "Cursor IDE",
            "capabilities": ["coding", "refactoring", "debugging"],
            "ai_model": "gpt-4o",
        },
        {
            "harness_id": "ollama-wrapper-001",
            "harness_name": "Ollama Wrapper",
            "capabilities": ["conversation", "translation", "summarization"],
            "ai_model": "qwen2.5:7b",
        },
    ]

    result = await agent.classify_hands(
        task_id="demo-task-001",
        command="重构 user_service.py 中的认证逻辑，并补充单元测试",
        harnesses=harnesses,
        context="代码库使用 Python FastAPI，已有机房部署",
    )

    print(f"  选中的 Harness: {result.get('selected', [])}")
    print(f"  理由: {result.get('reason', '')[:200]}")
    print(f"  使用的 Provider: {result.get('provider', '?')}")

    print("\n[OK] Wakeup Agent 完整流程测试通过")


async def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="WakeupAgent + DeepSeek 独立示例",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python examples/waker_with_deepseek.py --ai-api-key sk-xxx
  python examples/waker_with_deepseek.py --ai-provider ollama --ai-model qwen2.5:7b
  python examples/waker_with_deepseek.py --ai-provider openai --ai-api-key sk-xxx --ai-model gpt-4o --ai-base-url https://api.openai.com
        """,
    )
    parser.add_argument(
        "--ai-provider", default="",
        help="AI Provider 类型: openai / ollama / http_callback（默认从 AC_AI_PROVIDER 环境变量读取，fallback=openai）"
    )
    parser.add_argument("--ai-model", default="", help="AI 模型名")
    parser.add_argument("--ai-api-key", default="", help="AI API Key")
    parser.add_argument("--ai-base-url", default="", help="AI API Base URL")
    parser.add_argument("--ai-callback-url", default="", help="HTTP 回调 URL")
    parser.add_argument("--server-url", default="http://127.0.0.1:9103", help="平台 URL")
    parser.add_argument("--skip-tests", action="store_true", help="跳过功能测试，直接注册到平台")

    args = parser.parse_args()

    # 创建 AI Provider
    provider_type = args.ai_provider or os.environ.get("AC_AI_PROVIDER", "openai")

    print("=" * 60)
    print("WakeupAgent v6 — 通用 AI 接入层 独立示例")
    print("=" * 60)
    print(f"  AI Provider: {provider_type}")
    if args.ai_model:
        print(f"  Model: {args.ai_model}")
    if args.ai_base_url:
        print(f"  Base URL: {args.ai_base_url}")
    print("=" * 60)

    # 检查 API Key（仅 OpenAI 需要）
    if provider_type == "openai" and not args.ai_api_key and not os.environ.get("AC_AI_API_KEY"):
        print("\n[WARN] 未设置 AI API Key")
        print("  - 通过 --ai-api-key 参数传入")
        print("  - 或设置环境变量 AC_AI_API_KEY")
        print("  - DeepSeek: https://platform.deepseek.com/api_keys")
        print("  - OpenAI:   https://platform.openai.com/api-keys")
        print("\n  跳过功能测试，仅演示 Provider 信息...")

    provider = create_ai_provider(
        provider_type=provider_type,
        base_url=args.ai_base_url,
        api_key=args.ai_api_key,
        model=args.ai_model,
        host=args.ai_base_url,
        callback_url=args.ai_callback_url,
    )

    print(f"\n[INFO] Provider 类型: {provider.provider_type}")
    if hasattr(provider, "model"):
        print(f"[INFO] 模型: {provider.model}")
    if hasattr(provider, "base_url"):
        print(f"[INFO] Base URL: {provider.base_url}")

    if args.skip_tests:
        print("\n[INFO] 跳过测试，直接注册到平台...")
        pipe_dir = Path(os.environ.get("TEMP", ".")) / "agent_community_pipe"
        agent = WakeupAgent(
            ai_provider=provider,
            pipe_dir=pipe_dir,
            server_url=args.server_url,
        )
        if await agent.register():
            print(f"[OK] WakeupAgent 已注册到平台 ({args.server_url})")
            await agent.run()
        else:
            print("[ERROR] 注册失败")
            sys.exit(1)

    # 运行功能测试
    try:
        await test_ai_provider(provider)
        await test_wakeup_flow(provider)
    finally:
        if hasattr(provider, "close"):
            await provider.close()

    print("\n" + "=" * 60)
    print("示例脚本执行完毕")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
