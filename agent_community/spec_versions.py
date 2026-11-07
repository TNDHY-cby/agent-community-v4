# -*- coding: utf-8 -*-
"""协议**规范版本**的唯一真源（一处真相）。

## 为什么单独一个模块

MCP 的"支持到哪一版"同时被四处描述：stdio 服务端（`mcp_server`）、平台做 MCP 客户端时
（`platform/mcp_client`）、对外协议目录（`GET /api/protocols` 的 `mcp` 项），以及文档。
过去各写各的，改一处漏三处。

老板 2026-11-06 拍板：**MCP `2026-07-28` 不跟进**（它是减法版：删 `initialize` / `Mcp-Session-Id` /
`ping` / `logging/setLevel`，跟进＝一次传输层迁移），但**口径要如实写"本平台只支持到 `2025-06-18`"**——
不许让用户按"支持最新版"去理解。

## 纪律

- 本模块**零依赖、零导入**：任何 `platform.*` 或 FastAPI 都不能被拽进来
  （`mcp_server` 走 stdout，一拽进 FastAPI 就会污染协议流）；
- 版本列表**按新 → 旧排列**，首个即服务器默认（最高）版本；
- 遇到不认识的更高版本：协商回自己支持的最高版，**并在响应里给出实际版本**，不假装支持。
"""

from __future__ import annotations

#: 本平台支持的 MCP 协议版本（新 → 旧）；首个＝默认/最高
MCP_SUPPORTED = ["2025-06-18", "2025-03-26", "2024-11-05"]
#: 最高支持版本
MCP_SUPPORTED_MAX = MCP_SUPPORTED[0]
#: **明确不跟进**的更高版本（如实登记，别让"没做"被读成"做了"）
MCP_NOT_FOLLOWED = ["2026-07-28"]


def mcp_version_note() -> str:
    """一句人话：支持到哪一版、哪一版没跟进、遇到更新版会怎样。"""
    return ("MCP 协议：本平台支持到 **%s**（%s）；更高版本（%s）**未跟进** —— "
            "客户端若要求新版，`initialize` 会协商回 %s，并在响应里给出实际版本，不会假装支持。"
            % (MCP_SUPPORTED_MAX, " / ".join(MCP_SUPPORTED),
               " / ".join(MCP_NOT_FOLLOWED), MCP_SUPPORTED_MAX))
