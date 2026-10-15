"""platform/routers/workshops_session：**会话建立与命名**（老板流程 步骤 4 / 7 / 8 / 9）。

老板 2026-10-08 口述流程的落点：
  步骤 4  自动按 harness 模板**打开新对话**        → 调该 harness 的 `new_session` 脚本
  步骤 7  平台**给出新命名** `<工作间id>-<项目>-<职位>-<成员id>` → `action_scripts.session_name_for()`
  步骤 8  **复制粘贴重命名对话名**                 → 调该 harness 的 `rename_session` 脚本
  步骤 9  **更新视觉脚本**（名字是数据）            → 写进成员 `session_aliases` / `session_ref`

**两条设计原则**（都来自老板的准则）：
  · 准则⑤ 失败要说话：动作"跑了"≠"成了"。每个动作单列 `ok/verified`，
    **`verified=false` 不许当成功**；没配脚本的动作**不报错**，而是给**人工指引**（人工模式）；
  · 一员工一份：动作脚本渲染后落在**该成员自己的隔间**（与"一员工一桥一隔间"同一原则）。
"""

from __future__ import annotations

import asyncio

import os
import time
from pathlib import Path

from fastapi import APIRouter, Request

from ..audit import audit_log as _audit_log      # 与其它 router 同一取法（不在 server 里）
from ..state import workshops

router = APIRouter()


def _visual_options(member, probe: bool = True):
    """取"这个成员能怎么被视觉拉起"（**只读**，见 `V51`）。

    做成薄包装：router 不在导入期就把 `visual_launch` 拖进来（与本文件其它跨模块引用同一风格）。
    `probe=False` 时**不列窗口**（不起子进程）—— 步骤 9 那条路径用它，免得为了一个提示付一次 PowerShell 启动。
    """
    from ..visual_launch import options as _opts
    return _opts(member, probe=probe)


def _json_response(*a, **kw):
    from ..server import Utf8JSONResponse
    return Utf8JSONResponse(*a, **kw)


def _find_member(ws, member_id: str):
    for m in (ws.members or []):
        if m.member_id == member_id:
            return m
    return None


def _member_harness_id(member):
    return (list(getattr(member, "harness_ids", []) or []) or [None])[0]


def _scripts_dir(ws, member) -> Path:
    """该成员的动作脚本目录（落在它自己的隔间里）。"""
    from ..compartment import compartment_dir
    return Path(compartment_dir(ws.workspace_dir, member.role, member.member_id)) / "scripts"


def _manual_hint(action: str, name: str, member) -> str:
    if action == "new_session":
        return f"请在你的 harness 里**新建一个对话**（随后平台会把它重命名成：{name}）"
    if action == "rename_session":
        return f"请把该会话**重命名为**：{name}（复制粘贴即可）"
    return f"（{action}）请按人工模式操作"


@router.get("/api/workshop/{ws_id}/member/{member_id}/session-name")
async def member_session_name(ws_id: str, member_id: str):
    """步骤 7：**只算名字**（预览/复制用，不碰任何脚本、不改状态）。

    用户要"复制粘贴重命名"时，从这里取名字最稳（避免手工拼错）。
    """
    from ..action_scripts import session_name_for
    ws = workshops.get(ws_id)
    if not ws:
        return _json_response({"error": "工作间不存在"}, status_code=404)
    m = _find_member(ws, member_id)
    if not m:
        return _json_response({"error": "成员不存在"}, status_code=404)
    name = session_name_for(ws, m)
    return {"success": True, "workshop_id": ws_id, "member_id": member_id,
            "name": name, "project": getattr(ws, "name", ""),
            "existing_aliases": list(getattr(m, "session_aliases", []) or [])}


@router.get("/api/workshop/{ws_id}/members-status")
async def members_status(ws_id: str):
    """**成员卡片数据（一次拿全）** —— 给前端工作间视图用。

    为什么做成聚合接口：一张成员卡片要看五六样东西（派发进度、会话名、隔间/邮箱、桥在不在、
    这台 harness 哪几步能自动）。让前端逐个成员去问就是 N+1 次请求，且容易拼错口径。
    这里**一次给全**，且每项都是**磁盘/状态的实况**（不是推断）。

    字段（每个成员）：
      · `entry_state`/`entry_reason`：派发进度（老板要求前端读它，别只看 status）；
      · `session_name` + `session_aliases`/`session_ref`：步骤 7~9 的产物；
      · `compartment`：隔间路径、邮箱积压数、产物文件数（来自 `inspect_compartment`，磁盘实况）；
      · `bridge_files`：该成员隔间里有哪些接入产物（桥/契约/提示词）；
      · `steps`：步骤 4/5/8 各自 `automatic`（有脚本可自动）与 `foreground`（会不会打扰用户）。
    """
    from ..action_scripts import available_actions, session_name_for
    from ..compartment import inspect_compartment
    from ..server import harness_manager

    ws = workshops.get(ws_id)
    if not ws:
        return _json_response({"error": "工作间不存在"}, status_code=404)

    _avail_cache: dict = {}
    # ★2026-10-11 老板「删除功能并不完善」的补齐：成员卡也要能看出"它的 agent 已经没了"。
    #   实测背景：删 agent 不会清理引用它的工作间成员 ⇒ 成员卡照常显示 = 假状态（准则⑤）。
    from ..server import harness_manager as _hm
    from .. import orphan_check as _oc
    _known = set(_hm.sessions.keys())
    rows = []
    for m in (ws.members or []):
        hid = _member_harness_id(m) or ""
        _orph = _oc.orphan_state(m, _known)
        if hid not in _avail_cache:
            sess = harness_manager.sessions.get(hid) if hid else None
            try:
                _avail_cache[hid] = available_actions(sess) if sess else {}
            except Exception:  # noqa: BLE001
                _avail_cache[hid] = {}
        av = _avail_cache[hid]
        info = inspect_compartment(ws.workspace_dir, m.role, m.member_id)
        comp_dir = info.get("dir", "")
        # ⚠️ 2026-10-08 审查自查发现：`inspect_compartment()["files"]` 是**隔间根目录**的文件
        # （含 bridge.py / deliver_text.md 等接入产物），**不是 `output/` 里的成果**。
        # 原来的字段名 `output_files` 会骗人（前端拿它当"成果数"显示就错了），故拆成两个如实字段。
        try:
            out_dir = Path(comp_dir) / "output"
            real_outputs = len([p for p in out_dir.rglob("*") if p.is_file()]) if out_dir.is_dir() else 0
        except Exception:  # noqa: BLE001
            real_outputs = 0
        steps = {}
        for step, action in ((4, "new_session"), (5, "deliver"), (8, "rename_session")):
            a = av.get(action) or {}
            steps[f"{step}_{action}"] = {
                "automatic": bool(a.get("configured")),
                "mode": a.get("mode", "manual"),
                "foreground": a.get("foreground", "required"),
            }
        hname = ""
        try:
            sess = harness_manager.sessions.get(hid) if hid else None
            hname = getattr(getattr(sess, "info", None), "harness_name", "") or ""
        except Exception:  # noqa: BLE001
            hname = ""
        rows.append({
            "member_id": m.member_id, "role": m.role, "display_name": m.display_name,
            "status": getattr(m, "status", ""),
            "entry_state": getattr(m, "entry_state", ""),
            "entry_reason": getattr(m, "entry_reason", ""),
            "harness_id": hid, "harness_name": hname,
            # ★2026-10-11：该成员的 agent 是否**还在平台上**。
            #   实测背景：删掉 agent 后成员卡照常显示、平台不提醒 ⇒ "看起来正常"的假状态。
            #   `orphan=true` 时界面必须明说（并给「怎么恢复 / 怎么移除」）。
            "orphan": _orph["orphan"],
            "orphan_text": _orph["text"],
            "orphan_advice": _orph["advice"],
            "session_name": session_name_for(ws, m),      # 步骤7 的名字（未写入也可预览）
            "session_aliases": list(getattr(m, "session_aliases", []) or []),
            "session_ref": dict(getattr(m, "session_ref", {}) or {}),
            # V28 通道生命周期（前端据此显示"运行中/已停用"；停用后不得再投消息）
            "channel": {
                "seq": int(getattr(m, "channel_seq", 1) or 1),
                "state": getattr(m, "channel_state", "pending"),
                "since": float(getattr(m, "channel_since", 0) or 0),
                "retired_at": (float((list(getattr(m, "retired_channels", []) or []) or [{}])[-1].get("retired_at", 0) or 0)
                               if getattr(m, "retired_channels", None) else 0.0),
                "retired_count": len(list(getattr(m, "retired_channels", []) or [])),
                "name": (list(getattr(m, "session_aliases", []) or []) or [""])[0],
            },
            "compartment": {
                "dir": comp_dir,
                "exists": bool(info.get("exists")),
                "mailbox_pending": info.get("mailbox_pending") or {},
                # 两个**如实**计数（别再让前端把根文件数当成果数）：
                "root_files": len(info.get("files") or []),   # 隔间根：桥脚本/契约/投递文本等接入产物
                "output_files": real_outputs,                 # `output/` 底下的**成果**（递归）
            },
            "bridge_files": list(info.get("bridge_files") or []),
            "deliver_text_path": str(Path(comp_dir) / "deliver_text.md") if comp_dir else "",
            "steps": steps,
        })

    auto_all = all(
        all(s["automatic"] for s in r["steps"].values()) for r in rows
    ) if rows else False
    return {
        "success": True, "workshop_id": ws_id, "name": getattr(ws, "name", ""),
        "members": rows,
        "summary": {
            "count": len(rows),
            "all_steps_automatic": auto_all,
            "manual_members": [r["member_id"] for r in rows
                               if not all(s["automatic"] for s in r["steps"].values())],
        },
    }


def deliver_one(ws, m, *, new_name: str = "", text: str = "", return_text: bool = True) -> dict:
    """**步骤 5 的实现**（同步、不含 HTTP 细节）：把「具体桥 + 激活指令」送进该成员的会话。

    为什么抽出来：步骤 5 有**两个入口** —— 用户点「送出具体桥」，以及**新建通道时自动补做**
    （换通道后新对话里必须也有桥，否则那条通道收不到活）。两处必须同一套逻辑（同 `establish_one`）。
    """
    from ..action_scripts import available_actions, run_action, write_action_script
    from ..compartment import compartment_dir, ensure_compartment
    from ..server import (_activation_prompt, _auto_bridge_for_member,
                          harness_manager, save_state)

    hid = _member_harness_id(m)
    comp = ensure_compartment(ws.workspace_dir, m.role, m.member_id)
    bridge = _auto_bridge_for_member(ws, m, hid) if hid else {"ok": False, "path": "", "compartment": comp.get("dir", ""), "error": "该成员未绑定 harness"}

    name = str(new_name or "").strip() or \
        (list(getattr(m, "session_aliases", []) or []) or [session_name_for_safe(ws, m)])[0]
    py = os.environ.get("AC_PYTHON") or "python"
    bridge_path = str((bridge or {}).get("path") or "")
    prompt = ""
    try:
        prompt = _activation_prompt(hid, m.role, ws.workspace_dir, workshop_id=ws.workshop_id,
                                    member_id=m.member_id, artifact_path=bridge_path,
                                    compartment_dir=comp.get("dir", "")) or ""
    except Exception as e:  # noqa: BLE001
        prompt = f"（激活提示词生成失败：{type(e).__name__}: {e}）"

    text = str(text or "").strip() or "\n".join([
        f"【{name}】你是本工作间的成员（职位：{m.role}·{m.member_id}）。",
        "",
        f"1) 你的隔间（**只在这里读写文件**）：`{comp.get('dir', '')}`",
        f"   邮箱（平台往这里投消息）：`{comp.get('inbox', '')}`",
        f"2) 你的具体桥：`{bridge_path}`" if bridge_path else "2) ⚠️ 具体桥未生成（见下方 error）",
        f"   启动：`{py} -u \"{bridge_path}\"`" if bridge_path else "",
        "",
        prompt,
    ]).strip()

    sdir = _scripts_dir(ws, m)
    sess = harness_manager.sessions.get(hid) if hid else None
    avail = available_actions(sess) if sess else {"deliver": {"configured": False, "mode": "manual",
                                                              "foreground": "required"}}
    steps: dict = {}
    manual: list = []
    if avail.get("deliver", {}).get("configured"):
        cfg = {}
        if sess is not None:
            from ..action_scripts import get_config
            cfg = ((get_config(sess).get("deliver") or {}).get("params") or {})
        params = {"HARNESS_ID": hid or ""}
        params.update(cfg)
        p = sdir / "deliver.py"
        write_action_script("deliver", params, p)
        steps["5_deliver"] = run_action(
            p, "deliver",
            {"text": text, "session": dict(getattr(m, "session_ref", {}) or {})},
            timeout=90.0, python_exe=py)
        steps["5_deliver"]["script"] = str(p)
    else:
        manual.append({"step": 5, "action": "deliver",
                       "hint": "该 harness 未配置 deliver 脚本：请把 deliver_text.md 的内容粘贴进它的会话"})
        steps["5_deliver"] = {"ok": True, "verified": False, "mode": "manual",
                              "error": "该 harness 未配置 deliver 脚本",
                              "foreground": avail.get("deliver", {}).get("foreground", "required")}

    # 人工模式也留一份可直接复制的文本（用户视角）
    text_path = Path(comp.get("dir", "")) / "deliver_text.md"
    try:
        text_path.write_text(text, encoding="utf-8")
    except Exception as e:  # noqa: BLE001
        print(f"[deliver] 写 deliver_text.md 失败: {e}", flush=True)

    verified = bool(steps["5_deliver"].get("verified"))
    try:
        _audit_log.record("workshop.deliver_bridge", actor="user", target=ws.workshop_id,
                          detail=f"member={m.member_id} verified={verified} bridge={'ok' if bridge.get('ok') else 'fail'}",
                          extra={"m.member_id": m.member_id, "verified": verified,
                                 "bridge_path": bridge_path, "text_path": str(text_path),
                                 "harness_id": hid or ""})
    except Exception:  # noqa: BLE001
        pass
    save_state()
    return {
        "success": True,
        "workshop_id": ws.workshop_id, "m.member_id": m.member_id, "harness_id": hid or "",
        "bridge": bridge,                    # {ok, path, compartment, error}
        "bridge_path": bridge_path,
        "text_path": str(text_path),         # 人工模式下用户直接复制这个文件
        "text": text if return_text else "",
        "steps": steps, "manual": manual, "verified": verified,
        "note": ("桥与激活指令已由脚本投递并自证" if verified
                 else "**人工模式**：请把 text_path 的内容粘贴进该成员的会话；"
                      "平台不会把它当成已完成"),
    }


def attach_bridge_core(ws, m, *, template_name: str = "", new_name: str = "",
                       do_establish: bool = True, actor: str = "user") -> dict:
    """★V36 §3.2/§3.3：为某成员架起一条**具体桥**并绑定会话（可复用核心）。

    为什么抽成函数：**换通道（V28 establish_channel）后必须把桥绑到新会话**，
    那是同一套动作 ⇒ 不能只有 HTTP 端点能做，否则换会话后桥就吊在旧会话上（V36 §3.3）。

    返回 `{"success", "code", "error", "bridge_ref", ...}`；`code` 供端点映射成 HTTP 状态
    （核心本身不认识 HTTP）。**任何失败都不写 `state=attached`**（准则⑤）。
    """
    from .. import bridge_templates as _bt
    from ..server import harness_manager
    from ..server import _platform_base_url as _platform_url

    hid = _member_harness_id(m)
    if not hid:
        return {"success": False, "code": 400, "error": "该成员未绑定 harness，无法架桥"}
    sess_info = getattr(harness_manager.sessions.get(hid), "info", None)
    if sess_info is None:
        return {"success": False, "code": 404, "error": f"harness {hid} 未注册"}

    want = str(template_name or "").strip()
    tpl = _bt.get(sess_info, want) if want else None
    if want and tpl is None:
        return {"success": False, "code": 404,
                "error": f"harness {hid} 上没有名为「{want}」的具体桥模板",
                "hint": "可用 GET /api/harness/{harness_id}/bridge-templates 查看现有模板名",
                "available": [t["name"] for t in _bt.list_all(sess_info)]}
    if tpl is None:
        tpl = _bt.pick_for(sess_info, ws=ws.workshop_id, role=m.role) or _bt.default_one(sess_info)
    if tpl is None:
        return {"success": False, "code": 400,
                "error": "该 harness 还没有任何具体桥模板，无法架桥",
                "hint": "先到「Harness 接入」页为它登记一条具体桥模板（可手工填，不必依赖 AI）"}

    # 1) 渲染成该成员的桥产物（复用既有 L3 管线：路径由平台派生）
    from ..connect_artifact import plan as _plan, render as _render
    from ..compartment import ensure_compartment
    comp = ensure_compartment(ws.workspace_dir, m.role, m.member_id)
    pl = _plan(ws, sess_info, m.member_id, role=m.role, bridge_template=tpl)
    steps: list = []
    artifact: dict = {}
    if not pl.get("complete"):
        steps.append({"step": "plan", "ok": False, "error": "产物计划不完整（无工作区坐标）"})
    else:
        # ★平台派生坐标由**平台**注入，不要求用户手填（V36 §2.2）：PLATFORM_URL 是
        #   "平台自己的地址"，对任何工作间都一样（漏了它会报"缺少模板必填字段"）。
        try:
            pl["params"]["PLATFORM_URL"] = pl["params"].get("PLATFORM_URL") or _platform_url()
        except Exception:  # noqa: BLE001 — 取不到就保持原样，让下游如实报缺
            pass
        steps.append({"step": "plan", "ok": True, "target_dir": pl.get("target_dir", ""),
                      "artifact_kind": pl.get("artifact_kind", "")})
        try:
            artifact = _render(pl)
        except Exception as e:  # noqa: BLE001
            artifact = {"ok": False, "error": f"{type(e).__name__}: {e}"}
        steps.append({"step": "render", "ok": bool(artifact.get("ok")),
                      "path": artifact.get("path", ""), "error": artifact.get("error", "")})

    ok = bool(artifact.get("ok"))
    bridge_path = str(artifact.get("path") or "")

    # 2) 建/补会话（调用方已建会话时可跳过：换通道路径自己建过了）
    est = establish_one(ws, m, new_name=str(new_name or "").strip()) if do_establish else {}
    session_ref = dict(getattr(m, "session_ref", {}) or {})
    names = list(getattr(m, "session_aliases", []) or [])
    session_name = names[0] if names else session_name_for_safe(ws, m)

    # 3) 落 `bridge_ref` —— 这就是"这条桥 ↔ 这个会话"的绑定
    m.bridge_ref = {
        "template_name": tpl.get("name", ""),
        "bridge_path": bridge_path,
        "session_ref": session_ref,
        "session_name": session_name,
        "state": "attached" if ok else "none",
        "verified": False,          # 平台不采信声明（准则⑤）：桥"生成了"不等于"通了"
        "verified_at": 0.0,
        "attached_at": time.time() if ok else 0.0,
        "artifact_kind": artifact.get("artifact_kind", pl.get("artifact_kind", "")),
        "compartment": comp.get("dir", ""),
        "channel_seq": int(getattr(m, "channel_seq", 1) or 1),
        "error": "" if ok else str(artifact.get("error") or "桥产物未生成"),
    }
    from ..server import save_state
    save_state()
    try:
        _audit_log.record("workshop.member.bridge_attach", actor=actor, target=ws.workshop_id,
                          detail=f"member={m.member_id} template={tpl.get('name','')} "
                                 f"state={m.bridge_ref['state']} session={session_name} "
                                 f"channel_seq={m.bridge_ref['channel_seq']}",
                          extra={"workshop_id": ws.workshop_id, "member_id": m.member_id,
                                 "harness_id": hid, "template_name": tpl.get("name", ""),
                                 "bridge_path": bridge_path, "session_name": session_name,
                                 "state": m.bridge_ref["state"]})
    except Exception:  # noqa: BLE001 — 审计失败不影响架桥
        pass
    print(f"[bridge] attach ws={ws.workshop_id} member={m.member_id} "
          f"template={tpl.get('name','')!r} ok={ok} path={bridge_path}", flush=True)

    return {
        "success": ok,
        "code": 200,
        "error": m.bridge_ref["error"],
        "bridge_ref": dict(m.bridge_ref),
        "workshop_id": ws.workshop_id,
        "member_id": m.member_id,
        "harness_id": hid,
        "template": {"name": tpl.get("name", ""), "kind": tpl.get("kind", ""),
                     "match": tpl.get("match", {})},
        "bridge_path": bridge_path,
        "session_name": session_name,
        "session_ref": session_ref,
        "state": m.bridge_ref["state"],
        "verified": False,
        "steps": steps,
        "establish": est,
        "compartment": comp.get("dir", ""),
    }


@router.post("/api/workshop/{ws_id}/member/{member_id}/bridge/attach")
async def member_bridge_attach(ws_id: str, member_id: str, request: Request):
    """★V36 §3.2：**在工作间里为某成员架起一条具体桥，并绑到它的具体会话**。

    老板口径（2026-10-11）：「**一条桥 = 一个具体会话**」——所以这一步的产出是
    ① 该成员的桥产物（落**它自己的隔间**，由平台派生路径，见 `connect_artifact`）
    ② 成员上记一条 `bridge_ref`：用的哪条模板 / 桥在哪 / **绑的哪个会话** / 状态。

    body：
      `template_name`  —— 用哪条**具体桥模板**（V36 §3.1，来自 `/…/bridge-templates`）；
                          省略 ⇒ 按 `pick_for(ws, role)` 匹配，再回落到该 harness 的第一条。
      `new_name`       —— 建会话时的命名提示（**逻辑名，不是路径**）

    ⚠️ 本端点**不接受任何路径字段**（与 `connect-artifact` 同口径）：路径由平台派生。
    ⚠️ 失败要说话（准则⑤）：桥没生成时**不写 `state=attached`**，如实返回 error 与状态。
    """
    from ..server import Utf8JSONResponse
    ws = workshops.get(ws_id)
    if not ws:
        return Utf8JSONResponse({"error": "工作间不存在"}, status_code=404)
    m = _find_member(ws, member_id)
    if not m:
        return Utf8JSONResponse({"error": "成员不存在"}, status_code=404)

    try:
        body = await request.json()
    except Exception:
        body = {}
    body = body if isinstance(body, dict) else {}

    # 同 `connect-artifact` 的安全口径：只取逻辑 id，路径字段一律 400 且不读
    for _danger in ("out_dir", "target_dir", "path", "dir", "workspace_dir", "bridge_path"):
        if _danger in body:
            return Utf8JSONResponse(
                {"error": f"本端点不接受路径字段 {_danger!r} —— 桥的落点由平台按"
                          f"「成员隔间」派生（V36 §3.2 / V-15 §八）"},
                status_code=400,
            )

    # 实现在 `attach_bridge_core`（换通道后也要复用它，见 V36 §3.3）
    out = attach_bridge_core(ws, m,
                             template_name=str(body.get("template_name") or ""),
                             new_name=str(body.get("new_name") or ""))
    if not out.get("success"):
        return Utf8JSONResponse({k: v for k, v in out.items() if k != "code"},
                                status_code=int(out.get("code") or 400))
    return out


@router.get("/api/workshop/{ws_id}/member/{member_id}/bridge")
async def member_bridge_get(ws_id: str, member_id: str):
    """★V36 §3.2：读某成员的**具体桥绑定**（界面据此显示「桥 → 会话名 → 隔间 → 邮箱」）。

    `state` 为 `none`（空 dict）时 = **未架接** ⇒ 界面**必须明说"未架接"**，不许留空白
    （老板 2026-10-11 原话：「成员隔间与桥中并没有桥相关内容」）。
    """
    ws = workshops.get(ws_id)
    if not ws:
        return _json_response({"error": "工作间不存在"}, status_code=404)
    m = _find_member(ws, member_id)
    if not m:
        return _json_response({"error": "成员不存在"}, status_code=404)
    ref = dict(getattr(m, "bridge_ref", {}) or {})
    from ..compartment import compartment_dir, mailbox_dir
    return {
        "success": True,
        "workshop_id": ws_id,
        "member_id": member_id,
        "role": m.role,
        "display_name": m.display_name,
        "harness_id": _member_harness_id(m),
        "state": ref.get("state") or "none",
        "template_name": ref.get("template_name", ""),
        "bridge_path": ref.get("bridge_path", ""),
        "session_name": ref.get("session_name", ""),
        "session_ref": ref.get("session_ref", {}),
        "verified": bool(ref.get("verified")),
        "artifact_kind": ref.get("artifact_kind", ""),
        "error": ref.get("error", ""),
        # 隔间与邮箱一并给（界面一屏看全"桥→会话→隔间→邮箱"）
        "compartment": compartment_dir(ws.workspace_dir, m.role, m.member_id),
        "inbox": mailbox_dir(ws.workspace_dir, m.role, m.member_id, "inbox"),
        "attached": bool(ref),
    }


def _bridge_status_text(ref: dict) -> str:
    """把桥绑定状态翻成**人话**（供界面直显；按真实状态分三种，不许假成功）。

    ⚠️ 实测踩过：只要 `ref` 非空就写"已架接" ⇒ `state=none`（桥其实没生成）时也写"已架接"，
    属"看起来成功"的假状态（产品化准则⑤「失败要说话」）。故必须**看 state**。
    """
    if not ref:
        return "未架接（还没有为它架起具体桥）"
    state = str(ref.get("state") or "none")
    tpl = ref.get("template_name") or "（未命名模板）"
    sess = ref.get("session_name") or "（未绑定会话）"
    if state == "attached":
        # 桥"生成了" ≠ "通了"：没自证材料时不吹（平台不采信声明）
        base = f"已架接：{tpl} → {sess}"
        return base if ref.get("verified") else base + "（未自证：还没确认这条路真的通）"
    if state == "retired":
        return f"已停用（原：{tpl} → {sess}）"
    why = ref.get("error") or "桥产物未生成"
    return f"未架接（{why}）"


@router.get("/api/workshop/{ws_id}/bridge-bindings")
async def workshop_bridge_bindings(ws_id: str):
    """★V36 §3.2 + FE-17 §4：**该工作间全体成员的桥↔会话绑定**（给运维看板第一栏用）。

    只读、**无副作用**（不得触发激活、不得改状态 —— 对齐 V17 那次的教训）。
    """
    ws = workshops.get(ws_id)
    if not ws:
        return _json_response({"error": "工作间不存在"}, status_code=404)
    rows = []
    for m in ws.members:
        ref = dict(getattr(m, "bridge_ref", {}) or {})
        rows.append({
            "member_id": m.member_id,
            "role": m.role,
            "display_name": m.display_name,
            "harness_id": _member_harness_id(m),
            "channel": {"seq": int(getattr(m, "channel_seq", 1) or 1),
                        "state": getattr(m, "channel_state", "pending")},
            "bridge": {
                "state": ref.get("state") or "none",
                "template_name": ref.get("template_name", ""),
                "bridge_path": ref.get("bridge_path", ""),
                "session_name": ref.get("session_name", ""),
                "verified": bool(ref.get("verified")),
            },
            # 给界面一句**人话**（FE-17 §3：不许出现术语，卡住要说清为什么）
            # ⚠️ 措辞必须**跟着真实状态**走 —— 实测踩过：state=none 时却写"已架接"
            #    就是"看起来成功"的假状态（产品化准则⑤）。所以按 state 分三种说法。
            "status_text": _bridge_status_text(ref),
        })
    return {"success": True, "workshop_id": ws_id, "count": len(rows), "bindings": rows}


@router.get("/api/workshop/{ws_id}/orphans")
async def workshop_orphans(ws_id: str):
    """★老板 2026-10-11「删除功能并不完善」的补齐：**哪些成员的 agent 已经不在了**。

    实测背景：`DELETE /api/harness/{hid}` 本身很认真（清注册/队列/桥进程/桥目录/会话），
    但**不会清理引用它的东西** ⇒ 工作间里留着指向已不存在 agent 的成员（实测 4 个），
    而成员卡照常显示、平台不提醒 ⇒ "看起来正常"的假状态（准则⑤）。

    本端点**只读**，如实列出孤儿成员 + 人话「为什么 / 怎么办」。
    ⚠️ **不自动级联删**：删 agent 是否顺带清工作间成员是**产品决策**（历史要不要留、
       要不要用户点头），先"如实报"，由用户决定（与"停用≠删除"口径一致）。
    """
    from ..server import Utf8JSONResponse, harness_manager
    from .. import orphan_check as _oc
    ws = workshops.get(ws_id)
    if not ws:
        return Utf8JSONResponse({"error": "工作间不存在"}, status_code=404)
    known = set(harness_manager.sessions.keys())
    return _oc.scan_workshop(ws, known)


@router.get("/api/workshop/{ws_id}/delivery-status")
async def workshop_delivery_status(ws_id: str):
    """★V37 §5（P1）：**"消息到底送到没有"** —— 只读、无副作用。

    起因（老板 2026-10-11 事故）：邮箱里压着 4 条消息、桥没在跑，而**平台一声不吭**，
    他看到"消息发了、agent 没反应"，无从下手。本端点把这件事**说出来**。

    给界面用：`headline` 一句话 + 每个成员的 `stuck_reason` / `advice`（都是人话，无术语）。
    ⚠️ `bridge_running` 只在我们**有证据**（最近有投递）时才 True；否则 `None` + 说明 —— **不猜**。
    """
    from ..server import Utf8JSONResponse
    from .. import delivery_status as _ds
    from ..compartment import compartment_dir

    ws = workshops.get(ws_id)
    if not ws:
        return Utf8JSONResponse({"error": "工作间不存在"}, status_code=404)
    # 路径来源只该有一处：这里统一用 compartment.compartment_dir 推导
    comps = {}
    for m in (getattr(ws, "members", None) or []):
        comps[getattr(m, "member_id", "")] = str(
            compartment_dir(ws.workspace_dir, getattr(m, "role", ""), getattr(m, "member_id", ""))
        )
    return _ds.workshop_status(ws, compartments=comps)


@router.post("/api/workshop/{ws_id}/member/{member_id}/self-check")
async def member_bridge_self_check(ws_id: str, member_id: str, request: Request):
    """★V38 §3 第 3 步 + V37 §4.2 第 4 步：**"你的 agent 到底通没通"** —— 一次可判定的动作。

    做法：往该成员邮箱发一条**自检测试消息** → 等一会儿 → 给三态结论：
      · `ok`          通了（桥取走 + agent 回话 + 回报到平台）
      · `no_reply`    桥取走了、但 agent 没回话（卡在 agent 侧）
      · `not_picked`  测试消息还躺在邮箱里 —— **桥没有在运行**
    外加 `not_set_up`（还没架桥、没邮箱）。

    body（都可选）：`{"wait": 8}`（等多久，夹在 0~30 秒）
    ⚠️ 本端点会**真的往邮箱写一条测试文件**（这是它的目的），但**不改任何状态**；
       它写的是 `/api/bridge-catalog` 里那一型的同一套约定（不发明新协议）。
    """
    from ..server import Utf8JSONResponse
    from .. import bridge_selfcheck as _sc
    from ..compartment import mailbox_dir

    ws = workshops.get(ws_id)
    if not ws:
        return Utf8JSONResponse({"error": "工作间不存在"}, status_code=404)
    m = _find_member(ws, member_id)
    if not m:
        return Utf8JSONResponse({"error": "成员不存在"}, status_code=404)
    try:
        body = await request.json()
    except Exception:
        body = {}
    body = body if isinstance(body, dict) else {}
    try:
        wait = float(body.get("wait", 8))
    except Exception:  # noqa: BLE001
        wait = 8.0

    hid = _member_harness_id(m)
    inbox = str(mailbox_dir(ws.workspace_dir, m.role, m.member_id, "inbox"))
    out = _sc.selfcheck(inbox, harness_id=hid or "", workshop_id=ws_id,
                        member_id=m.member_id, role=m.role, wait=wait)
    out.update({"workshop_id": ws_id, "member_id": m.member_id, "harness_id": hid,
                "inbox": inbox})
    # 审计：自检是"用户点了才发"的动作，留痕便于回溯（不做自动探活）
    try:
        _audit_log.record("workshop.member.self_check", actor="user", target=ws_id,
                          detail=f"member={m.member_id} state={out.get('state')} "
                                 f"probe={((out.get('detail') or {}).get('probe_id')) or '-'}",
                          extra={"member_id": m.member_id, "harness_id": hid or "",
                                 "state": out.get("state", "")})
    except Exception:  # noqa: BLE001
        pass
    return out


@router.post("/api/workshop/{ws_id}/member/{member_id}/deliver-bridge")
async def deliver_bridge(ws_id: str, member_id: str, request: Request):
    """**步骤 5**（HTTP 薄壳；实现在 `deliver_one`）：送出具体桥，架起具体桥。

    body（都可选）：`{"new_name": "...", "text": "...", "return_text": true}`
    """
    ws = workshops.get(ws_id)
    if not ws:
        return _json_response({"error": "工作间不存在"}, status_code=404)
    m = _find_member(ws, member_id)
    if not m:
        return _json_response({"error": "成员不存在"}, status_code=404)
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        body = {}
    if not isinstance(body, dict):
        body = {}
    return await asyncio.to_thread(deliver_one, ws, m,
                                   new_name=str(body.get("new_name") or ""),
                                   text=str(body.get("text") or ""),
                                   return_text=bool(body.get("return_text", True)))



def session_name_for_safe(ws, member) -> str:
    """步骤7 命名的安全包装（名字生成失败也不该让整个流程炸掉）。"""
    try:
        from ..action_scripts import session_name_for
        return session_name_for(ws, member)
    except Exception as e:  # noqa: BLE001
        print(f"[session] 命名生成失败: {e}", flush=True)
        return f"{getattr(ws, 'workshop_id', 'ws')}-{getattr(member, 'role', '成员')}-{getattr(member, 'member_id', 'm')}"


def establish_one(ws, m, *, new_name: str = "") -> dict:
    """步骤 4→7→8→9 的**实现**（同步、不含 HTTP 细节）。

    为什么抽出来：老板流程里这四步是**自动**的，既要有"点一个成员"的单入口，
    也要有"按名单一次走完"的批量入口——两处必须**同一套逻辑**，否则口径迟早分叉。
    **幂等**：重复调用只把名字/引用刷新为同一结果。
    """
    from ..action_scripts import (available_actions, run_action,
                                  session_name_for, write_action_script)
    from ..compartment import ensure_compartment
    from ..server import harness_manager, save_state

    ws_id = ws.workshop_id
    member_id = m.member_id

    hid = _member_harness_id(m)
    sess = harness_manager.sessions.get(hid) if hid else None
    avail = available_actions(sess) if sess else {a: {"configured": False, "mode": "manual",
                                                      "foreground": "required"}
                                                  for a in ("new_session", "rename_session", "deliver")}
    # 步骤 7：名字由**平台**给（用户可用 new_name 覆盖）
    _seq = int(getattr(m, "channel_seq", 1) or 1)
    name = str(new_name or "").strip() or channel_session_name(ws, m, _seq)
    member_dir = ensure_compartment(ws.workspace_dir, m.role, m.member_id)
    sdir = _scripts_dir(ws, m)

    steps: dict = {}
    manual: list = []
    py = os.environ.get("AC_PYTHON") or "python"

    def _prep(action: str) -> dict:
        """渲染该成员的动作脚本（一员工一份），返回路径。"""
        cfg = {}
        if sess is not None:
            from ..action_scripts import get_config
            cfg = ((get_config(sess).get(action) or {}).get("params") or {})
        params = {"HARNESS_ID": hid or "", "HARNESS_NAME": getattr(sess.info, "harness_name", "") if sess and getattr(sess, "info", None) else ""}
        params.update(cfg)
        p = sdir / f"{action}.py"
        write_action_script(action, params, p)
        return {"path": str(p)}

    def _run(action: str, payload: dict, timeout: float = 45.0) -> dict:
        info = _prep(action)
        r = run_action(info["path"], action, payload, timeout=timeout, python_exe=py)
        r["script"] = info["path"]
        return r

    def _run_prepared(action: str, info: dict, payload: dict, timeout: float = 45.0) -> dict:
        r = run_action(info["path"], action, payload, timeout=timeout, python_exe=py)
        r["script"] = info["path"]
        return r

    # ── 步骤 4：新建会话（配了脚本才自动做；否则人工，**不算失败**）──
    # 无论哪种模式都**先把该成员的动作脚本渲染到他的隔间**（一员工一份）：
    # 人工模式下接入者/用户据此补上自己 harness 的命令，比"平台只给一句提示"更有用。
    _sinfo = _prep("new_session")
    _rinfo = _prep("rename_session")
    if avail.get("new_session", {}).get("configured"):
        steps["4_new_session"] = _run_prepared("new_session", _sinfo, {"name_hint": name})
    else:
        steps["4_new_session"] = {"ok": True, "verified": False, "mode": "manual",
                                  "script": _sinfo.get("path", ""),
                                  "error": "该 harness 未配置 new_session 脚本",
                                  "foreground": avail.get("new_session", {}).get("foreground", "required")}
        manual.append({"step": 4, "action": "new_session", "hint": _manual_hint("new_session", name, m)})

    session_ref = dict((steps["4_new_session"].get("evidence") or {}).get("session") or {}) \
        or dict(getattr(m, "session_ref", {}) or {})

    # ── 步骤 8：重命名（把平台给的名字写到会话上）──
    if avail.get("rename_session", {}).get("configured"):
        steps["8_rename_session"] = _run_prepared("rename_session", _rinfo,
                                                 {"new_name": name, "session": session_ref})
    else:
        steps["8_rename_session"] = {"ok": True, "verified": False, "mode": "manual",
                                     "script": _rinfo.get("path", ""),
                                     "error": "该 harness 未配置 rename_session 脚本",
                                     "foreground": avail.get("rename_session", {}).get("foreground", "required")}
        manual.append({"step": 8, "action": "rename_session", "hint": _manual_hint("rename_session", name, m)})

    # ── 步骤 9：把名字写进成员数据（**视觉脚本从这里读，不写死**）──
    # ⚠️ **平台生成的名字永远是身份凭据、必须留在首位**（V25 §4.1）：
    # 用户若给了自定义名，它排后面（用户可改，但别把身份改掉）。
    generated = channel_session_name(ws, m, _seq)   # V28：身份名 = 当前通道的名字
    rest = [a for a in (list(getattr(m, "session_aliases", []) or [])) if a and a != generated]
    if name and name != generated and name not in rest:
        rest.insert(0, name)
    applied = str((steps["8_rename_session"].get("evidence") or {}).get("applied_name") or "")
    if applied and applied != generated and applied not in rest:
        rest.insert(0, applied)
    m.session_aliases = ([generated] + rest)[:8]
    if session_ref:
        m.session_ref = session_ref

    verified = {k: bool(v.get("verified")) for k, v in steps.items()}

    # ★V28 补（2026-10-10 注册-激活实测发现）：**会话真的建成 ⇒ 通道必须同步为 active**
    #   否则出现"会话已建、界面却显示未接入"的自相矛盾（`channel_state` 只在 V28 的
    #   `channel/establish` 里被置 active，而这条**经典路径**（开工自动 / 单点 establish-session）
    #   也建了会话却没有同步状态 ⇒ 实测：三人会话都建好了，通道 state 仍是 pending）。
    #   诚实边界：**只在"步骤4 自证成功"时才置 active**；人工模式（未核实）仍保持 pending（不假装接入）。
    if bool(verified.get("4_new_session")):
        m.channel_state = "active"
        if not float(getattr(m, "channel_since", 0) or 0):
            m.channel_since = time.time()

    try:
        _audit_log.record("workshop.establish_session", actor="user", target=ws_id,
                          detail=f"member={member_id} name={name} verified={verified} "
                                 f"manual={len(manual)}",
                          extra={"member_id": member_id, "name": name, "verified": verified,
                                 "manual_steps": [x["step"] for x in manual],
                                 "harness_id": hid or "", "session_ref": session_ref})
    except Exception:  # noqa: BLE001
        pass
    save_state()
    return {
        "success": True,
        "workshop_id": ws_id, "member_id": member_id, "harness_id": hid or "",
        "name": name,                     # 步骤 7 的产物（用户可复制它去重命名）
        "compartment": member_dir.get("dir", ""),
        "scripts_dir": str(sdir),
        "steps": steps,
        "verified": verified,
        "all_verified": all(verified.values()) if verified else False,
        "manual": manual,                 # 没配脚本的步骤 —— 给人工指引，**不是失败**
        "session_ref": session_ref,
        "session_aliases": m.session_aliases,
        # ★2026-10-15（V51 §3.3）：步骤 9 写完名字**别停在这儿** —— 把"下一步能怎么拉起"
        #   一并给出来，界面才有按钮可点（此前"名字进数据"之后就没有然后了）。
        #   ⚠️这里**不做窗口探测**（probe=False）：探测要起 PowerShell，放在命名这条路径上太贵；
        #     真要看有哪些窗口，调 `GET …/visual-options`。
        "visual_options": _visual_options(m, probe=False),
        "note": ("全部动作由脚本完成并自证" if verified and all(verified.values())
                 else "部分步骤为**人工模式**（未核实）：按 manual 里的指引操作即可；"
                      "平台不会把它当成已完成"),
    }


@router.get("/api/workshop/{ws_id}/member/{member_id}/visual-options")
async def workshop_visual_options(ws_id: str, member_id: str):
    """**只读**：这个成员现在能怎么被"视觉拉起"（给界面按钮的可用性用）。**无任何副作用**。

    对齐 `V51` §2：档位照抄 `V25` §5 —— `wmpaste`（Windows 消息式注入）＋ `clipboard`（半自动兜底）。
    """
    from ..server import Utf8JSONResponse
    ws = workshops.get(ws_id)
    if not ws:
        return Utf8JSONResponse({"error": "工作间不存在"}, status_code=404)
    m = next((x for x in ws.members if x.member_id == member_id), None)
    if not m:
        return Utf8JSONResponse({"error": "成员不存在"}, status_code=404)
    # 列窗口要起子进程（阻塞）⇒ **必须 to_thread**（铁律：async 里绝不许同步阻塞）
    info = await asyncio.to_thread(_visual_options, m)
    return {"success": True, "workshop_id": ws_id, "member_id": member_id, **info}


@router.post("/api/workshop/{ws_id}/member/{member_id}/visual-launch")
async def workshop_visual_launch(ws_id: str, member_id: str, request: Request):
    """**真拉起**：把一段文本送进这个成员的会话（消息式注入优先，失败退剪贴板兜底）。

    body: `{"text": "...", "method": "auto|wmpaste|clipboard"}`（默认 `auto`）
    ⚠️这是**写动作**（会动剪贴板 / 往目标窗口投文本）⇒ 落审计，且**结果如实回报**
    （`ok=False` 时 `detail` 说清为什么、`next_step` 说清人该做什么）。
    """
    from ..server import Utf8JSONResponse
    from ..visual_launch import METHODS, launch as _launch
    ws = workshops.get(ws_id)
    if not ws:
        return Utf8JSONResponse({"error": "工作间不存在"}, status_code=404)
    m = next((x for x in ws.members if x.member_id == member_id), None)
    if not m:
        return Utf8JSONResponse({"error": "成员不存在"}, status_code=404)
    body = await request.json()
    text = str(body.get("text") or "")
    method = str(body.get("method") or "auto").strip().lower()
    if method not in METHODS:
        return Utf8JSONResponse(
            {"error": "未知的拉起方式 %r（可选：%s）" % (method, " / ".join(METHODS))},
            status_code=400)
    if not text.strip():
        return Utf8JSONResponse({"error": "text 不能为空（这是要送进会话的内容）"}, status_code=400)
    result = await asyncio.to_thread(_launch, m, text, method=method)
    try:
        _audit_log.record("workshop.visual_launch", actor="user", target=ws_id,
                          detail=f"member={member_id} method={result.get('method')} "
                                 f"ok={bool(result.get('ok'))} need_human={bool(result.get('need_human'))}",
                          extra={"member_id": member_id, "method": result.get("method"),
                                 "ok": bool(result.get("ok")),
                                 "need_human": bool(result.get("need_human")),
                                 "chars": len(text)})
    except Exception:  # noqa: BLE001
        pass
    return {"success": bool(result.get("ok")), "workshop_id": ws_id, "member_id": member_id,
            **result}


def channel_session_name(ws, m, seq: int) -> str:
    """**通道的会话名**（老板 2026-10-09 拍板：「命名可以更加具体化，可以加序号，可以加职位等等」）。

    第 1 个通道沿用原格式（**向后兼容**：老工作间/既有名字不变）：
        `<工作间id>-<项目>-<职位>-<成员id>`
    第 2 个通道起**追加序号**（用户在 harness 的对话列表里一眼看出是第几个对话）：
        `<工作间id>-<项目>-<职位>-<成员id>-<序号>`
    想再具体，用 `new_name` 追加自己的标注（如「…-2-重做前端」）——**平台生成的部分始终在前**。
    """
    from ..action_scripts import session_name_for   # 与 establish_one 同款（局部导入，避免循环依赖）
    base = session_name_for(ws, m)          # 含项目名与职位（老板要求"带项目名给用户看"）
    try:
        n = int(seq or 1)
    except Exception:  # noqa: BLE001
        n = 1
    return base if n <= 1 else f"{base}-{n}"


def member_channel_allows_dispatch(m) -> tuple[bool, str]:
    """**停用后不得再投消息**（V28 §2 规则 5）——派发处据此把关。

    否则用户会看到"说了停用、消息还照收"的假状态（准则⑤）。
    """
    state = str(getattr(m, "channel_state", "pending") or "pending")
    if state == "retired":
        return False, "该成员的通道**已停用**（消息不再投递）；要继续请先「新建通道」"
    return True, ""


def _archive_bridge_artifacts(comp_dir: str, seq: int) -> dict:
    """V28 §4.1：停用通道时**归档本通道的接入产物**，避免新建通道覆盖旧桥。

    实测事实：接入产物写在隔间内、文件名固定 `bridge.py`，而 `server.py:5891` 的注释
    正好记着一个同型历史 bug（"后写的覆盖先写的"）。故退役时把 `bridge*` 移到
    `<隔间>/channel_<序号>/`，**归档失败要说话**（不假装成功）。
    """
    out = {"moved": [], "errors": [], "dir": ""}
    try:
        base = Path(comp_dir)
        if not base.is_dir():
            out["errors"].append(f"隔间不存在：{comp_dir}")
            return out
        dst = base / f"channel_{int(seq or 1)}"
        items = [q for q in sorted(base.glob("bridge*")) if q.is_file()]
        if not items:
            out["dir"] = ""
            return out
        dst.mkdir(parents=True, exist_ok=True)
        for q in items:
            try:
                target = dst / q.name
                if target.exists():
                    target = dst / f"{q.stem}-{int(time.time())}{q.suffix}"
                q.replace(target)
                out["moved"].append(str(target))
            except Exception as e:  # noqa: BLE001
                out["errors"].append(f"{q.name}: {type(e).__name__}: {e}")
        out["dir"] = str(dst)
    except Exception as e:  # noqa: BLE001
        out["errors"].append(f"{type(e).__name__}: {e}")
    return out


def retire_channel(ws, m, *, reason: str = "") -> dict:
    """**停用当前通道**（老板 2026-10-09 拍板；V28 §2 六条规则）。

    - **停用 ≠ 删除**：隔间、产物、历史**一律不动**，只断"路"（桥归档、队列清空、会话引用释放）；
    - 幂等：当前没有**运行中**的通道 ⇒ 如实回 `action="none"`，**不假装停过**；
    - 写审计（谁/何时/为什么），并把本次退役记进 `retired_channels`（留痕）。
    """
    from ..compartment import compartment_dir
    from ..server import pending_activations, save_state

    state = str(getattr(m, "channel_state", "pending") or "pending")
    seq = int(getattr(m, "channel_seq", 1) or 1)
    if state != "active":
        return {"success": True, "action": "none", "member_id": m.member_id,
                "channel_seq": seq, "channel_state": state,
                "note": "当前没有**运行中**的通道，没有可停用的东西（未做任何改动）"}

    comp = str(compartment_dir(ws.workspace_dir, m.role, m.member_id))
    arch = _archive_bridge_artifacts(comp, seq)

    cleared = 0
    try:
        hid = _member_harness_id(m)
        if hid and hid in pending_activations:
            before = len(pending_activations[hid])
            pending_activations[hid] = [
                a for a in pending_activations[hid]
                if not (a.get("workshop_id") == ws.workshop_id and a.get("member_id") == m.member_id)
            ]
            cleared = before - len(pending_activations[hid])
    except Exception as e:  # noqa: BLE001
        arch["errors"].append(f"清队列失败：{type(e).__name__}: {e}")

    rec = {"seq": seq,
           "name": (list(getattr(m, "session_aliases", []) or []) or [""])[0],
           "session_ref": dict(getattr(m, "session_ref", {}) or {}),
           "retired_at": time.time(), "reason": str(reason or ""),
           "archived": list(arch.get("moved", [])), "queue_cleared": cleared}
    m.retired_channels = (list(getattr(m, "retired_channels", []) or []) + [rec])[-20:]
    m.channel_state = "retired"
    m.session_ref = {}
    m.session = None
    # ★V36 §3.3：停用通道后，那条桥**不再绑着任何会话** —— 必须如实标记，
    #   否则界面会照旧显示"已架接：模板 → 旧会话名"，而那条路其实已经断了（假状态，准则⑤）。
    #   做法：保留 ref（历史可查），但 state 改 `retired`，并记下停用时间与它曾绑的会话。
    _bref = dict(getattr(m, "bridge_ref", {}) or {})
    if _bref:
        _bref["state"] = "retired"
        _bref["retired_at"] = time.time()
        _bref["retired_channel_seq"] = seq
        _bref["retired_session_name"] = _bref.get("session_name", "")
        _bref["verified"] = False          # 断了就不该再声称自证过
        m.bridge_ref = _bref
    if str(getattr(m, "status", "")) in ("entered", "working", "idle", "activating"):
        m.status = "idle"
    try:
        _audit_log.record("workshop.channel_retire", actor="user", target=ws.workshop_id,
                          detail=f"member={m.member_id} channel_seq={seq} reason={reason or '-'} "
                                 f"archived={len(arch.get('moved', []))} queue_cleared={cleared}",
                          extra={"member_id": m.member_id, "channel_seq": seq, "reason": reason or "",
                                 "archived": arch.get("moved", []), "errors": arch.get("errors", [])})
    except Exception:  # noqa: BLE001
        pass
    save_state()
    return {"success": True, "action": "retired", "member_id": m.member_id,
            "channel_seq": seq, "channel_state": "retired",
            "archived": arch, "queue_cleared": cleared,
            "errors": arch.get("errors", []),
            "note": "通道已停用：**隔间与产物未动**（停用≠删除）；要继续该成员请「新建通道」"}


def establish_channel(ws, m, *, new_name: str = "", reason: str = "") -> dict:
    """**新建通道**（换对话；换人走名单变更、复用同一底层）。

    - 当前是 `active` ⇒ **拒绝**（一个成员同时只能有一条通道，否则消息会串）；
    - 序号 +1，名字用 `channel_session_name`（第 2 个起带序号；老板要求名字更具体），
      也可用 `new_name` 追加自定义标注（平台生成部分仍在首位）；
    - 复用 `establish_one`（步骤 4→7→8→9），返回它的形状 **+ 通道信息**。
    """
    from ..server import save_state

    state = str(getattr(m, "channel_state", "pending") or "pending")
    seq = int(getattr(m, "channel_seq", 1) or 1)
    if state == "active":
        return {"success": False,
                "error": "该成员当前已有**运行中**的通道：请先「停用通道」，再新建"
                         "（一个成员同时只能有一条通道，否则消息会串）",
                "channel_seq": seq, "channel_state": state}
    if state == "retired" or list(getattr(m, "session_aliases", []) or []):
        seq += 1                      # 已经有过通道 ⇒ 这是第 N+1 个
    name = str(new_name or "").strip() or channel_session_name(ws, m, seq)

    m.channel_seq = seq               # 先写序号：establish_one 要用它生成"身份名"
    out = establish_one(ws, m, new_name=name)
    ok = bool(out.get("success"))
    m.channel_state = "active" if ok else "retired"
    m.channel_since = time.time() if ok else float(getattr(m, "channel_since", 0) or 0)
    try:
        _audit_log.record("workshop.channel_establish", actor="user", target=ws.workshop_id,
                          detail=f"member={m.member_id} channel_seq={seq} name={name} "
                                 f"verified={out.get('all_verified')} reason={reason or '-'}",
                          extra={"member_id": m.member_id, "channel_seq": seq, "name": name,
                                 "reason": reason or "", "all_verified": bool(out.get("all_verified"))})
    except Exception:  # noqa: BLE001
        pass
    save_state()
    # 换通道后**必须补做步骤 5（送桥）** —— 否则新对话里没有桥、收不到活（V28 §2 规则 4）
    if ok:
        try:
            d = deliver_one(ws, m)
            _d5 = (d.get("steps") or {}).get("5_deliver") or {}
            out.setdefault("steps", {})["5_deliver"] = _d5
            out.setdefault("verified", {})["5_deliver"] = bool(_d5.get("verified"))
            out["all_verified"] = bool(out["verified"]) and all(out["verified"].values())
            out["deliver"] = {"verified": bool(d.get("verified")), "text_path": d.get("text_path", ""),
                              "bridge_path": d.get("bridge_path", ""), "manual": d.get("manual") or []}
        except Exception as e:  # noqa: BLE001
            out["deliver"] = {"error": f"{type(e).__name__}: {e}"}   # 失败要说话，不假装送过
    out["channel_seq"] = seq
    out["channel_state"] = m.channel_state
    out["channel_name"] = name
    # ★V36 §3.3：换通道后**桥要跟着换** —— 新会话必须有桥绑上，否则这条通道收不到活。
    #   做法：先记住停用前那条桥用的模板，再按同一模板重新架桥（绑到**新会话**）。
    #   ⚠️ `do_establish=False`：会话已由上面 establish_one 建好，这里不能再建一次。
    #   ⚠️ 失败要说话：重架失败就**如实记进 out**，绝不假装"桥跟着换好了"。
    if ok:
        _prev = dict(getattr(m, "bridge_ref", {}) or {})
        _prev_tpl = str(_prev.get("template_name") or "")
        _want_reattach = bool(_prev_tpl) or bool(_prev.get("bridge_path"))
        if _want_reattach:
            try:
                _re = attach_bridge_core(ws, m, template_name=_prev_tpl, do_establish=False)
                out["bridge_reattach"] = {
                    "attempted": True,
                    "success": bool(_re.get("success")),
                    "template_name": (_re.get("template") or {}).get("name", _prev_tpl),
                    "bridge_path": _re.get("bridge_path", ""),
                    "session_name": _re.get("session_name", ""),
                    "state": _re.get("state", ""),
                    "error": _re.get("error", "") or _re.get("error_hint", ""),
                }
            except Exception as e:  # noqa: BLE001
                out["bridge_reattach"] = {"attempted": True, "success": False,
                                          "error": f"{type(e).__name__}: {e}"}
        else:
            out["bridge_reattach"] = {
                "attempted": False,
                "note": "该成员原先没有架过具体桥，本次不涉及重架（可在工作间里架桥）",
            }
    return out


def channels_of(ws, m) -> dict:
    """某人历次通道（**只读**）：当前是哪个、各自何时停用、为什么。"""
    seq = int(getattr(m, "channel_seq", 1) or 1)
    hist = list(getattr(m, "retired_channels", []) or [])
    return {"workshop_id": ws.workshop_id, "member_id": m.member_id,
            "current": {"seq": seq, "state": getattr(m, "channel_state", "pending"),
                        "since": float(getattr(m, "channel_since", 0) or 0),
                        "name": (list(getattr(m, "session_aliases", []) or []) or [""])[0],
                        "session_ref": dict(getattr(m, "session_ref", {}) or {})},
            "history": hist,
            "note": "`state`: pending(未接入) / active(运行中) / retired(已停用)；停用≠删除，隔间与产物保留"}


@router.post("/api/workshop/{ws_id}/member/{member_id}/establish-session")
async def establish_session(ws_id: str, member_id: str, request: Request):
    """步骤 4→7→8→9（HTTP 薄壳；实现在 `establish_one`）。

    请求体（全部可选）：`{"new_name": "自定义名字"}` —— 不给就用步骤 7 生成的名字。
    """
    ws = workshops.get(ws_id)
    if not ws:
        return _json_response({"error": "工作间不存在"}, status_code=404)
    m = _find_member(ws, member_id)
    if not m:
        return _json_response({"error": "成员不存在"}, status_code=404)
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        body = {}
    if not isinstance(body, dict):
        body = {}
    # ⚠️ 2026-10-08 自查修：`establish_one` 会**同步跑子进程**（动作脚本，最长 45s/个）——
    # 若在 async 处理器里直接调用，会**阻塞整个事件循环**（同进程的其它请求全被拖住）。
    # 故丢到线程里跑（`asyncio.to_thread`），与"平台不该被一个外端脚本卡住"这一条对齐。
    return await asyncio.to_thread(establish_one, ws, m,
                                   **({"new_name": str(body.get("new_name") or "")}
                                      if body.get("new_name") else {}))


@router.post("/api/workshop/{ws_id}/onboard-members")
async def onboard_members(ws_id: str, request: Request):
    """**按名单一次走完会话建立与命名**（老板流程步骤 4/7/8/9 对全体成员）。

    老板流程里这四步是**自动**的，但此前只有"点某一个成员"的入口 ⇒ 界面上得逐个点。
    本端点让"确认名单之后"可以一键走完；**每个成员的结果仍逐条如实回报**
    （哪几步由脚本自证、哪几步是人工模式），不把"没配脚本"说成已建好（准则⑤）。

    body 可传：`{"member_ids": ["m0","m1"]}` —— 不给即全体成员。
    """
    from ..server import save_state
    ws = workshops.get(ws_id)
    if not ws:
        return _json_response({"error": "工作间不存在"}, status_code=404)
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        body = {}
    if not isinstance(body, dict):
        body = {}
    want = [str(x) for x in (body.get("member_ids") or []) if str(x).strip()]
    members = [m for m in (ws.members or []) if (not want or m.member_id in want)]
    if not members:
        return _json_response({"error": "名单为空（或指定的成员都不存在）"}, status_code=409)

    results, auto_ok, need_human = {}, [], []
    for m in members:
        try:
            # 同上：动作脚本是同步子进程 ⇒ 一律走线程，别卡住事件循环
            r = await asyncio.to_thread(establish_one, ws, m)
        except Exception as e:  # noqa: BLE001
            r = {"success": False, "member_id": m.member_id,
                 "error": f"{type(e).__name__}: {e}"}
        results[m.member_id] = r
        if r.get("success") and r.get("all_verified"):
            auto_ok.append(m.member_id)
        else:
            need_human.append(m.member_id)
    save_state()
    try:
        _audit_log.record("workshop.onboard_members", actor="user", target=ws_id,
                          detail=f"members={len(members)} auto_ok={len(auto_ok)} "
                                 f"need_human={len(need_human)}",
                          extra={"members": [m.member_id for m in members],
                                 "auto_ok": auto_ok, "need_human": need_human})
    except Exception:  # noqa: BLE001
        pass
    return {
        "success": True, "workshop_id": ws_id, "count": len(members),
        "results": results,
        "auto_ok": auto_ok,            # 全部步骤由脚本自证
        "need_human": need_human,      # 有步骤是人工模式（**不是失败**，但要人做）
        "summary": (f"{len(members)} 名成员：{len(auto_ok)} 名全自动完成，"
                    f"{len(need_human)} 名有需人工的步骤"),
    }


@router.post("/api/workshop/{ws_id}/member/{member_id}/channel/retire")
async def channel_retire(ws_id: str, member_id: str, request: Request):
    """**停用通道**（老板 2026-10-09 拍板）。body 可带 `{"reason": "用户不满意|上下文老化|..."}`。"""
    ws = workshops.get(ws_id)
    if not ws:
        return _json_response({"error": "工作间不存在"}, status_code=404)
    m = _find_member(ws, member_id)
    if not m:
        return _json_response({"error": "成员不存在"}, status_code=404)
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        body = {}
    if not isinstance(body, dict):
        body = {}
    return await asyncio.to_thread(retire_channel, ws, m, reason=str(body.get("reason") or ""))


@router.post("/api/workshop/{ws_id}/member/{member_id}/channel/establish")
async def channel_establish(ws_id: str, member_id: str, request: Request):
    """**新建通道**（换对话）。body 可带 `{"new_name": "...", "reason": "..."}`。

    ⚠️ 当前通道仍在运行 ⇒ **拒绝**（先停用再新建）；动作是同步子进程 ⇒ 走线程，别卡事件循环。
    """
    ws = workshops.get(ws_id)
    if not ws:
        return _json_response({"error": "工作间不存在"}, status_code=404)
    m = _find_member(ws, member_id)
    if not m:
        return _json_response({"error": "成员不存在"}, status_code=404)
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        body = {}
    if not isinstance(body, dict):
        body = {}
    return await asyncio.to_thread(establish_channel, ws, m,
                                   new_name=str(body.get("new_name") or ""),
                                   reason=str(body.get("reason") or ""))


@router.get("/api/workshop/{ws_id}/member/{member_id}/channels")
async def member_channels(ws_id: str, member_id: str):
    """**只读**：该成员历次通道（当前哪个 active / 何时停用 / 为什么 / 归档了哪些产物）。"""
    ws = workshops.get(ws_id)
    if not ws:
        return _json_response({"error": "工作间不存在"}, status_code=404)
    m = _find_member(ws, member_id)
    if not m:
        return _json_response({"error": "成员不存在"}, status_code=404)
    out = channels_of(ws, m)
    out["success"] = True
    return out
