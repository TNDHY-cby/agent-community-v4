#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""判据：**痕迹可见性方案的证伪实验台**（V131 §9）—— 三个 mock 节点真跑，三件事能不能分开。

老板 2026-11-06 拍板：「先做 3 个  mock 节点 + 判据工具（你来做），5 人盲判我出人」。

本工具就是那三个节点与那张对照表。它**不替人做判断**，只把"数据层能不能分开"钉死：

| 节点 | 行为 | 期望用户看到 |
|---|---|---|
| **A 正常回传** | 真报 3 条 `progress` + 真交产物（`sha256` 可重算） + 本侧真复核 | 在干活 / 已完成 / **已复核** |
| **B 完全沉默** | 注册了、接了活，**什么都不报** | **没有它的过程记录** / 没回传 / 无可核之物 |
| **C 假装在干活** | 报 3 条 `progress` 说"完成了"，**没有产物、没有凭据** | 有过程记录 / **仅自报** / **未复核** |

★硬阈值（§9）：若 B（没回传）与 C（假装）在**数据层**都只能读成同一件事 ⇒ 方案不成立。
本工具最后一格就是钉这条：两者的 `(有无过程记录, 有无交付, 采信态)` 必须互不相同。

真跑：临时实例（换端口 + 换 `AC_DATA_DIR`）+ 三个假 MCP 对端；**不用真实数据、不污染真实目录**。

| # | 格 | 断言 |
|---|---|---|
| ① | 夹具 | 工作间 + 3 条任务真建出来（账本里数得到） |
| ② | A 在干活 | activity `counts.progress=3`，行上 `source=self_reported` / `judge=unverified` |
| ③ | B 没回传 | activity `counts.progress=0` 且没有 progress 行（**不是空表装成功**） |
| ④ | 光看过程分不开 B/C | C 也是 `counts.progress=3` —— 如实记录这个事实，别假装进度能区分 |
| ⑤ | A 干完了 | 验收通过 ⇒ 任务 `state=accepted` |
| ⑥ | B 没干完 | 验收被拒（400 + code + next），任务 `state≠accepted` |
| ⑦ | C 说完成不算完成 | 验收被拒 `PEER_NOT_REVIEWED` + `peer_accepted=accepted_self` |
| ⑧ | A 干得对 | 本侧重算一致 ⇒ `accepted_peer` + `peer_checked_at` |
| ⑨ | C 干得对不对不明 | 停在 `accepted_self`，`peer_diff` 说清"没有可核的东西" |
| ⑩ | B 无可核之物 | `peer-review` 视图不是 `accepted_peer`，且没有交付记录 |
| ⑪ | ★硬阈值 | B 与 C 的 `(过程记录, 交付, 采信态)` 三元组**互不相同**（否则方案不成立） |
| ⑫ | 三件事有字段支撑 | activity 每行有 `source`/`judge`，且带 `how_to_read`（在干活/干完了/干得对 不是一回事） |
| ⑬ | 只读 | 读 activity 前后 `task_ledger.json` 的 sha256 不变 |

退出码：0＝全过；1＝有格没过；2＝起不来（如实说没跑成）。

用法：
```bash
python tools/visibility_falsify_check.py            # 跑并打印对照表
python tools/visibility_falsify_check.py --out temp/visibility_table.json   # 另存一份给盲判用
```
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
    """假 MCP 对端。`WITH_SESSION=True` 时才提供「读会话」工具（A 有、B/C 没有）。"""

    WITH_SESSION = False
    NAME = "示例成员"

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
        except Exception:                                      # noqa: BLE001 — 假对端：坏请求体当空，照样回一句（判据看的是平台侧）
            req = {}
        mid, method = req.get("id"), str(req.get("method") or "")
        sid = self.headers.get("Mcp-Session-Id") or "sess-fake"

        def ok(result):
            self._send({"jsonrpc": "2.0", "id": mid, "result": result},
                       headers={"Mcp-Session-Id": sid})

        if method == "initialize":
            # 如实声明本平台支持到的那一版（V131 §7 第 4 条同一口径）
            ok({"protocolVersion": "2025-06-18", "capabilities": {},
                "serverInfo": {"name": "fake-%s" % self.NAME, "version": "0"}})
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
                ok({"content": [{"type": "text", "text": json.dumps(
                    {"messages": [{"role": "agent", "text": "我在干活（示例）",
                                   "at": "2026-11-06T10:00:00"}]}, ensure_ascii=False)}]})
                return
            ok({"content": [{"type": "text", "text": "ok"}]})
            return
        self._send({"jsonrpc": "2.0", "id": mid,
                    "error": {"code": -32601, "message": "no method %s" % method}})


def _serve_fake(with_session: bool, name: str) -> tuple:
    port = _free_port()
    cls = type("H_%s" % ("sess" if with_session else "nosess"), (_Fake,),
               {"WITH_SESSION": with_session, "NAME": name})
    srv = ThreadingHTTPServer(("127.0.0.1", port), cls)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, "http://127.0.0.1:%d/mcp" % port


def _sha(p: Path) -> str:
    try:
        return hashlib.sha256(p.read_bytes()).hexdigest()
    except FileNotFoundError:
        return ""


def _task_of(tasks: list, seq: int) -> dict:
    for t in tasks or []:
        if int(t.get("task_seq") or 0) == int(seq):
            return t
    return {}


def main(argv: list | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    out_path = ""
    if "--out" in argv:
        i = argv.index("--out")
        if i + 1 < len(argv):
            out_path = argv[i + 1]

    port = _free_port()
    tmpdir = tempfile.mkdtemp(prefix="ac_visibility_")
    base = "http://127.0.0.1:%d" % port
    env = dict(os.environ)
    env.update({"AC_PORT": str(port), "AC_DATA_DIR": tmpdir, "PYTHONIOENCODING": "utf-8"})
    srv = subprocess.Popen([PY, "-m", "agent_community.platform.server"], cwd=str(ROOT), env=env,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    fakes = []
    print("判据：痕迹可见性证伪实验台（V131 §9 · 真跑：临时实例 + 3 个 mock 节点）")
    print("=" * 78)
    table = {}
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

        # ── 夹具：工作间 + 名单 + 3 条任务（A/B/C 各一条）────────────────────
        # ⚠️ **必须三个工位**（不是三条任务共用一个工位）：产物的扫描口径是「工位/产物/」，
        #   同一个工位的多条任务会共享产物目录 —— 用一条工位做三个节点，
        #   B（没交付）会把 A 的产物扫成自己的，⑥ 就成了假绿（首版实测踩过）。
        ws_a = Path(tmpdir) / "ws_vis"
        slots = ["前端组/员工01", "前端组/员工02", "前端组/员工03"]
        slot_a = slots[0]
        for d in (ws_a / "_lead", ws_a / "前端组"):
            d.mkdir(parents=True, exist_ok=True)
        for s in slots:
            (ws_a / s / "任务").mkdir(parents=True, exist_ok=True)
            (ws_a / s / "产物").mkdir(parents=True, exist_ok=True)
        (ws_a / "_lead" / "建模.md").write_text("# 建模（示例）\n", encoding="utf-8")
        (ws_a / "前端组" / "分工.md").write_text("- 版本: 1\n", encoding="utf-8")
        (ws_a / "前端组" / "_notice.md").write_text("", encoding="utf-8")
        _st, made = _http(base, "/api/workshop", {"name": "可见性实验间（示例）",
                                                 "hall_content": "证伪实验",
                                                 "workshop_path": str(ws_a)})
        wid = str((made or {}).get("workshop_id") or "")
        mk = _http(base, "/api/mind/A-01", {})[1] or {}
        _http(base, "/api/workshop/%s/manifest" % wid,
              {"mind_path": str(mk.get("mind_path") or ""), "entry_points": ["_lead/建模.md"]})
        txt = ("#回执 v1\n类型: 名单\n工作间: %s\n成员: 组长\n凭据:\n序号: 5\n---\n"
               % wid) + "".join(
                   "岗位: %s | harness: 示例Harness-%s | model: 示例模型 | 人数: 1\n"
                   % (s, chr(ord("A") + i)) for i, s in enumerate(slots)) + "---\n行数: 3"
        _http(base, "/api/workshop/%s/intake?source=C&confirm=1" % wid, {"text": txt})
        roster = _http(base, "/api/workshop/%s/roster" % wid)[1] or {}
        members = roster.get("members") or []
        seq_by_slot = {str(m.get("slot_dir") or ""): m.get("member_seq") for m in members}
        # 每个工位都要先"接上"（三项齐）—— 未接上的工位派活会被平台拦住，那会让夹具假红
        for s in slots:
            _http(base, "/api/workshop/%s/members/%s/evidence" % (wid, seq_by_slot.get(s)),
                  {"paste_seen": True, "press_ok": True, "rename_seen": True,
                   "verified_scope": "夹具：三项齐"})
        seqs = []
        for i, label in enumerate(("A 正常回传", "B 完全沉默", "C 假装在干活"), start=1):
            _ms = seq_by_slot.get(slots[i - 1])
            _st, dsp = _http(base, "/api/workshop/%s/dispatch" % wid,
                             {"member_seq": _ms, "title": "%s（T-%03d）" % (label, i),
                              "goal": "可见性证伪实验"})
            seqs.append(int((dsp or {}).get("task_seq") or i))

        _st, tasks_b = _http(base, "/api/workshop/%s/tasks" % wid)
        tasks = (tasks_b or {}).get("tasks") or []
        check("① 夹具：工作间 + 3 条任务真建出来（账本数得到）",
              bool(wid) and len(tasks) >= 3,
              "wid=%r tasks=%d" % (wid, len(tasks)))

        # ── 三个 mock 节点注册（A 有读会话工具；B/C 没有）────────────────────
        nodes = {}
        for hid, nm, with_sess in (("falsify-a", "示例Harness-A", True),
                                   ("falsify-b", "示例Harness-B", False),
                                   ("falsify-c", "示例Harness-C", False)):
            f, u = _serve_fake(with_sess, hid)
            fakes.append(f)
            _http(base, "/api/harness/register", {
                "harness_id": hid, "harness_name": nm, "harness_type": "custom",
                "wakeup_method": "mcp", "api_base_url": u, "ai": {"model_name": "示例模型"}})
            nodes[hid] = {"url": u, "task_seq": seqs[len(nodes)], "name": nm}

        # ── A：真报 3 条进度 ────────────────────────────────────────────────
        for k in range(3):
            _http(base, "/api/progress", {"text": "A 第 %d 步：在写代码（示例）" % (k + 1),
                                          "harness_id": "falsify-a",
                                          "task_id": "T-%03d" % nodes["falsify-a"]["task_seq"],
                                          "kind": "step"})
        # ── C：真报 3 条进度，但**只有说法**（"完成了"，没有产物）──────────
        for k in range(3):
            _http(base, "/api/progress", {"text": "C 第 %d 步：已完成，全部搞定（示例，无产物）" % (k + 1),
                                          "harness_id": "falsify-c",
                                          "task_id": "T-%03d" % nodes["falsify-c"]["task_seq"],
                                          "kind": "done" if k == 2 else "step"})
        # B：什么都不报（这就是它的全部行为）

        ledger = Path(tmpdir) / "task_ledger.json"
        before = _sha(ledger)

        # ── 读三个节点的活动视图 ────────────────────────────────────────────
        acts = {}
        for hid in ("falsify-a", "falsify-b", "falsify-c"):
            st, b = _http(base, "/api/harness/%s/activity?read_session=0" % hid)
            acts[hid] = (st, b or {})
        after_read = _sha(ledger)
        a_st, a_b = acts["falsify-a"]
        b_st, b_b = acts["falsify-b"]
        c_st, c_b = acts["falsify-c"]
        a_prog = (a_b.get("counts") or {}).get("progress")
        b_prog = (b_b.get("counts") or {}).get("progress")
        c_prog = (c_b.get("counts") or {}).get("progress")
        a_rows = [r for r in (a_b.get("work_trace") or []) if r.get("kind") == "progress"]
        b_rows = [r for r in (b_b.get("work_trace") or []) if r.get("kind") == "progress"]
        c_rows = [r for r in (c_b.get("work_trace") or []) if r.get("kind") == "progress"]

        check("② A 在干活（真报的 3 条都在，标 self_reported/unverified）",
              a_st == 200 and a_prog == 3 and bool(a_rows)
              and a_rows[-1].get("source") == "self_reported"
              and a_rows[-1].get("judge") == "unverified",
              "counts=%r rows=%d %s" % (a_prog, len(a_rows),
                                        json.dumps(a_rows[-1:], ensure_ascii=False)[:150]))
        check("③ B 没回传（0 条过程记录；不是空表装成功）",
              b_st == 200 and b_prog == 0 and not b_rows,
              "counts=%r rows=%d" % (b_prog, len(b_rows)))
        check("④ 光看过程记录分不开 B/C（C 也有 3 条，如实记录）",
              c_st == 200 and c_prog == 3,
              "C counts=%r rows=%d" % (c_prog, len(c_rows)))

        # ── A：真交产物 + 本侧真复核 + 验收通过 ─────────────────────────────
        data = "报告正文（示例）：这条任务真的做了东西。\n".encode("utf-8")
        (ws_a / slot_a / "产物" / "报告.md").write_bytes(data)
        sha, nbytes = hashlib.sha256(data).hexdigest(), len(data)
        peer_files = [{"name": "示例工位/报告.md", "sha256": sha, "bytes": nbytes}]
        seq_a_task = nodes["falsify-a"]["task_seq"]
        _http(base, "/api/workshop/%s/tasks/%d/peer-delivery" % (wid, seq_a_task),
              {"source": "mcp", "result": "交付 报告.md", "artifacts": peer_files})
        st_pr, pr = _http(base, "/api/workshop/%s/tasks/%d/peer-review" % (wid, seq_a_task),
                          {"peer_files": peer_files, "checked_by": "验收人（示例）"})
        st_rv, rv = _http(base, "/api/workshop/%s/tasks/%d/review" % (wid, seq_a_task),
                          {"result": "通过", "evidence_level": "L0"})
        tasks_now = (_http(base, "/api/workshop/%s/tasks" % wid)[1] or {}).get("tasks") or []
        a_task = _task_of(tasks_now, seq_a_task)
        a_view = _http(base, "/api/workshop/%s/tasks/%d/peer-review" % (wid, seq_a_task))[1] or {}

        # ── B：不交付，直接想验收通过（应当被拦住）──────────────────────────
        seq_b_task = nodes["falsify-b"]["task_seq"]
        st_rv_b, rv_b = _http(base, "/api/workshop/%s/tasks/%d/review" % (wid, seq_b_task),
                              {"result": "通过", "evidence_level": "L0"})
        tasks_now = (_http(base, "/api/workshop/%s/tasks" % wid)[1] or {}).get("tasks") or []
        b_task = _task_of(tasks_now, seq_b_task)
        b_view = _http(base, "/api/workshop/%s/tasks/%d/peer-review" % (wid, seq_b_task))[1] or {}

        # ── C：只有说法（交付清单为空），想验收通过（应当被拦住）────────────
        seq_c_task = nodes["falsify-c"]["task_seq"]
        _http(base, "/api/workshop/%s/tasks/%d/peer-delivery" % (wid, seq_c_task),
              {"source": "mcp", "result": "我做完了（仅自报，无产物）", "artifacts": []})
        st_rv_c, rv_c = _http(base, "/api/workshop/%s/tasks/%d/review" % (wid, seq_c_task),
                              {"result": "通过", "evidence_level": "L0"})
        tasks_now = (_http(base, "/api/workshop/%s/tasks" % wid)[1] or {}).get("tasks") or []
        c_task = _task_of(tasks_now, seq_c_task)
        c_view = _http(base, "/api/workshop/%s/tasks/%d/peer-review" % (wid, seq_c_task))[1] or {}

        check("⑤ A 干完了（复核后验收通过 ⇒ state=accepted）",
              st_rv == 200 and str(a_task.get("state") or "") == "accepted",
              "review=%s state=%r" % (st_rv, a_task.get("state")))
        check("⑥ B 没干完（没交付 ⇒ 验收被拒，且说清 code/next）",
              st_rv_b == 400 and str(b_task.get("state") or "") != "accepted"
              and (rv_b.get("code") or "") and (rv_b.get("next") or ""),
              "HTTP %s code=%r next=%r" % (st_rv_b, rv_b.get("code"), str(rv_b.get("next"))[:60]))
        # ⑦ 的 code **不写死在一个值**上：工位完全没产物时闸门更早（ARTIFACTS_EMPTY），
        #    有产物但没复核时才是 PEER_NOT_REVIEWED —— 两者都算"说法不能过验收"，
        #    真正的硬要求是「被拒 + 说清原因 + 任务没被记成通过 + 采信态停在 accepted_self」。
        check("⑦ C 说完成不算完成（验收被拒 + 停在 accepted_self，不是无理由的 400）",
              st_rv_c == 400 and str(c_task.get("state") or "") != "accepted"
              and bool((rv_c.get("code") or "")) and bool((rv_c.get("next") or ""))
              and str(c_view.get("accept_state") or "") == "accepted_self",
              "HTTP %s code=%r next=%r accept=%r state=%r"
              % (st_rv_c, rv_c.get("code"), str(rv_c.get("next"))[:50],
                 c_view.get("accept_state"), c_task.get("state")))
        check("⑧ A 干得对（本侧重算一致 ⇒ accepted_peer + peer_checked_at）",
              st_pr == 200 and (pr.get("accept_state") or "") == "accepted_peer"
              and bool(a_view.get("peer_checked_at")),
              "HTTP %s accept=%r checked_at=%r" % (st_pr, pr.get("accept_state"),
                                                   a_view.get("peer_checked_at")))
        c_diff = c_view.get("peer_diff") or {}
        check("⑨ C 干得对不对不明（停在 accepted_self，diff 说清没东西可核）",
              (c_view.get("accept_state") or "") == "accepted_self"
              and (not c_diff.get("matched")),
              "accept=%r matched=%r why=%r" % (c_view.get("accept_state"),
                                               c_diff.get("matched"),
                                               str(c_diff.get("why"))[:70]))
        check("⑩ B 无可核之物（不是 accepted_peer，也没有交付记录）",
              (b_view.get("accept_state") or "") != "accepted_peer"
              and not (b_view.get("delivery") or {}).get("result"),
              "accept=%r delivery=%r" % (b_view.get("accept_state"),
                                         json.dumps(b_view.get("delivery") or {},
                                                    ensure_ascii=False)[:80]))

        # ── ★硬阈值：B 与 C 必须能分开 ──────────────────────────────────────
        def _triple(has_prog: bool, delivery: str, accept: str) -> tuple:
            return (bool(has_prog), str(delivery or ""), str(accept or ""))

        b_triple = _triple(bool(b_prog), str((b_view.get("delivery") or {}).get("result") or ""),
                           str(b_view.get("accept_state") or ""))
        c_triple = _triple(bool(c_prog), str((c_view.get("delivery") or {}).get("result") or ""),
                           str(c_view.get("accept_state") or ""))
        check("⑪ ★硬阈值：B（没回传）与 C（假装）数据层互不相同",
              b_triple != c_triple and b_triple[0] is False and c_triple[0] is True,
              "B=%r C=%r" % (b_triple, c_triple))

        # ── ⑫ 三件事有字段支撑 ──────────────────────────────────────────────
        all_rows = (a_b.get("work_trace") or []) + (b_b.get("work_trace") or []) + \
                   (c_b.get("work_trace") or [])
        has_fields = bool(all_rows) and all((r.get("source") and r.get("judge")) for r in all_rows)
        check("⑫ 三件事有字段支撑（每行 source/judge + how_to_read 说明）",
              has_fields and bool(a_b.get("how_to_read")),
              "rows=%d how_to_read=%r" % (len(all_rows), str(a_b.get("how_to_read"))[:60]))

        # ── ⑬ 只读（读 activity 前后账本 sha256 不变；采样点必须在任何写操作之前）────
        check("⑬ 只读（读 activity 前后 task_ledger.json 的 sha256 不变）",
              before == after_read and bool(before),
              "before=%s after=%s" % (before[:10], after_read[:10]))

        # ── 对照表（给 5 人盲判用：只给现象，不给结论）──────────────────────
        table = {
            "workshop_id": wid,
            "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "nodes": {
                "A（示例节点，正常回传）": {
                    "progress_rows": a_prog,
                    "task_state": str(a_task.get("state") or ""),
                    "accept_state": str(a_view.get("accept_state") or ""),
                    "peer_checked_at": str(a_view.get("peer_checked_at") or ""),
                    "delivery": str((a_view.get("delivery") or {}).get("result") or ""),
                },
                "B（示例节点，沉默）": {
                    "progress_rows": b_prog,
                    "task_state": str(b_task.get("state") or ""),
                    "accept_state": str(b_view.get("accept_state") or ""),
                    "peer_checked_at": "",
                    "delivery": str((b_view.get("delivery") or {}).get("result") or ""),
                },
                "C（示例节点，只说不做）": {
                    "progress_rows": c_prog,
                    "task_state": str(c_task.get("state") or ""),
                    "accept_state": str(c_view.get("accept_state") or ""),
                    "peer_checked_at": "",
                    "delivery": str((c_view.get("delivery") or {}).get("result") or ""),
                },
            },
            "questions": {
                "① 哪条在干活": "看 progress_rows（>0 才是有人报过程）",
                "② 哪条干完了": "看 task_state（accepted 才是验收通过）",
                "③ 哪条干得对": "看 accept_state / peer_checked_at（accepted_peer 才是平台核过）",
            },
            "note": ("这张表是**给盲判用的现象**，不含结论；三个节点的名字在盲判时请打乱。"
                     "§9 硬阈值：看完后仍分不清 B 与 C ⇒ 方案不成立。"),
        }
        print("-" * 78)
        print("对照表（现象，供盲判）：")
        for k, v in table["nodes"].items():
            print("  %-22s 过程记录=%s 任务状态=%-9s 采信态=%-14s 复核时间=%s"
                  % (k, v["progress_rows"], v["task_state"] or "（无）",
                     v["accept_state"] or "（无）", v["peer_checked_at"] or "（无）"))
    finally:
        for f in fakes:
            try:
                f.shutdown()
            except Exception:                                  # noqa: BLE001 — 收尾：假对端关不掉不影响判据结论
                pass
        try:
            srv.terminate()
            srv.wait(timeout=10)
        except Exception:                                      # noqa: BLE001 — 收尾：进程本来就没起来，杀掉即可
            srv.kill()
        shutil.rmtree(tmpdir, ignore_errors=True)

    print("-" * 78)
    passed = sum(1 for _l, ok, _d in ROWS if ok)
    print("合计 %d 格：PASS %d / FAIL %d" % (len(ROWS), passed, len(ROWS) - passed))
    for lb, ok, d in ROWS:
        if not ok:
            print("  FAIL:", lb, "|", str(d)[:220])
    if out_path:
        p = Path(out_path)
        if not p.is_absolute():
            p = ROOT / p
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(table, ensure_ascii=False, indent=2), encoding="utf-8")
        print("对照表已写到：%s" % p)
    return 0 if passed == len(ROWS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
