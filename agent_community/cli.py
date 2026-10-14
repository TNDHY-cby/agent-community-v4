"""外端Agent生产合作社（External Agent Community） CLI — 命令行管理工具

用法：
    python -m agent_community.cli start [--port 18920] [--wakeup]
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
# ★2026-10-13：端口**只有一处真相**（`platform_address`）——
#   原来这里写 9103、server 写 9103、其它模块写 18920，同一件事四处各写一套。
#   端口是**用户自己的事**（AC_PORT / --port），产品只给一个可用默认。
from .platform_address import DEFAULT_PORT, base_url as _addr_base_url
BASE_URL = _addr_base_url()

# 日志目录：**跟着 AC_DATA_DIR 走**（与 server.py 的 DATA_DIR 同一落点）。
# 历史写法把服务日志写到 D:/server_out.log、D:/server_err.log —— 日志散落在
# 盘符根目录，既不符合「权威日志在 data/logs/」的项目约定，也让桌面的
# .vbs 启动器在失败提示里把用户引到一个不该存在的位置。
PROJECT_ROOT = Path(__file__).resolve().parents[1]
# ★★2026-10-14 实测缺陷修复（陌生人换数据目录后**仓库仍被写脏**）：
#   这里原来写死 `PROJECT_ROOT / "agent_community" / "data" / "logs"` ——
#   **唯一没跟 `AC_DATA_DIR` 走的落点**（其余 8 处都认，见 env_spec.data_dir 的说明）。
#   实测：设了 AC_DATA_DIR 之后，`agent_community/data/logs/server_stdout.log`
#   照样在**仓库里**长出来；装到只读位置时更会直接起不来。
#   ⇒ 走唯一解析点 `env_spec.data_dir()`。
from .platform.env_spec import data_dir as _data_dir  # noqa: E402

LOG_DIR = _data_dir() / "logs"


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
    """外端Agent生产合作社（External Agent Community） Platform — 外端 Agent 生产协作平台 CLI"""
    pass


@main.command()
@click.option("--port", type=int, default=DEFAULT_PORT,
              help=f"服务端口（默认 {DEFAULT_PORT}；也可用 AC_PORT）")
@click.option("--wakeup", is_flag=True, help="启动 Wakeup Agent（通用 AI 接入层）")
@click.option("--gui/--no-gui", default=True, help="启用桌面 GUI 窗口（默认: --gui）")
def start(port, wakeup, gui):
    """启动 外端Agent生产合作社（External Agent Community） 服务（AI 配置在桌面窗口内完成）"""
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

    # 日志统一写项目内 agent_community/data/logs（与 server.py 的 DATA_DIR 对齐）
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    _stdout_log = open(LOG_DIR / "server_stdout.log", "a", encoding="utf-8")
    _stderr_log = open(LOG_DIR / "server_stderr.log", "a", encoding="utf-8")

    # 后台启动（用 -m 模块方式避免相对导入问题）
    if sys.platform == "win32":
        proc = subprocess.Popen(
            [sys.executable, "-m", "agent_community.platform.server"],
            env=env,
            stdout=_stdout_log,
            stderr=_stderr_log,
            creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
        )
    else:
        proc = subprocess.Popen(
            [sys.executable, "-m", "agent_community.platform.server"],
            env=env,
            stdout=_stdout_log,
            stderr=_stderr_log,
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
    """停止 外端Agent生产合作社（External Agent Community） 服务"""
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
        click.secho("[WARN] 服务未运行，请先执行 python -m agent_community.cli start", fg="yellow")
        return

    base = _get_base_url()
    try:
        r = httpx.get(f"{base}/api/status", timeout=5.0)
        s = r.json()
        click.secho("=== 外端Agent生产合作社（External Agent Community） 服务状态 ===", fg="cyan")
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

def _task_payload(description: str) -> dict:
    """`agent-community task "…"` 发给 `/api/command` 的请求体（**只有这一处**）。

    ★为什么单独抽出来：这个字段名与端点**曾经对不上**（CLI 发 `content`、
    端点只认 `input`/`command`），而 CLI 又只打一个 `HTTP 400`、什么原因都不说 ——
    结果"照着 README 第 3 步做"的**第一次尝试就是失败**，且看不出为什么。
    抽出来之后，契约守卫 `tests/test_cli_contract.py` 可以把**这份请求体**直接喂给
    真端点函数（不是复制一份 payload 到测试里 —— 那样一改又漂移）。
    """
    return {
        "type": "new_task",
        "command": description,
        "from_agent": "cli",
        "agent_name": "CLI 用户",
    }


@main.command()
@click.argument("description")
def task(description):
    """向平台发送任务"""
    if not _is_running():
        click.secho("[ERROR] 服务未运行，请先执行 python -m agent_community.cli start", fg="red")
        sys.exit(1)

    base = _get_base_url()
    try:
        r = httpx.post(
            f"{base}/api/command",
            json=_task_payload(description),
            timeout=10.0,
        )
        if r.status_code == 200:
            data = r.json()
            task_id = data.get("task_id", "?")
            click.secho(f"[OK] 任务已提交", fg="green")
            click.echo(f"  Task ID: {task_id}")
            click.echo(f"  内容:    {description}")
            ws = data.get("workshop_id") or ""
            if ws:
                click.echo(f"  工作间:  {ws}（已自动建间，去界面里选成员）")
            click.echo(f"  查询结果: agent-community task-result {task_id}")
        else:
            # ★失败要说话（准则⑤）：把**服务端原话**打出来，别只留一个状态码。
            detail = ""
            try:
                j = r.json()
                detail = j.get("error") or j.get("detail") or ""
            except Exception:  # noqa: BLE001
                detail = (r.text or "")[:200]
            click.secho(f"[ERROR] 提交失败: HTTP {r.status_code}"
                        + (f" —— {detail}" if detail else ""), fg="red")
            sys.exit(1)
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
            click.echo(f"  标题: {t.get('title', '?')}")
            click.echo(f"  状态: {t.get('status', '?')}")
            click.echo(f"  创建: {t.get('created_at', '?')}")
            click.echo(f"  完成: {t.get('completed_at') or '（未记录完成时间）'}")
            # ★2026-10-14 实测修复：这里原来打的是 `len(t.get('agent_ids', []))`，
            #   而 **Task 模型里根本没有 `agent_ids` 字段**（见 protocol.Task）⇒
            #   那句"Agent: N 个参与"**永远是 0** —— 一句对用户说的假话（准则⑤）。
            #   参与度的事实字段是这三个：举手 / 派活 / 回报。
            _raises = len(t.get("hand_raises") or [])
            _deleg = len(t.get("delegations") or [])
            _res = len(t.get("delegation_results") or {})
            click.echo(f"  参与: 举手 {_raises} 个 | 派活 {_deleg} 条 | 回报 {_res} 条")
            # ★★2026-10-14 实测修复：**README 第 4 步叫"看结果"，可它从来不打结果** ——
            #   任务里明明有 `result`（真跑出来的一段完整回答），用户却只看得到状态与时间，
            #   会以为"没产出"。⇒ 把 result 打出来；**没有结果时说清为什么**（准则⑤）。
            res = str(t.get("result") or "").strip()
            if res:
                click.secho("\n结果:", fg="cyan")
                click.echo(res)
            else:
                st = str(t.get("status") or "")
                if st not in ("completed", "failed", "cancelled"):
                    why = "任务还在进行中（过一会儿再查）"
                elif not (_raises or _deleg or _res):
                    why = "没有成员参与 —— 平台还没有接入任何 Harness"
                else:
                    why = "本次没有产出结果"
                click.secho(f"\n（暂无结果：{why}）", fg="yellow")
                if not (_raises or _deleg or _res):
                    click.echo("  下一步：先接入一个 Harness —— 见 README「Harness 接入指南」，"
                               "或在界面里按引导走一遍： %s/register.html" % base)
            ws_id = t.get("workshop_id") or ""
            if ws_id:
                click.echo(f"  工作间: {ws_id}（界面里可看讨论与成员）")
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
            # 失败要说话：把服务端原话带上（原来只有一个状态码）
            detail = ""
            try:
                detail = r.json().get("error") or ""
            except Exception:  # noqa: BLE001
                detail = (r.text or "")[:200]
            click.secho(f"[ERROR] HTTP {r.status_code}" + (f" —— {detail}" if detail else ""), fg="red")
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
