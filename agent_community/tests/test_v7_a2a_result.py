# -*- coding: utf-8 -*-
"""守卫：**A2A 派活后的结果回写**（清单 B2：派了活，平台账本里永远没有结果）。

守三件事：
  ① 对端到终态 ⇒ 账本从 `issued` 推进到 `delivered`，并把对端原话/产物记进 `external`；
  ② **不许冒充验收**：`state` 只能是 `delivered`，`accepted/rejected` 仍只能由人给
     （这是 2026-10-25 那次用成本换来的教训：对方自报不能当通过）；
  ③ 对端一直不完成 ⇒ 别死循环：同一条有节流、过期会出队，并且**失败要说出来**。

真跑：真起一个假 A2A 对端（会先"在做"、再"完成"），真读写账本文件。
"""
from __future__ import annotations

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.append(str(_ROOT))


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """把数据目录指到临时目录，并起一个会"先在做、后完成"的假 A2A 对端。"""
    monkeypatch.setenv("AC_DATA_DIR", str(tmp_path))
    state = {"n": 0, "statuses": ["working", "completed"], "artifacts": ["产物/api.md"]}

    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            n = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(n).decode("utf-8", "replace")
            j = json.loads(raw) if raw else {}
            if str(j.get("method")) in ("GetTask", "tasks/get"):
                i = min(state["n"], len(state["statuses"]) - 1)
                state["n"] += 1
                st = state["statuses"][i]
                # ★2026-11-01 真机形状（假对端原来给的是字符串，所以漏掉了两个坑）：
                #   ① A2A v1.0 的 `status` 是**对象**：{"state": "TASK_STATE_XXX", "timestamp": ...}
                #   ② 枚举值带前缀 `TASK_STATE_`
                #   ③ 回复文本在 `artifacts[].parts[]`，而且**逐词**给（必须原样直拼）
                res = {"id": "t-1",
                       "status": {"state": "TASK_STATE_" + st.upper(),
                                  "timestamp": "2026-11-01T09:00:00.000Z"}}
                if st == "completed":
                    res["status"]["message"] = {"role": "agent", "parts": [{"text": "收到"}]}
                    res["artifacts"] = [{"artifactId": "result",
                                         "parts": [{"text": "收到"}, {"text": "，"}, {"text": "已"},
                                                   {"text": "完成"}]}]
            else:
                res = {"id": "t-1", "status": {"state": "TASK_STATE_WORKING"}}
            b = json.dumps({"jsonrpc": "2.0", "id": j.get("id"), "result": res}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        yield {"url": "http://127.0.0.1:%d" % srv.server_port, "state": state}
    finally:
        srv.shutdown()


def _issue(ws="WS-1", seq=1):
    from agent_community.platform import task_ledger as L

    L.issue(ws, seq, slot_dir="前端组/员工01", file="前端组/员工01/任务/T-001.md", title="做接口")
    return L.get(ws, seq)


def test_result_written_back_and_not_pretending_acceptance(env):
    from agent_community.platform import a2a_result_poll as P
    from agent_community.platform import task_ledger as L

    _issue()
    P.add(url=env["url"], a2a_task_id="t-1", workshop_id="WS-1", task_seq=1,
          harness_id="h-a2a")

    # 第一次查：对端还在做 ⇒ 不动账本、留在 pending
    s1 = P.sweep_once(gap=0)
    assert s1["checked"] == 1 and s1["done"] == 0
    assert L.get("WS-1", 1)["state"] == "issued"
    assert len(P.list_pending()) == 1

    # 第二次查：对端说完成 ⇒ 推进到 delivered 并带回结果与产物
    s2 = P.sweep_once(gap=0)
    assert s2["done"] == 1, s2
    rec = L.get("WS-1", 1)
    assert rec["state"] == "delivered", "只许推进到 delivered"
    assert rec["state"] not in ("accepted", "rejected"), "**不许冒充人拍板的验收结论**"
    ext = rec.get("external") or {}
    assert ext.get("source") == "a2a" and ext.get("task_id") == "t-1"
    # ★归一化：对端给的是 `TASK_STATE_COMPLETED`，账本里要记成 `completed`
    assert ext.get("state") == "completed", "A2A v1.0 的 TASK_STATE_ 前缀必须归一化掉"
    # ★逐词 parts 必须**直拼**，不许用换行/空格把句子拆碎（真机踩过）
    assert ext.get("result") == "收到，已完成", "parts 是逐词片段，要原样拼回一句（拿到 %r）" % ext.get("result")
    assert len(P.list_pending()) == 0, "写回后必须出队"


def test_status_object_is_understood(env):
    """`status` 是**对象**（v1.0）时也要能判终态 —— 否则账本永远停在 issued。

    真机实测：对端回 `{"status": {"state": "TASK_STATE_COMPLETED", ...}}`，
    而旧代码 `str(status)` 得到一整串字典文本，跟终态表一个字都对不上。
    """
    from agent_community.platform import a2a_result_poll as P
    from agent_community.platform import task_ledger as L

    _issue(seq=3)
    env["state"]["n"] = 99                    # 直接返回终态
    P.add(url=env["url"], a2a_task_id="t-3", workshop_id="WS-1", task_seq=3)
    s = P.sweep_once(gap=0)
    assert s["done"] == 1, "status 是对象时也必须认出终态"
    assert L.get("WS-1", 3)["state"] == "delivered"


def test_throttle_and_ttl(env):
    """别把对端打爆：同一条两次查询之间要有间隔；过期要出队。"""
    from agent_community.platform import a2a_result_poll as P

    _issue(seq=2)
    P.add(url=env["url"], a2a_task_id="t-2", workshop_id="WS-1", task_seq=2)
    P.sweep_once(gap=999)                    # 查一次
    s = P.sweep_once(gap=999)                # 立刻再查 ⇒ 被节流，不该再打对端
    assert s["checked"] == 0, "同一任务两次查询之间必须节流"

    # 把 added_at 改成很老 ⇒ 过期出队
    items = P.list_pending()
    for it in items:
        it["added_at"] = 0.0
    P._save(items)
    s2 = P.sweep_once(gap=0)
    assert s2["dropped"] == 1, "超过 TTL 要出队，不能无限留着"


def test_ledger_missing_counts_as_dropped(env):
    """账本里没这条（工作间被删等）⇒ 出队并说明，不无限重试。"""
    from agent_community.platform import a2a_result_poll as P

    P.add(url=env["url"], a2a_task_id="t-x", workshop_id="WS-NOPE", task_seq=99)
    env["state"]["n"] = 99                   # 让它直接返回终态
    s = P.sweep_once(gap=0)
    assert s["dropped"] == 1, s
    assert len(P.list_pending()) == 0
