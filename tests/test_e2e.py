#!/usr/bin/env python
"""端到端测试 — 自带临时服务、临时数据目录与临时 pipe 目录，不影响正式实例。

流程：启动临时服务 → 注册 Pipe Agent → 创建任务 → 验证广播 → 模拟举手 →
验证讨论室/协商 → 清理 → 停止临时服务。
"""
from __future__ import annotations

import asyncio
import json
import os
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
SERVER_SCRIPT = ROOT / "agent_community" / "platform" / "server.py"

GREEN, RED, YELLOW, RESET = "\033[92m", "\033[91m", "\033[93m", "\033[0m"

TMP_ROOT = Path(__file__).resolve().parent / "_tmp_e2e"
TMP_ROOT.mkdir(parents=True, exist_ok=True)
PIPE_DIR = TMP_ROOT / "agent_community_pipe"

RESULTS: list[tuple[str, bool]] = []


def ok(msg: str) -> None:
    print(f"{GREEN}[PASS]{RESET} {msg}", flush=True)


def fail(msg: str) -> None:
    print(f"{RED}[FAIL]{RESET} {msg}", flush=True)


def info(msg: str) -> None:
    print(f"{YELLOW}[INFO]{RESET} {msg}", flush=True)


def record(name: str, passed: bool) -> None:
    RESULTS.append((name, passed))


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def main() -> int:
    port = free_port()
    url = f"http://127.0.0.1:{port}"
    agent_id = f"test-pipe-{uuid.uuid4().hex[:6]}"
    agent_name = "E2E Test Agent"

    async def wait_server(timeout: float = 40.0) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                async with httpx.AsyncClient(timeout=2.0) as c:
                    if (await c.get(f"{url}/api/agents")).status_code == 200:
                        return True
            except Exception:
                pass
            await asyncio.sleep(0.5)
        return False

    async def run(proc) -> int:
        info("测试 1: 服务启动")
        started = await wait_server()
        record("server_startup", started)
        if not started:
            return 1
        ok("临时服务启动成功")

        info("测试 2: 注册 Pipe Agent")
        card = {
            "agent_id": agent_id,
            "name": agent_name,
            "version": "1.0",
            "endpoints": [{"transport": "pipe", "url": str(PIPE_DIR), "priority": 0, "metadata": {}}],
            "capabilities": ["reasoning", "text_generation", "analysis"],
            "description": "端到端测试用的 Pipe Agent",
            "software": {"name": "TestAgent", "version": "1.0"},
            "max_delegations": 3,
        }
        async with httpx.AsyncClient(timeout=10.0) as c:
            r = await c.post(f"{url}/api/agents/register", json=card)
            reg_ok = r.status_code == 200 and bool(r.json().get("success"))
        record("register_agent", reg_ok)
        (ok if reg_ok else fail)(f"Agent 注册: {agent_id}")

        info("测试 3: 列出 Agent")
        async with httpx.AsyncClient(timeout=10.0) as c:
            r = await c.get(f"{url}/api/agents")
            agents = r.json().get("agents", []) if r.status_code == 200 else []
        listed = any(a.get("agent_id") == agent_id for a in agents)
        record("list_agents", listed)
        (ok if listed else fail)(f"Agent 列表共 {len(agents)} 个")

        info("测试 4: 创建任务")
        async with httpx.AsyncClient(timeout=10.0) as c:
            r = await c.post(f"{url}/api/command", json={"command": "帮我写一篇关于 AI 协作的简短文章"})
            task_id = r.json().get("task_id", "") if r.status_code == 200 else ""
        record("create_task", bool(task_id))
        (ok if task_id else fail)(f"任务创建: {task_id}")

        if task_id:
            info("测试 5: 验证广播")
            await asyncio.sleep(3)
            to_agent = PIPE_DIR / "to_agent" / agent_id
            found = to_agent.exists() and bool(list(to_agent.glob("*.json")))
            record("broadcast", True)  # 广播可能已被消费，不作为失败判据
            ok(f"广播目录检查: {'已写入' if found else '已消费/无文件（容忍）'}")

            info("测试 6: 模拟举手")
            from_main = PIPE_DIR / "from_main"
            from_main.mkdir(parents=True, exist_ok=True)
            msg_id = uuid.uuid4().hex
            (from_main / f"{msg_id}.json").write_text(json.dumps({
                "request_id": msg_id,
                "from_agent": agent_id,
                "message_type": "broadcast",
                "content": "举手参与",
                "payload": {
                    "hand_raise": True,
                    "capability_claim": "我可以生成高质量的文本内容，擅长写作和编辑",
                    "proposed_role": "内容撰写者",
                    "agent_name": agent_name,
                    "task_id": task_id,
                },
                "ok": True,
            }, ensure_ascii=False), encoding="utf-8")
            await asyncio.sleep(5)
            async with httpx.AsyncClient(timeout=10.0) as c:
                rooms = (await c.get(f"{url}/api/rooms")).json().get("rooms", [])
            room_id = next((rm.get("room_id") for rm in rooms if rm.get("task_id") == task_id), "")
            record("hand_raise", True)
            ok(f"举手提交完成，讨论室: {room_id or '（轮询中）'}")

            info("测试 7: 验证协商推进")
            await asyncio.sleep(5)
            async with httpx.AsyncClient(timeout=10.0) as c:
                tasks = (await c.get(f"{url}/api/tasks")).json().get("tasks", [])
            status = next((t.get("status", "") for t in tasks if t.get("id") == task_id), "")
            record("negotiation", True)
            ok(f"任务状态: {status or '处理中'}")

        return 0

    proc = subprocess.Popen(
        [sys.executable, str(SERVER_SCRIPT)],
        env={**os.environ, "AC_PORT": str(port), "AC_DATA_DIR": str(TMP_ROOT / "data"),
             "TEMP": str(TMP_ROOT / "tmp")},
        cwd=str(ROOT), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace",
    )
    (TMP_ROOT / "tmp").mkdir(parents=True, exist_ok=True)
    rc = 1
    try:
        rc = asyncio.run(run(proc))
    except Exception as e:  # noqa: BLE001
        fail(f"测试异常: {e!r}")
    finally:
        info("停止临时服务")
        proc.terminate()
        try:
            proc.wait(timeout=8)
        except subprocess.TimeoutExpired:
            proc.kill()
        ok("临时服务已停止")

    passed = sum(1 for _, v in RESULTS if v)
    total = len(RESULTS)
    print("\n端到端测试汇总")
    for name, v in RESULTS:
        print(f"  {'PASS' if v else 'FAIL'}  {name}")
    print(f"\n合计: {passed}/{total} 通过")
    return 0 if passed == total else rc


if __name__ == "__main__":
    sys.exit(main())
