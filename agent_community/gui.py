"""Agent Community GUI — pywebview 桌面窗口

以子进程方式启动 FastAPI server，pywebview 窗口仅连接已有服务端。
AI Provider 配置统一从 ~/.agent_community/config.json 读取。
用法：
    from agent_community.gui import AgentCommunityApp
    app = AgentCommunityApp(port=9103)
    app.run()
"""

from __future__ import annotations
import os
import subprocess
import sys
import time

import webview


def _find_free_port(start: int = 9103, max_attempts: int = 20) -> int:
    """在 start 起找可用端口"""
    import socket
    for port in range(start, start + max_attempts):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.bind(("127.0.0.1", port))
            s.close()
            return port
        except OSError:
            s.close()
    return start


class AgentCommunityApp:
    """Agent Community 桌面应用（AI 配置在窗口内完成）"""

    def __init__(
        self,
        port: int = 0,
        title: str = "Agent Community",
        width: int = 1200,
        height: int = 800,
    ):
        self.port = port if port > 0 else _find_free_port()
        self.title = title
        self.width = width
        self.height = height
        self._server_proc: subprocess.Popen | None = None

    def _start_server_subprocess(self):
        """以子进程方式启动 FastAPI server（若端口已监听则复用，不重复启动）。

        这样桌面窗口可以「连接已有服务」，也能「窗口关闭时若由本窗口启动则回收」。
        """
        import socket
        # 若端口已被占用（已有服务在跑），复用，不重复启动
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(1)
            s.connect(("127.0.0.1", self.port))
            s.close()
            self._server_proc = None  # 复用已有服务，不由本窗口管理
            return
        except OSError:
            pass  # 端口空闲，需要启动

        env = os.environ.copy()
        env["AC_PORT"] = str(self.port)
        cwd = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

        if sys.platform == "win32":
            log_dir = os.path.join(cwd, "runtime_logs")
            os.makedirs(log_dir, exist_ok=True)
            self._server_proc = subprocess.Popen(
                [sys.executable, "-m", "agent_community.platform.server"],
                env=env,
                cwd=cwd,
                stdout=open(os.path.join(log_dir, "server_out.log"), "a"),
                stderr=open(os.path.join(log_dir, "server_err.log"), "a"),
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
            )
        else:
            self._server_proc = subprocess.Popen(
                [sys.executable, "-m", "agent_community.platform.server"],
                env=env,
                start_new_session=True,
            )

    def _wait_for_server(self, timeout: float = 10.0):
        """轮询等待 server 就绪"""
        import httpx
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                r = httpx.get(f"http://127.0.0.1:{self.port}/api/status", timeout=1.0)
                if r.status_code == 200:
                    return
            except Exception:
                pass
            time.sleep(0.3)
        raise TimeoutError(f"Server 启动超时 (端口 {self.port})")

    def run(self):
        """启动桌面应用"""
        # 以子进程方式启动 server
        self._start_server_subprocess()

        # 等待 server 就绪
        self._wait_for_server()

        url = f"http://127.0.0.1:{self.port}"

        # 创建 pywebview 窗口
        window = webview.create_window(
            title=self.title,
            url=url,
            width=self.width,
            height=self.height,
            resizable=True,
            min_size=(800, 500),
        )

        # 启动 webview 事件循环（阻塞直到窗口关闭）
        webview.start(debug=False)

        # 窗口关闭后停止 server
        self.stop()

    def stop(self):
        """停止 server 子进程"""
        if self._server_proc is not None:
            try:
                self._server_proc.terminate()
                self._server_proc.wait(timeout=5)
            except Exception:
                try:
                    self._server_proc.kill()
                except Exception:
                    pass
            self._server_proc = None


def main():
    """命令行入口：python -m agent_community.gui [--port 18920] [--no-subprocess]
    """
    import argparse
    ap = argparse.ArgumentParser(description="Agent Community 桌面窗口（pywebview）")
    ap.add_argument("--port", type=int, default=18920, help="服务端口（默认 18920，服务未运行时自动拉起）")
    ap.add_argument("--width", type=int, default=1280, help="窗口宽度")
    ap.add_argument("--height", type=int, default=820, help="窗口高度")
    ap.add_argument("--title", default="Agent Community · 外端Agent生产合作社", help="窗口标题")
    args = ap.parse_args()

    app = AgentCommunityApp(
        port=args.port,
        title=args.title,
        width=args.width,
        height=args.height,
    )
    app.run()


if __name__ == "__main__":
    main()

