# -*- coding: utf-8 -*-
"""`platform/audit_checks`：**审忆员扩面**（`V118` §8 G6 / 队列 50-4）。

## 原来只审"记忆 diff"

夜间审忆员（`tools/mind_reviewer.py`）管的是 `_pending/*.diff`（核心基模改写）。
老板 2026-11-09 把「冠层 + 根系」定下来后，**该被审的东西不止记忆**：
结构（拓扑）合不合法、资产（风格/案例/教训）跟不跟得上、有没有人越权写看板 ——
这三件事以前没人看，出了问题也只会"慢慢烂掉"。

## 本模块只做**体检报告**，不替人改

三类检查，全部**复用已有函数**（一处真相，不重写判定逻辑）：
1. `topology` —— `topology.validate()`（C001~C005）；
2. `assets`   —— `asset_health.scan()` 的信号 + 风格指南五维是否还是「待填」；
3. `authority` —— `topology.board_paths()` 里**声明了**的看板文件必须在；
   另有"声明了但读不到"要说清（不是假装健康）。

**只读**：跑完不改任何文件（判据 ⑤ 盯前后文件列表逐字相同）。
"""
from __future__ import annotations

from pathlib import Path

CATEGORIES = ("topology", "assets", "authority")
DIMS = ("代码审美", "协作节奏", "风险偏好", "验证严格度", "沟通密度")


def _item(cat: str, code: str, level: str, where: str, why: str, nxt: str = "") -> dict:
    return {"category": cat, "code": code, "level": level, "where": where,
            "why": why, "next": nxt}


def _check_topology(ws, topo_file: Path) -> list:
    from . import topology as _topo

    if not topo_file.is_file():
        return [_item("topology", "AUD_TOPOLOGY_MISSING", "info", str(topo_file),
                      "这个工作间还没有 `组织拓扑.md`（没有父子关系，看板路由与深度/跨度都判不了）",
                      "POST /api/workshop/{id}/topology 交一份")]
    topo, err = _topo.parse(topo_file.read_text(encoding="utf-8", errors="replace"))
    if err:
        return [_item("topology", "AUD_TOPOLOGY_UNREADABLE", "error", str(topo_file),
                      "读不懂这份拓扑：%s" % err, "看 ```json 块是否是合法 JSON")]
    out = []
    for e in ((_topo.validate(topo) or {}).get("errors") or []):
        out.append(_item("topology", "AUD_" + str(e.get("code")), "error",
                         str(e.get("cell") or ""), str(e.get("why") or ""),
                         "按 C001~C005 改拓扑（POST 会拒并给清单）"))
    for w in ((_topo.validate(topo) or {}).get("warnings") or []):
        out.append(_item("topology", "AUD_" + str(w.get("code")), "warn",
                         str(w.get("cell") or ""), str(w.get("why") or ""),
                         "在对应 Cell 的 policies 里写明为什么这么大/这么深"))
    return out


def _check_assets(mind_home: Path, members_count: int | None = None) -> list:
    from . import asset_health as _ah

    out: list = []
    style = mind_home / "asset" / "风格指南.md"
    txt = style.read_text(encoding="utf-8", errors="replace") if style.is_file() else ""
    if not style.is_file():
        out.append(_item("assets", "AUD_STYLE_MISSING", "warn", str(style),
                         "没有 `asset/风格指南.md`（团队指纹无从谈起）",
                         "建一个（`mind.ensure()` 会自动建骨架）"))
    else:
        blank = [d for d in DIMS if ("- %s:" % d) in txt
                 and "待填" in txt.split("- %s:" % d, 1)[1].split("\n", 1)[0]]
        if blank:
            out.append(_item("assets", "AUD_STYLE_BLANK", "info", str(style),
                             "风格指南这 %d 维还是「待填」：%s" % (len(blank), "、".join(blank)),
                             "让组长/人把这几行写成自己的口味（平台不替你编）"))
    try:
        # ⚠️ 成员数必须由调用方给：写死 0 会让**每个**工作间都报「默契退化」
        #   （判据 ⑥ 当场抓到）。给 None 时不传，让体检自己按「未知」处理。
        kw = {} if members_count is None else {"members_count": int(members_count)}
        scan = _ah.scan(str(mind_home.parent), str(mind_home.name), **kw)
        for s in ((scan or {}).get("signals") or []):
            if s.get("triggered"):
                out.append(_item("assets", "AUD_ASSET_" + str(s.get("signal") or "").upper(),
                                 "warn", str(mind_home),
                                 str(s.get("fact") or ""), str(s.get("suggest") or "")))
    except Exception as e:                                     # noqa: BLE001 — 体检算不出来要说清
        out.append(_item("assets", "AUD_ASSET_SCAN_FAILED", "error", str(mind_home),
                         "资产体检没跑成（%s: %s）" % (type(e).__name__, e),
                         "看日志；这是平台问题不是你的数据问题"))
    return out


def _check_authority(topo_file: Path, ws_path: Path) -> list:
    from . import topology as _topo

    if not topo_file.is_file():
        return []
    topo, err = _topo.parse(topo_file.read_text(encoding="utf-8", errors="replace"))
    if err:
        return []
    out: list = []
    for cid, info in (_topo.board_paths(topo) or {}).items():
        if not info.get("declared"):
            continue                                           # 没声明就不算欠账
        bp = Path(ws_path) / str(info.get("board_path") or "")
        if not bp.is_file():
            out.append(_item("authority", "AUD_BOARD_DECLARED_MISSING", "warn", str(bp),
                             "拓扑里声明了 `%s` 的看板，但文件不在" % cid,
                             "建它，或把声明改成实际路径（别让声明和现实两张皮）"))
    return out


def run(ws, *, mind_home: str = "", members_count: int | None = None) -> dict:
    """跑三类体检 ⇒ `{ok, counts, items, readonly: true}`（**不改盘**）。"""
    ws_path = Path(str(getattr(ws, "workspace_dir", "") or ""))
    topo_file = ws_path / "组织拓扑.md"
    mind = Path(str(mind_home)) if mind_home else (ws_path / "_mind")
    items: list = []
    items += _check_topology(ws, topo_file)
    items += _check_assets(mind, members_count)
    items += _check_authority(topo_file, ws_path)
    counts = {c: 0 for c in CATEGORIES}
    for it in items:
        counts[it["category"]] = counts.get(it["category"], 0) + 1
    errors = sum(1 for i in items if i["level"] == "error")
    warns = sum(1 for i in items if i["level"] == "warn")
    return {"ok": errors == 0, "readonly": True,
            "categories": list(CATEGORIES), "counts": counts,
            "levels": {"error": errors, "warn": warns,
                       "info": len(items) - errors - warns},
            "items": items,
            "no_score": True,
            "note": ("这是**体检报告**，不是评分：只列「哪里不对、为什么、下一步」，"
                     "没有分数也没有排名。平台不替人改，改由人或审忆员按单子来。")}
