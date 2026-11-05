#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""判据：**成员活动视图**（V131 §4）—— 「MCP 协议成员的工作痕迹和会话」能不能被用户看见。

老板 2026-11-04：「我要看到 MCP 协议成员有用户可以直观看见的工作痕迹和会话」。

真跑：起临时实例（换端口 + 换数据目录）+ 起**两个假 MCP 对端**（一个会读会话、一个不会），
把"能不能区分"逐格钉住。**不用真实数据、不污染真实目录。**

| # | 格 | 断言 |
|---|---|---|
| ① | 端点活着 | `GET /api/harness/{id}/activity` 回 200 且 `ok=true` |
| ② | 不存在的接入 | 404 + `code=NO_SUCH_HARNESS` + `next` 指路（不假装空数据） |
| ③ | 它自己报的进度 | 让它 POST 一条 progress ⇒ 出现在 `work_trace`，`source=self_reported`、`judge=unverified` |
| ④ | 平台自己发起的调用 | 平台调它一次工具并登记 ⇒ 出现 `source=platform_observed` |
| ⑤ | **能读会话** | 假对端有 `session_messages` ⇒ `session.capable=true` 且解析出 `role/text` |
| ⑥ | **读不了会话要说清** | 假对端没有读会话工具 ⇒ `capable=false` + `why` 非空 + `note` 给引导，**items 必须空**（不许编） |
| ⑦ | 只读 | 调前后 `workshops.json` / `task_ledger.json` 的 sha256 不变 |
| ⑧ | 三件事分得开 | 每条痕迹都有 `source` 与 `judge`；返回里带 `how_to_read` 说明"在干活/干完了/干得对"不是一回事 |

退出码：0＝全过；1＝有格没过；2＝起不来（如实说没跑成）。
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))
PY = sys.executable

ROWS: list = []


def check(label: str, ok: bool, detail: str = "") -> None:
    ROWS.append((label, bool(ok), detail))
    print("  %s %s%s" % ("PASS" if ok else "FAIL", label,
                        ("　（" + detail + "）") if detail and not ok else ""))


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def _http(base: str, path: str, body: dict | None = None, timeout: float = 30):
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    h = {"Content-Type": "application/json"} if data else {}
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


class _Fake(BaseHTTPRequestHandler):
    """假 MCP 对端：WITH_SESSION=True 时提供 session_messages。"""

    WITH_SESSION = True
    #: 记录"对方被读了几次会话"（验短缓存真的不打对方）
    CALLS = {"session_messages": 0}

    def log_message(self, *a):                                 # noqa: D401 — 静音
        pass

    def _send(self, obj, headers=None):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):                                         # noqa: N802
        n = int(self.headers.get("Content-Length") or 0)
        try:
            req = json.loads(self.rfile.read(n).decode() or "{}")
        except Exception:                                      # noqa: BLE001
            req = {}
        mid, method = req.get("id"), str(req.get("method") or "")
        sid = self.headers.get("Mcp-Session-Id") or "sess-fake"

        def ok(result):
            self._send({"jsonrpc": "2.0", "id": mid, "result": result},
                       headers={"Mcp-Session-Id": sid})

        if method == "initialize":
            ok({"protocolVersion": "2025-06-18", "capabilities": {},
                "serverInfo": {"name": "fake-member", "version": "0"}})
            return
        if method.startswith("notifications/"):
            self.send_response(200)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if method == "tools/list":
            tools = [{"name": "session_post", "description": "在自己会话里说话",
                      "inputSchema": {"type": "object",
                                      "properties": {"text": {"type": "string"}}}}]
            if self.WITH_SESSION:
                tools.append({"name": "session_messages", "description": "读会话",
                              "inputSchema": {"type": "object",
                                              "properties": {"limit": {"type": "integer"}}}})
            ok({"tools": tools})
            return
        if method == "tools/call":
            name = str((req.get("params") or {}).get("name") or "")
            if name == "session_messages":
                _Fake.CALLS["session_messages"] += 1
                msgs = [
                    {"role": "user", "text": "帮我核对一下 3 号工位的产物", "at": "2026-11-04T17:00:00"},
                    {"role": "agent", "text": "我改了 a.py，产物在 产物/a.py", "at": "2026-11-04T17:02:11"},
                ]
                ok({"content": [{"type": "text",
                                 "text": json.dumps({"messages": msgs}, ensure_ascii=False)}]})
                return
            ok({"content": [{"type": "text", "text": "ok（已写进我的会话）"}]})
            return
        self._send({"jsonrpc": "2.0", "id": mid,
                    "error": {"code": -32601, "message": "no method %s" % method}})


def _serve_fake(with_session: bool) -> tuple:
    port = _free_port()
    cls = type("H_%s" % ("sess" if with_session else "nosess"), (_Fake,),
               {"WITH_SESSION": with_session})
    srv = ThreadingHTTPServer(("127.0.0.1", port), cls)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, "http://127.0.0.1:%d/mcp" % port


def _sha(p: Path) -> str:
    try:
        return hashlib.sha256(p.read_bytes()).hexdigest()
    except FileNotFoundError:
        return ""


def main() -> int:
    port = _free_port()
    tmpdir = tempfile.mkdtemp(prefix="ac_activity_")
    base = "http://127.0.0.1:%d" % port
    env = dict(os.environ)
    env.update({"AC_PORT": str(port), "AC_DATA_DIR": tmpdir, "PYTHONIOENCODING": "utf-8"})
    srv = subprocess.Popen([PY, "-m", "agent_community.platform.server"], cwd=str(ROOT), env=env,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    fakes = []
    print("判据：成员活动视图（真跑：临时实例 + 假 MCP 对端）")
    print("=" * 74)
    try:
        up = False
        for _ in range(80):
            try:
                st, _j = _http(base, "/api/status", timeout=3)
                up = st == 200
            except (urllib.error.URLError, OSError):
                up = False
            if up:
                break
            time.sleep(0.5)
        if not up:
            print("[FAIL] 平台没起来 —— 这次不算跑成")
            return 2

        # 两个假对端：会读会话 / 不会读会话
        f1, u1 = _serve_fake(True)
        f2, u2 = _serve_fake(False)
        fakes += [f1, f2]

        for hid, url, nm in (("demo-ok", u1, "示例Harness-会读会话"),
                             ("demo-mute", u2, "示例Harness-不会读会话")):
            _http(base, "/api/harness/register", {
                "harness_id": hid, "harness_name": nm, "harness_type": "custom",
                "wakeup_method": "mcp", "api_base_url": url, "ai": {"model_name": "示例模型"}})

        # ③ 它自己报一条进度
        _http(base, "/api/progress", {"text": "我把 a.py 改完了", "harness_id": "demo-ok",
                                      "kind": "step"})

        # ④ 平台发起一次调用并登记（模拟 mcp_result_poll 的登记路径）
        code4 = 0
        try:
            from agent_community.platform import mcp_result_poll as MR

            os.environ["AC_DATA_DIR"] = tmpdir
            MR.maybe_track(harness_id="demo-ok", url=u1, tool="session_post",
                           call={"async": True, "handle": "h-demo"})
        except Exception as e:                                 # noqa: BLE001
            code4 = 1
            print("  （登记模拟失败：%s）" % e)

        ws = Path(tmpdir) / "workshops.json"
        tl = Path(tmpdir) / "task_ledger.json"
        before = (_sha(ws), _sha(tl))

        st, body = _http(base, "/api/harness/demo-ok/activity")
        check("① 端点活着且 ok", st == 200 and body.get("ok") is True,
              "HTTP %s %s" % (st, str(body)[:160]))

        st2, b2 = _http(base, "/api/harness/no-such-member/activity")
        check("② 不存在的接入 ⇒ 404 + 指路",
              st2 == 404 and b2.get("code") == "NO_SUCH_HARNESS" and b2.get("next"),
              "HTTP %s %s" % (st2, str(b2)[:160]))

        rows = body.get("work_trace") or []
        prog = [r for r in rows if r.get("kind") == "progress"]
        check("③ 它自己报的进度出现在时间线（self_reported / unverified）",
              bool(prog) and prog[0].get("source") == "self_reported"
              and prog[0].get("judge") == "unverified",
              json.dumps(prog[:1], ensure_ascii=False)[:200])

        outb = [r for r in rows if r.get("kind") == "outbound_call"]
        check("④ 平台自己发起的调用出现（platform_observed）",
              code4 == 0 and bool(outb) and outb[0].get("source") == "platform_observed",
              json.dumps(outb[:1], ensure_ascii=False)[:200])

        sess = body.get("session") or {}
        items = sess.get("items") or []
        check("⑤ 有读会话工具 ⇒ 真读到消息（role/text 都在）",
              sess.get("capable") is True and bool(items)
              and all(x.get("role") and x.get("text") for x in items),
              json.dumps(sess, ensure_ascii=False)[:240])

        st3, b3 = _http(base, "/api/harness/demo-mute/activity")
        s3 = (b3.get("session") or {})
        check("⑥ 没有读会话工具 ⇒ 如实说为什么（items 必须空，不编）",
              st3 == 200 and s3.get("capable") is False
              and bool((s3.get("why") or "").strip()) and not (s3.get("items") or []),
              json.dumps(s3, ensure_ascii=False)[:240])

        after = (_sha(ws), _sha(tl))
        check("⑦ 只读（调前后状态文件 sha256 不变）", before == after,
              "before=%s after=%s" % (before[0][:10], after[0][:10]))

        all_have = all((r.get("source") and r.get("judge")) for r in rows) if rows else False
        check("⑧ 每条痕迹都有 source/judge，且带「三件事别混」的说明",
              all_have and bool(body.get("how_to_read")),
              "rows=%d how_to_read=%r" % (len(rows), str(body.get("how_to_read"))[:60]))

        # ── ⑨~⑫：监控室的「会话观察窗」（老板 2026-11-04：「在监控室里开拓一个专门给
        #   MCP 这类无法在原生 harness 里体现会话的 agent 的会话观察窗」）──
        st_m, bm = _http(base, "/api/monitor/agents")
        rows_m = {r.get("harness_id"): r for r in (bm.get("agents") or [])}
        ok_row = rows_m.get("demo-ok") or {}
        mute_row = rows_m.get("demo-mute") or {}
        check("⑨ 没窗口但有读会话工具 ⇒ 监控室给 watch_kind=session",
              st_m == 200 and ok_row.get("watch_kind") == "session"
              and ok_row.get("session_watchable") is True,
              json.dumps(ok_row, ensure_ascii=False)[:120]
              + " ｜watch_kind=%r sw=%r why=%r" % (ok_row.get("watch_kind"),
                                                   ok_row.get("session_watchable"),
                                                   str(ok_row.get("session_why"))[:70]))
        check("⑩ 两者都没有 ⇒ watch_kind=none 且说清缺什么",
              mute_row.get("watch_kind") == "none"
              and bool((mute_row.get("session_why") or "").strip()),
              json.dumps(mute_row, ensure_ascii=False)[:220])

        st_s, bs = _http(base, "/api/monitor/demo-ok/session?limit=10")
        check("⑪ 会话观察窗真读到消息（不是空表）",
              st_s == 200 and bs.get("capable") is True and bool(bs.get("items")),
              "HTTP %s %s" % (st_s, str(bs)[:200]))
        after1 = _Fake.CALLS["session_messages"]
        _st2, bs2 = _http(base, "/api/monitor/demo-ok/session?limit=10")
        after2 = _Fake.CALLS["session_messages"]
        check("⑫ 5 秒内第二次走缓存（不打对方）",
              bs2.get("cached") is True and after2 == after1,
              "cached=%r 对方被读次数 %d→%d" % (bs2.get("cached"), after1, after2))
    finally:
        for f in fakes:
            try:
                f.shutdown()
            except Exception:                                  # noqa: BLE001
                pass
        try:
            srv.terminate()
            srv.wait(timeout=10)
        except Exception:                                      # noqa: BLE001
            srv.kill()
        shutil.rmtree(tmpdir, ignore_errors=True)

    print("-" * 74)
    passed = sum(1 for _l, ok, _d in ROWS if ok)
    print("合计 %d 格：PASS %d / FAIL %d" % (len(ROWS), passed, len(ROWS) - passed))
    for lb, ok, d in ROWS:
        if not ok:
            print("  FAIL:", lb, "|", str(d)[:200])
    return 0 if passed == len(ROWS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
