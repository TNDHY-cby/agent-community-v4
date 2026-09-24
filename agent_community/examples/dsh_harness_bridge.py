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
    python -m agent_community.examples.dsh_harness_bridge --url http://127.0.0.1:18920 --harness-id 你的harness_id
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

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from agent_community.platform.dsh_acp_bridge import DshAcpBridge  # noqa: E402

# 虚拟示例名：请用 --harness-id 覆盖
HARNESS_ID = "示例Harness-ACP"

# 一个持久连接，多个会话（1 harness → N 员工）
_bridge: DshAcpBridge | None = None
_sessions: dict[str, str] = {}  # key = f"{workshop_id}:{member_id}" → session_id


def build_harness_info() -> dict:
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
        # 示例：改成你自己的 ACP 启动命令
        "acp_command": "your-acp-command --flag value",
        "acp_cwd": "C:\\path\\to\\your\\harness",
        "can_wake": False,
        "waking_models": [],
        "waking_protocols": [],
        "ai": {
            "model_name": "your-model",
            "provider": "your-provider",
            "version": "",
            "capabilities": ["coding", "file_ops", "web_search"],
            "max_tokens": 128000,
            "can_see": False,
            "can_code": True,
            "can_browse": True,
            "can_file_ops": True,
            "description": "你的模型：能编码、读/写文件、跑命令、联网搜索",
        },
        "tools": [
            {"name": "bash", "description": "执行命令", "parameters": {}, "capability_tag": "coding"},
            {"name": "read_file", "description": "读文件", "parameters": {}, "capability_tag": "file_ops"},
            {"name": "write_file", "description": "写文件", "parameters": {}, "capability_tag": "file_ops"},
        ],
        "description": "示例 Harness（ACP 类），通过 ACP 拉起新会话",
    }


def post_json(url: str, body: dict) -> dict:
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    return json.loads(urllib.request.urlopen(req, timeout=30).read())


def get_json(url: str) -> dict:
    return json.loads(urllib.request.urlopen(url, timeout=30).read())


def register(base: str) -> None:
    r = post_json(f"{base}/api/harness/register", build_harness_info())
    print(f"[桥] 注册结果: {r.get('success')} agent_id={r.get('agent_id')}", flush=True)


def heartbeat_loop(base: str, interval: float = 30.0) -> None:
    while True:
        try:
            post_json(f"{base}/api/harness/heartbeat?harness_id={urllib.parse.quote(HARNESS_ID)}", {})
            print("[桥] 心跳在线", flush=True)
        except Exception as e:
            print(f"[桥] 心跳失败: {e}", flush=True)
        time.sleep(interval)


def ensure_bridge() -> DshAcpBridge:
    global _bridge
    if _bridge is None:
        _bridge = DshAcpBridge()
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
    """领到工作任务：发给对应会话，dsh 用工具真干活，返回结果。"""
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


def main() -> None:
    global HARNESS_ID
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:18920")
    ap.add_argument("--harness-id", default=HARNESS_ID, help="你的 harness_id（默认是虚拟示例名，必须改）")
    args = ap.parse_args()
    base = args.url.rstrip("/")
    HARNESS_ID = args.harness_id

    print(f"[桥] ACP harness 桥启动（示例），连接 {base}", flush=True)
    print(f"[桥] harness: {HARNESS_ID}（请确认这是你自己的 harness_id，不是示例名）", flush=True)
    register(base)
    threading.Thread(target=heartbeat_loop, args=(base,), daemon=True).start()

    print("[桥] 开始轮询激活/任务 ...", flush=True)
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
        except Exception as e:
            print(f"[桥] 轮询失败: {e}", flush=True)
        time.sleep(2)


if __name__ == "__main__":
    main()
