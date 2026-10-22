# -*- coding: utf-8 -*-
"""注册卡片**静态一致性**校验（V82 §3）：与"真能用"分开，别混。

产出 `{"conformance": "passed|failed", "conformance_errors": [{field, why}], "checked_at": ...}`。
每条错误都带**字段名与原因**，不合并成一句"卡片不合法"。
"""
from __future__ import annotations

from datetime import datetime

WAKEUP_METHODS = ("file_poll", "pending_poll", "http_api", "clipboard", "manual", "acp", "pipe", "ws")


def _err(out, field, why):
    out.append({"field": field, "why": why})


def check_card(info) -> dict:
    """校验一张已注册卡片的静态字段。`info` 可以是对象或 dict。"""
    def g(k, default=""):
        if isinstance(info, dict):
            return info.get(k, default)
        return getattr(info, k, default)

    errs = []
    hid = str(g("harness_id") or "")
    name = str(g("harness_name") or "")
    method = str(g("wakeup_method") or "")
    wake_dir = str(g("wakeup_dir") or "")
    caps = g("capabilities", None) or (g("ai", {}) or {}).get("capabilities") if isinstance(g("ai", {}), dict) else None

    if not name:
        _err(errs, "harness_name", "必填（用户要靠它认人）")
    elif len(name) > 64:
        _err(errs, "harness_name", "太长（%d > 64）" % len(name))

    if hid and not all(c.isalnum() or c in "-_." for c in hid):
        _err(errs, "harness_id", "只能用字母/数字/- _ .（现在是 %r）" % hid)

    if method and method not in WAKEUP_METHODS:
        _err(errs, "wakeup_method", "不认识的叫醒方式 %r（支持：%s）" % (method, "、".join(WAKEUP_METHODS)))
    if not method:
        _err(errs, "wakeup_method", "必填：平台不知道该怎么叫醒它，派活就永远唤不醒")

    if wake_dir and ("://" in wake_dir) and not wake_dir.startswith(("http://", "https://")):
        _err(errs, "wakeup_dir", "看起来像 URL 但 scheme 不是 http(s)：%r" % wake_dir[:60])

    if caps is not None:
        if not isinstance(caps, list) or any(not isinstance(x, str) for x in caps):
            _err(errs, "capabilities", "必须是字符串数组")
        elif any(len(x) > 32 for x in caps):
            _err(errs, "capabilities", "有一项太长（每个 ≤32 字）")

    return {"conformance": "failed" if errs else "passed",
            "conformance_errors": errs,
            "checked_at": datetime.now().isoformat(timespec="seconds")}
