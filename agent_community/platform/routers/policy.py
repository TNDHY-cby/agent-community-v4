# -*- coding: utf-8 -*-
"""platform/routers/policy：策略自省与待审批接口（V-14 步骤4）。

设计稿：design-docs/V14_安全治理策略引擎设计.md §3.3 / §五 步骤4

刻意只提供**人工**审批通道：
- 审批端点用 `actor_from_request()` 判定调用方，`harness`（外端 AI）一律 **403 forbidden**
- 理由（拍板结论）：信任根不能被可被 prompt injection 的组件替代。
  第二通道的平台 AI 在第二片也只给「建议权」，不给决定权。

端点：
- GET  /api/policy                       策略现状（配置来源 / 分类默认 / 规则 / 待办数）
- GET  /api/policy/pending               待审批列表
- POST /api/policy/pending/{pid}/resolve 人工批准或拒绝（body: {approve: bool, note: str}）
- POST /api/policy/reload                重读 policy.json（改文件后立即可用）
"""
from __future__ import annotations

from fastapi import APIRouter, Request

from ..policy import (
    ACTOR_HARNESS,
    ACTOR_OPERATOR,
    actor_from_request,
    policy_engine,
)

router = APIRouter()


def _resp(payload: dict, status_code: int = 200):
    from ..server import Utf8JSONResponse  # 延迟引用，避免循环导入
    if status_code == 200:
        return payload
    return Utf8JSONResponse(payload, status_code=status_code)


@router.get("/api/policy")
async def api_policy_describe():
    """策略现状自省：让操作者能看清「现在到底按什么规则在放行/拦截」。"""
    return policy_engine.describe()


@router.get("/api/policy/pending")
async def api_policy_pending(all: int = 0):
    """待审批列表。`?all=1` 连已处理的也返回（历史查阅）。"""
    items = policy_engine.list_pending(only_pending=not bool(all))
    return {"pending": items, "count": len(items)}


@router.post("/api/policy/pending/{pending_id}/resolve")
async def api_policy_resolve(pending_id: str, request: Request):
    """人工批准/拒绝一个待办。

    body: ``{"approve": true|false, "note": "..."}``

    **AI 无决定权**：带 `X-Agent-Token` 的调用方一律 403。
    """
    caller = actor_from_request(request)
    if caller == ACTOR_HARNESS:
        return _resp({
            "error": "forbidden",
            "detail": "审批权仅限人工。外端 AI 无决定权（第二片只给建议权，见设计稿 §3.3）。",
        }, 403)

    try:
        body = await request.json()
    except Exception:
        body = {}
    approve = bool(body.get("approve"))
    note = str(body.get("note") or "")

    out = policy_engine.resolve(pending_id, approve, by=ACTOR_OPERATOR, note=note)
    if out.get("error") == "not_found":
        return _resp(out, 404)
    if out.get("error") == "already_resolved":
        return _resp(out, 409)
    if out.get("error"):
        return _resp(out, 403)

    # 审计（审批也是决策，必须留痕）
    try:
        from ..audit import audit_log
        rec = out.get("record") or {}
        audit_log.record(
            event="policy.approval",
            actor=ACTOR_OPERATOR,
            target=(rec.get("target") or "")[:200],
            detail=(f"pending={pending_id} op={rec.get('op')} "
                    f"result={rec.get('status')} note={note}"),
        )
    except Exception as e:
        print(f"[policy] 记审批审计失败（忽略）: {e}", flush=True)

    return out


@router.post("/api/policy/reload")
async def api_policy_reload(request: Request):
    """重读 policy.json —— 证明「改配置文件即改策略」，不必重启服务。"""
    caller = actor_from_request(request)
    if caller == ACTOR_HARNESS:
        return _resp({"error": "forbidden", "detail": "改策略是运维动作，外端 AI 不可调用。"}, 403)
    return policy_engine.reload()
