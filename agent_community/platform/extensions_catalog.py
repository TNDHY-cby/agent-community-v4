# -*- coding: utf-8 -*-
"""platform/extensions_catalog：「工作间完全展开」要露出什么 —— 一处真相。

老板 2026-11-01：「『工作间完全展开』（展开后露出更多'插件'接入了的功能，以及插件接口使用说明）」。

这里的"插件"有两层，**别混**（混了用户就看不懂）：

1. **插件（真·插件）**：`platform/routers/plugins.py` 那套 —— 一条可执行命令，
   平台只登记与调用，**不内置任何第三方代码**。清单走 `GET /api/plugins`（可能是空的）。
2. **平台内置的扩展点**：已经做好的页面与接口（看板/监控室/隔间/回执/中转/外部公司…）。
   它们不是插件，但属于"平台还能干什么" —— 用户展开后应该一目了然。

本模块只放**清单与说明文本**（数据），不写逻辑；界面与文档都从这一处取（规则 20）。
"""
from __future__ import annotations

#: 平台内置的扩展点（页面 + 对应接口）。`page` 为空表示只有接口、没有界面。
BUILTIN = [
    {"id": "docking", "name": "接入 Agent", "page": "/docking.html",
     "api": "GET /api/harness/list",
     "what": "把外面的 agent 接进来：注册 / 检测 / 生成具体模板 / 架桥"},
    {"id": "workshop", "name": "工作间", "page": "/workshop.html",
     "api": "GET /api/workshop/{ws_id}",
     "what": "定方向 / 选组长 / 名单（本页）"},
    {"id": "console", "name": "员工对接", "page": "/v5.html",
     "api": "GET /api/workshop/{ws_id}/sidebar",
     "what": "这一间的活：派活 / 产物 / 验收 / 讨论区（只干这一间）"},
    {"id": "ops", "name": "看板", "page": "/ops.html",
     "api": "GET /api/audit",
     "what": "工单审计与 Agent 在线情况"},
    {"id": "monitor", "name": "监控室", "page": "/monitor.html",
     "api": "GET /api/monitor/health",
     "what": "画面墙（只读快照）"},
    {"id": "booths", "name": "隔间", "page": "/booths.html",
     "api": "GET /api/workshop/{ws_id}/members-status",
     "what": "成员隔间与桥的落点"},
    {"id": "handover", "name": "回执", "page": "/handover.html",
     "api": "POST /api/harness/task-result",
     "what": "交付回执台（无证据不回执）"},
    {"id": "hub", "name": "流程说明", "page": "/hub.html",
     "api": "GET /api/protocol-brief",
     "what": "使用说明书 · 全流程 · 接口清单（只讲不做）"},
    {"id": "mind", "name": "心智", "page": "/mind.html",
     "api": "GET /api/mind",
     "what": "工作间的心智根（宪章 / 索引 / 记忆 / 日志）"},
    {"id": "peers", "name": "外部公司", "page": "",
     "api": "POST /api/peers",
     "what": "登记对端公司、派活给别的合作社（点对点）"},
    {"id": "relay", "name": "中转", "page": "",
     "api": "POST /api/relay/send",
     "what": "经中转 Hub 跨网络送内容 / 派活（终端只出站）"},
    {"id": "evidence", "name": "证据链", "page": "",
     "api": "GET /api/workshop/{ws_id}/tasks",
     "what": "任务证据四步表与验收闸门"},
]

#: 插件接口使用说明（给用户照着抄的，不是给开发者看的源码说明）。
PLUGIN_API = {
    "what": "插件 = 一条可执行命令。平台只负责登记与调用，不内置任何第三方代码。",
    "list": "GET /api/plugins",
    "add": "POST /api/plugins    body: {\"name\":\"插件名\",\"cmd\":\"要执行的命令\",\"desc\":\"说明\"}",
    "delete": "DELETE /api/plugins/{name}",
    "invoke": "POST /api/plugins/{name}/invoke    body: {\"args\":\"传给命令的参数\"}",
    "note": ("调用有超时与输出上限；命令由**你自己**提供，平台不会替你去装任何东西。"
             "装不上就别装 —— 平台能力不依赖它。"),
}


def catalog(plugins: dict | None = None) -> dict:
    """给「工作间完全展开」的一整块数据：插件 + 内置扩展点 + 接口说明。"""
    return {
        "success": True,
        "plugins": plugins or {},
        "plugin_count": len(plugins or {}),
        "plugin_api": dict(PLUGIN_API),
        "builtin": [dict(x) for x in BUILTIN],
        "note": ("「插件」与「内置扩展点」是两件事：插件由你自己登记（可能为空）；"
                 "内置扩展点是平台已经做好的页面与接口，展开就能看到并点进去。"),
    }
