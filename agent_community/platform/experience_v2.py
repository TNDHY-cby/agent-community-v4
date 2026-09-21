# -*- coding: utf-8 -*-
"""经验包 V2：员工完成任务回报 → 经验沉淀 + capability_ledger 信誉加分 / 加权选人。

设计原则：
- 纯函数 + 显式依赖注入（harness_manager / task_memory / capability_ledger 由调用方传入），
  便于无副作用单测与函数级断言（不 import server.py）。
- 任何异常不外抛：经验沉淀绝不阻塞主回报流程。
- 不新建平行结构：经验条目复用 TaskMemory（task_memory.json，去重 + max_records 上限），
  信誉复用 CapabilityLedgerManager.record（reputation 字段既存，禁止另起炉灶）。
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Optional

from .protocol import HistoricalTask


# ─────────────────────────────────────────────────────────────
# 基础纯规则工具
# ─────────────────────────────────────────────────────────────

def extract_task_keywords(task_text: str) -> list[str]:
    """从任务文本提取关键词（纯规则，零 LLM）：
    - 按空白与标点切出长度>=2 的中文/英文词段；
    - 超长中文连续段（>=4 字）补充 2-gram，提高中文条目命中率；
    返回去重小写列表。
    """
    if not task_text:
        return []
    segs = re.split(r"[\s\W_]+", str(task_text))
    toks = [s for s in segs if len(s) >= 2]
    more = []
    for s in toks:
        if re.fullmatch(r"[\u4e00-\u9fff]{4,}", s):
            more.extend(s[i:i + 2] for i in range(len(s) - 1))
    return list(dict.fromkeys((t.lower() for t in toks + more)))


def harness_capabilities(hid: str, harness_manager) -> list[str]:
    """取 harness 注册能力名列表；无 session / 无注册能力时返回 []。"""
    if not hid:
        return []
    try:
        sess = getattr(harness_manager, "sessions", {}).get(hid)
    except Exception:
        sess = None
    caps: list = []
    if sess is not None:
        info = getattr(sess, "info", None)
        ai = getattr(info, "ai", None) if info is not None else None
        caps = list(getattr(ai, "capabilities", []) or []) if ai is not None else []
    return [c for c in caps if isinstance(c, str) and c.strip()]


def _capability_hit(caps: list[str], toks: list[str]) -> list[str]:
    """返回 capabilities 中与任务关键词有子串命中的子集（忽略大小写）。"""
    if not caps or not toks:
        return []
    return [c for c in caps if any(tok in c.lower() for tok in toks)]


# ─────────────────────────────────────────────────────────────
# V2-1：完成回报 → 经验沉淀 + 信誉加分
# ─────────────────────────────────────────────────────────────

def member_completion_caps(ws, member, harness_manager) -> list[str]:
    """纯规则判定本次完成回报所涉能力域：
    1. 取该成员绑定 harness 的已注册 capabilities；
    2. 任务关键词命中其中的子集（命中则只用命中子集，避免无关能力虚加分）；
    3. 无注册能力 / 无命中时回退全量或 ["general"]。
    """
    if member is None:
        return ["general"]
    hid = ((member.harness_ids or [None])[0]) if getattr(member, "harness_ids", None) else None
    caps = harness_capabilities(hid, harness_manager) if hid else []
    if not caps:
        return ["general"]
    task_text = getattr(ws, "hall_content", None) or ""
    toks = extract_task_keywords(task_text)
    if toks:
        hit = _capability_hit(caps, toks)
        if hit:
            return hit
    return caps


def apply_completion_rewards(
    ws,
    member,
    harness_id: str,
    text: str,
    summary: str,
    *,
    task_memory,
    capability_ledger,
    harness_manager,
) -> bool:
    """V2-1：harness 完成任务回报 → 写平台统一记忆 + capability_ledger 信誉加分。

    - 经验条目：task_memory.add(HistoricalTask)，task_id 固定为 ws:{ws_id}:{member_id}，
      同一成员同一任务的重复完成回报天然覆盖去重，不产生重复条目；
    - 信誉加分：对本次能力域逐 cap 调 capability_ledger.record(success=True, score=1.0)，
      复用既有 reputation 演化逻辑，不新建平行结构；
    - 任何异常仅打印日志并返回 False，绝不阻塞主回报流程。
    """
    try:
        if member is None or ws is None:
            return False
        hid = harness_id or (((member.harness_ids or [None])[0]) if getattr(member, "harness_ids", None) else None) or ""
        ws_id = getattr(ws, "workshop_id", "") or ""
        task_id = f"ws:{ws_id}:{member.member_id}"
        title = (getattr(ws, "hall_content", None) or "工作间任务").strip()
        if len(title) > 120:
            title = title[:120]
        produced = (summary or "").strip() or (text or "").strip()
        caps = member_completion_caps(ws, member, harness_manager)
        # 幂等去重：同一任务重复完成回报 → 覆盖更新条目，但不重复加信誉分
        is_repeat = task_memory.get(task_id) is not None
        task_memory.add(HistoricalTask(
            task_id=task_id,
            title=title,
            description=produced[:500],
            capabilities_used=caps,
            agent_executions={hid: produced[:300]} if hid else {},
            quality_score=1.0,
            duration_ms=0,
            completed_at=datetime.now().isoformat(),
        ))
        if hid and not is_repeat:
            for cap in caps:
                capability_ledger.record(
                    agent_id=hid, capability=cap, success=True, score=1.0, duration_ms=0
                )
        print(
            f"[exp-v2] 完成回报已沉淀: ws={ws_id} member={member.member_id} "
            f"hid={hid} caps={caps} task_memory_size={len(task_memory.tasks)}",
            flush=True,
        )
        return True
    except Exception as _e:  # noqa: BLE001 - 经验沉淀失败不影响主流程
        print(f"[exp-v2] 完成回报沉淀失败: {_e}", flush=True)
        return False


# ─────────────────────────────────────────────────────────────
# V2-2：capability_ledger 信誉加权选人（纯规则排序 + 上下文提示）
# ─────────────────────────────────────────────────────────────

def rank_harnesses_by_reputation(
    task_text: str,
    harness_ids: list[str],
    harness_manager,
    capability_ledger,
) -> list[tuple[str, float]]:
    """纯规则加权排序：给定任务文本与候选 harness id 列表，
    返回 [(harness_id, score)] 按 score 降序。

    score = 候选 harness 在「任务关键词命中能力域」上的 ledger 信誉均值；
    无关键词命中时按该 harness 全部注册能力计算，仍提供信誉参考。
    **无信誉数据**的 harness（所有候选能力均无 ledger 记录）不入榜，
    调用方据此保持原逻辑兜底。
    """
    toks = extract_task_keywords(task_text or "")
    scored: list[tuple[str, float]] = []
    for hid in harness_ids:
        caps = harness_capabilities(hid, harness_manager)
        if not caps:
            continue
        hit = _capability_hit(caps, toks) if toks else []
        if not hit:
            hit = caps
        reps = [capability_ledger.get_reputation(hid, cap) for cap in hit]
        # 无任何真实信誉记录（全部等于中性先验 0.5）→ 无数据兜底，不入榜
        if not any(r != 0.5 for r in reps):
            continue
        scored.append((hid, round(sum(reps) / len(reps), 3)))
    scored.sort(key=lambda x: x[1], reverse=True)
    return scored


def harness_reputation_bonus(
    task_text: str,
    harness_ids: list[str],
    harness_manager,
    capability_ledger,
    max_show: int = 6,
) -> str:
    """生成信誉加权提示文本（注入 AI 选人输入 / 组长分工上下文）。

    仅追加提示、不硬改候选集：返回 "" 时调用方保持原逻辑。
    """
    ranked = rank_harnesses_by_reputation(task_text, harness_ids, harness_manager, capability_ledger)
    if not ranked:
        return ""
    lines = []
    for hid, score in ranked[:max_show]:
        caps = harness_capabilities(hid, harness_manager)
        name = hid
        try:
            sess = getattr(harness_manager, "sessions", {}).get(hid)
            info = getattr(sess, "info", None)
            if info is not None and getattr(info, "harness_name", ""):
                name = info.harness_name
        except Exception:
            pass
        lines.append(f"- {name}（{hid}）：相关能力信誉 {score:.3f}（能力：{', '.join(caps[:6]) or '—'}）")
    return (
        "【信誉加权提示（平台依据 capability_ledger 历史信誉排序，"
        "同能力域信誉高的候选优先承接）】\n" + "\n".join(lines)
    )
