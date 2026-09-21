"""外部接管（manual）/ 纯规则降级（off）AI Provider。

背景：平台内部 AI API 无额度期间，需要让平台内部 AI 调用能被外部（如 Marvis）
接管模拟，使平台仍可继续开发与测试。

三种模式（配置项 ai_mode，默认 remote = 保持原有行为）：
- remote : 走真实 AI 后端（现状不变）
- manual : 待回复请求写入 data/ai_pending/*.json，等待外部通过 POST /api/ai/reply
           回写；超时（ai_manual_timeout，默认 120s）降级为纯规则占位，平台不卡死
- off    : 完全不调用任何 AI，直接返回纯规则占位

待回复文件字段：request_id / kind / mode / status / prompt / system_prompt /
context_digest / created_at / created_ts / timeout_s / reply / replied_at

幂等约定：
- resolve_reply 对同一 request_id 重复回写不会覆盖首次回复，返回 already=True
- request_id 不存在时明确报错（ok=False）
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from .ai_provider import AIProvider, ChatResponse

# 与 server.py 的 DATA_DIR 保持一致：agent_community/data
PENDING_DIR = Path(__file__).resolve().parents[1] / "data" / "ai_pending"

VALID_MODES = ("remote", "manual", "off", "local")
DEFAULT_TIMEOUT_S = 120.0
MIN_TIMEOUT_S = 1.0
POLL_INTERVAL_S = 0.3
PLACEHOLDER_PREFIX = "[AI 降级]"
WAIT_GRACE_S = 5.0  # 外层等待宽限：Provider 内部先超时，外层再加 5s 兜底
REMOTE_AI_CALL_TIMEOUT_S = 60.0  # remote：云端单次内部 AI 调用等待上限（保持各调用点原有语义）
LOCAL_AI_TIMEOUT_S = 180.0       # local：本机模型单次推理窗口（CPU 推理明显慢于云端，固定 60s 会被误取消）

_state: dict = {"mode": "remote", "timeout_s": DEFAULT_TIMEOUT_S}


# ═══════════════════════════════════════════════════════════════
# 模式管理
# ═══════════════════════════════════════════════════════════════

def get_mode() -> str:
    """返回当前 ai_mode。"""
    return str(_state["mode"])


def get_timeout() -> float:
    """返回 manual 模式等待外部回写的超时秒数。"""
    return float(_state["timeout_s"])


def set_mode(mode: str, timeout_s=None) -> str:
    """设置 ai_mode（remote / manual / off），可选更新超时秒数。非法值抛 ValueError。"""
    m = str(mode or "remote").strip().lower()
    if m not in VALID_MODES:
        raise ValueError(f"不支持的 ai_mode: {mode}（支持 {'/'.join(VALID_MODES)}）")
    _state["mode"] = m
    if timeout_s is not None:
        try:
            t = float(timeout_s)
        except (TypeError, ValueError):
            t = DEFAULT_TIMEOUT_S
        _state["timeout_s"] = max(MIN_TIMEOUT_S, t)
    return m


# ═══════════════════════════════════════════════════════════════
# 纯规则占位（off / manual 超时 时使用，绝不调用任何 AI）
# ═══════════════════════════════════════════════════════════════

def rule_placeholder(kind: str = "chat", reason: str = "") -> str:
    """生成纯规则占位文本。"""
    if _state["mode"] == "off":
        detail = "ai_mode=off，未调用任何 AI"
    else:
        detail = "ai_mode=manual 等待外部接管超时，已降级为纯规则"
    if reason:
        detail = f"{detail}（{reason}）"
    return f"{PLACEHOLDER_PREFIX} {detail}。"


def rule_classify_result(reason: str = "") -> dict:
    """纯规则的举手判断结果（无人举手，不阻塞流程）。"""
    return {
        "selected": [],
        "reason": reason or f"{PLACEHOLDER_PREFIX} 纯规则降级，未调用 AI",
    }


# ═══════════════════════════════════════════════════════════════
# 待回复请求的落盘 / 查询 / 回写
# ═══════════════════════════════════════════════════════════════

def _now_iso() -> str:
    dt = datetime.now()
    return f"{dt.year:04d}-{dt.month:02d}-{dt.day:02d} {dt.hour:02d}:{dt.minute:02d}:{dt.second:02d}"


def _digest(text: str, limit: int = 200) -> str:
    s = " ".join(str(text or "").split())
    return s[:limit]


def _path(request_id: str) -> Path:
    safe = "".join(c for c in str(request_id) if c.isalnum() or c in ("_", "-"))
    return PENDING_DIR / f"{safe}.json"


def _write_record(record: dict) -> None:
    PENDING_DIR.mkdir(parents=True, exist_ok=True)
    _path(record["request_id"]).write_text(
        json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def read_request(request_id: str) -> dict | None:
    """读取单个待回复请求记录，不存在或损坏返回 None。"""
    p = _path(request_id)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def list_pending(include_done: bool = False) -> list[dict]:
    """列出待回复请求（默认仅 status=pending），按创建时间升序。"""
    if not PENDING_DIR.exists():
        return []
    out: list[dict] = []
    for f in PENDING_DIR.glob("*.json"):
        try:
            rec = json.loads(f.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if not isinstance(rec, dict) or "request_id" not in rec:
            continue
        if not include_done and rec.get("status") != "pending":
            continue
        out.append(rec)
    out.sort(key=lambda r: float(r.get("created_ts") or 0))
    return out


def submit_request(
    kind: str,
    prompt: str,
    system_prompt: str = "",
    extra: dict | None = None,
) -> dict:
    """写入一条待外部回复的请求，返回完整记录（含 request_id）。"""
    request_id = "air_" + uuid4().hex[:12]
    record = {
        "request_id": request_id,
        "kind": kind,
        "mode": _state["mode"],
        "status": "pending",
        "prompt": prompt,
        "system_prompt": system_prompt,
        "context_digest": _digest(prompt),
        "created_at": _now_iso(),
        "created_ts": time.time(),
        "timeout_s": float(_state["timeout_s"]),
        "reply": None,
        "replied_at": None,
    }
    if extra:
        record.update(extra)
    _write_record(record)
    return record


def resolve_reply(request_id: str, reply_text: str) -> dict:
    """外部回写回复（幂等）。

    返回 {"ok": bool, ...}；request_id 不存在时 ok=False 并给出 error。
    重复回写不覆盖首次回复，返回 {"ok": True, "already": True, ...}。
    """
    p = _path(request_id)
    if not p.exists():
        return {"ok": False, "error": f"request_id 不存在: {request_id}"}
    rec = read_request(request_id)
    if rec is None:
        return {"ok": False, "error": f"request_id 记录损坏: {request_id}"}
    if rec.get("status") == "replied":
        return {
            "ok": True,
            "already": True,
            "request_id": request_id,
            "status": "replied",
            "reply": rec.get("reply"),
            "replied_at": rec.get("replied_at"),
            "message": "该 request_id 已回复过，本次未覆盖（幂等）",
        }
    rec["status"] = "replied"
    rec["reply"] = reply_text
    rec["replied_at"] = _now_iso()
    _write_record(rec)
    return {
        "ok": True,
        "already": False,
        "request_id": request_id,
        "status": "replied",
        "reply": reply_text,
        "replied_at": rec["replied_at"],
    }


def mark_status(request_id: str, status: str, reason: str = "") -> dict:
    """更新请求状态（timeout / cancelled），不覆盖已回复记录。"""
    rec = read_request(request_id)
    if rec is None:
        return {"ok": False, "error": f"request_id 不存在: {request_id}"}
    if rec.get("status") == "replied":
        return {"ok": True, "already": True, "request_id": request_id, "status": "replied"}
    rec["status"] = status
    if reason:
        rec["degrade_reason"] = reason
    _write_record(rec)
    return {"ok": True, "request_id": request_id, "status": status}


async def _wait_for_reply(request_id: str, timeout_s: float | None = None) -> str | None:
    """等待外部回写，返回回复文本；超时/被取消返回 None（并落盘状态）。

    采用「轮询文件」而非进程内 Event，保证跨进程回写（如外部工具直接写文件）同样生效。
    """
    limit = float(timeout_s if timeout_s is not None else _state["timeout_s"])
    deadline = time.monotonic() + max(MIN_TIMEOUT_S, limit)
    _t0 = time.monotonic()
    _next_beat = _t0 + 3.0
    _polls = 0
    while True:
        rec = read_request(request_id)
        if rec is None:
            print(
                f"[AI-MANUAL] request_id={request_id} 记录不可读（文件缺失或 JSON 损坏），"
                f"已轮询 {_polls} 次 / {time.monotonic() - _t0:.1f}s，等待结束",
                flush=True,
            )
            return None
        _polls += 1
        status = rec.get("status")
        if status == "replied" and rec.get("reply") is not None:
            print(
                f"[AI-MANUAL] request_id={request_id} 收到外部回写（{time.monotonic() - _t0:.1f}s，"
                f"reply 长度={len(str(rec.get('reply'))) }），继续流程",
                flush=True,
            )
            return str(rec.get("reply"))
        if status in ("timeout", "cancelled"):
            print(
                f"[AI-MANUAL] request_id={request_id} 状态={status}，等待结束返回 None",
                flush=True,
            )
            return None
        if time.monotonic() >= _next_beat:
            _next_beat += 3.0
            print(
                f"[AI-MANUAL-POLL] request_id={request_id} status={status} "
                f"elapsed={time.monotonic() - _t0:.1f}s limit={limit:g}s polls={_polls}",
                flush=True,
            )
        if time.monotonic() >= deadline:
            mark_status(request_id, "timeout", f"超过 {limit:g}s 未回写")
            print(
                f"[AI-MANUAL] request_id={request_id} 超过 {limit:g}s 未回写，"
                f"降级为纯规则占位（pending 文件保留，status=timeout）",
                flush=True,
            )
            return None
        await asyncio.sleep(POLL_INTERVAL_S)


def ai_call_timeout(local_default: float | None = None) -> float | None:
    """统一等待策略（所有内部 AI 调用点共用）。

    - manual：ai_manual_timeout + WAIT_GRACE_S（Provider 内部先落盘 timeout，外层仅兜底）
    - off   ：0，表示不等待（Provider 立即返回纯规则占位）
    - local ：LOCAL_AI_TIMEOUT_S（本机模型单次推理窗口，不再受调用点固定值约束）
    - remote：local_default（保持各调用点原有语义；None 表示不设外层超时）
    """
    mode = get_mode()
    if mode == "manual":
        base = max(MIN_TIMEOUT_S, float(get_timeout())) + WAIT_GRACE_S
        # 本地 AI 兜底可用时，允许调用点缩短外部回写等待，避免长时间空等
        if local_default is not None and local_default > 0:
            return max(MIN_TIMEOUT_S, min(base, float(local_default)))
        return base
    if mode == "off":
        return 0.0
    if mode == "local":
        # 本机模型推理远慢于云端：调用点传入的固定值（历史为硬编码 60s）一律不生效，
        # 否则会出现“本机仍在生成、外层已取消”的假失败。
        return max(MIN_TIMEOUT_S, LOCAL_AI_TIMEOUT_S)
    return local_default


def ai_mode_timeout() -> float | None:
    """按当前 ai_mode 直接给出内部 AI 调用的统一等待上限（供调用点替代硬编码固定值）。

    - manual：ai_manual_timeout + WAIT_GRACE_S（等外部回写的窗口）
    - off   ：0（不等待，Provider 立即返回纯规则占位）
    - local ：LOCAL_AI_TIMEOUT_S（本机模型推理窗口）
    - remote：REMOTE_AI_CALL_TIMEOUT_S（云端单次调用上限）
    """
    mode = get_mode()
    if mode == "manual":
        return max(MIN_TIMEOUT_S, float(get_timeout())) + WAIT_GRACE_S
    if mode == "off":
        return 0.0
    if mode == "local":
        return max(MIN_TIMEOUT_S, LOCAL_AI_TIMEOUT_S)
    return REMOTE_AI_CALL_TIMEOUT_S


async def run_ai_call(
    coro,
    local_timeout: float | None = None,
    label: str = "ai.call",
    mode_timeout: bool = False,
):
    """受控执行一次内部 AI 调用：按模式选择等待上限，超时打明确日志并抛出，绝不静默。

    mode_timeout=True 时忽略 local_timeout，改用 ai_mode_timeout()：
    调用点不再自带固定等待秒数（orchestrator 拆解阶段的历史缺陷即源于硬编码 60s）。
    """
    limit = ai_mode_timeout() if mode_timeout else ai_call_timeout(local_timeout)
    if limit is None or limit <= 0:
        return await coro
    try:
        return await asyncio.wait_for(coro, timeout=limit)
    except asyncio.TimeoutError:
        print(
            f"[AI-TIMEOUT] {label} 等待 {limit:g}s 未完成（mode={get_mode()}），转入降级流程",
            flush=True,
        )
        raise


# ═══════════════════════════════════════════════════════════════
# manual 模式 Provider
# ═══════════════════════════════════════════════════════════════

class ManualAIProvider(AIProvider):
    """manual 模式：请求落盘 → 等外部回写 → 超时降级纯规则。不阻塞事件循环。"""

    @property
    def provider_type(self) -> str:
        return "manual"

    async def chat(self, system_prompt: str, user_message: str) -> str:
        try:
            rec = submit_request("chat", user_message, system_prompt)
        except Exception as e:  # 落盘失败也不让平台崩
            return rule_placeholder("chat", f"待回复请求写入失败: {e}")
        try:
            reply = await _wait_for_reply(rec["request_id"])
        except asyncio.CancelledError:
            mark_status(rec["request_id"], "cancelled", "调用方取消（外层超时或任务中断）")
            print(
                f"[AI-MANUAL] request_id={rec['request_id']} 调用被取消，"
                f"pending 状态=cancelled，本次调用降级继续（不静默失败）",
                flush=True,
            )
            raise
        if reply is None:
            return rule_placeholder("chat", f"request_id={rec['request_id']}")
        return reply

    async def classify(
        self,
        query: str,
        candidates: list[dict],
        context: str = "",
    ) -> dict:
        prompt = (
            f"【举手判断】任务: {query}\n"
            f"上下文: {context}\n"
            f"候选: {json.dumps(candidates, ensure_ascii=False)}\n"
            f'请回复 JSON: {{"selected": ["id1"], "reason": "..."}}'
        )
        try:
            rec = submit_request(
                "classify", prompt, "", extra={"query": query, "candidates": candidates}
            )
        except Exception as e:
            return rule_classify_result(f"待回复请求写入失败: {e}")
        try:
            reply = await _wait_for_reply(rec["request_id"])
        except asyncio.CancelledError:
            mark_status(rec["request_id"], "cancelled", "调用方取消（外层超时或任务中断）")
            print(
                f"[AI-MANUAL] request_id={rec['request_id']} 调用被取消，"
                f"pending 状态=cancelled，本次调用降级继续（不静默失败）",
                flush=True,
            )
            raise
        if reply is None:
            return rule_classify_result(f"request_id={rec['request_id']}")
        try:
            start, end = reply.find("{"), reply.rfind("}") + 1
            if start >= 0 and end > start:
                data = json.loads(reply[start:end])
                return {
                    "selected": data.get("selected", []),
                    "reason": data.get("reason", str(reply)[:100]),
                }
        except Exception:
            pass
        return {"selected": [], "reason": str(reply)[:100]}

    async def chat_with_tools(
        self,
        messages: list[dict],
        tools: list[dict],
    ) -> ChatResponse:
        last_user = ""
        system_prompt = ""
        for m in messages or []:
            if m.get("role") == "system" and not system_prompt:
                system_prompt = str(m.get("content") or "")
        for m in reversed(messages or []):
            if m.get("role") == "user":
                last_user = str(m.get("content") or "")
                break
        try:
            rec = submit_request(
                "chat_with_tools",
                last_user,
                system_prompt,
                extra={"tool_count": len(tools or [])},
            )
        except Exception as e:
            return ChatResponse(content=rule_placeholder("chat_with_tools", f"待回复请求写入失败: {e}"))
        try:
            reply = await _wait_for_reply(rec["request_id"])
        except asyncio.CancelledError:
            mark_status(rec["request_id"], "cancelled", "调用方取消（外层超时或任务中断）")
            print(
                f"[AI-MANUAL] request_id={rec['request_id']} 调用被取消，"
                f"pending 状态=cancelled，本次调用降级继续（不静默失败）",
                flush=True,
            )
            raise
        if reply is None:
            return ChatResponse(content=rule_placeholder("chat_with_tools", f"request_id={rec['request_id']}"))
        return ChatResponse(content=reply)


# ═══════════════════════════════════════════════════════════════
# off 模式 Provider（纯规则，绝不调用 AI、绝不落盘请求）
# ═══════════════════════════════════════════════════════════════

class OffAIProvider(AIProvider):
    """off 模式：任何 AI 调用直接返回纯规则占位，不产生待回复请求。"""

    @property
    def provider_type(self) -> str:
        return "off"

    async def chat(self, system_prompt: str, user_message: str) -> str:
        return rule_placeholder("chat")

    async def classify(
        self,
        query: str,
        candidates: list[dict],
        context: str = "",
    ) -> dict:
        return rule_classify_result()

    async def chat_with_tools(
        self,
        messages: list[dict],
        tools: list[dict],
    ) -> ChatResponse:
        return ChatResponse(content=rule_placeholder("chat_with_tools"))


# ═══════════════════════════════════════════════════════════════
# 工厂
# ═══════════════════════════════════════════════════════════════

def create_external_provider(mode: str, timeout_s=None) -> AIProvider:
    """创建 manual / off Provider，并同步全局模式。"""
    m = set_mode(mode, timeout_s)
    if m == "manual":
        return ManualAIProvider()
    if m == "off":
        return OffAIProvider()
    raise ValueError(f"create_external_provider 仅支持 manual / off，收到: {mode}")
