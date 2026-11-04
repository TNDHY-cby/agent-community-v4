# -*- coding: utf-8 -*-
"""守卫：**「把本平台挂成 MCP 服务器」的配置真的能挂上**（老板 2026-10-25「直接按 MCP 协议用」）。

## 为什么要有这一份

我们改成"平台当 MCP 服务端、对方的 agent 当客户端"之后，最容易出错的是**那份配置本身**：
写死了某台机器的解释器/路径、或者给出来的 args 根本起不来 —— 用户照抄过去，客户端报"连接失败"，
而开发机上一切正常（典型的"只在我这台机器成立"）。

判据（**真起进程、真走 JSON-RPC**，不看源码文本）：
  ① 配置里的 `command` 是**当前跑平台的解释器**、`PYTHONPATH` 是**本仓库根目录**（不是写死的盘符）；
  ② 按这份配置 `spawn` 一个进程，发 `initialize` → 回 serverInfo；发 `tools/list` → 回全部工具；
  ③ 工具表里必须有 `status` 与 `submit_task_result`（一个读、一个写回执）；
  ④ 配置里**不出现令牌明文**；`format=md` 的说明里写了怎么自己验证。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.append(str(_ROOT))

from agent_community.platform import mcp_client_config as mc  # noqa: E402


def _spawn_and_talk(cfg: dict, timeout: float = 60) -> dict:
    """按配置真起一个 MCP 服务端进程，走 initialize + tools/list（真协议流）。"""
    env = dict(os.environ)
    env.update({k: str(v) for k, v in (cfg.get("env") or {}).items()})
    env.setdefault("PYTHONIOENCODING", "utf-8")
    proc = subprocess.Popen([cfg["command"], *cfg["args"]], stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env,
                            cwd=str(_ROOT), text=True, encoding="utf-8", errors="replace")
    out = {}
    try:
        req = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
               "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                          "clientInfo": {"name": "ac-guard", "version": "0"}}}
        proc.stdin.write(json.dumps(req) + "\n")
        proc.stdin.flush()
        out["init"] = _read_result(proc, 1, timeout)
        proc.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized",
                                     "params": {}}) + "\n")
        proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list",
                                     "params": {}}) + "\n")
        proc.stdin.flush()
        out["tools"] = _read_result(proc, 2, timeout)
    finally:
        try:
            proc.stdin.close()
        except OSError:                                          # 有意静默：收尾关闭失败不影响结论
            pass
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
    return out


def _read_result(proc, want_id: int, timeout: float) -> dict:
    import time as _t

    deadline = _t.time() + timeout
    while _t.time() < deadline:
        line = proc.stdout.readline()
        if not line:
            if proc.poll() is not None:
                err = ""
                try:
                    err = (proc.stderr.read() or "")[-600:]
                except (OSError, ValueError):                     # 有意静默：读不到 stderr 也照样报失败
                    err = ""
                raise AssertionError("MCP 服务端进程退出了（stderr 尾部：%s）" % err)
            continue
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            continue                                            # 非协议行（横幅等）跳过
        if msg.get("id") == want_id:
            if "error" in msg:
                raise AssertionError("MCP 回错误：%s" % msg["error"])
            return msg.get("result") or {}
    raise AssertionError("等不到 id=%s 的响应（超时 %ss）" % (want_id, timeout))


def test_http_form_points_at_the_platform_mcp_endpoint():
    """桌面类应用只能填地址 —— 那份 HTTP 写法里的 `url` 必须**真能 initialize**。

    2026-10-25 加：平台已经能当 MCP 服务端（`POST /mcp`），并在 `GET /api/mcp/calls` 留痕；
    所以"它会不会来"是可以判的 —— 前提是配置里给的地址是对的。
    """
    from fastapi.testclient import TestClient

    from agent_community.platform import server as srv

    d = mc.build(base_url="http://127.0.0.1:18920")
    url = d["http"]["mcpServers"]["external-agent-community"]["url"]
    assert url == "http://127.0.0.1:18920/mcp", url
    assert d["server"]["http_url"] == url
    # 用同一台 app 的 TestClient 直接打这个路径（真走 JSON-RPC）
    c = TestClient(srv.app, client=("127.0.0.1", 54321))
    r = c.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize",
                             "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                                        "clientInfo": {"name": "config-guard", "version": "0"}}})
    assert r.status_code == 200, r.text
    assert r.json()["result"]["serverInfo"]["name"] == "external-agent-community"


def test_md_gives_both_transports_and_says_look_at_evidence():
    md = mc.build_md(base_url="http://127.0.0.1:18920")
    assert "1-A" in md and "1-B" in md, "两种传输各给一份（桌面类填地址 / 命令行类填命令）"
    assert "/mcp" in md and "mcpServers" in md
    assert "/api/mcp/calls" in md, "自验方式必须写清：看平台留痕，而不是听它自己说"


def test_config_has_no_machine_specific_paths():
    d = mc.build(base_url="http://127.0.0.1:18920")
    entry = d["generic"]
    assert entry["command"] == sys.executable, "解释器必须是**当前跑平台的这个**（换机器自动对）"
    assert entry["args"] == ["-m", mc.SERVER_MODULE]
    assert entry["env"]["PYTHONPATH"] == str(_ROOT), "PYTHONPATH 必须是本仓库根目录（从文件位置算）"
    assert entry["env"]["AC4_BASE_URL"] == "http://127.0.0.1:18920"
    assert "token" not in json.dumps(entry, ensure_ascii=False).lower(), \
        "配置里不许出现令牌（只给环境变量名与说明）"


def test_generated_config_really_starts_and_lists_the_same_tools():
    """★核心：照抄这份配置去 spawn，真的能 initialize + 列出全部工具。"""
    d = mc.build(base_url="http://127.0.0.1:18920")
    got = _spawn_and_talk(d["generic"])
    info = (got.get("init") or {}).get("serverInfo") or {}
    assert info.get("name"), "initialize 要回 serverInfo（拿到 %r）" % got.get("init")
    tools = (got.get("tools") or {}).get("tools") or []
    names = [t.get("name") for t in tools if isinstance(t, dict)]
    # 不写死数字：和 `mcp_server.TOOLS` 比（那是真源；子进程列出的必须与之一致）
    from agent_community import mcp_server as _srv2
    assert len(names) == len(_srv2.TOOLS), (
        "工具数应为 %d（实测 %d：%s）" % (len(_srv2.TOOLS), len(names), names))
    for must in ("status", "submit_task_result"):
        assert must in names, "缺关键工具 %s（%s）" % (must, names)
    # 配置里承诺的工具表必须与**真列出来的**一致（不许一处真相两套）
    promised = [t["name"] for t in d["tools"]]
    assert sorted(promised) == sorted(names), \
        "配置里写的工具表 %s ≠ 真实工具表 %s" % (sorted(promised), sorted(names))


def test_md_guide_says_how_to_verify_yourself():
    md = mc.build_md(base_url="http://127.0.0.1:18920")
    assert "mcpServers" in md and "PYTHONPATH" in md
    assert "怎么自己验证" in md and "protocol_call_check" in md
    assert "AC4_TOKEN" in md, "令牌那条要写清（但不写值）"


if __name__ == "__main__":                                       # pragma: no cover
    import pytest

    raise SystemExit(pytest.main([__file__, "-v"]))
