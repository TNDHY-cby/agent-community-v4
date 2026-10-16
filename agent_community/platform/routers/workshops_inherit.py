"""platform/routers/workshops_inherit：**隔间「可被继任员工继承」**（V53，老板 2026-10-17 拍板「可以」）。

本文件只做**接线**：找成员 / 校验 / 调内核 / 落盘 / 审计。
判定与搬运在 `platform/inherit.py`（纯逻辑，可单测；见那里的五条钉死口径）。

两个端点：
    `POST /api/workshop/{ws_id}/member/{member_id}/inherit` —— 显式继承（body: from_member_id + 上限）
    `GET  /api/workshop/{ws_id}/member/{member_id}/inherit` —— 只读查询（继承自谁 / 继承文内容）

⚠️ 三态如实（准则⑤）：**没搬成功就不许说成功** —— 返回里 `verified` 是逐项核对出来的，
`skipped`/`failed` 逐条给原因；`GET` **不写盘**。
"""

from __future__ import annotations

import asyncio

import time

from fastapi import APIRouter, Request

from ..audit import audit_log as _audit_log
from ..compartment import compartment_dir, inspect_compartment
from ..inherit import DEFAULT_MAX_FILE_MB, DEFAULT_MAX_TOTAL_MB, inherit
from ..state import workshops

router = APIRouter()

_LEGACY_READ_LIMIT = 32 * 1024          # 继承文只读回最多 32 KiB（超了如实标注截断）


def _json_response(*a, **kw):
    from ..server import Utf8JSONResponse
    return Utf8JSONResponse(*a, **kw)


def _find_member(ws, member_id: str):
    return next((m for m in (ws.members or []) if m.member_id == member_id), None)


def _compartment_of(ws, m):
    return compartment_dir(ws.workspace_dir, getattr(m, "role", ""), getattr(m, "member_id", ""))


@router.post("/api/workshop/{ws_id}/member/{member_id}/inherit")
async def inherit_member(ws_id: str, member_id: str, request: Request):
    """**显式继承**：把 `from_member_id` 那个成员的隔间产物复制给 `member_id`，并写继承文。

    body（都可选）：`{"from_member_id": "...", "max_file_mb": 5, "max_total_mb": 50}`
    """
    from ..server import save_state
    ws = workshops.get(ws_id)
    if not ws:
        return _json_response({"error": "工作间不存在"}, status_code=404)
    to_m = _find_member(ws, member_id)
    if not to_m:
        return _json_response({"error": "成员不存在", "member_id": member_id}, status_code=404)
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        body = {}
    if not isinstance(body, dict):
        body = {}

    from_id = str(body.get("from_member_id") or "").strip()
    if not from_id:
        # 判据 1：不传来源 ⇒ **不搬、不写继承文**，如实说"没有指定来源"
        return _json_response({
            "success": False, "verified": False,
            "reason": "没有指定继承来源（from_member_id）——平台不会替你猜从谁继承",
            "note": "显式继承：请指明 from_member_id（同工作间内某个成员）",
        }, status_code=400)
    if from_id == member_id:
        return _json_response({"success": False, "verified": False,
                               "reason": "来源与目标是同一个成员", "member_id": member_id},
                              status_code=400)
    from_m = _find_member(ws, from_id)
    if not from_m:
        # 判据 7：指名不存在的来源 ⇒ 404 + 原因（不静默成功）
        return _json_response({"error": "来源成员不存在", "from_member_id": from_id}, status_code=404)

    def _num(key, default):
        try:
            v = float(body.get(key))
            return v if v > 0 else default
        except (TypeError, ValueError):
            return default

    src = _compartment_of(ws, from_m)
    dst = _compartment_of(ws, to_m)
    if not src.is_dir():
        return _json_response({
            "success": False, "verified": False,
            "reason": "来源隔间不存在（这位成员可能还没真正开工，没有可继承的产物）",
            "from_member_id": from_id, "src_dir": str(src),
        }, status_code=409)

    # 复制可能是几十 MiB ⇒ 走线程，别卡住事件循环
    out = await asyncio.to_thread(
        inherit, str(src), str(dst), str(ws.workspace_dir),
        {"member_id": from_id, "role": getattr(from_m, "role", ""),
         "display_name": getattr(from_m, "display_name", "")},
        ws_id, _num("max_file_mb", DEFAULT_MAX_FILE_MB), _num("max_total_mb", DEFAULT_MAX_TOTAL_MB))

    if not out.get("success"):
        return _json_response({"success": False, "verified": False,
                               "reason": out.get("reason") or "继承失败",
                               "from_member_id": from_id, "to_member_id": member_id},
                              status_code=409)

    moved = out.get("moved") or []
    skipped = out.get("skipped") or []
    failed = out.get("failed") or []
    note = "搬 %d 项 / 跳过 %d 项 / 失败 %d 项" % (len(moved), len(skipped), len(failed))
    to_m.inherited_from = from_id
    to_m.inherited_at = time.time()
    to_m.inherit_note = note
    save_state()
    try:
        _audit_log.record("workshop.member_inherit", actor="user", target=ws_id,
                          detail="%s ← %s：%s" % (member_id, from_id, note),
                          extra={"from_member_id": from_id, "to_member_id": member_id,
                                 "moved": len(moved), "skipped": len(skipped),
                                 "failed": len(failed), "verified": bool(out.get("verified")),
                                 "legacy_path": out.get("legacy_path", "")})
    except Exception as e:  # noqa: BLE001
        print("[inherit] 审计写入失败（不影响结果）: %s: %s" % (type(e).__name__, e), flush=True)

    return {
        "success": True, "verified": bool(out.get("verified")),
        "workshop_id": ws_id, "from_member_id": from_id, "to_member_id": member_id,
        "moved": moved, "skipped": skipped, "failed": failed,
        "total_bytes": out.get("total_bytes", 0),
        "legacy_path": out.get("legacy_path", ""),
        "note": note,
        "reason": "" if out.get("verified") else "有文件没搬成功（见 failed）——别当成搬完了",
    }


@router.get("/api/workshop/{ws_id}/member/{member_id}/inherit")
async def inherit_status(ws_id: str, member_id: str):
    """只读：这个成员继承过谁、隔间里有没有继承文、继承文全文（**不写盘**）。"""
    ws = workshops.get(ws_id)
    if not ws:
        return _json_response({"error": "工作间不存在"}, status_code=404)
    m = _find_member(ws, member_id)
    if not m:
        return _json_response({"error": "成员不存在", "member_id": member_id}, status_code=404)
    d = _compartment_of(ws, m)
    legacy = d / "legacy.md"
    text, truncated = "", False
    try:
        if legacy.is_file():
            raw = legacy.read_text(encoding="utf-8", errors="replace")
            truncated = len(raw) > _LEGACY_READ_LIMIT
            text = raw[:_LEGACY_READ_LIMIT]
    except OSError as e:  # noqa: BLE001
        text = "（读继承文失败：%s: %s）" % (type(e).__name__, e)
    return {
        "success": True, "workshop_id": ws_id, "member_id": member_id,
        "inherited_from": getattr(m, "inherited_from", "") or "",
        "inherited_at": float(getattr(m, "inherited_at", 0) or 0),
        "inherit_note": getattr(m, "inherit_note", "") or "",
        "legacy_exists": legacy.is_file(),
        "legacy_path": str(legacy),
        "legacy_text": text,
        "legacy_truncated": truncated,
        "compartment": inspect_compartment(ws.workspace_dir, getattr(m, "role", ""), member_id),
        "note": ("没有继承过（inherited_from 为空）" if not getattr(m, "inherited_from", "")
                 else "继承来源见 inherited_from；继承文在隔间根 legacy.md"),
    }
