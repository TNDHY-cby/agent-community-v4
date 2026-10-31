# -*- coding: utf-8 -*-
"""`platform/mcp_client_config`：**把平台当 MCP 服务端挂到别人的 agent 上**的配置生成（一处真相）。

## 为什么要有它（老板 2026-10-25：「MCP 不用折腾了，直接按 MCP 协议用」）

MCP 的方向是「**应用来调工具**」：平台**不能**反向让对方的模型开口。
所以正确用法是——**平台自己当 MCP 服务端**（`agent_community/mcp_server.py`，stdio，零第三方依赖），
对方那个 agent 把它当 MCP 服务器挂上；用户在自己那边说话时，agent 就**在自己的会话里回话**，
顺手还能调平台的工具（看状态/看任务/派讨论/交回执）。

## 这里只做一件事：**生成可以照抄的配置**

- 解释器用**当前跑平台的这个解释器**（`sys.executable`）—— 换机器也对；
- 包路径用**本仓库根目录**（`PYTHONPATH`），不依赖 cwd；
- 平台地址用 `platform_address.base_url_from_env()`（认 `AC4_BASE_URL`）；
- **令牌绝不写进配置**：需要时写环境变量名与来源提示，值由用户自己填（`AC4_TOKEN`）。

三份产物：
| key | 给谁 |
|---|---|
| `cherrystudio` | Cherry Studio / 多数桌面 MCP 客户端（`mcpServers` 形状） |
| `claude` | Claude Code / Claude Desktop（`claude mcp add-json` 可吃） |
| `generic` | 其它客户端的最小字段（command/args/env） |
"""
from __future__ import annotations

import os
import sys

#: 平台自带的 MCP 服务端模块（stdio、零第三方依赖，见该文件顶部说明）
SERVER_MODULE = "agent_community.mcp_server"
#: 平台对外的工具名（仅用于给人看"挂上以后能干什么"，**不在这里定义工具**）
TOOL_HINTS = [
    ("status", "平台健康与版本"),
    ("list_harnesses", "已接入的 harness"),
    ("list_agents", "登记过的 agent"),
    ("list_tasks", "任务列表"),
    ("get_task", "某个任务的详情与时间线"),
    ("create_workshop", "建一个工作间"),
    ("workshop_discuss", "在工作间里发起讨论"),
    ("workshop_review", "复核产出"),
    ("workshop_continue", "接着上一轮继续"),
    ("workshop_interject", "插一句话/插入人工意见"),
    ("submit_task_result", "把这次干的活回报给平台"),
    ("report_progress", "干活时每步报一次（一句话）—— 用户在「工作过程」里直接看到"),
    ("pending_session_notes", "取「平台请你自己发到你会话里」的话（由它自己发，平台不代发）"),
    ("report_session_sent", "回报「这条我已经在自己的会话里发了」（它自己的自报）"),
]


def _repo_root() -> str:
    """本仓库根目录（`agent_community` 包的父目录）——从**本文件位置**算，不写死盘符。"""
    here = os.path.dirname(os.path.abspath(__file__))          # …/agent_community/platform
    return os.path.dirname(os.path.dirname(here))


def build(*, base_url: str = "", token_env: str = "AC4_TOKEN") -> dict:
    """生成"把本平台挂成 MCP 服务端"的配置（**两种传输** + 一份人看的说明）。

    为什么要两种（2026-10-25）：
      · **HTTP**（`<平台地址>/mcp`）：桌面类应用（只能填 URL 的那种）用这个 ——
        它一来调，平台就**留痕**（`GET /api/mcp/calls`），这是"它真来过"的唯一硬证据；
      · **stdio**（`command`+`args`）：命令行类客户端（Claude Code / Cursor）用这个，
        平台起不了它的子进程没关系，客户端自己起。
    两种走的是**同一份工具实现**（`agent_community/mcp_server.py`），别以为是两套东西。
    """
    from ..platform_address import base_url_from_env

    base = (base_url or base_url_from_env() or "").rstrip("/")
    root = _repo_root()
    env = {"PYTHONPATH": root, "AC4_BASE_URL": base}
    entry = {
        "command": sys.executable,
        "args": ["-m", SERVER_MODULE],
        "env": env,
    }
    http_entry = {"url": base + "/mcp"}                 # 桌面类：只要一个地址
    return {
        "ok": True,
        "server": {"name": "external-agent-community", "module": SERVER_MODULE,
                   "interpreter": sys.executable, "repo_root": root, "base_url": base,
                   "http_url": base + "/mcp"},
        # ★桌面类应用（Cherry Studio 这类）用 **http** 这一份；命令行类客户端用 stdio 那一份
        "http": {"mcpServers": {"external-agent-community": dict(http_entry)}},
        "cherrystudio": {"mcpServers": {"external-agent-community": dict(http_entry)}},
        "stdio": {"mcpServers": {"external-agent-community": dict(entry)}},
        "claude": {"mcpServers": {"external-agent-community": dict(entry)}},
        "generic": dict(entry),
        "token_env": token_env,
        "token_note": ("平台设了访问令牌时，HTTP 写法要带令牌头（`Authorization: Bearer <令牌>`，"
                       "客户端的 MCP 设置里填 headers）；stdio 写法把它放进环境变量 %s。"
                       "**别把令牌写进配置文件后到处传**" % token_env),
        "tools": [{"name": n, "what": w} for n, w in TOOL_HINTS],
        "hint": ("挂上之后：在那个 agent 里正常说话，它就会**调平台的工具**——"
                 "平台这边 `GET /api/mcp/calls` 会记下它来过（客户端名/工具/成败）；"
                 "它一次都没来，那里就是空的。"),
    }


def build_md(*, base_url: str = "", token_env: str = "AC4_TOKEN") -> str:
    """给人照着贴的 Markdown 版（界面「复制」按钮直接塞这段）。**两种传输都给**，按客户端挑一份。"""
    import json as _json

    d = build(base_url=base_url, token_env=token_env)
    http_cfg = _json.dumps(d["http"], ensure_ascii=False, indent=2)
    stdio_cfg = _json.dumps(d["stdio"], ensure_ascii=False, indent=2)
    tools = "\n".join("| `%s` | %s |" % (t["name"], t["what"]) for t in d["tools"])
    return "\n".join([
        "# 把本平台挂成 MCP 服务器（对方 agent 直接按 MCP 协议用）",
        "",
        "> 为什么要这么做：MCP 的方向是「**应用来调工具**」。平台不能反向让对方的模型开口，",
        "> 所以正确用法是**平台当服务端**、对方的 agent 当客户端挂上它。",
        "> 挂上之后，你在那个 agent 里正常说话 —— 它需要平台数据时会**调**这些工具；",
        "> 平台这边 `GET /api/mcp/calls` 会记下它来过（那才是「它接上了」的硬证据）。",
        "",
        "## 1-A. 桌面类应用（只能填地址的：填这个）",
        "",
        "```json",
        http_cfg,
        "```",
        "",
        "地址就是 `<平台地址>/mcp`（现在是 `%s`）。" % d["server"]["http_url"],
        "",
        "## 1-B. 命令行类客户端（Claude Code / Cursor 这类：填这个）",
        "",
        "```json",
        stdio_cfg,
        "```",
        "",
        "字段说明（**没有一个是这座机器专属的**）：",
        "| 字段 | 值从哪来 | 说明 |",
        "|---|---|---|",
        "| `command` | 现在跑平台的那个解释器 | %s |" % d["server"]["interpreter"],
        "| `args` | 固定 | `-m %s` |" % SERVER_MODULE,
        "| `env.PYTHONPATH` | 本仓库根目录 | %s |" % d["server"]["repo_root"],
        "| `env.AC4_BASE_URL` | 平台地址（认 `AC4_BASE_URL`） | %s |" % d["server"]["base_url"],
        "",
        "## 2. 挂上以后它能干什么",
        "",
        "| 工具 | 干什么 |",
        "|---|---|",
        tools,
        "",
        "## 3. 怎么自己验证挂对了（**别听它自己说，看平台的留痕**）",
        "",
        "1. 在客户端里看这个 MCP 服务器的工具数 —— 应该是 **%d 个**；" % len(d["tools"]),
        "2. 让它在对话里用一次，比如「看一下合作社的状态」；",
        "3. 然后看平台：**`GET /api/mcp/calls`** —— 里面应该出现**那个客户端的名字**和它调的工具。",
        "   空的就是「它一次都没来过」——这时候任何「通了」都是平台自己在说话。",
        "4. 命令行里也能查：`python tools/protocol_call_check.py`（mcp 一行真跑 initialize + tools/list）。",
        "",
        "## 4. 令牌（可选）",
        "",
        d["token_note"],
        "",
    ])
