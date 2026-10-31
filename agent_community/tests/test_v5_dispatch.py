# -*- coding: utf-8 -*-
"""守卫：**P3 派活**（写文件 + `_notice` + 平台 append manifest + 三步激活 + B 档结算）。

对应 `V115` §2.D（D1~D14）。全部真跑：真文件、真 HTTP、真临时实例。
"""
from __future__ import annotations

import json
import socket
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.append(str(_ROOT))

from agent_community.platform import dispatch as dp  # noqa: E402
from agent_community.platform import manifest as mf  # noqa: E402
from agent_community.platform import mind  # noqa: E402
from agent_community.platform import v5_roster as roster  # noqa: E402


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """两套根 + 空名册 + 隔离的数据目录。"""
    minds = tmp_path / "minds"
    wsr = tmp_path / "ws"
    wsr.mkdir()
    monkeypatch.setenv("AC_MIND_ROOT", str(minds))
    monkeypatch.setenv("AC_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(mind, "mind_root", lambda: minds, raising=True)
    monkeypatch.setattr(roster, "_path", lambda: tmp_path / "data" / "v5_roster.json", raising=True)
    monkeypatch.setattr(dp, "queue_timeout", lambda: 60, raising=True)
    (wsr / "_lead").mkdir()
    (wsr / "_lead" / "建模.md").write_text("# 建模\n", encoding="utf-8")
    return {"minds": minds, "ws": wsr, "tmp": tmp_path}


def _prepare_workshop(env, monkeypatch, *, tier="c_visual", active=True):
    """建名单 + 写合法 manifest，返回 (client, workshop_id, member_seq)。"""
    from fastapi.testclient import TestClient

    from agent_community.platform import server as srv

    ws = srv.Workshop(workshop_id="WS-T", name="示例间", workspace_dir=str(env["ws"]),
                      hall_content="方向")
    srv.workshops["WS-T"] = ws
    roster.add_slots("WS-T", [{"岗位": "前端组/员工01", "harness": "h_cursor",
                               "model": "claude-x", "人数": 1}], source="C")
    seq = roster.roster("WS-T")["members"][0]["member_seq"]
    if active:
        # 档位不同、证据不同：A 档 protocol_ok / B 档 **bridge_ok + mailbox_ok（真取件）** / C 档三项
        #   ★2026-10-31：B 档只要 bridge_ok 不再算齐（老板 SOP：只通没取件最高 acked）⇒ 夹具补 mailbox_ok
        if tier == "a_protocol":
            ev = {"protocol_ok": True}
        elif tier == "b_bridge":
            ev = {"bridge_ok": True, "mailbox_ok": True}
        else:
            ev = {"paste_seen": True, "press_ok": True, "rename_seen": True}
        ev.update({"tier": tier,
                   "verified_scope": "%s 档证据齐（测试夹具）" % tier})
        roster.set_evidence("WS-T", seq, ev)
    body = mf.build(workshop_id="WS-T", workshop_path=str(env["ws"]),
                    mind_path=str(env["minds"] / "A-01"), entry_points=["_lead/建模.md"])
    mf.write(env["ws"], body)
    return TestClient(srv.app, client=("127.0.0.1", 54321)), "WS-T", seq


# ── D1/D2/D3：文件、notice、manifest（平台 append）──────────────────
def test_d1_d2_d3_writes_file_notice_and_manifest(env, monkeypatch):
    client, wid, seq = _prepare_workshop(env, monkeypatch)
    r = client.post(f"/api/workshop/{wid}/dispatch",
                    json={"member_seq": seq, "title": "接口对接", "goal": "把接口对上",
                          "accept": "每个端点写清错误码", "due": "2026-10-28"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] and body["file_created"] is True

    task_file = env["ws"] / body["file"]
    assert task_file.is_file(), "任务文件必须真写出来（D1）"
    text = task_file.read_text(encoding="utf-8")
    assert "接口对接" in text and "每个端点写清错误码" in text and "前端组/员工01" in text

    notice = env["ws"] / "前端组" / "员工01" / "_notice.md"
    assert notice.is_file() and "T-001" in notice.read_text(encoding="utf-8"), "notice 必须追加（D2）"

    ok, mfb, _ = mf.read(env["ws"])
    assert ok and body["file"] in (mfb.get("active_tasks") or []), \
        "★平台必须把任务 append 进 manifest.active_tasks（架构师排雷①）"
    assert body["manifest"]["manifest_hash"].startswith("sha256:")


def test_d2_notice_is_append_only(env, monkeypatch):
    client, wid, seq = _prepare_workshop(env, monkeypatch)
    client.post(f"/api/workshop/{wid}/dispatch", json={"member_seq": seq, "title": "第一条"})
    first = (env["ws"] / "前端组" / "员工01" / "_notice.md").read_text(encoding="utf-8")
    client.post(f"/api/workshop/{wid}/dispatch", json={"member_seq": seq, "title": "第二条"})
    later = (env["ws"] / "前端组" / "员工01" / "_notice.md").read_text(encoding="utf-8")
    assert later.startswith(first), "notice 只能追加，历史不许改写"


def test_d3_manifest_stays_valid_after_dispatch(env, monkeypatch):
    client, wid, seq = _prepare_workshop(env, monkeypatch)
    client.post(f"/api/workshop/{wid}/dispatch", json={"member_seq": seq, "title": "A"})
    got = client.get(f"/api/workshop/{wid}/manifest")
    assert got.status_code == 200 and got.json()["ok"] is True, "派活后 G0 必须还是绿的"


# ── D4~D8：激活三档 ───────────────────────────────────────────────
def test_d4_b_bridge_writes_flag_and_queues(env, monkeypatch):
    client, wid, seq = _prepare_workshop(env, monkeypatch, tier="b_bridge")
    body = client.post(f"/api/workshop/{wid}/dispatch",
                       json={"member_seq": seq, "title": "桥型"}).json()
    assert body["wakeup"]["result"] == "queued"
    flag = env["ws"] / "前端组" / "员工01" / "_wakeup.flag"
    assert flag.is_file() and "T-001" in flag.read_text(encoding="utf-8")
    assert body["activated"] is True, "queued 算激活已发起"


def test_d5_b_bridge_settles_ok_when_flag_taken(env, monkeypatch):
    client, wid, seq = _prepare_workshop(env, monkeypatch, tier="b_bridge")
    client.post(f"/api/workshop/{wid}/dispatch", json={"member_seq": seq, "title": "桥型"})
    (env["ws"] / "前端组" / "员工01" / "_wakeup.flag").unlink()      # 模拟桥取走
    out = client.post(f"/api/workshop/{wid}/wakeup/poll", json={"member_seq": seq}).json()
    assert out["results"][0]["state"] == "taken" and out["results"][0]["settled"] == "ok"


def test_d6_b_bridge_times_out_to_manual(env, monkeypatch):
    client, wid, seq = _prepare_workshop(env, monkeypatch, tier="b_bridge")
    client.post(f"/api/workshop/{wid}/dispatch", json={"member_seq": seq, "title": "桥型"})
    monkeypatch.setattr(dp, "queue_timeout", lambda: 0, raising=True)
    out = client.post(f"/api/workshop/{wid}/wakeup/poll", json={"member_seq": seq}).json()
    assert out["results"][0]["state"] == "timeout" and out["results"][0]["settled"] == "manual"


def test_d7_a_protocol_without_endpoint_says_manual_and_really_posts(env, monkeypatch):
    """A 档：没配地址 ⇒ **manual 且不假装 ok**；配了地址 ⇒ 真 POST 出去。"""
    client, wid, seq = _prepare_workshop(env, monkeypatch, tier="a_protocol")
    body = client.post(f"/api/workshop/{wid}/dispatch",
                       json={"member_seq": seq, "title": "协议型"}).json()
    assert body["wakeup"]["result"] == "manual" and body.get("needs_human") is True
    assert "没配唤醒地址" in body["wakeup"]["detail"]

    hits = []

    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            n = int(self.headers.get("Content-Length") or 0)
            hits.append(self.rfile.read(n).decode("utf-8"))
            self.send_response(200)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"{}")

        def log_message(self, *a):
            pass

    srv_http = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv_http.serve_forever, daemon=True).start()
    url = "http://127.0.0.1:%d/wakeup" % srv_http.server_port
    try:
        monkeypatch.setattr(dp, "_wakeup_endpoint", lambda hid: url, raising=True)
        body2 = client.post(f"/api/workshop/{wid}/dispatch",
                            json={"member_seq": seq, "title": "协议型2"}).json()
        assert body2["wakeup"]["result"] == "ok" and body2["wakeup"]["method"] == "http"
        assert hits and "task.assigned" in hits[0], "必须真发出去（不是记一笔）"
    finally:
        srv_http.shutdown()


def test_d8_wakeup_failure_is_not_reported_as_ok(env, monkeypatch):
    client, wid, seq = _prepare_workshop(env, monkeypatch, tier="a_protocol")
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    dead_port = s.getsockname()[1]
    s.close()
    monkeypatch.setattr(dp, "_wakeup_endpoint",
                        lambda hid: "http://127.0.0.1:%d/none" % dead_port, raising=True)
    body = client.post(f"/api/workshop/{wid}/dispatch",
                       json={"member_seq": seq, "title": "叫不醒"}).json()
    assert body["wakeup"]["result"] == "failed" and body.get("code") == "WAKEUP_FAILED"
    assert body["activated"] is False


# ── D9/D10：闸门与"正文不进球" ────────────────────────────────────
def test_d9_dispatch_blocked_when_member_not_active(env, monkeypatch):
    client, wid, seq = _prepare_workshop(env, monkeypatch, active=False)
    r = client.post(f"/api/workshop/{wid}/dispatch", json={"member_seq": seq, "title": "x"})
    assert r.status_code == 409
    j = r.json()
    assert j["code"] == "GATE_G5" and set(j["four"]) == {"who", "what", "where", "how"}


def test_d10_dispatch_requires_manifest(env, monkeypatch):
    client, wid, seq = _prepare_workshop(env, monkeypatch)
    (env["ws"] / "manifest.json").unlink()
    r = client.post(f"/api/workshop/{wid}/dispatch", json={"member_seq": seq, "title": "x"})
    assert r.status_code == 409 and r.json()["code"] == "GATE_G0"


def test_d10b_body_never_goes_into_db_but_file_must_exist(env, monkeypatch):
    client, wid, seq = _prepare_workshop(env, monkeypatch)
    r = client.post(f"/api/workshop/{wid}/dispatch",
                    json={"member_seq": seq, "title": "组长自己写正文", "write_file": "0"})
    assert r.status_code == 400 and r.json()["code"] == "TASK_SEQ_REQUIRED", "号不许猜"
    r0 = client.post(f"/api/workshop/{wid}/dispatch",
                     json={"member_seq": seq, "title": "组长自己写正文", "write_file": "0",
                           "task_id": "T-001"})
    assert r0.status_code == 400 and r0.json()["code"] == "TASK_FILE_MISSING", "正文没写就拒"
    # 组长真写好正文后，同一请求就过
    p = env["ws"] / dp.task_relpath("前端组/员工01", 1)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("# T-001 正文由组长写\n", encoding="utf-8")
    r2 = client.post(f"/api/workshop/{wid}/dispatch",
                     json={"member_seq": seq, "title": "组长自己写正文", "write_file": "0",
                           "task_id": "T-001"})
    assert r2.status_code == 200 and r2.json()["file_created"] is False


# ── D11/D12：账可查、任务号不撞 ───────────────────────────────────
def test_d11_dispatch_detail_shows_file_and_manifest(env, monkeypatch):
    client, wid, seq = _prepare_workshop(env, monkeypatch)
    body = client.post(f"/api/workshop/{wid}/dispatch",
                       json={"member_seq": seq, "title": "可查"}).json()
    d = client.get(f"/api/workshop/{wid}/dispatch/{body['task_seq']}").json()
    assert d["ok"] and d["file_exists"] is True and d["in_manifest"] is True
    assert d["entry"]["activated_at"]


def test_d12_task_seq_increments_and_no_reuse(env, monkeypatch):
    client, wid, seq = _prepare_workshop(env, monkeypatch)
    a = client.post(f"/api/workshop/{wid}/dispatch", json={"member_seq": seq, "title": "一"}).json()
    b = client.post(f"/api/workshop/{wid}/dispatch", json={"member_seq": seq, "title": "二"}).json()
    assert b["task_seq"] == a["task_seq"] + 1 and a["file"] != b["file"]


# ── D13/D14：回执走同一核心 / 证据等级 ────────────────────────────
def test_d13_dispatch_receipt_uses_same_core(env, monkeypatch):
    client, wid, seq = _prepare_workshop(env, monkeypatch)
    p = env["ws"] / dp.task_relpath("前端组/员工01", 2)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("# T-002 正文\n", encoding="utf-8")
    text = ("#回执 v1\n类型: 派活\n工作间: WS-T\n成员: 组长\n凭据:\n序号: 20\n---\n"
            "任务: T-002 | 派给: 前端组/员工01 | 标题: 回执派活 | 期限: 2026-10-28\n---\n行数: 1")
    r = client.post(f"/api/workshop/{wid}/intake?source=C&confirm=1", json={"text": text})
    body = r.json()
    assert body["applied"] is True and body["type"] == "dispatch"
    ok, mfb, _ = mf.read(env["ws"])
    assert ok and any("T-002" in x for x in (mfb.get("active_tasks") or []))


def test_d14_accept_receipt_rejects_L3_evidence(env, monkeypatch):
    client, wid, seq = _prepare_workshop(env, monkeypatch)
    text = ("#回执 v1\n类型: 验收\n工作间: WS-T\n成员: 组长\n凭据:\n序号: 30\n---\n"
            "验收: T-001 | 结果: 通过 | 产物: 1 | 备注: 好了 | 证据: L3\n---\n行数: 1")
    r = client.post(f"/api/workshop/{wid}/intake?source=C&confirm=1", json={"text": text})
    assert r.status_code == 422
    assert (r.json()["detail"]["errors"][0]["code"]) == "R011"
    ok_text = text.replace("证据: L3", "证据: L2")
    r2 = client.post(f"/api/workshop/{wid}/intake?source=C&confirm=1", json={"text": ok_text})
    assert r2.status_code == 200 and r2.json()["rows"][0]["证据"] == "L2"

# ── 「卡在哪一步」要说得清（2026-10-31）──────────────────────────────
def test_d15_steps_say_where_it_stuck(env, monkeypatch):
    """成功路径：四步全 ok、stuck_at 空、activated true。"""
    # B 档（桥）才会走到「queued」这条**真触达**路径；C 档是人工，属另一条用例
    client, wid, seq = _prepare_workshop(env, monkeypatch, tier="b_bridge")
    r = client.post(f"/api/workshop/{wid}/dispatch",
                    json={"member_seq": seq, "title": "四步表", "goal": "g"})
    assert r.status_code == 200, r.text
    b = r.json()
    for k in ("steps", "stuck_at", "why", "next", "manual_required"):
        assert k in b, "返回里缺 %s（界面没法显示卡在哪）" % k
    assert [x["n"] for x in b["steps"]] == [1, 2, 3, 4]
    assert all(x["ok"] for x in b["steps"]), b["steps"]
    assert b["stuck_at"] == "" and b["manual_required"] is False


def test_d16_manual_is_not_a_failure(env, monkeypatch):
    """C 档 manual：三步落盘 ⇒ `ok` 仍 true，`manual_required` true、`stuck_at` 空并给贴法。

    老板 SOP：视觉型（网页版）就是人工粘贴这条**正常**路径，别当错误报。
    """
    client, wid, seq = _prepare_workshop(env, monkeypatch, tier="c_visual")
    r = client.post(f"/api/workshop/{wid}/dispatch",
                    json={"member_seq": seq, "title": "人工档", "goal": "g"})
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["ok"] is True, "C 档 manual 不该被判失败"
    assert b["manual_required"] is True
    assert b["stuck_at"] == ""
    assert b["manual_hint"], "要告诉人「该怎么贴」"
    assert b["steps"][3]["result"] == "manual"

def test_d17_ledger_view_shares_steps(env, monkeypatch):
    """`GET …/dispatch/{seq}` 与 `POST …/dispatch` 返回的 steps/stuck_at 必须一致（规则 20）。"""
    client, wid, seq = _prepare_workshop(env, monkeypatch, tier="b_bridge")
    r = client.post(f"/api/workshop/{wid}/dispatch",
                    json={"member_seq": seq, "title": "账本同源", "goal": "g"})
    assert r.status_code == 200, r.text
    made = r.json()
    d = client.get(f"/api/workshop/{wid}/dispatch/{made['task_seq']}").json()
    assert "steps" in d and "stuck_at" in d, "账本视图缺 steps/stuck_at（两处口径会对不上）"
    assert [x["n"] for x in d["steps"]] == [x["n"] for x in made["steps"]]
    assert d["stuck_at"] == made["stuck_at"]
    assert d["manual_required"] == made["manual_required"]
