# -*- coding: utf-8 -*-
"""platform/ai_verify：AI 连通性自检（V-16 §四，2026-10-05 负责人拍板实施）。

**为什么要它**：V-16 审查 P1-4 的延伸 —— **保存成功 ≠ 能用**。
用户点「保存」看到成功，实际 provider 可能连不上（401 无效 key / 404 路径错 /
403 权限 / 超时），旧实现没有任何提示，于是"接了 X 就不行了"而界面一切正常。
本模块真的发一次**极小**探针（`max_tokens=1`），给出可读结论。

**设计红线（负责人拍板）**：
1. **默认不产生计费流量** —— `ai_verify_on_save` / `ai_verify_on_startup` 两个配置键
   **默认均为 False**；两个时机都要显式打开才发请求。
   ⚠️ 负责人原话是「启动时拉取」+「默认关避免计费」，两者有张力 —— 本实现按
   **不产生默认计费** 优先，故启动自检也做成开关。要默认开只需改 DEFAULT_CONFIG 一行。
2. **TTL 缓存 10 分钟**（`ai_verify_cache_ttl`，默认 600s）：同一
   provider+base_url+model+key 指纹在 TTL 内命中缓存，**不重复出网、不重复计费**。
3. 走 V-14 `network.egress` 闸门（出网留痕，同 `/api/ai/models`）。
4. **绝不抛异常**：任何失败都转成 `ok=False + 可读 reason`（调用点在保存链路与启动链路，
   抛异常会毁掉主流程）。
5. 只发一次、`max_tokens=1`、无工具调用 —— 费用可忽略。

**调用方式（两层，别用错）**：
- 在 async 代码里（FastAPI 端点 / lifespan）用 `await verify_provider_async(...)`；
- 在同步代码里用 `verify_provider(...)`（内部 `asyncio.run`，**不能在事件循环内调**）。

缓存键含 **key 的 sha256 前 16 位**（不存明文 key）。
"""
from __future__ import annotations

import hashlib
import threading
import time
from typing import Any, Optional

# 单次探针超时（秒）。⚠️ 2026-10-05 实测修正：
#   原设计稿写"超时 ≤8s"，查表口径抄的是 /api/ai/models 的 8.0s —— 但**那是列清单**，
#   而这里是**一次真实 chat 调用**。实测真实服务（mimo 线）`max_tokens=1` 也要
#   **37.8s** 才回（不带 max_tokens 54.2s；列清单仅 0.37s）。
#   8s 会把"慢但能用"误判成"连不上" —— 诊断结论直接反了。故默认放宽到 **45s**
#   （留余量），并可用配置键 `ai_verify_timeout` 覆盖（慢服务可再调大）。
VERIFY_TIMEOUT_S = 45.0
# `max_tokens=1`：只验证"通不通"，不实际生成内容（费用可忽略）
VERIFY_MAX_TOKENS = 1

_lock = threading.RLock()
# cache_key -> {"ts": float, "result": dict}
_cache: dict[str, dict] = {}


def cache_ttl(cfg: Optional[dict] = None) -> float:
    """自检结论的有效期（秒），配置键 `ai_verify_cache_ttl`，默认 600s（10 分钟）。"""
    try:
        if cfg is None:
            from ..config import load_config
            cfg = load_config()
        return max(0.0, float(cfg.get("ai_verify_cache_ttl", 600)))
    except Exception:
        return 600.0


def probe_timeout(cfg: Optional[dict] = None) -> float:
    """探针超时（秒），配置键 `ai_verify_timeout`，默认 30s（见模块头实测依据）。"""
    try:
        if cfg is None:
            from ..config import load_config
            cfg = load_config()
        return max(1.0, float(cfg.get("ai_verify_timeout", VERIFY_TIMEOUT_S)))
    except Exception:
        return VERIFY_TIMEOUT_S


def _fingerprint(provider_type: str, base_url: str, model: str, api_key: str) -> str:
    """缓存指纹：含 key 的 sha256 前 16 位，**不存明文**。"""
    key_digest = hashlib.sha256(str(api_key or "").encode("utf-8")).hexdigest()[:16]
    raw = "|".join([str(provider_type or ""), str(base_url or ""),
                    str(model or ""), key_digest])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def _classify(status: int, text: str) -> str:
    """把 HTTP 结果翻成用户能懂的一句话（V-16 §四 的"可读原因"）。"""
    if status == 401:
        return "密钥无效或未授权（HTTP 401）"
    if status == 403:
        return "密钥无权访问该模型或该端点（HTTP 403）"
    if status == 404:
        return ("接口路径或模型不存在（HTTP 404）—— 检查 base_url 是否多/少 /v1，"
                "或模型名是否正确")
    if status == 429:
        return "触发限流或额度不足（HTTP 429）"
    if status >= 500:
        return f"服务端错误（HTTP {status}）"
    low = (text or "").lower()
    if "unsupported model" in low or "model not found" in low or "invalid model" in low:
        return f"服务不认这个模型名（HTTP {status}）"
    return f"HTTP {status}"


def _precheck(provider_type: str, base_url: str, model: str) -> tuple[str, str, Optional[dict]]:
    """不需要出网的静态检查。返回 (归一化 base_url, 拒绝原因 or "", 结果 dict or None)。"""
    norm = str(base_url or "").strip()
    model = str(model or "").strip()
    ptype = str(provider_type or "openai").strip().lower()
    if ptype in ("manual", "off", "local"):
        return norm, f"当前 ai_mode/provider={ptype}，不需要也不应发云端探针", None
    if not norm:
        return norm, "未配置 base_url", None
    if not model:
        return norm, "未配置模型名（ai_model）", None
    try:
        from .ai_provider import _normalize_base_url as _norm
        norm = _norm(norm)
    except Exception:
        pass
    return norm, "", None


async def _probe_once(norm: str, model: str, api_key: str, timeout: float) -> dict:
    """真发一次极小探针（async，供 async 调用方直接 await）。"""
    started = time.time()
    try:
        import httpx as _httpx
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        body = {
            "model": model,
            "messages": [{"role": "user", "content": "ping"}],
            "max_tokens": VERIFY_MAX_TOKENS,
        }
        async with _httpx.AsyncClient(timeout=timeout) as c:
            r = await c.post(f"{norm}/v1/chat/completions", json=body, headers=headers)
        elapsed = int((time.time() - started) * 1000)
        status = int(r.status_code)
        ok = 200 <= status < 300
        return {"ok": ok, "status": status, "elapsed_ms": elapsed, "cached": False,
                "reason": "" if ok else _classify(status, r.text or "")}
    except Exception as e:
        elapsed = int((time.time() - started) * 1000)
        low = f"{type(e).__name__}: {e}".lower()
        if "timeout" in low or "timed out" in low:
            reason = f"连接超时（{timeout:g}s）"
        elif "connect" in low or "dns" in low or "name or service" in low:
            reason = f"连不上服务：{type(e).__name__}"
        else:
            reason = f"{type(e).__name__}: {e}"
        return {"ok": False, "status": 0, "elapsed_ms": elapsed, "cached": False,
                "reason": reason}


def _audit(actor: str, norm: str, model: str, result: dict) -> None:
    try:
        from .audit import audit_log as _audit_log
        _audit_log.record(
            "ai.verify", actor=actor, target=norm,
            detail=(f"model={model} ok={result.get('ok')} status={result.get('status')} "
                    f"cached={result.get('cached')} elapsed={result.get('elapsed_ms')}ms "
                    f"reason={result.get('reason') or '-'}"))
    except Exception:
        pass


async def verify_provider_async(provider_type: str, base_url: str, model: str,
                                api_key: str, timeout: float | None = None,
                                use_cache: bool = True, cfg: Optional[dict] = None,
                                actor: str = "system") -> dict:
    """**async 调用方用这个**（FastAPI 端点 / lifespan）。永不抛异常。

    `timeout=None` 时取配置键 `ai_verify_timeout`（默认 30s）。
    """
    norm, reject, _ = _precheck(provider_type, base_url, model)
    model = str(model or "").strip()
    if timeout is None:
        timeout = probe_timeout(cfg)
    out = {"model": model, "base_url": norm, "cached": False, "at": time.time()}
    if reject:
        return {**out, "ok": False, "status": 0, "elapsed_ms": 0, "reason": reject}

    fp = _fingerprint(provider_type, norm, model, api_key)
    ttl = cache_ttl(cfg)
    now = time.time()
    if use_cache:
        with _lock:
            hit = _cache.get(fp)
        if hit and (now - float(hit.get("ts") or 0)) < ttl:
            return {**hit["result"], "cached": True, "at": now}

    try:
        from .policy import NETWORK_EGRESS, blocked_message, check, pending_message
        _ev, _pending = check(NETWORK_EGRESS, target=norm, actor=actor)
        if _ev.blocked:
            return _store(fp, use_cache, {**out, "ok": False, "status": 0,
                                          "elapsed_ms": 0, "reason": blocked_message(_ev)})
        if _ev.needs_approval:
            return _store(fp, use_cache, {**out, "ok": False, "status": 0,
                                          "elapsed_ms": 0,
                                          "reason": pending_message(_ev, _pending)})
    except Exception as _pe:
        print(f"[ai_verify] 出网闸门检查异常，继续探测: {_pe}", flush=True)

    result = {**out, **await _probe_once(norm, model, api_key, timeout)}
    _audit(actor, norm, model, result)
    return _store(fp, use_cache, result)


def _store(fp: str, use_cache: bool, result: dict) -> dict:
    if use_cache:
        with _lock:
            _cache[fp] = {"ts": time.time(), "result": result}
    return result


def verify_provider(provider_type: str, base_url: str, model: str, api_key: str,
                    timeout: float | None = None, use_cache: bool = True,
                    cfg: Optional[dict] = None, actor: str = "system") -> dict:
    """同步调用方用这个（内部 `asyncio.run`）。**绝不能在事件循环内调用。**"""
    import asyncio
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(verify_provider_async(
            provider_type, base_url, model, api_key, timeout, use_cache, cfg, actor))
    # 在事件循环内 -> 明确报错而不是静默死锁（调用方应改用 verify_provider_async）
    return {"ok": False, "status": 0, "cached": False, "elapsed_ms": 0,
            "model": str(model or ""), "base_url": str(base_url or ""),
            "at": time.time(),
            "reason": "内部错误：事件循环内请改用 verify_provider_async"}


def clear_cache() -> int:
    """清空自检缓存（测试与"立刻重测"用）。返回清掉的条数。"""
    with _lock:
        n = len(_cache)
        _cache.clear()
    return n


def cache_info() -> dict:
    """自省：缓存条数与 TTL（指纹只给前 12 位，不含任何明文）。"""
    with _lock:
        return {"size": len(_cache), "ttl_s": cache_ttl(),
                "keys": [k[:12] for k in _cache]}
