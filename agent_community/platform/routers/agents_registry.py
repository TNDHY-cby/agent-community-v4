# -*- coding: utf-8 -*-
"""Agent 注册与在线列表（router 端点组）

## 端点（2 个）

```
POST /api/agents/register
GET /api/agents
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

# ★2026-11-01（真机清单 #8：`GET /openapi.json` → 500）：
#   本模块开了 `from __future__ import annotations`（注解全变字符串），但**从没导入 `AgentCard`**
#   ⇒ FastAPI 生成 schema 时拿到 `ForwardRef('AgentCard')` 解不开 ⇒ **整个 openapi 500**，
#   接入者丢掉"按 schema 写客户端"这条权威路径。
#   修法：模块级真导入（`protocol` 不会反向 import routers，无循环）。
from ..protocol import AgentCard

router = APIRouter()

@router.post("/api/agents/register")
async def register_agent(card: AgentCard):
    from ..server import Message, MessageType, _append_hall, agents, bcast_to_clients
    agents[card.agent_id] = card
    sys_msg = Message(
        type=MessageType.SYSTEM,
        from_agent="system",
        content=f"Agent「{card.name}」已注册",
        payload={"agent": card.model_dump()},
    )
    _append_hall(sys_msg)
    await bcast_to_clients(sys_msg)
    return {"success": True, "agent_id": card.agent_id}

@router.get("/api/agents")
async def list_agents(online_only: bool = False):
    from ..server import FRAMEWORK_NOTES, agents, harness_manager, orchestrator, ws_agents
    result = []
    for aid, card in agents.items():
        online = aid in ws_agents or harness_manager.is_harness_agent(aid) or aid == orchestrator.AGENT_ID
        is_harness = harness_manager.is_harness_agent(aid)
        hid = harness_manager.id_to_harness.get(aid, "")
        hs = harness_manager.sessions.get(hid)
        status = "online" if (online or card.endpoints) else "offline"
        if online_only and status != "online":
            continue
        sw = card.software
        sw_name = sw.get("name", "") if sw else ""
        sw_ver = sw.get("version", "") if sw else ""
        software_context = f"{sw_name} {sw_ver}".strip() if (sw_name or sw_ver) else card.description or ""
        # ★2026-10-11（老板要求）：列表要能看出**这个 harness 是什么框架、适配什么大模型**
        #   —— 此前只显示软件名，用户点进去才知道接的是什么。
        #   数据来源就是 harness 自己的注册信息（`harness_type` / `ai.model_name` / `ai.provider`），
        #   不做二次推断，取到什么显示什么；取不到就如实留空（界面不编）。
        _info = getattr(hs, "info", None) if hs else None
        _ai = (getattr(_info, "ai", None) or {}) if _info else {}
        _framework = str(getattr(_info, "harness_type", "") or "") if _info else ""
        _model = str((_ai.get("model_name") if isinstance(_ai, dict) else getattr(_ai, "model_name", "")) or "")
        _provider = str((_ai.get("provider") if isinstance(_ai, dict) else getattr(_ai, "provider", "")) or "")
        _model_ver = str((_ai.get("version") if isinstance(_ai, dict) else getattr(_ai, "version", "")) or "")
        result.append({
            "agent_id": aid, "name": card.name,
            "capabilities": card.capabilities,
            "description": card.description,
            "status": status,
            "software": card.software,
            "software_context": software_context,
            # 框架特点（harness_type）+ 适配大模型（provider/model/version），给"harness 列表"显示
            "framework": _framework,
            "framework_note": FRAMEWORK_NOTES.get(_framework, ""),
            "model": _model,
            "model_provider": _provider,
            "model_version": _model_ver,
            "agent_card": {
                "capabilities": card.capabilities,
                "description": card.description,
                "software": card.software,
            },
            "is_harness": is_harness,
            "harness_id": hid if is_harness else None,
            "harness_status": hs.status.value if hs else None,
        })
    return {"agents": result}
