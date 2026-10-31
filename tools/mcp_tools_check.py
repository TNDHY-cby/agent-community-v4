#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""判据：**MCP 真正落地** —— 平台把 14 个工具**逐个真调通**（外部 agent 视角，真 JSON-RPC over HTTP）。

## 为什么要它（老板 2026-10-25：「MCP 协议的会话内回答停了，现在要做的是 MCP 真正落地」）

"能连上、能列工具"只说明协议栈没坏；**落地**的意思是：外部 agent 用这些工具**能把活干完**。
所以这里逐个真调，而且**每条都要拿到真数据**（不是 200 就算过）：

| # | 工具 | 怎么判"真落地" |
|---|---|---|
| 1 | `status` | 回里有平台版本 |
| 2 | `list_harnesses` | 回里有刚注册的那条 |
| 3 | `list_agents` | 回的是列表（空也算真数据） |
| 4 | `create_workshop` | 真建出 `workshop_id` **和** `task_id` |
| 5 | `list_tasks` | 里面能看到刚建的任务 |
| 6 | `get_task` | 按 id 查得到同一条 |
| 7 | `workshop_discuss` | 发言真进讨论区（HTTP 侧再查一遍） |
| 8 | `workshop_interject` | 插话被接受或**如实说明为什么不能** |
| 9 | `workshop_review` | 审查被接受或如实说明 |
| 10 | `workshop_continue` | 续轮被接受或如实说明 |
| 11 | `submit_task_result` | 回执被接受，且 HTTP 侧能查到留痕/状态 |

跑在**临时实例**上（换端口 + 换数据目录），跑完关掉删目录 ⇒ 不污染真实数据。

退出码：0＝全部真过；1＝有工具没过（会点名）；2＝起不来/连不上（如实说没跑成）。
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


def _http(base: str, path: str, body: dict | None = None, token: str = "", timeout: float = 30):
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    h = {"Content-Type": "application/json"} if data else {}
    if token:
        h["X-Agent-Token"] = token
    req = urllib.request.Request(base + path, data=data, headers=h,
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


class McpHttpClient:
    """真的 MCP 客户端（HTTP 传输）—— 外部 agent 的样子，零第三方依赖。"""

    def __init__(self, url: str, name: str = "mcp-tools-check", token: str = ""):
        self.url = url
        self.name = name
        self.token = token
        self.sid = ""

    def _rpc(self, method: str, params: dict | None = None, rid: int = 1):
        h = {"Content-Type": "application/json"}
        if self.sid:
            h["Mcp-Session-Id"] = self.sid
        if self.token:
            h["Authorization"] = "Bearer " + self.token
        req = urllib.request.Request(self.url,
                                     data=json.dumps({"jsonrpc": "2.0", "id": rid,
                                                      "method": method,
                                                      "params": params or {}}).encode("utf-8"),
                                     headers=h, method="POST")
        with urllib.request.urlopen(req, timeout=60) as r:
            self.sid = r.headers.get("Mcp-Session-Id") or self.sid
            return json.loads(r.read().decode("utf-8") or "{}")

    def init(self) -> dict:
        d = self._rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                     "clientInfo": {"name": self.name, "version": "0"}})
        self._rpc("notifications/initialized", {})
        return (d.get("result") or {}).get("serverInfo") or {}

    def tools(self) -> list:
        return [t.get("name") for t in ((self._rpc("tools/list", {}) or {}).get("result") or {}).get("tools") or []]

    def call(self, name: str, args: dict | None = None, rid: int = 99):
        d = self._rpc("tools/call", {"name": name, "arguments": args or {}}, rid=rid)
        res = d.get("result") or {}
        text = ""
        for c in res.get("content") or []:
            if isinstance(c, dict) and c.get("type") == "text":
                text += str(c.get("text") or "")
        try:
            return res.get("isError") is True, json.loads(text) if text.strip() else {}
        except ValueError:
            return res.get("isError") is True, {"raw": text}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep", action="store_true", help="跑完不关临时实例")
    ap.add_argument("--base", default="", help="直接打一个已在跑的平台（默认自起临时实例）")
    a = ap.parse_args()

    port = _free_port()
    tmpdir = tempfile.mkdtemp(prefix="ac_mcp_tools_")
    base = a.base.rstrip("/") or ("http://127.0.0.1:%d" % port)
    srv = None
    if not a.base:
        env = dict(os.environ)
        env.update({"AC_PORT": str(port), "AC_DATA_DIR": tmpdir, "PYTHONIOENCODING": "utf-8"})
        srv = subprocess.Popen([PY, "-m", "agent_community.platform.server"], cwd=_ROOT, env=env,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    rows, fails, notes = [], [], []

    def check(name: str, ok: bool, detail: str = "") -> None:
        rows.append((name, bool(ok), detail))
        if not ok:
            fails.append("%s（%s）" % (name, detail))

    try:
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
            print("[FAIL] 平台没起来（%s）—— 这次不算跑成" % base)
            return 2

        # 先注册一个 harness（后面几个工具要用它，且能验"真数据"）
        st, reg = _http(base, "/api/harness/register", {
            "harness_id": "demo-mcp-tools", "harness_name": "示例Harness-MCP工具巡检",
            "harness_type": "custom", "wakeup_method": "mcp",
            "api_base_url": base + "/mcp", "ai": {"model_name": "示例模型"}})
        check("夹具：注册 harness", st in (200, 201), "HTTP %s" % st)

        cli = McpHttpClient(base + "/mcp", token="")
        info = cli.init()
        check("① initialize", bool(info.get("name")), "serverInfo=%r" % info)
        names = cli.tools()
        check("② tools/list 14 个工具", len(names) == 14, "%d 个：%s" % (len(names), names))

        err, d = cli.call("status", {})
        check("③ status 回真数据", (not err) and ("version" in json.dumps(d)), json.dumps(d)[:160])

        err, d = cli.call("list_harnesses", {})
        blob = json.dumps(d, ensure_ascii=False)
        check("④ list_harnesses 看得到刚注册的", (not err) and ("demo-mcp-tools" in blob), blob[:160])

        err, d = cli.call("list_agents", {})
        check("⑤ list_agents 有回", not err, json.dumps(d, ensure_ascii=False)[:160])

        err, made = cli.call("create_workshop", {"title": "MCP 工具巡检",
                                                 "description": "由 tools/mcp_tools_check.py 创建"})
        ws_id = str(made.get("workshop_id") or "")
        task_id = str(made.get("task_id") or "")
        check("⑥ create_workshop 建出工作间+任务", bool(ws_id) and bool(task_id),
              json.dumps(made, ensure_ascii=False)[:200])

        err, d = cli.call("list_tasks", {})
        check("⑦ list_tasks 看得到这条", task_id and task_id in json.dumps(d, ensure_ascii=False),
              json.dumps(d, ensure_ascii=False)[:160])

        err, d = cli.call("get_task", {"task_id": task_id})
        check("⑧ get_task 查得到同一条", (not err) and (task_id in json.dumps(d, ensure_ascii=False)),
              json.dumps(d, ensure_ascii=False)[:160])

        # 讨论类：先加成员 + 成员自检（协议型走真探），能开就真发
        _http(base, "/api/workshop/%s/members" % ws_id,
              {"members": [{"member_id": "m0", "role": "员工", "harness_ids": ["demo-mcp-tools"]}]})
        _http(base, "/api/workshop/%s/member/m0/self-check" % ws_id, {})
        said = "MCP 工具巡检：我在工作间里说话了"
        err, d = cli.call("workshop_discuss", {"ws_id": ws_id, "content": said})
        st, w = _http(base, "/api/workshop/%s" % ws_id)
        landed = said in json.dumps(w, ensure_ascii=False)
        check("⑨ workshop_discuss 发言真落进讨论区", landed, json.dumps(d, ensure_ascii=False)[:200])
        if not landed:
            notes.append("发言没落库时返回：%s" % json.dumps(d, ensure_ascii=False)[:160])

        for label, tool, args in (
                ("⑩ workshop_interject", "workshop_interject",
                 {"ws_id": ws_id, "content": "巡检插一句"}),
                ("⑪ workshop_review", "workshop_review",
                 {"ws_id": ws_id, "content": "巡检意见：可以继续", "verdict": "approved"}),
                ("⑫ workshop_continue", "workshop_continue", {"ws_id": ws_id})):
            err, d = cli.call(tool, args, rid=100 + len(rows))
            body = json.dumps(d, ensure_ascii=False)
            # 判据：要么真被接受（success/ok），要么**如实给出原因**（error/why/next）——不许静默空回
            ok = (not err) and (("success" in body and "true" in body.lower()) or d.get("ok") is True
                                or bool(d.get("error")) or bool(d.get("why")) or bool(d.get("next")))
            check(label + " 有明确结果或明确原因", ok, body[:200])

        err, d = cli.call("submit_task_result", {"harness_id": "demo-mcp-tools", "task_id": task_id,
                                                 "workshop_id": ws_id, "member_id": "m0",
                                                 "ok": True, "content": "工具巡检：活干完了"},
                          rid=200)
        body = json.dumps(d, ensure_ascii=False)
        check("⑬ submit_task_result 被接受且说清结果",
              (not err) and bool(d.get("success")) and ("task_state_changed" in d or d.get("note")),
              body[:240])
        # ── 「让它自己发」：平台放一句 → 它取 → 它自报发了 → 平台看得见 ──
        said_txt = "请你在自己的会话里说：平台自检通过"
        st, put = _http(base, "/api/mcp/session-notes", {"text": said_txt})
        check("⑮ 平台放下「请它自己发」的一句话", st == 200 and bool((put.get("note") or {}).get("id")),
              "HTTP %s %s" % (st, json.dumps(put, ensure_ascii=False)[:160]))
        err, pend = cli.call("pending_session_notes", {"limit": 5}, rid=300)
        pend_items = (pend or {}).get("notes") or []
        check("⑯ 它取得到这条待办（平台不代发）",
              (not err) and any(n.get("text") == said_txt for n in pend_items),
              json.dumps(pend, ensure_ascii=False)[:200])
        nid = next((n["id"] for n in pend_items if n.get("text") == said_txt), "")
        err2, rep = cli.call("report_session_sent", {"note_id": nid, "text": said_txt}, rid=301)
        check("⑰ 它自报「我已经发了」被记下（并写明这是它的自报）",
              (not err2) and bool((rep or {}).get("ok")) and bool((rep or {}).get("scope")),
              json.dumps(rep, ensure_ascii=False)[:200])
        st, lst = _http(base, "/api/mcp/session-notes", timeout=20)
        sent = [n for n in (lst.get("notes") or []) if n.get("id") == nid and n.get("state") == "sent"]
        check("⑱ 平台侧看得到它已发（含时间）", bool(sent),
              json.dumps(lst, ensure_ascii=False)[:200])

        # ── 「工作过程」：它干活时报一步，平台侧看得到（用户最直观的那条） ──
        step_txt = "我已经把配置改完了，正在跑自检"
        err3, rep = cli.call("report_progress",
                             {"text": step_txt, "task_id": task_id, "kind": "step"}, rid=400)
        check("⑲ report_progress 被接受（它自己报的一步）",
              (not err3) and bool((rep or {}).get("ok")), json.dumps(rep, ensure_ascii=False)[:200])
        st2, pg = _http(base, "/api/progress?limit=20", timeout=20)
        check("⑳ 平台侧「工作过程」里看得到这一步（含时间）",
              any(i.get("text") == step_txt and i.get("at") for i in (pg.get("items") or [])),
              json.dumps(pg, ensure_ascii=False)[:200])

        st, msg = _http(base, "/api/mcp/calls", timeout=20)
        calls = msg.get("calls") or []
        check("⑭ 平台留痕记下了这些调用（客户端名/工具）",
              any(c.get("kind") == "tools/call" for c in calls)
              and any(c.get("client") == "mcp-tools-check" for c in calls),
              "count=%s" % msg.get("count"))
    finally:
        if srv is not None and not a.keep:
            srv.terminate()
            try:
                srv.wait(timeout=15)
            except subprocess.TimeoutExpired:
                srv.kill()
        if not a.keep:
            shutil.rmtree(tmpdir, ignore_errors=True)

    print("=" * 78)
    print("判据：MCP 真正落地 —— 平台 14 个工具逐个真调（%s）" % base)
    print("=" * 78)
    for name, ok, detail in rows:
        print("  %s %s%s" % ("PASS" if ok else "FAIL", name, ("　（%s）" % detail) if not ok else ""))
    for n in notes:
        print("  （附注）%s" % n)
    print("-" * 78)
    print("合计 %d 格：PASS %d / FAIL %d" % (len(rows), len(rows) - len(fails), len(fails)))
    return 0 if not fails else 1


if __name__ == "__main__":
    raise SystemExit(main())
