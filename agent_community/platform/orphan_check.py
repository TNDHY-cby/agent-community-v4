# -*- coding: utf-8 -*-
"""platform/orphan_check：**"这个成员的 agent 还在不在"** —— 让"半删干净"看得见。

**起因（老板 2026-10-11 实测报告）**：「我之前把 agent 删掉了，看来删除功能并不完善」

示例Harness-E 复核实测（真实例）：
- `DELETE /api/harness/{hid}` 本身做得**很认真**：清注册、清三处队列、停桥进程、
  桥目录进回收站、关会话、写审计、落盘。
- **但它不会清理引用它的东西** ⇒ 删完之后工作间里留着**指向已不存在 agent 的成员**：
    实测 4 个（含老板那个 `示例Harness-B` 仍挂在 ws=237991b0 的组长位上）。
- 后果：**成员卡照常显示，平台不告诉你"它的 agent 已经没了"** —— 又是"看起来正常"的
  假状态（准则⑤）。用户只会觉得"接进去了但不干活"。

**本模块只做两件事**（纯读，不写任何东西）：
1. 判出一个成员是否"孤儿"（它要的 harness 不在注册表里）；
2. 给**人话**：为什么、怎么办。

⚠️ **不自动改数据**：删 agent 是否应顺带清工作间成员，是**产品决策**（要不要保留历史、
   要不要用户点头），所以先"如实报"，不擅自级联删（也与"停用≠删除"的一贯口径一致）。
"""
from __future__ import annotations

from typing import Any


def orphan_state(member: Any, known_harness_ids: set) -> dict:
    """判断一个成员是否"指着已不存在的 harness"。

    `known_harness_ids`：当前注册表里的 harness_id 集合（调用方给，避免本模块摸全局）。
    """
    hids = [str(x) for x in (getattr(member, "harness_ids", None) or []) if str(x).strip()]
    if not hids:
        return {"orphan": False, "missing": [], "text": "", "advice": ""}
    missing = [h for h in hids if h not in known_harness_ids]
    if not missing:
        return {"orphan": False, "missing": [], "text": "", "advice": ""}
    if len(missing) == len(hids):
        text = ("它的 agent（%s）已经不在平台上了 —— 这个成员现在**接不到任何活**"
                % "、".join(missing))
    else:
        text = ("它的一部分 agent 已经不在了：%s" % "、".join(missing))
    return {
        "orphan": True,
        "missing": missing,
        "text": text,
        "advice": ("要恢复：重新接入那个 agent（注册后名字一样就能接上）；"
                   "不要它了：在工作间里把这个成员移除（名单操作）"),
    }


def scan_workshop(ws: Any, known_harness_ids: set) -> dict:
    """扫一个工作间，列出所有"孤儿成员"（只读）。"""
    rows = []
    for m in (getattr(ws, "members", None) or []):
        st = orphan_state(m, known_harness_ids)
        if st["orphan"]:
            rows.append({
                "member_id": getattr(m, "member_id", ""),
                "role": getattr(m, "role", ""),
                "display_name": getattr(m, "display_name", ""),
                "session_name": (list(getattr(m, "session_aliases", []) or []) or [""])[0],
                **st,
            })
    return {
        "workshop_id": getattr(ws, "workshop_id", ""),
        "member_count": len(getattr(ws, "members", None) or []),
        "orphan_count": len(rows),
        "orphans": rows,
        # ★全站口径：有孤儿就必须说话（V37 §4.3 同款）
        "headline": (f"有 {len(rows)} 个成员的 agent 已经不在了（它们接不到活）"
                     if rows else "所有成员都对着还在的 agent"),
    }
