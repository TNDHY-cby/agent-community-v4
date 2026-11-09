# -*- coding: utf-8 -*-
"""`platform/dispatch_fingerprint`：**派活内容指纹**（借鉴竞品 A2 的幂等约束）。

## 为什么（`合作社竞品雷达\03-学习借鉴\20261108_学习借鉴.md` §二 A2）

竞品（paperclip）把"派活防重"写进**数据库唯一约束**：
`issue_plan_decompositions` 表对 `unique(sourceIssueId, acceptedPlanRevisionId)`，
配 `requestFingerprint` 识别"同版不同措辞"。

我方是 json 文件账本，没有唯一约束 ⇒ 用**应用层指纹**近似：
同工位 + 同内容 + **还在进行中** ⇒ 判为重复派活，返回已有任务号而不是新建第二条。

## 三条口径（每条都有教训）

1. **指纹只算内容**：`member_seq + title + goal + accept`（归一化后），
   **绝不含时间戳** —— AILP 的幂等 bug 就是 `received_at` 进了指纹，跨秒就不幂等；
2. **只跟"未结任务"比**：`issued/working/rework/delivered` 才算重复；
   已 `accepted`/`rejected` 的老任务**不算** —— 同一个活隔一阵再做一遍是合法的；
3. **可以显式绕过**：body 带 `force: true` ⇒ 明知重复也要再派一条（给"就是要两份"的场景留门）。
"""
from __future__ import annotations

import hashlib
import re

#: 算"还在进行中"的状态（跟 `presence.OPEN_STATES` 同口径 + delivered，因为还没验收）
OPEN = ("issued", "working", "rework", "delivered")

#: 全角 → 半角（标点/空格），避免"看起来一样"被判成不同
_FULL2HALF = str.maketrans({
    "，": ",", "。": ".", "：": ":", "；": ";", "！": "!", "？": "?",
    "（": "(", "）": ")", "【": "[", "】": "]", "“": '"', "”": '"',
    "‘": "'", "’": "'", "、": ",", "　": " ",
})


def normalize(text) -> str:
    """归一化：全角转半角、压掉所有空白、strip。

    **不转小写**：中文没有大小写，英文标识符（路径/命令）大小写有意义 —— 转小写会造成误判。
    """
    s = str(text or "").translate(_FULL2HALF)
    return re.sub(r"\s+", "", s).strip()


def fingerprint_of(member_seq, title, goal="", accept="") -> str:
    """派活内容指纹（同工位 + 同内容 ⇒ 同指纹）。**不含任何时间字段**。"""
    src = "|".join([str(int(member_seq or 0)), normalize(title),
                    normalize(goal), normalize(accept)])
    return hashlib.sha256(src.encode("utf-8")).hexdigest()


def find_open_duplicate(tasks: list, *, member_seq, fp: str) -> dict | None:
    """在**未结任务**里找同指纹同工位的那条（找到 ⇒ 它就是"已派过"）。"""
    want_seq = int(member_seq or 0)
    for t in (tasks or []):
        if str((t or {}).get("state") or "") not in OPEN:
            continue
        if int((t or {}).get("member_seq") or 0) != want_seq:
            continue
        if str((t or {}).get("fingerprint") or "") == fp:
            return t
    return None
