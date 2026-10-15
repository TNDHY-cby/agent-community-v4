# -*- coding: utf-8 -*-
"""平台助手工具 · `find_window` —— **按关键词在桌面上找窗口**（"视觉激活"的第一步：定位）。

## 为什么加这个工具（`FE-26` §3.4，2026-10-15 老板拍板「加」）

老板要把「视觉激活」做成前台的一个服务：**用户填关键词 → agent 去定位 → 落成视觉激活脚本**。
实测盘点下来，"定位"这一步**平台 Agent 没有工具可用**：
`visual_launch.list_windows()` 只存在于后端代码里，Agent 只能 `shell_exec` 去拼 PowerShell ——
那条路 **Windows 专属、拿不到结构化结果、不跨平台、也过不了产品化判据**。
本工具就是把**已有**的 `visual_launch` 能力**开放给 Agent**（**不另写一套枚举**，一处真相）。

## 纪律

1. **不假装找到**：非 Windows / 没有注入脚本 / 匹配不到 —— 一律**如实说**，并给"下一步怎么办"；
2. **回结构化结果**（Agent 要能据此判断），而不是只回一段人话；
3. 同步阻塞的枚举要 `to_thread`（枚举要起 PowerShell 子进程，async 里绝不许同步阻塞）；
4. **只读**：只列窗口，**不注入、不动用户桌面**（注入是 `try-activate` 端点的事，由用户在界面上点）。
"""
from __future__ import annotations

import asyncio
import json

from ..tool_registry import BaseTool, ToolSchema, ToolResult

__all__ = ["FindWindowTool"]


class FindWindowTool(BaseTool):
    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name="find_window",
            description=(
                "按**关键词**在用户桌面上找窗口（视觉激活的第一步：定位）。"
                "返回候选窗口（标题 / 进程 / pid / 窗口类 / 命中哪些关键词 / 匹配分）。"
                "用户说不清那个 agent 的窗口叫什么时，给他几个关键词（他会从窗口标题里认出来）再调本工具。"
                "⚠️ 只列窗口、**不注入**；非 Windows 或探不到时**如实说探不到**（不假装找到）。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "keywords": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "关键词列表（会出现在窗口标题里的字，例如 ['TraeCode','合作社']）",
                    },
                    "limit": {"type": "integer", "description": "最多回几个候选（默认 20）"},
                },
                "required": ["keywords"],
            },
        )

    async def execute(self, **params) -> ToolResult:
        from .. import visual_launch

        kws = [str(k).strip() for k in (params.get("keywords") or []) if str(k).strip()]
        if not kws:
            return ToolResult("find_window", False,
                              "keywords 不能为空 —— 给我几个**会出现在那个窗口标题里**的字"
                              "（比如 agent 的名字、会话名、项目名）。")
        try:
            limit = int(params.get("limit") or 20)
        except Exception:  # noqa: BLE001
            limit = 20

        # 枚举要起子进程 ⇒ 必须丢线程（铁律：async 里不许同步阻塞）
        res = await asyncio.to_thread(visual_launch.list_windows, "")
        if not res.get("supported"):
            return ToolResult("find_window", False,
                              "%s\n⇒ 这台机器上用不了「窗口枚举」（消息式注入是 Windows 专有的）；"
                              "这个 agent 只能走**剪贴板半自动**：平台把内容放好剪贴板，用户点一下窗口再粘贴。"
                              % (res.get("detail") or ""))

        wins = res.get("windows") or []
        scored = []
        for w in wins:
            title = str(w.get("title") or "")
            low = title.lower()
            hits = [k for k in kws if k.lower() in low]
            if hits:
                scored.append({**w, "hits": hits, "score": round(len(hits) / len(kws), 3)})
        scored.sort(key=lambda x: (-x["score"], str(x.get("title"))))
        scored = scored[:max(1, limit)]

        payload = {
            "keywords": kws,
            "total_windows": len(wins),
            "matched": len(scored),
            "candidates": scored,
            "probe_note": res.get("detail") or "",
        }
        if not scored:
            return ToolResult(
                "find_window", False,
                "按关键词 %s **一个窗口都没匹配上**（这台机器上共枚举到 %d 个候选窗口）。\n"
                "⇒ 别硬猜：建议把关键词换宽一点（只留 agent 名字/项目名里最独特的两个字），或者让用户"
                "**把那个 agent 的窗口先打开/切到前台**再找我一次。\n```json\n%s\n```"
                % (kws, len(wins), json.dumps(payload, ensure_ascii=False, indent=2)))

        lines = ["匹配到 **%d** 个候选窗口（共枚举 %d 个）：" % (len(scored), len(wins))]
        for i, c in enumerate(scored, 1):
            lines.append("  %d. 标题=「%s」　进程pid=%s　窗口类=%s　命中=%s　匹配分=%.2f"
                         % (i, c.get("title"), c.get("pid"), c.get("class"),
                            "/".join(c["hits"]), c["score"]))
        lines.append("⇒ 让用户在界面上**点选是哪一个**（视觉激活工作台的「确认」那一步）；"
                     "确认后由界面按钮做一次**试投**并读回验证 —— 本工具只负责定位。")
        lines.append("```json\n%s\n```" % json.dumps(payload, ensure_ascii=False, indent=2))
        return ToolResult("find_window", True, "\n".join(lines))
