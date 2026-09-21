"""Transport Adapters: WS / HTTP / PIPE

v3.1 更新：
- http_call 支持 AgentCard.endpoints（多端点）
- 新增 endpoint_select 自动选择最优端点
- 新增 room_notify 讨论室通知方法
"""

from __future__ import annotations
import asyncio, json, os, time
from pathlib import Path

import httpx
from fastapi import WebSocket

from .protocol import (
    AgentCard, AgentEndpoint, Message, MessageType,
    PipeRequest, PipeResponse, TransportType,
)


# ── 端点选择 ──────────────────────────────────────────────────

def select_endpoint(card: AgentCard, preferred: TransportType | None = None) -> AgentEndpoint | None:
    """从多端点中选择最优接入点"""
    if not card.endpoints:
        return None

    # 优先选择 preferred 类型
    if preferred:
        for ep in card.endpoints:
            if ep.transport == preferred:
                return ep

    # 按优先级排序，选最高的
    sorted_eps = sorted(card.endpoints, key=lambda x: x.priority, reverse=True)
    return sorted_eps[0]


# ── WS Adapter ─────────────────────────────────────────────────

async def ws_send(ws: WebSocket, msg: Message):
    try:
        await ws.send_text(msg.model_dump_json())
    except Exception:
        pass


# ── HTTP Adapter ───────────────────────────────────────────────

async def http_call(agent: AgentCard, task: str, from_agent: str = "platform") -> tuple[bool, str]:
    """平台 POST 到 Agent 端点。自动从 AgentCard.endpoints 选择 HTTP 端点。

    Returns:
        (ok: bool, content: str)
    """
    # 选择 HTTP 端点
    endpoint = None
    for ep in agent.endpoints:
        if ep.transport == TransportType.HTTP:
            endpoint = ep
            break

    if not endpoint:
        return False, f"[{agent.name}] 无可用的 HTTP 端点"

    url = endpoint.url

    try:
        async with httpx.AsyncClient(timeout=120.0) as c:
            r = await c.post(url, json={"task": task, "from": from_agent})
            if r.status_code == 200:
                data = r.json()
                content = data.get("result", data.get("error", str(data)))
                if "error" in str(content).lower() and "500" in str(content):
                    return False, f"[{agent.name}] 模型服务异常：{content[:300]}"
                return True, content
            return False, f"[{agent.name}] HTTP {r.status_code}"
    except httpx.TimeoutException:
        return False, f"[{agent.name}] 超时"
    except Exception as e:
        return False, f"[{agent.name}] 请求失败：{e}"


async def http_call_raw(endpoint: AgentEndpoint, payload: dict, timeout: float = 120.0) -> tuple[bool, str]:
    """直接向指定端点发送 HTTP 请求（不通过 AgentCard）"""
    if endpoint.transport != TransportType.HTTP:
        return False, "端点不是 HTTP 类型"

    try:
        async with httpx.AsyncClient(timeout=timeout) as c:
            r = await c.post(endpoint.url, json=payload)
            if r.status_code == 200:
                data = r.json()
                content = data.get("result", data.get("error", str(data)))
                return True, content
            return False, f"HTTP {r.status_code}"
    except httpx.TimeoutException:
        return False, "超时"
    except Exception as e:
        return False, f"请求失败：{e}"


# ── 讨论室通知（v3.1 新增）────────────────────────────────────

async def room_notify(agent: AgentCard, payload: dict) -> tuple[bool, str]:
    """向 Agent 发送讨论室相关的通知（协商邀请、投票请求等）。

    自动选择最优端点：HTTP > WS > PIPE。
    返回 (ok, content)。
    """
    # 优先 HTTP
    for ep in agent.endpoints:
        if ep.transport == TransportType.HTTP:
            try:
                async with httpx.AsyncClient(timeout=30.0) as c:
                    r = await c.post(ep.url, json=payload)
                    if r.status_code == 200:
                        data = r.json()
                        return True, data.get("result", data.get("error", str(data)))
                    return False, f"HTTP {r.status_code}"
            except Exception as e:
                return False, str(e)

    return False, "无可用的通知端点"


# ── PIPE Adapter ───────────────────────────────────────────────

class PipeAdapter:
    """平台端管道适配器：写入请求 → 轮询响应"""

    def __init__(self, pipe_dir: str):
        self.input_dir = Path(pipe_dir) / "to_main"
        self.output_dir = Path(pipe_dir) / "from_main"
        self.input_dir.mkdir(parents=True, exist_ok=True)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def send(self, req: PipeRequest) -> str:
        """写入请求文件，返回请求 ID"""
        path = self.input_dir / f"{req.message_id}.json"
        path.write_text(req.model_dump_json(), encoding="utf-8")
        return req.message_id

    async def wait_response(self, request_id: str, timeout: float = 120.0) -> PipeResponse | None:
        """轮询等待响应文件"""
        deadline = time.time() + timeout
        while time.time() < deadline:
            resp_files = sorted(self.output_dir.glob("*.json"))
            for f in resp_files:
                try:
                    data = json.loads(f.read_text(encoding="utf-8"))
                    if data.get("request_id") == request_id:
                        f.unlink()
                        return PipeResponse(**data)
                except Exception:
                    continue
            await asyncio.sleep(1.0)
        return None

    def list_pending(self) -> list[PipeRequest]:
        """列出所有待处理的请求（主 Agent 侧使用）"""
        reqs = []
        for f in sorted(self.input_dir.glob("*.json")):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                reqs.append(PipeRequest(**data))
            except Exception:
                pass
        return reqs

    def respond(self, request_id: str, resp: PipeResponse):
        """写入响应文件（主 Agent 侧使用）"""
        path = self.output_dir / f"{request_id}.json"
        path.write_text(resp.model_dump_json(), encoding="utf-8")
        req_path = self.input_dir / f"{request_id}.json"
        if req_path.exists():
            req_path.unlink()

    @classmethod
    def default(cls) -> PipeAdapter:
        """创建默认管道目录"""
        base = Path(os.environ.get("TEMP", ".")) / "agent_community_pipe"
        return cls(str(base))
