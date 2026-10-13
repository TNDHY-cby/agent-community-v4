# -*- coding: utf-8 -*-
"""platform/tier_guidance：接入档位 + 提醒工作（V-15 步骤6）。

设计稿：design-docs/V15_工作间会话联动与三层桥设计.md §6.2/§6.3/§10

**为什么要有提醒**：平台「不再动手架桥」了（V-15 的方向是让外端自己连），
那就**必须把话说清** —— 不提示，用户只会看到一个不动的界面。

**三个时机（§10.1）**：

    注册后  -> 接入档位 A/B/C/D；D 档给 §6.3 插件引导
    建间后  -> 接入产物路径 + 启动方式；C 档给「请建会话，cwd=工作区」+ 回执契约
    开工时  -> 各成员进入状态（**步骤3 已由 entry_state 覆盖**，此处不重复）

**§6.3 的红线：表达需求，不指定实现。**
平台**不为每个 harness 写安装步骤** —— 那是已被否掉的思路（N× 集成成本）。
文案只说"需要哪三项能力"，让用户自己去插件市场找。
"""
from __future__ import annotations

from typing import Any, Optional

# ── ★2026-10-13「最优解」：**不再用 A/B/C/D 给档位起名** ─────────────────────
# 问题（老板 2026-10-13 指出的口径债）：同一个"挡位"概念在系统里有**三份定义**，
#   ① 本模块原来的接入档位 A/B/C/D（ACP / MCP / 自建 / 插件市场）
#   ② register.html 自测题的 A~E（命令行 / 盯目录 / 网页接口 / 人工 / 测不出来）
#   ③ V46 的**适配等级** S/A/B/C/D
#   ⇒ 同一段话里"B 档"能指三个不同的东西（`wakeup_method` 其实只有 6 个值）。
# 裁定：**字母只留给"适配等级"一套**（`bridge_catalog.GRADES`）。
#   · 本模块不再输出 `access_tier` 那样的**档位名**，只输出**事实判断**：
#     `needs_protocol`（要不要它自己补协议能力）、`can_be_woken_by_platform`（平台能不能自动叫醒它）、
#     `interface_class`（它属于哪一类事实：有命令 / 盯目录 / 常驻HTTP / 只人工）。
#   · 给界面的一句话提醒一律用 **`wakeup_method` 原值 + 适配等级**，不再造第三个词。
#   · 旧的 `metadata["access_tier"]` 仍是**老版本落盘数据的字段名**，保留不删（只写不读）；
#     新语义写进 `metadata["interface_class"]`。这是"存档迁移"，不是两套口径。
TIER_A = "A"   # 兼容常量（老测试/老调用方仍在引用）；**不再出现在任何用户可见文案里**
TIER_B = "B"
TIER_D = "D"
TIER_C = "C"

#: 事实分类 —— 这是"档位"该有的样子：说事实，不造字母
CLASS_CMD = "has_command"        # 能被一条命令拉起
CLASS_DIR = "watches_dir"        # 盯着一个目录
CLASS_HTTP = "has_http_api"      # 自带常驻 HTTP 接口
CLASS_MANUAL = "manual_only"     # 只能人工粘贴
CLASS_UNKNOWN = "unknown"        # 测不出来

CLASS_LABEL = {
    CLASS_CMD: "能被一条命令拉起",
    CLASS_DIR: "盯着一个目录",
    CLASS_HTTP: "自带常驻 HTTP 接口",
    CLASS_MANUAL: "只能人工粘贴",
    CLASS_UNKNOWN: "测不出来（平台先落盘、暂不叫醒）",
}

# §6.3 插件引导文案（**表达需求，不指定实现**）
GUIDANCE_PLUGIN = (
    "你的 harness 无原生接入协议。可通过其插件市场获得接入能力，按优先级需要：\n"
    "  ① 平台能把消息发进来\n"
    "  ② 结果能发回平台\n"
    "  ③ 能按工作区建立独立上下文\n"
    "装好插件后**重新注册**，平台会自动探测新协议并回到自动接入路径。"
)

# §10.2 C 档回执契约模板（workspace_dir 由建间期填充）
RECEIPT_TEMPLATE = (
    "请在你的 harness 中新建会话，工作区坐标 = {workspace_dir}\n"
    "建完回报：POST {platform_url}/api/harness/activation-result\n"
    "          {{\"workshop_id\":\"{workshop_id}\",\"member_id\":\"{member_id}\","
    "\"status\":\"entered\",\n"
    "           \"session_id\":\"<你的会话ID>\",\"context_turns\":1}}\n"
    "未回报 → 平台标为 needs_human，不继续派活。"
)


#: 会话能力工具名（拍板 #2：静默发现，不立跨侧契约、不对外宣传）
_SESSION_TOOLS = ("new_session", "create_session", "session_new", "start_session")


def _wm_value(info: Any) -> str:
    w = getattr(info, "wakeup_method", "") or ""
    return str(getattr(w, "value", w)).lower()


def _has_session_tool(info: Any) -> Optional[str]:
    """tools 里有没有 new_session 类工具（拍板 #2：静默发现）。"""
    for t in (getattr(info, "tools", None) or []):
        name = str(getattr(t, "name", "") or "").lower()
        if name in _SESSION_TOOLS:
            return getattr(t, "name", "")
    return None


def interface_class(info: Any) -> str:
    """它属于哪一类**事实**（取代原来那套 A/B/C/D 档位名）。"""
    wm = _wm_value(info)
    if wm == "acp":
        return CLASS_CMD
    if wm == "file_poll":
        return CLASS_DIR
    if wm == "http_api":
        return CLASS_HTTP
    if wm in ("clipboard",):
        return CLASS_MANUAL
    if wm == "mcp":
        return CLASS_HTTP          # MCP 也是"平台直连它"，事实同一类
    return CLASS_UNKNOWN


def tier_of(info: Any) -> str:
    """**兼容保留**：老调用方仍要一个 A/B/C/D 代码。

    ⚠️ 这个返回值**只用于存档/兼容**，不许再写进任何用户可见文案（见本模块顶部裁定）。
    新代码请用 `interface_class()` / `can_be_woken_by_platform()` / `needs_protocol_for()`。
    """
    cls = interface_class(info)
    if cls == CLASS_CMD:
        return TIER_A
    if cls == CLASS_HTTP:
        return TIER_B
    # 无原生协议：默认按"可通过插件市场补能力"（诚实边界：平台探测不到插件市场，那是 harness 侧的事）；
    # 只有注册时显式声明 `metadata.plugin_marketplace == False` 才算"确实没有"。
    meta = getattr(info, "metadata", None) or {}
    if meta.get("plugin_marketplace") is False:
        return TIER_C
    return TIER_D


def can_be_woken_by_platform(info: Any) -> bool:
    """平台**能不能自动叫醒它**（最重要的事实判断，取代"X 档"的说法）。"""
    return _wm_value(info) in ("acp", "file_poll", "http_api", "mcp")


def needs_protocol_for(info: Any) -> bool:
    """要不要它自己去补"能被接进来"的能力（插件/自建会话）。

    诚实边界不变：平台**探测不到** harness 有没有插件市场，
    故"无原生协议"默认按"需要补"算；显式声明 `metadata.plugin_marketplace == False` 视为确实没有。
    """
    return interface_class(info) in (CLASS_MANUAL, CLASS_UNKNOWN)


def guidance_at_register(info: Any) -> dict:
    """注册后的提醒（§10.1 时机 1）。返回可直接塞进注册响应的 dict。

    ★2026-10-13 裁定：**对外只用事实判断 + `wakeup_method` 原值**，不再输出"X 档"。
      `access_tier` 仍在（老版本落盘字段名 & 老调用方兼容），但**界面话术一律不引用它**；
      要给人看的是 `interface_class_label` 与 `can_be_woken_by_platform`。
    """
    tier = tier_of(info)
    cls = interface_class(info)
    out = {
        "access_tier": tier,                       # 兼容保留（不用于文案）
        "interface_class": cls,
        "interface_class_label": CLASS_LABEL.get(cls, cls),
        "can_be_woken_by_platform": can_be_woken_by_platform(info),
        "needs_protocol": needs_protocol_for(info),
    }
    if tier == TIER_D:
        out["guidance_kind"] = "plugin"
        out["guidance"] = GUIDANCE_PLUGIN
        out["note"] = "「用户自建会话 + 回执契约」那条路会在建间后给出（需要工作区坐标）。"
    elif tier == TIER_C:
        out["guidance_kind"] = "receipt_pending"
        out["guidance"] = ("你已声明无插件市场，将走「用户自建会话 + 回执契约」："
                           "**建间后**到接入产物里取工作区坐标与回报格式。")
    elif tier == TIER_B:
        tool = _has_session_tool(info)
        out["guidance_kind"] = "auto" if tool else "incomplete"
        if tool:
            out["guidance"] = f"MCP 已发现会话工具 {tool}，平台将自动建立会话（不需要桥）。"
        else:
            out["guidance"] = ("MCP 类型但未发现 new_session 类工具 —— 平台将以自述回执为准"
                               "（可验证性下降）。")
    else:
        out["guidance_kind"] = "auto"
        out["guidance"] = f"接口 `{_wm_value(info) or 'acp'}`：平台可直接拉起会话并拿到 session_id。"
    return out


def guidance_at_artifact(info: Any, workspace_dir: str = "",
                         workshop_id: str = "", member_id: str = "",
                         platform_url: str = "", artifact_path: str = "",
                         artifact_kind: str = "") -> dict:
    """建间后的提醒（§10.1 时机 2）：产物路径 + 启动方式；人工型给回执契约。"""
    tier = tier_of(info)
    cls = interface_class(info)
    out = {
        "access_tier": tier,                       # 兼容保留（不用于文案）
        "interface_class": cls,
        "interface_class_label": CLASS_LABEL.get(cls, cls),
        "can_be_woken_by_platform": can_be_woken_by_platform(info),
        "artifact_path": artifact_path,
        "artifact_kind": artifact_kind,
    }
    if artifact_kind == "script" and artifact_path:
        out["startup_hint"] = f'python -u "{artifact_path}" --url {platform_url}'.strip()
        out["reminder"] = ("桥是独立进程：终端关闭就停了。要让平台随时能派活，"
                           "请把桥命令加入开机启动项或计划任务。")
    elif artifact_kind == "contract":
        out["reminder"] = "无桥型（0 进程）：按 connect_contract.json 里的回调地址直接 POST 回报。"
    elif artifact_kind == "prompt":
        out["reminder"] = "无桥型（0 进程）：把 activation_prompt.txt 全文粘贴到你的 harness 会话。"

    # 声明"没有插件市场"的那一档：**必须带回报契约**（§10.2 —— 不回报就不派活，必须说清）
    if tier == TIER_C:
        out["c_receipt"] = RECEIPT_TEMPLATE.format(
            workspace_dir=workspace_dir or "（工作区坐标）",
            platform_url=(platform_url or "").rstrip("/"),
            workshop_id=workshop_id or "",
            member_id=member_id or "",
        )
        out["c_receipt_required"] = True
    return out


def apply_needs_protocol(info: Any) -> bool:
    """把 needs_protocol + 引导文案写进注册条目 metadata（§6.3「引导产出」）。

    返回是否发生了写入（供调用方决定要不要 save_state）。
    """
    tier = tier_of(info)
    if tier not in (TIER_D, TIER_C):
        return False
    meta = getattr(info, "metadata", None)
    if meta is None:
        info.metadata = {}
        meta = info.metadata
    g = guidance_at_register(info)
    changed = False
    if meta.get("needs_protocol") is not True:
        meta["needs_protocol"] = True
        changed = True
    # ★2026-10-13：新语义写进 `interface_class`（**事实分类**）；
    #   `access_tier` 是**老版本落盘数据的字段名**，只写不读（不给界面用），删它会让老存档读不出来。
    if meta.get("interface_class") != g.get("interface_class"):
        meta["interface_class"] = g.get("interface_class")
        changed = True
    if meta.get("access_tier") != tier:
        meta["access_tier"] = tier
        changed = True
    if meta.get("protocol_guidance") != g.get("guidance"):
        meta["protocol_guidance"] = g.get("guidance")
        changed = True
    return changed
