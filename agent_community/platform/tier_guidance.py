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

# ── 档位定义 ────────────────────────────────────────────────────
TIER_A = "A"   # ACP 原生会话（session/new，可验）
TIER_B = "B"   # MCP 工具补偿（静默发现 new_session 类工具）
TIER_D = "D"   # 无原生协议，但**可通过插件市场**获得 -> 引导（§6.3）
TIER_C = "C"   # 无协议且无插件市场 -> 用户自建 + 回执契约（§10.2）

TIER_LABEL = {
    TIER_A: "A 档 · ACP 原生会话",
    TIER_B: "B 档 · MCP 工具补偿",
    TIER_D: "D 档 · 插件市场接入",
    TIER_C: "C 档 · 自建会话",
}

# 会话能力工具名（拍板 #2：静默发现，不立跨侧契约、不对外宣传）
_SESSION_TOOLS = ("new_session", "create_session", "session_new", "start_session")

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


def tier_of(info: Any) -> str:
    """判定接入档位（§6.2 接入路径是棵树）。

    **诚实边界**：平台**探测不到** harness 有没有插件市场 —— 那是 harness 侧的事。
    故「无原生协议」默认判 **D**（给插件引导），
    只有注册时显式声明 `metadata.plugin_marketplace == False` 才判 **C**。
    """
    wm = _wm_value(info)
    if wm == "acp":
        return TIER_A
    if wm == "mcp":
        # MCP 有会话工具 -> B；没有 -> 仍属「有原生协议」只是不完整，按 B 记但标缺
        return TIER_B
    # 无原生协议（clipboard / 其它）
    meta = getattr(info, "metadata", None) or {}
    if meta.get("plugin_marketplace") is False:
        return TIER_C          # 明确声明没有插件市场 -> 自建会话
    return TIER_D              # 默认：可通过插件市场获得（表达需求，不指定实现）


def guidance_at_register(info: Any) -> dict:
    """注册后的提醒（§10.1 时机 1）。返回可直接塞进注册响应的 dict。"""
    tier = tier_of(info)
    out = {
        "access_tier": tier,
        "access_tier_label": TIER_LABEL.get(tier, tier),
        "needs_protocol": tier in (TIER_D, TIER_C),
    }
    if tier == TIER_D:
        out["guidance_kind"] = "plugin"
        out["guidance"] = GUIDANCE_PLUGIN
        out["note"] = "C 档（自建会话 + 回执契约）将在建间后给出，需工作区坐标。"
    elif tier == TIER_C:
        out["guidance_kind"] = "receipt_pending"
        out["guidance"] = ("你已声明无插件市场，将走 C 档：用户自建会话 + 回执契约。"
                           "**建间后**到接入产物里取工作区坐标与回报格式。")
    elif tier == TIER_B:
        tool = _has_session_tool(info)
        out["guidance_kind"] = "auto" if tool else "incomplete"
        if tool:
            out["guidance"] = f"MCP 已发现会话工具 {tool}，平台将按 B 档自动建立会话。"
        else:
            out["guidance"] = ("MCP 类型但未发现 new_session 类工具 —— 平台将以自述回执为准"
                               "（可验证性下降）。")
    else:
        out["guidance_kind"] = "auto"
        out["guidance"] = "ACP 原生会话（A 档）：平台可直接 session/new 并拿到 session_id。"
    return out


def guidance_at_artifact(info: Any, workspace_dir: str = "",
                         workshop_id: str = "", member_id: str = "",
                         platform_url: str = "", artifact_path: str = "",
                         artifact_kind: str = "") -> dict:
    """建间后的提醒（§10.1 时机 2）：产物路径 + 启动方式；C 档给回执契约。"""
    tier = tier_of(info)
    out = {
        "access_tier": tier,
        "access_tier_label": TIER_LABEL.get(tier, tier),
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

    # C 档：**必须带回报契约**（§10.2 —— 不回报就不派活，必须说清）
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
    if meta.get("access_tier") != tier:
        meta["access_tier"] = tier
        changed = True
    if meta.get("protocol_guidance") != g.get("guidance"):
        meta["protocol_guidance"] = g.get("guidance")
        changed = True
    return changed
