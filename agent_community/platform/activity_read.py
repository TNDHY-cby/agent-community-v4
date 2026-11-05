# -*- coding: utf-8 -*-
"""读一个**协议型成员自己的会话**（一处真相）。

两个使用者：
- `routers/activity.py`：成员活动视图里的"它的会话"一节；
- `routers/monitor.py`：监控室里的**会话观察窗**（给没有窗口的 MCP 这类成员）。

## 三条纪律

1. **只读**：调它的"读会话"工具一次，不写它那边、不写我们这边（不存副本）；
2. **读不到就说清**：没工具 / 没地址 / 调不通 / 解析不出，四种都要给 `why`，**不许返回空表装成功**；
3. **不猜工具名**：识别逻辑只经 `docking._pick_session_read_tool()`（一处真相，改一处两处都变）。
"""
from __future__ import annotations

import json
import time

#: 一次最多返回几条（别把人家的会话一次搬空）
MAX_SESSION_ITEMS = 20
#: 调它读会话的超时（秒）——短，读不到就如实说
SESSION_TIMEOUT = 8.0


def _ts(epoch) -> str:
    try:
        return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(float(epoch)))
    except Exception:                                          # noqa: BLE001 — 坏时间不影响正文
        return ""


def parse_messages(text: str, limit: int = MAX_SESSION_ITEMS) -> list:
    """把"读会话"工具的返回尽量解析成 `[{role, text, at}]`；解析不出就返回空（调用方会说明）。"""
    s = (text or "").strip()
    if not s:
        return []
    obj = None
    cands = [s]
    if "[" in s:
        cands.append(s[s.find("["):])
    if "{" in s:
        cands.append(s[s.find("{"):])
    for cand in cands:
        try:
            obj = json.loads(cand)
            break
        except ValueError:
            continue
    if obj is None:
        return []
    if isinstance(obj, dict):
        for k in ("messages", "items", "data", "history", "list"):
            if isinstance(obj.get(k), list):
                obj = obj[k]
                break
    if not isinstance(obj, list):
        return []
    out = []
    for it in obj[:max(1, int(limit or MAX_SESSION_ITEMS))]:
        if not isinstance(it, dict):
            continue
        out.append({
            "role": str(it.get("role") or it.get("sender") or it.get("from") or "?")[:20],
            "text": str(it.get("text") or it.get("content") or it.get("message") or "")[:600],
            "at": str(it.get("at") or it.get("created_at") or it.get("timestamp") or "")[:32],
        })
    return out


async def read_session(harness_id: str, info, tool_names: list,
                       limit: int = MAX_SESSION_ITEMS) -> dict:
    """读它的会话（**只读一次**）。返回 `{capable, why, tool, items, read_at, note}`。"""
    from .routers.docking import _pick_session_read_tool

    out = {"capable": False, "why": "", "tool": "", "items": [], "read_at": "", "note": ""}
    read_tool = _pick_session_read_tool([{"name": n} for n in (tool_names or [])])
    out["capable"] = bool(read_tool)
    if not read_tool:
        out["why"] = ("它**没有暴露「读会话」工具**（session_messages / session_history 之类）"
                      "⇒ 平台看不见它的会话内容。这不是平台没读，是它没提供。")
        out["note"] = "让它补一个 session_messages(limit) 工具；补完这里会自动出现"
        return out

    url = str(getattr(info, "api_base_url", "") or "").strip()
    if not url:
        out["why"] = "这个接入没有 api_base_url ⇒ 无处去读"
        return out

    out["tool"] = str(read_tool.get("name") or "")
    try:
        from . import mcp_client as _mc

        props = (read_tool.get("inputSchema") or {}).get("properties") or {}
        args = {}
        for k in ("limit", "count", "n"):
            if k in props:
                args[k] = max(1, min(int(limit or MAX_SESSION_ITEMS), 50))
        res = await _mc.mcp_call_tool(url, out["tool"], args, timeout=SESSION_TIMEOUT)
        text = str((res or {}).get("text") or "")
        out["read_at"] = _ts(time.time())
        out["items"] = parse_messages(text, limit=limit)
        out["note"] = ("这是**它自己库里的内容**，平台只读了一次、没存副本；"
                       "`role` 里的 `agent` 是它、`user` 是问它的人")
        if not out["items"]:
            out["why"] = ("工具调通了，但返回的文本没解析出消息列表 —— 原文前 200 字：%s"
                          % text[:200])
    except Exception as e:                                     # noqa: BLE001 — 读不到要如实说
        out["why"] = "读会话失败：%s: %s" % (type(e).__name__, str(e)[:160])
    return out
