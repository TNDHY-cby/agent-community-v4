"""Agent Community CLI — 命令行管理工具

用法：
    agent-community start [--port 9103] [--wakeup]
    agent-community stop
    agent-community status
    agent-community task "描述你的任务"
    agent-community task-result <task_id>
    agent-community harness list
    agent-community agents list

AI Provider 配置统一在桌面窗口内完成，不再通过命令行传参。
"""

from __future__ import annotations
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import click
import httpx

# PID 文件路径
PID_DIR = Path.home() / ".agent_community"
PID_FILE = PID_DIR / "server.pid"
DEFAULT_PORT = 9103
BASE_URL = f"http://127.0.0.1:{DEFAULT_PORT}"


def _get_base_url() -> str:
    """从 PID 文件读取端口号"""
    port = DEFAULT_PORT
    if PID_FILE.exists():
        try:
            data = json.loads(PID_FILE.read_text())
            port = data.get("port", DEFAULT_PORT)
        except (json.JSONDecodeError, KeyError):
            pass
    return f"http://127.0.0.1:{port}"


def _read_pid() -> dict | None:
    """读取 PID 文件"""
    if not PID_FILE.exists():
        return None
    try:
        return json.loads(PID_FILE.read_text())
    except (json.JSONDecodeError, KeyError):
        return None


def _write_pid(pid: int, port: int):
    """写入 PID 文件"""
    PID_DIR.mkdir(parents=True, exist_ok=True)
    PID_FILE.write_text(json.dumps({"pid": pid, "port": port}))


def _remove_pid():
    """删除 PID 文件"""
    if PID_FILE.exists():
        PID_FILE.unlink()


def _is_running(port: int = None) -> bool:
    """检查服务是否在运行"""
    if port is None:
        data = _read_pid()
        port = data["port"] if data else DEFAULT_PORT
    try:
        r = httpx.get(f"http://127.0.0.1:{port}/api/status", timeout=3.0)
        return r.status_code == 200
    except Exception:
        return False


def _table(headers: list[str], rows: list[list[str]]):
    """终端表格输出"""
    if not rows:
        click.echo("  (无数据)")
        return
    col_widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            col_widths[i] = max(col_widths[i], len(str(cell)))
    sep = "+" + "+".join("-" * (w + 2) for w in col_widths) + "+"
    header_line = "|" + "|".join(f" {h:<{col_widths[i]}} " for i, h in enumerate(headers)) + "|"
    click.echo(sep)
    click.echo(header_line)
    click.echo(sep)
    for row in rows:
        row_line = "|" + "|".join(f" {str(c):<{col_widths[i]}} " for i, c in enumerate(row)) + "|"
        click.echo(row_line)
    click.echo(sep)


# ── start ──────────────────────────────────────────────────────

@click.group()
def main():
    """Agent Community Platform — 多 Agent 协作平台 CLI"""
    pass


@main.command()
@click.option("--port", type=int, default=9103, help="服务端口（默认 9103）")
@click.option("--wakeup", is_flag=True, help="启动 Wakeup Agent（通用 AI 接入层）")
@click.option("--gui/--no-gui", default=True, help="启用桌面 GUI 窗口（默认: --gui）")
def start(port, wakeup, gui):
    """启动 Agent Community 服务（AI 配置在桌面窗口内完成）"""
    # GUI 模式：通过 pywebview 启动桌面窗口
    if gui:
        from .gui import AgentCommunityApp
        app = AgentCommunityApp(port=port)
        app.run()
        return

    # 无 GUI 模式：后台守护进程
    if _is_running(port):
        click.secho(f"[ERROR] 端口 {port} 已被占用或已有实例在运行", fg="red")
        sys.exit(1)

    # 环境变量
    env = os.environ.copy()
    env["AC_PORT"] = str(port)

    # 后台启动（用 -m 模块方式避免相对导入问题）
    if sys.platform == "win32":
        proc = subprocess.Popen(
            [sys.executable, "-m", "agent_community.platform.server"],
            env=env,
            stdout=open("D:/server_out.log", "a"),
            stderr=open("D:/server_err.log", "a"),
            creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
        )
    else:
        proc = subprocess.Popen(
            [sys.executable, "-m", "agent_community.platform.server"],
            env=env,
            stdout=open("D:/server_out.log", "a") if sys.platform == "win32" else subprocess.DEVNULL,
            stderr=open("D:/server_err.log", "a") if sys.platform == "win32" else subprocess.DEVNULL,
            start_new_session=True,
        )

    _write_pid(proc.pid, port)
    click.echo(f"[INFO] 服务启动中 (PID: {proc.pid}, 端口: {port})...")

    # 自检：轮询 /api/status，超时 5s
    base = f"http://127.0.0.1:{port}"
    deadline = time.time() + 5.0
    while time.time() < deadline:
        try:
            r = httpx.get(f"{base}/api/status", timeout=2.0)
            if r.status_code == 200:
                data = r.json()
                click.secho("[OK] 服务已就绪", fg="green")
                click.echo(f"  版本: {data.get('version', '?')}")
                click.echo(f"  地址: {base}")
                click.echo(f"  已注册 Agent: {data.get('agents_registered', 0)}")
                click.echo(f"  Harness: {data.get('harnesses', 0)}")
                if wakeup:
                    click.echo("[INFO] Wakeup Agent 由 server 内部预注册，通过 --ai-provider 配置 AI 后端")
                return
        except Exception:
            pass
        time.sleep(0.5)

    click.secho(f"[ERROR] 服务启动超时（5s），请检查日志", fg="red")
    sys.exit(1)


# ── stop ───────────────────────────────────────────────────────

@main.command()
def stop():
    """停止 Agent Community 服务"""
    data = _read_pid()
    if not data:
        click.secho("[WARN] 未找到运行中的服务（PID 文件不存在）", fg="yellow")
        return

    pid = data["pid"]
    port = data["port"]

    if not _is_running(port):
        click.echo(f"[INFO] 服务 (PID: {pid}) 已不在运行，清理 PID 文件")
        _remove_pid()
        return

    try:
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True)
        else:
            os.kill(pid, signal.SIGTERM)
        _remove_pid()
        click.secho(f"[OK] 服务已停止 (PID: {pid})", fg="green")
    except Exception as e:
        click.secho(f"[ERROR] 停止服务失败: {e}", fg="red")
        sys.exit(1)


# ── status ─────────────────────────────────────────────────────

@main.command()
def status():
    """查看服务状态、在线 Agent、Harness 列表"""
    if not _is_running():
        click.secho("[WARN] 服务未运行，请先执行 agent-community start", fg="yellow")
        return

    base = _get_base_url()
    try:
        r = httpx.get(f"{base}/api/status", timeout=5.0)
        s = r.json()
        click.secho("=== Agent Community 服务状态 ===", fg="cyan")
        click.echo(f"  状态:   {s.get('status', '?')}")
        click.echo(f"  版本:   {s.get('version', '?')}")
        click.echo(f"  Agent:  {s.get('agents_online', 0)}/{s.get('agents_registered', 0)} 在线")
        click.echo(f"  任务:   {s.get('tasks_active', 0)} 活跃 / {s.get('tasks_total', 0)} 总计")
        click.echo(f"  Harness: {s.get('harnesses', 0)}")
        click.echo(f"  Pipe:   {s.get('pipe_dir', '?')}")
        click.echo()

        # Agent 列表
        r2 = httpx.get(f"{base}/api/agents", timeout=5.0)
        agents_data = r2.json().get("agents", [])
        if agents_data:
            click.secho("在线 Agent:", fg="cyan")
            _table(
                ["Agent ID", "名称", "状态", "能力"],
                [[a["agent_id"], a["name"], a["status"], ", ".join(a.get("capabilities", [])[:4])] for a in agents_data],
            )
            click.echo()

        # Harness 列表
        r3 = httpx.get(f"{base}/api/harness/list", timeout=5.0)
        hlist = r3.json().get("harnesses", [])
        if hlist:
            click.secho("已注册 Harness:", fg="cyan")
            _table(
                ["ID", "名称", "状态", "工具数"],
                [[h.get("harness_id", "?"), h.get("name", "?"), h.get("status", "?"), str(len(h.get("tools", [])))] for h in hlist],
            )
        else:
            click.echo("  (无 Harness 注册)")
    except Exception as e:
        click.secho(f"[ERROR] 无法连接服务: {e}", fg="red")
        sys.exit(1)


# ── task ───────────────────────────────────────────────────────

@main.command()
@click.argument("description")
def task(description):
    """向平台发送任务"""
    if not _is_running():
        click.secho("[ERROR] 服务未运行，请先执行 agent-community start", fg="red")
        sys.exit(1)

    base = _get_base_url()
    try:
        r = httpx.post(
            f"{base}/api/command",
            json={
                "type": "new_task",
                "content": description,
                "from_agent": "cli",
                "agent_name": "CLI 用户",
            },
            timeout=10.0,
        )
        if r.status_code == 200:
            data = r.json()
            task_id = data.get("task_id", "?")
            click.secho(f"[OK] 任务已提交", fg="green")
            click.echo(f"  Task ID: {task_id}")
            click.echo(f"  内容:    {description}")
            click.echo(f"  查询结果: agent-community task-result {task_id}")
        else:
            click.secho(f"[ERROR] 提交失败: HTTP {r.status_code}", fg="red")
    except Exception as e:
        click.secho(f"[ERROR] 无法连接服务: {e}", fg="red")
        sys.exit(1)


# ── task-result ────────────────────────────────────────────────

@main.command()
@click.argument("task_id")
def task_result(task_id):
    """查询任务结果"""
    if not _is_running():
        click.secho("[ERROR] 服务未运行", fg="red")
        sys.exit(1)

    base = _get_base_url()
    try:
        r = httpx.get(f"{base}/api/task/{task_id}", timeout=10.0)
        if r.status_code == 200:
            t = r.json()
            click.secho(f"=== 任务: {task_id} ===", fg="cyan")
            click.echo(f"  状态: {t.get('status', '?')}")
            click.echo(f"  创建: {t.get('created_at', '?')}")
            click.echo(f"  完成: {t.get('completed_at', '?')}")
            click.echo(f"  Agent: {len(t.get('agent_ids', []))} 个参与")
            msgs = t.get("messages", [])
            if msgs:
                click.secho(f"\n消息记录 ({len(msgs)} 条):", fg="cyan")
                rows = []
                for m in msgs[-20:]:
                    rows.append([
                        m.get("type", "?"),
                        m.get("from_agent", "?")[:16],
                        m.get("content", "")[:80],
                    ])
                _table(["类型", "来源", "内容"], rows)
        elif r.status_code == 404:
            click.secho(f"[WARN] 任务不存在: {task_id}", fg="yellow")
        else:
            click.secho(f"[ERROR] HTTP {r.status_code}", fg="red")
    except Exception as e:
        click.secho(f"[ERROR] 无法连接服务: {e}", fg="red")
        sys.exit(1)


# ── harness list ───────────────────────────────────────────────

@main.command()
@click.argument("action", type=click.Choice(["list"]))
def harness(action):
    """Harness 管理"""
    if not _is_running():
        click.secho("[ERROR] 服务未运行", fg="red")
        sys.exit(1)

    base = _get_base_url()
    try:
        r = httpx.get(f"{base}/api/harness/list", timeout=5.0)
        hlist = r.json().get("harnesses", [])
        if hlist:
            click.secho("已注册 Harness:", fg="cyan")
            _table(
                ["ID", "名称", "类型", "状态", "Agent ID", "工具数"],
                [[
                    h.get("harness_id", "?"),
                    h.get("name", "?"),
                    h.get("harness_type", "?"),
                    h.get("status", "?"),
                    h.get("agent_id", "?"),
                    str(len(h.get("tools", []))),
                ] for h in hlist],
            )
        else:
            click.echo("(无 Harness 注册)")
    except Exception as e:
        click.secho(f"[ERROR] 无法连接服务: {e}", fg="red")
        sys.exit(1)


# ── agents list ────────────────────────────────────────────────

@main.command()
@click.argument("action", type=click.Choice(["list"]))
def agents(action):
    """Agent 管理"""
    if not _is_running():
        click.secho("[ERROR] 服务未运行", fg="red")
        sys.exit(1)

    base = _get_base_url()
    try:
        r = httpx.get(f"{base}/api/agents", timeout=5.0)
        alist = r.json().get("agents", [])
        if alist:
            click.secho("平台 Agent:", fg="cyan")
            _table(
                ["Agent ID", "名称", "状态", "类型", "能力"],
                [[
                    a["agent_id"],
                    a["name"],
                    a["status"],
                    "Harness" if a.get("is_harness") else "内置",
                    ", ".join(a.get("capabilities", [])[:4]),
                ] for a in alist],
            )
        else:
            click.echo("(无已注册 Agent)")
    except Exception as e:
        click.secho(f"[ERROR] 无法连接服务: {e}", fg="red")
        sys.exit(1)


if __name__ == "__main__":
    main()
