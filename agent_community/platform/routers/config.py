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
from ..audit import audit_log as _audit_log

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
            "default_base_url": "https://api.example.com",
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

@router.get("/api/ai/models")
async def api_ai_models(request: Request, base_url: str = ""):
    """V-16 §3.1：拉远端模型清单 —— 设置页「模型」下拉的数据源。

    **为什么必须有它**：下拉原先只读前端硬编码 `presets`，
    接新服务（如 mimo）时必然空清单，用户没有任何可选项。
    实测 mimo 的 `GET /v1/models` 返回标准 OpenAI 格式，完全可用。

    设计要点：
    - **用服务端已存的解密 key** —— 前端拿到的只是 `mask_api_key` 掩码，
      由它来发请求只会 401；这也顺带避免密钥出现在前端。
    - **归一化 base_url**（同 P0-1 修复），否则 `/v1` 重复 -> 404。
    - **失败也返回 200** + `fallback:true`：设置页的唯一目标是让用户能选到模型，
      拉不到时回落内置清单 + 给可读原因，比抛错让下拉空着更符合用途。
    - 走 V-14 `network.egress` 闸门（出厂 ALLOW，不造成 UI 摩擦）。
    """
    from ..server import Utf8JSONResponse
    import httpx as _httpx

    from ..ai_provider import _normalize_base_url as _norm
    # ⚠️ 三个点：本文件在 platform/routers/ 下，`..` 是 platform，
    #   agent_community.config 要用 `...`（两个点会解析成 platform.config -> 500）
    from ...config import _decrypt_secret, load_config

    cfg = load_config()
    _base = str(base_url or "").strip() or str(cfg.get("ai_base_url") or "")
    if not _base:
        return {"ok": False, "models": [], "source": "builtin",
                "fallback": True, "error": "未配置 base_url"}
    norm = _norm(_base)

    # ── V-14 出网闸门 ──
    from ..policy import (NETWORK_EGRESS, actor_from_request, blocked_message,
                          check, pending_message)
    _ev, _pending = check(NETWORK_EGRESS, target=norm,
                          actor=actor_from_request(request))
    if _ev.blocked:
        return Utf8JSONResponse({"ok": False, "models": [], "source": "builtin",
                                 "fallback": True, "error": blocked_message(_ev)},
                                status_code=403)
    if _ev.needs_approval:
        return Utf8JSONResponse({"ok": False, "models": [], "source": "builtin",
                                 "fallback": True,
                                 "error": pending_message(_ev, _pending)},
                                status_code=202)

    # ── key：只在服务端解密，不回传 ──
    _enc = str(cfg.get("ai_api_key") or "")
    _key = _decrypt_secret(_enc) if _enc else ""

    try:
        async with _httpx.AsyncClient(timeout=8.0) as c:
            r = await c.get(f"{norm}/v1/models",
                            headers=({"Authorization": f"Bearer {_key}"} if _key else {}))
        if r.status_code != 200:
            return {"ok": False, "models": [], "source": "builtin", "fallback": True,
                    "error": f"GET {norm}/v1/models -> HTTP {r.status_code}"}
        ids = [str(m.get("id") or "") for m in
               (r.json().get("data") or []) if isinstance(m, dict)]
        ids = [x for x in ids if x]
        if not ids:
            return {"ok": False, "models": [], "source": "builtin", "fallback": True,
                    "error": "服务返回空模型清单"}
        try:
            _audit_log.record("ai.models_fetch", actor=actor_from_request(request),
                              target=norm, detail=f"models={len(ids)}")
        except Exception:
            pass
        return {"ok": True, "models": ids, "source": "remote", "fallback": False}
    except Exception as e:
        return {"ok": False, "models": [], "source": "builtin", "fallback": True,
                "error": f"{type(e).__name__}: {e}"}


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
    """返回当前配置（API Key 脱敏）。

    V-16 §四：额外回传三个自检开关与最近一次自检结论，供设置页显示
    「✅ 已连通 / ⚠️ 保存了但连不上：<原因>」。结论只是**缓存里的最近一次**，
    不在这里发请求（GET 不该产生计费流量）。
    """
    cfg = load_config()
    safe = dict(cfg)
    if safe.get("ai_api_key"):
        safe["ai_api_key"] = mask_api_key(safe["ai_api_key"])
    return {
        "config": safe,
        "ai_verify": _last_verify_snapshot(cfg),
    }


def _last_verify_snapshot(cfg: dict) -> dict:
    """最近一次自检结论（不改状态、不发请求）。"""
    try:
        from ..ai_verify import cache_info, cache_ttl
        info = cache_info()
        return {
            "on_save": bool(cfg.get("ai_verify_on_save", False)),
            "on_startup": bool(cfg.get("ai_verify_on_startup", False)),
            "cache_ttl_s": int(cache_ttl(cfg)),
            "cached_results": int(info.get("size") or 0),
        }
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}

@router.post("/api/config")
async def api_save_config(request: Request):
    _audit_log.record("config.save", actor="user", target="", detail="保存平台配置")
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
    # ── V-16 修复（P0：会摧毁密钥）────────────────────────────────────
    # `GET /api/config` 返回的是 **mask_api_key 后的掩码**，前端把它回填进
    # API KEY 输入框 -> 用户看到 `sk-c****…****njzs`、点眼睛也只是把 type 改成
    # text（**值本来就是掩码**，看不出问题）-> 一旦点「保存」，
    # `body["ai_api_key"]` 就是这段掩码 -> **真 key 被掩码覆盖**，永久丢失。
    #
    # 判定：提交值含 '*' 即视为掩码（OpenAI/DeepSeek/mimo 系 key 由字母数字
    # 与 `-`/`_` 组成，不会含 `*`）-> 忽略它，保留已存 key。
    _in_key = str(body.get("ai_api_key") or "")
    _key_was_masked = "*" in _in_key
    if _key_was_masked:
        api_key = (os.environ.get("AC_AI_API_KEY", "")
                   or cfg.get("ai_api_key", ""))
    else:
        api_key = _in_key or os.environ.get("AC_AI_API_KEY", "") or cfg.get("ai_api_key", "")
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
    # ⚠️ V-16 §四 实测修正：自检开关先算出「本次生效值」再落盘 ——
    #    否则 `_verify_after_save` 会拿**保存前**的 cfg 判断开关，
    #    用户刚勾上「保存时自检」的那一次反而不自检（恰好是最需要自检的一次）。
    _verify_on_save = bool(body.get("ai_verify_on_save", cfg.get("ai_verify_on_save", False)))
    _verify_on_startup = bool(body.get("ai_verify_on_startup",
                                       cfg.get("ai_verify_on_startup", False)))
    _verify_cache_ttl = body.get("ai_verify_cache_ttl", cfg.get("ai_verify_cache_ttl", 600))
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
        # V-16 §四：自检开关随配置持久化（默认关；空值回退已有配置，避免被冲掉）
        "ai_verify_on_save": _verify_on_save,
        "ai_verify_on_startup": _verify_on_startup,
        "ai_verify_cache_ttl": _verify_cache_ttl,
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
    _new_provider = None
    _provider_error = ""
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
        _provider_error = str(e)
        setattr(_m, "ai_provider", None)

    # ── 诚实回报（V-16 审查 P1-4 / P1-5）──────────────────────────
    # 原实现：失败也返回 `success:true` 且**没有任何错误字段** ——
    # 用户点保存看到成功，实际 `ai_provider` 已是 None，**收不到一丝提示**，
    # 于是"接了 X 就不行了"而界面一切正常。
    # 这里**不动** `configured` 的既有语义（会牵动未追完的前端分支），
    # 改用几个增量字段，前端据此如实显示。
    #
    # P1-5 修正：`load_config()` 会合并 `DEFAULT_CONFIG`（ai_model 默认
    # example-model-flash），所以**生效 model 永远非空**，按生效值判 model_missing
    # 几乎不可达。真正有意义的是「**用户本次有没有真选**」——
    # 下拉停在"请选择模型"时提交的是空值，此时虽有默认兜底，也该告诉用户
    # 「你没选，实际用的是 XXX」，而不是让他以为自己选上了。
    _model_selected = bool(str(body.get("ai_model") or "").strip())
    return {
        "success": True,
        "configured": bool(provider_type and api_key),
        "provider_type": provider_type,
        "model": model,
        "ai_mode": ai_mode,
        "ai_manual_timeout": manual_timeout,
        # provider 是否真的起来了（失败时 False）
        "provider_loaded": _new_provider is not None,
        "provider_error": _provider_error,
        # 本次提交是否选了模型（空 -> 已回退到 model 字段的实际值）
        "model_missing": bool(ai_mode == "remote" and not _model_selected),
        "model_effective": model,
        "base_url": base_url,
        # 提交的 key 是否是掩码（已忽略、保留原 key）—— 前端据此提示
        "api_key_masked_ignored": _key_was_masked,
        # ── V-16 §四 连通性自检（2026-10-05 负责人拍板实施）──────────────
        # **保存成功 ≠ 能用**：真发一次极小探针（max_tokens=1）给出可读结论。
        # ⚠️ 默认关（ai_verify_on_save=False）：负责人要求"避免默认产生计费流量"。
        # 开了才出网；结论按 base_url+model+key 指纹缓存 TTL（默认 10 分钟）。
        **await _verify_after_save(
            {"ai_verify_on_save": _verify_on_save,
             "ai_verify_cache_ttl": _verify_cache_ttl},
            provider_type, base_url, api_key, model, ai_mode,
            _new_provider is not None),
    }

async def _verify_after_save(cfg: dict, provider_type: str,
                             base_url: str, api_key: str, model: str,
                             ai_mode: str, provider_loaded: bool) -> dict:
    """保存后的连通性自检（V-16 §四）。**永不抛异常、绝不影响保存结果。**

    返回要并入响应体的自检字段；未开启或无需自检时 `provider_verified=None`
    （前端据此不显示结论）。

    ⚠️ `cfg` 必须传**本次生效值**（不是 `load_config()` 的旧对象）——
    实测踩过：传旧 cfg 会让"用户刚勾上保存时自检"的那一次不自检。

    触发条件（任一不满足即跳过，避免无谓出网）：
      - `ai_verify_on_save` 为真；
      - `ai_mode=remote`（manual/off/local 不发云端探针）；
      - provider 已加载成功（加载都失败就没必要再探）。
    """
    try:
        _on = bool(cfg.get("ai_verify_on_save", False))
        if not _on or ai_mode != "remote" or not provider_loaded:
            return {"provider_verified": None, "provider_verify_reason": "",
                    "provider_verified_at": 0}
        from ..ai_verify import cache_ttl, clear_cache, verify_provider_async
        # 关键：保存可能换了 key/base/model —— 旧结论立刻失效，否则会拿旧指纹的结论冒充
        clear_cache()
        actor = "user"
        res = await verify_provider_async(provider_type, base_url, model, api_key,
                                          use_cache=False, cfg=cfg, actor=actor)
        return {
            "provider_verified": bool(res.get("ok")),
            "provider_verify_reason": str(res.get("reason") or ""),
            "provider_verified_at": float(res.get("at") or 0),
            "provider_verify_elapsed_ms": int(res.get("elapsed_ms") or 0),
            "provider_verify_cache_ttl": int(cache_ttl(cfg)),
        }
    except Exception as _ve:
        # 自检是附加信息，任何异常都不能让保存失败
        print(f"[Config] 连通性自检失败（不影响保存）: {_ve}", flush=True)
        return {"provider_verified": None,
                "provider_verify_reason": f"自检本身出错：{type(_ve).__name__}",
                "provider_verified_at": 0}

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
    _audit_log.record("config.ai_reply", actor="user", target="", detail="人工代答 AI 回复")
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
