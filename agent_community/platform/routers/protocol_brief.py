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
    # ★2026-10-17 修（可移植性）：这里原来写死 `:18920` —— 陌生人换端口跑（`AC_PORT=8080`）时，
    #   简报里**每一条 URL 都是错的**（照做必然连不上），属"只在我这台机器成立"的典型。
    #   现在走平台唯一的地址解析点（`platform_address`），并保留 Host 头作为首选。
    try:
        from ...platform_address import base_url as _base_url
        _resolved = _base_url()
    except Exception:  # noqa: BLE001
        _resolved = ""
    if _resolved:
        base = _resolved
    else:                                   # 兜底：按请求 Host 拼（端口取 Host 里的端口）
        port = request.headers.get("host", "").split(":")[-1] or ""
        base = f"http://{host}:{port}" if port.isdigit() else f"http://{host}"
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
            "3. **激活回报（不能跳过）**：被指派进工作间后，回报 "
            "POST /api/harness/activation-result —— "
            "body: {workshop_id, member_id, status:'entered'}；"
            "平台要等**全员 entered** 才开始派任务，你不回报 = 工作间不开工",
            "   可选三字段（向后兼容，不发也行，发了更准确）：\n"
            "     session_id     本次会话 id —— 平台据此**记住你的会话**（缺了会失忆）\n"
            "     context_turns  会话已存在的上下文轮数，新会话应为 1；"
            "                    若远大于 1，平台会记一条「上下文复用」审计但**不阻断**\n"
            "     source         来源：acp/mcp/plugin=机器报，human=人工点击进入"
            "（平台区分人报与机器报，避免冒充）",
            "4. 领任务：平台派活给你（HTTP 推送 / 文件轮询 / ACP 子进程）",
            "5. 执行 + 回报：POST /api/harness/task-result（body: workshop_id/member_id/ok/result；"
            "若工作间开了辩论模式，可**在同一层**再带 claim/evidence/reasoning —— 见 how_to_behave.debate_statements）",
        ],
        "activation_result": {
            "endpoint": f"{base}/api/harness/activation-result",
            "method": "POST",
            "required": {"workshop_id": "工作间 id", "member_id": "成员 id",
                         "status": "entered / blocked / 其它"},
            "optional": {
                "session_id": "本次会话 id（缺则平台无法跨重启记住会话）",
                "context_turns": "上下文轮数，新会话应为 1；>1 会被记审计但不阻断",
                "source": "acp / mcp / plugin / human（无 session_id 时平台会合成 id 并标 human）",
                "reason": "status=blocked 时的原因，会显示给用户",
                "harness_id": "你的 harness_id（用于平台登记会话归属）",
            },
            "note": "三字段均为可选；老版本不发也能正常工作（向后兼容）。",
        },
        "how_to_behave": {
            "when_task_arrives": "按角色要求完成任务，直接产出可交付成果正文，不要寒暄/解释流程。",
            "when_confused": "不要猜。调 /api/assistant/chat 问平台 AI，它会告诉你该怎么配合。",
            "discussion": "工作间有三级讨论：任务理解 → 分工协商 → 阶段复盘。你在讨论区发言，平台 AI 主持。",
            # ★2026-10-15（V52）/ ★2026-10-17 补齐（V52 §8 欠账："回报模板同步"）：
            #   原来这里只有一句话"建议写成三段：结论/证据/推理"—— **没给字段名、没说写在哪**，
            #   成员照它写也进不了辩论池（解析器要的是顶层 `claim`）。现在给成**可照抄的契约**：
            #   字段名/位置/形状/示例一次说清；`example` 由守卫**真喂给解析器**验证（不许漂移）。
            "debate_statements": {
                "when": "**只在这个工作间开了「辩论模式」时才需要**（三级讨论的开关，**默认关**）。"
                        "没开就完全不用管 —— 普通回报格式一个字都不用改。",
                "where": "写在 `POST /api/harness/task-result` 的 body **顶层**"
                         "（与 workshop_id / member_id / ok / result 同级，不是嵌在 result 里）。",
                "fields": {
                    "claim": "一句话结论。**只有带了这个键，平台才当你发了一条陈述**；"
                             "不带 = 普通回报，行为与以前完全一样（向后兼容）。",
                    "evidence": "证据数组，每项 `{kind, ref, verified}`：`kind` 自由文本"
                                "（url / doc / tool / file / commit …），`ref` 是**来源身份**"
                                "（同一 URL 带不同 #fragment、大小写不同 ⇒ 判为同一来源）；"
                                "也可直接给字符串数组（等价于 `kind=url, verified=false`）。"
                                "**可以不给** —— 没证据的陈述会被记录、但权重为 0。",
                    "reasoning": "这些证据为什么支持这个结论（≤2000 字，可省）。",
                    "conflicts_with_checker": "可选布尔。你的结论与工具/计算/校验器的返回**互斥**时"
                                              "如实标 true（会被扣分且不进结论候选 —— 别硬撑）。",
                },
                "rules": "判据是**证据来源**，不是票数：同源的陈述会被**合并成一个簇**"
                         "（总权重仍是 1，不是 2）；无证据 ⇒ 权重 0（**话不会被删**，原话留在讨论区）；"
                         "与校验器冲突 ⇒ 扣分且不进结论候选。**这不是投票**，只看证据站不站得住。",
                "example": {
                    "workshop_id": "<工作间 id>", "member_id": "<你的成员 id>", "ok": True,
                    "result": "我把桥的超时窗口从 30s 提到 60s 并复验通过",
                    "claim": "把超时窗口提到 60s 能消掉这条假失败",
                    "evidence": [
                        {"kind": "file", "ref": "agent_community/platform/bridge_supervisor.py",
                         "verified": True},
                        {"kind": "test", "ref": "agent_community/tests/test_bridge_watch_probe.py",
                         "verified": True},
                    ],
                    "reasoning": "该用例在 30s 下稳定失败、60s 下稳定通过，且失败都发生在等待窗口内。",
                },
                "note": "证据也可以直接复用**完成声明**那套材料（`commit` / `file` / `test` 的值当 `ref`）——"
                        "平台没有第二套字段名。",
            },
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
            "body": {"message": "你的问题"},
            "body_example": {"message": "我不确定这个任务该怎么做，能帮我吗？"},
            "note": "入参键名是 message（服务端亦兼容 content 别名）。",
            "when_to_use": "对平台规则/任务要求/协作方式有疑问时，直接问，不要猜。",
        },
        "if_you_are_ai": "这份简报是给你的。按上面的 lifecycle 做，不懂就调 second_channel。不要向用户问技术细节。",
    }
