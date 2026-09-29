"""platform/routers/config：配置与 AI Provider 端点组（V-9 自 server.py 拆分）。

拆分原则：
- 可变全局状态统一引用 ..state（python -m 双副本共享同一组对象）
- server 内部辅助函数/常量在函数体内延迟 from ..server import（避免循环导入）
- ai_provider / ai_provider_config 由 __main__ 副本持有，经 _sv() 访问
"""
from __future__ import annotations

import sys

from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect

router = APIRouter()


def _sv():
    """返回活跃 server 副本（__main__，uvicorn 实际运行的实例）模块。"""
    return sys.modules["__main__"]


# ── 独立模块引用（与 server.py 共享同一实例）──
from ..ai_external import VALID_MODES as AI_MODES
from ..ai_external import get_mode as ai_external_get_mode
from ..ai_external import get_timeout as ai_external_get_timeout
from ..ai_external import list_pending as ai_external_list_pending
from ..ai_external import resolve_reply as ai_external_resolve_reply
from ..ai_external import set_mode as ai_external_set_mode
from ..ai_provider import create_ai_provider
from ...config import load_config
from ...config import mask_api_key
import os
from ...config import save_config
from ..harness_adapter import set_internal_ai_provider

# ── 共享可变状态（..state 单例，双副本一致）──
from ..state import agents

@router.get("/api/ai/providers")
async def api_ai_providers():
    """v6 新增：返回当前可用的 AI Provider 列表和状态"""
    _m = _sv()
    _provider = getattr(_m, "ai_provider", None)
    providers_status = []
    # 已激活的 provider（AI Provider 重建失败为 None 时兜底，不抛 AttributeError）
    if _provider:
        provider_info = {
            "type": _provider.provider_type,
            "active": True,
        }
        if hasattr(_provider, "model"):
            provider_info["model"] = _provider.model
        if hasattr(_provider, "base_url"):
            provider_info["base_url"] = _provider.base_url
        if hasattr(_provider, "host"):
            provider_info["host"] = _provider.host
        if hasattr(_provider, "callback_url"):
            provider_info["callback_url"] = _provider.callback_url
        providers_status.append(provider_info)
    # 所有可用类型
    available_types = [
        {
            "type": "openai",
            "description": "OpenAI 兼容 API（DeepSeek / OpenAI / Claude / Gemini 等）",
            "env_vars": ["AC_AI_BASE_URL", "AC_AI_API_KEY", "AC_AI_MODEL"],
            "default_base_url": "https://api.deepseek.com",
            "default_model": "deepseek-chat",
        },
        {
            "type": "ollama",
            "description": "本地 Ollama",
            "env_vars": ["AC_OLLAMA_HOST", "AC_OLLAMA_MODEL"],
            "default_host": "http://localhost:11434",
            "default_model": "qwen2.5:7b",
        },
        {
            "type": "http_callback",
            "description": "HTTP 回调方式（向外部 URL POST 请求）",
            "env_vars": ["AC_HTTP_CALLBACK_URL"],
            "requires_callback_url": True,
        },
    ]
    _provider_cfg = getattr(_m, "ai_provider_config", None) or {}
    safe_config = dict(_provider_cfg)
    if safe_config.get("api_key"):
        safe_config["api_key"] = mask_api_key(safe_config["api_key"])
    return {
        "ai_provider_enabled": _provider is not None,
        "ai_mode": ai_external_get_mode(),
        "ai_manual_timeout": ai_external_get_timeout(),
        "available_ai_modes": list(AI_MODES),
        "current_provider": providers_status[0] if providers_status else None,
        "config": safe_config,
        "available_providers": available_types,
        "wakeup_agent_registered": "wakeup-agent" in agents,
    }

@router.get("/api/config/status")
async def api_config_status():
    """返回当前配置状态：是否已配置、provider 类型、模型。"""
    cfg = load_config()
    provider_type = cfg.get("ai_provider", "")
    model = cfg.get("ai_model", "")
    api_key = cfg.get("ai_api_key", "")
    configured = bool(provider_type and api_key)
    return {
        "configured": configured,
        "provider_type": provider_type,
        "model": model,
        "ai_mode": cfg.get("ai_mode", "remote"),
        "ai_manual_timeout": cfg.get("ai_manual_timeout", 120),
    }

@router.get("/api/config")
async def api_get_config():
    """返回当前配置（API Key 脱敏）。"""
    cfg = load_config()
    safe = dict(cfg)
    if safe.get("ai_api_key"):
        safe["ai_api_key"] = mask_api_key(safe["ai_api_key"])
    return {"config": safe}

@router.post("/api/config")
async def api_save_config(request: Request):
    from ..server import Utf8JSONResponse
    """接收配置并保存，保存后重新加载 AI Provider。"""
    body = await request.json()
    cfg = load_config()
    provider_type = str(body.get("ai_provider") or cfg.get("ai_provider") or "").strip()
    ai_mode = str(body.get("ai_mode") or cfg.get("ai_mode", "remote")).strip().lower()
    if ai_mode not in AI_MODES:
        return Utf8JSONResponse(
            {"error": f"ai_mode 非法，支持 {'/'.join(AI_MODES)}"}, status_code=400
        )
    manual_timeout = body.get("ai_manual_timeout", cfg.get("ai_manual_timeout", 120))
    base_url = body.get("ai_base_url") or cfg.get("ai_base_url", "")
    # V-9 修复：运行时 key 统一 env > body > cfg（env 永不落盘覆盖）
    api_key = body.get("ai_api_key") or os.environ.get("AC_AI_API_KEY") or cfg.get("ai_api_key", "")
    model = body.get("ai_model") or cfg.get("ai_model", "")
    temperature = body.get("ai_temperature", cfg.get("ai_temperature", 1.0))
    thinking = body.get("ai_thinking", cfg.get("ai_thinking", False))
    wakeup_enabled = body.get("wakeup_enabled", cfg.get("wakeup_enabled", False))
    port = body.get("port", cfg.get("port", 9103))
    # remote 模式必须有 provider 类型；manual / off 为无额度期间的降级通道，不强制
    # V-18 修复：body 显式传空 ai_provider 时即使 cfg 有默认值也拒绝（防 500 掩盖 400 校验意图）
    if "ai_provider" in body:
        _body_provider = str(body.get("ai_provider") or "").strip()
        if ai_mode == "remote" and not _body_provider:
            return Utf8JSONResponse({"error": "ai_provider 不能为空"}, status_code=400)
    if ai_mode == "remote" and not provider_type:
        return Utf8JSONResponse({"error": "ai_provider 不能为空"}, status_code=400)
    # 持久化配置
    # V-9 修复：env 值永不落盘覆盖——存在 AC_AI_API_KEY 时配置文件保存空 key，运行时仍取 env
    env_key = os.environ.get("AC_AI_API_KEY", "")
    persist_key = "" if env_key else (api_key if api_key else cfg.get("ai_api_key", ""))
    save_config({
        "ai_provider": provider_type,
        "ai_base_url": base_url,
        "ai_api_key": persist_key,
        "ai_model": model,
        "ai_mode": ai_mode,
        "ai_manual_timeout": manual_timeout,
        "ai_temperature": temperature,
        "ai_thinking": thinking,
        "wakeup_enabled": wakeup_enabled,
        "port": port,
    })
    # 重新加载 AI Provider
    # V-18 修复：__main__ 副本属性可能缺失/为 None，一律 getattr 兜底，禁止裸访问抛 AttributeError
    _m = _sv()
    _provider_cfg = getattr(_m, "ai_provider_config", None) or {}
    _provider_cfg.clear()
    _provider_cfg.update({
        "type": provider_type or ai_mode,
        "base_url": base_url,
        "api_key": api_key,
        "model": model,
        "mode": ai_mode,
        "manual_timeout": manual_timeout,
    })
    try:
        ai_external_set_mode(ai_mode, manual_timeout)
        # manual/off 接管模式必须覆盖配置里的 provider_type（否则会误走云端真实 API）
        _eff_type = ai_mode if ai_mode in ("manual", "off") else (provider_type or "openai")
        _new_provider = create_ai_provider(
            provider_type=_eff_type,
            base_url=base_url,
            api_key=api_key,
            model=model,
            manual_timeout=manual_timeout,
        )
        setattr(_m, "ai_provider", _new_provider)
        set_internal_ai_provider(_new_provider)
        print(f"[Config] AI Provider 已重新加载: {_new_provider.provider_type} (ai_mode={ai_mode})")
    except Exception as e:
        print(f"[Config] AI Provider 重新加载失败: {e}")
        setattr(_m, "ai_provider", None)
    return {
        "success": True,
        "configured": bool(provider_type and api_key),
        "provider_type": provider_type,
        "model": model,
        "ai_mode": ai_mode,
        "ai_manual_timeout": manual_timeout,
    }

@router.get("/api/ai/pending")
async def api_ai_pending():
    """列出待外部接管的 AI 请求（manual 模式下由平台写入 data/ai_pending/*.json）。
    V-7 安全收敛：仅返回脱敏摘要（request_id/时间/提示摘要），
    不返回 prompt/system_prompt 全文与 extra 上下文，避免列表接口泄露完整对话内容。
    """
    pending = []
    for rec in ai_external_list_pending():
        pending.append({
            "request_id": rec.get("request_id", ""),
            "kind": rec.get("kind", ""),
            "mode": rec.get("mode", ""),
            "status": rec.get("status", "pending"),
            "created_at": rec.get("created_at", ""),
            "created_ts": rec.get("created_ts", 0),
            "timeout_s": rec.get("timeout_s", 0),
            "prompt_digest": str(rec.get("context_digest") or "")[:120],
            "prompt_excerpt": str(rec.get("prompt") or "")[:300],
        })
    return {
        "success": True,
        "ai_mode": ai_external_get_mode(),
        "ai_manual_timeout": ai_external_get_timeout(),
        "pending_count": len(pending),
        "pending": pending,
    }

@router.post("/api/ai/reply")
async def api_ai_reply(request: Request):
    from ..server import Utf8JSONResponse
    """外部 AI 助手回写 AI 回复，平台按原流程继续。

    body: {"request_id": "air_xxx", "reply": "回复文本"}
    幂等：同一 request_id 重复回写不覆盖首次回复；request_id 不存在返回 404。
    """
    body = await request.json()
    request_id = str(body.get("request_id") or "").strip()
    if not request_id:
        return Utf8JSONResponse({"error": "request_id 不能为空"}, status_code=400)
    if body.get("reply") is None:
        return Utf8JSONResponse({"error": "reply 不能为空"}, status_code=400)
    result = ai_external_resolve_reply(request_id, str(body.get("reply")))
    if not result.get("ok"):
        return Utf8JSONResponse(result, status_code=404)
    return result
