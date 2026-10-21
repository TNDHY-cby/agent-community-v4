# -*- coding: utf-8 -*-
"""platform/routers/rooms_discuss：**讨论室一族**（2026-10-21 从 `server.py` 整块搬出）。

## 这里面是什么（9 个端点）

```
GET  /api/room/{room_id}                             看一间讨论室的状态与消息
GET  /api/room/by-task/{task_id}                     按任务找它那间
POST /api/room/{room_id}/message                     往讨论室发一条消息
POST /api/room/{room_id}/proposal                    提交提案
POST /api/room/{room_id}/proposal/prefill            AI 读讨论记录，预填提案字段
POST /api/room/{room_id}/vote                        投票
POST /api/room/{room_id}/consensus                   强制形成共识（采纳票数最高的提案）
POST /api/room/{room_id}/proposal/{pid}/edit         改自己的提案（未采纳前）
POST /api/room/{room_id}/proposal/{pid}/withdraw     撤回自己的提案（未采纳前）
```

## 为什么这么搬（不是重写）

- **函数体原样搬**：搬家不引入行为变化，判据是"同样的请求，同样的响应"；
- **`server` 里的全局在函数体内惰性 import**：`server.py` 在导入期就会 `include` 各 router，
  模块级 `from ..server import …` 会形成循环（仓库既有约定，见 `routers/docking.py`）；
- 注册走 `routers/rooms.py` 聚合器的 `routes.extend`（不用 `include_router`，否则路由自省看不到真实路径）。

## 状态在哪

`discussion_rooms` / `tasks` / WebSocket 广播仍是**一处真相**，都还在 `server.py`；
本模块只**读/改**它们，不新造第二份。
"""
from __future__ import annotations

import json

from fastapi import APIRouter, Request

router = APIRouter()

@router.get("/api/room/{room_id}")
async def get_room(room_id: str):
    from ..server import Utf8JSONResponse, discussion_rooms
    if room_id not in discussion_rooms:
        return Utf8JSONResponse({"error": "room not found"}, status_code=404)
    return discussion_rooms[room_id].model_dump()

@router.get("/api/room/by-task/{task_id}")
async def get_room_by_task(task_id: str):
    from ..server import Utf8JSONResponse, discussion_rooms, task_to_room
    room_id = task_to_room.get(task_id)
    if not room_id or room_id not in discussion_rooms:
        return Utf8JSONResponse({"error": "room not found"}, status_code=404)
    return discussion_rooms[room_id].model_dump()

@router.post("/api/room/{room_id}/message")
async def room_message(room_id: str, request: Request):
    """Agent 或用户向讨论室发送消息"""
    from ..server import DiscMessageType, DiscussionMessage, Message, MessageType, Utf8JSONResponse, bcast_to_clients, discussion_rooms
    if room_id not in discussion_rooms:
        return Utf8JSONResponse({"error": "room not found"}, status_code=404)
    body = await request.json()
    content = body.get("content", "").strip()
    agent_id = body.get("agent_id", "user")
    agent_name = body.get("agent_name", "用户")
    msg_type = body.get("msg_type", "speak")
    if not content:
        return Utf8JSONResponse({"error": "消息不能为空"}, status_code=400)
    room = discussion_rooms[room_id]
    disc_msg = DiscussionMessage(
        room_id=room_id, agent_id=agent_id, agent_name=agent_name,
        msg_type=DiscMessageType(msg_type) if msg_type in DiscMessageType.__members__ else DiscMessageType.SPEAK,
        content=content,
        ref_proposal_id=body.get("ref_proposal_id", ""),
    )
    room.messages.append(disc_msg)
    await bcast_to_clients(Message(
        type=MessageType.ROOM_MESSAGE, from_agent=agent_id,
        content=content, task_id=room.task_id, room_id=room_id,
        payload={"event": "room_message", "disc_message": disc_msg.model_dump()},
    ))
    return {"success": True}

@router.post("/api/room/{room_id}/proposal")
async def submit_proposal(room_id: str, request: Request):
    """提交分工提案"""
    from ..server import DiscMessageType, DiscussionMessage, Message, MessageType, TaskProposal, Utf8JSONResponse, bcast_to_clients, discussion_rooms, save_state
    if room_id not in discussion_rooms:
        return Utf8JSONResponse({"error": "room not found"}, status_code=404)
    body = await request.json()
    room = discussion_rooms[room_id]
    proposal = TaskProposal(
        room_id=room_id,
        proposed_by=body.get("proposed_by", "user"),
        title=body.get("title", "分工提案"),
        description=body.get("description", ""),
        assignments=body.get("assignments", {}),
        dependencies=body.get("dependencies", []),
    )
    room.proposals.append(proposal)
    room.messages.append(DiscussionMessage(
        room_id=room_id,
        agent_id=proposal.proposed_by,
        agent_name=proposal.proposed_by,
        msg_type=DiscMessageType.PROPOSE,
        content=f"提交提案: {proposal.title}",
        ref_proposal_id=proposal.proposal_id,
    ))
    await bcast_to_clients(Message(
        type=MessageType.PROPOSAL_SUBMIT, from_agent=proposal.proposed_by,
        content=f"新提案: {proposal.title}",
        task_id=room.task_id, room_id=room_id,
        payload={"event": "proposal_submitted", "proposal": proposal.model_dump()},
    ))
    save_state()
    return {"success": True, "proposal": proposal.model_dump()}

@router.post("/api/room/{room_id}/proposal/prefill")
async def prefill_proposal(room_id: str):
    """AI 根据讨论内容预填提案字段。"""
    from ..server import Utf8JSONResponse, ai_external_run_ai_call, ai_provider, discussion_rooms
    if room_id not in discussion_rooms:
        return Utf8JSONResponse({"error": "room not found"}, status_code=404)
    room = discussion_rooms[room_id]
    if not ai_provider:
        return {"success": False, "error": "AI 后端未配置"}
    # 收集讨论消息原文
    msgs_text = []
    for m in room.messages:
        header = f"[{m.msg_type.value}] {m.agent_name}:"
        msgs_text.append(f"{header} {m.content}")
    discussion_summary = "\n".join(msgs_text) if msgs_text else "（暂无讨论消息）"
    # 构建预填提示词
    prompt = f"""以下是讨论室中的对话记录。请根据这些内容，为"分工方案"生成预填数据。
讨论记录:
{discussion_summary}
参与讨论的 Agent 列表: {json.dumps(list(room.participants), ensure_ascii=False)}
请以 JSON 格式返回预填数据（只返回 JSON，不要其他文字）:
{{
  "title": "方案标题（简洁描述工作任务）",
  "description": "方案描述（概括总体目标和方法）",
  "assignments": {{
    "agent_id_1": {{
      "task_title": "该 Agent 的任务标题",
      "task_description": "具体执行步骤和期望",
      "capability_required": "所需能力标签（如 coding / writing / analysis / research / general）",
      "expected_output": "期望输出格式（如 Markdown 文档）"
    }},
    "agent_id_2": {{ ... }}
  }}
}}
要求:
1. 为每个 participant 的 agent_id 生成分工条目
2. 能力标签尽量精准，不明确时填 "general"
3. 如果讨论记录太少，标题和描述可基于 room.task_title / room.task_description 推断"""
    try:
        raw = await ai_external_run_ai_call(
            ai_provider.chat("你是一个善于总结和规划的工作助手。严格按要求返回 JSON。", prompt),
            label="server.room_plan_prefill",
        )
        # ★2026-10-12：**先认 AI 错误文本**。原代码在解析不到 JSON 时走 else 分支
        #   `return {"success": True, ...}` —— 而 provider 的错误文本（`[Error: HTTP 402] {...}`）
        #   切片后**是合法 JSON 但只有 error 键**，于是"AI 根本没答"会被当成
        #   "AI 预填成功（内容为空）" ⇒ **假成功**（准则⑤）。
        from ..ai_provider import ai_error_text as _aet   # 搬家后必须多一层（routers/ -> platform/）
        _err = _aet(raw)
        if _err:
            return {"success": False, "error": f"平台 AI 调用失败，未能预填：{_err[:200]}",
                    "error_code": "ai_unavailable",
                    "hint": "检查平台 AI 配置/额度后重试；也可以直接手工填写分工。"}
        # 尝试提取 JSON（容错处理）
        json_start = raw.find("{")
        json_end = raw.rfind("}") + 1
        if json_start >= 0 and json_end > json_start:
            data = json.loads(raw[json_start:json_end])
            if "title" not in data:
                data["title"] = room.task_title or "分工方案"
            if "description" not in data:
                data["description"] = room.task_description or ""
            if "assignments" not in data:
                data["assignments"] = {}
            return {"success": True, "prefill": data}
        else:
            return {"success": True, "prefill": {"title": room.task_title or "分工方案", "description": room.task_description or "", "assignments": {}}}
    except Exception as e:
        return {"success": False, "error": f"AI 预填失败: {str(e)}"}

@router.post("/api/room/{room_id}/vote")
async def cast_vote(room_id: str, request: Request):
    """Agent 投票"""
    from ..server import Agreement, Message, MessageType, Utf8JSONResponse, _reach_consensus, bcast_to_clients, discussion_rooms, save_state
    if room_id not in discussion_rooms:
        return Utf8JSONResponse({"error": "room not found"}, status_code=404)
    body = await request.json()
    room = discussion_rooms[room_id]
    agent_id = body.get("agent_id", "")
    vote = body.get("vote", "approve")  # approve / reject / abstain
    proposal_id = body.get("proposal_id", "")
    if room.agreement is None:
        room.agreement = Agreement(room_id=room_id, proposal_id=proposal_id)
    room.agreement.votes[agent_id] = vote
    room.agreement.vote_count = len(room.agreement.votes)
    room.agreement.approve_count = sum(1 for v in room.agreement.votes.values() if v == "approve")
    room.agreement.reject_count = sum(1 for v in room.agreement.votes.values() if v == "reject")
    # 全票通过 → 自动达成共识
    if room.agreement.approve_count == len(room.participants):
        await _reach_consensus(room, next(p for p in room.proposals if p.proposal_id == proposal_id))
    await bcast_to_clients(Message(
        type=MessageType.VOTE_CAST, from_agent=agent_id,
        content=f"{agent_id} 投了 {vote}",
        task_id=room.task_id, room_id=room_id,
        payload={"event": "vote_cast", "agent_id": agent_id, "vote": vote, "agreement": room.agreement.model_dump()},
    ))
    save_state()
    return {"success": True, "agreement": room.agreement.model_dump()}

@router.post("/api/room/{room_id}/consensus")
async def force_consensus(room_id: str, request: Request):
    """手动触发共识（用户或管理员操作）"""
    from ..server import Utf8JSONResponse, _reach_consensus, discussion_rooms, save_state
    if room_id not in discussion_rooms:
        return Utf8JSONResponse({"error": "room not found"}, status_code=404)
    room = discussion_rooms[room_id]
    body = await request.json()
    proposal_id = body.get("proposal_id", "")
    proposal = next((p for p in room.proposals if p.proposal_id == proposal_id), None)
    if not proposal and room.proposals:
        proposal = room.proposals[-1]
    if not proposal:
        return Utf8JSONResponse({"error": "no proposal to adopt"}, status_code=400)
    await _reach_consensus(room, proposal)
    save_state()
    return {"success": True, "proposal": proposal.model_dump()}

@router.post("/api/room/{room_id}/proposal/{proposal_id}/edit")
async def edit_proposal(room_id: str, proposal_id: str, request: Request):
    """编辑提案（仅用户提交的）"""
    from ..server import DiscMessageType, DiscussionMessage, Message, MessageType, Utf8JSONResponse, _try_bcast, discussion_rooms, save_state
    if room_id not in discussion_rooms:
        return Utf8JSONResponse({"error": "room not found"}, status_code=404)
    room = discussion_rooms[room_id]
    proposal = next((p for p in room.proposals if p.proposal_id == proposal_id), None)
    if not proposal:
        return Utf8JSONResponse({"error": "proposal not found"}, status_code=404)
    if proposal.status == "accepted":
        return Utf8JSONResponse({"error": "已采纳的方案不可编辑"}, status_code=400)
    if proposal.proposed_by != "user":
        return Utf8JSONResponse({"error": "仅可编辑自己提交的方案"}, status_code=400)
    body = await request.json()
    if "title" in body:
        proposal.title = body["title"]
    if "description" in body:
        proposal.description = body["description"]
    if "assignments" in body:
        updated = {}
        for agent_id, assign_data in body["assignments"].items():
            if isinstance(assign_data, dict):
                old = proposal.assignments.get(agent_id, {})
                updated[agent_id] = {
                    "agent_id": agent_id,
                    "task_title": assign_data.get("task_title", old.get("task_title", "")),
                    "task_description": assign_data.get("task_description", old.get("task_description", "")),
                    "capability_required": assign_data.get("capability_required", old.get("capability_required", "general")),
                    "expected_output": assign_data.get("expected_output", old.get("expected_output", "")),
                }
        proposal.assignments = updated
    room.messages.append(DiscussionMessage(
        room_id=room_id,
        agent_id="user",
        agent_name="用户",
        msg_type=DiscMessageType.AMEND,
        content=f"修改了提案: {proposal.title}",
        ref_proposal_id=proposal_id,
    ))
    await _try_bcast(Message(
        type=MessageType.ROOM_EVENT, from_agent="user",
        content=f"用户修改了提案: {proposal.title}",
        task_id=room.task_id, room_id=room_id,
        payload={"event": "proposal_amended", "proposal": proposal.model_dump()},
    ))
    save_state()
    return {"success": True, "proposal": proposal.model_dump()}

@router.post("/api/room/{room_id}/proposal/{proposal_id}/withdraw")
async def withdraw_proposal(room_id: str, proposal_id: str):
    """撤回提案（仅用户提交的）"""
    from ..server import DiscMessageType, DiscussionMessage, Message, MessageType, Utf8JSONResponse, _try_bcast, discussion_rooms, save_state
    if room_id not in discussion_rooms:
        return Utf8JSONResponse({"error": "room not found"}, status_code=404)
    room = discussion_rooms[room_id]
    proposal = next((p for p in room.proposals if p.proposal_id == proposal_id), None)
    if not proposal:
        return Utf8JSONResponse({"error": "proposal not found"}, status_code=404)
    if proposal.status == "accepted":
        return Utf8JSONResponse({"error": "已采纳的方案不可撤回"}, status_code=400)
    if proposal.proposed_by != "user":
        return Utf8JSONResponse({"error": "仅可撤回自己提交的方案"}, status_code=400)
    room.proposals[:] = [p for p in room.proposals if p.proposal_id != proposal_id]
    room.messages.append(DiscussionMessage(
        room_id=room_id,
        agent_id="user",
        agent_name="用户",
        msg_type=DiscMessageType.AMEND,
        content=f"撤回了提案: {proposal.title}",
        ref_proposal_id=proposal_id,
    ))
    await _try_bcast(Message(
        type=MessageType.ROOM_EVENT, from_agent="user",
        content=f"用户撤回了提案: {proposal.title}",
        task_id=room.task_id, room_id=room_id,
        payload={"event": "proposal_amended", "room_id": room_id},
    ))
    save_state()
    return {"success": True}
