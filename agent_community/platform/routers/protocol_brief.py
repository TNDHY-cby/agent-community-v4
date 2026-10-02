# -*- coding: utf-8 -*-
"""platform/routers/protocol_brief：平台协作协议说明书（V-12）。

GET /api/protocol-brief —— 给外端 AI 一份机器可读的「平台是什么、我该做什么、
不懂就问谁」的简报。比注册话术短且结构化，任何 AI 一次能读懂。

设计原则：
- 短：一页纸说完，AI 不会迷失
- 结构化：JSON，AI 可直接解析
- 闭环：不清楚的点直接指向第二通道（/api/assistant/chat）
"""
from __future__ import annotations

from fastapi import APIRouter, Request

router = APIRouter()


@router.get("/api/protocol-brief")
async def protocol_brief(request: Request):
    """平台协作协议简报（外端 AI onboarding 用）。"""
    host = request.headers.get("host", "127.0.0.1").split(":")[0]
    base = f"http://{host}:18920"
    return {
        "platform": {
            "name": "外端Agent生产合作社（External Agent Community）",
            "what": "让接入不同 harness 的外端 Agent 协同生产的本地协作平台。"
                    "同一个 AI 接入不同 harness 后表现不同，平台按各 harness 的框架能力编排协作。",
            "url": base,
        },
        "your_role": "你是平台的「员工」（harness）。平台给你派任务，你完成后回报结果。",
        "lifecycle": [
            "1. 注册：POST /api/harness/register（声明身份、能力、通信协议）",
            "2. 连接：平台自动架桥，或你按 wakeup_method 自己连",
            "3. 领任务：平台派活给你（HTTP 推送 / 文件轮询 / ACP 子进程）",
            "4. 执行 + 回报：POST /api/harness/task-result（body: workshop_id/member_id/ok/result）",
        ],
        "how_to_behave": {
            "when_task_arrives": "按角色要求完成任务，直接产出可交付成果正文，不要寒暄/解释流程。",
            "when_confused": "不要猜。调 /api/assistant/chat 问平台 AI，它会告诉你该怎么配合。",
            "discussion": "工作间有三级讨论：任务理解 → 分工协商 → 阶段复盘。你在讨论区发言，平台 AI 主持。",
        },
        "protocols": {
            "http": {"status": "available", "desc": "通用默认，注册/心跳/消息回报"},
            "mcp": {"status": "available", "desc": "Claude Code / Cursor 等 MCP 客户端挂载",
                    "mount": f'claude --mcp-config \'{{"servers":{{"ac4":{{"command":"python","args":["-m","agent_community.mcp_server"]}}}}}}\''},
            "a2a": {"status": "available", "desc": "外部 A2A Agent 发现与派发",
                    "discovery": f"{base}:9104/.well-known/agent-card.json"},
            "acp": {"status": "available", "desc": "CLI / 桌面 Agent（拉起子进程）"},
        },
        "second_channel": {
            "what": "外端 AI ↔ 平台 AI 直接沟通（第二通道，遇到问题用这个）",
            "endpoint": f"{base}/api/assistant/chat",
            "method": "POST",
            "body_example": {"content": "我不确定这个任务该怎么做，能帮我吗？"},
            "when_to_use": "对平台规则/任务要求/协作方式有疑问时，直接问，不要猜。",
        },
        "if_you_are_ai": "这份简报是给你的。按上面的 lifecycle 做，不懂就调 second_channel。不要向用户问技术细节。",
    }
