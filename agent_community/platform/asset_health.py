# -*- coding: utf-8 -*-
"""`platform/asset_health`：**团队资产体检**（`V118` §4 / `V141` §1.3 / 队列 49-8）。

老板认可的设定：**「只报事实，不评分」**；**「不自动稀释」** = 子集派遣只带**只读**风格快照。

## 两件事

1. **五维风格指纹**（代码审美 / 协作节奏 / 风险偏好 / 验证严格度 / 沟通密度）——
   存在根A 的 `asset/风格指南.md` **头部**（`- 代码审美: …` 这种一行一维）。没登记就如实说"未登记"，**不替它编**。
2. **腐化四信号**（僵化 / 默契退化 / 案例陈旧 / 教训遗忘）——
   每个信号只给**事实**（数字、日期、缺哪个文件）+ 一句**建议**，**没有分、没有排名、没有"健康度%"**。

## 为什么不能评分

全项目口径：不评分、不排名（老板 2026-10-08 暂停信誉机制）。体检一旦出分数，
就会变成"这个队几分" → 立刻滑向信誉评分。所以这里只允许输出事实。

## 「不自动稀释」

`snapshot()` 给子集派遣/借用返回的是**只读快照**（`readonly: true`），
写回父团队必须显式"风格合并"（另档，不在本模块）。
"""
from __future__ import annotations

import os
import time
from pathlib import Path

#: 五维风格指纹（顺序固定，报告按这个顺序出）
DIMS = [
    ("code_taste", "代码审美"),
    ("collab_rhythm", "协作节奏"),
    ("risk_appetite", "风险偏好"),
    ("verify_strictness", "验证严格度"),
    ("comm_density", "沟通密度"),
]

#: 腐化四信号（每条都能算成事实）
SIGNALS = [
    ("rigid", "僵化", "reusable 档经验最近 N 天没有新增"),
    ("tacit_decay", "默契退化", "名单成员数与风格快照里登记的不一致"),
    ("stale_cases", "案例陈旧", "`asset/经典案例/` 最后更新超过 N 天（或目录缺）"),
    ("forgotten_lessons", "教训遗忘", "`06_死胡同/` 有内容但 `asset/失败教训.md` 没有/更旧"),
]


def enabled() -> bool:
    return str(os.environ.get("AC_ASSET_HEALTH") or "1").strip() not in ("0", "false", "False", "off")


def _read_style(p: Path) -> dict:
    """读 `asset/风格指南.md` 头部的五维（`- 代码审美: xxx`）。读不到的维度留空。"""
    out = {k: "" for k, _ in DIMS}
    out["members_declared"] = None
    if not p.is_file():
        return out
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError as e:                                        # noqa: BLE001 — 读不到如实说
        print("[asset] 读不了 %s（%s: %s）" % (p, type(e).__name__, e), flush=True)
        return out
    for line in text.splitlines()[:40]:                        # 只看头部
        s = line.strip().lstrip("-").strip()
        if ":" not in s and "：" not in s:
            continue
        k, _, v = s.replace("：", ":").partition(":")
        k, v = k.strip(), v.strip()
        for key, label in DIMS:
            if k == label:
                out[key] = v
        if k in ("成员数", "人数"):
            try:
                out["members_declared"] = int("".join(c for c in v if c.isdigit()) or 0)
            except ValueError:
                out["members_declared"] = None
    return out


def _newest(d: Path) -> float | None:
    if not d.is_dir():
        return None
    ts = []
    for f in d.rglob("*"):
        if f.is_file():
            try:
                ts.append(f.stat().st_mtime)
            except OSError:                                    # noqa: BLE001 — 单个文件 stat 不到就跳过
                continue
    return max(ts) if ts else None


def scan(mind_root: str, key: str, *, members_count: int | None = None,
         stale_days: int = 30, now: float | None = None) -> dict:
    """体检一个团队/心智根（**只报事实 + 一句建议，没有分**）。"""
    if not enabled():
        return {"ok": True, "enabled": False, "key": key,
                "note": "资产体检已关闭（AC_ASSET_HEALTH=0）"}
    now = time.time() if now is None else float(now)
    root = Path(str(mind_root or ""))
    home = root / str(key or "")
    if not home.is_dir():
        return {"ok": False, "code": "NO_MIND_HOME",
                "error": "心智根里没有 %s（%s）" % (key, home),
                "next": "GET /api/mind/{agent_key} 先建根，或确认 agent_key"}

    style_p = home / "asset" / "风格指南.md"
    style = _read_style(style_p)
    days = lambda ts: None if ts is None else round((now - float(ts)) / 86400.0, 1)  # noqa: E731

    # ① 僵化：reusable 档经验最近 stale_days 天有没有新增
    mem_dir = home / "memories"
    reusable_recent = 0
    reusable_total = 0
    newest_reusable = None
    if mem_dir.is_dir():
        for f in mem_dir.glob("*.md"):
            try:
                t = f.read_text(encoding="utf-8", errors="replace")
                if "- 档: reusable" not in t:
                    continue
                reusable_total += 1
                mt = f.stat().st_mtime
                if newest_reusable is None or mt > newest_reusable:
                    newest_reusable = mt
                if now - mt <= stale_days * 86400:
                    reusable_recent += 1
            except OSError:                                    # noqa: BLE001 — 单个文件读不到不影响统计
                continue

    # ② 默契退化：名单成员数 vs 快照里登记的
    declared = style.get("members_declared")
    member_gap = None
    if declared is not None and members_count is not None:
        member_gap = int(members_count) - int(declared)

    # ③ 案例陈旧 / ④ 教训遗忘
    cases_newest = _newest(home / "asset" / "经典案例")
    dead_newest = _newest(home / "06_死胡同")
    lessons_p = home / "asset" / "失败教训.md"
    lessons_ts = None
    if lessons_p.is_file():
        try:
            lessons_ts = lessons_p.stat().st_mtime
        except OSError:                                        # noqa: BLE001 — stat 不到就当没有
            lessons_ts = None

    facts = [
        {"signal": "rigid", "label": "僵化", "fact":
            "`reusable` 档经验共 %d 条，最近 %d 天内新增 **%d** 条（最近一条是 %s 前）"
            % (reusable_total, stale_days, reusable_recent,
               "%s 天" % days(newest_reusable) if newest_reusable else "从没有过"),
         "triggered": reusable_total > 0 and reusable_recent == 0,
         "suggest": "经验层停更 ⇒ 建议抽一条「这次的做法能不能复用」写进 memories（reusable 档）"},
        {"signal": "tacit_decay", "label": "默契退化", "fact":
            ("名单成员数 %d，风格快照里登记 %d（差 %+d）" % (members_count, declared, member_gap)
             if member_gap is not None else
             ("风格快照里没登记成员数" if declared is None else "拿不到当前名单成员数")),
         "triggered": bool(member_gap not in (None, 0)),
         "suggest": "人变了但风格指南没更新 ⇒ 建议按新的合作节奏改一次 `asset/风格指南.md`（**人改**，平台不动）"},
        {"signal": "stale_cases", "label": "案例陈旧", "fact":
            ("`asset/经典案例/` 最近更新是 %s 前" % ("%s 天" % days(cases_newest))
             if cases_newest else "`asset/经典案例/` 不存在或为空"),
         "triggered": (cases_newest is None) or ((now - cases_newest) > stale_days * 86400),
         "suggest": "案例太久没换 ⇒ 建议把最近做成的那件事整理成一个案例（一条就够）"},
        {"signal": "forgotten_lessons", "label": "教训遗忘", "fact":
            ("`06_死胡同/` 最近更新 %s 前，`asset/失败教训.md` %s"
             % ("%s 天" % days(dead_newest) if dead_newest else "（空）",
                ("是 %s 天前" % days(lessons_ts)) if lessons_ts else "**不存在**")),
         "triggered": bool(dead_newest) and (lessons_ts is None or dead_newest > lessons_ts),
         "suggest": "踩过的坑没汇总 ⇒ 建议把 `06_死胡同/` 里那条搬一句到 `asset/失败教训.md`"},
    ]

    return {
        "ok": True, "enabled": True, "key": key, "mind_home": str(home),
        "fingerprint": {"dims": [{"key": k, "label": lb, "value": style.get(k) or "(未登记)"}
                                 for k, lb in DIMS],
                        "file": str(style_p),
                        "declared": "asset/风格指南.md 头部；没登记就如实说「未登记」，平台不替它编"},
        "signals": facts,
        "triggered": [f["signal"] for f in facts if f["triggered"]],
        "stale_days": stale_days,
        "no_score": True,
        "note": ("**只报事实，不评分、不排名**：每条给「事实 + 一句建议」，没有健康度百分比。"
                 "体检结果不改任何资产文件 —— 改不改、怎么改，是人的事。"),
    }


def snapshot(mind_root: str, key: str, *, members_count: int | None = None) -> dict:
    """**只读风格快照**（子集派遣 / 联合体借用用）——「不自动稀释」的执行形态。

    拿到的只是**一份只读副本**：`readonly: true`，并明确写清"写回父团队必须显式风格合并"。
    """
    s = scan(mind_root, key, members_count=members_count)
    if not s.get("ok"):
        return s
    return {"ok": True, "readonly": True, "key": key,
            "fingerprint": s.get("fingerprint"),
            "signals": [{"signal": x["signal"], "label": x["label"], "fact": x["fact"]}
                        for x in (s.get("signals") or [])],
            "note": ("这是**只读快照**：子集派遣只带它走，**不会**自动改父团队的资产。"
                     "要把改动写回父团队，必须显式「风格合并」并被记账（另档）。")}
