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


def _measured_params(info: Any, kind: str) -> dict:
    """**这次跑通时能确定下来的参数**（写进模板本体）。

    只写"平台真知道的"：
      · 平台地址 —— 验证间里那只桥就是回连它跑通的；
      · 按注册声明派生的关键值（`file_poll` 的 inbox、`cli_acp` 的拉起命令/工作目录）。
    **不写猜的**：拿不到的就不写，缺什么由 `missing` 如实显示（宁可不写，也不编）。
    """
    p: dict = {"PLATFORM_URL": BRIDGE_PLATFORM}
    inbox = str(getattr(info, "wakeup_dir", "") or "").strip()
    acp = str(getattr(info, "acp_command", "") or "").strip()
    if kind == "file_poll" and inbox:
        p["INBOX_DIR"] = inbox
    elif kind == "cli_acp" and acp:
        p["ACP_COMMAND"] = acp
        cwd = str(getattr(info, "acp_cwd", "") or "").strip()
        if cwd:
            p["ACP_CWD"] = cwd
    return p


#: 平台地址（桥脚本要回连它）。从既有配置取，取不到才退回本机默认 —— 别处不再硬编码。
def _platform_url() -> str:
    # ★2026-10-13：委托唯一解析点（优先 AC_PLATFORM_URL/AC4_BASE_URL/AC_BASE_URL，
    #   再按 AC_PORT 推）——原来这里硬编码 18920，用户换端口后它就错了。
    import os as _os
    for key in ("AC_PLATFORM_URL", "AC_BASE_URL"):
        v = _os.environ.get(key)
        if v:
            return str(v).rstrip("/")
    from ..platform_address import base_url as _addr_base_url
    return _addr_base_url()


BRIDGE_PLATFORM = _platform_url()

#: 桥脚本所在（与 `harness_messaging` / `bridge_supervisor` 用**同一个** examples 目录）
#: ⚠️★2026-10-13（V44 #10）：键**必须与桥模板的 `kind` 完全一致**。
#:   原来是 `"acp"`，而模板 kind 是 `cli_acp` ⇒ 下面 `bridge_script_path(kind)` 拿 `kind` 去查
#:   **永远查不到** ⇒ A 档 harness 在验证间里必然 `bridge_started=false`、第 4 步必然 `not_picked`
#:   （实测：dsl 型 harness 只能"先手动把常驻桥跑起来"才能把第 4 步弄绿）。
#:   已按 KINDS 口径改正；判据只留这一处，别在别处再写一遍桥型名。
BRIDGE_SCRIPTS = {
    "file_poll": "filepoll_harness_bridge.py",
    "pending_poll": "pending_poll_bridge.py",
    "cli_acp": "acp_harness_bridge.py",
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
    #    pending_poll / cli_acp 用 `--url`（实测踩到过这个不一致）。
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
    elif kind == "cli_acp":
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


#: 走**文件邮箱**的桥型（探针要写成 `task_*.json` 丢进 inbox）。
#: ⚠️其余桥型（`pending_poll` / `cli_acp` …）**根本不读邮箱** —— 用邮箱验它们必然
#:   `not_picked`，平台还会对用户说假话「桥没有在运行」。
_FILE_MAILBOX_KINDS = {"file_poll"}


async def channel_selfcheck(harness_id: str, *, kind: str, wait: float = 8.0) -> dict:
    """★**按桥型选探针通道**（2026-10-12 实测修）。

    **为什么要按桥型分**（真机实测教训）：
      验证间原来**一律**把试探写成 `task_*.json` 丢进文件邮箱、再判断"文件有没有被搬进
      `delivered/`"。这对 `file_poll` 是对的；但 `pending_poll` 桥是**轮询平台 API**
      （`GET /api/harness/pending-tasks`）取件的，**根本不读邮箱** ⇒ 文件永远不动
      ⇒ 结论必然 `not_picked`，reason 还说「**桥没有在运行**」——**而桥其实跑得好好的**。
      平台就这么对用户说了假话，用户只会去查一个并不存在的"桥没起"问题。

    **正确通道**：平台**自带**的"桥测试通道"
      （`POST /api/harness/bridge-test` → 进 `pending_bridge_tests` 队列 → 桥轮询领取并
       **自动回报** `bridge-test-result`）。它验的正是"桥活着 + 连得上平台 + 能回报"，
      **不需要 agent 参与** —— 对 `pending_poll` 这类桥这才是恰当的探针。
    """
    # ⚠️★**必须是 async**（2026-10-12 我第一版写成同步函数、在里面 `time.sleep` ⇒
    #   **阻塞整个事件循环**最多 `wait` 秒 ⇒ 平台监听套接字出现 `WinError 64`
    #   「Accept failed on a socket」，进程**活着但不再收连接**（实测把运行中的平台搞挂）。
    #   教训：凡是在 `async def` 里被调用的等待，一律 `asyncio.sleep`，绝不 `time.sleep`。
    import asyncio as _a
    import time as _t

    from .state import _bridge_tests_inflight, pending_bridge_tests
    from .harness_adapter import harness_manager

    sess = harness_manager.sessions.get(harness_id)
    if sess is None:
        return {"success": False, "state": "not_set_up",
                "reason": f"harness {harness_id} 未注册",
                "advice": "先注册再验", "detail": {"kind": kind}}

    try:
        w = max(0.0, min(float(wait or 0), 30.0))
    except Exception:  # noqa: BLE001
        w = 8.0

    test_id = os.urandom(6).hex()
    payload = {
        "type": "bridge_test", "harness_id": harness_id, "test_id": test_id,
        "sent_at": _dt_now(),
        "message": "平台桥测试：请确认桥通道正常，然后回报 bridge-test-result",
        "report_endpoint": "/api/harness/bridge-test-result",
    }
    # 与 `harness_bridge_test` 端点同一套登记口径（test_id 必须匹配 + 有有效期）
    _bridge_tests_inflight[harness_id] = {
        "test_id": test_id, "reported": False, "expire_ts": _t.time() + 180,
    }
    pending_bridge_tests.setdefault(harness_id, []).append(payload)

    got = None
    deadline = _t.time() + w
    while _t.time() < deadline:
        got = (getattr(sess, "metadata", None) or {}).get("bridge_test") or \
            (getattr(sess.info, "metadata", None) or {}).get("bridge_test")
        if got and str(got.get("test_id")) == test_id:
            break
        await _a.sleep(0.5)      # ★绝不能是 time.sleep（会阻塞事件循环、把平台搞挂）
        got = None
    if got is None:
        left = pending_bridge_tests.get(harness_id) or []
        still = any(str(x.get("test_id")) == test_id for x in left)
        if still:
            reason = ("桥**没来领**这条测试件 —— 这一型（%s）的桥要自己轮询平台；"
                      "多半是它没在跑、或起它时 `--url`/`--harness-id` 填错了。" % kind)
            # ★2026-10-13（V44）：advice 原来**写死** `pending_poll_bridge.py`，
            #   于是一个 cli_acp 的 harness 拿到的是"起 pending_poll 桥"这种**跟它无关**的指引
            #   （真机实测：E1 那份回执里就抱怨了这一点）。
            #   ⇒ 按当前 kind 给出**对应那一只**脚本名与参数（口径只此一处）。
            _script = BRIDGE_SCRIPTS.get(kind) or "bridge.py"
            if kind == "file_poll":
                _how = f"`{_script} --harness-id {harness_id} --inbox <你的目录> --platform <平台地址>`"
            elif kind == "pending_poll":
                _how = f"`{_script} --harness-id {harness_id} --url <平台地址> --work-dir <工作目录>`"
            elif kind == "cli_acp":
                _how = (f"`{_script} --harness-id {harness_id} --url <平台地址> "
                        f"--acp-command \"<拉起命令>\"`")
            else:
                _how = f"`{_script} --harness-id {harness_id} --url <平台地址>`"
            advice = f"起这只桥（用平台生成的桥脚本，例如 {_how}），再重跑。"
        else:
            reason = ("桥领走了测试件，但**没有回报** —— 它跑起来了，只是回报那一步没走通。" )
            advice = "看桥的输出/日志里回报那一段的报错（常见：平台地址或 token 不对）。"
        return {"success": False, "state": "not_picked" if still else "no_reply",
                "reason": reason, "advice": advice,
                "detail": {"kind": kind, "test_id": test_id,
                           "channel": "bridge_test_queue", "waited_seconds": w}}
    ok = bool(got.get("ok"))
    return {"success": ok, "state": "ok" if ok else "no_reply",
            "reason": ("桥通道正常（桥领了测试件并回报 ok）" if ok
                       else "桥回报了，但说它不 ok：%s" % str(got.get("echo"))[:80]),
            "advice": "" if ok else "看桥的输出，按它报的错修。",
            "detail": {"kind": kind, "test_id": test_id,
                       "channel": "bridge_test_queue", "echo": got.get("echo", ""),
                       "waited_seconds": w}}


def _dt_now() -> str:
    from datetime import datetime
    return datetime.now().isoformat(timespec="seconds")


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
        # ★★2026-10-12 **按桥型选探针通道**（真机实测修，见 `channel_selfcheck` 的说明）：
        #   `file_poll` 才用文件邮箱；`pending_poll` 这类**轮询平台 API**的桥
        #   必须走平台的"桥测试通道"，否则必然假阴性 `not_picked`，
        #   而且平台会错怪用户「桥没有在运行」（桥其实好好的）。
        if kind in _FILE_MAILBOX_KINDS:
            # 自检：真发一条探针，看"桥取走了没 / agent 回话没"
            from .bridge_selfcheck import selfcheck
            chk = selfcheck(inbox, harness_id=harness_id, workshop_id=ws.workshop_id,
                            member_id=m.member_id, role=SANDBOX_ROLE, wait=wait)
            chk["channel"] = "file_mailbox"
        else:
            chk = await channel_selfcheck(harness_id, kind=kind, wait=wait)
            # 说明清楚"这一型验的是什么" —— 免得接入方以为验了 agent 的回话能力
            chk.setdefault("detail", {})["channel_note"] = (
                "这一型（%s）的桥不走文件邮箱 ⇒ 探针走**平台桥测试通道**，"
                "验的是「桥在跑 + 连得上平台 + 能回报」；**agent 的回话能力不在本次验证范围内**。"
                % kind)
    finally:
        # **无论成败都停桥**：验证间的桥是临时的，不能留常驻进程
        stopped = stop_bridge_proc(started)
    # ★★2026-10-12 实测修一处**指路指到了废墟**（真机材料）：
    #   那个真实 agent 收到过「在成员隔间目录里运行 bridge.py」这类指引，于是打算去
    #   `verify_sandbox/vs_<hid>/验证员_v0/` 找 `bridge.py` 与 `deliver_text.md` ——
    #   **而验证间隔间在 teardown 时就被删了**（`compartment_removed: true`），
    #   它去找只会一无所获，然后怀疑自己或平台。
    #   ⇒ 印证一句话：**验证间是"一次性"的，别把它当"持久桥的落脚点"**。
    #   要在 harness 侧**持久运行**，正确出口是平台的构桥 API
    #   （`POST /api/harness/{hid}/bridge/generate` → 生成到你自己的目录并登记坐标）。
    _persist_hint = {
        "sandbox_dir_deleted": True,
        "why": ("验证间（`verify_sandbox/vs_%s/...`）是**一次性的**：验证跑完就拆、"
                "隔间与里面的 `bridge.py` **一并删除**。"
                "⇒ **不要**去那里找桥脚本（找不到是正常的，不是你没找对）。" % harness_id),
        # ⚠️★这段文字是**实测核对过**的（我第一版写成「out_dir 填你自己的目录」——
        #   实测被 400 拒：「out_dir 必须位于平台 bridges 目录内」。差点又给出一条走不通的指引。）
        "how_to_run_persistently": (
            "生成并持久运行桥（**实测核对过的两条**）：\n"
            "  1) POST /api/harness/%s/bridge/generate  "
            '{"template": "%s"}\n'
            "     · **省略 `out_dir`** ⇒ 平台落到 `<平台>/agent_community/bridges/<harness_id>/bridge.py`；\n"
            "     · 若要自己指定，`out_dir` **必须在平台的 `bridges/` 目录内**（否则 400）；\n"
            "     · 返回里有 `bridge_file` / `bridge_dir` / **`launch_hint`（现成的启动命令，照它跑）**。\n"
            "  2) 用返回的 `launch_hint` 把桥**常驻**跑起来（它按间隔轮询平台领件）；\n"
            "  3) 之后随时用 `POST /api/harness/bridge-test` 探它是否还活着。"
            % (harness_id, kind or "pending_poll")),
        "template_verified": True,
    }
    # ⚠️`bridge_selfcheck` 给的 advice 里会有「在**成员隔间目录**里运行 bridge.py」——
    #   那句话对**工作间**是对的（隔间是持久的），但在**验证间**里是错的：隔间验证完就删。
    #   这里补一句把方向掰正（不改 `bridge_selfcheck`，因为它在工作间场景仍是对的）。
    _adv = str(chk.get("advice") or "")
    _adv += ("\n⚠️ 注意：**验证间的隔间是一次性的**（验证完就删，里面的 `bridge.py` 也一并没了）——"
             "别去验证间目录里找桥脚本。要在 harness 侧**持久运行**，见本响应里的 "
             "`persistent_bridge.how_to_run_persistently`。")
    return {"success": bool(chk.get("success")), "stage": "selfcheck",
            "state": chk.get("state"), "reason": chk.get("reason"),
            "advice": _adv, "detail": chk.get("detail") or {},
            "sandbox_id": ws.workshop_id, "member_id": m.member_id,
            "draft_made": bool(draft_made), "had_template": had_template,
            "bridge_kind": kind, "bridge_started": bool(started),
            "bridge_start_error": start_err, "bridge_stopped": stopped,
            "persistent_bridge": _persist_hint}


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
    # ★2026-10-12：**必须说清"验的是什么范围"** —— 否则"verified"会被读成
    #   "harness 侧整条链路都通了"，而那可能超出了本次实际证明的东西：
    #     · 文件邮箱通道（`file_poll`）：验的是**完整链路**（平台→桥→你的 agent→回报）；
    #     · 桥测试通道（非邮箱型）：验的是**桥配置本身**（桥能连平台、领件、回报），
    #       而且**那只桥是平台在验证间里启动的** ⇒
    #       **"harness 侧是否真的运行这只桥、agent 能否回话"本次并未验证**。
    #   （把范围写进证据里，接入方和界面才不会过度解读。）
    _ch = str((evidence or {}).get("channel") or "")
    # ★2026-10-13：**通道名只留一套**。此前同一个东西有三个叫法：
    #   面向接入方的 `sent_via` 用 `inbox` / `pending_queue`，
    #   而这里落盘用 `file_mailbox` / `bridge_test_queue` —— 同一份数据两种词，
    #   谁读谁要猜。⇒ 落盘统一成**面向接入方那套**；读的时候两套都认（历史记录不失效）。
    _ch = {"file_mailbox": "inbox", "bridge_test_queue": "pending_queue"}.get(_ch, _ch)
    _scope = ("完整链路：平台 → 桥 → 你的 agent → 回报"
              if _ch in ("file_mailbox", "inbox") else
              "**仅桥配置**（桥能连平台/领件/回报；且这只桥是**平台在验证间里启动的**）——"
              "**harness 侧是否运行该桥、agent 能否回话，本次未验证**")
    ev = dict(evidence or {})
    ev["channel"] = _ch
    ev["verified_scope"] = _scope
    # ⚠️历史记录里也必须带范围 —— 原来这里 append 的是**原始 evidence**（不含 scope），
    #   于是"当时到底验了什么范围"这一步**没有落盘**：界面/接入方看响应知道，
    #   隔天回头看历史就分不清了。⇒ 落盘用带 scope 的那份。
    hist[-1]["evidence"] = dict(ev)
    meta["bridge_template_verified"] = hist[-5:]

    # ══════════════════════════════════════════════════════════════════════
    # ★★2026-10-13：**把"已验证"写进模板本体**（V42 F3 定稿要求，此前只写了历史列表）
    #
    # 老板的问题原话：「我要的跑通后注册具体模板实现了没？」
    # 实测（`agent_community/data/harnesses.json`）：跑通后模板本体仍是
    #   `{"name":"验证间草稿桥","kind":"pending_poll","params":{},"source":"draft"}`
    # —— **没有 verified / verified_at / verified_by / evidence**，params 还是空的，
    # 名字还叫"草稿"。而 V42 §F3 与 §143 明确要求模板带这几个字段。
    # ⇒ 半截功能：**登记了"验过"这件事，却没登记"这条模板验过了"**。
    # 现在补齐：合并实测参数 + 打上验证标记 + 把范围一起写进去。
    # ══════════════════════════════════════════════════════════════════════
    merged = dict(tpl.get("params") or {})
    for k, v in _measured_params(sess.info, tpl.get("kind", "")).items():
        merged.setdefault(k, v)          # 实测/注册声明里能确定的，补进去；已有的不覆盖
    tpl_written = _bt.put(sess.info, {
        **tpl,
        "params": merged,
        "verified": True,
        "verified_at": now,
        "verified_by": "sandbox",
        "evidence": dict(ev),
        "complete": True,
        "missing": [],
    }, source=str(tpl.get("source") or "draft"))
    _persist()
    return {"success": True, "harness_id": harness_id, "template_name": tpl_written.get("name", ""),
            "verified": True, "verified_at": now, "verified_by": "sandbox",
            "verified_scope": _scope,
            "evidence": ev,
            "template": tpl_written,
            "params_written": merged,
            "note": ("已把「跑通」的配置写进模板本体（`verified=true` / `verified_at` / `evidence`，"
                     "并补上能确定的参数），以后 `bridge/generate` 省略 `template` 时会用它；"
                     "范围：" + _scope)}


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
    # ① 停桥 —— ★只停**验证间自己起的那只**。
    #   ⚠️★2026-10-13 实测事故（用户视角最气人的一种）：这里原来传的是
    #   `info.bridge_dir`（该 harness **登记在册的桥目录**）—— 而第 5 步跑通之后，
    #   用户会在**那个目录**里常驻跑着自己的桥。于是"点一下自检"= **把用户跑通的桥杀掉**，
    #   事后 `bridges_running` 从 1 变 0，而平台只报"验证间已拆"，**没说自己停了用户的桥**。
    #   ⇒ 判据：**按验证间自己的目录停**（沙箱里那只临时桥的 argv 里就带这个路径）；
    #     验证间目录都算不出来时，**宁可不杀**，也绝不按 harness 的桥目录去扫。
    if callable(stop_bridge):
        try:
            sandbox_dir = ""
            try:
                mem = (getattr(ws, "members", None) or [{}])[0]
                sandbox_dir = _bt_mailbox(ws, mem) if mem else ""
            except Exception:  # noqa: BLE001
                sandbox_dir = ""
            if sandbox_dir:
                removed["bridge_stopped"] = stop_bridge(harness_id, sandbox_dir)
            else:
                removed["bridge_stopped"] = {
                    "killed": 0,
                    "note": "算不出验证间目录 ⇒ **没有停任何桥**（绝不按 harness 的桥目录去扫，那是用户的常驻桥）",
                }
        except Exception as e:  # noqa: BLE001
            removed["bridge_stopped"] = f"停桥失败（不阻断拆除）: {e}"
    # ② 删隔间产物（验证间目录随它一起删）
    # ⚠️★删目录是**同步 I/O**，在 async 里直接做会占住事件循环（目录大/磁盘忙时更久）。
    #   这里是"清扫"动作、不影响正确性 ⇒ 挪到工作线程（2026-10-12 全仓扫描扫出来的同族问题）。
    try:
        import asyncio as _a
        import shutil
        base = str(getattr(ws, "workspace_dir", "") or "")
        if base and os.path.isdir(base):
            await _a.to_thread(shutil.rmtree, base, True)
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
