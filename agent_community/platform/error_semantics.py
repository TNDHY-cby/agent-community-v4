# -*- coding: utf-8 -*-
"""`platform/error_semantics`：**错误码的"该重试 / 该停手"语义**（借鉴竞品 A4）。

## 为什么（`合作社竞品雷达\03-学习借鉴\20261108_学习借鉴.md` §二 A4）

竞品（avernet）把错误码分成五类冲突码（`409303` 租约冲突 / `409304` fencing token 被拒 /
`409305` 幂等键复用 / `409307` 对象冻结 / `409308` 版本冲突），并给每个码配**对外固定文案**。
文档里最实用的一句是：

> **调用方是 LLM 时，只给错误码不够，必须给一句"下一步该做什么"**；
> 否则模型会把 409 当作可重试，形成重试风暴。

我方 `code` + `next`（人话）已经有了，**缺一个机器可读的 `retryable`**：
调用方（尤其是 agent）需要一个**布尔**来决定"再试一次"还是"停手找人"。

## 口径（三条）

1. **默认停手**（fail-closed）：不认识的码一律 `retryable=False` —— 宁可不重试，也别造重试风暴；
2. **幂等命中不算失败**：`ALREADY_*` / `duplicate` 这类是**成功语义**，标 `retryable=False` + `done=True`；
3. **加字段不许丢人话**：`error` / `next` 原样保留（`next` 就是给 LLM 看的那句）。

## 一处真相

码表只在这一份文件里。新增错误码忘了登记 ⇒ `tools/error_semantics_check.py` 会红
（它扫 `routers/*.py` 里出现过的 `"code": "..."` 字面量，要求都能在表里查到或明确标 unknown）。
"""
from __future__ import annotations

#: 可以重试（临时状态：对方刚好忙/暂时不可达/闸门稍后能过）
RETRYABLE = {
    "WAKEUP_FAILED",          # 没叫醒 —— 修好唤醒地址或稍后重试是合理的
    "BUSY", "TEMPORARY", "RETRY_LATER",
    "CELL_EMPTY",             # 分队暂时没人（人一到就能派）
}

#: 该停手（结构性拒绝：再试一次也不会变）
TERMINAL = {
    "NO_SUCH_WORKSHOP", "NO_SUCH_TASK", "NO_SUCH_MEMBER", "NO_SUCH_CELL",
    "NOT_REJECTED", "LEAD_NOT_IN_SQUAD", "SQUAD_PAUSED", "SQUAD_CLOSED",
    "REDISPATCH_BUDGET_EXHAUSTED", "DEPTH_EXCEEDED",
    "L1_NEEDS_PROOF", "EVIDENCE_L3_REJECTED", "BAD_EVIDENCE",
    "WHO_NOT_PROVEN", "NOT_LEAD", "NO_WORKSPACE_DIR", "NO_MEMBER",
    "BAD_REQUEST", "AILP_INVALID", "DISPATCH_REJECTED", "GATE_G0", "GATE_G1", "GATE_G2",
    "BAD_INBOX_DIR", "DISPUTE_OPEN", "PEER_REFUTED",
    "FREECHAT_RETIRED", "NO_MANIFEST",
}

#: 幂等命中（**成功语义**，不是失败）
ALREADY = {
    "ALREADY_DONE", "ALREADY_DISPATCHED", "ALREADY_ACKED", "DUPLICATE",
}


def retryable_of(code: str) -> bool:
    """这个码该不该让调用方再试一次。**不认识的码一律 False**（fail-closed）。"""
    c = str(code or "").strip().upper()
    if c in ALREADY:
        return False
    if c in RETRYABLE:
        return True
    return False


def is_already(code: str) -> bool:
    return str(code or "").strip().upper() in ALREADY


def annotate(payload: dict) -> dict:
    """给错误响应**补** `retryable`（和幂等命中的 `done`），不改原有字段。"""
    if not isinstance(payload, dict):
        return payload
    code = str(payload.get("code") or "")
    if not code:
        return payload
    out = dict(payload)
    out["retryable"] = retryable_of(code)
    if is_already(code) and "done" not in out:
        out["done"] = True
    # 给 LLM 的那句必须还在（A4 文档专门强调）
    if not out.get("next") and not out.get("error"):
        out["next"] = "这个错误没有附带说明（平台缺陷）：先看 code，再找后端补 next"
    return out


def table() -> dict:
    """给前端/agent 看的整张表（`/api/error-semantics`）。"""
    return {"ok": True, "retryable": sorted(RETRYABLE), "terminal": sorted(TERMINAL),
            "already": sorted(ALREADY),
            "policy": ("默认**停手**（不认识的码 retryable=false）；`ALREADY_*` 是**成功语义**"
                       "（retryable=false + done=true）；只有临时状态才 retryable=true"),
            "how_to_use": ("调用方拿到错误先看 `retryable`：true ⇒ 稍后重试；false ⇒ 停手，"
                           "按 `next` 那句人话改。**不要对 409 一律重试**（会形成重试风暴）。")}
