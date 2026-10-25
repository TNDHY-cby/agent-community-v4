# -*- coding: utf-8 -*-
"""「让它在自己的会话里说话」的**通用引导**（一处真相）。

老板 2026-10-25 定的两条口径合起来要求这个模块存在：
1. 「平台不用从它的输入框把字发给它，只需要对方能够在它的会话里面发消息。」
2. 「**平台也要有引导让对方加**。」

⇒ 平台不能只在文档里提一句：凡是**协议型 harness 但工具表里没有"会话内发言"工具**的，
对接中心就要能**当场给用户一段可复制的引导**，让用户把这段话交给外端 agent，它照着加一个工具。

这里只负责**生成文字**（Markdown）；调用方：
- `GET /api/docking/harness/{id}/session-post-guide?format=md`（界面上"复制这段要求给它"）；
- 注册引导 `register_prompt`（登记时就先提一句，指向同一口径）。
"""
from __future__ import annotations

#: 平台认的"会话内发言"工具名族（与 `routers/docking.py::_SESSION_POST_HINTS` 同一口径）
SESSION_POST_HINTS = ("session_post", "post_to_session", "session_message", "announce", "say",
                      "speak", "reply_in_session", "post_message", "send_message", "notify",
                      "broadcast")


def has_session_post(tool_names) -> bool:
    """工具表里有没有"往它自己会话里发一条"的工具。"""
    return any(any(h in str(n).lower() for h in SESSION_POST_HINTS) for n in (tool_names or []))


def build_md(*, harness_id: str = "", tools=None, platform_url: str = "") -> str:
    """生成给**外端 agent 看**的 Markdown 引导（用户整段复制过去即可）。"""
    names = [str(n) for n in (tools or []) if str(n).strip()]
    base = (platform_url or "").rstrip("/")
    lines = [
        "# 请给你自己加一个「会话内发言」的工具（`session_post`）",
        "",
        "我们（平台侧）把口径定死了：**平台不会去点你的输入框、也不会替你打字**，",
        "它只会调你暴露的**工具/接口**。所以「用户能在你自己的会话里看见你干活」这件事，",
        "只能由你提供一个能力出来，平台来调。",
        "",
        "## 你要加的工具",
        "",
        "| 项 | 要求 |",
        "|---|---|",
        "| 工具名 | `session_post`（平台按名字识别；名字里含 session_post / announce / say / "
        "post_message / send_message / notify 这类词也算） |",
        "| 入参 | `text`（string，必填）—— 要发到**你自己会话里**的那句话 |",
        "| 语义 | 这句话要**真的出现在你自己的会话/对话记录里**，用户在你那边就能看到；"
        "不是只把文本返回给调用方 |",
        "| 出参 | 把你收到的那句话**原样带回**（平台会用它比对一次性口令，确认你真接住了） |",
        "",
        # ★2026-10-25 老板真机第三轮：消息确实写进去了，但**写到了另一个会话**，用户在自己窗口里看不到。
        "## ⚠ 最关键的一条：写进「用户当前正在看的那个会话」",
        "",
        "平台刚在你身上实测过：`session_post` 返回 `ok / in_session / message_id` 全对，",
        "平台再用 `session_messages` 读回也确实找到了那句话 —— **但你写进的是另一个会话**",
        "（`session_id` 一直是同一个固定值），所以用户在**他自己打开的那个会话**里什么都看不到。",
        "",
        "请按这个来：",
        "",
        "1. 调用方传了 `session_id` ⇒ **就写进那一个**（不要另建、不要改成默认的）；",
        "2. 没传 ⇒ 写进**用户当前活跃/正在看的那个会话**（不是「最近创建」的那个、也不是内部默认会话）；",
        "3. 返回里**必须带回**：`session_id` + **`session_title`**（人类可读的会话标题，"
        "`title` / `conversation_title` 也认）—— 用户要靠这个核对「是不是我看到的那条会话」；",
        "4. `session_messages` 也要能把**那个会话**的消息读回来（`limit` 默认 20）。",
        "",
        f"## 你现在暴露的工具（平台刚读到的）",
        "",
        ("- " + "、".join("`%s`" % n for n in names)) if names else "- （平台还没读到你的工具表）",
        "",
        "## 你还要加第二个工具（**不加平台就没法判断真假**）：`session_messages`",
        "",
        "| 项 | 要求 |",
        "|---|---|",
        "| 工具名 | `session_messages`（名字里含 session_history / session_read / read_session / "
        "list_messages / history 也算） |",
        "| 入参 | `limit`（int，可选，默认 20）；多会话再加可选 `session_id` |",
        "| 出参 | 最近若干条消息的文本，**必须包含你刚用 session_post 写进去的那条** |",
        "| 为什么 | 平台发完会**立刻读回来找口令** —— 读得到才算真的落在会话里；读不到或没有这个工具，"
        "平台只会标「只能信你自报」，**不会假装验证过** |",
        "",
        "## 还要加一个（不然用户没法挑到自己那条会话）：sessions_list",
        "",
        "| 项 | 要求 |",
        "|---|---|",
        "| 工具名 | sessions_list（含 session_list / list_sessions / conversations / chats_list 也算） |",
        "| 入参 | limit（int，可选，默认 30） |",
        "| 出参 | 数组：[{session_id, title, updated_at}] —— **title 要用户看得懂**（就是他在界面上看到的标题） |",
        "| 为什么 | 平台要把会话列出来让用户**挑自己正在看的那个**，然后把 session_id 传给你的 session_post；",
        "  没有这个工具，平台只能往你的默认会话写，用户就永远看不到 |",
        "",
        "## 用户看不到你说话，多半是这三条",
        "",
        "1. **写到了另一个会话**：新建/默认到别的会话了，用户正看的那个自然什么都没有 —— "
        "请尊重调用方传进来的 `session_id`，并在返回里带回**真实的** `session_id`；",
        "2. **只返回没写**：返回 `{ok:true, in_session:true}` 但根本没落到会话里；",
        "3. **只进了草稿**：进了输入框/草稿但没真正提交。",
        "（第 2、3 条平台读回核对会当场抓出来。）",
        "",
        "## 平台会怎么调你（照着自测就行）",
        "",
        "1. 平台生成一次性口令（例如 `收到-A37E42`），实际发的话是：",
        "   `请回复：收到（口令：收到-A37E42，请原样带回）`",
        "2. 平台调 `session_post`，参数是 `{\"text\": \"<上面那句>\"}`；",
        "3. 你的会话里应当出现这一句；同时你的返回里要包含同一句话（含口令）；",
        "4. 平台用返回比对口令 ⇒ `verified`；看用的是哪个工具 ⇒ `in_session`。",
        "",
        "期望平台侧看到：",
        "",
        "```jsonc",
        "{",
        "  \"tool\": \"session_post\",",
        "  \"in_session\": true,      // ← 关键：是真的落进了你自己的会话",
        "  \"verified\": true,        // ← 口令对上了，说明这条消息你真接住了",
        "  \"reply\": \"请回复：收到（口令：收到-A37E42，请原样带回）\"",
        "}",
        "```",
        "",
        "## 做不到怎么办",
        "",
        "如果你的 MCP 服务**没有权限/没办法**往自己的会话里写消息，请**照实说**。",
        "平台会把这条标成「回复只回到平台，不会出现在它会话里」——**不算失败**，",
        "但用户就看不到你在会话里说话，所以这是你最值得补的一块。",
        "",
        "## 加完怎么验",
        "",
        "1. 重启你的 MCP 服务，确认 `tools/list` 里有 `session_post`；",
        f"2. 回一句「已加 session_post{('（harness_id=' + harness_id + '）') if harness_id else ''}」；",
        "3. 平台会立刻真发一句测试，把 `tool / in_session / verified` 三个字段贴出来；",
        "4. 三个都对上，就会在工作间派活时优先用你这条通道。",
    ]
    if base:
        lines += ["", f"（平台地址：{base}）"]
    return "\n".join(lines)


def build_json(*, harness_id: str = "", tools=None, platform_url: str = "") -> dict:
    """给界面用的结构化版（同一口径，别在 JS 里另写一份）。"""
    return {
        "harness_id": harness_id,
        "has_session_post": has_session_post(tools),
        "tools": [str(n) for n in (tools or []) if str(n).strip()],
        "tool_name": "session_post",
        "param": "text",
        "why": "平台不点它的输入框、不替它打字 ⇒ 要它在自己会话里说话，只能由它提供这个工具",
        "guide_md": build_md(harness_id=harness_id, tools=tools, platform_url=platform_url),
    }
