#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""判据：**高级协议真的能让"外部对端把活干完"**（A2A / gRPC；ACP 已有 `acp_core_check.py`）。

## 为什么要有它（老板 2026-10-25：「MCP 之外的高级协议同样收口」）

`protocol_call_check` 只证明"接口可达"（能连、能列协议）。**落地**的意思是：
一个真的外部对端按协议把活干完，而且**平台侧查得到**——

```
A2A ：Agent Card → tasks/send（派活）→ 平台真建出任务 → tasks/get 同一条 → tasks/cancel 有明确结果
gRPC：ListProtocols → RegisterHarness（拿 token）→ SubmitTask（派活）→ GetTask 同一条 → ReportResult 被接受
```

跑在**临时实例**上（换端口 + 换数据目录），跑完关掉删目录 ⇒ 不污染真实数据。

退出码：0＝全过（或用 SKIP 如实说明）；1＝有格没过；2＝起不来。
"""
from __future__ import annotations

import argparse
import io
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


def _http(base: str, path: str, body: dict | None = None, method: str = "",
          timeout: float = 30, token: str = ""):
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    headers = {"Content-Type": "application/json"} if data else {}
    if token:
        headers["X-Agent-Token"] = token      # 平台 P1 身份凭证（harness 回报要它）
    req = urllib.request.Request(base + path, data=data, headers=headers,
                                 method=method or ("POST" if data is not None else "GET"))
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8", "replace")
            return int(r.status), (json.loads(raw) if raw.strip() else {})
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            return int(e.code), (json.loads(raw) if raw.strip() else {})
        except ValueError:
            return int(e.code), {"raw": raw[:300]}


def _rpc(url: str, method: str, params: dict, rid: int = 1, timeout: float = 60,
         token: str = ""):
    """JSON-RPC over HTTP；`token` 非空时带 `Authorization: Bearer`（A2A 入站要它）。"""
    data = json.dumps({"jsonrpc": "2.0", "id": rid, "method": method,
                       "params": params}).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8", "replace")
            return int(r.status), (json.loads(raw) if raw.strip() else {})
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            return int(e.code), (json.loads(raw) if raw.strip() else {})
        except ValueError:
            return int(e.code), {"raw": raw[:300]}


def _rpc_auth(url: str, method: str, params: dict, token: str, rid: int = 2,
              timeout: float = 60):
    return _rpc(url, method, params, rid=rid, timeout=timeout, token=token)


def _wait_up(base: str, path: str = "/api/status", tries: int = 60) -> bool:
    for _ in range(tries):
        try:
            st, _d = _http(base, path, timeout=3)
            if st == 200:
                return True
        except (urllib.error.URLError, OSError):   # noqa: BLE001 — 有意静默：还没起来就继续等（超时后如实报）
            pass
        time.sleep(0.5)
    return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep", action="store_true", help="跑完不关临时实例/对端")
    a = ap.parse_args()

    port = _free_port()
    tmpdir = tempfile.mkdtemp(prefix="ac_proto_peer_")
    base = "http://127.0.0.1:%d" % port
    env = dict(os.environ)
    env.update({"AC_PORT": str(port), "AC_DATA_DIR": tmpdir, "AC4_BASE_URL": base,
                "PYTHONIOENCODING": "utf-8"})
    procs: list = []
    rows, fails, skips = [], [], []

    def check(name: str, ok: bool, detail: str = "") -> None:
        rows.append((name, bool(ok), detail))
        if not ok:
            fails.append("%s（%s）" % (name, detail))

    def skip(name: str, why: str) -> None:
        rows.append((name, None, why))
        skips.append("%s（%s）" % (name, why))

    try:
        srv = subprocess.Popen([PY, "-m", "agent_community.platform.server"], cwd=_ROOT, env=env,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        procs.append(srv)
        if not _wait_up(base):
            print("[FAIL] 临时平台起不来（%s）—— 这次不算跑成" % base)
            return 2
        check("临时平台起来（换端口+换数据目录）", True)

        # ★A2A / gRPC 的入站**都要求 Bearer agent_token**（fail-closed）⇒
        #   真实流程是：先在平台上登记 harness 拿到令牌，再拿它按协议干活。
        st0, reg = _http(base, "/api/harness/register", {
            "harness_id": "demo-peer-token", "harness_name": "示例Harness-对端令牌",
            # ⚠️ wakeup_method 是**枚举**：「2a 不在里面（合法值：acp/file_poll/http_api/http/
            #    clipboard/mcp）。写错会让注册 400、拿不到令牌，后面全红（2026-10-25 真踩）。
            "harness_type": "custom", "wakeup_method": "http_api",
            "ai": {"model_name": "示例模型"}})
        PEER_TOKEN = str((reg or {}).get("agent_token") or "")
        check("夹具：登记 harness 拿到 agent_token", st0 in (200, 201) and bool(PEER_TOKEN),
              "HTTP %s token=%s" % (st0, "有" if PEER_TOKEN else "空"))

        # ── A2A：真对端派活 ────────────────────────────────────────────
        a2a_port = _free_port()
        a2a_url = "http://127.0.0.1:%d" % a2a_port
        _a2a_err = os.path.join(tmpdir, "a2a.err.log")
        p_a2a = subprocess.Popen([PY, "-m", "agent_community.a2a_server",
                                  "--port", str(a2a_port), "--host", "127.0.0.1"],
                                 cwd=_ROOT, env=env, stdout=subprocess.DEVNULL,
                                 stderr=open(_a2a_err, "w", encoding="utf-8"))
        procs.append(p_a2a)
        if not _wait_up(a2a_url, "/.well-known/agent-card.json"):
            _why = ""
            try:
                _why = io.open(_a2a_err, encoding="utf-8", errors="replace").read()[-300:]
            except OSError:                                              # noqa: BLE001 — 读不到就算了
                _why = "（没留下 stderr）"
            skip("A2A 整段", "a2a_server 起不来（端口 %d）：%s" % (a2a_port, _why.replace("\n", " ")))
            a2a_ok = False
        else:
            a2a_ok = True
            st, card = _http(a2a_url, "/.well-known/agent-card.json")
            check("A2A ① Agent Card 能发现", st == 200 and bool(card.get("name")),
                  "HTTP %s %s" % (st, json.dumps(card, ensure_ascii=False)[:160]))
            title = "A2A 对端派活：请把这件事记进平台"
            # 不带令牌 ⇒ 必须被拒（fail-closed），并说清原因
            st_na, denied = _rpc(a2a_url, "tasks/send",
                                 {"message": {"parts": [{"type": "text", "text": "无令牌测试"}]}})
            check("A2A ⓪ 不带令牌必须被拒（fail-closed）",
                  st_na in (401, 403) or bool((denied or {}).get("error")),
                  "HTTP %s %s" % (st_na, json.dumps(denied, ensure_ascii=False)[:160]))
            st, sent = _rpc_auth(a2a_url, "tasks/send",
                                 {"message": {"parts": [{"type": "text", "text": title}]}},
                                 PEER_TOKEN)
            res = (sent or {}).get("result") or {}
            a2a_tid = str(res.get("id") or (res.get("task") or {}).get("id") or "")
            check("A2A ② tasks/send 回了一个任务号", bool(a2a_tid),
                  json.dumps(sent, ensure_ascii=False)[:220])
            # 平台侧对账：真建出任务/工作间（别只听 A2A 自己说）
            st2, tasks = _http(base, "/api/tasks")
            blob = json.dumps(tasks, ensure_ascii=False)
            check("A2A ③ 平台侧真建出了这条任务", bool(a2a_tid) and a2a_tid in blob,
                  "任务数 %s" % len(tasks.get("tasks") or []))
            st3, got = _rpc_auth(a2a_url, "tasks/get", {"taskId": a2a_tid}, PEER_TOKEN, rid=3)
            check("A2A ④ tasks/get 查得到同一条",
                  str((got or {}).get("result", {}).get("id") or "") == a2a_tid,
                  json.dumps(got, ensure_ascii=False)[:200])
            st4, canceled = _rpc_auth(a2a_url, "tasks/cancel", {"taskId": a2a_tid},
                                      PEER_TOKEN, rid=4)
            check("A2A ⑤ tasks/cancel 有明确结果（不留空）",
                  bool((canceled or {}).get("result")) or bool((canceled or {}).get("error")),
                  json.dumps(canceled, ensure_ascii=False)[:200])

            # ── V119 批 B：A2A 从"半落地"到"落地"（⑥⑦⑧⑨）──────────────
            caps = (card.get("capabilities") or {})
            lp = caps.get("statusLongPoll") or {}
            check("A2A ⑥ 卡上如实声明边界：不流式、但可长轮询",
                  caps.get("streaming") is False and lp.get("supported") is True
                  and lp.get("method") == "tasks/watch" and "长轮询" in str(lp.get("note")),
                  json.dumps(caps, ensure_ascii=False)[:220])

            # ⑦ 长轮询：没有变化时必须**如实**返回 changed=false（不假装有更新）
            st7, got7 = _rpc_auth(a2a_url, "tasks/watch",
                                  {"taskId": a2a_tid, "waitSeconds": 1}, PEER_TOKEN, rid=7)
            res7 = (got7 or {}).get("result") or {}
            meta7 = res7.get("metadata") or {}
            check("A2A ⑦ tasks/watch 超时如实返回 changed=false（长轮询，不是流式）",
                  meta7.get("changed") is False and "长轮询" in str(meta7.get("wait_note")),
                  json.dumps(got7, ensure_ascii=False)[:220])

            # ⑧ 未声明能力不许调（fail-closed，并告诉它能用哪些）
            st8, got8 = _rpc_auth(a2a_url, "tasks/send",
                                  {"skillId": "绝不存在的能力-zzz",
                                   "message": {"parts": [{"text": "试着调一个没声明的能力"}]}},
                                  PEER_TOKEN, rid=8)
            err8 = (got8 or {}).get("error") or {}
            check("A2A ⑧ 未声明的能力必须被拒（fail-closed 且列出可用项）",
                  int(err8.get("code") or 0) == -32004
                  and "未声明能力" in str(err8.get("message"))
                  and "platform:" in str(err8.get("message")),
                  json.dumps(got8, ensure_ascii=False)[:240])

            # ⑨ 产物 + 验收**同源**：在临时实例上建一个 v5 工作间（带名单/产物/验收），
            #    再用 A2A 派进去，看 tasks/get 里 artifacts 与 metadata.review 是不是真的。
            wsdir = os.path.join(tmpdir, "ws_a2a_v5")
            os.makedirs(os.path.join(wsdir, "前端组", "员工01", "产物"), exist_ok=True)
            with io.open(os.path.join(wsdir, "前端组", "员工01", "产物", "接口说明.md"),
                         "w", encoding="utf-8") as f:
                f.write("# 接口说明\n\n- POST /x\n")
            stw, wbody = _http(base, "/api/workshop",
                               {"name": "A2A产物验收", "hall_content": "方向",
                                "workshop_path": wsdir})
            wid = str(wbody.get("workshop_id") or "")
            rcpt = ("#回执 v1\n类型: 名单\n工作间: %s\n成员: 组长\n凭据:\n序号: 31\n---\n"
                    "岗位: 前端组/员工01 | harness: demo-peer-token | model: 示例模型 | 人数: 1\n"
                    "---\n行数: 1" % wid)
            _http(base, "/api/workshop/%s/intake?source=C&confirm=1" % wid, {"text": rcpt})
            # 派活要过 G5（工位必须 active）—— 先记证据把它接上（三件都打勾 + 写覆盖范围）
            st_ev, ev = _http(base, "/api/workshop/%s/members/1/evidence" % wid,
                              {"tier": "c_visual", "paste_seen": True, "press_ok": True,
                               "rename_seen": True,
                               "verified_scope": "粘贴+回车+改名（对端判据自造）"})
            # 派活还要过 G0（合法 manifest）—— 心智根 + manifest，入口指向**真存在**的文件
            _sm, mind = _http(base, "/api/mind/A2A-01", {})
            _http(base, "/api/workshop/%s/manifest" % wid,
                  {"mind_path": str(mind.get("mind_path") or ""),
                   "entry_points": ["前端组/员工01/产物/接口说明.md"],
                   "task_focus": "A2A 落地判据"})
            _http(base, "/api/workshop/%s/verify-artifacts" % wid, {})
            # 建一条 v5 任务再验收（通过必须带 ≥L1 证据）
            stt, tbody = _http(base, "/api/workshop/%s/dispatch" % wid,
                               {"member_seq": 1, "title": "接口落地", "accept": "写清错误码"})
            seq = int((tbody or {}).get("task_seq") or 0)
            rev_st = None
            if seq:
                rev_st, _rev_body = _http(base, "/api/workshop/%s/tasks/%d/review" % (wid, seq),
                                          {"result": "通过", "evidence_level": "L1",
                                           "evidence_ref": "对端 AgentCard + 目录 diff 已人工核对",
                                           "note": "目录里真有产物"})
            else:
                _why9 = ("派活没建出任务：evidence=%s dispatch=%s"
                         % (json.dumps(ev, ensure_ascii=False)[:90],
                            json.dumps(tbody, ensure_ascii=False)[:160]))
                print("[judge] ⑨ 夹具提示：", _why9)
            st9, got9 = _rpc_auth(a2a_url, "tasks/send",
                                  {"workshopId": wid,
                                   "message": {"parts": [{"text": "派进已有工作间"}]}},
                                  PEER_TOKEN, rid=9)
            res9 = (got9 or {}).get("result") or {}
            arts9 = res9.get("artifacts") or []
            rev9 = (res9.get("metadata") or {}).get("review") or {}
            if seq:
                # 平台侧对账：账本里这条到底记成什么了（别只听 A2A 自己说）
                _stl, ledger9 = _http(base, "/api/workshop/%s/tasks" % wid)
                _t9 = next((t for t in (ledger9.get("tasks") or [])
                            if int(t.get("task_seq") or 0) == seq), {})
            else:
                _t9 = {}
            check("A2A ⑨ 产物（v5 目录 diff，带 sha256）与验收结论随任务一起出去",
                  bool(arts9) and any((a.get("metadata") or {}).get("sha256") for a in arts9)
                  and rev9.get("evidence_level") == "L1"
                  and res9.get("status") == "completed",
                  "artifacts=%d review=%s status=%s ｜ 账本=%s ｜ review_http=%s ｜ 派活=%s"
                  % (len(arts9), json.dumps(rev9, ensure_ascii=False)[:100], res9.get("status"),
                     json.dumps(_t9, ensure_ascii=False)[:140], rev_st,
                     json.dumps(tbody, ensure_ascii=False)[:120]))

        # ── gRPC：真对端派活 ─────────────────────────────────────────
        try:
            import grpc                                                    # noqa: F401
            from agent_community.proto import agent_community_pb2 as pb
            from agent_community.proto import agent_community_pb2_grpc as pbg
        except Exception as e:                                             # noqa: BLE001
            skip("gRPC 整段", "本机没装 grpcio / 桩（%s: %s）" % (type(e).__name__, e))
        else:
            gport = _free_port()
            p_grpc = subprocess.Popen([PY, "-m", "agent_community.grpc_gateway",
                                       "--port", str(gport)], cwd=_ROOT, env=env,
                                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            procs.append(p_grpc)
            time.sleep(3)
            try:
                ch = grpc.insecure_channel("127.0.0.1:%d" % gport)
                stub = pbg.AgentCommunityStub(ch)
                md = (("authorization", "Bearer " + PEER_TOKEN),)
                pr = stub.ListProtocols(pb.ProtocolsRequest(), timeout=20, metadata=md)
                check("gRPC ① ListProtocols 通", bool(getattr(pr, "payload_json", "")),
                      str(getattr(pr, "payload_json", ""))[:160])
                reg = stub.RegisterHarness(pb.RegisterRequest(payload_json=json.dumps({
                    "harness_id": "demo-grpc-peer", "harness_name": "示例Harness-gRPC对端",
                    "harness_type": "custom", "wakeup_method": "http_api",
                    "ai": {"model_name": "示例模型"}}, ensure_ascii=False)),
                    timeout=30, metadata=md)
                regd = {}
                try:
                    regd = json.loads(getattr(reg, "payload_json", "") or "{}")
                except ValueError:
                    regd = {}
                tok = str(regd.get("agent_token") or "")
                check("gRPC ② RegisterHarness 回执带令牌", bool(tok),
                      json.dumps(regd, ensure_ascii=False)[:200])
                title = "gRPC 对端派活：请把这件事记进平台"
                ack = stub.SubmitTask(pb.SubmitTaskRequest(
                    task_id="", payload_json=json.dumps({"name": title[:20],
                                                         "hall_content": title},
                                                        ensure_ascii=False),
                    agent_token=tok), timeout=30, metadata=md)
                g_tid = str(getattr(ack, "task_id", "") or "")
                check("gRPC ③ SubmitTask 回的 task_id 非空", bool(g_tid),
                      "ok=%s err=%s" % (getattr(ack, "ok", None), getattr(ack, "error", "")))
                st, tasks = _http(base, "/api/tasks")
                check("gRPC ④ 平台侧真建出了这条任务",
                      bool(g_tid) and g_tid in json.dumps(tasks, ensure_ascii=False),
                      "任务数 %s" % len(tasks.get("tasks") or []))
                gs = stub.GetTask(pb.GetTaskRequest(task_id=g_tid), timeout=20, metadata=md)
                check("gRPC ⑤ GetTask 查得到同一条",
                      str(getattr(gs, "task_id", "") or "") == g_tid,
                      "status=%r" % getattr(gs, "status", ""))
                rr = stub.ReportResult(pb.TaskResult(
                    payload_json=json.dumps({"harness_id": "demo-grpc-peer", "task_id": g_tid,
                                             "ok": True, "content": "gRPC 对端：活干完了"},
                                            ensure_ascii=False),
                    agent_token=tok), timeout=30, metadata=md)
                check("gRPC ⑥ ReportResult 被平台接受",
                      bool(getattr(rr, "ok", False)), str(getattr(rr, "error", ""))[:160])
            except Exception as e:                                         # noqa: BLE001
                check("gRPC 整段跑通", False, "%s: %s" % (type(e).__name__, str(e)[:200]))
        # ── ACP：平台真拉起一个 agent 进程，走完一轮对话 ──────────────
        try:
            from agent_community.platform.acp_harness_bridge import AcpHarnessBridge   # noqa: F401
        except Exception:                                                  # noqa: BLE001
            try:
                from agent_community.platform.harness_adapter import AcpHarnessBridge  # noqa: F401
            except Exception as e2:                                        # noqa: BLE001
                AcpHarnessBridge = None
                skip("ACP 整段", "拿不到平台的 ACP 客户端（%s: %s）" % (type(e2).__name__, e2))
        if AcpHarnessBridge is not None:
            fake = os.path.join(_ROOT, "agent_community", "tests", "_fake_acp_agent.py")
            mark = os.path.join(tmpdir, "acp_mark.txt")
            if not os.path.isfile(fake):
                skip("ACP 整段", "仓库里没有对端假 agent（tests/_fake_acp_agent.py）")
            else:
                br = None
                try:
                    # 与 tools/acp_core_check.py **完全同一套驱动**（命令用正斜杠、mark 走环境变量）
                    import shlex as _shlex

                    os.environ["AC_FAKE_ACP_MARK"] = mark
                    _py_slash = PY.replace("\\", "/")
                    _fake_slash = fake.replace("\\", "/")
                    _cmd = _shlex.split('"%s" "%s"' % (_py_slash, _fake_slash))
                    br = AcpHarnessBridge(cmd=_cmd, repo=tmpdir)
                    br.start()
                    init = br.initialize(timeout=30)
                    check("ACP ① 进程真被这条命令拉起来（initialize 拿到 result）",
                          bool((init or {}).get("result")), str(init)[:160])
                    sess = br.new_session(tmpdir, timeout=30)
                    check("ACP ② session/new 回了一个会话", sess is not None, str(sess)[:120])
                    _stop, said = br.prompt(getattr(sess, "session_id", ""), "请回复：收到", timeout=30)
                    text = str(said or "")
                    check("ACP ③ prompt 真回了话（含「收到」）", "收到" in text, text[:200])
                    mk = ""
                    try:
                        mk = io.open(mark, encoding="utf-8", errors="replace").read()
                    except OSError:                                        # noqa: BLE001 — 没有 mark 就当没证据
                        mk = ""
                    check("ACP ④ 对端自己留的 mark 里有拉起痕迹", "started" in mk.lower(),
                          mk[:160])
                except Exception as e:                                     # noqa: BLE001
                    check("ACP 整段跑通", False, "%s: %s" % (type(e).__name__, str(e)[:200]))
                finally:
                    os.environ.pop("AC_FAKE_ACP_MARK", None)
                    try:
                        if br is not None:
                            br.stop()
                    except Exception:      # noqa: BLE001 — 有意静默：收尾关不掉不影响结论
                        pass

        # ── HTTP（harness 端点）：外部对端拿真令牌走消息与回执 ─────────
        try:
            hid = "demo-http-peer"
            st, regd = _http(base, "/api/harness/register", {
                "harness_id": hid, "harness_name": "示例Harness-HTTP对端", "harness_type": "custom",
                "wakeup_method": "http_api", "api_base_url": base + "/noop",
                "ai": {"model_name": "示例模型"}})
            tok2 = str((regd or {}).get("agent_token") or "")
            check("HTTP ① 注册拿令牌", st in (200, 201) and bool(tok2), "HTTP %s" % st)
            st, ack = _http(base, "/api/harness/message",
                            {"harness_id": hid, "text": "HTTP 对端：我来了"}, token=tok2)
            check("HTTP ② /api/harness/message 被接受（带令牌）", not ack.get("error"),
                  "HTTP %s %s" % (st, json.dumps(ack, ensure_ascii=False)[:160]))
            # 建一条真任务，再把回执打回去（证明"平台真的收下了这条回执"）
            st, made = _http(base, "/api/workshop", {"name": "HTTP 对端派活",
                                                     "hall_content": "HTTP 对端派活",
                                                     "create_task": True})
            tid = str((made or {}).get("task_id") or "")
            st, res = _http(base, "/api/harness/task-result",
                            {"harness_id": hid, "task_id": tid, "ok": True,
                             "result": "HTTP 对端：活干完了"}, token=tok2)
            check("HTTP ③ 回执被接受且说清结果",
                  (not res.get("error")) and bool(res.get("success"))
                  and ("task_state_changed" in res or res.get("note")),
                  json.dumps(res, ensure_ascii=False)[:200])
            st, got = _http(base, "/api/task/%s" % tid)
            check("HTTP ④ 平台侧查得到这条任务", bool(tid) and st == 200,
                  "HTTP %s tid=%s" % (st, tid))
        except Exception as e:                                             # noqa: BLE001
            check("HTTP 对端整段跑通", False, "%s: %s" % (type(e).__name__, str(e)[:200]))

        # ── WS：真连 /ws，建间后真收到 task_created 事件 ────────────────
        try:
            import websockets.sync.client as _wsc
        except Exception as e:                                             # noqa: BLE001
            skip("WS 整段", "本机没有 websockets 客户端库（%s: %s）" % (type(e).__name__, e))
        else:
            try:
                ictx = _wsc.connect("ws://127.0.0.1:%d/ws" % port, open_timeout=10,
                                    close_timeout=5)
                with ictx as _ws:
                    # 建一个工作间（会广播 task_created），然后等事件
                    # ⚠️ **只有「大厅发布」(/api/command) 会广播 	ask_created**；
                    #   /api/workshop 建间**不广播**（2026-10-25 实测：只连 WS 等它等不到）。
                    _http(base, "/api/command", {"input": "WS 对端事件：请广播我"})
                    got = ""
                    for _ in range(6):
                        try:
                            got = _ws.recv(timeout=5)
                        except Exception:                                  # noqa: BLE001 — 超时就算没收到
                            got = ""
                        if "task_created" in str(got):
                            break
                    # ★2026-10-25 这条一开始是红的：外部客户端一帧都收不到（进程内 TestClient 能收到）。
                    #   根因＝-m 跑起来时有两份 ws_clients（__main__ 与包内模块各一份），
                    #   /ws 加进前者、而 bcast_to_clients 遍历后者（永远空）。已由 server.ws_registry()
                    #   收成一处真相（V106 §3 ④）。
                    check("WS ① 真连上 /ws 并收到 task_created 事件",
                          "task_created" in str(got), str(got)[:200])
            except Exception as e:                                         # noqa: BLE001
                skip("WS 整段", "外部 WS 客户端跑不起来：%s: %s"
                     % (type(e).__name__, (str(e) or repr(e))[:160]))

    finally:
        if not a.keep:
            for p in procs:
                try:
                    p.terminate()
                    p.wait(timeout=10)
                except Exception:                                          # noqa: BLE001 — 收尾失败不影响结论
                    try:
                        p.kill()
                    except Exception:      # noqa: BLE001 — 有意静默：收尾杀不掉不影响已经得出的结论
                        pass
            shutil.rmtree(tmpdir, ignore_errors=True)

    print("=" * 78)
    print("判据：高级协议真对端干活（A2A / gRPC / ACP / HTTP / WS；临时实例 %s）" % base)
    print("=" * 78)
    for name, ok, detail in rows:
        tag = "SKIP" if ok is None else ("PASS" if ok else "FAIL")
        print("  %s %s%s" % (tag, name, ("　（%s）" % detail) if ok is not True else ""))
    print("-" * 78)
    _pass = sum(1 for _n, _o, _d in rows if _o is True)
    print("合计 %d 格：PASS %d / FAIL %d / SKIP %d" % (len(rows), _pass, len(fails), len(skips)))
    return 0 if not fails else 1


if __name__ == "__main__":
    raise SystemExit(main())
