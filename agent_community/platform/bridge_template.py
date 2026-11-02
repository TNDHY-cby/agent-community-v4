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
    # ★2026-11-01（老板：「注册后要变成一个**具体模板**，工作间里用**模板 + 实时路径**重新拉人」）：
    #   A2A **没有桥**，但**必须有具体模板** —— 模板里存的是"与工作间无关"的那部分：
    #   端点、Agent Card、能力、回报契约、派活协议。工作间建间时再叠 workspace_dir/工位/contextId。
    #   （上一版把 A2A 从这条链上摘掉了，等于"注册完什么都没沉淀"，老板要求的就是这个。）
    "a2a": "a2a",
    "": "pending_poll",    # 未知 -> 兜底轮询桥
}

# §5.3：L3 产物类型
ARTIFACT_KIND = {
    "cli_acp": "script",
    "file_poll": "script",
    "pending_poll": "script",
    # A2A 的 L3 产物是**派活契约**（端点 + 任务信封 + 回报方式），不是脚本
    "a2a": "contract",
    None: "prompt",        # 无模板 -> 提示词/契约（按唤醒方式再细分）
}

METADATA_KEY = "bridge_template"


def _platform_url() -> str:
    """平台基址：**委托唯一解析点**（原来这里硬编码 `127.0.0.1:18920` 兜底）。

    ★2026-10-13（老板：「本地网址不应该引入」「本地端口因人而异」）：
      端口是用户自己的事（`AC_PORT` / `--port`），模块里不许写死本机地址兜底。
    """
    try:
        from .server import _platform_base_url
        return _platform_base_url()
    except Exception:
        from ..platform_address import base_url as _addr_base_url
        return _addr_base_url()


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
    elif template == "a2a":
        # ★2026-11-01（老板：注册产出「具体模板」，工作间用「模板 + 实时路径」拉人）：
        #   这里是**与工作间无关**的那部分 —— 一次采集、多次落地。
        #   ⚠️ **不含** workspace_dir / 工位 / contextId / 任务文件：那些是建间期注入的（L3）。
        _card = {}
        try:
            _md = getattr(info, "metadata", None) or {}
            _card = (_md.get("a2a_card") or {}) if isinstance(_md, dict) else {}
        except Exception:                                      # noqa: BLE001 — 卡读不到不影响模板
            _card = {}
        required = ["HARNESS_ID", "API_BASE_URL", "MODEL_NAME"]
        params = {
            "HARNESS_ID": hid,
            "API_BASE_URL": str(getattr(info, "api_base_url", "") or ""),
            "MODEL_NAME": _ai_field(info, "model_name"),
            "PLATFORM_URL": _platform_url(),
            # 派活协议与回报契约：写进模板，建间期直接用，不用再问平台一遍
            "DISPATCH_PROTOCOL": "a2a-jsonrpc:tasks/send",
            "QUERY_PROTOCOL": "a2a-jsonrpc:tasks/get",
            "SESSION_FIELD": "message.contextId",
            "REPORT_PATH": "/api/harness/task-result",
            "CARD_NAME": str((_card or {}).get("name") or ""),
            "CARD_SKILLS": [str((s or {}).get("id") or (s or {}).get("name") or "")
                            for s in ((_card or {}).get("skills") or [])][:10],
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


def render_for_workshop(info: Any, *, workshop_id: str, workshop_dir: str,
                        slot_dir: str, member_seq: int = 0, task_seq: int = 0,
                        task_rel: str = "", context_id: str = "") -> dict:
    """**L3：把 L2 具体模板 + 工作间实时路径 → 这个工位此刻的落地参数。**

    老板 2026-11-01 原话：「注册后变为一个**具体模板**，用户在工作间的时候**不用连接好的会话**，
    而是用**具体模板 + 工作间实时路径**重新拉人」。

    ⇒ 分工要清楚：
      · **L2（注册期）**：与工作间**无关**的东西 —— 端点、卡、能力、派活协议、回报契约；
      · **L3（建间/拉人期，本函数）**：叠上**这次**的工作间目录、工位、任务文件、会话号。

    为什么不能直接复用注册时那套：同一个 harness 可以进多个工作间、多个工位，
    每个组合的任务路径与会话（`contextId`）都不一样 —— 拿注册时的固定值就会串。
    """
    tpl = get(info) or {}
    wm = _wm_value(info)
    out: dict = {
        "kind": tpl.get("artifact_kind") or ("contract" if wm in ("http_api", "http", "a2a")
                                             else "prompt"),
        "wakeup_method": wm,
        "workshop_id": workshop_id,
        "member_seq": int(member_seq or 0),
        # 实时路径（每次现算，不存模板里）
        "workspace_dir": str(workshop_dir or ""),
        "slot_dir": str(slot_dir or ""),
        "task_file": str(task_rel or ""),
        "template_found": bool(tpl),
        "missing": list(tpl.get("missing") or []),
        "ready": bool(tpl.get("complete")),
    }

    if wm == "a2a":
        p = tpl.get("params") or {}
        platform_url = str(p.get("PLATFORM_URL") or _platform_url()).rstrip("/")
        # 会话号：由调用方给（按 工作间+工位 算的 contextId，见 platform/a2a_context.py）
        out.update({
            "protocol": "a2a-jsonrpc:tasks/send",
            "query_protocol": "a2a-jsonrpc:tasks/get",
            "endpoint": str(p.get("API_BASE_URL") or getattr(info, "api_base_url", "") or ""),
            "context_id": str(context_id or ""),
            "session_field": str(p.get("SESSION_FIELD") or "message.contextId"),
            # 回报契约：**这条是 L3 才有的**（含本次 workshop_id / member_id）
            "report": {
                "url": platform_url + str(p.get("REPORT_PATH") or "/api/harness/task-result"),
                "body": {"workshop_id": workshop_id, "member_id": "m%d" % int(member_seq or 0),
                         "harness_id": str(getattr(info, "harness_id", "") or ""),
                         "ok": True, "result": "<你的结果或产物清单>"},
            },
            "task_label": ("T-%03d" % int(task_seq)) if task_seq else "",
        })
        if not out["endpoint"]:
            out["missing"] = list(set(out["missing"] + ["API_BASE_URL"]))
            out["ready"] = False
        return out

    # 其它形态：把 L2 的 params 原样带出（脚本/契约由各自的 generate 继续派生），
    # 只补上"这次"的路径 —— 保持与 a2a 同一形状，调用方不用分叉。
    p = dict(tpl.get("params") or {})
    p.update({"WORKSPACE_DIR": str(workshop_dir or ""), "SLOT_DIR": str(slot_dir or ""),
              "TASK_FILE": str(task_rel or "")})
    out["params"] = p
    return out


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
