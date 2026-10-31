#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""判据：**MCP 高级协议全链路**真的能跑通（外部 agent 连上来，看活、说话、交活，平台记得住）。

## 为什么要有它（老板 2026-10-25：「我们先把高级协议先跑通」）

`tools/protocol_call_check.py` 只证明"接口可达"（initialize / tools/list）。
"跑通"要更进一步：**一个外部 agent 走 MCP 协议，能把活干完**——

```
连上（stdio + initialize + tools/list）
  → create_workshop 派/建一个工作间（真建出任务）
  → list_tasks 看得到它
  → workshop_discuss 在里面说一句
  → submit_task_result 把回执交回去
  → 平台侧真变了（任务状态/结果/消息都能查到）
```

## 不污染真实数据（这条是硬要求）

自己起一个**临时平台实例**（换端口 + 换数据目录，`AC_PORT` / `AC_DATA_DIR`），
跑完就关、临时目录删掉。 ⇒ 顺手也是"换端口换数据目录不改一行代码"那条产品化判据的一次真跑。

退出码：0＝全格通过；1＝有格没过；2＝连不上/起不来（如实说"没跑成"，不假绿）。

用法：
    python tools/mcp_loop_check.py
    python tools/mcp_loop_check.py --keep        # 留着临时实例与目录，便于手查
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
if _ROOT not in sys.path:
    sys.path.append(_ROOT)

PY = sys.executable


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def _http(base: str, path: str, body: dict | None = None, timeout: float = 20):
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    req = urllib.request.Request(base + path, data=data,
                                 headers={"Content-Type": "application/json"} if data else {},
                                 method="POST" if data is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return int(r.status), json.loads(r.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            return int(e.code), json.loads(raw or "{}")
        except ValueError:
            return int(e.code), {"raw": raw[:300]}


class McpAgent:
    """一个**真的 MCP 客户端**（stdio JSON-RPC）—— 就是"外部 agent"的样子，零第三方依赖。"""

    def __init__(self, cfg: dict, cwd: str, extra_env: dict | None = None):
        env = dict(os.environ)
        env.update({k: str(v) for k, v in (cfg.get("env") or {}).items()})
        env.update({k: str(v) for k, v in (extra_env or {}).items()})
        env["PYTHONIOENCODING"] = "utf-8"
        self.p = subprocess.Popen([cfg["command"], *cfg["args"]], stdin=subprocess.PIPE,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  text=True, encoding="utf-8", errors="replace",
                                  env=env, cwd=cwd)
        self._id = 0

    def call(self, method: str, params: dict, timeout: float = 90) -> dict:
        self._id += 1
        mid = self._id
        self.p.stdin.write(json.dumps({"jsonrpc": "2.0", "id": mid, "method": method,
                                       "params": params}) + "\n")
        self.p.stdin.flush()
        deadline = time.time() + timeout
        while time.time() < deadline:
            line = self.p.stdout.readline()
            if not line:
                if self.p.poll() is not None:
                    err = ""
                    try:
                        err = (self.p.stderr.read() or "")[-500:]
                    except (OSError, ValueError):
                        err = ""
                    raise RuntimeError("MCP 进程退出了（stderr：%s）" % err)
                continue
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if msg.get("id") == mid:
                if "error" in msg:
                    raise RuntimeError("MCP 报错：%s" % msg["error"])
                return msg.get("result") or {}
        raise RuntimeError("等不到 %s 的响应（超时 %ss）" % (method, timeout))

    def notify(self, method: str) -> None:
        self.p.stdin.write(json.dumps({"jsonrpc": "2.0", "method": method, "params": {}}) + "\n")
        self.p.stdin.flush()

    def tool(self, name: str, args: dict | None = None, timeout: float = 90):
        """调一个工具，返回被解析过的 JSON（工具回的是文本，里面是 JSON）。"""
        res = self.call("tools/call", {"name": name, "arguments": args or {}}, timeout=timeout)
        text = ""
        for c in res.get("content") or []:
            if isinstance(c, dict) and c.get("type") == "text":
                text += str(c.get("text") or "")
        try:
            return json.loads(text) if text.strip() else {}
        except ValueError:
            return {"raw": text}

    def close(self) -> None:
        try:
            self.p.stdin.close()
        except OSError:      # noqa: BLE001 — 有意静默：收尾关管道失败不影响已经得出的结论
            pass
        try:
            self.p.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.p.kill()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep", action="store_true", help="跑完不关临时实例（默认关掉并删目录）")
    ap.add_argument("--peer-url", default="",
                    help="真 MCP 对端的地址（例如本机某个 MCP 插件的 http://127.0.0.1:PORT/mcp）；"
                         "不给就用一个占位地址 —— 那样成员「自检」必然不过，闸门会如实拦下讨论")
    a = ap.parse_args()

    from agent_community.platform.mcp_client_config import build

    port = _free_port()
    tmpdir = tempfile.mkdtemp(prefix="ac_mcp_loop_")
    base = "http://127.0.0.1:%d" % port
    env = dict(os.environ)
    env.update({"AC_PORT": str(port), "AC_DATA_DIR": tmpdir, "PYTHONIOENCODING": "utf-8"})
    srv = subprocess.Popen([PY, "-m", "agent_community.platform.server"], cwd=_ROOT, env=env,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    rows, fails, notes, skips = [], [], [], []

    def check(name: str, ok: bool, detail: str = "") -> None:
        rows.append((name, bool(ok), detail))
        if not ok:
            fails.append("%s（%s）" % (name, detail))

    def skip(name: str, why: str) -> None:
        """**环境不在就如实跳过**：不算通、也不算平台坏了（否则会被读成"平台有 bug"）。"""
        rows.append((name, None, why))
        skips.append("%s（%s）" % (name, why))

    agent = None
    try:
        # 0) 临时平台起来了吗（换端口 + 换数据目录）
        up = False
        for _ in range(60):
            try:
                st, _j = _http(base, "/api/status", timeout=3)
                up = st == 200
            except (urllib.error.URLError, OSError):
                up = False
            if up:
                break
            time.sleep(0.5)
        if not up:
            print("[FAIL] 临时平台没起来（端口 %d / 数据目录 %s）—— 这次不算跑成" % (port, tmpdir))
            return 2
        check("临时平台起来（换端口+换数据目录）", True)

        # 0b) 先注册一个 harness —— 后面要拿它签发的 Agent-Token（回执必须带）
        reg = _http(base, "/api/harness/register", {"harness_id": "demo-mcp-loop",
                                                    "harness_name": "示例Harness-MCP自检",
                                                    "harness_type": "custom",
                                                    "wakeup_method": "mcp",
                                                    "api_base_url": a.peer_url or (base + "/mcp"),
                                                    "ai": {"model_name": "示例模型"}})
        check("注册一个 harness（加成员/交回执要用它）", reg[0] in (200, 201), "HTTP %s" % reg[0])
        token = str((reg[1] or {}).get("agent_token") or "")
        check("平台给这个 harness 签发了 Agent-Token", bool(token),
              "register 返回里没有 agent_token ⇒ 回执会 401")

        # 1) 一个**外部 agent** 按 MCP 协议连上来（令牌走环境变量，不经过模型）
        cfg = build(base_url=base)["generic"]
        agent = McpAgent(cfg, cwd=_ROOT, extra_env={"AC_HARNESS_TOKEN": token})
        init = agent.call("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                         "clientInfo": {"name": "mcp-loop-agent", "version": "0"}})
        info = init.get("serverInfo") or {}
        check("initialize 通了", bool(info.get("name")), "serverInfo=%r" % info)
        agent.notify("notifications/initialized")
        tools = [t.get("name") for t in (agent.call("tools/list", {}).get("tools") or [])
                 if isinstance(t, dict)]
        check("tools/list 给了工具表", len(tools) >= 10, "工具数 %d" % len(tools))

        # 2) 建工作间（真建出任务）
        made = agent.tool("create_workshop", {"title": "MCP 全链路自检",
                                              "description": "由 tools/mcp_loop_check.py 自动创建"})
        ws_id = str(made.get("workshop_id") or made.get("ws_id") or made.get("id") or "")
        task_id = str(made.get("task_id") or "")
        check("create_workshop 真建出工作间", bool(ws_id), json.dumps(made, ensure_ascii=False)[:200])
        check("create_workshop 带出任务号", bool(task_id), "task_id=%r" % task_id)

        # 3) 平台侧对账（不是只听 MCP 自己说）
        if ws_id:
            st, ws = _http(base, "/api/workshop/%s/members-status" % ws_id)
            check("平台侧查得到这个工作间", st in (200, 404) and st == 200, "HTTP %s" % st)

        # 4) list_tasks 看得到它
        tasks = agent.tool("list_tasks")
        items = tasks.get("tasks") if isinstance(tasks, dict) else tasks
        ids = [str(t.get("id") or t.get("task_id") or "") for t in (items or []) if isinstance(t, dict)]
        check("list_tasks 里能看到这条任务", bool(task_id) and task_id in ids,
              "任务数 %d" % len(ids))

        # 4c) 加成员（空成员的工作间，讨论会被闸门拦住 —— 那是正确行为，不是 bug）
        if ws_id:
            st, memb = _http(base, "/api/workshop/%s/members" % ws_id,
                             {"members": [{"member_id": "m0", "role": "员工",
                                           "harness_ids": ["demo-mcp-loop"]}]})
            check("给工作间加一个成员", st in (200, 201), "HTTP %s %s" % (st, str(memb)[:120]))

        # 4d) 让这个成员「验一次」（不验，讨论会被闸门如实拦下 —— 那是正确行为）
        if ws_id:
            st, sc = _http(base, "/api/workshop/%s/member/m0/self-check" % ws_id, {})
            _peer_ok = bool((sc or {}).get("ok"))
            if _peer_ok:
                check("成员「验一次」通过（协议型＝真探了它）", True)
            else:
                skip("成员「验一次」", "对端不可达/没起来：%s" % str((sc or {}).get("why") or sc)[:120])

        # 5) 在工作间里说一句（真落进讨论流）
        if ws_id:
            d = agent.tool("workshop_discuss", {"ws_id": ws_id, "content": "MCP 自检：我在工作间里说话了",
                                                "agent": "demo-mcp-loop"})
            st, ws2 = _http(base, "/api/workshop/%s" % ws_id)
            landed = st == 200 and "MCP 自检：我在工作间里说话了" in json.dumps(ws2, ensure_ascii=False)
            # 判据只认"真的进去了"。发言之后的 AI 环节要另配密钥，失败不是讨论失败 —— 但必须说出来。
            if landed:
                check("workshop_discuss 发言真的进了讨论区", True)
            elif not _peer_ok:
                skip("workshop_discuss 发言", "对端没在跑 ⇒ 闸门如实拒（这是正确行为，不是平台 bug）")
            else:
                check("workshop_discuss 发言真的进了讨论区", False,
                      json.dumps(d, ensure_ascii=False)[:220])
            if d.get("error"):
                notes.append("发言已落库；随后的 AI 环节报错（这条实例没配 AI）：%s"
                             % str(d.get("error"))[:120])

        # 6) 交回执（真带 Agent-Token 的 MCP 客户端的 Env 里要给它 —— 见 D后端-36/V104）
        if task_id:
            res = agent.tool("submit_task_result", {"harness_id": "demo-mcp-loop", "task_id": task_id,
                                                    "workshop_id": ws_id, "member_id": "m0",
                                                    "ok": True, "content": "MCP 自检：活干完了",
                                                    "confidence": 0.9})
            accepted = bool(res.get("success")) and not res.get("error")
            check("submit_task_result 被平台接受（带 X-Agent-Token）", accepted,
                  json.dumps(res, ensure_ascii=False)[:220])
            check("回执说清它记到哪 / 状态变没变",
                  ("task_state_changed" in res) and (bool(res.get("note")) or res.get("task_state_changed") is True),
                  json.dumps(res, ensure_ascii=False)[:220])
            # 真对账：回执要么出现在这条工作间的讨论里，要么任务状态真变了
            st, ws3 = _http(base, "/api/workshop/%s" % ws_id)
            in_ws = st == 200 and "MCP 自检：活干完了" in json.dumps(ws3, ensure_ascii=False)
            st2, task2 = _http(base, "/api/task/%s" % task_id)
            blob2 = json.dumps(task2, ensure_ascii=False)
            changed = st2 == 200 and (str(task2.get("status")) in ("completed", "done")
                                      or "MCP 自检：活干完了" in blob2)
            check("平台侧真记下了回执（讨论区或任务状态）", in_ws or changed,
                  "ws记录=%s 任务status=%r" % (in_ws, task2.get("status")))
    finally:
        if agent is not None:
            agent.close()
        if not a.keep:
            srv.terminate()
            try:
                srv.wait(timeout=15)
            except subprocess.TimeoutExpired:
                srv.kill()
            shutil.rmtree(tmpdir, ignore_errors=True)

    print("=" * 78)
    print("判据：MCP 高级协议全链路（临时实例 %s，数据目录隔离）" % base)
    print("=" * 78)
    for name, ok, detail in rows:
        tag = "SKIP" if ok is None else ("PASS" if ok else "FAIL")
        print("  %s %s%s" % (tag, name, ("　（%s）" % detail) if ok is not True else ""))
    for _n in notes:
        print("  （附注）%s" % _n)
    print("-" * 78)
    _pass = sum(1 for _n, _o, _d in rows if _o is True)
    print("合计 %d 格：PASS %d / FAIL %d / SKIP %d（SKIP＝环境不在，如实跳过，不算通）"
          % (len(rows), _pass, len(fails), len(skips)))
    if a.keep:
        print("（--keep：临时实例还在跑 %s，数据目录 %s）" % (base, tmpdir))
    return 0 if not fails else 1


if __name__ == "__main__":
    raise SystemExit(main())
