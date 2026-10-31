# -*- coding: utf-8 -*-
"""**假 ACP agent（可多轮、可多会话、可记上下文）**：给 `V119` 批 C 提供可复现对端。

协议口径照抄 `platform/acp_harness_bridge.py` 的客户端实现（一处真相）：
  · `initialize`     → `{protocolVersion, agentCapabilities:{}}`
  · `session/new`    → `{sessionId:"s-<n>"}`（**每条会话一个号**，支持并行多会话）
  · `session/prompt` → **先记进本会话的历史**，再推 `session/update`（回复里带回"我记住了什么"），
                       最后回 `{stopReason:"end_turn"}`

为什么升级成有状态：`V119` 批 C 要判的是「**多轮上下文不丢**」和「多会话不串」，
无状态的假 agent 测不出这两条（它的回复永远是同一句 = 假绿）。

被拉起的证据：`AC_FAKE_ACP_MARK` 指向的文件里追加每一件事（含 pid / 会话号 / 每轮收到的文本）。
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

PROTOCOL_VERSION = 1


def _mark(text: str) -> None:
    p = os.environ.get("AC_FAKE_ACP_MARK")
    if not p:
        return
    try:
        with open(p, "a", encoding="utf-8") as fh:
            fh.write(text.rstrip() + "\n")
    except OSError:
        pass


def _send(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


class _Agent:
    def __init__(self) -> None:
        self.sessions: dict[str, list] = {}
        self.seq = 0

    def new_session(self) -> str:
        self.seq += 1
        sid = "s-%d" % self.seq
        self.sessions[sid] = []
        return sid

    def reply(self, sid: str, text: str) -> str:
        """把这一轮记进会话历史，回复里**回显**历史（这样多轮/串会话都能被真判出来）。"""
        hist = self.sessions.setdefault(sid, [])
        hist.append(text)
        return "收到第%d轮。本会话记得：%s" % (len(hist), " / ".join(hist))


def main() -> int:
    _mark("[fake-acp] started pid=%d cwd=%s" % (os.getpid(), Path.cwd()))
    agent = _Agent()
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except ValueError:
            continue
        method = str(req.get("method") or "")
        rid = req.get("id")
        params = req.get("params") or {}
        _mark("[fake-acp] recv %s id=%s" % (method, rid))

        if method == "initialize":
            _send({"jsonrpc": "2.0", "id": rid,
                   "result": {"protocolVersion": PROTOCOL_VERSION, "agentCapabilities": {}}})
        elif method == "session/new":
            sid = agent.new_session()
            _mark("[fake-acp] session/new -> %s cwd=%s" % (sid, params.get("cwd")))
            _send({"jsonrpc": "2.0", "id": rid, "result": {"sessionId": sid}})
        elif method == "session/prompt":
            sid = str(params.get("sessionId") or "")
            text = ""
            for block in params.get("prompt") or []:
                text += str(block.get("text") or "")
            if sid not in agent.sessions:
                # 未知会话必须明确报错（客户端据此判定"旧会话已释放"，不假装续上）
                _send({"jsonrpc": "2.0", "id": rid,
                       "error": {"code": -32001, "message": "unknown session: %s" % sid}})
                _mark("[fake-acp] prompt on unknown session %s (rejected)" % sid)
                continue
            reply = agent.reply(sid, text)
            _send({"jsonrpc": "2.0", "method": "session/update",
                   "params": {"sessionId": sid,
                              "update": {"sessionUpdate": "agent_message_chunk",
                                         "content": {"type": "text", "text": reply}}}})
            _send({"jsonrpc": "2.0", "id": rid, "result": {"stopReason": "end_turn"}})
            _mark("[fake-acp] prompted sid=%s turn=%d" % (sid, len(agent.sessions[sid])))
        else:
            if rid is not None:
                _send({"jsonrpc": "2.0", "id": rid,
                       "error": {"code": -32601, "message": "method not found: %s" % method}})
    _mark("[fake-acp] stdin closed, exit")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
