# -*- coding: utf-8 -*-
"""`platform/lead_roster`：**组长列表**（设计 `V116` §9 / `V115` P5 批）。

## 铁律（架构师与 `V116` 都写死过）

1. **`lead_eligible` 是算出来的，不许手填**：`tooling.fs_write` 且（`protocol_call` 或 `bridge_poll`）；
2. **平台不排序、不推荐**：输出按 `harness_id` 字典序，响应里明写"按名字排序，不是推荐"；
3. **测不出来就是 `null`**，`null` 不进候选，并说清"为什么测不出来、怎么补测"。

## tooling 从哪来（如实）

只从**注册信息**里读，不猜：
- `tools` 名单里出现文件操作类工具（`write_file`/`edit`/`apply_patch`/`fs`…）⇒ `fs_write=true`；
- `wakeup_method`：`mcp`/`http_api`/`acp` ⇒ `protocol_call=true`；`file_poll` ⇒ `bridge_poll=true`；`clipboard` ⇒ 只有 `gui_paste`；
- 都读不到 ⇒ `null` + 原因。
"""
from __future__ import annotations

FS_TOOL_HINTS = ("write_file", "writefile", "edit_file", "apply_patch", "fs_write",
                 "create_file", "write", "shell", "terminal", "run_command")
PROTOCOL_WAKEUPS = ("mcp", "http_api", "http", "acp")


def _harness_infos() -> list:
    """取所有已注册 harness 的 `(harness_id, info)` —— 结构变了就返回空（并说一句）。"""
    try:
        from .server import harness_manager  # type: ignore[attr-defined]
    except Exception as e:                                     # noqa: BLE001 — 结构变了要说，不能装作没有
        print("[lead-roster] 取 harness 名册失败（%s: %s）；本次按空列表处理"
              % (type(e).__name__, e), flush=True)
        return []
    out = []
    sessions = getattr(harness_manager, "sessions", {}) or {}
    for hid, sess in sessions.items():
        info = getattr(sess, "info", None)
        out.append((str(hid), info))
    return out


def tooling_of(info) -> dict:
    """（下面这段是声明侧的推断；实测值由 `_merge_measured` 覆盖。）"""
    """从注册信息抽 tooling。**测不出来就 None**（不猜、不假装能）。"""
    if info is None:
        return {"fs_write": None, "protocol_call": None, "bridge_poll": None, "gui_paste": None,
                "why": "这条 harness 没有注册信息（拿不到 info）"}
    tools = [str(t).lower() for t in (getattr(info, "tools", None) or [])]
    if not tools:
        # 有的结构把工具放在 capabilities 里
        tools = [str(t).lower() for t in (getattr(info, "capabilities", None) or [])]
    fs = True if any(h in t for t in tools for h in FS_TOOL_HINTS) else (None if not tools else False)
    wake = str(getattr(getattr(info, "wakeup_method", ""), "value",
                       getattr(info, "wakeup_method", "")) or "").lower()
    proto = True if wake in PROTOCOL_WAKEUPS else False
    bridge = True if wake == "file_poll" else False
    gui = True if wake == "clipboard" else False
    out = {"fs_write": fs, "protocol_call": proto, "bridge_poll": bridge, "gui_paste": gui,
           "wakeup_method": wake, "tools_count": len(tools), "why": ""}
    # 不合格就**必须**给具体原因（界面只显示这一句；空着等于没说，2026-10-29 真机渲染时发现）
    if not eligible(out):
        out["why"] = _why_not_eligible(out)
    return out


def _why_not_eligible(tool: dict) -> str:
    """三种缺法分开说：测不到 fs / 报的工具里没有文件操作 / 能写但没有回执通道。"""
    fs = tool.get("fs_write")
    has_channel = bool(tool.get("protocol_call") or tool.get("bridge_poll"))
    wake = str(tool.get("wakeup_method") or "")
    if fs is None:
        return ("这个 harness 注册时没报工具名单 ⇒ 平台**测不到它能不能写文件**，所以不算组长候选；"
                "要当组长得先让它报 `tools`（或点「实测写一次盘」）")
    if fs is False:
        return ("它报的工具里**没有文件操作**（`write_file` / `edit` / `shell` 之类）⇒ 判它写不了文件；"
                "组长必须能写任务文件与产物，所以它只能当成员")
    if not has_channel:
        hint = ("唤醒方式是 `%s`（只能人工粘贴，回执送不回来）" % wake) if wake else "没有可用的回执通道"
        return ("能写文件，但**没有回执通道**：%s ⇒ 派了活它没法把结果交回来；"
                "要当组长得配 `mcp` / `http_api` / `acp`（协议）或 `file_poll`（桥）" % hint)
    return ""


def _merge_measured(harness_id: str, tool: dict) -> dict:
    """实测（`tooling_probe`）优先：它写过盘就覆盖声明的 `fs_write`，并标清来源。

    ★无论有没有实测记录，**出来时 `why` 一定是当前口径**（合格＝空串，不合格＝具体原因）。
      否则"实测说能写、但没有回执通道"这种情况会留着旧的"测不到"原因（2026-10-29 判据抓到）。
    """

    def _final(out: dict) -> dict:
        out["why"] = "" if eligible(out) else _why_not_eligible(out)
        return out

    try:
        from . import tooling_probe as _tp

        m = _tp.measured(harness_id)
    except Exception as e:                                     # noqa: BLE001 — 读不到就说一句
        print("[lead-roster] 取实测 tooling 失败（%s: %s）" % (type(e).__name__, e), flush=True)
        return _final(dict(tool))
    if not m:
        return _final(dict(tool))
    out = dict(tool)
    out["fs_write_measured"] = m.get("fs_write")
    out["fs_write_measured_at"] = m.get("at")
    out["fs_write_measured_scope"] = m.get("scope")
    if m.get("fs_write") is not None:
        out["fs_write"] = bool(m.get("fs_write"))
        out["fs_write_source"] = "measured"
    return _final(out)


def eligible(tooling: dict) -> bool:
    return bool(tooling.get("fs_write") is True and
                (tooling.get("protocol_call") or tooling.get("bridge_poll")))


def _directions_of(info) -> list:
    """方向树：`agent_profile.fit_directions` 若存在就读，否则空（不编）。"""
    for attr in ("fit_directions", "directions"):
        v = getattr(info, attr, None)
        if isinstance(v, (list, tuple)) and v:
            return [str(x) for x in v]
    return []


def candidates(direction: str = "", *, fallback_all: bool = True) -> dict:
    """按方向列组长候选。**只列 `lead_eligible` 的**；未命中方向时退化全量并说明。"""
    items = []
    for hid, info in _harness_infos():
        tool = _merge_measured(hid, tooling_of(info))
        dirs = _directions_of(info)
        items.append({"harness_id": hid,
                      "harness_name": str(getattr(info, "harness_name", "") or hid),
                      "model": str(getattr(getattr(info, "ai", None), "model_name", "") or ""),
                      "directions": dirs,
                      "lead_eligible": eligible(tool),
                      "tooling": tool})
    items.sort(key=lambda x: x["harness_id"])            # 稳定排序：按名字，不是推荐
    want = str(direction or "").strip()
    matched = [x for x in items if want and want in (x["directions"] or [])]
    pool = matched if matched else []
    note = ""
    if want and not matched:
        if fallback_all:
            pool = items
            note = ("方向「%s」没有绑定到任何 harness 的方向树 ⇒ **退化列全量**（平台不判断谁合适，"
                    "你自己按名字点一个）" % want)
        else:
            note = "方向「%s」没有匹配的 harness（本实例不退化）" % want
    elif not want:
        pool = items
        note = "没给方向 ⇒ 列全量"
    leads = [x for x in pool if x["lead_eligible"]]
    not_leads = [{"harness_id": x["harness_id"], "why": (x["tooling"].get("why")
                                                         or "缺 fs_write 或（protocol_call/bridge_poll）")}
                 for x in pool if not x["lead_eligible"]]
    return {"ok": True, "direction": want, "count": len(leads), "candidates": leads,
            "excluded": not_leads, "note": note,
            "sort_note": "按 `harness_id` 字典序列出，**不是推荐、不是评分**",
            "hint": ("组长必须能写文件 + 能把回执送回来；测不出来的（`fs_write=null`）不进候选。"
                     "网页版那类只能当成员。")}


def harness_options() -> list:
    """给界面展示"选谁"用：所有 harness + 它能不能当组长（含原因）。"""
    out = []
    for hid, info in _harness_infos():
        tool = _merge_measured(hid, tooling_of(info))
        out.append({"harness_id": hid,
                    "harness_name": str(getattr(info, "harness_name", "") or hid),
                    "model": str(getattr(getattr(info, "ai", None), "model_name", "") or ""),
                    "lead_eligible": eligible(tool), "tooling": tool})
    out.sort(key=lambda x: x["harness_id"])
    return out
