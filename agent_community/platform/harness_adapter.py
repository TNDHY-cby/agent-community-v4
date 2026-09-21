"""Harness 外部接入适配层 v3.2



职责：将外部 AI Harness（Trae CN / Cursor / Ollama / Claude Desktop 等）

映射为平台 Agent，透明参与讨论室和任务委托。



设计原则：

1. 平台不控制 Harness，只提供接入接口

2. Harness 以自己的方式连上来（HTTP/WS/Pipe）

3. 接入后自动生成 AgentCard，参与广播举手、讨论室协商、委托执行

4. 消息桥接：平台 → Harness → AI 处理 → 结果回传

5. 支持文件轮询 / 剪贴板桥接作为后备（对 Trae CN 这类不可回调的 Harness）

"""



from __future__ import annotations

import asyncio

import json

import os

import time

import threading

from datetime import datetime

from pathlib import Path

from typing import Optional, Callable



import httpx

from fastapi import WebSocket, Request

from fastapi.responses import JSONResponse



from .protocol import (

    AgentCard, AgentEndpoint, TransportType,

    HarnessInfo, HarnessSession, HarnessStatus,

    HarnessMessage, HarnessMessageType, HarnessTool, HarnessAI,

    Message, MessageType,

    DiscussionRoom, DiscussionRoomStatus,

    BroadcastHandRaise,

    DelegationRequest, ExecutionResult,

    ExecutionContext, ExecuteMessage,

    ReviewRequest, ReviewResponse,

    WakeupMethod,

)

# ── 平台内 AI 注入点（manual 接管时用于模拟外端 harness 执行/审查）──
_internal_ai_provider = None


def set_internal_ai_provider(provider) -> None:
    """server 启动/配置变更时注入当前 AI Provider；manual 模式下委托执行改由它承接。"""
    global _internal_ai_provider
    _internal_ai_provider = provider


def get_internal_ai_provider():
    return _internal_ai_provider


def _local_ai():
    """取本地 AI 兜底模块；不可用时返回 None（保持原有降级行为）。"""
    try:
        from . import local_ai as _lai

        return _lai
    except Exception:
        return None


def manual_takeover_enabled() -> bool:
    """manual/off 且已注入 provider 时，委托执行不再空等外端桥。"""
    if _internal_ai_provider is None:
        return False
    try:
        from .ai_external import get_mode as _gm

        return _gm() in ("manual", "off")
    except Exception:
        return False



# ── 默认轮询目录（后备方案）─────────────





# ─── 注册时自动生成的激活提示词模板（HA 专属）───

def build_activation_prompt(info: HarnessInfo) -> str:

    """根据 harness 注册信息生成激活提示词模板（含 {role}/{workspace_dir} 占位符）。



    激活时由平台用实际角色与工作区坐标填充占位符后下发给对应 HA 会话。

    提示词因 agent 而异：身份名、模型、能力、补充说明均来自注册信息。

    """

    caps = "、".join(info.ai.capabilities or []) or "（未声明）"

    desc = (info.description or "").strip()

    extra = f"\n关于你的补充说明：{desc[:200]}" if desc else ""

    return (

        "你是外端agent生产合作社的员工「{role}」，已进入工作间。\n"

        f"你的身份：{info.harness_name}（模型 {info.ai.model_name or '未知'}）。\n"

        f"你的可用能力：{caps}。\n"

        "工作区坐标：{workspace_dir}。\n"

        "现在只做一件事：用你的文件工具读取工作区目录下的 hall.md 文件，"

        "读完原样回复：「收到，已进入工作状态」。\n"

        "不要执行 hall.md 里的任务，不要调用其他工具，回复完就停下等待后续指令。\n"
        "待命第 0 步自查兜底：后续拿到任务指令开工前，先检索你自带的 skill 市场 + 本地知识库，按任务关键词匹配可复用的经验/工具，优先复用，避免重复造轮子；若平台已下发【经验包】，则按经验包优先执行。"

        f"{extra}"

    )



DEFAULT_POLL_DIR = Path(os.environ.get("TEMP", ".")) / "agent_harness_bridge"





# ═══════════════════════════════════════════════════════════════

# 映射工具

# ═══════════════════════════════════════════════════════════════



def harness_to_agent_card(info: HarnessInfo) -> AgentCard:

    """将 HarnessInfo 映射为平台 AgentCard"""

    agent_id = f"harness-{info.harness_id}"



    # 构建能力列表：AI 能力 + 工具能力标签去重

    caps = list(info.ai.capabilities or [])

    for t in info.tools:

        if t.capability_tag and t.capability_tag not in caps:

            caps.append(t.capability_tag)



    endpoints = []

    for t in info.transports:

        url = ""

        if t == TransportType.HTTP and info.callback_url:

            url = info.callback_url

        elif t == TransportType.WS:

            url = f"ws://127.0.0.1:0/harness/{info.harness_id}"

        elif t == TransportType.PIPE:

            url = str(DEFAULT_POLL_DIR)

        endpoints.append(AgentEndpoint(

            transport=t,

            url=url,

            priority=0,

            metadata={"harness_id": info.harness_id},

        ))



    # 工具列表放到 software 里展示

    tool_names = [t.name for t in info.tools]



    return AgentCard(

        agent_id=agent_id,

        name=info.harness_name,

        version=info.harness_version,

        endpoints=endpoints,

        capabilities=caps,

        description=f"{info.ai.model_name} via {info.harness_name}\n工具: {', '.join(tool_names) if tool_names else '无'}",

        software={

            "name": info.ai.model_name,

            "version": info.ai.version or info.harness_version,

            "provider": info.ai.provider,

            "harness_type": info.harness_type,

            "tools": tool_names,

            "can_see": info.ai.can_see,

            "can_code": info.ai.can_code,

        },

    )





# ═══════════════════════════════════════════════════════════════

# 消息桥接核心

# ═══════════════════════════════════════════════════════════════



class HarnessBridge:

    """平台 ↔ Harness 双向消息桥接器。



    三种桥接模式：

    1. HTTP 回调 — Harness 有回调 URL，平台直接 POST 过去

    2. WebSocket — Harness 通过 WS 保持长连

    3. 文件轮询 — 平台写入文件，Harness 轮询读取（后备方案）

    """



    def __init__(self, session: HarnessSession, poll_dir: Path = DEFAULT_POLL_DIR):

        self.session = session

        self.poll_dir = poll_dir

        self._ws: Optional[WebSocket] = None

        self._pending: dict[str, asyncio.Future] = {}  # delegation_id → Future



        # 确保文件轮询目录就绪

        self.to_harness = poll_dir / f"to_{session.harness_id}"

        self.from_harness = poll_dir / f"from_{session.harness_id}"

        self.to_harness.mkdir(parents=True, exist_ok=True)

        self.from_harness.mkdir(parents=True, exist_ok=True)



    def attach_ws(self, ws: WebSocket):

        self._ws = ws

        self.session.transport = TransportType.WS



    # ─ 发送 ─



    async def send(self, msg: HarnessMessage) -> tuple[bool, str]:

        """向 Harness 发送消息，自动选择最优 transport"""

        transport = self.session.transport



        # 1) WebSocket 优先

        if transport == TransportType.WS and self._ws:

            try:

                await self._ws.send_text(msg.model_dump_json())

                return True, "ws:ok"

            except Exception as e:

                # WS 失败回退到文件轮询

                transport = TransportType.PIPE



        # 2) HTTP 回调

        if transport == TransportType.HTTP and self.session.info.callback_url:

            return await self._send_http(msg)



        # 3) 文件轮询（兜底）

        return self._send_pipe(msg)



    async def _send_http(self, msg: HarnessMessage) -> tuple[bool, str]:

        try:

            async with httpx.AsyncClient(timeout=30.0) as c:

                r = await c.post(

                    self.session.info.callback_url,

                    json=msg.model_dump(),

                )

                if r.status_code == 200:

                    data = r.json()

                    return True, data.get("result", data.get("content", ""))

                return False, f"HTTP {r.status_code}"

        except Exception as e:

            return False, str(e)



    def _send_pipe(self, msg: HarnessMessage) -> tuple[bool, str]:

        """写入文件轮询目录"""

        ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")

        path = self.to_harness / f"{ts}_{msg.id}.json"

        path.write_text(msg.model_dump_json(), encoding="utf-8")

        return True, f"pipe:wrote:{path.name}"



    # ─ 接收轮询 ─



    def poll_incoming(self) -> list[HarnessMessage]:

        """读取 Harness 写入的回复文件（供 Harness 侧轮询调用时对调使用；

        实际是读取 from_harness 目录下 Harness 写入的文件）"""

        results = []

        if not self.from_harness.exists():

            return results

        for f in sorted(self.from_harness.glob("*.json")):

            try:

                data = json.loads(f.read_text(encoding="utf-8"))

                results.append(HarnessMessage(**data))

                f.unlink()

            except Exception:

                pass

        return results



    def poll_platform(self) -> list[HarnessMessage]:

        """Harness 侧调用：读取平台写入的消息（to_harness 目录）"""

        results = []

        if not self.to_harness.exists():

            return results

        for f in sorted(self.to_harness.glob("*.json")):

            try:

                data = json.loads(f.read_text(encoding="utf-8"))

                results.append(HarnessMessage(**data))

                f.unlink()

            except Exception:

                pass

        return results



    def reply(self, msg: HarnessMessage):

        """Harness 侧回复：写入 from_harness 目录"""

        path = self.from_harness / f"{msg.id}.json"

        path.write_text(msg.model_dump_json(), encoding="utf-8")



    # ─ 委托执行桥 ─



    async def delegate_to_harness(

        self,

        del_req: DelegationRequest,

        room_desc: str = "",

    ) -> HarnessMessage:

        """将平台委托转发给 Harness 内 AI，等待回复"""

        msg = HarnessMessage(

            harness_id=self.session.harness_id,

            direction="to_harness",

            msg_type=HarnessMessageType.DELEGATION,

            content=(

                f"【委托任务】\n"

                f"标题: {del_req.title}\n"

                f"描述: {del_req.description}\n"

                f"所需能力: {del_req.capability_required}\n"

                f"讨论室上下文: {room_desc}\n"

                f"依赖: {json.dumps(del_req.depends_on, ensure_ascii=False)}\n"

                f"预期输出字段: {json.dumps(del_req.expected_output.required_fields, ensure_ascii=False)}\n"

            ),

            task_id=del_req.task_id,

            delegation_id=del_req.id,

            payload={

                "delegation": del_req.model_dump(),

            },

        )



        # 对可回调的 Harness 创建 Future 等待

        future: asyncio.Future = asyncio.get_event_loop().create_future()

        self._pending[del_req.id] = future



        ok, info = await self.send(msg)



        if not ok:

            future.cancel()

            return HarnessMessage(

                harness_id=self.session.harness_id,

                direction="from_harness",

                msg_type=HarnessMessageType.RESULT,

                content=f"[错误] 无法送达 Harness: {info}",

                delegation_id=del_req.id,

            )



        try:

            result = await asyncio.wait_for(future, timeout=300.0)

            return result

        except asyncio.TimeoutError:

            return HarnessMessage(

                harness_id=self.session.harness_id,

                direction="from_harness",

                msg_type=HarnessMessageType.RESULT,

                content="[超时] Harness 执行超时（300s）",

                delegation_id=del_req.id,

            )



    def on_harness_reply(self, reply: HarnessMessage):

        """Harness 回复到达时触发，解析 delegation result"""

        did = reply.delegation_id

        if did and did in self._pending:

            fut = self._pending.pop(did)

            if not fut.done():

                fut.set_result(reply)

            return True

        return False



    # ─ 邀请加入 ─



    async def invite_agent(self, task: Task, required_capabilities: list[str]) -> HarnessMessage:

        """v4 MVP：发送邀请消息，让 Harness AI 自行评估是否参与任务"""

        msg = HarnessMessage(

            harness_id=self.session.harness_id,

            direction="to_harness",

            msg_type=HarnessMessageType.INVITE,

            content=(

                f"【任务邀请】\n"

                f"任务ID: {task.id}\n"

                f"标题: {task.title}\n"

                f"描述: {task.description}\n"

                f"所需能力: {json.dumps(required_capabilities, ensure_ascii=False)}\n\n"

                f"请评估你的 AI ({self.session.info.ai.model_name}) 和工具是否能胜任。\n"

                f"回复 JSON：{{\"accept\":true|false,\"capability_claim\":\"...\",\"confidence\":0.0-1.0}}"

            ),

            task_id=task.id,

        )

        future: asyncio.Future = asyncio.get_event_loop().create_future()

        key = f"invite_{task.id}"

        self._pending[key] = future



        ok, _ = await self.send(msg)

        if not ok:

            future.cancel()

            return HarnessMessage(

                harness_id=self.session.harness_id,

                direction="from_harness",

                msg_type=HarnessMessageType.INVITE_REPLY,

                content='{"accept":false,"reason":"无法送达 Harness"}',

                task_id=task.id,

            )



        try:

            return await asyncio.wait_for(future, timeout=15.0)

        except asyncio.TimeoutError:

            return HarnessMessage(

                harness_id=self.session.harness_id,

                direction="from_harness",

                msg_type=HarnessMessageType.INVITE_REPLY,

                content='{"accept":false,"reason":"邀请超时（15s）"}',

                task_id=task.id,

            )



    # ─ 执行子任务（注入上下文）─




    async def _manual_execute_subtask(self, exec_msg: ExecuteMessage) -> HarnessMessage:
        """manual 接管：委托执行改由平台外部接管通道（Marvis 等）模拟该成员产出。"""
        del_req = exec_msg.delegation
        ctx = exec_msg.context
        role = getattr(self.session, "agent_name", "") or self.session.harness_id
        system_prompt = (
            f"你是多 Agent 协作平台中的执行成员「{role}」，负责能力：{del_req.capability_required}。"
            "直接产出可交付成果正文，不要解释流程、不要寒暄、不要复述要求。"
            "产出必须紧扣【所属任务】给出具体内容本体（真实清单/模块/方案/代码），"
            "严禁复述输入字段名、严禁只描述任务结构、严禁输出「根据给定的任务描述」这类元话术。"
        )
        user_message = (
            f"【所属任务】{ctx.task_title or del_req.title}\n{ctx.task_description}\n\n"
            f"【你负责的子任务】\n标题: {del_req.title}\n描述: {del_req.description}\n"
            f"所需能力: {del_req.capability_required}\n"
            f"预期输出字段: {json.dumps(del_req.expected_output.required_fields, ensure_ascii=False)}\n"
            f"前序产出: {json.dumps(ctx.parent_outputs, ensure_ascii=False)}\n"
            f"全局约束: {json.dumps(ctx.global_constraints, ensure_ascii=False)}"
        )
        local = _local_ai()
        text = ""
        try:
            from .ai_external import run_ai_call as _rac

            text = await _rac(
                _internal_ai_provider.chat(system_prompt, user_message),
                local_timeout=(local.quick_wait_limit() if local else None),
                label=f"manual.execute_subtask:{del_req.id}",
            )
        except Exception as e:
            print(f"[manual-exec] 外部接管未取得产出({del_req.id}): {str(e)[:90]}", flush=True)
        source = "external"
        # 外部接管缺席（超时/空转/占位）时，由本机模型兜底，保证平台离线自洽
        if local is not None and local.is_placeholder(text or ""):
            try:
                text = await local.chat(system_prompt, user_message)
                source = "local:" + str((local.detect() or {}).get("backend", "?"))
                print(f"[manual-exec] 本地 AI 已兜底({del_req.id}) 来源={source} 长度={len(text)}", flush=True)
            except Exception as e:
                print(f"[manual-exec] 本地兜底失败({del_req.id}): {str(e)[:90]}", flush=True)
        return HarnessMessage(
            harness_id=self.session.harness_id,
            direction="from_harness",
            msg_type=HarnessMessageType.RESULT,
            content=text or "[manual 接管] 未取得模型产出",
            task_id=del_req.task_id,
            delegation_id=del_req.id,
            payload={"ok": bool(text), "manual": True, "source": source},
        )
    async def execute_subtask(self, exec_msg: ExecuteMessage) -> HarnessMessage:

        """v4 MVP：将带完整上下文的执行消息转发给 Harness 并等待结果"""
        # manual 接管：外端 harness 无额度时，不再空等桥的 300s 超时
        if manual_takeover_enabled():
            return await self._manual_execute_subtask(exec_msg)

        del_req = exec_msg.delegation

        ctx = exec_msg.context



        msg = HarnessMessage(

            harness_id=self.session.harness_id,

            direction="to_harness",

            msg_type=HarnessMessageType.EXECUTE,

            content=(

                f"【所属任务】{ctx.task_title}\n{ctx.task_description}\n\n"

                f"【执行任务】\n"

                f"标题: {del_req.title}\n"

                f"描述: {del_req.description}\n"

                f"所需能力: {del_req.capability_required}\n"

                f"预期输出字段: {json.dumps(del_req.expected_output.required_fields, ensure_ascii=False)}\n"

                f"前序产出: {json.dumps(ctx.parent_outputs, ensure_ascii=False)}\n"

                f"全局约束: {json.dumps(ctx.global_constraints, ensure_ascii=False)}\n"

            ),

            task_id=del_req.task_id,

            delegation_id=del_req.id,

            payload={

                "exec_msg": exec_msg.model_dump(),

            },

        )



        future: asyncio.Future = asyncio.get_event_loop().create_future()

        self._pending[del_req.id] = future



        ok, info = await self.send(msg)

        if not ok:

            future.cancel()

            return HarnessMessage(

                harness_id=self.session.harness_id,

                direction="from_harness",

                msg_type=HarnessMessageType.RESULT,

                content=f"[错误] 无法送达 Harness: {info}",

                delegation_id=del_req.id,

            )



        try:

            return await asyncio.wait_for(future, timeout=300.0)

        except asyncio.TimeoutError:

            return HarnessMessage(

                harness_id=self.session.harness_id,

                direction="from_harness",

                msg_type=HarnessMessageType.RESULT,

                content="[超时] Harness 执行超时（300s）",

                delegation_id=del_req.id,

            )



    # ─ 审查子任务（收敛驱动）─




    async def _manual_review_subtask(self, review_req: ReviewRequest) -> HarnessMessage:
        """manual 接管：审查同样由平台外部接管通道模拟，返回严格 JSON。"""
        system_prompt = (
            "你是多 Agent 协作平台的审查员，只输出 JSON，不要任何解释。"
            '格式：{"verdict":"pass|revise|reject","feedback":"...","score":0.8}'
        )
        user_message = (
            f"【审查任务】\n审查对象: {review_req.target_type}\n"
            f"期望能力: {review_req.expected_capability}\n\n待审查内容:\n{review_req.content}"
        )
        local = _local_ai()
        text = ""
        try:
            from .ai_external import run_ai_call as _rac

            text = await _rac(
                _internal_ai_provider.chat(system_prompt, user_message),
                local_timeout=(local.quick_wait_limit() if local else None),
                label=f"manual.review_subtask:{review_req.request_id}",
            )
        except Exception as e:
            print(f"[manual-exec] 外部审查未取得意见({review_req.request_id}): {str(e)[:90]}", flush=True)
        # 外部缺席或未返回合法 JSON 时，由本机模型兜底审查
        if local is not None and local.extract_json(text or "") is None:
            try:
                lt = await local.chat(system_prompt, user_message, max_tokens=400)
                parsed = local.extract_json(lt)
                if isinstance(parsed, dict) and parsed.get("verdict"):
                    text = json.dumps(parsed, ensure_ascii=False)
                    print(f"[manual-exec] 本地 AI 已兜底审查({review_req.request_id}) "
                          f"verdict={parsed.get('verdict')}", flush=True)
            except Exception as e:
                print(f"[manual-exec] 本地审查兜底失败({review_req.request_id}): {str(e)[:90]}", flush=True)
        if not text:
            text = '{"verdict":"pass","feedback":"manual 接管未取得审查意见，默认通过","score":0.0}'
        return HarnessMessage(
            harness_id=self.session.harness_id,
            direction="from_harness",
            msg_type=HarnessMessageType.REVIEW_REPLY,
            content=text,
            task_id=review_req.task_id,
            delegation_id=review_req.delegation_id,
            payload={"review_reply": True, "manual": True},
        )
    async def review_subtask(self, review_req: ReviewRequest) -> HarnessMessage:

        """v4 MVP：发送审查请求，让 Harness AI 审查另一个 Agent 的执行结果"""
        if manual_takeover_enabled():
            return await self._manual_review_subtask(review_req)

        msg = HarnessMessage(

            harness_id=self.session.harness_id,

            direction="to_harness",

            msg_type=HarnessMessageType.REVIEW,

            content=(

                f"【审查任务】\n"

                f"审查对象: {review_req.target_type}\n"

                f"期望能力: {review_req.expected_capability}\n"

                f"超时: {review_req.timeout_seconds}s\n\n"

                f"待审查内容:\n{review_req.content}"

            ),

            task_id=review_req.task_id,

            delegation_id=review_req.delegation_id,

            payload={"review": review_req.model_dump()},

        )



        key = f"review_{review_req.request_id}"

        future: asyncio.Future = asyncio.get_event_loop().create_future()

        self._pending[key] = future



        ok, info = await self.send(msg)

        if not ok:

            future.cancel()

            return HarnessMessage(

                harness_id=self.session.harness_id,

                direction="from_harness",

                msg_type="review_reply",

                content='{"verdict":"pass","feedback":"审查者不可达，默认通过","score":0.0}',

                delegation_id=review_req.delegation_id,

            )



        try:

            return await asyncio.wait_for(future, timeout=review_req.timeout_seconds)

        except asyncio.TimeoutError:

            return HarnessMessage(

                harness_id=self.session.harness_id,

                direction="from_harness",

                msg_type="review_reply",

                content='{"verdict":"fail","feedback":"审查超时","score":0.0}',

                delegation_id=review_req.delegation_id,

            )



    # ─ 解除绑定 ─



    async def close_binding(self) -> bool:

        """v4 MVP：解除 Harness 与当前任务的绑定，清理 pending futures"""

        for key, fut in list(self._pending.items()):

            if not fut.done():

                fut.cancel()

        self._pending.clear()

        return True





# ═══════════════════════════════════════════════════════════════

# Session 管理器

# ═══════════════════════════════════════════════════════════════



class HarnessSessionManager:

    """管理所有已注册的 Harness 会话"""



    def __init__(self):

        self.sessions: dict[str, HarnessSession] = {}     # harness_id → session

        self.bridges: dict[str, HarnessBridge] = {}       # harness_id → bridge

        self.id_to_harness: dict[str, str] = {}           # agent_id → harness_id

        self.heartbeat_timeout: float = 60.0               # 心跳超时秒数



    def register(self, info: HarnessInfo) -> tuple[HarnessSession, HarnessBridge]:

        """注册或更新 Harness，同时记录唤醒配置"""

        # 生成 agent_card 用 agent_id

        agent_id = f"harness-{info.harness_id}"



        # 构建唤醒配置元数据

        wakeup_config = {

            "wakeup_method": info.wakeup_method.value if info.wakeup_method else "clipboard",

            "wakeup_url": info.wakeup_url or info.callback_url,

            "wakeup_dir": info.wakeup_dir,

            "acp_command": info.acp_command,

            "acp_cwd": info.acp_cwd,

        }



        if info.harness_id in self.sessions:

            # 更新已有 session（保持原状态，不强制在线；刷新注册时间）

            sess = self.sessions[info.harness_id]

            sess.info = info

            sess.agent_id = agent_id

            sess.last_heartbeat = datetime.now().isoformat()

            sess.registered_at = datetime.now().isoformat()

            sess.metadata["wakeup"] = wakeup_config
            sess.metadata["activation_prompt"] = build_activation_prompt(info)

            # 同步持久化的桥测试记录到运行时 metadata

            if (info.metadata or {}).get("bridge_test"):

                sess.metadata["bridge_test"] = info.metadata["bridge_test"]

            bridge = self.bridges[info.harness_id]

            bridge.session = sess

        else:

            sess = HarnessSession(

                harness_id=info.harness_id,

                info=info,

                agent_id=agent_id,

                status=HarnessStatus.OFFLINE,  # 注册不等于在线：心跳/桥测试通过后才置 ONLINE

                transport=info.transports[0] if info.transports else TransportType.HTTP,

                metadata={"wakeup": wakeup_config,
                "activation_prompt": (info.metadata or {}).get("activation_prompt") or build_activation_prompt(info)},

            )

            # 同步持久化的桥测试记录到运行时 metadata（首次加载场景）

            if (info.metadata or {}).get("bridge_test"):

                sess.metadata["bridge_test"] = info.metadata["bridge_test"]

            bridge = HarnessBridge(sess)

            self.sessions[info.harness_id] = sess

            self.bridges[info.harness_id] = bridge

            self.id_to_harness[agent_id] = info.harness_id



        return sess, bridge



    def unregister(self, harness_id: str):

        sess = self.sessions.pop(harness_id, None)

        if sess:

            self.id_to_harness.pop(sess.agent_id, None)

        self.bridges.pop(harness_id, None)



    def get_bridge(self, harness_id: str) -> Optional[HarnessBridge]:

        return self.bridges.get(harness_id)



    def get_bridge_by_agent(self, agent_id: str) -> Optional[HarnessBridge]:

        hid = self.id_to_harness.get(agent_id)

        if hid:

            return self.bridges.get(hid)

        return None



    def is_harness_agent(self, agent_id: str) -> bool:

        return agent_id in self.id_to_harness



    def heartbeat(self, harness_id: str):

        if harness_id in self.sessions:

            self.sessions[harness_id].last_heartbeat = datetime.now().isoformat()

            self.sessions[harness_id].status = HarnessStatus.ONLINE



    def check_timeouts(self) -> list[str]:

        """返回心跳超时的 harness_id 列表"""

        now = datetime.now()

        timeout_ids = []

        for hid, sess in self.sessions.items():

            try:

                last = datetime.fromisoformat(sess.last_heartbeat)

                if (now - last).total_seconds() > self.heartbeat_timeout:

                    sess.status = HarnessStatus.OFFLINE

                    timeout_ids.append(hid)

            except Exception:

                pass

        return timeout_ids



    def list_sessions(self) -> list[dict]:

        return [

            {

                "harness_id": s.harness_id,

                "harness_name": s.info.harness_name,

                "harness_type": s.info.harness_type,

                "agent_id": s.agent_id,

                "status": s.status.value,

                "ai": s.info.ai.model_dump(),

                "tools": [t.model_dump() for t in s.info.tools],

                "transport": s.transport.value,

                "registered_at": s.registered_at,

                "message_count": s.message_count,

                "wakeup": s.metadata.get("wakeup", {

                    "wakeup_method": s.info.wakeup_method.value if s.info.wakeup_method else "clipboard",

                    "wakeup_url": s.info.wakeup_url or s.info.callback_url,

                    "wakeup_dir": s.info.wakeup_dir,

                    "acp_command": s.info.acp_command,

                    "acp_cwd": s.info.acp_cwd,

                }),

                "bridge_dir": s.info.bridge_dir,

                "bridge_status": s.info.bridge_status,

                "bridge_test": s.metadata.get("bridge_test", {}),

            }

            for s in self.sessions.values()

        ]



    # ─ 消息回复处理 ─



    def handle_reply(self, reply: HarnessMessage):

        """处理来自 Harness 的回复消息，路由到对应 bridge"""

        bridge = self.bridges.get(reply.harness_id)

        if bridge is None:

            return



        # 更新消息计数

        if reply.harness_id in self.sessions:

            self.sessions[reply.harness_id].message_count += 1



        # 处理邀请回复

        key_invite = f"invite_{reply.task_id}"

        if key_invite in bridge._pending:

            fut = bridge._pending.pop(key_invite)

            if not fut.done():

                fut.set_result(reply)

            return



        # 处理审查回复

        did = reply.delegation_id

        key_review = f"review_{reply.payload.get('review', {}).get('request_id', '')}"

        if key_review in bridge._pending:

            fut = bridge._pending.pop(key_review)

            if not fut.done():

                fut.set_result(reply)

            return



        # 处理旧版举手回复（向后兼容）

        key_hand = f"hand_{reply.task_id}"

        if key_hand in bridge._pending:

            fut = bridge._pending.pop(key_hand)

            if not fut.done():

                fut.set_result(reply)

            return



        # 处理旧版讨论室回复

        key_room = f"room_{reply.room_id}"

        if key_room in bridge._pending:

            fut = bridge._pending.pop(key_room)

            if not fut.done():

                fut.set_result(reply)

            return



        # 处理委托回复 (on_harness_reply 已处理 delegation_id 匹配)

        did_match = reply.delegation_id

        if did_match and did_match in bridge._pending:

            fut = bridge._pending.pop(did_match)

            if not fut.done():

                fut.set_result(reply)

            return





# 全局单例

harness_manager = HarnessSessionManager()

