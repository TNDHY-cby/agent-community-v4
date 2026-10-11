# -*- coding: utf-8 -*-
"""platform/connect_artifact：L3 接入产物按唤醒方式派生（V-15 步骤5）。

设计稿：design-docs/V15_工作间会话联动与三层桥设计.md §5.3 + §八

**关键认识（§5.3）**：「具体桥」只对有桥的协议成立 ——
若对 `http_api` 型要求"生成脚本"，**这个要求本身就是错的**。故按档派生：

    acp        -> 可执行脚本（复用 bridge_factory 渲染 L1 模板）
    file_poll  -> 轮询器脚本（同上）
    http_api   -> 连接契约 JSON（callback URL + 工作区坐标 + 回执格式）—— 0 进程
    clipboard  -> 激活提示词 txt（人粘贴）—— 0 进程

**四档全部落 `ws.workspace_dir`**（表格明确列了存放位置）。

---

**安全边界（§八，本模块存在的根本原因）**：

    ❌ caller 传 out_dir   -> 调用方定路径 -> 任意文件写入（V-6 护栏拦它，**原样保留不动**）
    ✅ 平台从 ws.workspace_dir 推导 -> 路径不可被请求输入影响 -> 可安全进工作区

所以本模块**只接受 workshop_id / member_id 这类逻辑 id，绝不接受任何路径字段**；
`target_dir` 一律取自 `ws.workspace_dir`（建间期由平台生成的 `ws_<hex8>`）。
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Optional

# 唤醒方式 -> 接入产物类型（§5.3 表）
KIND_BY_WM = {
    "acp": "script",
    "file_poll": "script",
    "http_api": "contract",
    "http": "contract",
    "clipboard": "prompt",
}
DEFAULT_KIND = "script"          # 兜底：不在表里的按有桥处理（与 auto-connect 口径一致）

# 各类型的产物文件名
FILENAME = {
    "script": "bridge.py",
    "contract": "connect_contract.json",
    "prompt": "activation_prompt.txt",
}


def artifact_kind(wm: str) -> str:
    return KIND_BY_WM.get((wm or "").strip().lower(), DEFAULT_KIND)


def plan(ws: Any, info: Any, member_id: str = "", role: str = "",
         bridge_template: Optional[dict] = None) -> dict:
    """算出 L3 产物的类型/落点/参数。**不写盘**，供预检与响应预览。

    **落点一律由平台派生**（`test_connect_artifact.py::test_plan_has_no_user_path_input`
    盯着这条：本函数的签名里**不允许出现任何路径入参**）。
      - 不给 `role` => 落 `ws.workspace_dir`（工作间根，历史行为）；
      - 给了 `role` => 落**该成员的隔间** `<ws_dir>/<职位>_<成员id>/`（V23 §9 老板口述：
        「单个会话拥有一个隔间和一个具体桥」）。

    ⚠️ 我做这版时曾图省事加了个 `target_dir` 路径参数，**被上面那条测试直接拦下**——
    加一个"可传任意路径"的口子，就把"路径不可被请求体影响"这条可断言的性质削掉了。
    现改为只收 `role`，路径在本函数内用同一个 `compartment.compartment_dir()` 推导：
    与调用方 `ensure_compartment()` 共用同一处推导，仍只有一个路径来源。
    """
    from . import bridge_template as bt
    from . import compartment as _cmp

    wm = bt._wm_value(info)
    # ── ★V36 §3.2：成员**显式挑的那条具体桥模板**优先 ──────────────────────
    # 老板口径「一条桥 = 一个具体会话」+ V36 §3.1：用户可以在界面上为某成员选一条
    # 「具体桥模板」（来自 `platform/bridge_templates.py` 的多条列表）。
    # ① `bridge_template` 入参（本函数新增，**不是路径**，故不违反上面那条守护性质）
    #    ⇒ 用户显式选中的那条；
    # ② 没给 ⇒ 维持原行为：用 harness 级那条（`bridge_template.get`，V-15 自动派生）。
    # 这样"没挑模板"的老路径**行为完全不变**（向后兼容），"挑了模板"才走新分支。
    if isinstance(bridge_template, dict) and bridge_template:
        l2 = dict(bridge_template)
    else:
        l2 = bt.get(info) or bt.build(info) or {}
    # 产物**类型**仍按唤醒方式派生（历史口径不动）：
    # ⚠️ 别混淆两个"kind" —— 具体桥模板里的 `kind` 是**传输层种类**（cli_acp / file_poll /
    #    pending_poll / script_framework，见 V36 §2.1），而这里的 `artifact_kind` 是
    #    **产物形态**（script / contract / prompt，由唤醒方式决定）。二者不是一回事。
    kind = artifact_kind(wm)

    if str(role or "").strip():
        _target = str(_cmp.compartment_dir(ws.workspace_dir, role, member_id))
    else:
        _target = str(getattr(ws, "workspace_dir", "") or "")

    params = dict(l2.get("params") or {})
    params["HARNESS_ID"] = params.get("HARNESS_ID") or str(getattr(info, "harness_id", ""))
    # 工作区坐标在**这里**注入（步骤5：L2 只存 harness 相关参数）
    # V23 §9：有隔间时，该成员的工作区坐标**就是它的隔间**（它只能在自己的隔间里写文件）
    params["WORKSPACE_DIR"] = _target
    # 2026-10-08 老板：「一个员工一个具体桥和隔间 ⇒ 不会发生**谁收到就是谁的**」。
    # 邮箱是这条规则的**寻址**落点：把 INBOX_DIR 从「harness 级 wakeup_dir」改成
    # **该成员隔间里的 inbox** ⇒ 同一 harness 开多个会话也不会串（谁的桥只取谁的邮箱）。
    # 附带修好一个真问题：从前本类 harness 若 wakeup_dir 为空，会 "缺少模板必填字段: INBOX_DIR"
    # 而桥渲染失败；现在有成员邮箱兜住。
    if str(role or "").strip():
        params["INBOX_DIR"] = str(_cmp.mailbox_dir(ws.workspace_dir, role, member_id, "inbox"))
    if kind == "contract":
        params["PLATFORM_URL"] = params.get("PLATFORM_URL") or ""
    if kind == "script":
        # TRIGGER_CMD 是 L1 file_poll/pending_poll 的可选占位，不传则留空
        params.setdefault("TRIGGER_CMD", "")

    return {
        "workshop_id": str(getattr(ws, "workshop_id", "")),
        "member_id": member_id,
        "harness_id": params["HARNESS_ID"],
        "wakeup_method": wm,
        "artifact_kind": kind,
        # ⚠️ `_render_script` 按 `plan["template"]` 去 `bridge_factory.generate()` 取内置渲染模板，
        # 所以这里必须给出**传输层模板名**。两种来源的字段名不同（实测踩过）：
        #   · 旧来源 `bridge_template.build()` 用字段 `template`
        #   · V36 的多条「具体桥模板」用字段 `kind`（cli_acp / file_poll / pending_poll / …）
        # 只认 `template` 会让"用户挑的模板"渲染失败并报 "L2 模板缺失"。
        "template": l2.get("template") or l2.get("kind") or "",
        "template_name": l2.get("name") or "",
        "target_dir": _target,   # ← 唯一路径来源（默认工作间根；给了隔间就是隔间）
        "filename": FILENAME.get(kind, "artifact.txt"),
        "params": params,
        "complete": bool(params.get("WORKSPACE_DIR")),
        "note": _note(kind),
    }


def _note(kind: str) -> str:
    return {
        "script": "生成可执行脚本（平台不启动，由用户运行）",
        "contract": "无桥型：生成连接契约 JSON（0 进程）",
        "prompt": "无桥型：生成激活提示词，由人工粘贴到 harness 会话（0 进程）",
    }.get(kind, "")


def render(plan_: dict) -> dict:
    """按 plan 生成产物到 `plan_['target_dir']`。**该路径由平台派生，不可被输入影响。**

    返回 {ok, artifact_kind, path, bytes, error}。
    """
    kind = plan_["artifact_kind"]
    target = Path(plan_["target_dir"])
    if not str(target).strip():
        return {"ok": False, "error": "workspace_dir 为空，无处落产物"}
    try:
        target.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        return {"ok": False, "error": f"工作区不可写: {e}"}

    if kind == "script":
        return _render_script(plan_, target)
    if kind == "contract":
        return _render_contract(plan_, target)
    return _render_prompt(plan_, target)


def _render_script(plan_: dict, target: Path) -> dict:
    tmpl = plan_.get("template") or ""
    if not tmpl:
        return {"ok": False, "error": "L2 模板缺失，无法渲染脚本"}
    try:
        from .bridge_factory import generate, BridgeTemplateError
        path = generate(tmpl, plan_["params"], target, plan_["filename"])
        return {"ok": True, "artifact_kind": "script",
                "path": str(path), "bytes": path.stat().st_size,
                "launch_hint": f'python -u "{path}" --url {plan_["params"].get("PLATFORM_URL", "")}'.strip()}
    except Exception as e:
        return {"ok": False, "error": f"脚本生成失败: {e}"}


def _render_contract(plan_: dict, target: Path) -> dict:
    """连接契约：外端按这份 JSON 就知道往哪回、回什么格式。"""
    p = plan_["params"]
    contract = {
        "kind": "connect_contract",
        "workshop_id": plan_["workshop_id"],
        "member_id": plan_["member_id"],
        "harness_id": plan_["harness_id"],
        "wakeup_method": plan_["wakeup_method"],
        "platform_url": p.get("PLATFORM_URL", ""),
        "workspace_dir": p.get("WORKSPACE_DIR", ""),
        "callbacks": {
            "activation": f"{p.get('PLATFORM_URL', '')}/api/harness/activation-result",
            "task": f"{p.get('PLATFORM_URL', '')}/api/harness/task-result",
            "heartbeat": f"{p.get('PLATFORM_URL', '')}/api/harness/heartbeat",
        },
        "activation_body": {
            "workshop_id": plan_["workshop_id"],
            "member_id": plan_["member_id"],
            "status": "entered",
            "session_id": "<本次会话 id，建议提供>",
            "context_turns": 1,
            "source": "acp|mcp|plugin",
        },
        "generated_at": time.time(),
        "note": "无桥型（http_api/http）。0 进程，直接按上表 POST 回报。",
    }
    f = target / plan_["filename"]
    try:
        f.write_text(json.dumps(contract, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"ok": True, "artifact_kind": "contract",
                "path": str(f), "bytes": f.stat().st_size}
    except Exception as e:
        return {"ok": False, "error": f"契约写入失败: {e}"}


def _render_prompt(plan_: dict, target: Path) -> dict:
    """激活提示词：CLIPBOARD 档的产物 —— 由人粘贴，平台不发。"""
    p = plan_["params"]
    try:
        from .server import _activation_prompt
        txt = _activation_prompt(
            p.get("HARNESS_ID", ""),
            _member_role(target),
            p.get("WORKSPACE_DIR", ""),
            plan_["workshop_id"],
            plan_["member_id"],
        )
    except Exception:
        txt = (f"你已进入工作间，工作区：{p.get('WORKSPACE_DIR','')}\n"
               f"读取 hall.md 后回复「收到，已进入工作状态」。")
    # 头部加一段"这是人工粘贴用的"说明，避免被误当成平台消息
    header = ("# 人工激活提示词（CLIPBOARD 型）\n"
              "# 复制下面全文，粘贴到你的 harness 对话框：\n\n")
    f = target / plan_["filename"]
    try:
        f.write_text(header + txt, encoding="utf-8")
        return {"ok": True, "artifact_kind": "prompt",
                "path": str(f), "bytes": f.stat().st_size}
    except Exception as e:
        return {"ok": False, "error": f"提示词写入失败: {e}"}


def _member_role(target: Path) -> str:
    """提示词需要 role；计划里没带就取默认 —— 不因此失败。"""
    return "员工"
