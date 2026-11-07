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
#:
#: ★2026-11-06（老板「做多协议选中适合的功能」；设计 `V132`）—— 每一项额外声明**能力事实**，
#: 这四组字段是"多选组合能不能闭环"的**唯一真源**（界面与 `protocol_plan` 都读这里，别在两处再写）：
#:   · `role`             —— 在协作里扮演什么（task=能派活 / tools=只能被调 / session=平台拉起会话 /
#:                            bridge=它自己来取件 / manual=只能人搬运）
#:   · `can_dispatch`     —— 平台能不能把活儿**派给它**
#:   · `can_receive_result` —— 它交回的结果平台收不收得到
#:   · `can_call_our_tools` —— 它能不能反过来调平台的工具（MCP 就是这一条）
#:   · `can_read_session` —— 平台能不能读它的会话（"tool"＝要它自己提供读会话工具）
#:   · `transport`        —— 映射到 `TransportType` 的哪个值；**None ＝ 传输层表达不了**（那就只记在
#:                            `protocols` 字段里，不许硬塞一个语义不同的值）
PROTOCOLS = [
    {
        "id": "a2a", "label": "A2A（对方是 A2A agent，有 Agent Card）",
        # ★2026-11-01 新增（老板要在接入向导里选 A2A）：
        #   A2A agent 本质是 **HTTP 服务**，所以能探活/读卡；但它交付任务用的是 A2A 的
        #   `tasks/send`（JSON-RPC 信封），**不是**普通 HTTP POST —— 这一点必须写清，
        #   否则用户以为"注册成功 = 能派活"，实际派出去的格式它不认。
        "can_be_woken": True, "needs_bridge": False, "needs_session": False,
        "role": "task", "can_dispatch": True,
        "can_receive_result": "yes", "can_call_our_tools": False,
        "can_read_session": "tool", "transport": "a2a",
        "required": ["harness_id", "api_base_url", "ai.model_name"],
        "cards": [
            {"action": "test_now", "title": "读它的 Agent Card（现在就支持）",
             "detail": "三条路径都试：`/.well-known/agent-card.json`（v1.0）、`/.well-known/agent.json`（v0.2）、"
                       "以及挂在端点基址上的 `<基址>/agent-card.json`；读到就说明它是 A2A 且活着；"
                       "读卡不需要令牌、不花 AI 费用"},
            {"action": "copy_guide", "title": "派活按 A2A 信封发（平台已支持）",
             "detail": "平台按 JSON-RPC `tasks/send`（`message.parts[].text`）派活、`tasks/get` 回查；"
                       "对端只认 v1.0 方法名（`SendMessage`）时平台会自动改发 —— 不需要它做兼容"},
            {"action": "edit_fields", "title": "回执：平台会回查，不靠它自觉",
             "detail": "平台按 `tasks/get` 轮询到终态就写回任务账本（记成 `delivered`，**不等于验收**）；"
                       "它也可以主动 POST 平台的 `/api/harness/task-result` —— 两条路谁先到算谁"},
            {"action": "add_member", "title": "不需要新建会话", "detail": "加进工作间就能被派活"},
        ],
        "test_how": "读 Agent Card（`POST …/test-now`；三条路径都试）+ **真发一条探针任务**（对端会话里能看到）",
        "next_step": ("加进工作间就能派活；结果由平台 	asks/get 回查写回账本，再由人验收。"
                        "完整口径（接入 / 派活 / 回话 / 会话轮换 / 失败语义）见 "
                        "design-docs/V125_A2A接入与派活_使用方法与实现约束_2026-11-02.md"),
    },
    {
        "id": "mcp", "label": "MCP（对方是 MCP 服务端）",
        "can_be_woken": True, "needs_bridge": False, "needs_session": False,
        "role": "tools", "can_dispatch": False,
        "can_receive_result": "yes", "can_call_our_tools": True,
        "can_read_session": "tool", "transport": "mcp",
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
        "role": "task", "can_dispatch": True,
        "can_receive_result": "partial", "can_call_our_tools": False,
        "can_read_session": "no", "transport": "http",
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
        "role": "session", "can_dispatch": True,
        "can_receive_result": "yes", "can_call_our_tools": False,
        "can_read_session": "yes", "transport": None,
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
        "role": "bridge", "can_dispatch": False,
        "can_receive_result": "partial", "can_call_our_tools": False,
        "can_read_session": "no", "transport": None,
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
        "role": "manual", "can_dispatch": False,
        "can_receive_result": "no", "can_call_our_tools": False,
        "can_read_session": "no", "transport": None,
        # ★2026-11-06（老板：「clipboard 是不是可以用视觉脚本，记得说明」）：
        #   `can_be_woken=False` 说的是**开箱事实**（没有脚本时平台叫不醒桌面型 agent）；
        #   一旦它的档案里存了**视觉脚本**（`activation_script`：窗口标题/关键词 + 一句要送的话），
        #   平台就能**自动把话送进它的窗口**（cdp → 原生 → UIA → 半自动，能核实就核实）。
        #   ⚠️ 但"话送进去了" ≠ "它执行了"：结果平台核不到，仍需人看产物或它自己回报。
        "visual_script": True,
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
        "role": "bridge", "can_dispatch": False,
        "can_receive_result": "no", "can_call_our_tools": False,
        "can_read_session": "no", "transport": "http",
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

#: 向导层协议的合法 id（多选、`protocols` 字段、`protocol-plan` 校验**都读这一份**）
PROTOCOL_IDS = tuple(p["id"] for p in PROTOCOLS)
#: 向导 id → `TransportType` 值；`None` ＝ 传输层表达不了（只记 `protocols`，不硬塞）
TRANSPORT_BY_ID = {p["id"]: p.get("transport") for p in PROTOCOLS}
#: 能派活的协议，按**平台优先尝试**的顺序（`protocol_plan` 与派活触达共用这一份）
DISPATCH_ORDER = tuple(p["id"] for p in PROTOCOLS if p.get("can_dispatch"))
#: 角色的人话（界面/计划里直接显示，别在 JS 里另写一份）
ROLE_LABELS = {
    "task": "能派活（它有任务语义）",
    "tools": "只能被调（它是工具面，平台不能给它派活）",
    "session": "能派活（平台拉起会话，按轮次推进）",
    "bridge": "只能落盘等它来取（不能叫醒）",
    "manual": "只能人搬运（半自动）",
}


#: 视觉脚本能干什么、不能干什么（**一处真相**：界面/计划/文档都读这里，别各写一句）
VISION = {
    "can": ("配了**视觉脚本**（窗口标题或关键词 + 一句要送的话）后，平台能**自动把话送进它的窗口**："
            "按 `cdp → 原生 → UIA → 半自动` 四通道选，能核实投递就核实，核不了如实说未核实。"),
    "cannot": ("只能证明「**话送进去了**」，**不能证明它执行了 / 干完了**。"
               "结果要靠人看产物、或它自己给回执；平台不会把「投递成功」记成「任务完成」。"),
    "where": ("对接中心「脚本激活」工作台；一键激活 `POST /api/docking/harness/{id}/activate`；"
              "派活到视觉档时也会走同一条投递（`dispatch.activate` 的 `c_visual` 分支）。"),
    "fields": ["activation_script.text", "activation_script.title（最精确）",
               "activation_script.keywords", "activation_script.verified（没自证过就是 false，不假装）"],
    "which_protocols": ["clipboard（主要）", "http 兜底 / file_poll（桌面型也可配，能力归视觉这一档）"],
}


def build() -> dict:
    """给界面用的整棵树（一步真相：界面别在 JS 里再抄一份字段）。

    `meta` 给界面判断"多选时能干什么"用（V132）：`dispatch_order` / `role_labels` /
    `protocol_ids` —— 与 `protocol-plan` 端点**同一份真源**。
    """
    return {
        "steps": STEPS,
        "protocols": PROTOCOLS,
        "protocol_ids": list(PROTOCOL_IDS),
        "meta": {"dispatch_order": list(DISPATCH_ORDER),
                 "role_labels": dict(ROLE_LABELS),
                 "note": "多选后调 /api/docking/protocol-plan 拿『这套组合能干什么』；别在前端自己拼 register 载荷"},
        "vision": dict(VISION),
        "guide": {"endpoint": "/api/harness/register-prompt",
                  "query": {"wakeup_method": "<协议 id>", "format": "md"},
                  "note": "把返回的 Markdown 直接复制给外端 agent"},
        "test": {"endpoint": "/api/docking/harness/{harness_id}/test-now", "method": "POST"},
        "register": {"endpoint": "/api/harness/register", "method": "POST"},
        "plan": {"endpoint": "/api/docking/protocol-plan", "method": "POST"},
        "next": {"add_member": "/api/workshop/{ws_id}/members", "method": "POST"},
    }
