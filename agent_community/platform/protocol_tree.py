# -*- coding: utf-8 -*-
"""对接中心的**注册链路树**（老板 2026-10-25 口述流程的"数据那一半"）。

老板原话（顺序就是他要的界面顺序）：

> 1. 询问协议（若已知可手动跳过）
> 2. 选择协议（选择协议后，才进行真注册，选择不同的注册给出不同的适配的功能卡片，
>    **不要全乱放一起，做成树状链路**）
> 3. 复制对应注册引导（给 agent 看的，.md 格式就行）——这里留给（脚本注册之类的）
> 4. 检测
> 5. 平台与 agent 对话框（人工搬运）……

所以这个模块只提供**一处真相**：每一步做什么、每种协议有哪些"功能卡片"、
必填字段是哪些、测怎么测、下一步去哪。界面照着渲染成树即可，**不要在 JS 里再抄一份**。
"""
from __future__ import annotations

#: 五步（顺序不许颠倒；第 1 步可跳过 —— 老板原话"若已知可手动跳过"）
STEPS = [
    {"id": 1, "title": "先问协议", "skippable": True,
     "what": "平台先帮你猜：它是能被命令拉起（ACP）、盯目录（file_poll）、有常驻 HTTP（http_api/http）、"
             "是个 MCP 服务（mcp），还是只能人工粘贴（clipboard）。",
     "how": "不知道就点「帮我探一次」；已经知道就直接跳过（skippable=true）"},
    {"id": 2, "title": "选协议 → 真注册", "skippable": False,
     "what": "选定协议后**才**发起真注册；不同协议给不同的功能卡片，卡片按树挂在该协议下面，不混在一起。",
     "how": "选一条 ⇒ 只显示这条协议的卡片与必填字段 ⇒ 填完点注册"},
    {"id": 3, "title": "复制注册引导（给外端 agent 看的 .md）", "skippable": False,
     "what": "把该协议专属的登记话术**以 Markdown 复制**给外端 agent，让它自己登记（平台不替它填）。"
             "这一页也留给「脚本注册」那类：桌面型 agent 在这里配视觉激活。",
     "how": "复制 ⇒ 粘给外端 agent；桌面型再做「框选输入框/配脚本」"},
    {"id": 4, "title": "检测", "skippable": False,
     "what": "注册完当场验一次（不靠猜）：协议型真调接口，桌面型靠视觉自检/人工确认。",
     "how": "每行一个「测一次」⇒ 结果显示在这行；不可用的行灰掉并写原因"},
    {"id": 5, "title": "跟它对话（人工搬运）", "skippable": False,
     "what": "平台把话放到一个可复制的框里，人粘给对方；对方的回答由人粘回来。"
             "这是「半自动」：平台不假装能核实对方收到了。",
     "how": "复制平台的话 ⇒ 粘给对方 ⇒ 把对方的话粘回平台 ⇒ 点「我确认它回了」"},
]

#: 每种协议一张卡（`cards` 就是界面要挂在该协议下面的"功能卡片"，别平铺）
PROTOCOLS = [
    {
        "id": "mcp", "label": "MCP（对方是 MCP 服务端）",
        "can_be_woken": True, "needs_bridge": False, "needs_session": False,
        "required": ["harness_id", "api_base_url", "ai.model_name"],
        "cards": [
            {"action": "test_now", "title": "直推（无桥）", "detail": "平台直接调它的 tools/list 与 tools/call"},
            {"action": "test_now", "title": "工具表", "detail": "注册时自动拉一次；之后「测一次」也会刷新"},
            {"action": "add_member", "title": "不需要新建会话", "detail": "加进工作间就能派活"},
            {"action": "edit_fields", "title": "会话内发言（要它自己提供工具）",
             "detail": "平台不点它的输入框；要它**在自己的会话里**说一句，它得暴露 session_post(text) 这类工具；没有就只回到平台"},
        ],
        "test_how": "真调 initialize + tools/list（`POST …/test-now`）",
        "next_step": "在某个工作间点「加成员」把它选进来，就能被派活",
    },
    {
        "id": "http_api", "label": "HTTP 服务（对方有常驻 HTTP 地址）",
        "can_be_woken": True, "needs_bridge": False, "needs_session": False,
        "required": ["harness_id", "api_base_url", "ai.model_name"],
        "cards": [
            {"action": "copy_guide", "title": "直推", "detail": "平台按 api_base_url 把任务 POST 过去"},
            {"action": "edit_fields", "title": "无回调也要说清", "detail": "对方不回执时，任务停在「已送出未核实」"},
        ],
        "test_how": "真 GET 一次 api_base_url（`POST …/test-now`）",
        "next_step": "加进工作间；要回执就让对方给回调地址",
    },
    {
        "id": "acp", "label": "ACP（平台拉起子进程）",
        "can_be_woken": True, "needs_bridge": False, "needs_session": True,
        "required": ["harness_id", "acp_command", "ai.model_name"],
        "cards": [
            {"action": "verify", "title": "平台真拉起", "detail": "按 acp_command 起进程，走 initialize → session/new → prompt"},
            {"action": "edit_fields", "title": "要一个工作目录", "detail": "acp_cwd：它干活落在哪"},
        ],
        "test_how": "走 `/verify`（会真拉起子进程握手）",
        "next_step": "加进工作间；会话由平台按 ACP 协议建",
    },
    {
        "id": "file_poll", "label": "文件轮询（桥搬件）",
        "can_be_woken": False, "needs_bridge": True, "needs_session": True,
        "required": ["harness_id", "wakeup_dir", "ai.model_name"],
        "cards": [
            {"action": "open_bridge", "title": "桥搬件", "detail": "平台落盘 → 桥把任务搬进 wakeup_dir → 它自己来取"},
            {"action": "open_script_editor", "title": "叫不醒", "detail": "它不自己醒的话，只能靠人/视觉激活"},
        ],
        "test_how": "没有可调接口：用验证间「验一次」看桥有没有把件取走",
        "next_step": "先把桥跑起来（生成桥 → 起桥 → 验一次）",
    },
    {
        "id": "clipboard", "label": "人工粘贴（只能人搬运）",
        "can_be_woken": False, "needs_bridge": True, "needs_session": True,
        "required": ["harness_id", "ai.model_name"],
        "cards": [
            {"action": "manual_chat", "title": "半自动", "detail": "平台把话放剪贴板 + 它能点的位置，人点一下 Ctrl+V"},
            {"action": "open_script_editor", "title": "视觉激活", "detail": "桌面型：框选输入框一次，之后平台自己点+粘+发"},
        ],
        "test_how": "没有可调接口：走「视觉自检 / 试投一句」",
        "next_step": "先框选一次输入框；再用「试投一句」看它回不回话",
    },
    {
        "id": "http", "label": "HTTP 兜底（只落盘、不叫醒）",
        "can_be_woken": False, "needs_bridge": True, "needs_session": True,
        "required": ["harness_id", "ai.model_name"],
        "cards": [
            {"action": "copy_guide", "title": "只落盘", "detail": "平台只把任务写进它的目录，等它自己来取"},
            {"action": "open_script_editor", "title": "不假装能叫醒", "detail": "界面会显示「平台叫不醒它」"},
        ],
        "test_how": "没有可调接口：看它有没有来取件",
        "next_step": "给它配一套视觉脚本，或让人提醒它",
    },
]

BY_ID = {p["id"]: p for p in PROTOCOLS}


def build() -> dict:
    """给界面用的整棵树（一步真相：界面别在 JS 里再抄一份字段）。"""
    return {
        "steps": STEPS,
        "protocols": PROTOCOLS,
        "guide": {"endpoint": "/api/harness/register-prompt",
                  "query": {"wakeup_method": "<协议 id>", "format": "md"},
                  "note": "把返回的 Markdown 直接复制给外端 agent"},
        "test": {"endpoint": "/api/docking/harness/{harness_id}/test-now", "method": "POST"},
        "register": {"endpoint": "/api/harness/register", "method": "POST"},
        "next": {"add_member": "/api/workshop/{ws_id}/members", "method": "POST"},
    }
