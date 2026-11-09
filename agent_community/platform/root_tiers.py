# -*- coding: utf-8 -*-
"""`platform/root_tiers`：**根系四层的治理参数**（`V141` §3.1，队列 49-1）。

老板 2026-11-09 的设定原话（**不改动**）：

> **主根层** 宪章（评分、奖惩、底线规则）—— 低频 / 高权威 / **变更需高门槛**
> **侧根层** 审忆（审查记忆、纠偏）—— 中频 / 中高 / **要证据链、交叉复核、可申诉**
> **须根层** 日志 —— 高频 / 低 / 自动记录
> **储藏根层** 经验 —— 周期性 / 中 / **重提炼、分级入库、可淘汰**

**这一层的价值不是"多一个常量文件"，而是把"频率-风险-门槛"写成机器能查的东西**：
以前只有"宪章人写"这一条真的按门槛在管，其余三层全靠自觉。

## 三条硬不变量（判据盯着，不许被绕过）

1. **宪章只有人能改**（`write="human_only"` + `appealable=False`）——
   申诉通道对宪章**一律拒**（"宪章不可被申诉改，只能人改"）；
2. **经验必须分档**（`grade ∈ {reusable, record}`）—— 写入不给档就拒（`R1`）；
3. **只有 record 档可自动淘汰**（`reusable` 要人决定，`R2`）。
"""
from __future__ import annotations

#: 四层治理参数（**唯一真源**；`GET /api/root-tiers` 与判据读的都是这张表）
TIERS: dict = {
    "charter": {"label": "主根 · 宪章", "write": "human_only", "need_evidence": False,
                "need_crosscheck": False, "appealable": False, "periodic": None,
                "evictable": False,
                "what": "评分、奖惩、底线规则（**只有人能改**；审忆员不改、申诉也改不了）"},
    "review": {"label": "侧根 · 审忆", "write": "reviewer", "need_evidence": True,
               "need_crosscheck": True, "appealable": True, "periodic": "nightly",
               "evictable": False,
               "what": "审查记忆、纠偏、防记忆污染（改前 hash + 归档 + 留 diff）"},
    "log": {"label": "须根 · 日志", "write": "auto", "need_evidence": False,
            "need_crosscheck": False, "appealable": False, "periodic": "daily",
            "evictable": False,
            "what": "日常留痕（自动记录、append-only、只在 30 天后归档）"},
    "experience": {"label": "储藏根 · 经验", "write": "reviewer", "need_evidence": True,
                   "need_crosscheck": False, "appealable": True, "periodic": "monthly",
                   "grade": ["reusable", "record"], "evictable": True,
                   "what": "沉淀复用、反哺规则（**必须分 reusable/record 两档**；record 可淘汰）"},
}

#: `write` 的语义 → 允许的写入者
WRITERS = {
    "human_only": ("human", "user"),
    "reviewer": ("reviewer", "human", "user"),
    "auto": ("auto", "system", "reviewer", "human", "user"),
}

#: 经验档位的人话
GRADES = {"reusable": "复用级（能当经验复用，**不受自动淘汰**，要人决定）",
          "record": "记录级（只是留个记录，长期没人用可淘汰）"}


def tier_of(name: str) -> dict:
    """按名字取一层的参数（不认识的层 ⇒ 空 dict，调用方用 `not t` 判）。"""
    return dict(TIERS.get(str(name or "").strip().lower()) or {})


def names() -> list:
    return list(TIERS)


def can_write(tier: str, who: str) -> bool:
    """这层**允许谁写**（`human_only` / `reviewer` / `auto`）。"""
    t = tier_of(tier)
    if not t:
        return False
    allowed = WRITERS.get(str(t.get("write") or ""), ())
    return str(who or "").strip().lower() in allowed


def appealable(tier: str) -> bool:
    """这层**能不能申诉**。宪章 `False`（不可被申诉改）。"""
    return bool(tier_of(tier).get("appealable"))


def need_evidence(tier: str) -> bool:
    return bool(tier_of(tier).get("need_evidence"))


def evictable(tier: str) -> bool:
    return bool(tier_of(tier).get("evictable"))


def grades_of(tier: str) -> list:
    return list(tier_of(tier).get("grade") or [])


def validate_grade(tier: str, grade: str) -> tuple:
    """校验一条写入的档位 ⇒ `(ok, 错误码, 人话)`。

    **这一层的硬要求**：`experience` 必须给 `grade`，不给就拒（`R1`）；
    其它层不要求档位（它们没有"分档"这件事）。
    """
    t = tier_of(tier)
    if not t:
        return False, "NO_SUCH_TIER", "没有这一层：%s（可用：%s）" % (tier, "、".join(names()))
    want = grades_of(tier)
    if not want:
        return True, "", ""                       # 这层不分档
    g = str(grade or "").strip().lower()
    if not g:
        return (False, "GRADE_REQUIRED",
                "这一层**必须**带 `grade`（%s）：不给就拒 —— 否则「能复用的经验」和「只是记录」"
                "会混在一层，检索面很快被垃圾淹没" % " / ".join(want))
    if g not in want:
        return (False, "BAD_GRADE",
                "`grade` 只能是 %s（收到 %r）" % (" / ".join(want), grade))
    return True, "", ""


def table() -> dict:
    """整张表（给前端 / agent / 判据看的口径）。"""
    return {"ok": True, "tiers": {k: dict(v) for k, v in TIERS.items()},
            "grades": dict(GRADES),
            "invariants": [
                "宪章只有人能改（write=human_only、appealable=false、申诉一律拒）",
                "经验必须分档（grade ∈ reusable / record，不给就拒）",
                "只有 record 档可自动淘汰；reusable 要人决定",
                "归档不物理删（淘汰 = 移进 archive/，可恢复）",
            ],
            "note": ("这四层解决的是**变更门槛**：频率越高、风险越低、越自动化；"
                     "越高权威、越慢、越要人。以前只有「宪章人写」一条真在管，其余靠自觉。")}
