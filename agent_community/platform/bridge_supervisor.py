"""桥进程监管器 — 让已注册 harness 的桥与平台同生命周期。

用途：平台启动时自动拉起该 harness 的桥子进程（常驻），平台关闭时自动回收，
实现「打开平台 → 桥自启，关闭平台 → 桥自关」，避免"注册了但桥没常驻"而收不到任务。

用法（在 server.py 的 startup/shutdown 里接线）：
    from .bridge_supervisor import start_harness_bridges, stop_harness_bridges
    start_harness_bridges()          # startup 时调用
    stop_harness_bridges()           # shutdown 时调用
"""

from __future__ import annotations
import os
import subprocess
import sys
from pathlib import Path

# 每个要跟随平台一起起停的桥。key = harness_id
# bridge_file: 平台生成的桥脚本路径（若显式路径不存在则按实际副本动态推导）；url: 平台地址；extra: 额外参数
_BRIDGES: dict[str, dict] = {
    "example-web-bridge": {
        "bridge_file": "{{DSH_BRIDGE_PATH}}",  # 示例：用户侧实际桥文件路径
        "url": "http://127.0.0.1:18920",
        "extra": [],
    },
}

_procs: dict[str, subprocess.Popen] = {}


def _resolve_bridge_file(hid: str) -> str:
    """解析桥脚本真实路径：显式配置路径失效时，动态定位当前项目副本中的桥。

    修复背景：_BRIDGES 曾硬编码开发副本 D:\\外端Agent生产合作社（External Agent Community），导致实际工作副本
    （如用户本地部署目录）启动时找不到桥脚本、桥从不运行、
    激活消息永远堆积在 pending 队列。本模块位于 <root>\\agent_community\\platform\\
    → parents[2] 即项目根，桥位于 <root>\\agent_community\\bridges\\<hid>\\bridge.py。
    """
    cfg_path = _BRIDGES.get(hid, {}).get("bridge_file", "")
    here = Path(__file__).resolve()
    # 优先动态推导当前运行副本（本模块位于 <root>/agent_community/platform/，parents[2] 即项目根）
    for ancestor in (here.parents[2], here.parents[3], here.parents[4]):
        cand = ancestor / "agent_community" / "bridges" / hid / "bridge.py"
        if cand.is_file():
            return str(cand)
    # 推导失败再回退显式配置路径（兼容手工维护的部署）
    if cfg_path and os.path.isfile(cfg_path):
        return cfg_path
    return cfg_path


def _find_platform_python() -> str:
    """优先用当前解释器（平台 server 运行在带 fastapi 的 Python 下），否则退回 python。"""
    exe = sys.executable
    if exe and os.path.isfile(exe):
        return exe
    return "python"


def start_harness_bridges() -> list[dict]:
    """拉起所有已登记的桥，返回已启动的清单。幂等：已运行的跳过。"""
    started: list[dict] = []
    py = _find_platform_python()
    for hid, cfg in _BRIDGES.items():
        bf = _resolve_bridge_file(hid)
        if not os.path.isfile(bf):
            print(f"[bridge_supervisor] {hid} 桥脚本不存在: {bf}，跳过", flush=True)
            continue
        if hid in _procs and _procs[hid].poll() is None:
            print(f"[bridge_supervisor] {hid} 桥已在运行，跳过", flush=True)
            continue
        cmd = [py, "-u", bf, "--url", cfg["url"], *cfg.get("extra", [])]
        try:
            env = dict(os.environ)
            env["DSH_SUPERVISOR_PID"] = str(os.getpid())  # 桥据此探测父进程存活，父死自退出（防孤儿）
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
                env=env,
            )
            _procs[hid] = proc
            started.append({"harness_id": hid, "pid": proc.pid, "cmd": " ".join(cmd)})
            print(f"[bridge_supervisor] {hid} 桥已启动 pid={proc.pid}", flush=True)
        except Exception as e:
            print(f"[bridge_supervisor] {hid} 桥启动失败: {e}", flush=True)
    return started


def stop_harness_bridges() -> None:
    """终止所有被本模块拉起的桥子进程。"""
    for hid, proc in list(_procs.items()):
        if proc.poll() is None:
            try:
                proc.terminate()
            except Exception:
                pass
            try:
                proc.wait(timeout=5)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
        _procs.pop(hid, None)
        print(f"[bridge_supervisor] {hid} 桥已停止", flush=True)


async def watch_harness_bridges(interval: float = 30.0) -> None:
    """平台内看护循环：周期检查已登记桥子进程，崩溃/异常退出则自动重启。
    平台模式此前只在 lifespan 启动时 start 一次、关闭时 stop 一次，
    桥中途退出没有任何自动拉起路径——补上这一环，桥与平台同生命周期自治。
    仅管理本模块 _procs 拉起的子进程；不触碰遗留孤儿进程（避免误杀持会话进程）。
    """
    import asyncio
    while True:
        try:
            dead = [hid for hid, p in _procs.items() if p.poll() is not None]
            for hid in dead:
                print(f"[bridge_supervisor] {hid} 桥退出码={_procs[hid].returncode}，自动重启", flush=True)
                _procs.pop(hid, None)
            if dead:
                start_harness_bridges()
        except Exception as _e:
            print(f"[bridge_supervisor] 看护循环异常: {_e}", flush=True)
        await asyncio.sleep(interval)


def _standalone_main() -> None:
    """独立运行：启动所有桥并阻塞（等价于手动跑桥）。"""
    start_harness_bridges()
    import time
    while True:
        for hid, proc in list(_procs.items()):
            if proc.poll() is not None:
                print(f"[bridge_supervisor] {hid} 桥退出码={proc.returncode}，移除", flush=True)
                _procs.pop(hid, None)
        if not _procs:
            break
        time.sleep(2)


if __name__ == "__main__":
    _standalone_main()
