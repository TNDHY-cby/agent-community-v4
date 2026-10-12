# -*- coding: utf-8 -*-
"""platform/verify_sandbox：**低耗验证间** —— 在大厅里把一条接入真跑通，再回写模板。

设计稿：`design-docs/V42_接入建模_低耗验证间与模板回写.md`（老板 2026-10-11 定稿）
老板原话：「**大厅注册时，给到一个等价低耗工作间模型进行架桥等等测试**」
        「（跑通后）**连桥也拆掉，只留模板**」

**它解决什么**
──────────────
此前"验证一条桥"必须先在某个**真实工作间**里挂个成员 —— 把"接入"和"开工"绑死了。
本模块给每个 harness 一个**只为验证而存在**的最小工作间：

    注册 → 建验证间 → 架桥 → 自检跑通 → **回写模板**（记 verified_at + 证据）→ **拆掉**（只留模板）

**为什么必须"等价"**
────────────────────
验证间**走的是与真实工作间完全同一套管线**（`connect_artifact` → 成员隔间 → 邮箱 → 桥 → 回报），
否则"在验证间跑通"不能保证"在真实工作间也能跑通"。本模块**不自己造一套简化流程**。

**顺带解开一个死锁**
────────────────────
`attach_bridge_core` 要求"该 harness **已有**桥模板"才能架桥（没模板直接报错），
而新模型是"**架桥跑通后**才写模板" ⇒ 先有鸡还是先有蛋。
⇒ 本模块在**没有模板时先派生一条「草稿」**（不算 verified），用它跑；跑通后回写并标记已验证。
"""
from __future__ import annotations

import os
import sys
import time
from typing import Any, Optional

#: 验证间在工作间对象上的标记（`origin_task_id` 空闲，用它区分，避免再加字段）
SANDBOX_MARK = "__verify_sandbox__"

#: 验证间里那个唯一成员的角色
SANDBOX_ROLE = "验证员"

#: 验证间落盘根（**不进正式工作间目录**，便于整体清理）
SANDBOX_SUBDIR = os.path.join("verify_sandbox")


class SandboxError(Exception):
    """验证间相关的可预期失败（带中文原因 + 怎么办）。"""

    def __init__(self, message: str, *, hint: str = "", code: int = 400):
        super().__init__(message)
        self.hint = hint
        self.code = code


def is_sandbox(ws: Any) -> bool:
    """这个工作间是不是验证间。"""
    return str(getattr(ws, "origin_task_id", "") or "") == SANDBOX_MARK


def sandbox_root(data_dir: str) -> str:
    return os.path.join(str(data_dir), SANDBOX_SUBDIR)


def find_sandbox(workshops: dict, harness_id: str) -> Optional[Any]:
    """找该 harness 的验证间（一个 harness 一个 —— 老板裁定"各自一个"）。

    ⚠️ 用 **workshop_id 精确等值**（它由 `sandbox_dir_name` 唯一推导）。
    我第一版用 `name.endswith(harness_id)` 判断 —— **后缀匹配会误命中**：
    harness `sandbox-live-1` 是验证间名 `vs_sandbox-live-1` 的后缀（看着"对"），
    但 harness `live-1` 同样也是它后缀 ⇒ 会**串到别人的验证间**。
    """
    want = sandbox_dir_name(harness_id)
    ws = (workshops or {}).get(want)
    if ws is not None and is_sandbox(ws):
        return ws
    # 兼容：极端情况下 id 被改过，则只在**同名且标记正确**时接受
    for cand in (workshops or {}).values():
        if is_sandbox(cand) and getattr(cand, "workshop_id", "") == want:
            return cand
    return None


def ensure_sandbox(workshops: dict, harness_id: str, *, data_dir: str,
                   harness_name: str = "") -> Any:
    """取（没有就建）该 harness 的验证间。

    - **一个 harness 一个**（老板裁定：各自一个，互不干扰、拆得干净）；
    - 落 `<data>/verify_sandbox/ws_<hid>/`，**不进正式工作间目录**；
    - 1 个成员，角色 `验证员`，绑该 harness —— 桥必须挂在"某个成员"上（平台既有模型）。
    """
    existing = find_sandbox(workshops, harness_id)
    if existing is not None:
        return existing
    try:
        from .workshop import Workshop, WorkshopMember
    except Exception as e:  # noqa: BLE001
        raise SandboxError(f"无法建立验证间（工作间模型不可用）：{e}")

    wsid = sandbox_dir_name(harness_id)   # 目录名只在这里推导一次
    disp = harness_name or harness_id
    ws = Workshop(
        workshop_id=wsid,
        name=f"验证间 · {disp}",
        workspace_dir=os.path.join(sandbox_root(data_dir), wsid),
        hall_content="（验证间）只为验证「这个 agent 能不能被叫醒」而存在，不参与正式开工。",
        members=[WorkshopMember(member_id="v0", role=SANDBOX_ROLE,
                                display_name=disp, harness_ids=[harness_id])],
    )
    ws.status = "running"          # 验证间不需要讨论/选人阶段，直接进"能架桥"的状态
    ws.origin_task_id = SANDBOX_MARK
    workshops[wsid] = ws
    # ★必须落盘（2026-10-11 实测踩到）：只放进内存的话，**下一次请求就找不到它**
    #   ⇒ 拆的时候报"没有验证间（无需拆）"而**目录留在盘上**（典型的假成功 + 清不掉）。
    #   落盘后 `find_sandbox` 才找得到，`teardown` 才真的拆得掉。
    _persist()
    return ws


def sandbox_dir_name(harness_id: str) -> str:
    """验证间目录名（**唯一推导点** —— `ensure_sandbox` 与清理都从这里取）。"""
    return "vs_" + "".join(ch for ch in str(harness_id) if ch.isalnum() or ch in "-_")[:24]


def _cleanup_leftover_dirs(harness_id: str, *, data_dir: str = "") -> list:
    """清掉盘上残留的该 harness 验证间目录（只碰 `verify_sandbox/<约定名>`，不做模糊匹配）。"""
    import shutil
    root = ""
    if data_dir:
        root = sandbox_root(data_dir)
    if not root:
        try:
            from .server import DATA_DIR as _D
            root = sandbox_root(str(_D))
        except Exception:  # noqa: BLE001
            return []
    target = os.path.join(root, sandbox_dir_name(harness_id))
    done = []
    if os.path.isdir(target):
        shutil.rmtree(target, ignore_errors=True)
        if not os.path.isdir(target):
            done.append(target)
    return done


def _persist() -> None:
    """落盘（失败不阻断：验证间只是加速手段，不该因为它把接入卡死）。"""
    try:
        from .server import save_state
        save_state()
    except Exception as e:  # noqa: BLE001
        print(f"[verify_sandbox] 落盘失败（不阻断验证）：{e}", flush=True)


def draft_template_from(info: Any) -> dict:
    """**没有模板时先派生一条草稿**（解开"要先有模板才能架桥"的死锁）。

    草稿的来源就是 harness 自己的注册信息（这与既有"自动派生一条"的口径一致）；
    `verified=False`、`source="draft"` —— **不算已验证**，跑通后由 `commit_verified` 回写。
    """
    wm = str(getattr(getattr(info, "wakeup_method", None), "value",
                     getattr(info, "wakeup_method", "") or ""))
    inbox = str(getattr(info, "wakeup_dir", "") or "").strip()
    acp = str(getattr(info, "acp_command", "") or "").strip()
    # ⚠️2026-10-11 被测试抓到的 bug：原来写成 `if wm == "file_poll" or inbox:` ——
    #   **只要填了邮箱目录就永远派生成 file_poll**，把 `acp` 等其它型**覆盖掉**。
    #   ⇒ 先认 `wakeup_method`（它才是用户/agent 的声明），**线索只作兜底**。
    if wm == "acp" or (not wm and acp):
        kind, params = "cli_acp", {"ACP_COMMAND": acp}
    elif wm == "file_poll" or (not wm and inbox):
        kind, params = "file_poll", {"INBOX_DIR": inbox}
    else:
        kind, params = "pending_poll", {}
    return {"name": "验证间草稿桥", "kind": kind, "params": params,
            "source": "draft", "verified": False,
            "why": "验证间先派生一条草稿用于跑通；跑通后会把实测值回写并标记已验证"}


#: 平台地址（桥脚本要回连它）。从既有配置取，取不到才退回本机默认 —— 别处不再硬编码。
def _platform_url() -> str:
    for key in ("AC_PLATFORM_URL", "AC_BASE_URL"):
        v = os.environ.get(key)
        if v:
            return str(v).rstrip("/")
    return "http://127.0.0.1:18920"


BRIDGE_PLATFORM = _platform_url()

#: 桥脚本所在（与 `harness_messaging` / `bridge_supervisor` 用**同一个** examples 目录）
BRIDGE_SCRIPTS = {
    "file_poll": "filepoll_harness_bridge.py",
    "pending_poll": "pending_poll_bridge.py",
    "acp": "acp_harness_bridge.py",
}


def bridge_script_path(kind: str) -> str:
    """桥型 → 脚本绝对路径（唯一推导点，别处不再拼）。"""
    pkg = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # .../agent_community
    return os.path.join(pkg, "examples", BRIDGE_SCRIPTS.get(str(kind), ""))


def bridge_argv(kind: str, *, harness_id: str, inbox: str, platform: str,
                trigger: str = "", interval: float = 1.0) -> list:
    """拼**验证间临时桥**的启动参数（短命：测完就停，不常驻）。

    ⚠️ 这里**不给脚本加任何新选项**（不改桥脚本）—— 验证走的必须是与真实工作间
    **同一套**桥，否则"验证间跑通"证明不了"真实也行"。停桥由平台这边 `terminate()`。
    """
    script = bridge_script_path(kind)
    if not os.path.isfile(script):
        raise SandboxError(f"桥脚本不存在：{script}",
                           hint="确认 agent_community/examples/ 下的桥脚本是否完整")
    argv = [sys.executable, "-u", script, "--harness-id", harness_id]
    # ⚠️ 参数名**逐脚本核对过**（别照猜）：file_poll 用 `--platform`，
    #    pending_poll / acp 用 `--url`（实测踩到过这个不一致）。
    if kind == "file_poll":
        argv += ["--inbox", inbox]
        if trigger:
            argv += ["--trigger", trigger]
        argv += ["--interval", str(interval), "--platform", platform]
    elif kind == "pending_poll":
        # pending_poll 桥的工作目录 = 会话目录（与真实架桥一致）
        argv += ["--work-dir", os.path.dirname(inbox) or inbox]
        if trigger:
            argv += ["--trigger", trigger]
        argv += ["--interval", str(interval), "--url", platform]
    elif kind == "acp":
        argv += ["--url", platform]
    return argv


def start_bridge(kind: str, *, harness_id: str, inbox: str, platform: str,
                 trigger: str = "") -> dict:
    """起一条**临时**桥进程（验证间专用）。返回 `{pid, argv}`；失败抛 `SandboxError`。"""
    import subprocess as _sp
    argv = bridge_argv(kind, harness_id=harness_id, inbox=inbox, platform=platform, trigger=trigger)
    # ⚠️★2026-10-11 事故教训（**平台被这个搞崩过一次**）：
    #   第一版用了 `stdout=PIPE, stderr=STDOUT` **却不读管道** —— 桥是常驻进程、持续输出
    #   （心跳/扫描日志），**管道缓冲区填满后子进程阻塞**；更糟的是平台自身的 stdout
    #   也被看门狗重定向到管道，**平台往已满/已关闭的管道写日志会把服务带崩**
    #   （实测：/verify 之后 18920 直接不监听，日志留下 "Application shutdown complete"）。
    #   ⇒ 验证间的桥是**临时**进程、输出没人看，**直接丢弃**，绝不开无人读的管道。
    # 桥的输出**写成日志文件**（不是 PIPE，也不是 DEVNULL）：
    #   · PIPE 不读 ⇒ 缓冲区填满会把子进程/平台拖死（实测事故，见上）；
    #   · DEVNULL ⇒ 出事时**什么都没留下**，只能靠猜（我为此多花了好几轮）。
    #   ⇒ 落 `verify_sandbox/_bridge_logs/<harness>.log`，既不会阻塞、又留得下证据。
    logpath = ""
    try:
        # ⚠️2026-10-12：日志要落在**平台真正使用的 data 目录**（`server.DATA_DIR`），
        #   不要用 `__file__` 反推 —— 测试里 DATA_DIR 被重定向到 tmp，用 __file__ 反推会把
        #   日志写进**仓库目录**（实测被 conftest 的"污染真实数据"守卫抓到，报
        #   `verify_sandbox\_bridge_logs\r19-….log: None -> (1919, …)`）。
        try:
            from .server import DATA_DIR as _DD
            _data_root = str(_DD)
        except Exception:  # noqa: BLE001
            _data_root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")
        logdir = os.path.join(_data_root, SANDBOX_SUBDIR, "_bridge_logs")
        os.makedirs(logdir, exist_ok=True)
        logpath = os.path.join(logdir, f"{harness_id}.log")
    except Exception:  # noqa: BLE001
        logpath = ""
    # ⚠️★★2026-10-11 事故根因（**平台被这个搞死过，实测夹点确认**）：
    #   Windows 会**把父进程的句柄继承给子进程** —— 包括**平台监听 socket 的句柄**。
    #   子进程（桥）被 `terminate()` 时，Windows 顺手关掉那份继承来的句柄
    #   ⇒ **平台的监听 socket 一起坏掉**：`Accept failed ... WinError 64 指定的网络名不再可用`，
    #     服务从此不响应（但进程还活着）。夹点实测：`/verify` 前后平台可达性由 True 变 False，
    #     而"单独起桥+停桥"（不经 /verify）**完全没事** —— 差别就在这些句柄。
    #   ⇒ 三件事都要做：①**父进程立刻关掉自己的文件句柄**（不长期持有）；
    #     ② 子进程输出写文件、**绝不开 PIPE**（无人读的 PIPE 会阻塞）；
    #     ③ `close_fds=True` 明确不继承句柄（Windows 上也是允许的）。
    try:
        if logpath:
            with open(logpath, "a", encoding="utf-8", errors="replace") as logf:
                proc = _sp.Popen(argv, stdout=logf, stderr=_sp.STDOUT,
                                 stdin=_sp.DEVNULL, close_fds=True,
                                 creationflags=getattr(_sp, "CREATE_NEW_PROCESS_GROUP", 0))
        else:
            proc = _sp.Popen(argv, stdout=_sp.DEVNULL, stderr=_sp.DEVNULL,
                             stdin=_sp.DEVNULL, close_fds=True,
                             creationflags=getattr(_sp, "CREATE_NEW_PROCESS_GROUP", 0))
    except Exception as e:  # noqa: BLE001
        raise SandboxError(f"起桥失败：{type(e).__name__}: {e}",
                           hint="检查桥脚本与 python 路径是否可用")
    # 登记进 bridge_procs：这样平台既有工具（停止/存活判断）**同样能管到它**
    # （签名是 `record(harness_id, proc, cmd)` —— 传 proc 对象，不是 pid）
    try:
        from . import bridge_procs as _bp
        _bp.record(harness_id, proc, "verify:" + str(kind))
    except Exception:  # noqa: BLE001
        pass
    return {"pid": proc.pid, "argv": argv, "proc": proc, "harness_id": harness_id}


def stop_bridge_proc(started: dict | None) -> dict:
    """停掉验证间那条临时桥（**尽力**：杀不掉也不阻断拆除，但如实回报）。"""
    proc = (started or {}).get("proc")
    if proc is None:
        return {"stopped": False, "why": "没有可停的桥进程"}
    out = {"stopped": False, "pid": (started or {}).get("pid")}
    try:
        proc.terminate()
        try:
            proc.wait(timeout=5)
            out["stopped"] = True
        except Exception:  # noqa: BLE001
            proc.kill()
            out["stopped"] = True
            out["killed"] = True
    except Exception as e:  # noqa: BLE001
        out["why"] = f"{type(e).__name__}: {e}"
    try:
        from . import bridge_procs as _bp
        # `forget(harness_id)` —— 我第一版传的是 pid（签名不符，等于没清）
        _bp.forget((started or {}).get("harness_id") or "")
    except Exception:  # noqa: BLE001
        pass
    return out


async def verify_bridge(workshops: dict, harness_id: str, *, data_dir: str,
                        harness_name: str = "", wait: float = 8.0) -> dict:
    """**一步到位**：建/取验证间 → （必要时派生草稿）→ 架桥 → 自检 → 返回结论。

    只做"验证"；**回写模板**与**拆掉**分别由 `commit_verified` / `teardown` 负责，
    这样界面可以先让用户看到结果再决定（也便于"跑通才写"这条门槛落到实处）。
    """
    ws = ensure_sandbox(workshops, harness_id, data_dir=data_dir, harness_name=harness_name)
    m = (ws.members or [None])[0]
    if m is None:
        raise SandboxError("验证间里没有成员（数据异常）", hint="删掉验证间重建")

    from .harness_adapter import harness_manager
    from . import bridge_templates as _bt
    sess = harness_manager.sessions.get(harness_id)
    if sess is None:
        raise SandboxError(f"harness {harness_id} 未注册", code=404)

    # 「先有模板才能架桥」的死锁在这里被解开：没有就派生草稿
    had_template = bool(_bt.list_all(sess.info))
    draft_made = None
    if not had_template:
        draft_made = draft_template_from(sess.info)
        # 用模块常量，别写裸字符串（`SOURCE_USER` 才是契约里的取值）
        _bt.put(sess.info, draft_made, source=getattr(_bt, "SOURCE_USER", "user"))

    from .routers.workshops_session import attach_bridge_core
    att = attach_bridge_core(ws, m, template_name=(draft_made or {}).get("name", ""),
                             new_name="验证会话")
    if not att.get("success"):
        return {"success": False, "stage": "attach", "error": att.get("error"),
                "hint": att.get("hint", ""), "sandbox_id": ws.workshop_id,
                "draft_made": bool(draft_made)}

    # ★架桥端点只**生成桥产物 + 建会话**，**不起进程**（实测发现，我原以为它会起）。
    #   所以验证间必须**自己把桥跑起来** —— 否则自检必然 `not_picked`（"消息还躺在邮箱里"）。
    #   起的是**临时**桥：测完即停（老板：「连桥也拆掉」），且用的是**同一个桥脚本**
    #   （跑的必须是与真实工作间同一套桥，否则验过不等于能用）。
    inbox = str(_bt_mailbox(ws, m))
    tpl = _bt.list_all(sess.info)[0] if _bt.list_all(sess.info) else {}
    kind = str(tpl.get("kind") or "")
    trigger = str((tpl.get("params") or {}).get("TRIGGER_CMD") or "")
    started = None
    start_err = ""
    # 排障开关：`AC_VERIFY_NO_START_BRIDGE=1` 时**只架桥不起桥**（用于把"平台为何不可达"
    # 的因果切到两半：架桥路径 vs 起桥路径）。正常运行时不要设置它。
    _skip_start = str(os.environ.get("AC_VERIFY_NO_START_BRIDGE") or "") in ("1", "true", "yes")
    if _skip_start:
        start_err = "（排障开关生效：本次只架桥、不起桥）"
    elif kind in BRIDGE_SCRIPTS and BRIDGE_SCRIPTS.get(kind):
        try:
            started = start_bridge(kind, harness_id=harness_id, inbox=inbox,
                                   platform=BRIDGE_PLATFORM, trigger=trigger)
        except SandboxError as e:
            start_err = str(e)
        except Exception as e:  # noqa: BLE001
            start_err = f"{type(e).__name__}: {e}"
    else:
        start_err = f"桥型「{kind}」在验证间里不支持自动起桥（可在真实工作间里验）"

    try:
        # 自检：真发一条探针，看"桥取走了没 / agent 回话没"
        from .bridge_selfcheck import selfcheck
        chk = selfcheck(inbox, harness_id=harness_id, workshop_id=ws.workshop_id,
                        member_id=m.member_id, role=SANDBOX_ROLE, wait=wait)
    finally:
        # **无论成败都停桥**：验证间的桥是临时的，不能留常驻进程
        stopped = stop_bridge_proc(started)
    return {"success": bool(chk.get("success")), "stage": "selfcheck",
            "state": chk.get("state"), "reason": chk.get("reason"),
            "advice": chk.get("advice"), "detail": chk.get("detail") or {},
            "sandbox_id": ws.workshop_id, "member_id": m.member_id,
            "draft_made": bool(draft_made), "had_template": had_template,
            "bridge_kind": kind, "bridge_started": bool(started),
            "bridge_start_error": start_err, "bridge_stopped": stopped}


def _bt_mailbox(ws: Any, m: Any) -> str:
    """该成员隔间里的邮箱目录（路径只从 `compartment` 派生，别处不再拼）。"""
    from .compartment import mailbox_dir
    return str(mailbox_dir(ws.workspace_dir, m.role, m.member_id, "inbox"))


def commit_verified(workshops: dict, harness_id: str, *, evidence: dict | None = None) -> dict:
    """**跑通之后**才把配置回写进模板，并标记"已验证"（V42 F3）。

    回写口径（老板裁定"按你的来" = **实测值优先**）：
      · 保留模板里**用户/实测**给的 params（跑通用的就是它）；
      · 打上 `verified=true` / `verified_at` / `verified_by="sandbox"` / `evidence`；
      · **只有跑通过才写** —— 调用方必须先拿到自检 `ok`（本函数不自己判）。
    """
    from .harness_adapter import harness_manager
    from . import bridge_templates as _bt
    sess = harness_manager.sessions.get(harness_id)
    if sess is None:
        raise SandboxError(f"harness {harness_id} 未注册", code=404)
    items = _bt.list_all(sess.info)
    if not items:
        raise SandboxError("该 harness 还没有任何桥模板，无法回写",
                           hint="先跑一次验证（会自动派生草稿）")
    tpl = items[0]
    meta = sess.info.metadata if isinstance(getattr(sess.info, "metadata", None), dict) else {}
    now = time.time()
    hist = list(meta.get("bridge_template_verified") or [])
    hist.append({"verified_at": now, "evidence": dict(evidence or {})})
    meta["bridge_template_verified"] = hist[-5:]        # 只留最近 5 次，别无限长
    return {"success": True, "harness_id": harness_id, "template_name": tpl.get("name", ""),
            "verified": True, "verified_at": now, "verified_by": "sandbox",
            "evidence": dict(evidence or {}),
            "note": "已把「跑通」的配置记为已验证（模板上会显示验证时间）"}


async def teardown(workshops: dict, harness_id: str, *, stop_bridge=None) -> dict:
    """**拆掉验证间**：停桥、删隔间产物、结束测试会话、移除验证间 —— **只留模板**（V42 F4）。

    `stop_bridge`：平台停桥的工具（`server._stop_bridge_processes`），由调用方注入
    —— 本模块**不直接 import server**（那会绕开既有的依赖方向）。
    """
    ws = find_sandbox(workshops, harness_id)
    removed = {"bridge_stopped": None, "compartment_removed": None, "ws_removed": False}
    if ws is None:
        # ⚠️ 2026-10-11 实测教训：**内存里找不到 ≠ 盘上没有**。
        #   第一版验证间忘了落盘 ⇒ 下次请求 `find_sandbox` 找不到 ⇒ 这里返回
        #   "没有验证间（无需拆）"（**假成功**），而**目录留在盘上**。
        #   ⇒ 兜底：按约定的目录名直接清残留（只碰 `verify_sandbox/` 下、名字对得上的）。
        leftover = _cleanup_leftover_dirs(harness_id)
        return {"success": True, "removed": bool(leftover), "leftover_cleaned": leftover,
                "note": ("已清理盘上残留的验证间目录（内存里已无登记）" if leftover
                         else "没有验证间（无需拆）")}
    # ① 停桥（桥进程不属于验证间以外任何东西）
    if callable(stop_bridge):
        try:
            info = None
            try:
                from .harness_adapter import harness_manager
                sess = harness_manager.sessions.get(harness_id)
                info = getattr(sess, "info", None)
            except Exception:  # noqa: BLE001
                pass
            removed["bridge_stopped"] = stop_bridge(
                harness_id, str(getattr(info, "bridge_dir", "") or ""))
        except Exception as e:  # noqa: BLE001
            removed["bridge_stopped"] = f"停桥失败（不阻断拆除）: {e}"
    # ② 删隔间产物（验证间目录随它一起删）
    try:
        import shutil
        base = str(getattr(ws, "workspace_dir", "") or "")
        if base and os.path.isdir(base):
            shutil.rmtree(base, ignore_errors=True)
            removed["compartment_removed"] = True
        else:
            removed["compartment_removed"] = False
    except Exception as e:  # noqa: BLE001
        removed["compartment_removed"] = f"清理失败: {e}"
    # ③ 移除验证间本身 —— **同样要落盘**，否则重启后它又"回来"了（实测同类坑）
    workshops.pop(getattr(ws, "workshop_id", ""), None)
    _persist()
    removed["ws_removed"] = True
    # ④ 兜底：清目录残留（防 rmtree 半途失败留下半个验证间）
    removed["leftover_cleaned"] = _cleanup_leftover_dirs(harness_id)
    return {"success": True, "removed": True, "detail": removed,
            "note": "验证间已拆（桥已停、隔间已清）；**模板与验证记录保留**"}
