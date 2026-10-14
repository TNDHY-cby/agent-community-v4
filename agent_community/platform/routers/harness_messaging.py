"""platform/routers/harness_messaging：Harness 消息与任务回报（V-13 自 harness.py 拆分）。

承载 message / task-result / pending-* / api-probe / api-message / auto-connect 等端点。
拆分纪律：路由路径、参数、响应体与拆分前逐字一致（纯搬迁，零行为变更）。
"""
from __future__ import annotations
from ..spawn import spawn_kwargs as _spawn_kwargs
from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect
from ..protocol import HarnessMessage
from ..protocol import WakeupMethod
from datetime import datetime
from ..harness_adapter import harness_manager
from ..api_wakeup import probe_http_api
from ..api_wakeup import send_http_api_message
from ..state import _bridge_tests_inflight, agents, assistant_history, assistant_history_max, pending_activations, pending_bridge_tests, pending_pre_register, pending_tasks, workshops
from ..audit import audit_log as _audit_log

router = APIRouter()

@router.post("/api/harness/message")
async def harness_message(request: Request):
    _audit_log.record("harness.message", actor="user", target="", detail="harness 消息")
    from ..server import Utf8JSONResponse, _sanitize_harness_content
    # ★★2026-10-12 补齐缺失的 import（**半截功能**："半截功能"专项审计用 AST 扫出来的）：
    #   `_harness_identity_check` 定义在 `routers/harness_common.py`，但本文件**没有导入它**
    #   ⇒ `POST /api/harness/message`（**外端 agent 的消息主入口**）一被调用就 `NameError`。
    #   （测试没抓到，是因为没有用例走这条端点的成功路径 —— 这正说明"没被调用过"不等于"没问题"。）
    from .harness_common import _harness_identity_check
    """接收来自 Harness 的消息回复"""
    body = await request.json()
    try:
        msg = HarnessMessage(**body)
    except Exception as e:
        return Utf8JSONResponse({"error": f"消息格式错误: {e}"}, status_code=400)
    # P1 身份凭证：目标 harness 已签发 token 时，强制校验 X-Agent-Token 头（向后兼容）
    _id_resp = _harness_identity_check(msg.harness_id, request.headers)
    if _id_resp:
        return _id_resp
    # V-14：外部 harness 主动上报内容做提示注入防护（进 pending futures 前）
    if isinstance(msg.content, str) and msg.content.strip():
        msg.content = _sanitize_harness_content(msg.content)
    harness_manager.handle_reply(msg)
    # 更新会话状态
    if msg.harness_id in harness_manager.sessions:
        harness_manager.sessions[msg.harness_id].last_heartbeat = datetime.now().isoformat()
        harness_manager.sessions[msg.harness_id].message_count += 1
    return {"success": True, "msg_id": msg.id}

@router.get("/api/harness/list")
async def list_harnesses():
    """列出所有已接入的 Harness"""
    return {"harnesses": harness_manager.list_sessions()}

@router.get("/api/harness/pending-activations")
async def harness_pending_activations(harness_id: str):
    """harness 桥轮询：领取该 harness 名下的待激活任务。"""
    acts = pending_activations.pop(harness_id, [])
    return {"activations": acts}

@router.get("/api/harness/pending-tasks")
async def harness_pending_tasks(harness_id: str):
    """harness 桥轮询：领取该 harness 名下的待执行任务。"""
    tasks = pending_tasks.pop(harness_id, [])
    return {"tasks": tasks}

@router.post("/api/harness/task-result")
async def harness_task_result(request: Request):
    from ..server import _append_msg, _apply_harness_reply, _do_complete_workshop, _do_continue_workshop, _get_decision_mode, _sanitize_harness_content, _settle_vote, save_state
    # ★★2026-10-13 补齐缺失的 import（**同族没改干净**，与上面 `harness_message` 一字不差的病）：
    #   2026-10-12 那次"半截功能"专项审计修了 `harness_message` 的 NameError（见上方注释），
    #   却**漏了这个函数** —— 于是凡是**回报里带 `harness_id`** 的任务回报都直接 500。
    #   实测：本项目自己的桥只带 workshop_id/member_id（文档格式里也没有 harness_id）⇒ 一直没炸；
    #   而一只**规规矩矩带上 harness_id 的外部 agent** 一回报就 500（今天真机验证时抓到）。
    #   ⇒ 教训：**"同族"要一起改**（规则 20）；并且"没被调用过"不等于"没问题"。
    from .harness_common import _harness_identity_check
    """harness 桥回报任务结果。"""
    body = await request.json()
    # P1 身份凭证：若该 harness 已签发 token，强制校验 X-Agent-Token（向后兼容）
    _hid0 = str(body.get("harness_id") or "").strip()
    if _hid0:
        _id_resp = _harness_identity_check(_hid0, request.headers)
        if _id_resp:
            # 被拒的回报也要留痕（审计的价值在可追责；target 带上是哪只 harness 试图回报）
            _audit_log.record("harness.task_result", actor="user", target=_hid0,
                              detail="任务回报（身份校验未通过，被拒）")
            return _id_resp
    # ── 委托链结果兑现：无论 ok 真假，都要把回报路由给等待中的 Future ──
    # 否则 bridge.execute_subtask / review_subtask 会空等 300s / 30s 超时，
    # orchestrator.execute_layers 整条任务链表现为“永久卡在 broadcasting”。
    _hid = _hid0   # 先初始化：下面 try 里可能抛异常，避免审计那行 NameError
    try:
        _hid = str(body.get("harness_id") or "").strip()
        _did = str(body.get("delegation_id") or "").strip()
        if not _hid:
            # 兼容旧桥：按 workshop/member 反查 harness
            _ws0 = workshops.get(body.get("workshop_id", ""))
            _mid0 = body.get("member_id")
            if _ws0 and _mid0:
                for _m in getattr(_ws0, "members", []):
                    if _m.member_id == _mid0:
                        _aid = getattr(_m, "agent_id", "") or ""
                        if _aid:
                            _hid = harness_manager.id_to_harness.get(_aid, "")
                        break
        if _hid and not _did:
            # 兜底：该桥只有一个待决委托时，视为它的结果
            _br = harness_manager.bridges.get(_hid)
            if _br:
                _pkeys = [k for k, _f in list(_br._pending.items()) if not _f.done()]
                if len(_pkeys) == 1:
                    _did = _pkeys[0]
        if _hid:
            _reply_content = _sanitize_harness_content(
                str(body.get("result") or body.get("text") or body.get("reply") or "")
            )  # V-14：外部内容提示注入防护
            _reply_msg = HarnessMessage(
                harness_id=_hid,
                direction="from_harness",
                msg_type="result",
                content=_reply_content,
                task_id=str(body.get("task_id") or ""),
                delegation_id=_did,
                payload={"ok": bool(body.get("ok", True)), "raw": body},
            )
            harness_manager.handle_reply(_reply_msg)
            print(
                f"[harness_reply] 回报路由: harness={_hid} delegation={_did or '-'} ok={body.get('ok', True)}",
                flush=True,
            )
    except Exception as _e:
        print(f"[harness_reply] 回报路由异常: {_e}", flush=True)
    # ★2026-10-13：审计放在**解析完 harness 之后**。
    #   老写法 `target=""` 写在函数首行（还没解析 body），后果：一个平台接 N 只 harness 时，
    #   这条"任务回报"根本**查不到是谁报的** —— 审计的全部价值在可追责，查不到对象等于没记。
    #   这里 `_hid` 可能是 body 里带的，也可能是按 workshop/member **反查**出来的（兼容旧桥），
    #   用反查后的 id 才追得到账；两样都没有时才留空（那是格式问题，不是没记录）。
    _audit_log.record("harness.task_result", actor="user", target=_hid or _hid0,
                      detail="任务回报")
    ws = workshops.get(body.get("workshop_id", ""))
    if ws:
        mid = body.get("member_id")
        # ── R3：决策模式回报（组长独裁 decision / 举手表决 vote）──
        # 仅在 review 态且带明确裁决字段时接管收口；普通回报不受影响
        _dm = str(body.get("decision") or "").strip().lower()
        _vt = str(body.get("vote") or "").strip().lower()
        if ws.status == "review" and mid:
            if _dm in ("continue", "complete") and _get_decision_mode(ws.workshop_id) == "leader":
                _append_msg(ws, "notice", f"【组长裁决】组长回报 decision={_dm}，按裁决执行。", zone=3)
                if _dm == "complete":
                    _do_complete_workshop(ws, by="leader")
                else:
                    _do_continue_workshop(ws, by="leader")
                save_state()
            if _vt in ("continue", "complete") and _get_decision_mode(ws.workshop_id) == "vote":
                _settle_vote(ws, mid, _vt)
        # 通用回报接入：harness 通过 task-result 回报结果/发言，统一接入讨论区
        # （http_api 类 harness 也可通过 HTTP 直接回报到这里）
        result = _sanitize_harness_content(
            body.get("result") or body.get("text") or body.get("reply") or ""
        )  # V-14：进入讨论区前做提示注入防护
        hid = body.get("harness_id") or ""
        ok_flag = bool(body.get("ok", True))  # 桥 report_task 回传；旧桥未传默认 True（成功语义）
        # 结构化卡点上报：可选 status(done/blocked/progress/none，大小写不敏感) + summary 短文本
        status = body.get("status") or ""
        summary = body.get("summary") or ""
        if not ok_flag and (status or "").strip().lower() not in ("done", "blocked", "progress"):
            # 系统级失败占位（会话缺失/重建失败等）不是成员真实发言，但必须在讨论区可见：
            # 写入讨论区并标记为失败+系统消息（meta.status=failed / meta.system=True），
            # 同时 notify_leader=False —— 不推进组长事件水位、不参与事件判定，
            # 避免历史死循环「失败回报 → 唤醒组长 → 再次派发」。
            print(f"[harness_reply] {hid} 任务未完成回报（ok=false）已作为失败提示写入讨论区: {result[:100]}", flush=True)
            _apply_harness_reply(body.get("workshop_id", ""), mid or "", result, hid, source="harness_http",
                                 status="failed", summary=summary, notify_leader=False)
        else:
            _apply_harness_reply(body.get("workshop_id", ""), mid or "", result, hid, source="harness_http",
                                 status=status, summary=summary)
    return {"success": True}

@router.post("/api/harness/api-probe")
async def harness_api_probe(request: Request):
    _audit_log.record("harness.api_probe", actor="user", target="", detail="探测 HTTP API")
    from ..server import Utf8JSONResponse, save_state, validate_harness_api_url
    """探测 harness 自带 HTTP API 是否可用（http_api 类唤醒）。
    body: {"harness_id": "..."} 或 {"base_url": "...", "message_path": "/message"}
    若 base_url 缺省则从该 harness 注册信息取 api_base_url。
    探测成功自动把该 harness 的 wakeup_method 升级为 http_api。
    """
    body = await request.json()
    hid = (body.get("harness_id") or "").strip()
    base_url = (body.get("base_url") or "").strip()
    message_path = (body.get("message_path") or "/message").strip()
    if not base_url and hid:
        sess = harness_manager.sessions.get(hid)
        if sess and sess.info:
            base_url = getattr(sess.info, "api_base_url", "") or ""
            message_path = getattr(sess.info, "api_message_path", "") or message_path
    if not base_url:
        return Utf8JSONResponse({"error": "base_url 或 harness_id 需要提供（且该 harness 已配置 api_base_url）"}, status_code=400)
    ok, err = validate_harness_api_url(base_url)
    if not ok:
        return Utf8JSONResponse({"error": f"base_url 校验失败: {err}"}, status_code=400)
    ok, detail = probe_http_api(base_url, message_path)
    if ok and hid:
        sess = harness_manager.sessions.get(hid)
        if sess and sess.info:
            sess.info.wakeup_method = WakeupMethod.HTTP_API
            sess.info.api_base_url = base_url
            sess.info.api_message_path = message_path
            harness_manager.register(sess.info)
            save_state()
    return {"success": ok, "harness_id": hid, "detail": detail}

@router.post("/api/harness/api-message")
async def harness_api_message(request: Request):
    _audit_log.record("harness.api_message", actor="user", target="", detail="HTTP API 消息")
    from ..server import Utf8JSONResponse, validate_harness_api_url
    """向 harness 的 HTTP API 推送一条测试消息（验证 http_api 唤醒链路）。
    body: {"harness_id": "...", "content": "...", "from_id": "..."}
    平台用该 harness 的 api_base_url/api_message_path 推送。
    """
    body = await request.json()
    hid = (body.get("harness_id") or "").strip()
    content = (body.get("content") or "").strip()
    if not hid or not content:
        return Utf8JSONResponse({"error": "harness_id 和 content 必填"}, status_code=400)
    sess = harness_manager.sessions.get(hid)
    if not sess or not sess.info:
        return Utf8JSONResponse({"error": f"harness {hid} 未注册"}, status_code=404)
    base_url = getattr(sess.info, "api_base_url", "") or ""
    message_path = getattr(sess.info, "api_message_path", "") or "/message"
    if not base_url:
        return Utf8JSONResponse({"error": f"harness {hid} 未配置 api_base_url，无法 HTTP 推送"}, status_code=400)
    ok, err = validate_harness_api_url(base_url)
    if not ok:
        return Utf8JSONResponse({"error": f"harness {hid} 的 api_base_url 校验失败: {err}"}, status_code=400)
    from_id = body.get("from_id") or f"agent-community-{hid}"
    ok, detail = send_http_api_message(base_url, content, from_id=from_id, message_path=message_path)
    return {"success": ok, "ok": ok, "harness_id": hid, "detail": detail, "base_url": base_url}

@router.get("/api/harness/{harness_id}/activation_prompt")
async def get_harness_activation_prompt(harness_id: str):
    from ..server import Utf8JSONResponse
    """查看外端 Harness 的唤醒提示词模板（含 {role}/{workspace_dir} 占位符，激活时由平台填充）。"""
    sess = harness_manager.sessions.get(harness_id)
    if not sess:
        return Utf8JSONResponse({"error": "harness not found"}, status_code=404)
    prompt = sess.metadata.get("activation_prompt") or ""
    if not prompt:
        prompt = (sess.info.metadata or {}).get("activation_prompt") or ""
    return {"success": True, "harness_id": harness_id, "agent_id": sess.agent_id, "prompt": prompt}

def _probe_pid_watching(inbox: str) -> int:
    """默认实现：扫进程找**看着这个邮箱的桥**（可被测试替换，见 `_set_watch_probe`）。

    ★2026-10-12 改：**与 `bridge_instances` 统一到同一套判据**（原来两处各写一套）。
    **原判据的三个毛病**（同一家族、我这轮连续栽过）：
      ① **不检查是不是桥** —— 任何命令行里提到该邮箱路径的进程（我自己的测试脚本、
         一条 `dir`、一个 `python -c`）都会被当成"已有桥在看着"；
      ② **不认解释器** —— powershell/cmd 包裹进程的命令行同样含路径；
      ③ 与 `bridge_instances` 口径分叉，两处迟早打架。
    **误判"已有桥在跑"的后果最重**：auto-connect 会**跳过起桥** ⇒
    **桥根本没起、界面却看着正常**（本项目最怕的死状态）。
    ⇒ 改为复用 `_probe_pids_watching` + `_chain_roots`（在跑桥脚本 / 只认解释器 / 父子链算一只）。
    **不对称性**：宁可**少认**也不能多认 —— 少认了会再起一只，而**单实例锁会拦下并说明理由**；
    多认了则是**静默不启动**，用户无从发现。
    """
    inbox = (inbox or "").strip()
    if not inbox:
        return 0
    try:
        roots = _chain_roots(_probe_pids_watching(inbox))
        return int(roots[0]) if roots else 0
    except Exception as e:  # noqa: BLE001
        print(f"[auto-connect] 扫进程探测失败（按未探测到处理）: {e}", flush=True)
        return 0


#: 可替换的探测实现（测试用；生产恒为 `_probe_pid_watching`）
_WATCH_PROBE = _probe_pid_watching


def _set_watch_probe(fn) -> None:
    """替换/还原"桥是否在看着某邮箱"的探测实现（供测试注入，避免真扫进程）。"""
    global _WATCH_PROBE
    _WATCH_PROBE = fn or _probe_pid_watching


def _probe_pids_watching(needle: str) -> list:
    """扫进程命令行，返回**所有**匹配该线索的 pid（不只第一个）。

    ★2026-10-12 新增：真机实测发现 `example-harness-01` 名下**同时有 4 只桥在跑**
      （那个 agent 在"路径引号被拆断、进程秒退"时重试了几次，每次都真的起了一只）。
      **同一 harness 多只桥 ⇒ 重复投递 / 竞争取件 / 心跳互相覆盖** —— 而平台**一个字都没说**。
      （`harness_messaging` 里那句注释早就写着"同一邮箱多只桥 ⇒ 重复投递"，
        问题是这个事实从来没被**报告出去**。）
      ⇒ 本函数给它一个可报告的计数。只有一只时与 `_probe_pid_watching` 等价。
    """
    needle = (needle or "").strip()
    if not needle:
        return []
    try:
        import json as _json
        import os as _o2
        import subprocess as _sp
        # ⚠️★**不要**对线索做 `[System.IO.Path]::GetFullPath`：
        #   本函数既可能收到**绝对路径**（邮箱），也可能收到 **harness_id**（一个普通标识符）。
        #   `GetFullPath('example-harness-01')` 会把它解析成"平台当前目录下的同名文件"，
        #   于是拿一个**根本不存在的路径**去匹配进程命令行 ⇒ **永远匹配不到**。
        #   （实测：名下明明有 4 只桥在跑，却回 `bridges_running = 0`。我又栽在"想当然"上。）
        #   ⇒ 原样用线索，并额外试一次"正斜杠"变体（进程命令行里两种都可能出现）。
        # ⚠️判据必须**同时**满足两条，否则会误计：
        #   ① 命令行里含该 harness 的线索；
        #   ② **确实是在跑桥脚本**（`*bridge.py*`）。
        #   只按①判会把我自己（或用户）任何"命令行里提到这个 harness_id"的进程都算成桥 ——
        #   实测：一个 `python -c "...example-harness-01..."` 的调用脚本被计成了第 5 只桥。
        #   ③ ★只认**解释器**（`Name -like 'python*'`）：powershell/cmd 包裹进程的命令行里
        #     同样含脚本路径，混进来会让"链根"变成那个包裹进程，而且
        #     **同一个 powershell 里连起两只桥时会被归并成一只（漏数）**。
        #     （实测：`bridge_pids` 里曾混进 powershell 的 43440 —— 归并侥幸答对，但靠运气。）
        ps = ("$needle = $env:AC_NEEDLE; "
              "$me = [int]$env:AC_SELF_PID; "
              "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -and "
              "$_.CommandLine -like '*bridge.py*' -and "
              "$_.Name -like 'python*' -and "
              "$_.ParentProcessId -ne $me -and "
              "($_.CommandLine -like ('*' + $needle + '*') -or "
              " $_.CommandLine -like ('*' + ($needle -replace '\\\\','/') + '*')) } | "
              "Select-Object ProcessId,ParentProcessId | ConvertTo-Json -Compress")
        env = dict(_o2.environ)
        env["AC_NEEDLE"] = needle
        env["AC_SELF_PID"] = str(_o2.getpid())
        r = _sp.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True,
                    text=True, encoding="utf-8", errors="replace", timeout=30, env=env)
        out = (r.stdout or "").strip()
        if not out or out == "null":
            return []
        v = _json.loads(out)
        if isinstance(v, int):          # 只有一个结果时 ConvertTo-Json 返回标量
            v = [v]
        if isinstance(v, dict):         # 单个对象（无数组）也包一层
            v = [v]
        out_p = []
        for x in (v or []):
            try:
                out_p.append({"pid": int(x.get("ProcessId") or x.get("pid")),
                              "ppid": int(x.get("ParentProcessId") or x.get("ppid") or 0)})
            except Exception:  # noqa: BLE001
                continue
        return out_p
    except Exception:  # noqa: BLE001
        return []


def _chain_roots(matches):
    """把匹配到的桥进程归并成**逻辑桥**：父子链算一只，只数**链根**。

    ★为什么必须按链根数：`python.exe` 可能是**外壳**（它会重新拉起真正的解释器，
      命令行一模一样）⇒ 一只桥对应两个同名进程。按进程数计 ⇒ 每只桥被数成 2 倍，
      **给用户报假的"重复桥"告警**（2026-10-12 实测发生过）。
    判据：父进程**不在**匹配集合里 ⇒ 它是这条链的根 ⇒ 计 1。
      A→B→C（都匹配）⇒ 只数 A；两次独立启动（父都是 powershell）⇒ 数 2。
    """
    matched = {m.get("pid") for m in (matches or [])}
    # ★取的是**链根自己的 pid**（父进程不在匹配集合里的那些），不是父 pid —— 我第一版写反过
    return sorted(m.get("pid") for m in (matches or [])
                  if m.get("ppid") not in matched)


async def bridge_instances_async(harness_id: str) -> dict:
    """`bridge_instances` 的**异步版**：把"要跑 PowerShell 的同步扫描"挪到工作线程。

    ★2026-10-12 实测（**我自己引进的缺陷**，与早上 `time.sleep` 那次同一族）：
      我把 `bridge_instances` 加进 `POST /api/harness/bridge-test` 后，实测该端点耗时
      **1.28s**（对照：`/api/status` 0.017s、`/api/agents` 0.055s）——
      这 1.2 秒里**平台无法响应任何请求**；机器忙时 PowerShell 启动可拖到 `timeout=30` 上限。
      症状表现在**外**（差点查错方向）：watchdog 日志出现 `TimeoutError: timed out`、
      以及"**进程还在、却不再收连接**"（`TCP not listening`）并自动重启。
      ⇒ 规则：**async 端点里绝不做同步阻塞的事**（子进程 / sleep / 扫描 … 一律挪线程）。
    """
    import asyncio as _a
    return await _a.to_thread(bridge_instances, harness_id)


def bridge_instances(harness_id: str) -> dict:
    """这个 harness 名下**有几只桥在跑**（供响应体如实报告，别让用户蒙在鼓里）。

    ⚠️**它内部会同步跑 PowerShell（阻塞）** ⇒ 在 async 端点里请用 `bridge_instances_async`。
    """
    matches = _probe_pids_watching(harness_id)
    roots = _chain_roots(matches)                 # ★逻辑桥数（链根），不是进程数
    pids = [m.get("pid") for m in matches]
    # ★★平台**自己起的**桥必须从登记表来（2026-10-12 实测补）：
    #   进程扫描按 `ParentProcessId ≠ 平台` **有意排除平台的子进程**（避免把"探测自己起的
    #   子进程"当成桥）⇒ **平台自己起的桥在扫描里根本看不见**（实测：auto-connect 起了一只，
    #   `bridges_running` 却报 0）。而 `_bp.record(hid, proc)` 正是 auto-connect 起桥后
    #   登记的（V-15 步骤2）⇒ **两路合并才准**（与 `_bridge_pid_watching` 同一顺序口径：
    #   先登记表、再进程扫描）。
    reg_pid = 0
    try:
        from .. import bridge_procs as _bp
        rec = ((_bp.describe() or {}).get("bridges") or {}).get(harness_id) or {}
        if rec.get("pid") and str(rec.get("status", "")) == "alive":
            reg_pid = int(rec["pid"])
    except Exception:  # noqa: BLE001
        reg_pid = 0
    if reg_pid and reg_pid not in pids:
        roots = sorted(set(roots) | {reg_pid})
        pids = sorted(set(pids) | {reg_pid})
    return {"count": len(roots), "pids": pids, "logical_pids": roots,
            "from_registry": reg_pid,
            "warning": ("" if len(roots) <= 1 else
                        ("⚠️ 检测到该 harness 名下**同时有 %d 只在跑的桥** —— "
                         "同一 harness 多只桥会导致**重复投递 / 竞争取件 / 心跳互相覆盖**。"
                         "请只保留一只：把多余的停掉（它们通常是失败重试时多起的）。\n"
                         "（计数口径：**按「逻辑桥」数** —— 父子进程链算一只，不是按进程数，"
                         "所以 ≥2 才真的是有两只桥在跑。）\n"
                         "（说明：**新版桥脚本已自带单实例锁** —— 同 work-dir 起第二只会被"
                         "**拒绝启动并说明理由**。所以这里出现多只，通常是"
                         "① 用的是旧版桥脚本，或 ② 这几只是在加锁之前起的。"
                         "把多余的停掉、用同一条命令重起一只即可。）"
                         % len(roots)))}


def _bridge_pid_watching(harness_id: str, inbox: str) -> int:
    """**已经有一只桥在看着这个邮箱**吗？返回它的 pid，没有返回 0。

    ★2026-10-11 实测 bug（正是"唤不醒"的机制之一）：
      `auto-connect` 原来只用 `harness_manager.bridges` 判"桥已在跑"，而那个登记表
      **认不出平台起的 example 桥**（实测：平台上明明已有桥在看同一 --inbox，
      它仍判定"没在跑"）⇒ **每调一次就再起一只**：
        · 同一邮箱多只桥 ⇒ 重复投递；
        · ★更要命：桥是**起的时候**才把「叫醒命令」带上 ⇒ 用户后配的命令
          **永远不生效**，而界面显示"已配置" ⇒ 他配了、却依然唤不醒（`wake_pending_restart` 也永不出现）。
      判据改为**可自证的事实**：进程命令行里含该邮箱路径（桥启动参数里必然带 `--inbox <路径>`）。

    顺序（★很重要）：**先看平台自己的登记表**（`bridge_procs`，零 I/O、可被测试桩控制），
    再看进程扫描。理由：既有测试用 `_FakeProc` 桩（没有 `poll()`），
    只靠"扫进程 + poll"会既漏判、又让被测路径去扫真实进程。
    """
    # ① 平台登记表（唯一真源的存活记录）。接口是 `is_alive()` / `describe()`
    #    —— 我第一版又猜了个 `snapshot()`（**并不存在**）。教训：先 `dir()` 再写，别猜名字。
    #    ⚠️ 也别在这里按"存活"早退：**策略闸门必须先生效**（AI 发起要走审批，绝不能被
    #       "反正桥已在跑"绕过去）。所以这里**只取 pid 供返回体展示**，做不做早退由**闸门之后**决定。
    try:
        from .. import bridge_procs as _bp
        for hid_key, rec in (((_bp.describe() or {}).get("bridges")) or {}).items():
            if hid_key == harness_id and rec.get("status") == "alive" and rec.get("pid"):
                return int(rec["pid"])
    except Exception as e:  # noqa: BLE001
        print(f"[auto-connect] 读桥登记表失败（继续扫进程）: {e}", flush=True)
    # ② 进程扫描（登记表认不出平台示例桥时兜底）
    return int(_WATCH_PROBE(inbox) or 0)


def _wake_trigger_for(harness_id: str) -> str:
    """取该 harness 配置的「叫醒命令」**包装脚本**路径（没配就返回空串）。

    ★V38 第 2 步：桥要"叫醒 agent"必须拿到这个；平台**猜不到**，只能由用户在界面上配
    （`POST /api/harness/{hid}/wake` 写包装脚本）。
    ⚠️ 整段容错：拿不到就叫醒不了，但**绝不能让 auto-connect 因此崩**（失败要说话，不是要崩）。
    """
    try:
        from .. import bridge_wake as _bw
        # ⚠️ 别用 auto-connect 里的局部变量 `_pkg`（模块级不存在；我第一版就栽在这里，
        #    被 try/except 吞掉 ⇒ trigger 永远为空、**静默失效**）。
        #    ⚠️ 也别猜 `os` / `_os` 哪个在模块级（实测两个都不是！）—— **自己 import**，最稳。
        #   本文件在 <repo>/agent_community/platform/routers/ ⇒ 要 <repo> 需**上溯 4 层**
        #   （routers → platform → agent_community → <repo>）。我第一版只上溯 3 层，
        #   实测把包装脚本写进了 `<repo>/agent_community/agent_community/data/runtime/wake/`
        #   ——**多了一层**，与平台其余运行数据目录不一致。
        import os as _o
        root = _o.path.dirname(_o.path.dirname(_o.path.dirname(
            _o.path.dirname(_o.path.abspath(__file__)))))
        cur = _bw.read_wrapper(root, harness_id)
        return str(cur.get("path") or "") if cur.get("configured") else ""
    except Exception as e:  # noqa: BLE001
        print(f"[auto-connect] 读叫醒命令失败（不影响起桥）: {e}", flush=True)
        return ""


@router.post("/api/harness/auto-connect")
async def harness_auto_connect(request: Request):
    _audit_log.record("harness.auto_connect", actor="user", target="", detail="自动架桥（启进程）")
    from ..server import Utf8JSONResponse
    """一键自动架桥：按 wakeup_method 自动生成并启动桥进程（用户不当信息中转）。

    http_api  → 平台直接推送，无需桥，直接返回就绪
    file_poll → 后台启动 filepoll_harness_bridge.py
    acp       → 后台启动 acp_harness_bridge.py（需 acp_command 真实可执行）
    clipboard → 不能全自动，返回 manual + 粘贴提示（不阻塞）
    其他      → 后台启动 pending_poll_bridge.py（兜底）
    """
    import os as _os
    import subprocess as _sp
    from ..protocol import WakeupMethod

    body = await request.json()
    hid = str(body.get("harness_id") or "").strip()
    if not hid:
        return Utf8JSONResponse({"error": "缺少 harness_id"}, status_code=400)

    sess = harness_manager.sessions.get(hid)
    if not sess:
        return Utf8JSONResponse({"error": f"harness {hid} 未注册"}, status_code=404)
    info = sess.info

    # 幂等：桥已在跑就直接返回
    existing = harness_manager.bridges.get(hid)
    if existing and getattr(existing, "proc", None) and existing.proc.poll() is None:
        # ★2026-10-11 实测陷阱：桥是**起的时候**把「叫醒命令」带上的（见下面 file_poll 分支的
        #   `--trigger`）。而这里早退 ⇒ **桥已经在跑时，用户后配的叫醒命令静默无效** ——
        #   他会看到"已配成功"，却依然唤不醒 agent，且没有任何提示（正是"看起来正常"的假状态）。
        #   ⇒ 如实告诉他：要生效必须让桥重启一次（retrigger=true 可代劳）。
        _trig_now = _wake_trigger_for(hid)
        if _trig_now:
            # 不在这里替用户杀进程（本模块没有那套工具；我也不会去猜内部函数名——
            # 今天已经因为猜 `_pkg`/`_os`/`_audit_log` 栽过三次）。**如实说清 + 给可用办法**：
            return {"success": True, "harness_id": hid, "status": "connected",
                    "bridge_type": "already-running", "pid": existing.proc.pid,
                    "wake_pending_restart": True,
                    "note": "桥已在运行，**但你配置的「叫醒命令」要等桥重启后才生效**。"
                            "两条路：①删掉这个 harness 的桥进程、再调一次本端点；"
                            "②在「已注册清单」里点该 harness 的「重新连接」（会重新起桥）。"}
        return {"success": True, "harness_id": hid, "status": "connected",
                "bridge_type": "already-running", "pid": existing.proc.pid,
                "note": "桥已在运行"}

    wm = getattr(info, "wakeup_method", None)
    from ...platform_address import base_url as _addr_base_url   # 平台地址一处真相
    base_url = _addr_base_url()
    # __file__ = .../agent_community/platform/routers/harness.py
    # _routers = .../agent_community/platform/routers
    # _pkg     = .../agent_community          (= _routers/../..)
    # _ex      = .../agent_community/examples
    _here = _os.path.dirname(_os.path.abspath(__file__))          # .../platform/routers
    _pkg = _os.path.dirname(_os.path.dirname(_here))              # .../agent_community
    _ex = _os.path.join(_pkg, "examples")                         # .../agent_community/examples

    # 选桥脚本 + 参数
    bridge_cmd: list[str] | None = None
    bridge_type = ""
    # ⚠️ 2026-10-11：**这个变量必须在所有分支之前初始化**。
    #   我昨天加"已有桥就别重复起"时，把它只写进了 FILE_POLL 分支，却在函数末尾共用 ⇒
    #   走 acp / http 等**其它分支**时直接 `UnboundLocalError` ⇒ 接口 **500**（实测复现）。
    #   教训：跨分支共用的局部变量，声明要放在分支之前。
    _already_pid = 0
    if wm == WakeupMethod.HTTP_API:
        # ★2026-10-12 同修：`http_api` **不是桥型**（合法桥型只有
        #   file_poll/cli_acp/pending_poll/script_framework）—— 这一型**平台直接推送、根本没有桥**。
        #   原来回 `"bridge_type": "http_api"` 会让接入方去找一只不存在的桥。
        return {"success": True, "harness_id": hid, "status": "ready",
                "bridge_started": False, "bridge_type": None,
                "wakeup_method": "http_api",
                "note": "http_api 类由**平台直接推送**，**不需要桥**（所以 bridge_type 为空是正常的）"}
    elif wm == WakeupMethod.FILE_POLL:
        script = _os.path.join(_ex, "filepoll_harness_bridge.py")
        inbox = (getattr(info, "wakeup_dir", "") or "").strip()
        if not _os.path.isfile(script):
            return Utf8JSONResponse({"error": f"桥脚本不存在: {script}"}, status_code=500)
        if not inbox:
            return {"success": True, "harness_id": hid, "status": "manual",
                    "bridge_type": "file_poll",
                    "note": "file_poll 需要 wakeup_dir（inbox 目录），注册时未填，请手动架桥"}
        # ★2026-10-11：记下"是不是已有桥在看着这个邮箱"（可靠判据，见 _bridge_pid_watching）。
        #   原来的早退只看 harness_manager.bridges，认不出平台起的桥 ⇒ 每调一次再起一只
        #   ⇒ ①重复投递 ②**用户后配的叫醒命令永不生效**（桥是起的时候才带上它）。
        #   ⚠️ 但**不能在这里直接返回**：策略闸门必须先跑（AI 发起要走审批，不能被它绕过）。
        #      所以只取 pid，真正的"不重复启动"判断放在闸门**之后**。
        # ⚠️★必须挪到**工作线程**：`_bridge_pid_watching` 内部要跑 PowerShell（同步阻塞），
        #   直接在 async 端点里调会卡死事件循环（实测同类端点 0.02s → 1.28s，
        #   机器忙时可达 30s；外部症状 = watchdog 报 TimeoutError + 自动重启）。
        import asyncio as _a
        _already_pid = await _a.to_thread(_bridge_pid_watching, hid, inbox)
        bridge_cmd = ["-u", script, "--harness-id", hid, "--inbox", inbox, "--platform", base_url]
        bridge_type = "file_poll"
        _trig = _wake_trigger_for(hid)
        if _trig:
            # ★V38 第 2 步：把用户配的「叫醒命令」传给桥 —— 否则桥只落盘、**不会叫醒 agent**
            #   （实测教训：带空格/带参数的命令行直接传会被拆坏 ⇒ 传**包装脚本**的路径）
            bridge_cmd += ["--trigger", _trig]
    elif wm == WakeupMethod.ACP:
        script = _os.path.join(_ex, "acp_harness_bridge.py")
        if not _os.path.isfile(script):
            return Utf8JSONResponse({"error": f"桥脚本不存在: {script}"}, status_code=500)
        if not (getattr(info, "acp_command", "") or "").strip():
            # ★2026-10-12：`bridge_type` 必须是**合法桥型名**。
            #   合法桥型只有 `file_poll / cli_acp / pending_poll / script_framework`
            #   （见 `bridge_catalog.CATALOG`）—— 原来是 `"acp"`（**非法**，那是 wakeup_method 的名字）。
            #   词汇混用会让下游（界面/文档/接入方）拿到一个"世界上不存在的桥型"。
            return {"success": True, "harness_id": hid, "status": "manual",
                    "bridge_type": "cli_acp",
                    "note": "cli_acp 类需要 acp_command，注册时未填，请手动架桥"}
        bridge_cmd = ["-u", script, "--harness-id", hid, "--url", base_url]
        bridge_type = "cli_acp"
    elif wm == WakeupMethod.CLIPBOARD:
        # ★★2026-10-12 实测修：这一段原来回 `"bridge_type": "clipboard"` ——
        #   而 **`clipboard` 不是桥型**（合法桥型只有 file_poll/cli_acp/pending_poll/script_framework），
        #   它是 **`wakeup_method`** 的取值。把二者混为一谈，接入方拿到就会以为"clipboard 是一种桥"。
        #   ⇒ 三件事分开说清：
        #     ① **本次没有起桥**（`bridge_started=False`）——不要假装有；
        #     ② 若要走桥，**建议的桥型**是 `pending_poll`（单独字段、标明是"桥型"）；
        #     ③ **激活提示词去哪儿拿**要给出**确切端点**（原 note 只说"见激活提示"，接入方无处可寻）。
        return {"success": True, "harness_id": hid, "status": "manual",
                "bridge_started": False,
                "wakeup_method": "clipboard",
                "bridge_type": None,                  # 没有起桥 ⇒ 不能报一个桥型
                "suggested_bridge_kind": "pending_poll",
                "suggested_bridge_kind_note": (
                    "`pending_poll` 是**桥型**（可选：让桥轮询取件并落盘到工作目录，"
                    "再由用户粘贴给你）。它与 `wakeup_method` 是两回事 —— "
                    "你的 `wakeup_method` 仍是 `clipboard`（人工中转）。"),
                "activation_prompt_url": "/api/harness/%s/activation_prompt" % hid,
                "note": ("clipboard（人工中转）类：**平台无法自动叫醒你**，本次**没有起桥**，"
                         "属正常（不是失败）。要拿到需要你执行的内容，请取"
                         "「激活提示词」：`GET /api/harness/%s/activation_prompt`，"
                         "把返回内容交给用户、由用户**手动粘贴**给你。"
                         "若希望走桥自动取件，可用 `bridge_type=pending_poll` 架一只桥"
                         "（桥会把待办落到工作目录，你读它、写 replies，桥转发回平台）。" % hid)}
    else:
        # 兜底：pending_poll
        script = _os.path.join(_ex, "pending_poll_bridge.py")
        if not _os.path.isfile(script):
            return Utf8JSONResponse({"error": f"桥脚本不存在: {script}"}, status_code=500)
        work_dir = (getattr(info, "wakeup_dir", "") or "").strip() or _os.path.join(_pkg, "data", "bridges", hid)
        bridge_cmd = ["-u", script, "--harness-id", hid, "--url", base_url, "--work-dir", work_dir]
        _trig2 = _wake_trigger_for(hid)
        if _trig2:
            # 同一口径：兜底桥也吃 --trigger（见 file_poll 分支的说明）
            bridge_cmd += ["--trigger", _trig2]
        bridge_type = "pending_poll"

    # 后台启动桥进程（DETACHED，父进程退出不带走）
    if bridge_cmd is None:
        return Utf8JSONResponse({"error": "未能确定桥脚本"}, status_code=500)

    # ── V-14 策略闸门：这里是真 subprocess.Popen 启动进程（拍板口径的最严档）──
    # actor 分级是关键：operator（人从本机 UI 点一键注册）走 actor_override 放行，
    # 不堵 V-12 的主流程；external_ai / harness（AI 自主架桥）出厂 ASK，需人批后才启动。
    # 威胁模型是「AI 驱动的代码执行」，不是「操作者自己点按钮」。
    from ..policy import (
        BRIDGE_LAUNCH,
        actor_from_request,
        blocked_message,
        check,
        pending_message,
    )
    _ev, _pending = check(
        BRIDGE_LAUNCH,
        target=f"{hid}:{bridge_type}",
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

    # ★2026-10-11：闸门已过 ⇒ 这里才判断"是否已有桥在看着这个邮箱"，**不重复启动**。
    #   放在闸门之后是刻意的：AI 发起的启动请求必须照常走审批（上面 202 分支），
    #   不能被"反正已经有一只桥在跑"绕过。
    if _already_pid:
        _trig_have = _wake_trigger_for(hid)
        _audit_log.record("harness.auto_connect.reuse", actor="user", target=hid,
                          detail=f"已有桥在看邮箱（pid={_already_pid}），未重复启动")
        return {"success": True, "harness_id": hid, "status": "connected",
                "bridge_type": bridge_type, "pid": _already_pid,
                "wake_pending_restart": bool(_trig_have),
                "note": ("已有一只桥在看这个邮箱（pid=%d）；**没有重复启动**。" % _already_pid)
                        + ("但你配了「叫醒命令」，它要**桥重启后**才生效 —— 先停掉这只桥再调本端点，"
                           "或在界面点该 harness 的「重新连接」。" if _trig_have else "")}

    py_exe = _os.environ.get("AC_PYTHON") or _os.sys.executable
    full_cmd = [py_exe] + bridge_cmd
    try:
        env = dict(_os.environ)
        env["示例Harness-E_SUPERVISOR_PID"] = str(_os.getpid())
        if bridge_type == "pending_poll":
            _os.makedirs(work_dir, exist_ok=True)
        proc = _sp.Popen(
            full_cmd,
            stdout=_sp.DEVNULL,
            stderr=_sp.DEVNULL,
            **_spawn_kwargs(),
            env=env,
        )
    except Exception as e:
        return Utf8JSONResponse({"error": f"桥启动失败: {e}"}, status_code=500)

    # 等 2 秒验证存活
    #
    # ⚠️★必须用 **`await asyncio.sleep`**（2026-10-12 全仓扫描扫出）：原来是
    #   `time.sleep(2.0)` —— 在 async 端点里**同步睡 2 秒 = 事件循环被占住 2 秒**，
    #   期间平台不响应任何请求；而 auto-connect 正是**用户起桥时必调**的端点，
    #   再叠上同步扫描（现已挪线程）就更容易触发 watchdog 的"卡住→重启"。
    #   （async 里只该 await，不该 sleep；这条与"把阻塞活挪线程"是同一条纪律。）
    import asyncio as _a2
    await _a2.sleep(2.0)
    alive = proc.poll() is None

    # ── V-15 步骤2：把 proc 登记进存活表 ──────────────────────
    # 原实现的 proc 是**局部变量，函数返回即丢** —— 平台事后完全不知道这个桥活着没活着，
    # 于是 `_dispatch_to_harness` 只能无条件说"已入队（等桥领取）"（假成功）。
    # 现登记进 bridge_procs，让队列分支有非阻塞、零 I/O 的存活源可查。
    from .. import bridge_procs as _bp
    if alive:
        _bp.record(hid, proc, cmd=" ".join(full_cmd))
    else:
        _bp.forget(hid)          # 启动即退，别在存活表里留脏记录

    return {
        "success": True,
        "harness_id": hid,
        "status": "connected" if alive else "failed",
        "bridge_type": bridge_type,
        "pid": proc.pid if alive else None,
        "cmd": " ".join(full_cmd),
        "note": "" if alive else "桥进程启动后立即退出，请检查 harness 配置",
    }

