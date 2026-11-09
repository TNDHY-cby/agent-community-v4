# -*- coding: utf-8 -*-
"""`platform/topology`：**组织拓扑的唯一真源与校验器**（`V118` §2 + §8 G1 / 队列 50-1）。

## 为什么

老板 2026-11-09 定「冠层 + 根系」时说得很清楚：**分级不是画上去的，是要有校验器拦得住的**。
现在 `manifest` 的 G0 只覆盖"工作间级"五错，**Cell 级（分队/工位那一层）一条都没有** ——
谁把父子关系写成环、写两个爹、一个节点挂 20 个孩子，平台一句话不说。

## 唯一真源

`<工作区>/组织拓扑.md` —— **一份文件描述整棵树**（里面是一个 ```json 代码块）。
别处不许再存一份"父子关系"（一处真相）。

## 五条硬约束（`V118` §2 原文，一条不多一条不少）

| 码 | 内容 | 违背时 |
|---|---|---|
| `C001` | 父子链不许成环 | **拒** |
| `C002` | 一个 Cell 只能有一个直接上级（且 `children` 要与它一致） | **拒** |
| `C003` | 直接下级默认 ≤ `max_span`(5)，可被 `policies.max_span` 覆盖 | **警告 + 需显式确认** |
| `C004` | 向下默认 ≤ `max_depth`(5)，可被 `policies.max_depth` 覆盖 | **警告 + 需显式确认** |
| `C005` | 每个 Cell 必须声明"产物怎么验"（`verify.how` + `verify.where`） | **拒**（防空壳） |

## 一条纪律

**校验失败的文件一个字节都不动**（判据 K6 真验版本号不变）——
校验器最怕的样子是"拒了但已经写进去一半"。
"""
from __future__ import annotations

import json
import re

FORMAT_VERSION = 1
DEFAULT_MAX_SPAN = 5
DEFAULT_MAX_DEPTH = 5
FENCE_RE = re.compile(r"```json\s*\n(.*?)\n```", re.S)

#: 约束的人话（响应里直接用）
CODES = {
    "C001": "父子链成环",
    "C002": "一个 Cell 有多个上级 / children 与实际不符",
    "C003": "直接下级超过 max_span（需显式确认）",
    "C004": "层级深度超过 max_depth（需显式确认）",
    "C005": "没声明「产物怎么验」（空壳）",
}


def empty(workshop_id: str = "") -> dict:
    """一棵合法的空树（新工作间的初值：只有根 Cell）。"""
    wid = str(workshop_id or "")
    return {"format_version": FORMAT_VERSION, "workshop_id": wid,
            "root": wid or "root",
            "cells": ([{"id": wid or "root", "type": "工作间", "parent": None, "children": [],
                        "role": "本工作间", "verify": {"how": "产物目录 diff + 清单 sha256",
                                                     "where": "产物/"}}]
                      if (wid or "root") else [])}


def parse(text: str) -> tuple:
    """从 `组织拓扑.md` 的 ```json 块解析 ⇒ `(topo, error)`。解析不出就 `({}, 原因)`。"""
    raw = str(text or "")
    m = FENCE_RE.search(raw)
    body = m.group(1) if m else raw.strip()
    if not body:
        return {}, "文件里没有 ```json 代码块（拓扑要么在代码块里，要么整篇是 JSON）"
    try:
        topo = json.loads(body)
    except ValueError as e:
        return {}, "JSON 解析失败：%s" % e
    if not isinstance(topo, dict):
        return {}, "拓扑必须是对象（含 cells / root）"
    if not isinstance(topo.get("cells"), list):
        return {}, "拓扑缺 `cells` 数组"
    return topo, ""


def dump(topo: dict, *, note: str = "") -> str:
    """把拓扑写成 `组织拓扑.md` 的正文（**唯一真源的样子**）。"""
    head = ("# 组织拓扑（唯一真源）\n\n"
            "> 这份文件描述**整棵树**：工作间 → 分队 → 工位 → 任务。别的文件不许再存一份父子关系。\n"
            "> 五条硬约束由 `platform/topology.py` 校验（C001 无环 / C002 单亲 / C003 跨度 / C004 深度 / C005 可验证产物）。\n"
            "> `format_version` 每次**合法修改**自增；校验失败时这份文件**一个字节都不动**。\n")
    if note:
        head += "\n> %s\n" % str(note)
    return head + "\n```json\n" + json.dumps(topo, ensure_ascii=False, indent=2) + "\n```\n"


def _by_id(topo: dict) -> dict:
    out = {}
    for c in (topo.get("cells") or []):
        if isinstance(c, dict) and c.get("id"):
            out[str(c["id"])] = c
    return out


def validate(topo: dict, *, max_span: int | None = None,
             max_depth: int | None = None) -> dict:
    """五条硬约束 ⇒ `{ok, errors, warnings, stats}`（**纯函数，不碰盘**）。"""
    errors: list = []
    warnings: list = []
    cells = _by_id(topo)
    if not cells:
        return {"ok": False, "errors": [{"code": "C000", "cell": "", "why": "cells 是空的（至少要有根 Cell）"}],
                "warnings": [], "stats": {"cells": 0}}

    def _lim(cell: dict, key: str, default: int) -> int:
        pol = (cell or {}).get("policies") or {}
        try:
            return int(pol.get(key) or default)
        except (TypeError, ValueError):
            return default

    # —— C002 单亲 + children 与实际一致 ——
    claimed: dict = {cid: [] for cid in cells}
    for cid, c in cells.items():
        parent = c.get("parent")
        if parent is None:
            continue
        p = str(parent)
        if p not in cells:
            errors.append({"code": "C002", "cell": cid,
                           "why": "parent=%s 不在 cells 里（父节点必须真实存在）" % p})
            continue
        claimed[p].append(cid)
    for cid, c in cells.items():
        kids = [str(x) for x in (c.get("children") or [])]
        if len(set(kids)) != len(kids):
            errors.append({"code": "C002", "cell": cid, "why": "children 里有重复项"})
        extra = [k for k in kids if k not in claimed[cid]]
        missing = [k for k in claimed[cid] if k not in kids]
        if extra:
            errors.append({"code": "C002", "cell": cid,
                           "why": "children 里写了 %s，但它们没把 parent 指回来" % "、".join(extra[:4])})
        if missing:
            errors.append({"code": "C002", "cell": cid,
                           "why": "%s 把 parent 指给了你，但你的 children 里没有它们" % "、".join(missing[:4])})

    # —— C001 无环（从每个节点上溯，步数超过节点数即成环）——
    seen_cycle = set()
    for cid in cells:
        path, cur, steps = [], cid, 0
        while cur is not None and steps <= len(cells) + 1:
            if cur in path:
                cyc = path[path.index(cur):] + [cur]
                key = tuple(sorted(set(cyc)))
                if key not in seen_cycle:
                    seen_cycle.add(key)
                    errors.append({"code": "C001", "cell": cid,
                                   "why": "成环：%s" % " → ".join(cyc)})
                break
            path.append(cur)
            nxt = (cells.get(cur) or {}).get("parent")
            cur = str(nxt) if nxt is not None else None
            steps += 1

    # —— C003 跨度 / C004 深度（警告 + 需显式确认）——
    for cid, c in cells.items():
        span = len(c.get("children") or [])
        lim = _lim(c, "max_span", DEFAULT_MAX_SPAN if max_span is None else int(max_span))
        if span > lim:
            warnings.append({"code": "C003", "cell": cid, "needs_confirm": True,
                             "why": "直接下级 %d 个，超过上限 %d（要么拆，要么在 policies.max_span 里写明为什么这么大）"
                                    % (span, lim)})

    def _depth(cid: str, guard: int = 0) -> int:
        if guard > len(cells) + 1:
            return 0                                           # 防环时递归爆栈
        kids = [str(x) for x in ((cells.get(cid) or {}).get("children") or [])]
        kids = [k for k in kids if k in cells]
        if not kids:
            return 1
        return 1 + max(_depth(k, guard + 1) for k in kids)

    # ⚠️ 根的个数检查**无条件**做：以前写在 `if root not in cells:` 里面，
    #   导致"只要 topo.root 指了一个真实存在的 id，两棵树也能塞进来"（判据 K2 当场抓到）。
    roots = [cid for cid, c in cells.items() if c.get("parent") is None]
    if len(roots) != 1:
        errors.append({"code": "C002", "cell": "",
                       "why": "根的个数是 %d（必须恰好 1 个：parent 为 null 的那个）—— 指定 root 也躲不过" % len(roots)})
    root = str(topo.get("root") or "")
    if root not in cells:
        root = roots[0] if roots else ""
    if root:
        d = _depth(root)
        lim_d = _lim(cells.get(root) or {}, "max_depth",
                     DEFAULT_MAX_DEPTH if max_depth is None else int(max_depth))
        if d > lim_d:
            warnings.append({"code": "C004", "cell": root, "needs_confirm": True,
                             "why": "整棵树有 %d 层，超过上限 %d（要么压平，要么在根 policies.max_depth 里写明）"
                                    % (d, lim_d)})

    # —— C005 可验证产物 ——
    for cid, c in cells.items():
        v = c.get("verify") or {}
        how = str((v or {}).get("how") or "").strip()
        where = str((v or {}).get("where") or "").strip()
        if not how or not where:
            errors.append({"code": "C005", "cell": cid,
                           "why": "没声明「产物怎么验」（verify.how / verify.where 都要有）—— 空壳 Cell 不许进树"})

    return {"ok": not errors, "errors": errors, "warnings": warnings,
            "codes": dict(CODES),
            "stats": {"cells": len(cells), "errors": len(errors), "warnings": len(warnings)},
            "note": ("错误（**拒**）不给写；警告（跨度/深度）可以写，但要在 policies 里写明为什么 —— "
                     "这就是「需显式确认」的意思。")}


def apply(old_text: str, new_topo: dict, *, max_span: int | None = None,
          max_depth: int | None = None, note: str = "") -> dict:
    """校验通过才产出新正文（`format_version` 自增）；**不通过就原样返回旧正文**。

    返回 `{ok, text, result}`：`text` 永远是可以安全写回文件的内容
    （失败时就是 `old_text` 本身 ⇒ 调用方写回去也不会破坏文件，但**判据会盯"版本号不变"**）。
    """
    res = validate(new_topo, max_span=max_span, max_depth=max_depth)
    if not res.get("ok"):
        return {"ok": False, "text": old_text, "result": res,
                "note": "校验没过 ⇒ **文件不动**（一个字节都不写）"}
    old_ver = 0
    if old_text:
        old_top, err = parse(old_text)
        if not err:
            try:
                old_ver = int(old_top.get("format_version") or 0)
            except (TypeError, ValueError):
                old_ver = 0
    topo = dict(new_topo)
    topo["format_version"] = old_ver + 1 if old_ver else FORMAT_VERSION
    return {"ok": True, "text": dump(topo, note=note), "result": res,
            "format_version": topo["format_version"]}

# ══════════════════════════════════════════════════════════════════════════
# 看板强制路由（`V118` §8 G5 / 队列 50-2）
#   1. 只写自己看板  2. 往父写摘要（不是全文）  3. 跨组找 LCA（不直接写别的组）
# ══════════════════════════════════════════════════════════════════════════


def ancestors(topo: dict, cell_id: str) -> list:
    """从自己往上数（不含自己）：`[父, 爷爷, ...]`。成环时提前停（不爆栈）。"""
    cells = _by_id(topo)
    out, cur, guard = [], cell_id, 0
    seen = {cell_id}
    while guard <= len(cells) + 1:
        nxt = (cells.get(cur) or {}).get("parent")
        if nxt is None:
            break
        nxt = str(nxt)
        if nxt in seen:                                        # 环：停下（校验器会另报 C001）
            break
        out.append(nxt)
        seen.add(nxt)
        cur, guard = nxt, guard + 1
    return out


def lca(topo: dict, a: str, b: str) -> str:
    """最近共同祖先（找不到就返回空串）。自己跟自己 ⇒ 返回自己。"""
    if a == b:
        return a
    chain_b = [b] + ancestors(topo, b)
    for x in [a] + ancestors(topo, a):
        if x in chain_b:
            return x
    return ""


def route_write(topo: dict, src: str, dst: str, *, kind: str = "full") -> dict:
    """**这条写入该不该放行**（`G5`）⇒ `{allow, mode, via, code?, why, next?}`。

    `mode`：`full`（自己）/ `summary`（往上）/ `via_lca`（跨组，需先经共同祖先）。
    `kind`：`full`（全文）或 `summary`（摘要）—— 往上写**只收摘要**。
    """
    cells = _by_id(topo)
    s, d = str(src or ""), str(dst or "")
    k = str(kind or "full").strip().lower()
    if not s or not d:
        return {"allow": False, "code": "G5_BAD_INPUT", "mode": "none",
                "why": "要 src（谁在写）与 dst（写给哪个看板）"}
    if s not in cells:
        return {"allow": False, "code": "G5_UNKNOWN_CELL", "mode": "none",
                "why": "拓扑里没有这个 Cell：%s" % s, "next": "GET …/topology 看现有 cells"}
    if d not in cells:
        return {"allow": False, "code": "G5_UNKNOWN_CELL", "mode": "none",
                "why": "拓扑里没有这个 Cell：%s" % d, "next": "GET …/topology 看现有 cells"}

    # ① 写自己
    if s == d:
        return {"allow": True, "mode": "full", "via": "",
                "why": "写自己的看板，随便写"}

    up = ancestors(topo, s)
    if d in up:                                                # ② 往上（父/爷爷…）
        if k == "summary":
            return {"allow": True, "mode": "summary", "via": "",
                    "why": "往上第 %d 级写摘要 —— 对" % (up.index(d) + 1)}
        return {"allow": False, "mode": "summary", "code": "G5_PARENT_ONLY_SUMMARY",
                "why": "往上一级**只能写摘要**，不收全文（否则上级看板会被下级细节淹没）",
                "next": "把内容压成一段摘要再写（body 里 kind=summary）"}

    if s in ancestors(topo, d):                                # ③ 往下（后代）
        return {"allow": False, "mode": "none", "code": "G5_NO_DOWNWARD",
                "why": "不能替**下级**写看板（那会让「谁说的」变成一笔糊涂账）",
                "next": "让它自己写；你要交代事情就走派活/通知"}

    common = lca(topo, s, d)                                   # ④ 跨组
    if common and common != s and common != d:
        return {"allow": False, "mode": "via_lca", "via": common,
                "code": "G5_CROSS_GROUP_VIA_LCA",
                "why": "跨组：%s 与 %s 最近共同祖先是 %s，**不能直接写对方看板**" % (s, d, common),
                "next": "先写到 `%s` 的看板（摘要），由它分发；或走协商/通知" % common}
    return {"allow": False, "mode": "none", "code": "G5_NO_COMMON_ANCESTOR",
            "why": "跨组且找不到共同祖先（%s 与 %s 不在同一棵树里）" % (s, d),
            "next": "先确认拓扑是一棵树（C001/C002 会告诉你哪里有环或双亲）"}


def board_paths(topo: dict) -> dict:
    """每个 Cell 的看板/心跳落点（`board_path` 缺省时按 id 推一个，**只推路径不代建**）。"""
    out = {}
    for cid, c in _by_id(topo).items():
        bp = str((c or {}).get("board_path") or "").strip()
        out[cid] = {"board_path": bp or ("组织/%s/看板.md" % cid),
                    "declared": bool(bp)}
    return out
