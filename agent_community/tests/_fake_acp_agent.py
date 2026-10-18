# -*- coding: utf-8 -*-
"""**假 ACP agent**：给"acp 档能不能真被一条命令拉起来"提供可复现的对端（只用于测试/体检，不是产品）。

协议口径照抄 `platform/acp_harness_bridge.py` 的客户端实现（一处真相）：
  · 收 `initialize`            → 回 `{protocolVersion, agentCapabilities:{}}`
  · 收 `session/new {cwd}`     → 回 `{sessionId:"s-fake"}`
  · 收 `session/prompt`        → 先推一条 `session/update`（agent_message_chunk）再回 `{stopReason:"end_turn"}`

被拉起的证据：进程启动时把 pid 写进 `AC_FAKE_ACP_MARK`（如果设了）；每收一个请求追加一行到同目录的 `.log`。
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

PROTOCOL_VERSION = 1
SESSION_ID = "s-fake"


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


def main() -> int:
    _mark("[fake-acp] started pid=%d cwd=%s" % (os.getpid(), Path.cwd()))
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except Exception:                                   # noqa: BLE001
            continue
        method = str(req.get("method") or "")
        rid = req.get("id")
        _mark("[fake-acp] recv %s id=%s" % (method, rid))
        if method == "initialize":
            _send({"jsonrpc": "2.0", "id": rid,
                   "result": {"protocolVersion": PROTOCOL_VERSION, "agentCapabilities": {}}})
        elif method == "session/new":
            _send({"jsonrpc": "2.0", "id": rid, "result": {"sessionId": SESSION_ID}})
        elif method == "session/prompt":
            params = req.get("params") or {}
            # 先推一条流式通知（客户端靠它拼回复文本）
            _send({"jsonrpc": "2.0", "method": "session/update",
                   "params": {"sessionId": params.get("sessionId") or SESSION_ID,
                              "update": {"sessionUpdate": "agent_message_chunk",
                                         "content": {"type": "text",
                                                     "text": "收到，已进入工作状态。"}}}})
            _send({"jsonrpc": "2.0", "id": rid, "result": {"stopReason": "end_turn"}})
            _mark("[fake-acp] prompted ok")
        else:
            if rid is not None:
                _send({"jsonrpc": "2.0", "id": rid,
                       "error": {"code": -32601, "message": "method not found: %s" % method}})
    _mark("[fake-acp] stdin closed, exit")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
