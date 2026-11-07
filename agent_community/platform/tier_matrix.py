# -*- coding: utf-8 -*-
"""`platform/tier_matrix`：**档位 × 四件事**的对照表（设计 `V134` ⑨，2026-11-07）。

## 为什么要有它

`tier`（由名单证据推：`a_protocol` / `b_bridge` / `c_visual` / `legacy_unknown`）在
**派活 / 通知 / 产物 / 验收**四件事上分别怎么处理，原来**各写各的**（`dispatch.activate`、
`artifact_inbox`、`p4_v5.review` 各一份）⇒ 加一档或改一档语义时**必漏**
（本项目已经踩过：B 档"只通没取件"被判 active）。

⇒ 这里放**唯一一张表**：每档 × 每件事给一个**行为码**（代码里按码分支），
外加一句人话（界面/文档直接用）。

## 加新档的规矩

新增一个 tier **必须填满整行**（四个轴都要有值）；`test_tier_matrix.py` 会在缺格时变红。
"""
from __future__ import annotations

#: 四个轴（顺序固定：界面与文档都照这个顺序渲染）
AXES = ("reach", "notice", "artifact", "review")

AXIS_LABELS = {
    "reach": "派活怎么触达",
    "notice": "通知怎么落",
    "artifact": "产物从哪来",
    "review": "验收怎么放",
}

#: 行为码 → 人话（代码里只认码，别在分支里写死句子）
CODES = {
    "protocol_push": "平台按协议直接推（A2A / HTTP_api / ACP / MCP / 桥）",
    "bridge_file": "平台往桥目录投文件，等它取件",
    "manual_relay": "平台写任务文件，由人转达（半自动）",
    "notice_file": "写 `_notice.md`（**写了不等于看了**）",
    "visual_paste": "视觉投递进它窗口（投递≠照做）",
    "no_auto_notice": "平台没有能自动触达它的通知通道",
    "dir_scan": "平台扫它的收件目录（`产物/` 或档案指定目录，真 sha256）",
    "self_report": "只能靠它自己交回执",
    "gate_l0l2": "按证据等级放行（L0 扫盘 / L1 凭据 / L2+ 需人）",
    "gate_l1_needs_proof": "L1 必须带可核对凭据，否则拒",
    "gate_reject_auto": "不能自动放行，必须人看",
}

#: **唯一真相**：档位 × 四轴 = 行为码
TIER_MATRIX = {
    "a_protocol": {
        "label": "A 档 · 协议直连（能派活、能收回执）",
        "reach": "protocol_push",
        "notice": "notice_file",
        "artifact": "dir_scan",
        "review": "gate_l0l2",
        "note": "最适配：平台能派、能收；产物靠扫盘 + 回执",
    },
    "b_bridge": {
        "label": "B 档 · 桥/文件轮询（能取件、能回执）",
        "reach": "bridge_file",
        "notice": "notice_file",
        "artifact": "dir_scan",
        "review": "gate_l1_needs_proof",
        "note": "注意：**只通没取件**不算 active（这条是踩过的坑，写进矩阵别再分叉）",
    },
    "c_visual": {
        "label": "C 档 · 视觉/人工（只能看见窗口）",
        "reach": "manual_relay",
        "notice": "visual_paste",
        "artifact": "dir_scan",
        "review": "gate_reject_auto",
        "note": "平台能投递、能扫产物，但**放行必须人看**（视觉投递≠照做）",
    },
    "legacy_unknown": {
        "label": "旧数据 · 档位未知",
        "reach": "manual_relay",
        "notice": "no_auto_notice",
        "artifact": "self_report",
        "review": "gate_reject_auto",
        "note": "老工作间没有证据链 ⇒ 一律按人工处理，不假装能自动",
    },
}


def matrix() -> dict:
    """给界面/文档用的完整表（含每格人话）。"""
    rows = []
    for tier, row in TIER_MATRIX.items():
        rows.append({"tier": tier, "label": row.get("label") or tier,
                     "axes": {a: {"code": row.get(a), "meaning": CODES.get(row.get(a) or "", "（未定义）")}
                              for a in AXES},
                     "note": row.get("note") or ""})
    return {"ok": True, "axes": list(AXES), "axis_labels": dict(AXIS_LABELS),
            "tiers": rows, "codes": dict(CODES),
            "note": ("一处真相：`dispatch` / `artifact_inbox` / `review` 的行为码都从这里取；"
                     "新增档位必须填满四个轴（缺格 ⇒ 守卫变红）")}


def code_of(tier: str, axis: str) -> str:
    """取某档某轴的行为码（拿不到就给**最保守**的码，绝不假装能自动）。"""
    row = TIER_MATRIX.get(str(tier or "")) or {}
    axis = str(axis or "")
    if axis not in AXES:
        return ""
    if row.get(axis):
        return str(row[axis])
    return "gate_reject_auto" if axis == "review" else "no_auto_notice"


def meaning_of(code: str) -> str:
    return CODES.get(str(code or ""), "（未定义的行为码）")


def gaps() -> list:
    """缺格检查（守卫用）：返回缺了什么，空列表＝整表完整。"""
    out = []
    for tier, row in TIER_MATRIX.items():
        for a in AXES:
            code = row.get(a)
            if not code:
                out.append({"tier": tier, "axis": a, "why": "缺行为码"})
            elif code not in CODES:
                out.append({"tier": tier, "axis": a, "why": "行为码未定义：%s" % code})
    return out
