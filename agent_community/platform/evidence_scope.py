# -*- coding: utf-8 -*-
"""platform/evidence_scope："这次验证到底证明了什么、没证明什么" 的唯一口径（V73 §9 第 4 项）。

## 为什么单独一层

同一句话原来散在两个地方：
- 写入侧 `platform/verify_sandbox.py` 落盘 `evidence.verified_scope`（它才是真源头）；
- 读取侧 `platform/harness_capability.py` 又抄了一份常量给"没有证据"的记录用。

两处各写一套 = 迟早不一致（规则 20）。⇒ 文案收在这里，谁要就 import。

## 两类范围（与写入侧完全一致，别自己改字）

| 通道 | 范围原话 |
|---|---|
| `inbox` / `file_mailbox` | 完整链路：平台 → 桥 → 你的 agent → 回报 |
| 其它（如 `pending_queue`） | 仅桥配置（桥能连平台/领件/回报；且这只桥是平台在验证间里启动的）——harness 侧是否运行该桥、agent 能否回话，本次未验证 |
| 没有任何证据 | 未验证：既没有 bridge_test，也没有 bridge_template_verified —— harness 是否在跑桥、agent 能否回话，均未验证 |

⚠️ 落盘时通道名只留一套（面向接入方那套）：`file_mailbox → inbox`、`bridge_test_queue → pending_queue`。
"""
from __future__ import annotations

#: 完整链路（file_poll/inbox 这类：agent 自己就从那个目录取件）
SCOPE_FULL_CHAIN = "完整链路：平台 → 桥 → 你的 agent → 回报"

#: 仅桥配置（默认；不能说成"接上了"）
SCOPE_BRIDGE_ONLY = ("仅桥配置（桥能连平台/领件/回报；且这只桥是平台在验证间里启动的）——"
                     "harness 侧是否运行该桥、agent 能否回话，本次未验证")

#: 一条证据都没有时的说法
SCOPE_NONE = ("没有任何验证证据（本记录里既没有 bridge_test，也没有 bridge_template_verified）"
              "——harness 是否在跑桥、agent 能否回话，均未验证")

#: 通道名的唯一映射（写入侧与读取侧共用；历史记录里的旧名也认）
CHANNEL_ALIASES = {"file_mailbox": "inbox", "bridge_test_queue": "pending_queue"}

#: 视为"完整链路"的通道
FULL_CHAIN_CHANNELS = frozenset({"inbox", "file_mailbox"})


def normalize_channel(channel: str) -> str:
    """通道名归一（面向接入方那套）。"""
    ch = str(channel or "")
    return CHANNEL_ALIASES.get(ch, ch)


def scope_for_channel(channel: str) -> str:
    """按通道给"本次验证范围"的原话（唯一实现）。"""
    ch = normalize_channel(channel)
    return SCOPE_FULL_CHAIN if ch in FULL_CHAIN_CHANNELS else SCOPE_BRIDGE_ONLY


def interpret_scope(scope: str) -> dict:
    """把一段已落盘的 `verified_scope` 读成结构化判断（给界面/能力视图用）。

    认不出来就如实说认不出来（`kind="unknown"`），不猜。
    """
    s = str(scope or "")
    if not s:
        return {"kind": "none", "proves": [], "does_not_prove": [],
                "text": SCOPE_NONE}
    if "仅桥配置" in s:
        return {"kind": "bridge_only", "proves": ["桥能连平台", "桥能领件", "桥能回报"],
                "does_not_prove": ["harness 侧是否在跑那只桥", "agent 能否回话"], "text": s}
    if "完整链路" in s:
        return {"kind": "full_chain", "proves": ["平台 → 桥 → agent → 回报 整条链"],
                "does_not_prove": [], "text": s}
    return {"kind": "unknown", "proves": [], "does_not_prove": [],
            "text": s, "note": "这段范围没认出来（不上纲到'已验证'）"}
