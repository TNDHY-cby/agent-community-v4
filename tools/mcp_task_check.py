# -*- coding: utf-8 -*-
"""判据：MCP 侧异步任务**真能查**（真起一个假 MCP Server，不是 mock）。

老板 2026-11-03：「A2A 只是一个协议，为什么不再拓展一个 MCP Tasks」。

验六格：
  ① 标准 Tasks 扩展的 Server → 探测出 `tasks`，且 `tasks/get` 能查到 done + 文本
  ② 只有「自定义查状态工具」的 Server → 探测出 `job_tool`，按工具名查得到
  ③ 什么都没有的 Server → 如实说 **不支持**（不编"看起来在跑"）
  ④ 状态归一化：对方报 `TASK_STATE_COMPLETED` / `completed` 都认成 done
  ⑤ 非终态（working）时 `terminal=False`
  ⑥ 查不动（端点不在）要抛错而不是假装成功

用法：`python tools/mcp_task_check.py` —— 跑在随机端口，跑完自动收。
"""
from __future__ import annotations

import json
import socket
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent_community.platform import mcp_client as MC  # noqa: E402

ROWS: list[tuple[str, bool, str]] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    ROWS.append((label, ok, detail))
    print("  %s %s%s" % ("PASS" if ok else "FAIL", label, ("　（" + detail + "）") if detail and not ok else ""))


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class _Base(BaseHTTPRequestHandler):
    #: 子类覆盖：{"mode": "tasks"|"job"|"none"}
    MODE = "none"

    def log_message(self, *a):  # noqa: D401 — 静音（判据输出要干净）
        pass

    def _send(self, obj, code=200, headers=None):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):  # noqa: N802
        n = int(self.headers.get("Content-Length") or 0)
        try:
            req = json.loads(self.rfile.read(n).decode() or "{}")
        except Exception:  # noqa: BLE001
            req = {}
        mid = req.get("id")
        method = str(req.get("method") or "")
        sid = self.headers.get("Mcp-Session-Id") or "sess-fake"

        def ok(result):
            self._send({"jsonrpc": "2.0", "id": mid, "result": result}, headers={"Mcp-Session-Id": sid})

        if method == "initialize":
            caps = {}
            if self.MODE == "tasks":
                caps["tasks"] = {"list": False}
            ok({"protocolVersion": "2025-06-18", "capabilities": caps,
                "serverInfo": {"name": "fake-%s" % self.MODE, "version": "0"}})
            return
        if method.startswith("notifications/"):
            self.send_response(200)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if method == "tools/list":
            tools = []
            if self.MODE == "job":
                tools = [{"name": "get_job_status", "description": "查任务状态",
                          "inputSchema": {"type": "object", "properties": {}, "required": []}}]
            elif self.MODE == "tasks":
                tools = [{"name": "do_thing", "description": "干一件事",
                          "inputSchema": {"type": "object", "properties": {}, "required": []}}]
            ok({"tools": tools})
            return
        if method == "tools/call":
            name = str((req.get("params") or {}).get("name") or "")
            if name == "get_job_status":
                ok({"content": [{"type": "text",
                                 "text": json.dumps({"status": "completed",
                                                     "result": "活干完了（自定义 job 约定）"},
                                                    ensure_ascii=False)}]})
                return
            ok({"content": [{"type": "text", "text": "ok"}]})
            return
        if method == "tasks/get":
            # 故意用 v1.0 的带前缀枚举，验归一化
            ok({"taskId": "t1", "status": {"state": "TASK_STATE_COMPLETED"},
                "content": [{"type": "text", "text": "活干完了（标准 Tasks 扩展）"}]})
            return
        self._send({"jsonrpc": "2.0", "id": mid,
                    "error": {"code": -32601, "message": "no method %s" % method}})


def _serve(mode: str) -> tuple[ThreadingHTTPServer, str]:
    port = _free_port()
    cls = type("H_%s" % mode, (_Base,), {"MODE": mode})
    srv = ThreadingHTTPServer(("127.0.0.1", port), cls)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, "http://127.0.0.1:%d/mcp" % port


def main() -> int:
    import asyncio

    print("判据：MCP 侧异步任务（真起假 Server，不是 mock）")
    print("=" * 74)
    servers = []
    try:
        # ① 标准 Tasks 扩展
        s1, u1 = _serve("tasks")
        servers.append(s1)
        tools1 = asyncio.run(MC.mcp_list_tools(u1))
        init1, _ = asyncio.run(MC.mcp_open_session(u1))
        # mcp_open_session 返回的**已经是解包后的 result**，别再套一层 .get("result")
        caps1 = (init1 or {}).get("capabilities") or {}
        conv1 = MC.mcp_task_convention(tools1, caps1)
        check("① a 探测出「标准 Tasks 扩展」", conv1.get("kind") == "tasks", str(conv1))
        got1 = asyncio.run(MC.mcp_task_get(u1, "t1", conv1))
        check("① b tasks/get 查到终态", got1.get("terminal") is True and got1.get("state") == "done",
              json.dumps(got1, ensure_ascii=False)[:200])
        # state_raw 是**去掉 TASK_STATE_ 前缀之后**的原值（归一化就包含去前缀），
        # 所以这里期望 'completed'；state 才是映射后的 done。
        check("④ 状态归一化（TASK_STATE_COMPLETED → completed → done）",
              got1.get("state") == "done" and got1.get("state_raw") == "completed",
              "state=%r state_raw=%r" % (got1.get("state"), got1.get("state_raw")))
        check("⑤ 终态时 text 取得到", "标准 Tasks" in str(got1.get("text")), str(got1.get("text"))[:60])

        # ② 只有自定义查状态工具
        s2, u2 = _serve("job")
        servers.append(s2)
        tools2 = asyncio.run(MC.mcp_list_tools(u2))
        init2, _ = asyncio.run(MC.mcp_open_session(u2))
        # mcp_open_session 返回的**已经是解包后的 result**，别再套一层 .get("result")
        caps2 = (init2 or {}).get("capabilities") or {}
        conv2 = MC.mcp_task_convention(tools2, caps2)
        check("② a 探测出「自定义 job 工具」",
              conv2.get("kind") == "job_tool" and conv2.get("tool") == "get_job_status", str(conv2))
        got2 = asyncio.run(MC.mcp_task_get(u2, "j1", conv2))
        check("② b 按工具名查得到终态", got2.get("terminal") is True and got2.get("state") == "done",
              json.dumps(got2, ensure_ascii=False)[:200])

        # ③ 都不支持
        s3, u3 = _serve("none")
        servers.append(s3)
        tools3 = asyncio.run(MC.mcp_list_tools(u3))
        init3, _ = asyncio.run(MC.mcp_open_session(u3))
        # mcp_open_session 返回的**已经是解包后的 result**，别再套一层 .get("result")
        caps3 = (init3 or {}).get("capabilities") or {}
        conv3 = MC.mcp_task_convention(tools3, caps3)
        check("③ 都不支持时如实说不支持", conv3.get("kind") == "", str(conv3))
        got3 = asyncio.run(MC.mcp_task_get(u3, "x", conv3))
        check("③ b 返回 state=unsupported（不编状态）",
              got3.get("state") == "unsupported" and got3.get("ok") is False,
              json.dumps(got3, ensure_ascii=False)[:200])

        # ⑥ 端点不在 → 抛错
        dead = "http://127.0.0.1:%d/mcp" % _free_port()
        try:
            asyncio.run(MC.mcp_task_get(dead, "x"))
            check("⑥ 端点不在时要抛错（不能假装成功）", False, "竟然没抛错")
        except Exception as e:  # noqa: BLE001
            check("⑥ 端点不在时要抛错（不能假装成功）", True, type(e).__name__)
        # ── V128（2026-11-04）：异步结果**自动回收**五格（真起假 Server + 真队列文件 + 真账本）
        import importlib
        import os
        import shutil
        import tempfile

        from agent_community.platform import mcp_result_poll as MR
        from agent_community.platform import task_ledger as TL

        tmpdir = tempfile.mkdtemp(prefix="ac_mcp_task_")
        old_data = os.environ.get("AC_DATA_DIR")
        os.environ["AC_DATA_DIR"] = tmpdir

        MR.reset_for_tests()
        # ⑩ 登记 → 自动查到终态 → 出队
        MR.maybe_track(harness_id="h-demo", url=u1, tool="do_thing",
                       call={"async": True, "handle": "t1"})
        registered = len(MR.list_pending()) == 1
        stats1 = asyncio.run(MR.sweep_once())
        left = MR.list_pending()
        check("⑩ 登记后自动查到终态并出队",
              registered and stats1.get("done") == 1 and not left,
              "登记=%s 统计=%s 残留=%d" % (registered, stats1, len(left)))

        # ⑪ 结果有家（出站表里 state=done，文本与假 Server 给的逐字一致）
        outs = MR.list_outcalls(harness_id="h-demo")
        one = next((o for o in outs if str(o.get("handle")) == "t1"), {})
        check("⑪ 结果落进「出站调用结果表」且文本一致",
              one.get("state") == "done" and "标准 Tasks" in str(one.get("text") or ""),
              json.dumps(one, ensure_ascii=False)[:220])

        # ⑫ 挂得上任务 ⇒ 真写账本（external.source == "mcp"，推进到 delivered）
        TL.issue("ws-demo", 7, slot_dir="_lead", file="任务/t7.md", title="演示任务")
        MR.maybe_track(harness_id="h-demo", url=u1, tool="do_thing",
                       call={"async": True, "handle": "t2"},
                       workshop_id="ws-demo", task_seq=7)
        stats2 = asyncio.run(MR.sweep_once())
        rec = TL.get("ws-demo", 7) or {}
        ext = rec.get("external") or {}
        check("⑫ 有账本条目时真写账本（source=mcp / delivered）",
              ext.get("source") == "mcp" and rec.get("state") == "delivered",
              "统计=%s 账本=%s" % (stats2, json.dumps(rec, ensure_ascii=False)[:240]))

        # ⑬ 对端不支持异步 ⇒ 出队 + 写明 why，且**不再重试**
        MR.reset_for_tests()
        MR.maybe_track(harness_id="h-none", url=u3, tool="whatever",
                       call={"async": True, "handle": "hx"})
        asyncio.run(MR.sweep_once())
        outs3 = MR.list_outcalls(harness_id="h-none")
        u = next((o for o in outs3 if str(o.get("handle")) == "hx"), {})
        again = asyncio.run(MR.sweep_once())
        check("⑬ 不支持就写明原因并出队（不装还在跑）",
              u.get("state") == "unsupported" and (u.get("why") or "").strip()
              and again.get("checked") == 0 and not MR.list_pending(),
              "出站=%s 再查=%s" % (json.dumps(u, ensure_ascii=False)[:200], again))

        # ⑭ 队列在盘上（模块重载后仍读得到 ⇒ 不是只在内存里）
        MR.reset_for_tests()
        MR.maybe_track(harness_id="h-demo", url=u1, tool="do_thing",
                       call={"async": True, "handle": "t9"})
        importlib.reload(MR)
        survived = any(str(x.get("handle")) == "t9" for x in MR.list_pending())
        check("⑭ 队列落盘（模块重载后还在）", survived, "重载后 pending=%s" % MR.list_pending())

        if old_data is None:
            os.environ.pop("AC_DATA_DIR", None)
        else:
            os.environ["AC_DATA_DIR"] = old_data
        shutil.rmtree(tmpdir, ignore_errors=True)

    finally:
        for s in servers:
            try:
                s.shutdown()
            except Exception:  # noqa: BLE001
                pass

    print("-" * 74)
    passed = sum(1 for _l, ok, _d in ROWS if ok)
    print("合计 %d 格：PASS %d / FAIL %d" % (len(ROWS), passed, len(ROWS) - passed))
    for l, ok, d in ROWS:
        if not ok:
            print("  FAIL:", l, "|", d[:200])
    return 0 if passed == len(ROWS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
