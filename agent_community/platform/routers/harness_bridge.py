"""platform/routers/harness_bridge：Harness 桥生成与激活（V-13 自 harness.py 拆分）。

承载 bridge-test / bridge-generate / bridge-path / bridge-verify / prefill / activate / peer-route 等端点。
拆分纪律：路由路径、参数、响应体与拆分前逐字一致（纯搬迁，零行为变更）。
"""
from __future__ import annotations
from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect
from ..protocol import HarnessStatus
from pathlib import Path
from ..ai_external import run_ai_call as ai_external_run_ai_call
import asyncio
from datetime import datetime
from ..harness_adapter import harness_manager
import json
import os
import time
from uuid import uuid4
from .workshops_common import _sv
from ..state import _bridge_tests_inflight, agents, assistant_history, assistant_history_max, pending_activations, pending_bridge_tests, pending_pre_register, pending_tasks, workshops
from ..audit import audit_log as _audit_log

router = APIRouter()

# ── 桥落盘根目录（2026-10-06 提为模块级常量，为的是可被测试隔离）──────────
# 原来在每个用到的地方**按 __file__ 现算**（`Path(__file__).resolve().parent.parent / "bridges"`），
# 于是 `agent_community/tests/conftest.py` 的 `isolate_data_dirs` **patch 不到它** ——
# 测试（test_policy_wiring_bridge 的桩）会真的往**真实仓库**的
# `agent_community/platform/bridges/h1/` 落盘（HANDOVER §13.8 记的那个缺陷）。
# 提成模块级常量后，conftest 直接 monkeypatch 本常量即可隔离；生产行为**曾经**变化——
# ⚠️ 2026-10-11 实测修正：本文件在 <repo>/agent_community/platform/routers/ 下
#   ⇒ 要 `<repo>/agent_community` 必须**上溯 3 层**（routers → platform → agent_community）。
#   原写 `parent.parent` 只到 `.../agent_community/platform`（**注释却写着 agent_community**），
#   于是 `BRIDGES_ROOT` 落到 `platform/bridges`、wake 包装脚本被写到
#   `<repo>/agent_community/agent_community/data/runtime/wake/`（多一层）。
#   权威对照（都在本仓库内、指向 `agent_community/bridges`，磁盘上真实存在且有内容）：
#     · `platform/tools/bridge_tool.py`：`parent.parent.parent / "bridges"`
#     · `platform/bridge_supervisor.py`：拼 `agent_community/bridges`
#   本文件是唯一例外 —— 这属于"提常量时搬错了一层"的静默回归，已修回。
_PKG_ROOT = Path(__file__).resolve().parent.parent.parent   # <repo>/agent_community
BRIDGES_ROOT = _PKG_ROOT / "bridges"                        # <repo>/agent_community/bridges


@router.post("/api/harness/bridge-test")
async def harness_bridge_test(request: Request):
    _audit_log.record("harness.bridge.test", actor="user", target="", detail="桥通道测试")
    from ..server import Utf8JSONResponse, _file_poll_send, _harness_wakeup_method, _pending_push, _register_bridge_test_inflight
    """平台测试桥功能：向指定 harness 的桥发送一条测试消息。
    分发按 wakeup_method：
      - file_poll：写测试 JSON 到 harness 的 inbox（wakeup_dir），由文件桥/对象侧检测
      - 其余：进 pending_bridge_tests 队列，由通用 pending 桥轮询领取
    对象侧收到后应回报 POST /api/harness/bridge-test-result。
    """
    body = await request.json()
    hid = body.get("harness_id", "")
    if not hid:
        return Utf8JSONResponse({"error": "harness_id required"}, status_code=400)
    sess = harness_manager.sessions.get(hid)
    if not sess:
        return Utf8JSONResponse({"error": f"harness {hid} 未注册"}, status_code=404)
    test_id = uuid4().hex[:12]
    payload = {
        "type": "bridge_test",
        "harness_id": hid,
        "test_id": test_id,
        "sent_at": datetime.now().isoformat(),
        "message": "平台桥测试：请确认桥通道正常，然后回报 bridge-test-result",
        "report_endpoint": "/api/harness/bridge-test-result",
    }
    method = _harness_wakeup_method(hid)
    if method == "file_poll":
        ok = _file_poll_send(hid, payload)
        if not ok:
            return Utf8JSONResponse({"error": "写入 inbox 失败（wakeup_dir 不可写）"}, status_code=500)
        print(f"[bridgetest] file_poll 已写 inbox: {hid} test_id={test_id}", flush=True)
    else:
        _pending_push(pending_bridge_tests, hid, payload)
        print(f"[bridgetest] 已入 pending 队列: {hid} test_id={test_id}", flush=True)
    _register_bridge_test_inflight(hid, test_id)  # V-17：登记待回报测试，供回报归属校验
    return {"success": True, "harness_id": hid, "test_id": test_id, "sent_via": method}

@router.get("/api/harness/pending-bridge-tests")
async def harness_pending_bridge_tests(harness_id: str):
    """harness 桥轮询：领取该 harness 名下的桥测试任务。"""
    tests = pending_bridge_tests.pop(harness_id, [])
    return {"tests": tests}

@router.post("/api/harness/bridge-test-result")
async def harness_bridge_test_result(request: Request):
    _audit_log.record("harness.bridge.test_result", actor="user", target="", detail="桥测试回报")
    from ..server import Utf8JSONResponse, save_state
    """harness 桥回报测试结果（对象侧确认桥通道正常）。"""
    body = await request.json()
    hid = body.get("harness_id", "")
    if not hid:
        return Utf8JSONResponse({"error": "harness_id required"}, status_code=400)
    sess = harness_manager.sessions.get(hid)
    if not sess:
        return Utf8JSONResponse({"error": f"harness {hid} 未注册"}, status_code=404)
    # V-17 归属校验：test_id 必须匹配平台近期发出的测试，且该测试未被回报过
    inflight = _bridge_tests_inflight.get(hid)
    body_test_id = str(body.get("test_id") or "").strip()
    if not inflight or inflight.get("test_id") != body_test_id:
        print(f"[安全][bridgetest] {hid} 回报 test_id={body_test_id!r} 与平台发出记录不匹配，已拒绝", flush=True)
        return Utf8JSONResponse(
            {"error": "桥测试回报不匹配：test_id 与平台发出的测试不一致（请先由平台发起测试）"},
            status_code=403,
        )
    if inflight.get("reported"):
        print(f"[安全][bridgetest] {hid} 重复回报 test_id={body_test_id}，已拒绝", flush=True)
        return Utf8JSONResponse({"error": "该测试已回报过，拒绝重复提交"}, status_code=403)
    if time.time() > inflight.get("expire_ts", 0):
        _bridge_tests_inflight.pop(hid, None)
        print(f"[安全][bridgetest] {hid} 回报超期（test_id={body_test_id}），已拒绝", flush=True)
        return Utf8JSONResponse({"error": "测试回报已过期，请重新发起桥测试"}, status_code=403)
    inflight["reported"] = True
    ok = bool(body.get("ok"))
    record = {
        "test_id": body.get("test_id", ""),
        "ok": ok,
        "echo": body.get("echo", ""),
        "at": datetime.now().isoformat(),
    }
    sess.metadata["bridge_test"] = record
    # 同步到 info.metadata 以便 save_state 持久化（session 层 metadata 不落盘）
    sess.info.metadata = dict(sess.info.metadata or {})
    sess.info.metadata["bridge_test"] = record
    if ok:
        sess.info.bridge_status = "tested"
    print(f"[bridgetest] {hid} 测试结果: ok={ok} echo={body.get('echo','')[:60]}", flush=True)
    save_state()
    return {"success": True, "harness_id": hid, "ok": ok}

@router.post("/api/harness/{harness_id}/verify")
async def harness_verify_bridge(harness_id: str, request: Request):
    """★V42 F1~F3：**在大厅里把一条接入真跑通**（低耗验证间；不用碰真实工作间）。

    老板口径（2026-10-11）：「大厅注册时，给到一个**等价低耗工作间模型**进行架桥等等测试」
    「**让它先跑通才写入具体模板里**，然后测试的再清理掉」

    它做三件：① 建/取该 harness 的验证间（**各自一个**）
              ② （没有模板时先派生草稿，解开"要先有模板才能架桥"的死锁）→ 架桥 → **真自检**
              ③ **跑通了才**回写模板并标记"已验证"（`verified_at` + 证据）

    body（可选）：`{"wait": 8, "teardown": true}`
      · `teardown=true` ⇒ 验证完立刻**拆掉验证间**（老板：「连桥也拆掉，只留模板」）。
    """
    from ..server import Utf8JSONResponse, _stop_bridge_processes
    from .. import verify_sandbox as _vs
    from ..state import workshops as _workshops
    from ..server import DATA_DIR as _DATA_DIR

    sess = harness_manager.sessions.get(harness_id)
    if not sess:
        return Utf8JSONResponse({"error": f"harness {harness_id} 未注册"}, status_code=404)
    try:
        body = await request.json()
    except Exception:
        body = {}
    body = body if isinstance(body, dict) else {}
    try:
        wait = float(body.get("wait", 8))
    except Exception:  # noqa: BLE001
        wait = 8.0

    try:
        out = await _vs.verify_bridge(_workshops, harness_id,
                                      data_dir=str(_DATA_DIR),
                                      harness_name=getattr(sess.info, "harness_name", ""),
                                      wait=wait)
    except _vs.SandboxError as e:
        return Utf8JSONResponse({"success": False, "error": str(e), "hint": e.hint},
                                status_code=int(e.code or 400))

    # ★门槛：**只有跑通（state=ok）才回写模板**（老板："先跑通才写入具体模板里"）
    committed = None
    if out.get("success"):
        try:
            committed = _vs.commit_verified(_workshops, harness_id, evidence={
                "probe_id": (out.get("detail") or {}).get("probe_id"),
                "state": out.get("state"),
                "sandbox_id": out.get("sandbox_id"),
                # ★2026-10-12：**把"走的是哪条探针通道"一起记进去** ——
                #   否则 `commit_verified` 分不清"验的是完整链路"还是"只验了桥配置"，
                #   会把 verified 说过头（见 `commit_verified` 里的 `verified_scope`）。
                "channel": (out.get("detail") or {}).get("channel"),
                "bridge_kind": out.get("bridge_kind"),
            })
        except Exception as e:  # noqa: BLE001
            committed = {"success": False, "error": f"回写失败：{e}"}
    do_teardown = bool(body.get("teardown", False))
    torndown = None
    if do_teardown:
        torndown = await _vs.teardown(_workshops, harness_id,
                                      stop_bridge=lambda hid, bd: _stop_bridge_processes(hid, bd))
    out["committed"] = committed
    out["torndown"] = torndown
    out["gate_note"] = ("✅ 跑通 ⇒ 已把实测配置写进模板并标记已验证"
                        if (committed or {}).get("success")
                        else "⛔ 没跑通 ⇒ **没有**写进模板（老板口径：先跑通才写入）")
    _audit_log.record("harness.verify", actor="user", target=harness_id,
                      detail=f"验证间自检 state={out.get('state')} 写模板={bool((committed or {}).get('success'))}",
                      extra={"harness_id": harness_id, "state": out.get("state", ""),
                             "sandbox_id": out.get("sandbox_id", ""), "teardown": do_teardown})
    return out


@router.post("/api/harness/{harness_id}/probe")
async def harness_probe_endpoint(harness_id: str, request: Request):
    """★V42 §4.5：**平台侧主动探测** —— 由平台去测这个外端 agent 有什么接口。

    老板原话（2026-10-11 22:30）：「要是平台有 agent 可以去拉桥就好了，这样就不怕外端 agent
    因为**沙箱等限制无法检测自身接口**了」

    为什么需要它：外端 agent 常被沙箱限制，**看不到自己的进程/端口/路径**
    （实测：一个**桌面型**外端 agent 被问"怎么被叫醒"回"我什么接口都没有" —— 那是**它看不到**）。
    平台跑在宿主上、看得见它。（★对外源码不写具体产品名：规则 6「对外文档与示例一律用虚拟名」。）

    **纪律**（都写进代码与返回体）：
      · **用户点了才探**（本端点不自我调度、无后台轮询）；
      · **零 token**：全程脚本探测，**不调用任何 AI**（返回体里 `token_cost: 0`）；
      · **只给线索、不下定论** —— 要写进模板必须**跑通一次**（自检通过）。

    body（可选）：`{"do_http": true}` —— 是否连 HTTP 端点也探（默认探）。
    """
    from ..server import Utf8JSONResponse
    from .. import harness_probe as _hp
    sess = harness_manager.sessions.get(harness_id)
    if not sess:
        return Utf8JSONResponse({"error": f"harness {harness_id} 未注册"}, status_code=404)
    try:
        body = await request.json()
    except Exception:
        body = {}
    body = body if isinstance(body, dict) else {}
    # 线索可从请求体显式给（外端 agent 最清楚自己叫什么进程/开什么端口），
    # 也可以此前存在 harness.metadata["probe_hints"] 里（见 probe_harness 的合并口径）。
    out = _hp.probe_harness(
        sess.info, do_http=bool(body.get("do_http", True)),
        hints_in={k: body.get(k) for k in ("process_hint", "port_hint", "path_hint")
                  if body.get(k) not in (None, "")})
    out["harness_id"] = harness_id
    out["how_to_use"] = ("这些是**线索**：把它交给外端 agent 让它确认，"
                         "或直接在「验证间」里架桥跑一次 —— **跑通了才写进模板**。")
    _audit_log.record("harness.probe", actor="user", target=harness_id,
                      detail=f"平台侧主动探测：候选 {len(out.get('candidates') or [])} 条（零 token）",
                      extra={"harness_id": harness_id,
                             "candidates": [c.get("kind") for c in (out.get("candidates") or [])]})
    return out


@router.get("/api/harness/{harness_id}/wake")
async def harness_wake_get(harness_id: str):
    """★V38 第 2 步：**这个 harness 现在是靠什么被叫醒的**（只读）。

    老板 2026-10-11 的真缺口：桥在跑、消息送到了，**但 agent 不会被叫醒** ——
    因为生成桥里的触发命令是空的。界面据此显示"配了没 / 配的是什么"。
    """
    from ..server import Utf8JSONResponse
    from .. import bridge_wake as _bw
    sess = harness_manager.sessions.get(harness_id)
    if not sess:
        return Utf8JSONResponse({"error": f"harness {harness_id} 未注册"}, status_code=404)
    root = str(_PKG_ROOT.parent)          # <repo>（runtime 目录挂在仓库根下）
    cur = _bw.read_wrapper(root, harness_id)
    return {
        "success": True,
        "harness_id": harness_id,
        "configured": bool(cur.get("configured")),
        "wrapper_path": cur.get("path", ""),
        "command": cur.get("command", ""),
        # 人话：界面直接显示
        "status_text": ("已配：收到任务会执行「%s」来叫醒它" % cur.get("command", "")
                        if cur.get("configured")
                        else "未配：桥只会把消息落盘，**不会叫醒你的 agent**"),
        "why": "平台猜不到你的 agent 怎么被拉起 —— 这一项只能由你提供",
    }


@router.post("/api/harness/{harness_id}/wake")
async def harness_wake_set(harness_id: str, request: Request):
    """★V38 第 2 步（写）：**配一条"叫醒命令"**，平台把它包成脚本（免引号地狱）。

    body：`{"command": "把 agent 拉起来的那条命令"}`

    ⚠️ 安全边界（与 `connect-artifact` 同纪律）：
      - **不接受任何路径字段**（400 且不读）—— 包装脚本只写在平台的 `data/runtime/wake/`；
      - 命令只当**脚本内容**用，校验：非空、禁换行（防多行注入）、限长。
    """
    from ..server import Utf8JSONResponse, save_state
    from .. import bridge_wake as _bw
    sess = harness_manager.sessions.get(harness_id)
    if not sess:
        return Utf8JSONResponse({"error": f"harness {harness_id} 未注册"}, status_code=404)
    try:
        body = await request.json()
    except Exception:
        return Utf8JSONResponse({"error": "请求体不是合法 JSON"}, status_code=400)
    body = body if isinstance(body, dict) else {}
    for _danger in ("path", "dir", "out_dir", "target_dir", "wrapper_path"):
        if _danger in body:
            return Utf8JSONResponse(
                {"error": f"本端点不接受路径字段 {_danger!r} —— 包装脚本的落点由平台派生"},
                status_code=400,
            )
    cmd = str(body.get("command") or "")
    # ★★2026-10-12 **实测修一处"向用不到的人索要命令"**（老板真机实测第一手材料）：
    #   手工/人工中转型（`clibpoard`）**本来就没有"叫醒命令"** —— 它的叫醒方式是
    #   **用户把任务手动粘贴进来**。而本端点原先对空命令直接 400（`validate_command`
    #   要求非空），于是外端 agent 走到第 2 步就卡死，只能如实回"此步不适用"。
    #   ⇒ 与"file_poll 不该被索要 acp_command"同一原则：**不需要就不要**，
    #     并且**说清这一型为什么不需要**（准则⑤：失败/不适用都要说话，不能默默卡住）。
    _wm = str(getattr(getattr(sess.info, "wakeup_method", None), "value",
                      getattr(sess.info, "wakeup_method", "")) or "")
    if not cmd.strip() and _wm == "clipboard":
        sess.info.metadata = dict(sess.info.metadata or {})
        sess.info.metadata["wake_wrapper"] = {
            "path": "", "kind": "manual", "command": "",
            "mode": "manual",
            "note": "人工中转型：没有叫醒命令；任务由用户手动粘贴给该 agent",
        }
        try:
            save_state()
        except Exception:  # noqa: BLE001
            pass
        return {
            "success": True, "harness_id": harness_id, "mode": "manual",
            "command": "", "wrapper_path": "",
            "note": ("这是**人工中转型**（clipboard）：它没有「叫醒命令」可配 —— "
                     "平台的桥会把要送的内容整理好，由**用户手动粘贴**给这个 agent。"
                     "这一步对它来说已完成，不必填命令。"),
            "next": "POST /api/harness/auto-connect 起桥（人工模式会返回 manual，属正常）",
        }
    # 落点由 bridge_wake 依 server.DATA_DIR 决定（这里不再传仓库根：
    #   传了也没用，且会让仓库布局变成落点依据 —— 2026-10-12 已收敛为单一真相）。
    w = _bw.write_wrapper("", harness_id, cmd)
    if not w.get("ok"):
        # 失败要说话：原因 + 怎么改
        return Utf8JSONResponse({"error": w.get("error", "写入失败"), "hint": w.get("fix", "")},
                                status_code=400)
    # 记进 harness metadata，随注册信息一起持久化（界面/桥都读得到）
    try:
        sess.info.metadata = dict(sess.info.metadata or {})
        sess.info.metadata["wake_wrapper"] = {"path": w["path"], "kind": w.get("kind", ""),
                                              "command": cmd.strip()}
        save_state()
    except Exception as e:  # noqa: BLE001
        print(f"[wake] 记 metadata 失败（不影响包装脚本）: {e}", flush=True)
    _audit_log.record("harness.wake.set", actor="user", target=harness_id,
                      detail=f"包装脚本={w.get('kind')} 命令长度={len(cmd.strip())}",
                      extra={"harness_id": harness_id, "wrapper_kind": w.get("kind", "")})
    return {
        "success": True,
        "harness_id": harness_id,
        "wrapper_path": w["path"],
        "kind": w.get("kind", ""),
        "command": cmd.strip(),
        "next": "现在跑一次自检（/api/workshop/{ws}/member/{mid}/self-check）确认它真的会被叫醒",
    }


@router.get("/api/role-catalog")
async def role_catalog_get():
    """★职业（职位）表 —— **一处真相**（老板 2026-10-11「职业列表落后了」）。

    改前：给 AI 的提示词里**写死**「职业从 组长/码农/画师/搜索者 里选」⇒
    选不出产品/测试/运维/数据这类角色（做非"写代码画图"的项目就不够用）。
    现在职业表在 `platform/role_catalog.py`，提示词与界面**都取这里**（规则 20）。

    ⚠️ 角色是**自由文本**（`WorkshopMember.role` 就是字符串）⇒ 本端点只给**推荐**，
       界面可展示为候选/提示，**不得**当成白名单拦住用户自己填。
    """
    from .. import role_catalog as _rc
    return {
        "success": True,
        "required": _rc.REQUIRED_ROLE,          # 「组长」必须有（平台流程依赖）
        "count": len(_rc.role_names()),
        "roles": _rc.list_roles(),
        "groups": _rc.groups(),
        "legacy_aliases": dict(_rc.LEGACY_ALIASES),
        "note": "职业是自由文本：这些只是**推荐**，用户可按任务自己命名",
        "source_of_truth": "platform/role_catalog.py（提示词与界面都引这里）",
    }


@router.get("/api/bridge-catalog")
async def bridge_catalog_list():
    """★V38 第 1/2 步：**四种桥型的"该填什么"** —— 接入这件事的**单一真相**。

    界面话术、文档、模板校验、注册引导**都取这里**（规则 20：整体同步更新 ——
    一件事的对外表达只能有一处真相，不许各写一套）。

    每一型都回答四问：**谁提供 / 怎么填 / 怎么自证 / 常见坑**。
    ⚠️ 其中 `provides=="user"` 的字段就是**必须问用户的那几项**
    （如「怎么叫醒你的 agent」—— 平台猜不到，实测不配就会"唤不醒"）。
    """
    from .. import bridge_catalog as _bc
    return {"success": True, "count": len(_bc.list_kinds()),
            "kinds": _bc.list_kinds(), "truth": _bc.truth_summary()}


@router.get("/api/bridge-catalog/{kind}")
async def bridge_catalog_describe(kind: str):
    """某一型的完整"该填什么"（界面第 2 步渲染、话术生成**都取这里**）。"""
    from ..server import Utf8JSONResponse
    from .. import bridge_catalog as _bc
    d = _bc.describe(kind)
    if not d:
        return Utf8JSONResponse(
            {"error": f"不支持的桥型：{kind}",
             "hint": "可用 GET /api/bridge-catalog 看全部可选桥型",
             "available": [k["kind"] for k in _bc.list_kinds()]},
            status_code=404,
        )
    return {"success": True, "spec": d}


@router.get("/api/harness/bridge-templates")
async def harness_bridge_templates():
    """列出平台内置桥模板库（按接口类型划分），供平台 Agent / 外部选择模板。"""
    from ..bridge_factory import list_templates
    templates = list_templates()
    return {"success": True, "count": len(templates), "templates": templates}


# ── V36 §3.1「具体桥模板」（多条，per-harness）──────────────────────────────
# ⚠️ 与上面 `/api/harness/bridge-templates` 的区别（别混）：
#    · GET  /api/harness/bridge-templates              → **平台内置 L1 传输层模板库**（4 种，只读）
#    · GET  /api/harness/{hid}/bridge-templates        → **该 harness 的具体桥模板**（V36，可多条、可编辑）
#    · POST /api/harness/{hid}/bridge-template         → 新增/覆盖一条（同名即覆盖）
# 判定依据：V35 §10.1 裁定 3「不限条数」+ 裁定 1「手工填必须能走通」。


@router.get("/api/harness/{harness_id}/bridge-templates")
async def harness_bridge_templates_list(harness_id: str):
    """列出该 harness 的**具体桥模板**（V36 §3.1）。

    副作用说明（如实写清）：读的时候会**顺带把旧的单数键迁移进列表**（V36 §4.1）——
    这是"读到即迁移"的既定口径，迁移是幂等的、且**保留原单数键**（旧读取方不受影响）。
    """
    from ..server import Utf8JSONResponse
    from .. import bridge_templates as _bt
    sess = harness_manager.sessions.get(harness_id)
    if not sess:
        return Utf8JSONResponse({"error": f"harness {harness_id} 未注册"}, status_code=404)
    items = _bt.list_all(sess.info)
    return {
        "success": True,
        "harness_id": harness_id,
        "count": len(items),
        "templates": items,
        "status": _bt.status_of(sess.info),
    }


#: V41 裁定 1B：被收敛掉的通用桥模板的历史（存在 harness.metadata 下，
#: **不放进模板 dict** —— 那个 dict 会被 `_normalize_one` 规范化并丢掉未知键）
_HISTORY_KEY = "bridge_template_history"


def _collapse_to_single(info, keep: str) -> list:
    """★V41 裁定 1B：把该 harness 的通用桥模板**收敛为一条**（`keep` 那条）。

    做法：其余各条**不删除**，而是挂到 `keep` 那条的 `superseded` 列表里
    （保留 name/kind/match/updated_at，可回看、可追溯），并从活动列表移除。

    ⚠️ 为什么放在这里而不是 `bridge_templates.put`：`put` 是**存储层契约**
    （"同名覆盖、可并存"有 17 条测试依赖它）；"只允许一条"是**产品裁定**，
    属 API 层语义 ⇒ 在这里收敛，两边都不失守。
    返回被收敛掉的模板名列表（供响应如实告知用户）。
    """
    from .. import bridge_templates as _bt
    items = _bt.list_all(info)
    others = [t for t in items if t.get("name") != keep]
    if not others or _bt.get(info, keep) is None:
        return []
    # ⚠️ 历史**不能塞进模板 dict**：`_normalize_one` 只保留契约内的键，
    #    未知键（如 `superseded`）会在下一次读写时被丢掉（实测踩到）。
    #    ⇒ 存在 harness 自己的 metadata 下（独立键，不碰存储层契约）。
    meta = info.metadata if isinstance(getattr(info, "metadata", None), dict) else {}
    hist = list(meta.get(_HISTORY_KEY) or [])
    for t in others:
        hist.append({"name": t.get("name"), "kind": t.get("kind"),
                     "match": t.get("match"), "params": t.get("params"),
                     "source": t.get("source"), "updated_at": t.get("updated_at"),
                     "superseded_at": time.time(),
                     "why": "V41 裁定 1B：一个 harness 只保留一条通用桥模板"})
        _bt.delete(info, t.get("name"))
    meta[_HISTORY_KEY] = hist
    return [t.get("name") for t in others]


@router.post("/api/harness/{harness_id}/bridge-template")
async def harness_bridge_template_put(harness_id: str, request: Request):
    """新增/覆盖一条**具体桥模板**（V36 §3.1）。同名即覆盖。

    body: `{"name":"...", "kind":"cli_acp|file_poll|pending_poll|script_framework",
            "path":"具体落点路径", "params":{...}, "match":{"workshop":null,"role":null}}`
    `match` 缺省 = **全局可用**（裁定 2）——不得因未指定而拒绝。
    """
    from ..server import Utf8JSONResponse, save_state
    from .. import bridge_templates as _bt
    sess = harness_manager.sessions.get(harness_id)
    if not sess:
        return Utf8JSONResponse({"error": f"harness {harness_id} 未注册"}, status_code=404)
    try:
        body = await request.json()
    except Exception:
        return Utf8JSONResponse({"error": "请求体不是合法 JSON"}, status_code=400)
    if not isinstance(body, dict):
        return Utf8JSONResponse({"error": "请求体必须是 JSON 对象"}, status_code=400)
    name_in = str(body.get("name") or "").strip()
    existed = bool(name_in) and _bt.get(sess.info, name_in) is not None
    try:
        one = _bt.put(sess.info, body, source=_bt.SOURCE_USER)
    except _bt.BridgeTemplateError as e:
        # 失败要说话：中文、说清哪不对
        return Utf8JSONResponse({"error": str(e), "hint": _bridge_template_hint(body)}, status_code=400)
    # ★V41 P3「叫醒命令只有一处真相」（2026-10-11，实测分叉后修）：
    #   此前两处存法：①模板 `params.TRIGGER_CMD`（用户在注册页填的**原始命令**）
    #                ②harness `metadata.wake_wrapper`（**包装脚本路径** —— 桥实际用的是这个）。
    #   实测分叉：只填模板 TRIGGER_CMD ⇒ `GET …/wake` 仍报"未配"、桥**不会叫醒 agent**
    #   （`status_text` 甚至明说"不会叫醒你的 agent"）⇒ 用户配了却不生效。
    #   ⇒ **在保存模板这个入口把 ① 自动落到 ②**：用户只填一处，平台负责一致。
    _trigger_cmd = str(((one or {}).get("params") or {}).get("TRIGGER_CMD") or "").strip()
    _wake_synced = {}
    if _trigger_cmd:
        try:
            from .. import bridge_wake as _bw
            # 同上：落点只认 server.DATA_DIR（2026-10-12 收敛）
            _wr = _bw.write_wrapper("", harness_id, _trigger_cmd)
            if _wr.get("ok"):
                sess.info.metadata = dict(sess.info.metadata or {})
                sess.info.metadata["wake_wrapper"] = {
                    "path": _wr["path"], "kind": _wr.get("kind", ""), "command": _trigger_cmd,
                    "source": "bridge_template.TRIGGER_CMD",
                }
                _wake_synced = {"path": _wr["path"], "kind": _wr.get("kind", ""), "command": _trigger_cmd}
            else:
                _wake_synced = {"error": _wr.get("error") or "包装脚本写入失败",
                                "hint": _wr.get("fix") or ""}
        except Exception as _e:  # noqa: BLE001 — 同步失败不阻断存模板，但要如实回报
            _wake_synced = {"error": f"{type(_e).__name__}: {_e}"}

    # ★V41 裁定 1B（老板 2026-10-11 18:13）：**一个 harness 只允许一条通用桥模板**。
    #   落法：本次写入的那条即"那唯一一条"；若此前并存多条 ⇒ **不静默删**，
    #   把其余移入本条的 `superseded`（保留可回看，与"停用≠删除"一致），并如实告知。
    _superseded = _collapse_to_single(sess.info, keep=one.get("name", ""))
    # 覆盖前备份：与资料库同一纪律（改前留痕，可回溯）
    if existed:
        _backup_bridge_templates(sess.info)
    save_state()
    _audit_log.record(
        "harness.bridge_template.put", actor="user", target=harness_id,
        detail=f"{'覆盖' if existed else '新增'}具体桥模板 name={one['name']} kind={one['kind']} "
               f"match={one['match']}",
        extra={"harness_id": harness_id, "name": one["name"], "kind": one["kind"],
               "replaced": existed},
    )
    return {
        "success": True,
        "harness_id": harness_id,
        "template": one,
        "replaced": existed,           # 让界面能如实说"覆盖了原来的"
        "status": _bt.status_of(sess.info),
        # ★V41 裁定 1B：如实告知"收敛为一条"这件事（不静默）
        "single_template": True,
        "superseded": _superseded,
        "note": (f"按裁定：一个 harness 只保留一条通用桥模板；"
                 f"原有的 {'、'.join(_superseded)} 已移入历史（可回看，未删除）"
                 if _superseded else "已保存（该 harness 唯一的一条通用桥模板）"),
        # ★V41 P3：如实回报"叫醒命令"的去向（用户只填一处，平台保证它真的生效）
        "wake_synced": _wake_synced,
        "wake_note": ("模板里的 TRIGGER_CMD 已同步为**桥真正使用的那一处**（包装脚本）："
                      "这样你只填一次，不会再出现'填了却不生效'"
                      if _wake_synced.get("path")
                      else ("⚠ 你填了 TRIGGER_CMD，但**同步失败**："
                            + str(_wake_synced.get("error") or "未知原因")
                            + ("；怎么改：" + str(_wake_synced.get("hint")) if _wake_synced.get("hint") else "")
                            if _wake_synced.get("error") else "")),
    }


@router.delete("/api/harness/{harness_id}/bridge-template/{name}")
async def harness_bridge_template_delete(harness_id: str, name: str):
    """删一条具体桥模板（V36 §3.1）。**不删已架起的桥产物**、**不动旧单数键**。"""
    from ..server import Utf8JSONResponse, save_state
    from .. import bridge_templates as _bt
    sess = harness_manager.sessions.get(harness_id)
    if not sess:
        return Utf8JSONResponse({"error": f"harness {harness_id} 未注册"}, status_code=404)
    if not _bt.delete(sess.info, name):
        return Utf8JSONResponse(
            {"error": f"没有名为「{name}」的具体桥模板", "hint": "可用 GET …/bridge-templates 查看现有模板名"},
            status_code=404,
        )
    save_state()
    _audit_log.record("harness.bridge_template.delete", actor="user", target=harness_id,
                      detail=f"删除具体桥模板 name={name}",
                      extra={"harness_id": harness_id, "name": name})
    return {"success": True, "harness_id": harness_id, "deleted": name, "status": _bt.status_of(sess.info)}


def _bridge_template_hint(body: dict) -> str:
    """校验失败时给"怎么改"的指引（准则⑤：失败要说话 + 规则 17 自服务）。"""
    return (
        "模板名不能为空、不能含 \\ / : * ? \" < > | 这些字符；"
        "kind 只支持 cli_acp / file_poll / pending_poll / script_framework；"
        "match 可以整个不填（= 任意工作间可用）。"
    )


def _backup_bridge_templates(info) -> None:
    """把当前的多条模板另存一份到 metadata['bridge_templates_history']（保留最近 5 份）。

    与资料库的 `model.history/` 同一思路：**覆盖前留痕**，出问题能回溯。
    失败不阻塞主流程（备份是附加保障，不是写入前置条件）。
    """
    try:
        import copy
        meta = info.metadata if isinstance(getattr(info, "metadata", None), dict) else None
        if meta is None:
            return
        snap = copy.deepcopy(meta.get("bridge_templates") or [])
        if not snap:
            return
        hist = meta.get("bridge_templates_history")
        if not isinstance(hist, list):
            hist = []
        hist = (hist + [{"at": time.time(), "templates": snap}])[-5:]
        meta["bridge_templates_history"] = hist
    except Exception as e:
        print(f"[bridge_template] 备份失败（不阻塞写入）: {e}", flush=True)


@router.post("/api/harness/{harness_id}/bridge/generate")
async def harness_bridge_generate(harness_id: str, request: Request):
    _audit_log.record("harness.bridge.generate", actor="user", target=harness_id, detail="生成桥脚本（落盘）")
    from ..server import Utf8JSONResponse, _platform_base_url, save_state
    """平台标准构桥 API：按 harness 注册信息渲染内置模板生成桥脚本。
    治本设计：平台内置桥模板库（cli_acp / file_poll / pending_poll 等），此处只做模板复制+配置注入的机械操作，
    不依赖 LLM 自由设计。生成后自动登记桥坐标（bridge_dir）。
    body: {"template": "file_poll", "out_dir": "可选输出目录"}
    """
    from ..bridge_factory import generate, safe_slug, list_templates, BridgeTemplateError
    try:
        body = await request.json()
    except Exception:
        body = {}
    template = (body.get("template") or "cli_acp").strip()
    out_dir = (body.get("out_dir") or "").strip()
    sess = harness_manager.sessions.get(harness_id)
    if not sess:
        return Utf8JSONResponse({"error": f"harness {harness_id} 未注册"}, status_code=404)
    available = {t["name"] for t in list_templates()}
    if template not in available:
        return Utf8JSONResponse({"error": f"模板不存在: {template}，可用: {sorted(available)}"}, status_code=400)
    info = sess.info
    # 平台自址：桥用同一地址回连
    try:
        platform_url = str(request.base_url).rstrip("/")
    except Exception:
        platform_url = _platform_base_url()
    # 按模板 interface_type 组装参数（模板 required_fields 兜底校验）
    if template == "cli_acp":
        acp_command = (info.acp_command or "").strip()
        if not acp_command:
            return Utf8JSONResponse(
                {"error": f"harness {harness_id} 未配置 acp_command，无法生成 cli_acp 桥"},
                status_code=400,
            )
        ai = info.ai
        params = {
            "HARNESS_ID": harness_id,
            "ACP_COMMAND": acp_command,
            "ACP_CWD": (info.acp_cwd or "").strip() or str(Path(__file__).resolve().parent.parent),
            "MODEL_NAME": (getattr(ai, "model_name", "") if ai else "") or "unknown-model",
            "PROVIDER": (getattr(ai, "provider", "") if ai else "") or "unknown",
            "DESCRIPTION": (getattr(ai, "description", "") if ai else "") or f"agent_community harness {harness_id}",
        }
    elif template == "file_poll":
        wakeup_dir = (info.wakeup_dir or "").strip()
        if not wakeup_dir:
            return Utf8JSONResponse(
                {"error": f"harness {harness_id} 未配置 wakeup_dir（inbox），无法生成 file_poll 桥"},
                status_code=400,
            )
        params = {
            "HARNESS_ID": harness_id,
            "INBOX_DIR": wakeup_dir,
            "TRIGGER_CMD": (body.get("trigger_cmd") or "").strip(),
            "PLATFORM_URL": platform_url,
        }
    elif template == "pending_poll":
        work_dir = (body.get("work_dir") or "").strip()
        if not work_dir:
            work_dir = str(BRIDGES_ROOT / safe_slug(harness_id))
        params = {
            "HARNESS_ID": harness_id,
            "WORK_DIR": work_dir,
            "TRIGGER_CMD": (body.get("trigger_cmd") or "").strip(),
            "PLATFORM_URL": platform_url,
        }
    else:
        return Utf8JSONResponse({"error": f"暂不支持自动生成模板: {template}"}, status_code=400)
    bridges_root = BRIDGES_ROOT          # 模块级常量，测试可 monkeypatch（见文件头说明）
    if out_dir:
        # V-6 修复：out_dir 必须位于平台 bridges 根目录内，防任意文件写入
        p = Path(out_dir)
        if not p.is_absolute():
            p = bridges_root / p
        try:
            p.resolve().relative_to(bridges_root.resolve())
        except (ValueError, OSError):
            return Utf8JSONResponse(
                {"error": f"out_dir 必须位于平台 bridges 目录内: {bridges_root}"},
                status_code=400,
            )
        target = p
    else:
        target = bridges_root / safe_slug(harness_id)
    # ── V-14 策略闸门（必须在 generate 之前：它会真的 write_text 落盘）──
    # 实测依据：bridge_factory.generate() 内部 `bridge_file.write_text(text)`，
    # 端点函数体里看不到写操作 —— 只看端点体会误判成「仅返回数据」。
    # 行为是「写盘但不启动」→ 按拍板口径取严，出厂 ASK；AI 发起时同样要人批。
    from ..policy import (
        BRIDGE_WRITE,
        actor_from_request,
        blocked_message,
        check,
        pending_message,
    )
    _ev, _pending = check(
        BRIDGE_WRITE,
        target=f"{harness_id}:{template}",
        actor=actor_from_request(request),
    )
    if _ev.blocked:
        return Utf8JSONResponse(
            {"error": blocked_message(_ev), "rule_id": _ev.rule_id}, status_code=403
        )
    if _ev.needs_approval:
        return Utf8JSONResponse(
            {"status": "pending_approval", "op": _ev.op,
             "message": pending_message(_ev, _pending), "pending": _pending},
            status_code=202,
        )

    try:
        bridge_file = generate(template, params, target)
    except BridgeTemplateError as e:
        return Utf8JSONResponse({"error": f"桥生成参数校验失败: {e}"}, status_code=400)
    except Exception as e:
        return Utf8JSONResponse({"error": f"桥生成失败: {e}"}, status_code=500)
    # 自动登记桥坐标
    sess.info.bridge_dir = str(target)
    if sess.info.bridge_status != "tested":
        sess.info.bridge_status = "reported"
    save_state()
    return {
        "success": True,
        "harness_id": harness_id,
        "template": template,
        "bridge_file": str(bridge_file),
        "bridge_dir": str(target),
        "bridge_status": sess.info.bridge_status,
        "launch_hint": f'python -u "{bridge_file}" --url {_platform_base_url()}',
    }

@router.post("/api/harness/bridge-path")
async def harness_bridge_path(request: Request):
    _audit_log.record("harness.bridge.path", actor="user", target="", detail="登记桥路径")
    from ..server import Utf8JSONResponse, save_state
    """对象建好桥、平台测试通过后，对象告知桥文件路径，平台记录到 harness 信息。
    body: {"harness_id": "...", "bridge_dir": "桥文件所在文件夹路径"}
    """
    body = await request.json()
    hid = body.get("harness_id", "")
    bridge_dir = (body.get("bridge_dir") or "").strip()
    if not hid:
        return Utf8JSONResponse({"error": "harness_id required"}, status_code=400)
    if not bridge_dir:
        return Utf8JSONResponse({"error": "bridge_dir required"}, status_code=400)
    sess = harness_manager.sessions.get(hid)
    if not sess:
        return Utf8JSONResponse({"error": f"harness {hid} 未注册"}, status_code=404)
    sess.info.bridge_dir = bridge_dir
    if sess.info.bridge_status != "tested":
        sess.info.bridge_status = "reported"
    print(f"[bridge] {hid} 桥坐标已记录: {bridge_dir} status={sess.info.bridge_status}", flush=True)
    save_state()
    return {"success": True, "harness_id": hid, "bridge_dir": bridge_dir, "bridge_status": sess.info.bridge_status}

@router.post("/api/harness/{harness_id}/activate")
async def harness_activate_window(harness_id: str):
    _audit_log.record("harness.bridge.activate", actor="user", target=harness_id, detail="激活 harness 窗口")
    from ..server import Utf8JSONResponse, _activate_windows_by_pids, _find_bridge_processes
    """Harness 监控室：将指定 harness 对应的软件窗口激活并置顶。"""
    sess = harness_manager.sessions.get(harness_id)
    if not sess:
        return Utf8JSONResponse({"error": f"harness {harness_id} 未注册"}, status_code=404)
    info = getattr(sess, "info", None)
    bridge_dir = (getattr(info, "bridge_dir", "") or "") if info else ""
    hints = [harness_id]
    if info:
        hn = (getattr(info, "harness_name", "") or "").strip()
        if hn and hn != harness_id:
            hints.append(hn)
    procs = await asyncio.to_thread(_find_bridge_processes, harness_id, bridge_dir)
    pids = [p.get("pid") for p in procs if isinstance(p, dict) and p.get("pid")]
    result = await asyncio.to_thread(_activate_windows_by_pids, pids, hints)
    result["harness_id"] = harness_id
    if not result.get("success"):
        result["hint"] = "未找到该 harness 的可激活窗口（桥进程可能无主窗口）"
    return result

@router.post("/api/harness/bridge-verify")
async def harness_bridge_verify(request: Request):
    _audit_log.record("harness.bridge.verify", actor="user", target="", detail="校验桥")
    from ..server import Utf8JSONResponse, _file_poll_send, _find_bridge_processes, _harness_wakeup_method, _pending_push, _register_bridge_test_inflight, save_state
    """平台级桥验证：综合检查桥坐标、桥进程、历史测试，并实时发测试等待真实回报。
    判定标准：实时通道检查必须收到对象侧回报（test_id 匹配且 ok=true）才算通过，
    不采信页面上的 bridge_status 字段（防状态造假/残留）。
    """
    body = await request.json()
    hid = body.get("harness_id", "")
    if not hid:
        return Utf8JSONResponse({"error": "harness_id required"}, status_code=400)
    try:
        timeout = min(max(int(body.get("timeout", 10)), 2), 30)
    except Exception:
        timeout = 10
    sess = harness_manager.sessions.get(hid)
    if not sess:
        return Utf8JSONResponse({"error": f"harness {hid} 未注册"}, status_code=404)
    info = sess.info
    bridge_dir = getattr(info, "bridge_dir", "") or ""
    # 1) 桥坐标检查
    dir_check = {"recorded": bool(bridge_dir), "exists": False, "path": bridge_dir}
    if bridge_dir:
        dir_check["exists"] = os.path.isdir(bridge_dir)
        if dir_check["exists"]:
            try:
                files = sorted(p.name for p in Path(bridge_dir).iterdir())
                dir_check["file_count"] = len(files)
                dir_check["recent_files"] = files[-5:]
            except Exception as e:
                dir_check["file_count"] = -1
                dir_check["error"] = str(e)
    # 2) 桥进程检查（与删除清理同一匹配口径，只查不杀）
    procs = await asyncio.to_thread(_find_bridge_processes, hid, bridge_dir)
    proc_check = {"running": len(procs) > 0, "processes": procs[:5]}
    # 3) 历史测试记录（仅参考，不作为通过依据）
    bt = (info.metadata or {}).get("bridge_test") or {}
    hist_check = {
        "has_record": bool(bt.get("test_id")),
        "ok": bool(bt.get("ok")),
        "at": bt.get("at", ""),
    }
    # 4) 实时通道检查：发新测试，等对象侧真实回报
    test_id = uuid4().hex[:12]
    payload = {
        "type": "bridge_test",
        "harness_id": hid,
        "test_id": test_id,
        "sent_at": datetime.now().isoformat(),
        "message": "平台桥验证：请确认桥通道正常，然后回报 bridge-test-result",
        "report_endpoint": "/api/harness/bridge-test-result",
    }
    method = _harness_wakeup_method(hid)
    if method == "file_poll":
        send_ok = _file_poll_send(hid, payload)
    else:
        send_ok = _pending_push(pending_bridge_tests, hid, payload)
    _register_bridge_test_inflight(hid, test_id)  # V-17：登记待回报测试，供回报归属校验
    channel_check = {"sent": send_ok, "sent_via": method, "test_id": test_id, "reported": False, "ok": False}
    if send_ok:
        loop = asyncio.get_event_loop()
        deadline = loop.time() + timeout
        while loop.time() < deadline:
            rec = sess.metadata.get("bridge_test") or {}
            if rec.get("test_id") == test_id:
                channel_check["reported"] = True
                channel_check["ok"] = bool(rec.get("ok"))
                channel_check["echo"] = rec.get("echo", "")
                channel_check["reported_at"] = rec.get("at", "")
                break
            await asyncio.sleep(0.5)
    # 汇总：目录存在 + 进程在跑 + 实时回报 ok 才算通过
    ok = bool(dir_check["exists"] and proc_check["running"] and channel_check["sent"] and channel_check["ok"])
    report = {
        "harness_id": hid,
        "checked_at": datetime.now().isoformat(),
        "bridge_dir": dir_check,
        "bridge_process": proc_check,
        "history_test": hist_check,
        "live_channel": channel_check,
        "ok": ok,
    }
    # 验证结果留痕并持久化
    sess.metadata["bridge_verify"] = report
    sess.info.metadata = dict(sess.info.metadata or {})
    sess.info.metadata["bridge_verify"] = report
    save_state()
    print(f"[verify] {hid} 验证完成 ok={ok} dir={dir_check['exists']} proc={proc_check['running']} "
          f"live_sent={channel_check['sent']} live_ok={channel_check['ok']}", flush=True)
    return report

@router.post("/api/harness/prefill")
async def harness_prefill(request: Request):
    _audit_log.record("harness.prefill", actor="user", target="", detail="预填注册信息")
    from ..server import Utf8JSONResponse, validate_acp_command, validate_callback_url, validate_harness_api_url, validate_wakeup_dir
    """AI 识别智能填入：根据自然语言描述提取 harness 结构化字段。"""
    body = await request.json()
    text = (body.get("text") or "").strip()
    if not text:
        return Utf8JSONResponse({"error": "描述不能为空"}, status_code=400)
    if not _sv().ai_provider:
        return Utf8JSONResponse({"error": "AI Provider 未配置，无法智能填入"}, status_code=400)
    system = (
        "你是 harness 信息解析助手。从用户对 harness 的描述中提取结构化字段，只输出 JSON，不要任何解释。\n"
        '输出格式：{"harness_id":"","harness_name":"","harness_type":"","model_name":"","provider":"","capabilities":["..."],"wakeup_method":"","callback_url":"","wakeup_dir":"","acp_command":"","acp_cwd":"","description":""}\n'
        "规则：harness_id 用完整名称（与显示名一致），不要用简写；capabilities 用简短能力标签（如 coding/file_ops/web_search）；"
        "wakeup_method 从 http/file_poll/clipboard/acp 里选（描述提到'回调地址/HTTP POST'→http，'文件轮询/监听目录'→file_poll，'剪贴板'→clipboard，'ACP/子进程/CLI 拉起'→acp）；description 保留坐标、能力、限制。"
    )
    try:
        reply = await ai_external_run_ai_call(
            _sv().ai_provider.chat(system, f"用户对 harness 的描述：\n{text}"),
            label="server.harness_smart_fill",
        )
        # ★2026-10-11：**先认 AI 错误文本**（provider 把失败当文本返回，而它切片后是合法 JSON
        #   `{"error":{...}}`）⇒ 不判会把"AI 没答"当成"AI 提取到空字段"。
        from ..ai_provider import ai_error_text as _ai_err
        _err = _ai_err(reply)
        if _err:
            return {"success": False, "error_code": "ai_unavailable",
                    "error": f"平台 AI 调用失败，未能提取字段：{_err[:200]}",
                    "hint": "检查平台 AI 配置/额度后重试；或直接手工填写注册表单。"}
        start, end = reply.find("{"), reply.rfind("}") + 1
        if start >= 0 and end > start:
            fields = json.loads(reply[start:end])
            if not isinstance(fields, dict):
                return {"success": False, "error": "AI 返回 JSON 不是对象"}
            # V-15 修复：对 AI 回填的关键字段做安全校验，非法值剔除并附警告，
            # 防止注入的 acp_command / 恶意回调 URL 被直接回填进注册表单。
            warnings: list[str] = []
            for uf in ("callback_url", "wakeup_url"):
                uv = str(fields.get(uf) or "").strip()
                if uv:
                    ok, err = validate_callback_url(uv)
                    if not ok:
                        fields[uf] = ""
                        warnings.append(f"{uf} 非法已清空: {err}")
            ab = str(fields.get("api_base_url") or "").strip()
            if ab:
                ok, err = validate_harness_api_url(ab)
                if not ok:
                    fields["api_base_url"] = ""
                    warnings.append(f"api_base_url 非法已清空: {err}")
            ac = str(fields.get("acp_command") or "").strip()
            if ac:
                ok, err = validate_acp_command(ac)
                if not ok:
                    fields["acp_command"] = ""
                    warnings.append(f"acp_command 非法已清空: {err}")
            wd = str(fields.get("wakeup_dir") or "").strip()
            if wd:
                ok, err = validate_wakeup_dir(wd)
                if not ok:
                    fields["wakeup_dir"] = ""
                    warnings.append(f"wakeup_dir 非法已清空: {err}")
            resp: dict = {"success": True, "fields": fields}
            if warnings:
                resp["warnings"] = warnings
            return resp
        return {"success": False, "error": "AI 返回无法解析为 JSON"}
    except Exception as e:
        return {"success": False, "error": f"智能填入失败: {e}"}

@router.post("/api/harness/peer-route")
async def get_peer_route(request: Request):
    _audit_log.record("harness.peer_route", actor="user", target="", detail="点对点转交")
    from ..server import Utf8JSONResponse
    """查询目标 Harness Agent 的路由信息，供直连委托/审查使用。
    平台退为路由注册中心：当双 Harness 均在线时，
    委托和审查不再经平台中转，而是直接 HTTP POST 到对端。
    """
    body = await request.json()
    to_agent_id = body.get("to_agent_id", "")
    to_hid = harness_manager.id_to_harness.get(to_agent_id)
    if not to_hid:
        return Utf8JSONResponse(
            {"error": "target agent is not a harness"}, status_code=404
        )
    sess = harness_manager.sessions.get(to_hid)
    if not sess or sess.status != HarnessStatus.ONLINE:
        return Utf8JSONResponse({"error": "target harness offline"}, status_code=404)
    return {
        "harness_id": to_hid,
        "agent_id": to_agent_id,
        "callback_url": sess.info.callback_url,
        "transport": sess.transport.value,
        "harness_name": sess.info.harness_name,
    }

