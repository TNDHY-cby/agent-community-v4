# -*- coding: utf-8 -*-
"""`platform/lead_algorithm`：**组长算法（两条路）**（设计 `V136`，老板 2026-11-07）。

## 一句话

输入一批接入的**能力面**（`V134` 算的，不手填），输出两种可行配置：

| 路径 | 什么时候走 | 输出 |
|---|---|---|
| **A · solo** | 有 `protocol_fit=high` 的候选（能派活+收回执+能写+能读） | 一个全能组长，不要助手 |
| **B · team** | 没有 high，但用助手能补齐 | 少协议组长 + 助手（每个说清"补了哪一项"） |
| **blocked** | 补齐不了 | 缺哪一项、谁能补、怎么补（**绝不返回假配置**） |

## 两条纪律（与既有口径衔接）

1. **不排分、不做"最佳人选"排名**：只给"两条路是否可行 + 具体配置 + 理由"；
   `protocol_count` 是**事实**（声明了几种协议），不是"能力分"；同分一律按 `harness_id` 字典序（可复现）。
2. **纯函数、只读**：推荐不落库；调用前后不改任何状态。
"""
from __future__ import annotations

#: 默认需求（与 `lead_capabilities` 的能力项一一对应）：四件事都得有人做
DEFAULT_NEEDS = ("dispatch", "receive_result", "read_artifacts", "wake")

#: 每项需求的人话（界面/判据都读这一份）
NEED_LABELS = {
    "dispatch": "能派活",
    "receive_result": "能收回执",
    "read_artifacts": "能看产物",
    "wake": "能自动唤醒",
}


def covers_of(caps: dict) -> list:
    """这个接入能覆盖哪几项需求（能力面 → 需求集合）。

    ⚠️ `read_artifacts` 只看**能不能读**（`can_read_files is True`）：能写文件 ≠ 能看产物 ——
       首版把"能写"也算进来，导致"读不了文件的主组长"被算成覆盖了"看产物"，助手永远配不出来（实测抓到）。
    """
    out = []
    if bool(caps.get("can_dispatch")):
        out.append("dispatch")
    if str(caps.get("can_receive_result") or "") == "yes":
        out.append("receive_result")
    if caps.get("can_read_files") is True:
        out.append("read_artifacts")
    if bool(caps.get("can_wake_automatically")):
        out.append("wake")
    return out


def _norm(c: dict) -> dict:
    """把能力面收敛成算法要用的形状（缺字段按"不能"处理，不猜能）。"""
    caps = c.get("capabilities") if isinstance(c.get("capabilities"), dict) else c
    return {
        "harness_id": str(c.get("harness_id") or ""),
        "harness_name": str(c.get("harness_name") or c.get("harness_id") or ""),
        "lead_eligible": bool(caps.get("lead_eligible")),
        "protocol_fit": str((caps.get("protocol_fit") or {}).get("level") or "low")
        if isinstance(caps.get("protocol_fit"), dict) else str(caps.get("protocol_fit") or "low"),
        "protocol_count": int(caps.get("protocol_count") or 0),
        "can_read_files": caps.get("can_read_files"),
        "can_write_files": caps.get("can_write_files"),
        "can_dispatch": bool(caps.get("can_dispatch")),
        "can_receive_result": str(caps.get("can_receive_result") or ""),
        "can_wake_automatically": bool(caps.get("can_wake_automatically")),
        "covers": covers_of(caps),
        "fit_reasons": list(((caps.get("protocol_fit") or {}).get("reasons") or [])
                            if isinstance(caps.get("protocol_fit"), dict) else []),
    }


def _sort_key(c: dict) -> tuple:
    """稳定排序：协议数多的在前，同分按名字（**不掺偏好**）。"""
    return (-int(c.get("protocol_count") or 0), str(c.get("harness_id") or ""))


def _coverage(winner_sets: list) -> dict:
    """把"谁覆盖了什么"摊成按需求的表。"""
    cov = {n: {"covered_by": []} for n in DEFAULT_NEEDS}
    for who, items in winner_sets:
        for it in items:
            cov.setdefault(it, {"covered_by": []})
            if who not in cov[it]["covered_by"]:
                cov[it]["covered_by"].append(who)
    for n, v in cov.items():
        v["label"] = NEED_LABELS.get(n, n)
        v["missing"] = not bool(v["covered_by"])
    return cov


def plan(candidates: list, needs: tuple | list | None = None, *,
         allow_lead_ids: set | None = None) -> dict:
    """**组长算法**：返回 `{mode, lead, helpers, coverage, why, alternatives, blocked?}`。

    `allow_lead_ids`：**限定"主组长"只能从这些 id 里选**（助手仍可从全池挑）。
    预列表（`V137`）按方向算配置时用它：主组长必须是这个方向的，助手可以跨方向借。
    """
    want = tuple(str(x) for x in (needs or DEFAULT_NEEDS) if str(x).strip())
    pool = [_norm(c) for c in (candidates or []) if isinstance(c, dict)]
    pool = [c for c in pool if c["harness_id"]]
    if not pool:
        return {"ok": True, "mode": "blocked", "needs": list(want), "lead": {}, "helpers": [],
                "coverage": {}, "why": ["还没有任何已注册的接入"],
                "alternatives": [], "blocked": {
                    "missing": list(want), "who_could_fix": [],
                    "next": "先去「对接中心」注册至少一个接入（能写文件 + 有协议/桥通道才能当主组长）"},
                "sort_note": "按协议数与名字排序是技术性稳定，不是推荐度"}

    def _lead_ok(c: dict) -> bool:
        if not c["lead_eligible"]:
            return False
        return allow_lead_ids is None or c["harness_id"] in allow_lead_ids

    eligible = [c for c in pool if _lead_ok(c)]
    highs = sorted([c for c in eligible if c["protocol_fit"] == "high"], key=_sort_key)
    mediums = sorted([c for c in eligible if c["protocol_fit"] != "high"], key=_sort_key)

    # ── 路径 A：全能单组长 ─────────────────────────────────────────────
    alternatives = []
    if highs:
        lead = highs[0]
        cov = _coverage([(lead["harness_id"], [n for n in want if n in lead["covers"]])])
        missing = [n for n in want if cov.get(n, {}).get("missing")]
        if not missing:
            return {
                "ok": True, "mode": "solo", "needs": list(want),
                "lead": _as_lead(lead, covers=[n for n in want if n in lead["covers"]]),
                "helpers": [], "coverage": cov,
                "why": ["它是**协议全能型**：%d 种协议，能派活、能收回执、能读写文件 ⇒ 一个人就闭环"
                        % lead["protocol_count"]],
                "others_solo": [{"harness_id": h["harness_id"], "protocol_count": h["protocol_count"]}
                                for h in highs[1:]],
                "alternatives": [{"mode": "team", "feasible": True,
                                  "reason": "也可以走 B（少协议组长 + 助手），但 A 已闭环、协作成本最低"}],
                "sort_note": ("多个 high 时按**协议数降序**、同分按 `harness_id` —— "
                              "这是技术性稳定，**不是推荐度**"),
            }
        # high 但需求里还有它覆盖不到的项（例如用户自定义 needs）⇒ 也要配助手
        alternatives.append({"mode": "solo", "feasible": False,
                             "reason": "它是 high，但需求 %s 它覆盖不到" % "、".join(missing)})

    # ── 路径 B：少协议组长 + 助手互补 ──────────────────────────────────
    team_reasons = []
    for lead in (mediums + [h for h in highs if h["harness_id"] not in
                            {x["harness_id"] for x in mediums}]):
        covered = [n for n in want if n in lead["covers"]]
        missing = [n for n in want if n not in covered]
        helpers, still = _greedy_fill(pool, exclude=lead["harness_id"], missing=list(missing),
                                      needs=want, covered=covered)
        if not still:
            cov = _coverage([(lead["harness_id"], covered)]
                            + [(h["harness_id"], h["adds"]) for h in helpers])
            return {
                "ok": True, "mode": "team", "needs": list(want),
                "lead": _as_lead(lead, covers=covered),
                "helpers": helpers, "coverage": cov,
                "why": (["没有协议全能的候选（%s）"
                         % ("；".join("`%s`：%s" % (c["harness_id"], "、".join(c["fit_reasons"]))
                                     for c in (highs or mediums)[:2]) or "候选里没有 high")]
                        + (["所以让「%s」当主组长，再由 %d 个助手补缺口" % (lead["harness_id"], len(helpers))]
                           if helpers else ["它一个人就覆盖了全部需求（只是不是 high）"])),
                "alternatives": ([{"mode": "solo", "feasible": False,
                                   "reason": "没有 `protocol_fit=high` 的候选"}]
                                 + alternatives),
                "sort_note": ("助手按**能补的缺口数**贪心挑，同分按协议数与名字 —— "
                              "技术性稳定，**不是推荐度**"),
                "team_reasons": team_reasons,
            }
        team_reasons.append("「%s」当主组长时仍缺 %s（补不齐）"
                            % (lead["harness_id"], "、".join(still)))

    # ── 都不可行：如实说缺什么 ─────────────────────────────────────────
    fitter = []
    for c in pool:
        can = [n for n in want if n in c["covers"]]
        if can:
            fitter.append({"harness_id": c["harness_id"], "covers": can,
                           "why_not_lead": ("不合格（缺写文件或协议通道）" if not c["lead_eligible"]
                                            else "适配度只有 %s" % c["protocol_fit"])})
    return {
        "ok": True, "mode": "blocked", "needs": list(want), "lead": {}, "helpers": [],
        "coverage": {}, "why": ["两条路都走不通"] + team_reasons[:3],
        "alternatives": alternatives,
        "blocked": {
            "missing": list(want),
            "who_could_fix": fitter[:8],
            "next": ("三条路任选：① 给某个候选加一个能派活并收回执的协议（A2A / HTTP_api / ACP / MCP / 桥）；"
                     "② 让它报 `tools` 让平台测得能写/读文件；③ 把需求缩小（例如先只要 `dispatch`）"),
        },
        "sort_note": "按协议数与名字排序是技术性稳定，不是推荐度",
    }


def _as_lead(c: dict, *, covers: list) -> dict:
    return {"harness_id": c["harness_id"], "harness_name": c["harness_name"],
            "protocol_fit": c["protocol_fit"], "protocol_count": c["protocol_count"],
            "lead_eligible": c["lead_eligible"], "covers": covers,
            "fit_reasons": c["fit_reasons"]}


def _greedy_fill(pool: list, *, exclude: str, missing: list, needs: tuple,
                 covered: list) -> tuple:
    """贪心补缺：每轮挑"能补最多缺口"的候选（同分按协议数、名字）。返回 `(helpers, still_missing)`。"""
    still = [m for m in missing]
    used = {exclude}
    helpers = []
    while still:
        best, best_adds = None, []
        for c in pool:
            if c["harness_id"] in used:
                continue
            adds = [n for n in still if n in c["covers"]]
            if not adds:
                continue
            if (best is None
                    or len(adds) > len(best_adds)
                    or (len(adds) == len(best_adds) and _sort_key(c) < _sort_key(best))):
                best, best_adds = c, adds
        if best is None:
            break
        used.add(best["harness_id"])
        still = [m for m in still if m not in best_adds]
        helpers.append({"harness_id": best["harness_id"], "harness_name": best["harness_name"],
                        "protocol_fit": best["protocol_fit"], "adds": best_adds,
                        "why": "它能补上「%s」" % "、".join(NEED_LABELS.get(a, a) for a in best_adds)})
    return helpers, still
