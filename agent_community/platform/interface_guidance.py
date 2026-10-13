# -*- coding: utf-8 -*-
"""platform/interface_guidance：**按「接口 / 叫醒方式」给各自的引导**（一处真相）。

老板 2026-10-13 拍板原话：「**不同接口不同引导**」。

## 为什么要有这个模块

接入的五步（注册 → 配叫醒 → 起桥 → 自检 → 持久跑桥）对**不同叫醒方式**根本不是同一套流程：

| 叫醒方式 | 第 2 步配叫醒 | 第 3 步起桥 | 第 4 步自检 | 第 5 步持久跑桥 | 活怎么到它面前 |
|---|---|---|---|---|---|
| `acp` | **必需**（命令） | 必需 | 必需 | **必需** | 桥用命令拉起会话 |
| `file_poll` | 必需（目录） | 必需 | 必需 | **必需** | 桥往目录投件 |
| `http_api` | **无此步** | **无此步** | 验直推地址 | **无此步** | 平台 POST 到它自己的 HTTP |
| `http` | 无（兜底） | 必需 | 必需 | 必需 | **先落盘，不叫醒**（没人轮询就永远躺着） |
| `clipboard` | 无（人工） | 理论上无需 | 关桥 | 关桥 | **落盘等人粘贴** |
| `mcp` | 无 | 无 | 验 MCP 连通 | 无 | 平台作为 MCP 客户端直调 |

原来的问题（V44 #15）：**所有接口都拿到同一套话术**，于是出现
「`http_api` 的 harness 走完了桥的 3/4/5 步」「第 4 步验的是 pending_poll、第 5 步在跑 file_poll，
而"已验证模板"被读成"这条链路通了"」这类误读。

## 本模块的契约

- **唯一真相**：每个 `wakeup_method` 的 `steps` / `bridge` / `inbox` / `pickup` / `platform_can`
  都只在这里写一遍；后端响应、界面话术、文档都从它取（规则 20：不留两份口径）。
- **只说平台真知道的**：不知道的写成 `不适用 / 未验证`，不许编（准则⑤）。
- **未知取值**照旧给"我们不知道"的中性答复，不硬猜。
"""
from __future__ import annotations

from typing import Any

# ── 步骤名（五步的规范名，各处引用同一份，别写字面量） ──
STEP_REGISTER = "register"
STEP_WAKE = "wake"
STEP_BRIDGE = "bridge"
STEP_SELFCHECK = "selfcheck"
STEP_PERSIST = "persist"

STEP_LABEL = {
    STEP_REGISTER: "第 1 步 注册",
    STEP_WAKE: "第 2 步 配叫醒",
    STEP_BRIDGE: "第 3 步 起桥",
    STEP_SELFCHECK: "第 4 步 自检",
    STEP_PERSIST: "第 5 步 持久跑桥",
}

# 每个步骤的三态
NEEDED = "需要"
NOT_APPLICABLE = "不适用"
OPTIONAL = "可选"

#: 活怎么到 agent 面前
INBOX = {
    "inbox": "落在这个 harness 的**文件邮箱**里（它的脚本自己去读）",
    "pending_queue": "进**待取队列**，由它的桥轮询领走",
    "direct_push": "**平台直接 POST** 到它自己的 HTTP 地址（不过桥、不落盘）",
    "manual": "**平台落盘 + 等人粘贴**（平台不会自动叫醒它）",
    "nowhere": "**平台落盘后无人取**（没人轮询、也没桥）—— 会被积压",
    "mcp_call": "**平台作为 MCP 客户端直接调用**它的工具",
}


def _s(*, wake: str = NOT_APPLICABLE, bridge: str = NOT_APPLICABLE,
       selfcheck: str = NEEDED, persist: str = NOT_APPLICABLE) -> dict:
    """拼一份"哪几步要做"的表（第 1 步注册对所有接口都一样，恒定）。"""
    return {
        STEP_REGISTER: NEEDED,
        STEP_WAKE: wake,
        STEP_BRIDGE: bridge,
        STEP_SELFCHECK: selfcheck,
        STEP_PERSIST: persist,
    }


#: ★按叫醒方式的引导（**一处真相**）
BY_WAKEUP: dict[str, dict] = {
    "acp": {
        "label": "ACP（命令行拉起）",
        "tier_hint": "A 档 · 能被一条命令拉起",
        "steps": _s(wake=NEEDED, bridge=NEEDED, selfcheck=NEEDED, persist=NEEDED),
        "bridge_kind": "cli_acp",
        "inbox": INBOX["pending_queue"],
        "pickup": "桥用你给的那条命令拉起新会话，把任务发进会话、把结果回报给平台。",
        "persist_how": "把桥命令常驻跑起来（终端关了它就停了；建议加开机启动/计划任务）。",
        "wake_answer": "填你自测出来的那条**能拉起新实例的命令**（命令里能带参数就带）。"
                       "⚠️ Windows 路径请用**正斜杠**（平台按 POSIX 规则切词，反斜杠会被吃掉）。",
        "platform_can": ["平台能用这条命令自动叫醒它", "第 4 步自检会真的起一只桥跑一遍"],
        "platform_cannot": ["平台不会替它写这条命令（只有它自己知道）",
                            "第 4 步只验桥配置；agent 会不会回话要真派一次活才知道"],
    },
    "file_poll": {
        "label": "文件轮询（盯目录）",
        "tier_hint": "B 档 · 盯着一个文件夹",
        "steps": _s(wake=NEEDED, bridge=NEEDED, selfcheck=NEEDED, persist=NEEDED),
        "bridge_kind": "file_poll",
        "inbox": INBOX["inbox"],
        "pickup": "平台把任务写进那个目录；它自己的脚本读到就干活，再把结果回报平台。",
        "persist_how": "把桥常驻跑起来（桥负责收回报）；同时确认它自己的目录监听脚本也在跑。",
        "wake_answer": "填它**真正在盯的那个目录**（绝对路径）。",
        "platform_can": ["平台能把任务写进那个目录", "第 4 步自检走完整文件邮箱链路"],
        "platform_cannot": ["平台不知道目录里到底有没有人在读 —— 没人读就等于没接上"],
    },
    "http_api": {
        "label": "常驻 HTTP 接口（平台直推）",
        "tier_hint": "C 档 · 自带一个 HTTP 地址",
        "steps": _s(wake=NOT_APPLICABLE, bridge=NOT_APPLICABLE,
                   selfcheck=NEEDED, persist=NOT_APPLICABLE),
        "bridge_kind": None,
        "inbox": INBOX["direct_push"],
        "pickup": "**不需要桥**：平台直接把任务 POST 到你那个 HTTP 地址，它自己处理、自己回报。",
        "persist_how": "不用跑桥。要保证的是**它那个 HTTP 服务一直在**（进程别被关掉）。",
        "wake_answer": "没有「叫醒命令」这回事 —— 只要把你的 HTTP 地址交给平台（探测会自己认出来）。",
        "platform_can": ["平台能直推任务（不过桥、延迟最低）", "第 4 步自检验的是**直推地址**能否应答"],
        "platform_cannot": ["平台不负责拉起那个 HTTP 服务（那是它自己的进程）"],
    },
    "http": {
        "label": "HTTP 回调 / 兜底轮询",
        "tier_hint": "E 档 · 测不出来（兜底）",
        "steps": _s(wake=NOT_APPLICABLE, bridge=NEEDED, selfcheck=NEEDED, persist=NEEDED),
        "bridge_kind": "pending_poll",
        "inbox": INBOX["nowhere"],
        "pickup": "平台**只会把任务落盘进待取队列**；没人轮询它就一直躺着（不会自己跑到 agent 面前）。",
        "persist_how": "要么起一只桥去轮询取件，要么改成 `file_poll`/`http_api`/`acp` 之一。",
        "wake_answer": "没有可填的（平台不会自动叫醒它）—— 这是**如实的能力边界**，不是失败。",
        "platform_can": ["平台会把活存下来等你来接"],
        "platform_cannot": ["**平台不会自动叫醒它** —— 选这一档等于「先落盘、暂不叫醒」"],
    },
    "clipboard": {
        "label": "人工中转（手动粘贴）",
        "tier_hint": "D 档 · 人工中转",
        "steps": _s(wake=NOT_APPLICABLE, bridge=OPTIONAL, selfcheck=OPTIONAL, persist=OPTIONAL),
        "bridge_kind": "pending_poll",
        "inbox": INBOX["manual"],
        "pickup": "平台落盘 → **人把内容粘给 agent** → agent 干完 → 人/桥把结果送回去。",
        "persist_how": "桥常驻可以让「取件/回传」自动化，但**「敲门」这一下永远得人来**。",
        "wake_answer": "没有「叫醒命令」（`mode:manual` 即正常完成）—— 任务由**用户手动粘贴**。",
        "platform_can": ["桥能把活送到门口、也能把结果带回来"],
        "platform_cannot": ["**平台无法自动叫醒它**（「敲门」得人来）"],
    },
    "mcp": {
        "label": "MCP 客户端",
        "tier_hint": "B 档 · MCP 工具补偿",
        "steps": _s(wake=NOT_APPLICABLE, bridge=NOT_APPLICABLE,
                   selfcheck=NEEDED, persist=NOT_APPLICABLE),
        "bridge_kind": None,
        "inbox": INBOX["mcp_call"],
        "pickup": "平台作为 MCP 客户端连上它，`tools/call` 直接调它的工具，不需要桥。",
        "persist_how": "不用跑桥；要保证远程 MCP 服务可达。",
        "wake_answer": "没有「叫醒命令」：平台直连它的 MCP 服务。",
        "platform_can": ["平台能列出它的工具并直接调用"],
        "platform_cannot": ["MCP 服务本身要一直活着（那是它自己的进程）"],
    },
}

#: 未知/没声明的叫醒方式：**如实说不知道**，并给出"怎么自己确认"
UNKNOWN = {
    "label": "未声明 / 平台不认识",
    "tier_hint": "未知",
    "steps": _s(wake=OPTIONAL, bridge=OPTIONAL, selfcheck=NEEDED, persist=OPTIONAL),
    "bridge_kind": None,
    "inbox": INBOX["pending_queue"],
    "pickup": "平台还不知道它靠什么被叫醒 —— 派活前请先确认这一点，别让活躺在队列里。",
    "persist_how": "先把叫醒方式定下来（自测四项：能不能被命令拉起 / 盯不盯目录 / 有没有常驻 HTTP / 只能人工）。",
    "wake_answer": "先做自测再填；测不出来就如实选 `clipboard`（一定不会错，之后可改）。",
    "platform_can": ["平台会把活存下来"],
    "platform_cannot": ["平台不知道该怎么叫醒它"],
}

#: 桥型 → 叫醒方式的对应（用于"你选的桥型和你声明的接口对不上"的告警）
KIND_FOR_WAKEUP = {
    "acp": "cli_acp",
    "file_poll": "file_poll",
    "http": "pending_poll",
    "clipboard": "pending_poll",
    "http_api": None,
    "mcp": None,
}


def normalize(wakeup_method: Any) -> str:
    """把各种形状（枚举 / 字符串 / None）归一成小写字符串。"""
    w = wakeup_method or ""
    return str(getattr(w, "value", w)).strip().lower()


def for_wakeup(wakeup_method: Any) -> dict:
    """取该叫醒方式的引导（未知则回 `UNKNOWN`，**不猜**）。"""
    key = normalize(wakeup_method)
    base = BY_WAKEUP.get(key) or UNKNOWN
    out = dict(base)
    out["wakeup_method"] = key or "(未声明)"
    return out


def match_for(wakeup_method: Any) -> dict:
    """该接口的**适配与高效性等级 + 该配哪只桥**（取 `bridge_catalog.WAKEUP_TO_BRIDGES` 一处真相）。

    单独包一层是为了：`bridge_catalog` 万一不可用，引导致少还能给出五步表，
    不至于整块 500 —— 但**降级时如实说明**，不假装有等级。
    """
    try:
        from . import bridge_catalog as _bc
        m = _bc.match(wakeup_method)
        if m:
            return m
        return {"grade": "?", "grade_label": "未知（平台不认识这种接口）",
                "primary_bridge": None, "bridges": [], "score": 0,
                "latency": "未知", "overhead": "未知", "fully_auto": False,
                "note": f"平台不认识接口 `{normalize(wakeup_method)}` —— 请先确认它的叫醒方式",
                "effort": ""}
    except Exception as e:  # noqa: BLE001
        return {"grade": "?", "grade_label": f"（等级表不可用：{e}）", "primary_bridge": None,
                "bridges": [], "score": 0, "latency": "", "overhead": "", "fully_auto": False,
                "note": "", "effort": ""}


def bridge_kind_warning(wakeup_method: Any, kind: str) -> str:
    """桥型与声明的叫醒方式不匹配时，给一句**人话告警**（空串 = 没问题）。

    ★V44 #15 的产品化形态：**不禁止**（用户可能有自己的理由），但**必须说话**。
    ★2026-10-13：「谁配谁」的判据搬到 `bridge_catalog.bridge_fit`（"接口 × 桥型"矩阵）——
      这里只把结论翻成人话，**不再自己维护一份对应表**（否则又是两处口径）。
    """
    key = normalize(wakeup_method)
    kind = (kind or "").strip()
    if not kind:
        return ""
    try:
        from . import bridge_catalog as _bc
        fit = _bc.bridge_fit(key, kind)
    except Exception:  # noqa: BLE001
        expect = KIND_FOR_WAKEUP.get(key, "?")
        if expect == kind:
            return ""
        return (f"⚠️ 桥型与叫醒方式可能不匹配：你声明 `{key}`，这次要生成 `{kind}` —— "
                f"请自行确认（平台的适配矩阵本次不可用）。")
    f = fit.get("fit")
    if f == "best":
        return ""
    if f == "ok":
        return f"提示：{fit['reason']}"
    if f == "redundant":
        return (f"你选的桥型是 `{kind}`，但你声明的叫醒方式是 `{key}` —— {fit['reason']}。"
                f"继续用桥也能跑，但请知悉：这是一条多余的链路。")
    if f == "unknown":
        return f"⚠️ {fit['reason']}"
    return f"⚠️ 桥型与叫醒方式不匹配：你声明 `{key}`，但这次要生成 `{kind}`。{fit['reason']}"


def summary_block(wakeup_method: Any, *, harness_id: str = "") -> dict:
    """**注册响应里那一块**（给界面/文档直接显示的按接口引导）。"""
    g = for_wakeup(wakeup_method)
    m = match_for(wakeup_method)
    return {
        "wakeup_method": g["wakeup_method"],
        "interface_label": g["label"],
        "tier_hint": g["tier_hint"],
        "steps": dict(g["steps"]),
        "step_labels": dict(STEP_LABEL),
        "bridge_kind": g.get("bridge_kind"),
        "where_tasks_go": g["inbox"],
        "how_tasks_arrive": g["pickup"],
        "how_to_keep_running": g["persist_how"],
        "what_to_fill_in_step2": g["wake_answer"],
        "platform_can": list(g["platform_can"]),
        "platform_cannot": list(g["platform_cannot"]),
        # ★2026-10-13：适配与高效性等级 + 该配哪只桥（一处真相：bridge_catalog）
        "grade": m.get("grade"),
        "grade_label": m.get("grade_label"),
        "primary_bridge": m.get("primary_bridge"),
        "compatible_bridges": list(m.get("bridges") or []),
        "efficiency": {"latency": m.get("latency"), "overhead": m.get("overhead"),
                       "fully_auto": m.get("fully_auto")},
        "harness_id": harness_id,
    }


def flow_text(wakeup_method: Any, *, harness_id: str = "", platform_url: str = "") -> str:
    """同一份引导的**纯文本版**（给"复制给 AI"的话术用；与 `summary_block` 同源）。"""
    g = for_wakeup(wakeup_method)
    m = match_for(wakeup_method)
    hid = harness_id or "<你的 harness_id>"
    base = (platform_url or "").rstrip("/") or "<平台地址>"
    _bridge = m.get("primary_bridge") or "（不需要桥）"
    lines = [f"【你的接口：{g['label']}】{g['tier_hint']}",
             f"【适配/高效性等级：{m.get('grade')}】{m.get('grade_label')}",
             f"【该配的桥：{_bridge}】可兼容：{m.get('bridges') or '（无）'}",
             f"【延迟/开销】{m.get('latency')}；{m.get('overhead')}",
             "五步里**哪几步对你要做**："]
    for k in (STEP_REGISTER, STEP_WAKE, STEP_BRIDGE, STEP_SELFCHECK, STEP_PERSIST):
        lines.append(f"  · {STEP_LABEL[k]}：{g['steps'][k]}")
    lines += [
        f"第 2 步要填什么：{g['wake_answer']}",
        f"活怎么到你面前：{g['inbox']}",
        f"具体怎么运转：{g['pickup']}",
        f"怎么让它一直活着：{g['persist_how']}",
        f"平台能做到：{'；'.join(g['platform_can'])}",
        f"平台做不到（如实告诉你）：{'；'.join(g['platform_cannot'])}",
        f"自检入口：POST {base}/api/harness/{hid}/verify",
    ]
    return "\n".join(lines)
