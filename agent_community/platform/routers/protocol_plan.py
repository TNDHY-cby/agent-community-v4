# -*- coding: utf-8 -*-
"""`routers/protocol_plan`：**多协议选中的"这套组合能干什么"**（V132，老板 2026-11-06）。

一个端点，只读：

    POST /api/docking/protocol-plan   {"ids": ["a2a","mcp"], "fields": {...}}
      → 每协议可用性/缺什么、合并必填、能力、派活路线、重复提醒、逐协议自验入口、注册载荷

为什么不放在 `routers/docking.py`：那个文件 3400+ 行；新端点按"端点组"单独成文件，
在 `server.py` 挂载（`app.include_router`）。算法全在 `platform/protocol_plan.py`（纯函数，可单测）。
"""
from __future__ import annotations

from fastapi import APIRouter, Request

from .. import protocol_tree as tree
from .. import protocol_plan as plan

router = APIRouter(tags=["protocol-plan"])


@router.post("/api/docking/protocol-plan")
async def protocol_plan(request: Request):
    """算"选中这些协议之后能干什么"（**只读**：不写盘、不改档案）。

    body：`{"ids": ["a2a","mcp","http_api"], "fields": {"harness_id": "...", "api_base_url": "...",
            "acp_command": "...", "ai": {"model_name": "..."}}}`
    `fields` 只用来算"还缺什么"，不必全填。

    失败语义（不猜、不假装）：
      · `ids` 不是数组 / 为空 ⇒ 400 `BAD_PROTOCOLS`
      · 含未知协议 id ⇒ 400 `UNKNOWN_PROTOCOL`，并把**可用值列出来**
    """
    from fastapi.responses import JSONResponse

    try:
        body = await request.json()
    except Exception:                                          # noqa: BLE001 — 坏 JSON 当空，下面会拒
        body = {}
    body = body if isinstance(body, dict) else {}
    ids = body.get("ids")
    if not isinstance(ids, list) or not ids:
        return JSONResponse({
            "ok": False, "code": "BAD_PROTOCOLS",
            "error": "`ids` 要是非空数组（选中了哪些协议）",
            "available": list(tree.PROTOCOL_IDS),
            "next": "把界面上勾选的协议 id 传进来；一个都没选就先选一个",
        }, status_code=400)

    unknown = [str(x) for x in ids if str(x).strip().lower() not in tree.PROTOCOL_IDS]
    if unknown:
        return JSONResponse({
            "ok": False, "code": "UNKNOWN_PROTOCOL",
            "error": "不认识的协议：%s" % "、".join(unknown),
            "available": list(tree.PROTOCOL_IDS),
            "next": "用 available 里的 id；界面的协议卡就取自同一份（/api/docking/protocol-tree）",
        }, status_code=400)

    fields = body.get("fields") if isinstance(body.get("fields"), dict) else {}
    out = plan.build_plan([str(x) for x in ids], fields)
    out["at"] = __import__("time").strftime("%Y-%m-%dT%H:%M:%S")
    return out
