"""dsh (DeepSeek Harness) 接入桥 — 基于 ACP (Agent Client Protocol)。

通过 JSON-RPC over stdio 程序化拉起 dsh 新会话、发消息、收回复。
一条连接可同时拥有多个 session → 天然支持「1 harness → N 会话并行」。

设计要点（spike 已验证）：
- HA 会话 = ACP：session/new（cwd=工作区坐标）→ session/prompt（多轮）→ session/update（收回复）
- 会话 fresh-only、connection-owned（连接断开 = 全部会话释放）
- API key 须注入环境变量（dsh 的 llm 提供方不认 credentials 里的扁平 key）

参考：<dsh仓库>/packages/acp/acp/README.md（ACP 协议规范）
spike：spike_acp_test.py（已跑通 initialize → session/new → session/prompt → "收到。"）

自测：python agent_community/platform/acp_bridge.py
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from dataclasses import dataclass, field
from typing import Optional

REPO = os.environ.get("DSH_REPO_DIR") or r"D:\Programs\deepseek-harness"  # 默认示例路径，可用环境变量 DSH_REPO_DIR 覆盖
CMD = [
    "node", "--import", "tsx",
    "packages/examples/acp-demo/src/bin.ts",
    "--config", "examples/acp-agent/cordis.yml",
]
CREDENTIALS = os.environ.get("DSH_CREDENTIALS", "~/.dsh/.credentials.yaml")
PROTOCOL_VERSION = 1


def load_api_key() -> str:
    """从 dsh credentials 读 DEEPSEEK_API_KEY（不打印明文）。"""
    try:
        with open(CREDENTIALS, "r", encoding="utf-8") as f:
            for line in f:
                if line.startswith("DEEPSEEK_API_KEY:"):
                    return line.split(":", 1)[1].strip()
    except Exception:
        pass
    return os.environ.get("DEEPSEEK_API_KEY", "")


@dataclass
class AcpSession:
    """一个 HA 会话（一个并行工作的"手"，即 dsh 的 ACP session）。"""
    session_id: str
    cwd: str
    text_parts: list[str] = field(default_factory=list)  # 已提交文本块

    def committed_text(self) -> str:
        return "".join(self.text_parts)


class AcpBridge:
    """dsh ACP 桥：spawn 一个 dsh ACP server，管理多个会话。

    线程模型：本桥为同步实现（子进程 + 读线程），供 server.py 通过
    asyncio.to_thread 调用，避免阻塞事件循环。
    """

    def __init__(self, repo: str = REPO, cmd: Optional[list] = None):
        self.repo = repo
        self.cmd = cmd if cmd is not None else CMD
        self.proc: Optional[subprocess.Popen] = None
        self.sessions: dict[str, AcpSession] = {}
        self._responses: dict[int, dict] = {}
        self._notifications: list[dict] = []
        self._next_id = 0
        self._lock = threading.Lock()
        self._reader: Optional[threading.Thread] = None
        self._stderr_tail: list[str] = []

    @classmethod
    def from_harness_info(cls, info) -> "AcpBridge":
        """从 HarnessInfo 构造桥：读 acp_command / acp_cwd，否则用默认 dsh 命令。"""
        import shlex
        cmd = shlex.split(info.acp_command) if getattr(info, "acp_command", "") else CMD
        repo = getattr(info, "acp_cwd", "") or REPO
        return cls(repo=repo, cmd=cmd)

    # ── 生命周期 ─────────────────────────────────────────────

    def start(self, api_key: Optional[str] = None) -> None:
        env = dict(os.environ)
        env["DEEPSEEK_API_KEY"] = api_key or load_api_key()
        self.proc = subprocess.Popen(
            self.cmd, cwd=self.repo, env=env,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, bufsize=1, encoding="utf-8",
        )
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()
        threading.Thread(target=self._stderr_loop, daemon=True).start()

    def _read_loop(self) -> None:
        assert self.proc and self.proc.stdout
        for line in self.proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except Exception:
                continue
            with self._lock:
                if obj.get("method") == "session/update":
                    self._notifications.append(obj)
                elif "id" in obj:
                    self._responses[obj["id"]] = obj

    def _stderr_loop(self) -> None:
        assert self.proc and self.proc.stderr
        for line in self.proc.stderr:
            self._stderr_tail.append(line.rstrip())
            if len(self._stderr_tail) > 50:
                self._stderr_tail = self._stderr_tail[-50:]

    def close(self) -> None:
        if self.proc:
            try:
                self.proc.stdin.close()
            except Exception:
                pass
            try:
                self.proc.terminate()
                self.proc.wait(timeout=10)
            except Exception:
                try:
                    self.proc.kill()
                except Exception:
                    pass
        self.proc = None

    def stderr_tail(self) -> str:
        return "\n".join(self._stderr_tail)

    # ── JSON-RPC ─────────────────────────────────────────────

    def _send(self, obj: dict) -> None:
        assert self.proc and self.proc.stdin
        self.proc.stdin.write(json.dumps(obj) + "\n")
        self.proc.stdin.flush()

    def _call(self, method: str, params: dict, timeout: float) -> Optional[dict]:
        with self._lock:
            self._next_id += 1
            req_id = self._next_id
        self._send({"jsonrpc": "2.0", "id": req_id, "method": method, "params": params})
        deadline = time.time() + timeout
        while time.time() < deadline:
            with self._lock:
                resp = self._responses.pop(req_id, None)
            if resp is not None:
                return resp
            time.sleep(0.05)
        return None

    # ── 高层操作 ─────────────────────────────────────────────

    def initialize(self, timeout: float = 30.0) -> dict:
        return self._call(
            "initialize",
            {"protocolVersion": PROTOCOL_VERSION, "clientCapabilities": {}},
            timeout,
        ) or {}

    def new_session(self, cwd: str, timeout: float = 90.0) -> Optional[AcpSession]:
        """开一个新会话。cwd = 工作区坐标（会话 workspace 根）。"""
        r = self._call("session/new", {"cwd": cwd, "mcpServers": []}, timeout)
        if not r or "result" not in r:
            return None
        sid = r["result"]["sessionId"]
        sess = AcpSession(session_id=sid, cwd=cwd)
        self.sessions[sid] = sess
        return sess

    def prompt(self, session_id: str, text: str, timeout: float = 300.0) -> tuple[Optional[str], str]:
        """发一条消息，等待回合结束，返回 (stopReason, 已提交文本)。"""
        sess = self.sessions.get(session_id)
        with self._lock:
            start_idx = len(self._notifications)
        r = self._call(
            "session/prompt",
            {"sessionId": session_id, "prompt": [{"type": "text", "text": text}]},
            timeout,
        )
        stop: Optional[str] = None
        if r and "result" in r:
            stop = r["result"].get("stopReason")
        elif r and "error" in r:
            stop = "error:" + str(r["error"].get("message", "?"))[:200]

        # 收集本会话本回合新到的 agent_message_chunk 文本
        with self._lock:
            new = self._notifications[start_idx:]
        for n in new:
            try:
                params = n.get("params", {})
                if params.get("sessionId") != session_id:
                    continue
                upd = params.get("update", {})
                if upd.get("sessionUpdate") == "agent_message_chunk":
                    content = upd.get("content", {})
                    if content.get("type") == "text" and sess:
                        sess.text_parts.append(content.get("text", ""))
            except Exception:
                pass
        return stop, sess.committed_text() if sess else ""


# ── 自测（独立验证：拉起 → 开会话 → 发消息 → 收「收到。」）──

if __name__ == "__main__":
    import sys

    print("[bridge] start dsh ACP ...", flush=True)
    bridge = AcpBridge()
    bridge.start()
    try:
        init = bridge.initialize()
        print("INIT:", json.dumps(init, ensure_ascii=False), flush=True)

        cwd = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "spike_ws"))
        os.makedirs(cwd, exist_ok=True)
        sess = bridge.new_session(cwd)
        if not sess:
            print("SESSION/NEW FAILED", flush=True)
            print("STDERR tail:", bridge.stderr_tail(), flush=True)
            sys.exit(1)
        print("SESSION/NEW:", sess.session_id, flush=True)

        stop, text = bridge.prompt(
            sess.session_id,
            "只回复两个字：收到。不要调用任何工具，不要读文件，不要做任何其他操作。",
        )
        print("STOP:", stop, flush=True)
        print("TEXT:", text, flush=True)
        print("DONE", flush=True)
    finally:
        bridge.close()
