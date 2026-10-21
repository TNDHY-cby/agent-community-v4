# -*- coding: utf-8 -*-
"""助手会话一族（router 端点组）

## 端点（2 个）

```
POST /api/assistant/chat
GET /api/assistant/history
```

## 为什么在 `routers/` 里

2026-10-21 从 `server.py` 整块搬出（审计点名"巨无霸"结构债）。**函数体原样搬，行为不变**；
`server` 里的全局在**函数体内惰性 import**（模块级 import 会与 `server` 形成循环，见 `routers/docking.py`）；
注册走 `app.include_router`。

搬出工具：`python tools/move_endpoints.py`（可复用，别手工搬）。
"""
from __future__ import annotations

import json

from fastapi import APIRouter, Request

router = APIRouter()

@router.post("/api/assistant/chat")
async def api_assistant_chat(request: Request):
    """平台助手持续会话：用户在 register 页直聊窗口与平台 Agent 对话。
    body: {"message": "..."}
    维护 assistant_history 全局上下文（含 system 注入的 harness 接入通知），
    复用 ReActLoop + ToolRegistry，工具集含通用文件/网页工具及
    list_harness / launch_harness / bridge_test 三个 Harness 运维工具。
    返回 {"reply": "...", "steps": [...]}。
    """
    from ..server import Message, MessageType, Utf8JSONResponse, ai_provider, assistant_history, assistant_history_max, bcast_to_clients
    body = await request.json()
    # V-14 修正：兼容 content 别名。
    # 2026-10-03 实测发现：V-12 的 protocol_brief.py 与 register.html 第二通道示例
    # 把入参写成了 {"content": "..."}，而本端点只认 message —— 外部 AI 照着平台
    # 自己发的简报调用会直接 400。既有对外文档已发布，故此处**兼容两种键名**，
    # 而不是只改文档（避免已按旧文档接入的一方踩坑）。
    message = (body.get("message") or body.get("content") or "").strip()
    if not message:
        # 2026-10-15：原来是英文 `message required` —— 与 register 那两处同一个毛病
        #（V34 §9.2：错误一律中文；第一次接入的人看不懂英文报错就卡住）。
        return Utf8JSONResponse({"error": "缺少必填字段 message（你想问平台助手的问题）"}, status_code=400)
    from .react_loop import ReActLoop
    from .tool_registry import ToolRegistry
    from .tools import (
        ReadFileTool, WriteFileTool, ShellExecTool, WebFetchTool,
        GenerateBridgeTool, ListBridgeTemplatesTool,
        ListHarnessTool, LaunchHarnessTool, BridgeTestTool, ProbeHarnessTool,
        FindWindowTool,
    )
    tr = ToolRegistry()
    tr.register_many([
        ReadFileTool(), WriteFileTool(), ShellExecTool(), WebFetchTool(),
        GenerateBridgeTool(), ListBridgeTemplatesTool(),
        ListHarnessTool(), LaunchHarnessTool(), BridgeTestTool(), ProbeHarnessTool(),
        FindWindowTool(),
    ])
    system_prompt = (
        "你是 外端Agent生产合作社（External Agent Community） 平台的内置 AI 助手（平台 Agent）。"
        "当前处于与用户直聊的持续会话窗口，用户可能是想与你沟通外部 Harness 的接入与架桥。"
        "你可以调用工具来完成用户的任务：读取/写入文件、执行命令、抓取网页、"
        "生成桥脚本、列出/启动 Harness、对桥做实时测试、探测外部对象接入方式等。"
        "对话历史中带有 [系统] 前缀的消息是平台注入的 Harness 接入通知，说明有哪些外部 Agent 已接入，"
        "请据此掌握目标信息并配合用户完成沟通与架桥。"
        "当用户要接入一个新的外部 Agent 但说不清技术细节时，用 probe_harness 工具探测它"
        "（给进程名/端口/路径线索即可），摸清它是 HTTP API 型、文件型还是 ACP 型，"
        "然后给出推荐的注册配置（wakeup_method + api_base_url 等），帮助用户完成接入。"
        # ★2026-10-15（FE-26 §3.4）：视觉激活的"定位"这一步 —— 与上一条同一个写法（规则 20）
        "当用户要**视觉激活**某个 agent（把它从桌面上叫起来）时：先向他确认几个**会出现在那个窗口标题里**"
        "的关键词，用 find_window 工具去定位，把候选窗口（标题/进程/匹配命中）如实报给他，"
        "让他在界面上点选；**不要自己注入、不要替他点** —— 试投与保存都由他在界面上按按钮完成。"
        "定位不到就把关键词放宽一点重试，或让他先把那个 agent 的窗口打开再找你。"
        "优先使用工具获取信息，然后给出准确回答，使用中文回复。"
    )
    # 每步工具调用推送给前端（前端已连 /ws，走 message 事件）
    async def on_step(step):
        for tc in step.tool_calls:
            evt = Message(
                type=MessageType.EVENT, from_agent="orchestrator",
                content=f"🔧 调用工具: {tc['name']}",
                agent_name="平台助手",
                payload={"event": "assistant_tool_call", "tool_name": tc["name"],
                         "tool_args": tc.get("arguments", {})},
            )
            await bcast_to_clients(evt)
    try:
        loop = ReActLoop(tr, ai_provider, max_steps=10, on_step=on_step)
        result = await loop.run(system_prompt, message, history=list(assistant_history))
    except Exception as e:
        return Utf8JSONResponse({"error": f"平台助手调用失败: {e}"}, status_code=500)
    # 写入持续会话历史
    assistant_history.append({"role": "user", "content": message})
    assistant_history.append({"role": "assistant", "content": result.final_answer})
    if len(assistant_history) > assistant_history_max:
        del assistant_history[:-assistant_history_max]
    steps_summary = []
    for st in result.steps:
        for tc in st.tool_calls:
            steps_summary.append({
                "step": st.step,
                "tool": tc["name"],
                "args": tc.get("arguments", {}),
            })
    return Utf8JSONResponse({
        "success": True,
        "reply": result.final_answer,
        "steps": steps_summary,
        "history_len": len(assistant_history),
    })

@router.get("/api/assistant/history")
async def api_assistant_history():
    """返回平台助手持续会话历史（含系统注入的 harness 接入通知）。"""
    from ..server import Utf8JSONResponse, assistant_history
    return Utf8JSONResponse({
        "success": True,
        "history": list(assistant_history),
    })
