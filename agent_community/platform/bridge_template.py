# -*- coding: utf-8 -*-
"""platform/bridge_template：L2 具体桥模板（V-15 步骤4）。

设计稿：design-docs/V15_工作间会话联动与三层桥设计.md §5

三层桥模型::

    L1 通用模板      bridge_templates/{cli_acp,file_poll,pending_poll}/*.tmpl
          │  + harness 参数（**本模块在注册期采集**）
          ▼
    L2 具体桥模板    【本模块】持久化于 info.metadata['bridge_template']
          │  + workspace_dir（建间期注入，见步骤5）
          ▼
    L3 接入产物      按唤醒方式派生（步骤5）：脚本 / 连接契约 / 提示词

**为什么 L2 是新的一层**：现状 `bridge/generate` 每次**现取 L1 模板 + 现取 params**，
没有"这个 harness 的具体模板"这个实体 —— 建工作间时还得重新探一遍 harness。
L2 一次采集、多次落地。

**存哪**：`info.metadata["bridge_template"]` —— `save_state()` 走 `info.model_dump()`
**自带持久化**，不必新建文件（复用 `bridge_test`/`activation_prompt` 的既有惯例）。

**它同时是一个可校验的产物**：`complete` + `missing` 告诉用户"差什么才能架桥"，
比等到建间时才报错早一步。
"""
from __future__ import annotations

import time
from typing import Any, Optional

# 唤醒方式 -> L1 模板名（None = 无桥，按 §5.3 派生别的产物）
# 与 auto-connect 的分支口径一致：UNKNOWN/其它走 pending_poll 兜底
WM_TO_TEMPLATE = {
    "acp": "cli_acp",
    "file_poll": "file_poll",
    "http_api": None,      # 无桥 -> 连接契约
    "http": None,
    "clipboard": None,     # 无桥 -> 激活提示词（§11.1 人工）
    "": "pending_poll",    # 未知 -> 兜底轮询桥
}

# §5.3：L3 产物类型
ARTIFACT_KIND = {
    "cli_acp": "script",
    "file_poll": "script",
    "pending_poll": "script",
    None: "prompt",        # 无模板 -> 提示词/契约（按唤醒方式再细分）
}

METADATA_KEY = "bridge_template"


def _platform_url() -> str:
    try:
        from .server import _platform_base_url
        return _platform_base_url()
    except Exception:
        return "http://127.0.0.1:18920"


def _ai_field(info: Any, *names: str) -> str:
    """从 info.ai（HarnessAI）里逐个找字段，找不到就空串 —— 不因字段缺失崩。"""
    ai = getattr(info, "ai", None)
    for n in names:
        v = getattr(ai, n, None)
        if v:
            return str(v)
    return ""


def _wm_value(info: Any) -> str:
    """把 wakeup_method 规一成小写枚举值。

    ⚠️ `WakeupMethod(str, Enum)` 的 `str()` 返回的是 **`"WakeupMethod.ACP"`**，
    不是 `"acp"` —— 直接 `str(x).lower()` 会得到 `"wakeupmethod.acp"`，
    查不到任何映射、静默落到兜底模板。必须取 `.value`。
    """
    w = getattr(info, "wakeup_method", "") or ""
    return str(getattr(w, "value", w)).lower()


def build(info: Any) -> Optional[dict]:
    """按 HarnessInfo 生成 L2 具体桥模板。返回 None 表示不需要模板（无桥型）。

    只采集**与 harness 有关、与工作间无关**的参数 —— 工作区坐标由步骤5 在建间期注入。
    """
    wm = _wm_value(info)
    hid = str(getattr(info, "harness_id", "") or "")
    # 设计稿 §5.2 的 capabilities：由 MCP tools/list 或声明采集，随模板一起落盘
    caps = [str(getattr(t, "name", "") or "")
            for t in (getattr(info, "tools", None) or [])
            if getattr(t, "name", None)]
    template = WM_TO_TEMPLATE.get(wm, "pending_poll")
    if template is None:
        # 无桥型：仍然登记，但标 artifact_kind 让下游派生正确产物
        return {
            "template": None,
            "wakeup_method": wm,
            "artifact_kind": "contract" if wm in ("http_api", "http") else "prompt",
            "params": {"HARNESS_ID": hid},
            "required": ["HARNESS_ID"],
            "missing": ["HARNESS_ID"] if not hid else [],
            "complete": bool(hid),
            "capabilities": caps,
            "platform_url": _platform_url(),
            "created_at": time.time(),
            "source": "auto",
        }

    kind = ARTIFACT_KIND.get(template, "script")
    if template == "cli_acp":
        required = ["HARNESS_ID", "ACP_COMMAND", "ACP_CWD", "MODEL_NAME",
                    "PROVIDER", "DESCRIPTION"]
        params = {
            "HARNESS_ID": hid,
            "ACP_COMMAND": str(getattr(info, "acp_command", "") or ""),
            "ACP_CWD": str(getattr(info, "acp_cwd", "") or ""),
            "MODEL_NAME": _ai_field(info, "model_name"),
            "PROVIDER": _ai_field(info, "provider", "provider_name") or "unknown",
            "DESCRIPTION": str(getattr(info, "description", "") or ""),
        }
    elif template == "file_poll":
        required = ["HARNESS_ID", "INBOX_DIR", "PLATFORM_URL"]
        params = {
            "HARNESS_ID": hid,
            "INBOX_DIR": str(getattr(info, "wakeup_dir", "") or ""),
            "PLATFORM_URL": _platform_url(),
        }
    else:  # pending_poll 兜底
        required = ["HARNESS_ID", "WORK_DIR", "PLATFORM_URL"]
        params = {
            "HARNESS_ID": hid,
            "WORK_DIR": str(getattr(info, "wakeup_dir", "") or ""),
            "PLATFORM_URL": _platform_url(),
        }

    missing = [k for k in required if not str(params.get(k) or "").strip()]
    return {
        "template": template,
        "wakeup_method": wm,
        "artifact_kind": kind,
        "params": params,
        "required": required,
        "missing": missing,
        "complete": not missing,
        "capabilities": caps,
        "platform_url": params.get("PLATFORM_URL", ""),
        "created_at": time.time(),
        "source": "auto",
    }


def ensure(info: Any, force: bool = False) -> Optional[dict]:
    """登记/刷新 L2 到 info.metadata（幂等）。

    只在**参数真的变了**时才覆盖（除非 force）—— 避免每次注册都抹掉用户手工改过的模板。
    返回 None 表示该类型无需模板。
    """
    tpl = build(info)
    if tpl is None:
        return None
    meta = getattr(info, "metadata", None)
    if meta is None:
        info.metadata = {}
        meta = info.metadata
    old = meta.get(METADATA_KEY) or {}
    if not force and old.get("template") == tpl["template"] and \
            old.get("params") == tpl["params"] and old.get("missing") == tpl["missing"]:
        return old                      # 没变就不动（保留原 created_at）
    meta[METADATA_KEY] = tpl
    return tpl


def get(info: Any) -> Optional[dict]:
    """读回已登记的 L2（不触发生成）。"""
    meta = getattr(info, "metadata", None) or {}
    v = meta.get(METADATA_KEY)
    return dict(v) if isinstance(v, dict) else None


def status_of(info: Any) -> dict:
    """给注册响应/前端用的摘要。"""
    tpl = get(info)
    if not tpl:
        return {"has_template": False,
                "note": "该唤醒方式无桥（无需 L2 模板）",
                "artifact_kind": "contract" if _wm_value(info) in ("http_api", "http")
                else "prompt"}
    return {
        "has_template": True,
        "template": tpl.get("template"),
        "artifact_kind": tpl.get("artifact_kind"),
        "wakeup_method": tpl.get("wakeup_method"),
        "complete": tpl.get("complete"),
        "missing": tpl.get("missing"),
    }
