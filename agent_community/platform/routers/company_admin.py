"""platform/routers/company_admin：公司级远程接入管理（注册页「远程接入」弹窗后端支撑）。

为注册页 register.html 的「远程接入」弹窗提供：登记合作公司 / 签发公司令牌 / 吊销 / 校验。
令牌语义与 deploy/issue_token.py 完全一致：
- 签发 = HMAC(secret, "company:" + company_id)（复用 identity.issue_company_token，单一签发源）；
- 吊销 = 从登记册 companies.json 删除条目（边缘 known_company_ids 即刻不认 → 入站 403，无需改平台代码）；
- 过期 = expires_at 已过（days>0 时签发时写入；days=0/缺省 = 永不过期）。

安全约束：本组端点面向本机/可信局域网管理员（注册页无登录态，与既有注册端点同级），
不做额外鉴权；令牌明文仅在本页生成与展示一次，由管理员自行安全交付合作方。
"""
from __future__ import annotations

from datetime import datetime, timedelta
from fastapi import APIRouter, Request

from ..identity import (
    issue_company_token as _issue_company_token,
    verify_company_token as _verify_company_token,
    load_company_registry,
    save_company_registry,
)

router = APIRouter()


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _is_expired(rec: dict) -> bool:
    exp = (rec or {}).get("expires_at") or ""
    if not exp:
        return False
    try:
        return datetime.fromisoformat(exp) < datetime.now().astimezone()
    except Exception:  # noqa: BLE001  格式异常按已过期处理（fail-closed）
        return True


def _public_rec(company_id: str, rec: dict) -> dict:
    return {
        "company_id": company_id,
        "company_name": rec.get("company_name") or company_id,
        "note": rec.get("note", ""),
        "role": rec.get("role", "peer"),
        "status": rec.get("status", "active"),
        "issued_at": rec.get("issued_at", ""),
        "expires_at": rec.get("expires_at", ""),
        "expired": _is_expired(rec),
    }


@router.get("/api/companies")
async def api_companies_list():
    """列出公司登记册全部条目（含过期状态），供弹窗「已授权公司」表格渲染。"""
    from ..server import Utf8JSONResponse
    records = load_company_registry()
    items = [_public_rec(cid, rec) for cid, rec in records.items()]
    return Utf8JSONResponse({"ok": True, "companies": items, "count": len(items)})


@router.post("/api/companies")
async def api_companies_register(request: Request):
    """登记合作公司（幂等：company_id 已存在时只更新名称/备注，不重签令牌）。

    body: {"company_name": "...", "company_id": "可选，留空自动生成", "note": "可选"}
    """
    from ..server import Utf8JSONResponse
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        return Utf8JSONResponse({"error": "请求体必须是 JSON"}, status_code=400)
    name = str(body.get("company_name") or "").strip()
    if not name:
        return Utf8JSONResponse({"error": "company_name 必填"}, status_code=400)
    cid = str(body.get("company_id") or "").strip().lower()
    note = str(body.get("note") or "").strip()
    if not cid:
        # 自动生成：拼音/英文短标识不现实，直接用时间戳短码，避免中文/特殊字符
        cid = "co-" + datetime.now().strftime("%H%M%S")
    if not cid.replace("-", "").replace("_", "").isalnum():
        return Utf8JSONResponse({"error": "company_id 只能含字母/数字/连字符/下划线"}, status_code=400)

    records = load_company_registry()
    existed = cid in records
    rec = records.get(cid) or {}
    rec.update({
        "company_id": cid,
        "company_name": name,
        "note": note or rec.get("note", ""),
        "role": rec.get("role", "peer"),
        "status": rec.get("status", "active"),
        "issued_at": rec.get("issued_at", _now_iso()),
        "expires_at": rec.get("expires_at", ""),
    })
    records[cid] = rec
    save_company_registry(records)
    return Utf8JSONResponse({
        "ok": True,
        "created": not existed,
        "company": _public_rec(cid, rec),
    })


@router.post("/api/company/token/issue")
async def api_company_token_issue(request: Request):
    """为公司签发（或重签）公司令牌。body: {"company_id": "...", "days": 0|N}。

    days>0：N 天后过期；days=0 或缺省：永不过期。重签会覆盖旧的 expires_at（历史 token 仍有效，
    因为 HMAC 不变；吊销唯一手段是删除登记条目）。
    """
    from ..server import Utf8JSONResponse
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        return Utf8JSONResponse({"error": "请求体必须是 JSON"}, status_code=400)
    cid = str(body.get("company_id") or "").strip()
    if not cid:
        return Utf8JSONResponse({"error": "company_id 必填"}, status_code=400)
    try:
        days = int(body.get("days", 0))
    except Exception:  # noqa: BLE001
        days = 0
    if days < 0:
        return Utf8JSONResponse({"error": "days 不能为负"}, status_code=400)

    records = load_company_registry()
    if cid not in records:
        return Utf8JSONResponse({"error": f"公司 {cid} 未登记，请先在「登记合作公司」登记"}, status_code=404)
    token = _issue_company_token(cid)
    rec = records[cid]
    rec["issued_at"] = _now_iso()
    rec["expires_at"] = (datetime.now().astimezone() + timedelta(days=days)).isoformat(timespec="seconds") if days > 0 else ""
    rec["status"] = "active"
    save_company_registry(records)
    return Utf8JSONResponse({
        "ok": True,
        "company_id": cid,
        "token": token,
        "issued_at": rec["issued_at"],
        "expires_at": rec["expires_at"],
        "note": "令牌即合作方通行证，请通过安全通道（Tailscale / 加密聊天）交付，仅发给信任的公司。",
    })


@router.post("/api/company/token/revoke")
async def api_company_token_revoke(request: Request):
    """吊销公司令牌：从登记册删除条目，边缘鉴权即刻拒绝该 company_id 的入站请求。"""
    from ..server import Utf8JSONResponse
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        return Utf8JSONResponse({"error": "请求体必须是 JSON"}, status_code=400)
    cid = str(body.get("company_id") or "").strip()
    if not cid:
        return Utf8JSONResponse({"error": "company_id 必填"}, status_code=400)
    records = load_company_registry()
    if cid not in records:
        return Utf8JSONResponse({"error": f"公司 {cid} 不在登记册中，无需吊销"}, status_code=404)
    del records[cid]
    save_company_registry(records)
    return Utf8JSONResponse({"ok": True, "revoked": cid, "note": "已吊销：该公司的令牌即刻失效，9104 入站将拒绝。"})


@router.post("/api/company/token/verify")
async def api_company_token_verify(request: Request):
    """校验令牌是否对应当前登记公司且未过期（供弹窗自检 / 交付前核对）。

    body: {"company_id": "...", "token": "..."}  → {"valid": true|false, "reason": "..."}
    """
    from ..server import Utf8JSONResponse
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        return Utf8JSONResponse({"error": "请求体必须是 JSON"}, status_code=400)
    cid = str(body.get("company_id") or "").strip()
    token = str(body.get("token") or "").strip()
    if not cid or not token:
        return Utf8JSONResponse({"error": "company_id 与 token 必填"}, status_code=400)
    records = load_company_registry()
    rec = records.get(cid)
    if not rec:
        return Utf8JSONResponse({"valid": False, "reason": f"公司 {cid} 未登记（可能已被吊销）"})
    if _is_expired(rec):
        return Utf8JSONResponse({"valid": False, "reason": f"令牌已过期（{rec.get('expires_at')}）"})
    if not _verify_company_token(cid, token):
        return Utf8JSONResponse({"valid": False, "reason": "token 与 company_id 不匹配"})
    return Utf8JSONResponse({"valid": True, "reason": "有效", "company_id": cid})
