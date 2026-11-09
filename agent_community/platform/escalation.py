# -*- coding: utf-8 -*-
"""`platform/escalation`：**分层协商的危急度状态机**（`V118` §5 / `V141` §1.3 / 队列 49-7）。

老板 2026-11-09 认可的口径（原话不改）：日常自治 / 危急度上报 / 阶段大会三层 + 常设审忆员；
**「超时未处理 ⇒ 自动升一级 + 审忆员记『超时未决』」——这是状态机，不是文案**。

## 为什么要有它

现在的"上报"靠人记：谁提了、几小时内该谁管、超了没有 —— 全靠脑子。
`lead-todo` 能算"谁没回执"，但**算不出"这件事卡在谁那里、还有多久到点"**。
这一层把「级别 → 时限 → 处理层级」写成表，超时就**真的升一级**（可查、可追）。

## 三条纪律

1. **级别是数据**：`L1..L5` 各有代号、时限、处理层级、载体（见 `LEVELS`），不是散落的 if；
2. **超时就升**：`sweep()` 扫一遍超未处理的记录 ⇒ 级别 +1 并标 `timed_out`（叫"超时未决"，不叫"已处理"）；
   **L5 到顶不再升**（已经是用户 + 审忆员 + 冻结）；
3. **只升不降、只记不改历史**：每次升级往 `log` 里追加一条（谁升的、为什么），原记录不覆盖。
"""
from __future__ import annotations

import json
import os
import time

#: 危急度五级（**唯一真源**；`GET …/escalation/levels` 与判据读的都是它）
LEVELS: dict = {
    "L1": {"code": "OBSERVE", "label": "L1 观察", "what": "不影响产出，值得记",
           "limit_min": None, "owner": "组级看板观察区", "carrier": "看板追加（L0 留痕）"},
    "L2": {"code": "GROUP", "label": "L2 组内", "what": "影响组内，组内可解",
           "limit_min": 24 * 60, "owner": "组长裁决", "carrier": "回执 `类型: 状态` + 账本"},
    "L3": {"code": "CROSS", "label": "L3 跨组", "what": "影响多组 / 资源竞争",
           "limit_min": 8 * 60, "owner": "团长（最近共同祖先）仲裁",
           "carrier": "回执 + `manifest_ref` 指向涉事任务"},
    "L4": {"code": "URGENT", "label": "L4 紧急", "what": "影响稳定 / 价值观冲突",
           "limit_min": 2 * 60, "owner": "用户直接介入", "carrier": "回执 + `_pending`（等拍板）"},
    "L5": {"code": "CRISIS", "label": "L5 危机", "what": "不可逆损害 / 信任破裂",
           "limit_min": 0, "owner": "用户 + 审忆员", "carrier": "同上 + 冻结"},
}

ORDER = ["L1", "L2", "L3", "L4", "L5"]

#: 阶段大会的周期（第三层：只校准方向、**不改拓扑**）
COUNCIL = {"group": {"every_days": 14, "label": "组级 2 周"},
           "team": {"every_days": 30, "label": "团级每月"},
           "all": {"every_days": 90, "label": "全体季度或里程碑"}}


def enabled() -> bool:
    return str(os.environ.get("AC_ESCALATION") or "1").strip() not in ("0", "false", "False", "off")


def levels_table() -> dict:
    return {"ok": True, "levels": {k: dict(v) for k, v in LEVELS.items()}, "order": list(ORDER),
            "council": {k: dict(v) for k, v in COUNCIL.items()},
            "rules": ["超时未处理 ⇒ **自动升一级**（状态机，不是文案）",
                      "L5 到顶不再升（已用户 + 审忆员 + 冻结）",
                      "只升不降；每次升级往 log 追一条，原记录不覆盖",
                      "阶段大会**只校准方向，不改拓扑**（拓扑变更走六原语，另档）"],
            "note": "级别是数据不是 if：时限/处理层级/载体都在表里，改口径只改这张表。"}


def level_of(name: str) -> dict:
    return dict(LEVELS.get(str(name or "").strip().upper()) or {})


def next_level(name: str) -> str:
    """升一级（L5 到顶 ⇒ 返回自己）。"""
    n = str(name or "").strip().upper()
    if n not in ORDER:
        return n
    i = ORDER.index(n)
    return ORDER[min(i + 1, len(ORDER) - 1)]


def new_record(*, level: str = "L2", summary: str = "", workshop_id: str = "",
               who: str = "", task_ref: str = "", now: float | None = None) -> dict:
    """开一条上报记录（算好 due_ts）。级别不认识 ⇒ 落到 L2 并注明。"""
    lv = str(level or "L2").strip().upper()
    note = ""
    if lv not in LEVELS:
        note = "级别 %r 不认识 ⇒ 按 L2 处理" % level
        lv = "L2"
    now = time.time() if now is None else float(now)
    lim = level_of(lv).get("limit_min")
    due = None if lim is None else now + float(lim) * 60
    # ⚠️ id 不能只用"秒级时间戳"：同一秒提两条会撞 id（判据 ⑤ 当场抓到 ——
    #   `next(c for c in changed if c.id == 探针id)` 拿到的其实是另一条）。
    import uuid as _uuid

    return {"id": "esc-%d-%s" % (int(now), _uuid.uuid4().hex[:6]),
            "level": lv, "code": level_of(lv)["code"],
            "summary": str(summary or ""), "workshop_id": str(workshop_id or ""),
            "task_ref": str(task_ref or ""), "who": str(who or "member"),
            "created_ts": now, "due_ts": due, "status": "open", "timed_out": False,
            "escalations": 0, "note": note,
            "log": [{"at": now, "level": lv, "why": "提报"}]}


def is_overdue(rec: dict, now: float | None = None) -> bool:
    """超没超时（L1 无时限 ⇒ 永不超时；已关闭的不算）。"""
    if str((rec or {}).get("status")) != "open":
        return False
    due = (rec or {}).get("due_ts")
    if due is None:
        return False
    now = time.time() if now is None else float(now)
    return now >= float(due)


def sweep(records: list, now: float | None = None) -> dict:
    """扫一遍：超时的**升一级**并标 `timed_out`（叫"超时未决"）。返回改动的清单。"""
    now = time.time() if now is None else float(now)
    changed = []
    for r in (records or []):
        if not is_overdue(r, now):
            continue
        old = str(r.get("level") or "")
        new = next_level(old)
        if new == old:                                          # L5 到顶：不再升，但标"超时未决"
            r["timed_out"] = True
            r.setdefault("log", []).append({"at": now, "level": old,
                                           "why": "超时未决（已到 L5 顶，不再升）"})
            changed.append({"id": r.get("id"), "from": old, "to": old,
                            "why": "超时未决（到顶）"})
            continue
        r["level"] = new
        r["code"] = level_of(new)["code"]
        lim = level_of(new).get("limit_min")
        r["due_ts"] = None if lim is None else now + float(lim) * 60
        r["timed_out"] = True
        r["escalations"] = int(r.get("escalations") or 0) + 1
        r.setdefault("log", []).append({"at": now, "level": new,
                                       "why": "超时未处理 ⇒ 自动升一级（原 %s）" % old})
        changed.append({"id": r.get("id"), "from": old, "to": new, "why": "超时自动升级"})
    return {"ok": True, "changed": changed, "checked": len(records or [])}


def view(records: list, now: float | None = None) -> dict:
    """看板用：按级别分组、标出超时未决（**只报事实**）。"""
    now = time.time() if now is None else float(now)
    rows = []
    for r in (records or []):
        left = None
        if r.get("due_ts") is not None:
            left = round((float(r["due_ts"]) - now) / 60.0, 1)
        rows.append({"id": r.get("id"), "level": r.get("level"), "code": r.get("code"),
                     "summary": r.get("summary"), "status": r.get("status"),
                     "timed_out": bool(r.get("timed_out")), "overdue": is_overdue(r, now),
                     "minutes_left": left, "owner": level_of(str(r.get("level") or "")).get("owner"),
                     "escalations": int(r.get("escalations") or 0)})
    counts = {}
    for r in rows:
        counts[str(r["level"])] = counts.get(str(r["level"]), 0) + 1
    return {"ok": True, "items": sorted(rows, key=lambda x: ORDER.index(str(x["level"]))
                                        if str(x["level"]) in ORDER else 9),
            "counts": counts,
            "overdue": len([r for r in rows if r["overdue"]]),
            "note": ("只报事实：谁提的、几级、还有多久到点、超没超。"
                     "**不评分、不排名**；超时的会自动升一级（`POST …/escalation/sweep`）。")}


def load(path) -> list:
    from pathlib import Path

    p = Path(str(path))
    if not p.is_file():
        return []
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except (ValueError, OSError) as e:                          # noqa: BLE001 — 坏文件要说，不能假装"空"
        print("[escalation] 读不了 %s（%s: %s）⇒ 按空处理" % (p, type(e).__name__, e), flush=True)
        return []
    return d if isinstance(d, list) else []


def save(path, records: list) -> None:
    from pathlib import Path

    p = Path(str(path))
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(records[-500:], ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(p)
