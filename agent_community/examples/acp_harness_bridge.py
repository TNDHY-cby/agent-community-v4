"""ACP 类 harness 桥示例 — 独立进程，代表一个真实 harness 接入平台。

这是一个【参考实现/示例】：默认用虚拟示例名「示例Harness-ACP」注册。
**请通过 --harness-id 传入你自己的 harness_id**，并在 build_harness_info 里
改成你自己的 acp_command / acp_cwd / 模型信息，否则不会收到任何任务。

运行后：
1. 注册自己（POST /api/harness/register）
2. 周期心跳（POST /api/harness/heartbeat），保持在线
3. 轮询领取激活任务 → spawn 会话 → 读 hall.md → 回「收到」→ 会话【持久化不关】
4. 轮询领取工作任务 → 发给对应会话 → 用自己的工具真干活 → 回报结果

关键：一个桥连接（一个 harness）维护多个会话（多个员工），会话常驻待命。

用法：
    python -m agent_community.examples.acp_harness_bridge --url <平台地址> --harness-id 你的harness_id
"""

from __future__ import annotations

import argparse
import json
import time
import threading
import urllib.request
import urllib.parse
import os
import sys
from pathlib import Path

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from agent_community.platform.acp_harness_bridge import AcpHarnessBridge  # noqa: E402

# ★2026-10-13：平台地址**跟着 AC_PORT 走**（端口因人而异，别写死本机地址）。
def _platform_url_default():
    import os as _os
    return "http://127.0.0.1:" + str(_os.environ.get("AC_PORT") or "18920")


# 虚拟示例名：请用 --harness-id 覆盖
HARNESS_ID = "示例Harness-ACP"

# ★2026-10-13（V44 #13）：ACP 拉起命令/工作目录**必须能传进来**。
#   原来 `AcpHarnessBridge()` 无参构造只会去读环境变量 `AC_ACP_COMMAND`，
#   而全机没人设它 ⇒ 桥领到件却**永远拉不起 agent**（必抛「未配置 ACP 启动命令」）。
#   ⇒ 三级来源：命令行参数 > 环境变量 > 空（空就如实报错，不假成功）。
ACP_COMMAND = ""
ACP_CWD = ""

# ★2026-10-13（V44 #14）：Agent-Token。平台注册时会签发并存 `metadata.agent_token`；
#   `/api/harness/message` 与 `/api/harness/task-result` **强制校验** `X-Agent-Token`，
#   桥不带就会被自己拦下（401）。这里三级来源：命令行 > 环境变量 > 向平台问一次。
AGENT_TOKEN = ""

# 一个持久连接，多个会话（1 harness → N 员工）
_bridge: AcpHarnessBridge | None = None
_sessions: dict[str, str] = {}  # key = f"{workshop_id}:{member_id}" → session_id

# ★2026-10-13：与平台模板一致 —— `base` 是**模块级全局**（由 main 赋值）。
#   为什么：`handle_bridge_test` 这类"顺手回报"的函数拿不到参数，模板里就是走全局
#   （实测踩过：示例里漏了这句 ⇒ 桥测试回报 `name 'base' is not defined` ⇒ 第 4 步永远 no_reply）。
base = ""   # 由 main 按 --url/AC_PORT 填


def build_harness_info() -> dict:
    """**只声明桥自己真知道的东西**（V44 #12 的教训见 `register_bridge()`）。"""
    return {
        "harness_id": HARNESS_ID,
        "harness_name": HARNESS_ID,
        "harness_version": "0.1.0",
        "harness_type": "cli-agent",
        "transports": ["http"],
        "callback_url": "",
        "wakeup_method": "acp",
        "wakeup_url": "",
        "wakeup_dir": "",
        # ★别在这里写 `your-acp-command` 这类**占位示例值**：注册接口是幂等覆盖，
        #   写进来就会把用户/平台已经填好的真值冲掉（V44 #12）。命令请用 --acp-command 传。
        "acp_command": ACP_COMMAND,
        "acp_cwd": ACP_CWD,
        "can_wake": False,
        "waking_models": [],
        "waking_protocols": [],
        "ai": {
            "model_name": "unknown-model",
            "provider": "unknown",
            "version": "",
            "capabilities": ["coding", "file_ops", "web_search"],
            "max_tokens": 128000,
            "can_see": False,
            "can_code": True,
            "can_browse": True,
            "can_file_ops": True,
            "description": "示例 Harness（ACP 类）：通过 ACP 拉起新会话",
        },
        "tools": [
            {"name": "bash", "description": "执行命令", "parameters": {}, "capability_tag": "coding"},
            {"name": "read_file", "description": "读文件", "parameters": {}, "capability_tag": "file_ops"},
            {"name": "write_file", "description": "写文件", "parameters": {}, "capability_tag": "file_ops"},
        ],
        "description": "示例 Harness（ACP 类），通过 ACP 拉起新会话",
    }


def _headers() -> dict:
    """所有请求都带上身份凭证（不带会被平台自己的身份校验拦下）。"""
    h = {"Content-Type": "application/json"}
    if AGENT_TOKEN:
        h["X-Agent-Token"] = AGENT_TOKEN
    return h


def post_json(url: str, body: dict) -> dict:
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(),
        headers=_headers(), method="POST",
    )
    return json.loads(urllib.request.urlopen(req, timeout=30).read())


def get_json(url: str) -> dict:
    req = urllib.request.Request(url, headers=_headers(), method="GET")
    return json.loads(urllib.request.urlopen(req, timeout=30).read())


def _warn_token_source() -> None:
    """没拿到 token 时**如实说清去哪取**（不假成功、也不编一个不存在的端点）。

    什么时候需要它：平台给该 harness **启用过** Agent-Token 时，
    `message` / `task-result` 会强制校验 `X-Agent-Token`，桥不带就被自己拦下（401）。
    正确来源两条（**都已实测可用**）：
      ① 平台生成的桥里就已经注入了（推荐直接用 `POST …/bridge/generate` 生成的桥）；
      ② 手工跑本示例时，用 `--agent-token` / 环境变量 `AC_AGENT_TOKEN` 传入 ——
         值可从平台 `GET /api/harness/{hid}` 的 `agent_token` 字段（需带平台 token 调用）拿到。
    """
    if AGENT_TOKEN:
        print("[桥] 已带 Agent-Token（不打印内容）", flush=True)
        return
    try:
        r = get_json(f"{base}/api/harness/{urllib.parse.quote(HARNESS_ID)}")
        if (r or {}).get("agent_token"):
            print("[桥] ⚠️ 平台里该 harness 已启用 Agent-Token，而本次**没带上** —— "
                  "回报会被 401 拦下。请用 `--agent-token` 传入（或改用平台生成的桥，它已注入）。",
                  flush=True)
            return
    except Exception:  # noqa: BLE001
        pass
    print("[桥] 未带 Agent-Token（平台未启用时属正常）", flush=True)


def register_bridge(base: str, *, register: bool = False) -> None:
    """**默认不注册**（V44 #12）。

    为什么：这个接口是**幂等覆盖**，示例桥启动时用一堆 `your-xxx` 占位值重新注册，
    会把用户/平台已经填好的真实注册**冲掉**（实测发生过：一次真机接入里注册被覆盖，
    靠重新 register 才救回来）。
    ⇒ 默认 `--no-register`（人已注册好了，桥只消费）；
      显式 `--register` 时才写，且**只写桥自己真知道的东西**（命令/工作目录）。
    """
    if not register:
        print("[桥] 跳过注册（默认不复写注册信息；需要时用 --register）", flush=True)
        return
    info = build_harness_info()
    if not info.get("acp_command"):
        info.pop("acp_command", None)   # 空值也别覆盖已有值
    if not info.get("acp_cwd"):
        info.pop("acp_cwd", None)
    r = post_json(f"{base}/api/harness/register", info)
    print(f"[桥] 注册结果: {r.get('success')} agent_id={r.get('agent_id')}", flush=True)


def heartbeat_loop(base: str, interval: float = 30.0) -> None:
    while True:
        try:
            post_json(f"{base}/api/harness/heartbeat?harness_id={urllib.parse.quote(HARNESS_ID)}", {})
            print("[桥] 心跳在线", flush=True)
        except Exception as e:
            print(f"[桥] 心跳失败: {e}", flush=True)
        time.sleep(interval)


def ensure_bridge() -> AcpHarnessBridge:
    global _bridge
    if _bridge is None:
        # ★把命令/工作目录**交给桥**（不要指望环境变量 —— 全机没人设）。
        #   ⚠️ 用 shlex.split（与平台 `AcpHarnessBridge.from_harness_info` 同一口径）；
        #      Windows 路径请给**正斜杠** —— POSIX 模式的 shlex 会把反斜杠当转义吃掉。
        import shlex as _shlex
        cmd = _shlex.split(ACP_COMMAND) if ACP_COMMAND else None
        if not cmd:
            raise RuntimeError(
                "未配置 ACP 启动命令：请用 `--acp-command \"<命令>\"`（或环境变量 AC_ACP_COMMAND）传入。"
                "平台里该值存在 harness 注册信息的 `acp_command`，可用 GET /api/harness/<id> 查到。"
            )
        _bridge = AcpHarnessBridge(cmd=cmd, repo=(ACP_CWD or None))
        _bridge.start()
        _bridge.initialize()
    return _bridge


def member_key(act: dict) -> str:
    return f"{act.get('workshop_id')}:{act.get('member_id')}"


def handle_activation(act: dict) -> bool:
    """领到激活任务：开会话 → 读 hall.md → 回「收到」→ 会话持久化待命。"""
    bridge = ensure_bridge()
    key = member_key(act)
    workspace_dir = act.get("workspace_dir", "")
    role = act.get("role", "员工")
    print(f"[桥] 领取激活任务: {role} @ {workspace_dir}", flush=True)

    sess = bridge.new_session(workspace_dir)
    if sess is None:
        print("[桥] session/new 失败", flush=True)
        return False

    # 优先使用平台在注册时生成的 HA 专属激活提示词，回退默认模板
    tpl = act.get("activation_prompt") or (
        "你已进入工作间，你的角色是「{role}」，工作区坐标：{workspace_dir}。\n"
        "现在只做一件事：用你的文件工具读取工作区目录下的 hall.md 文件，"
        "读完原样回复：「收到，已进入工作状态。」\n"
        "不要执行 hall.md 里的任务，不要调用其他工具，回复完就停下等待后续指令。"
    )
    try:
        prompt = tpl.format(role=role, workspace_dir=workspace_dir)
    except Exception:
        prompt = tpl
    stop, text = bridge.prompt(
        sess.session_id,
        prompt,
        timeout=180,
    )
    ok = "收到" in text
    if ok:
        _sessions[key] = sess.session_id  # 会话持久化，不关闭
    print(f"[桥] 激活结果: ok={ok} reply={text.strip()[:80]}", flush=True)
    return ok


def handle_task(task: dict) -> tuple[bool, str]:
    """领到工作任务：发给对应会话，agent 用工具真干活，返回结果。"""
    bridge = ensure_bridge()
    key = member_key(task)
    sid = _sessions.get(key)
    if not sid:
        return False, "无对应会话（可能未激活）"
    task_text = task.get("task", "")
    print(f"[桥] 领取工作任务: {task_text[:60]}", flush=True)
    stop, text = bridge.prompt(
        sid,
        f"开始执行你的任务：\n{task_text}\n\n"
        f"请用你的工具（写文件/跑命令/搜索等）真正完成它，把成果写进工作区目录，"
        f"完成后用一句话汇报你产出了什么。",
        timeout=600,
    )
    return True, text


def report_activation(base: str, act: dict, ok: bool) -> None:
    try:
        post_json(f"{base}/api/harness/activation-result", {
            "workshop_id": act.get("workshop_id"),
            "member_id": act.get("member_id"),
            "status": "entered" if ok else "blocked",
        })
    except Exception as e:
        print(f"[桥] 激活回报失败: {e}", flush=True)


def report_task(base: str, task: dict, ok: bool, text: str) -> None:
    try:
        post_json(f"{base}/api/harness/task-result", {
            "workshop_id": task.get("workshop_id"),
            "member_id": task.get("member_id"),
            "ok": ok,
            "result": text[:2000],
        })
    except Exception as e:
        print(f"[桥] 任务回报失败: {e}", flush=True)


# ── ★2026-10-13：与平台模板生成的桥**对齐**（V44 #17）────────────────────────
#   `bridge_templates/cli_acp/bridge.py.tmpl`（真正持久跑的那只）有这三样，而本示例没有：
#     ① 桥测试轮询 + 回报  ② 单实例锁  ③ 桥坐标登记
#   后果实测（20:56，dsl 型真机）：第 4 步自检**桥起来了却不领测试件** ⇒ 永远 `not_picked`。
#   而验证间 `verify_sandbox.BRIDGE_SCRIPTS` 用的**正是本示例** ⇒ 这个缺口直接把 A 档挡在门外。
#   ⇒ 示例必须与模板同能力（要改就两边一起改，判据见 tests/test_v44_batch1_fixes.py）。

def handle_bridge_test(t: dict) -> None:
    """响应平台桥测试：回显原样 echo 回报，证明通道正常。"""
    try:
        post_json(f"{base}/api/harness/bridge-test-result", {
            "harness_id": HARNESS_ID,
            "test_id": t.get("test_id", ""),
            "ok": True,
            "echo": t.get("echo", ""),
        })
        print(f"[桥] 桥测试回报: test_id={t.get('test_id')} echo={str(t.get('echo',''))[:40]}", flush=True)
    except Exception as e:
        print(f"[桥] 桥测试回报失败: {e}", flush=True)


def register_bridge_path(base: str) -> None:
    """把桥文件所在目录登记给平台（平台按此定位桥、清理桥）。"""
    try:
        bridge_dir = str(Path(__file__).resolve().parent)
        post_json(f"{base}/api/harness/bridge-path", {
            "harness_id": HARNESS_ID,
            "bridge_dir": bridge_dir,
        })
        print(f"[桥] 桥坐标已登记: {bridge_dir}", flush=True)
    except Exception as e:
        print(f"[桥] 桥坐标登记失败: {e}", flush=True)


#: 必须持有引用，否则文件对象被回收 ⇒ 锁被释放
_LOCK_HANDLE = None


def acquire_single_instance(lock_path):
    """成功返回 (handle, "")；已有实例在跑返回 (None, 原因)。

    为什么用"持锁"而不是"pid 文件"：pid 文件在进程崩溃时留僵尸锁、pid 还会被系统复用 ⇒ 判错；
    持锁由操作系统在进程结束时自动释放（正常退出或崩溃都一样）—— 永不产生僵尸锁。
    """
    global _LOCK_HANDLE
    try:
        f = open(lock_path, "a+")
    except Exception as e:  # noqa: BLE001
        return None, f"无法创建锁文件（跳过单实例保护）：{e}"
    try:
        # ⚠️★必须先 seek(0)：`a+` 是追加模式、初始指针在 EOF，
        #   而 msvcrt.locking 锁的是"从当前指针起的 N 字节" ⇒ 不 seek 就锁在不同字节上、形同虚设。
        f.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        f.close()
        return None, "另一只桥正在使用同一个工作目录（拒绝重复启动）"
    try:
        f.seek(0)
        f.truncate()
        f.write(str(os.getpid()))
        f.flush()
    except Exception:  # noqa: BLE001
        pass
    _LOCK_HANDLE = f
    return f, ""


def _fill_acp_command_from_platform() -> None:
    """没传 `--acp-command` 时，**试着向平台取该 harness 已注册的 acp_command**。

    为什么值得试一次：验证间/auto-connect 起的桥**不带**这个参数（只传 `--harness-id/--url`），
    而平台注册信息里本来就有这条命令。
    ⚠️ 目前平台**没有**「读单个 harness 注册信息」的公开端点 ⇒ 取不到是正常的
    （实测 `GET /api/harness/<id>` = 404）。**取不到就如实说**，并在派活那一步明确报错（不假成功）。
    自己跑示例时请用 `--acp-command` 传入（或直接用平台生成的桥，那里已经注入好了）。
    """
    global ACP_COMMAND, ACP_CWD
    if ACP_COMMAND:
        return
    try:
        r = get_json(f"{base}/api/harness/{urllib.parse.quote(HARNESS_ID)}")
        ACP_COMMAND = str((r or {}).get("acp_command") or "").strip()
        if not ACP_CWD:
            ACP_CWD = str((r or {}).get("acp_cwd") or "").strip()
        if ACP_COMMAND:
            print("[桥] 已从平台取到注册时的 ACP 启动命令（不打印内容）", flush=True)
    except Exception:  # noqa: BLE001
        # 端点不存在 / 未授权都走到这里 —— 不阻断，也不伪装成功
        print("[桥] ⚠️ 未能从平台取到 ACP 启动命令（平台暂不提供该端点）——"
              "派活时会明确报错；请用 --acp-command 传入或改用平台生成的桥", flush=True)


def main() -> None:
    global base, HARNESS_ID, ACP_COMMAND, ACP_CWD, AGENT_TOKEN
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default=_platform_url_default())
    ap.add_argument("--harness-id", default=HARNESS_ID, help="你的 harness_id（默认是虚拟示例名，必须改）")
    # ★V44 #13：ACP 拉起命令/工作目录 —— 桥必须拿到它们才拉得起 agent
    ap.add_argument("--acp-command", default=os.environ.get("AC_ACP_COMMAND", ""),
                    help="拉起 agent 的命令（与注册信息 acp_command 同值；Windows 路径用正斜杠）")
    ap.add_argument("--acp-cwd", default=os.environ.get("AC_ACP_CWD", ""),
                    help="命令的工作目录（可选，与注册信息 acp_cwd 同值）")
    # ★V44 #14：身份凭证
    ap.add_argument("--agent-token", default=os.environ.get("AC_AGENT_TOKEN", ""),
                    help="平台签发的 Agent-Token（不带则向平台问一次；平台未启用时可留空）")
    # ★V44 #12：默认不复写注册
    ap.add_argument("--register", action="store_true",
                    help="启动时向平台重新注册（默认**不**做：注册接口是幂等覆盖，占位值会冲掉真注册）")
    args = ap.parse_args()
    base = args.url.rstrip("/")
    HARNESS_ID = args.harness_id
    ACP_COMMAND = (args.acp_command or "").strip()
    ACP_CWD = (args.acp_cwd or "").strip()
    AGENT_TOKEN = (args.agent_token or "").strip()

    print(f"[桥] ACP harness 桥启动（示例），连接 {base}", flush=True)
    print(f"[桥] harness: {HARNESS_ID}（请确认这是你自己的 harness_id，不是示例名）", flush=True)

    # ★单实例保护（开始轮询之前）：同一只桥的目录只允许一个桥进程。
    #   cli_acp 型没有独立"邮箱/工作目录"，身份就用**桥脚本所在目录**。
    _lock_dir = Path(__file__).resolve().parent
    _lk, _why = acquire_single_instance(_lock_dir / ".bridge.lock")
    if _lk is None and _why and "拒绝重复启动" in _why:
        print(f"[桥] ⛔ {_why}", flush=True)
        print(f"[桥] 同一个 harness 只应有一只桥在跑；要重启请先停掉原来那只。（桥目录: {_lock_dir}）", flush=True)
        raise SystemExit(2)
    if _why:
        print(f"[桥] 提示: {_why}", flush=True)

    register_bridge(base, register=bool(args.register))
    _fill_acp_command_from_platform()
    _warn_token_source()
    threading.Thread(target=heartbeat_loop, args=(base,), daemon=True).start()
    register_bridge_path(base)

    print("[桥] 开始轮询激活/任务/桥测试 ...", flush=True)
    while True:
        try:
            # 领激活任务
            ad = get_json(f"{base}/api/harness/pending-activations?harness_id={urllib.parse.quote(HARNESS_ID)}")
            for act in ad.get("activations", []):
                ok = handle_activation(act)
                report_activation(base, act, ok)

            # 领工作任务
            td = get_json(f"{base}/api/harness/pending-tasks?harness_id={urllib.parse.quote(HARNESS_ID)}")
            for task in td.get("tasks", []):
                ok, text = handle_task(task)
                report_task(base, task, ok, text)
                print(f"[桥] 任务完成: ok={ok} result={text.strip()[:120]}", flush=True)

            # ★领桥测试（与模板生成的桥同一套：第 3/4 步自检就靠它）
            bd = get_json(f"{base}/api/harness/pending-bridge-tests?harness_id={urllib.parse.quote(HARNESS_ID)}")
            for t in bd.get("tests", []):
                handle_bridge_test(t)
        except Exception as e:
            print(f"[桥] 轮询失败: {e}", flush=True)
        time.sleep(2)


if __name__ == "__main__":
    main()
