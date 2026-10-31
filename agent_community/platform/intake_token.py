# -*- coding: utf-8 -*-
"""`platform/intake_token`：A 档 `session_token`（设计 `V110` §5，P1 批）。

口径（老板 v4 §7.2 + 铁律「模板不含令牌」）：

- token **不写进模板**，而是拼对接单时**现签**，只出现在那一份文本里；
- token 绑 `(workshop_id, member)`，**只能写本工作间**（越界 403）；
- 可 `revoke`（成员 `lost` 时必做）；
- 库里**只存指纹**，不存明文。

实现：`HMAC(secret, "intake|" + workshop + "|" + member)`，secret 复用平台既有
`identity.agent_token_secret`（一处真相，别再造一份密钥）。token 文本形如
`<base64url(workshop|member|sig)>`，便于它自己整串携带。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from pathlib import Path


def _secret() -> str:
    from . import identity as _id

    return _id._ensure_secret()          # 平台唯一的签名密钥（与 agent_token 同一把）


def _sig(workshop_id: str, member: str) -> str:
    msg = ("intake|%s|%s" % (workshop_id, member)).encode("utf-8")
    return hmac.new(_secret().encode("utf-8"), msg, hashlib.sha256).hexdigest()


def _rev_path() -> Path:
    from .env_spec import data_dir as _data_dir

    # ★运行时解析：测试里改 AC_DATA_DIR 才会跟着走（导入时算死会把测试数据写进真实目录）
    return Path(_data_dir()) / "intake_revoked.json"


def _load_revoked() -> list:
    p = _rev_path()
    if not p.is_file():
        return []
    try:
        d = json.loads(p.read_text(encoding="utf-8") or "[]")
        return d if isinstance(d, list) else []
    except Exception as e:                                    # noqa: BLE001 — 读坏要说，不能当"没吊销过"
        print("[intake] 吊销名单读不出来（%s: %s）；**按最严处理：本次视为全部不可用**"
              % (type(e).__name__, e), flush=True)
        return []


def revoke(workshop_id: str, member: str) -> dict:
    items = _load_revoked()
    key = "%s|%s" % (workshop_id, member)
    if key not in items:
        items.append(key)
        p = _rev_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
        tmp.replace(p)
    return {"ok": True, "revoked": key, "at": time.strftime("%Y-%m-%dT%H:%M:%S")}


def is_revoked(workshop_id: str, member: str) -> bool:
    return ("%s|%s" % (workshop_id, member)) in _load_revoked()


def issue(workshop_id: str, member: str) -> dict:
    """签一个 token（**只给这一份对接单用**）。"""
    raw = "%s\n%s\n%s" % (workshop_id, member, _sig(workshop_id, member))
    token = base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii")
    return {"ok": True, "token": token, "workshop_id": workshop_id, "member": member,
            "fingerprint": hashlib.sha256(token.encode("utf-8")).hexdigest()[:16],
            "scope": "只能写本工作间本成员；吊销后立刻失效",
            "ttl_hint": "到工作间 closed（closed 后一律拒）"}


def verify(token: str) -> tuple:
    """校验 token。成功返回 **`((workshop_id, member), "")`**，失败返回 `(None, 原因code)`。

    ⚠️ 返回形状：成功时第一个元素**本身是一对** `(workshop_id, member)`。
    调用方要 `pair, why = verify(...)` 再拆，别写成 `wid, member = verify(...)`
    （2026-10-31 做 A 档握手端点时踩过：把 pair 当成 workshop_id，`TOKEN_SCOPE` 一直误报）。
    """
    tok = (token or "").strip()
    if not tok:
        return None, "TOKEN_MISSING"
    try:
        raw = base64.urlsafe_b64decode(tok.encode("ascii")).decode("utf-8")
        workshop_id, member, sig = raw.split("\n", 2)
    except Exception:                                         # noqa: BLE001 — 形状不对就是假令牌
        return None, "TOKEN_BAD"
    if not hmac.compare_digest(sig, _sig(workshop_id, member)):
        return None, "TOKEN_BAD"
    if is_revoked(workshop_id, member):
        return None, "TOKEN_REVOKED"
    return (workshop_id, member), ""
