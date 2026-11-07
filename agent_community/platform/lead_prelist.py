# -*- coding: utf-8 -*-
"""`platform/lead_prelist`：**组长预列表**（设计 `V137`，2026-11-07 老板两次要求后的**优化版**）。

## 它解决什么

建工作间**之前**，用户要能回答：「做**前端**这类活，该用谁当组长？一个人够不够，还是要搭团？」

⇒ 键是**方向**，值是**该方向下的两套配置**（单 / 团），由 `V136` 的算法对"该方向的候选"算出来。

## ★ 优化版加的三件事（都是"让人一眼看懂、不猜"）

1. **一行结论 `headline`**：每个方向第一句就是人话 ——
   「一个人就够：示例Harness-A（2 种协议，四件事全包）」/「得搭团：A 当组长，借 测试 的 B 补『能看产物』」/
   「现在没人能接：缺 能看产物」；
2. **梯队 `alternatives`**：同一方向如果还有别的能 solo 的人，列在后面（按协议数、名字）——
   不是"推荐"，是"还有谁"；
3. **任务卡 `task_card`**：`?task=` 命中时给一张卡（关键词 → 命中方向 → 每个方向一行结论）；
   没命中给**换词建议 + 现有方向清单**，并说明"方向只认接入自己报的"。

## 三条纪律（没变）

1. **方向只认候选自己报的**（`fit_directions`），**没报的不归任何方向组**（进 `no_direction`）；
2. **不排分、不推荐**：方向内按协议数、`harness_id` 排序；`sort_note` 明写"不是推荐度"；
3. **不假装可行**：`feasible=false` 必有 `reason`/`missing`；`blocked` 必有 `next`。

## 助手可以跨方向

助手补的是**能力缺口**（能读/能收回执/能唤醒），不是"同方向的人"，所以允许借；
但每个助手都标 `from_direction`（它属于哪个方向），让人知道借了谁。
"""
from __future__ import annotations

from . import lead_algorithm as _alg


def _dirs_of(entry: dict) -> list:
    raw = entry.get("directions") or entry.get("fit_directions") or []
    out = []
    for d in raw:
        s = str(d or "").strip()
        if s and s not in out:
            out.append(s)
    return out


def _brief(c: dict) -> dict:
    """候选的精简视图（与组长列表同一套字段口径）。"""
    caps = c.get("capabilities") if isinstance(c.get("capabilities"), dict) else c
    cov = _alg.covers_of(caps)
    return {"harness_id": str(c.get("harness_id") or ""),
            "harness_name": str(c.get("harness_name") or c.get("harness_id") or ""),
            "protocol_fit": str(((caps.get("protocol_fit") or {}).get("level")
                                if isinstance(caps.get("protocol_fit"), dict)
                                else caps.get("protocol_fit")) or "low"),
            "protocol_count": int(caps.get("protocol_count") or 0),
            "covers": cov,
            "solo_feasible": all(n in cov for n in _alg.DEFAULT_NEEDS),
            "needs_helpers": [n for n in _alg.DEFAULT_NEEDS if n not in cov]}


def _labels(items: list) -> str:
    return "、".join(_alg.NEED_LABELS.get(x, x) for x in (items or []))


def _headline(g: dict) -> str:
    """一句话说清这个方向该怎么配（`V137` 优化：不用读字段就能懂）。"""
    d = g.get("direction") or "?"
    solo = g.get("solo") or {}
    team = g.get("team") or {}
    if solo.get("feasible"):
        lead = solo.get("lead") or {}
        alt = solo.get("alternatives") or []
        tail = ("；还有 %d 个也能一个人干" % len(alt)) if alt else ""
        return ("%s：**一个人就够** —— %s（%d 种协议，四件事全包）%s"
                % (d, lead.get("harness_name") or lead.get("harness_id") or "?",
                   int(lead.get("protocol_count") or 0), tail))
    if team.get("feasible"):
        lead = team.get("lead") or {}
        hs = team.get("helpers") or []
        parts = []
        for h in hs:
            parts.append("%s（补 %s）" % (h.get("harness_name") or h.get("harness_id") or "?",
                                        _labels(h.get("adds"))))
        return ("%s：**得搭团** —— %s 当组长，借 %s"
                % (d, lead.get("harness_name") or lead.get("harness_id") or "?", "、".join(parts)))
    miss = (g.get("blocked") or {}).get("missing") or (team.get("missing") or [])
    return "%s：**现在没人能接** —— 缺 %s" % (d, _labels(miss) or "（原因见 blocked）")


def build_prelist(entries: list, *, task: str = "") -> dict:
    """按方向给"单/团"两套配置。`entries` = `[{harness_id, harness_name, directions, capabilities}]`。"""
    pool = []
    for e in (entries or []):
        if not isinstance(e, dict) or not e.get("harness_id"):
            continue
        pool.append({**e, "harness_id": str(e["harness_id"]),
                     "harness_name": str(e.get("harness_name") or e["harness_id"]),
                     "directions": _dirs_of(e)})

    all_dirs: list = []
    for e in pool:
        for d in e["directions"]:
            if d not in all_dirs:
                all_dirs.append(d)
    all_dirs.sort()

    want = str(task or "").strip()
    hit_dirs = [d for d in all_dirs if want and (want in d or d in want)] if want else []
    task_card = {}
    if want:
        if hit_dirs:
            task_card = {"status": "hit", "keyword": want, "matched_directions": hit_dirs,
                         "next": "按下面命中的方向看结论；两个方向都要也支持（各自独立配）"}
        else:
            task_card = {
                "status": "miss", "keyword": want, "matched_directions": [],
                "available_directions": all_dirs,
                "next": ("没匹配到任何方向。换个说法（例如把「做页面」换成「前端」），"
                         "或先给接入补方向：对接中心 → 该接入 → 方向（`fit_directions`，可多个）"),
            }

    groups: list = []
    for d in all_dirs:
        cands = [e for e in pool if d in e["directions"]]
        if not cands:
            continue
        # 主组长**只能是这个方向的人**（`allow_lead_ids`）；助手可以从全池借（跨方向补能力）
        p = _alg.plan([{"harness_id": e["harness_id"], "harness_name": e["harness_name"],
                        "capabilities": e.get("capabilities") or e} for e in pool],
                      allow_lead_ids={e["harness_id"] for e in cands})
        lead = p.get("lead") or {}
        helpers = []
        for h in (p.get("helpers") or []):
            owner = next((e for e in pool if e["harness_id"] == h["harness_id"]), {})
            helpers.append({**h,
                            "from_direction": (owner.get("directions") or ["（未报方向）"])[0]})
        solo_ok = str(p.get("mode") or "") == "solo"
        team_ok = str(p.get("mode") or "") == "team"
        blk = p.get("blocked") or {}
        # 同方向还有谁能一个人干（梯队；不含已选的那个）
        alt = []
        for e in sorted(cands, key=lambda x: (-int(((x.get("capabilities") or {}).get("protocol_count") or 0)),
                                               x["harness_id"])):
            caps = e.get("capabilities") or e
            if _brief({"capabilities": caps})["solo_feasible"] and e["harness_id"] != lead.get("harness_id"):
                alt.append({"harness_id": e["harness_id"], "harness_name": e["harness_name"],
                            "protocol_count": int(caps.get("protocol_count") or 0)})
        g = {
            "direction": d,
            "candidates": [_brief({"capabilities": e.get("capabilities") or e}) | {
                "harness_id": e["harness_id"], "harness_name": e["harness_name"]}
                for e in sorted(cands, key=lambda x: x["harness_id"])],
            "solo": ({"feasible": True, "lead": lead, "covers": list(_alg.DEFAULT_NEEDS),
                      "alternatives": alt}
                     if solo_ok else
                     {"feasible": False,
                      "reason": (p.get("alternatives") or [{}])[0].get("reason")
                      or "没有能一个人闭环的候选（缺：%s）" % _labels(blk.get("missing")),
                      "alternatives": alt}),
            "team": ({"feasible": True, "lead": lead, "helpers": helpers,
                      "coverage": p.get("coverage") or {}} if team_ok else
                     {"feasible": False,
                      "missing": blk.get("missing") or [],
                      "reason": "这个方向搭团也补不齐（缺：%s）" % _labels(blk.get("missing")),
                      "next": blk.get("next") or "给候选加协议/读文件工具，或缩小需求"}),
            "blocked": ({"missing": blk.get("missing") or [], "next": blk.get("next") or ""}
                        if str(p.get("mode")) == "blocked" else {}),
            "why": p.get("why") or [],
        }
        g["headline"] = _headline(g)
        groups.append(g)

    no_dir = [_brief({"capabilities": e.get("capabilities") or e}) | {
        "harness_id": e["harness_id"], "harness_name": e["harness_name"]}
        for e in sorted(pool, key=lambda x: x["harness_id"]) if not e["directions"]]

    by_mode = {
        "solo": [{"direction": g["direction"], "harness_id": g["solo"]["lead"]["harness_id"],
                  "protocol_count": g["solo"]["lead"].get("protocol_count")}
                 for g in groups if g["solo"].get("feasible")],
        "team": [{"direction": g["direction"],
                  "lead": g["team"]["lead"]["harness_id"],
                  "helpers": [h["harness_id"] for h in g["team"].get("helpers") or []],
                  "missing": g["team"].get("missing") or []}
                 for g in groups if g["team"].get("feasible")],
        "blocked": [{"direction": g["direction"],
                     "missing": (g["blocked"] or {}).get("missing") or []}
                    for g in groups if g.get("blocked")],
    }

    return {
        "ok": True,
        "task_keyword": want,
        "task_card": task_card,
        "directions": groups,
        "matched_directions": hit_dirs,
        "match_note": (task_card.get("next") or "") if want else "",
        "no_direction": no_dir,
        "no_direction_note": ("这些接入**没报 `fit_directions`** ⇒ 平台不替它编方向；"
                              "要用它们请先在对接中心补方向（或在注册时带上）"),
        "by_mode": by_mode,
        "needs_def": {"items": list(_alg.DEFAULT_NEEDS), "labels": dict(_alg.NEED_LABELS)},
        "guidance": {
            "if_empty": ("还没有接入报方向 —— 在对接中心给 agent 填「方向」"
                         "（`fit_directions`，可多个，如 前端 / 后端 / 测试 / 运维）；注册时也能带上"),
            "if_all_blocked": ("所有方向都补不齐 —— 给候选加一个能派活并收回执的协议、"
                               "或让它报 `tools` 证明能读写文件；也可以先缩小需求（`V136` 的 `?needs=`）"),
            "if_partial": "有的方向能一个人干、有的要搭团 —— 按方向各配各的，不必全平台统一",
        },
        "sort_note": ("方向按名字排序、方向内按协议数与 `harness_id` 字典序 —— "
                      "技术性稳定，**不是推荐度**；助手可跨方向借（看 `from_direction`）"),
    }
