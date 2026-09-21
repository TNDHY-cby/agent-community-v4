#!/usr/bin/env python3
"""Agent Community v4 — 一键启动脚本

启动流程：
1. 启动 platform/server.py（uvicorn，默认端口 9103）
2. 预注册内置 Agent
3. 可选启动 Ollama Agent
4. 自动打开浏览器到 http://127.0.0.1:9103

用法：
    python start.py                     # 默认启动
    python start.py --port 9200         # 指定端口
    python start.py --no-browser        # 不打开浏览器
    python start.py --ollama            # 同时启动 Ollama Agent
    python start.py --ollama-model qwen2.5:14b  # 指定 Ollama 模型
    python start.py --wakeup            # 同时启动 Wakeup Agent（需 Ollama）
"""

from __future__ import annotations
import argparse
import asyncio
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

ROOT = Path(__file__).parent
PLATFORM_DIR = ROOT / "platform"
SERVER_SCRIPT = PLATFORM_DIR / "server.py"


def parse_args():
    parser = argparse.ArgumentParser(description="Agent Community v4 启动器")
    parser.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    parser.add_argument("--gui", action="store_true", default=True, help="启用桌面 GUI 窗口（默认开启）")
    parser.add_argument("--no-gui", action="store_true", help="禁用桌面 GUI，纯后台运行")
    parser.add_argument("--port", type=int, default=9103, help="服务器端口（默认 9103）")
    parser.add_argument("--ollama", action="store_true", help="同时启动 Ollama Agent")
    parser.add_argument("--ollama-model", type=str, default="qwen2.5:7b", help="Ollama 模型名（默认 qwen2.5:7b）")
    parser.add_argument("--ollama-host", type=str, default="http://127.0.0.1:11434", help="Ollama 服务地址")
    parser.add_argument("--wakeup", action="store_true", help="同时启动 Wakeup Agent（通用 AI 接入层）")
    parser.add_argument("--wakeup-timeout", type=float, default=60.0, help="唤醒超时秒数")
    # v6 新增：通用 AI 接入层参数
    parser.add_argument("--ai-provider", type=str, default="", help="AI Provider 类型: openai / ollama / http_callback（默认从 AC_AI_PROVIDER 环境变量读取）")
    parser.add_argument("--ai-model", type=str, default="", help="AI 模型名（如 deepseek-chat / qwen2.5:7b）")
    parser.add_argument("--ai-api-key", type=str, default="", help="AI API Key")
    parser.add_argument("--ai-base-url", type=str, default="", help="AI API Base URL")
    parser.add_argument("--ai-callback-url", type=str, default="", help="HTTP 回调 URL（http_callback 类型专用）")
    return parser.parse_args()


def check_server_script() -> bool:
    if not SERVER_SCRIPT.exists():
        print(f"[ERROR] 找不到 server.py: {SERVER_SCRIPT}")
        return False
    return True


def check_port_available(port: int) -> bool:
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", port))
        s.close()
        return True
    except OSError:
        return False


def check_ollama(args) -> bool:
    """检查 Ollama 是否可用"""
    import json
    try:
        import httpx
        r = httpx.get(f"{args.ollama_host}/api/tags", timeout=10.0)
        if r.status_code != 200:
            print(f"[WARN] Ollama 服务不可用: HTTP {r.status_code}")
            return False
        models = [m["name"] for m in r.json().get("models", [])]
        base = args.ollama_model.split(":")[0]
        available = [m for m in models if m.startswith(base)]
        if available:
            print(f"[OK] Ollama 可用模型: {', '.join(available[:5])}")
            return True
        else:
            print(f"[WARN] 未找到模型 {args.ollama_model}，可用模型: {', '.join(models[:10])}")
            return False
    except Exception as e:
        print(f"[WARN] 无法连接 Ollama: {e}")
        return False


async def start_ollama_agent(args):
    """启动 Ollama Agent 子进程"""
    agent_script = ROOT / "agents" / "ollama_agent.py"
    if not agent_script.exists():
        print("[WARN] 找不到 agents/ollama_agent.py，跳过 Ollama Agent")
        return None

    print(f"[INFO] 启动 Ollama Agent (模型: {args.ollama_model})...")
    proc = subprocess.Popen(
        [sys.executable, str(agent_script),
         "--server-url", f"http://127.0.0.1:{args.port}",
         "--ollama-host", args.ollama_host,
         "--ollama-model", args.ollama_model],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    return proc


def main():
    args = parse_args()

    # 决定是否使用 GUI
    use_gui = args.gui and not args.no_gui

    if not check_server_script():
        sys.exit(1)

    # GUI 模式
    if use_gui:
        sys.path.insert(0, str(ROOT))
        from agent_community.gui import AgentCommunityApp
        app = AgentCommunityApp(
            port=args.port,
            ai_provider=args.ai_provider,
            ai_model=args.ai_model,
            ai_api_key=args.ai_api_key,
            ai_base_url=args.ai_base_url,
        )
        app.run()
        return

    # 无 GUI 模式：后台运行 + 浏览器

    # 检查端口占用
    if not check_port_available(args.port):
        print(f"[ERROR] 端口 {args.port} 已被占用，请使用 --port 指定其他端口")
        sys.exit(1)

    print("=" * 60)
    print("Agent Community Platform v4")
    print("=" * 60)
    print(f"  端口: {args.port}")
    print(f"  地址: http://127.0.0.1:{args.port}")
    print(f"  Pipe 目录: {Path.home() / 'AppData' / 'Local' / 'Temp' / 'agent_community_pipe'}")
    print("=" * 60)

    # 构建环境变量
    server_env = {**__import__("os").environ, "AC_PORT": str(args.port)}
    if args.ai_provider:
        server_env["AC_AI_PROVIDER"] = args.ai_provider
    if args.ai_model:
        server_env["AC_AI_MODEL"] = args.ai_model
    if args.ai_api_key:
        server_env["AC_AI_API_KEY"] = args.ai_api_key
    if args.ai_base_url:
        server_env["AC_AI_BASE_URL"] = args.ai_base_url
    if args.ai_callback_url:
        server_env["AC_HTTP_CALLBACK_URL"] = args.ai_callback_url

    # 启动 server
    print("[INFO] 启动平台服务...")
    server_proc = subprocess.Popen(
        [sys.executable, str(SERVER_SCRIPT)],
        env=server_env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )

    # 等待 server 就绪
    print("[INFO] 等待服务就绪...")
    max_wait = 15
    for i in range(max_wait):
        time.sleep(1)
        if not check_port_available(args.port):
            print("[OK] 服务已启动")
            break
    else:
        print("[ERROR] 服务启动超时")
        server_proc.terminate()
        sys.exit(1)

    # 启动 Ollama Agent
    ollama_proc = None
    wakeup_proc = None
    if args.ollama:
        if check_ollama(args):
            try:
                ollama_proc = subprocess.Popen(
                    [sys.executable, str(ROOT / "agents" / "ollama_agent.py"),
                     "--server-url", f"http://127.0.0.1:{args.port}",
                     "--ollama-host", args.ollama_host,
                     "--ollama-model", args.ollama_model],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                )
                print(f"[OK] Ollama Agent 已启动 (模型: {args.ollama_model})")
            except Exception as e:
                print(f"[WARN] Ollama Agent 启动失败: {e}")
        else:
            print("[WARN] 跳过 Ollama Agent（Ollama 不可用）")

    # 启动 Wakeup Agent（v6：通用 AI 接入层）
    if args.wakeup:
        wakeup_script = ROOT / "agents" / "wakeup_agent.py"
        if wakeup_script.exists():
            # 构建 Wakeup Agent 的命令行参数
            wakeup_args = [
                sys.executable, str(wakeup_script),
                "--server-url", f"http://127.0.0.1:{args.port}",
                "--wakeup-timeout", str(args.wakeup_timeout),
            ]
            if args.ai_provider:
                wakeup_args += ["--ai-provider", args.ai_provider]
            if args.ai_model:
                wakeup_args += ["--ai-model", args.ai_model]
            if args.ai_api_key:
                wakeup_args += ["--ai-api-key", args.ai_api_key]
            if args.ai_base_url:
                wakeup_args += ["--ai-base-url", args.ai_base_url]
            if args.ai_callback_url:
                wakeup_args += ["--ai-callback-url", args.ai_callback_url]

            provider_info = args.ai_provider or __import__("os").environ.get("AC_AI_PROVIDER", "openai")
            model_info = args.ai_model or __import__("os").environ.get("AC_AI_MODEL", "default")

            try:
                wakeup_proc = subprocess.Popen(
                    wakeup_args,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                )
                print(f"[OK] Wakeup Agent 已启动 (Provider: {provider_info}, Model: {model_info})")
            except Exception as e:
                print(f"[WARN] Wakeup Agent 启动失败: {e}")
        else:
            print(f"[WARN] 找不到 {wakeup_script}，跳过 Wakeup Agent")

    # 打开浏览器
    if not args.no_browser:
        url = f"http://127.0.0.1:{args.port}"
        print(f"[INFO] 打开浏览器: {url}")
        webbrowser.open(url)

    print("\n[INFO] 服务运行中，按 Ctrl+C 停止...\n")

    try:
        # 保持运行并打印 server 日志
        while True:
            if server_proc.poll() is not None:
                print("[ERROR] 服务器异常退出")
                break
            # 打印 Ollama Agent 输出
            if ollama_proc and ollama_proc.poll() is not None:
                print("[WARN] Ollama Agent 已退出")
                ollama_proc = None
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n[INFO] 正在停止服务...")
    finally:
        server_proc.terminate()
        if ollama_proc:
            ollama_proc.terminate()
        if wakeup_proc:
            wakeup_proc.terminate()
        print("[INFO] 已停止所有服务")


if __name__ == "__main__":
    main()
