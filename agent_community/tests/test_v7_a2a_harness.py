# -*- coding: utf-8 -*-
"""守卫：**A2A 作为接入形态**（老板 2026-11-01：「加入注册功能中啊，明明是最适配的你不加」）。

守的是"最适配"这句话落到实处没有 —— A2A 天生带任务语义与回执，所以它应该：
  ① 能被注册（`wakeup_method=a2a` 不要求 acp_command，也不需要平台拉起它）；
  ② 检测时能读它的 Agent Card（两种标准路径都试）；
  ③ 派活走 **A2A 的 `tasks/send` 信封**（JSON-RPC），不是普通 HTTP POST；
  ④ 能进组长候选（只要它 `fs_write` 也过）。

全部真跑：真起假 A2A 对端（校验它到底收到了什么信封）、真 monkeypatch harness 表。
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

CARD = {"name": "示例A2A-对端", "description": "假对端", "capabilities": {}, "skills": []}


@pytest.fixture()
def fake_a2a():
    """起一个假 A2A 对端：GET 给卡（只暴露 v0.2 兼容路径），POST 收 tasks/send。"""
    seen: dict = {}

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/.well-known/agent.json":        # 刻意只给 v0.2 路径（考兼容）
                b = json.dumps(CARD).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(b)))
                self.end_headers()
                self.wfile.write(b)
            else:
                self.send_response(404)
                self.end_headers()

        def do_POST(self):
            n = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(n).decode("utf-8", "replace")
            seen["body"] = raw
            b = json.dumps({"jsonrpc": "2.0", "id": json.loads(raw).get("id"),
                            "result": {"id": "task-a2a-1", "status": "submitted"}}).encode()
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
        yield "http://127.0.0.1:%d" % srv.server_port, seen
    finally:
        srv.shutdown()


def _patch_harness(monkeypatch, url: str, *, wakeup: str = "a2a", transports=None):
    """把 `server.harness_manager.sessions` 换成一个假的（dispatch 里就是从这里查的）。

    ⚠️ 注意：class body 里**引用不到同名的函数参数**（`transports = transports` 会 NameError），
    所以先在函数作用域把值绑成局部变量，再在类体里引用它们。
    """
    from agent_community.platform import server as S

    _wm, _url, _tr = wakeup, url, list(transports or [])

    class _Info:
        wakeup_method = _wm
        api_base_url = _url
        transports = _tr

    class _Sess:
        info = _Info()
        wakeup_url = ""
        callback_url = ""

    class _HM:
        sessions = {"h-a2a": _Sess()}

    monkeypatch.setattr(S, "harness_manager", _HM(), raising=False)


# ── ① 注册不要求 acp_command ─────────────────────────────────────
def test_a2a_registration_does_not_require_acp_command():
    """A2A 是对等远程 agent，平台**不拉起它** ⇒ 不能要求 `acp_command`。

    （实测踩过：`_need_acp` 表里没 a2a ⇒ 默认从严 ⇒ 注册 400 `acp_command 不能为空`。）
    """
    from agent_community.platform.protocol import WakeupMethod
    from agent_community.platform.routers import harness_register as HR  # noqa: F401

    # 直接读那张表的判定：a2a 必须落 False（不需要拉起命令）
    src = Path(_ROOT / "agent_community/platform/routers/harness_register.py").read_text(encoding="utf-8")
    assert "WakeupMethod.A2A: False" in src, "`_need_acp` 表里必须显式把 A2A 标成不需要拉起"
    assert WakeupMethod.A2A.value == "a2a"


# ── ② 三处枚举都认 a2a ──────────────────────────────────────────
def test_a2a_is_recognized_everywhere():
    from agent_community.platform import card_conformance as CC
    from agent_community.platform import lead_roster as LR
    from agent_community.platform.protocol import TransportType, WakeupMethod

    assert "a2a" in [m.value for m in WakeupMethod]
    assert "a2a" in [t.value for t in TransportType]
    assert "a2a" in CC.WAKEUP_METHODS, "不认 a2a ⇒ 卡片一致性检查会把 A2A 判成'不认识的叫醒方式'"
    assert "a2a" in LR.PROTOCOL_WAKEUPS, "不认 a2a ⇒ A2A 会被误判'没有回执通道'，进不了组长候选"


# ── ③ 派活必须发 A2A 信封（不是普通 POST）────────────────────────
def test_activate_sends_a2a_envelope_not_plain_post(monkeypatch, fake_a2a):
    url, seen = fake_a2a
    from agent_community.platform import dispatch as D

    _patch_harness(monkeypatch, url)
    assert D._harness_is_a2a("h-a2a") is True, "wakeup_method=a2a 必须被认出来"
    r = D.activate("/tmp", "_lead", harness_id="h-a2a", tier="a_protocol",
                   task_id="T-007", task_rel="_lead/任务/T-007.md")
    assert r.get("method") == "a2a", r
    assert r.get("result") == "ok", r
    body = json.loads(seen.get("body") or "{}")
    assert body.get("jsonrpc") == "2.0", "必须是 JSON-RPC 2.0 信封"
    assert body.get("method") == "tasks/send", "A2A 的任务投递方法是 tasks/send"
    text = json.dumps(body.get("params") or {}, ensure_ascii=False)
    assert "parts" in text and "T-007" in text, "任务要放进 message.parts"


def test_non_a2a_harness_still_uses_plain_post(monkeypatch, fake_a2a):
    """不是 A2A 的（比如 http_api）不能被误改成 A2A 信封（避免把普通对接打坏）。"""
    url, _seen = fake_a2a
    from agent_community.platform import dispatch as D

    _patch_harness(monkeypatch, url, wakeup="http_api", transports=[])
    assert D._harness_is_a2a("h-a2a") is False


def test_transports_declared_a2a_is_also_recognized(monkeypatch, fake_a2a):
    """按 `http_api` 注册、但在 `transports` 里声明了 a2a 的，也算 A2A。"""
    url, _seen = fake_a2a
    from agent_community.platform import dispatch as D

    _patch_harness(monkeypatch, url, wakeup="http_api", transports=["a2a", "http"])
    assert D._harness_is_a2a("h-a2a") is True


# ── ④ A2A 能进组长候选（前提是 fs_write 也过）────────────────────
def test_a2a_counts_as_protocol_channel_for_lead():
    """组长门槛 = `fs_write` ＋（`protocol_call` 或 `bridge_poll`）。

    A2A 必须算"有协议通道"——否则最适配的接入反而当不了组长。
    """
    from agent_community.platform import lead_roster as LR

    assert "a2a" in LR.PROTOCOL_WAKEUPS


# ── ⑤ 真对端兼容（2026-11-01 实测补的洞）──────────────────────────
# 起因：本机装的是 `@hanphone/dsh-a2a` 0.5.2（A2A v1.0.1 实现），拿平台客户端去连它，
# 三处都撞上，而且每一处都会把"对端完全正常"误判成"对端不通"：
#   ① 卡挂在 `<基址>/agent-card.json`（不套 `.well-known/`）；
#   ② RPC 端点只认基址本身（带尾斜杠回 404）；
#   ③ 只认 v1.0 方法名（`tasks/send` 回 -32601）。
CARD_V1 = {"name": "示例A2A-v1.0-对端", "description": "卡挂在端点基址上",
           "capabilities": {}, "skills": []}


@pytest.fixture()
def fake_a2a_v1():
    """模拟 v1.0 口径的第三方实现（卡在基址、端点无尾斜杠、只认 SendMessage/GetTask）。"""
    seen: dict = {"methods": [], "params": {}}

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/agent-card.json":
                b = json.dumps(CARD_V1, ensure_ascii=False).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(b)))
                self.end_headers()
                self.wfile.write(b)
            else:
                self.send_response(404)
                self.end_headers()

        def do_POST(self):
            n = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(n).decode("utf-8", "replace")
            if self.path != "/":            # 只认基址本身（带尾斜杠就是 404）
                self.send_response(404)
                self.end_headers()
                return
            req = json.loads(raw)
            seen["methods"].append(req.get("method"))
            seen["params"] = req.get("params") or {}
            if req.get("method") == "tasks/send":
                out = {"jsonrpc": "2.0", "id": req.get("id"),
                       "error": {"code": -32601, "message": "Unknown method tasks/send"}}
            elif req.get("method") == "SendMessage":
                out = {"jsonrpc": "2.0", "id": req.get("id"),
                       "result": {"task": {"id": "t-1", "status": {"state": "TASK_STATE_WORKING"}}}}
            elif req.get("method") == "GetTask":
                out = {"jsonrpc": "2.0", "id": req.get("id"),
                       "result": {"id": "t-1", "status": {"state": "TASK_STATE_COMPLETED"}}}
            else:
                out = {"jsonrpc": "2.0", "id": req.get("id"),
                       "error": {"code": -32601, "message": "unknown method"}}
            b = json.dumps(out, ensure_ascii=False).encode()
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
        yield "http://127.0.0.1:%d" % srv.server_port, seen
    finally:
        srv.shutdown()


def test_client_reads_card_on_endpoint_base(fake_a2a_v1):
    """卡挂在端点基址上（`<基址>/agent-card.json`）也要读得到。"""
    from agent_community import a2a_client as C

    base, _seen = fake_a2a_v1
    card = C.discover_card(base)
    assert card["name"] == CARD_V1["name"]


def test_client_falls_back_to_v1_method(fake_a2a_v1):
    """对端不认 `tasks/send` ⇒ 自动改发 v1.0 `SendMessage`，并且不卡在"等它跑完"。"""
    from agent_community import a2a_client as C

    base, seen = fake_a2a_v1
    res = C.send_task(base, "只回复：收到", None)
    assert seen["methods"][:2] == ["tasks/send", "SendMessage"], seen["methods"]
    assert "task" in res, res
    assert seen["params"]["configuration"]["returnImmediately"] is True


def test_client_get_task_uses_v1_id_param(fake_a2a_v1):
    """v1.0 的 `GetTask` 参数是 `id`（v0.2 是 `taskId`）—— 翻错就是"查不到"。"""
    from agent_community import a2a_client as C

    base, seen = fake_a2a_v1
    C.get_task(base, "t-1", None)
    assert seen["methods"][-1] == "GetTask"
    assert seen["params"] == {"id": "t-1"}


def test_guidance_grade_and_facts_recognize_a2a():
    """注册引导/等级表/接入事实三处都必须认 a2a（规则 20：一处事实，处处对齐）。"""
    from agent_community.platform import bridge_catalog as BC
    from agent_community.platform import interface_guidance as IG
    from agent_community.platform import tier_guidance as TG

    g = IG.for_wakeup("a2a")
    assert "A2A" in g["label"]
    assert g["steps"][IG.STEP_BRIDGE] == IG.NOT_APPLICABLE, "A2A 不需要桥"
    assert g["inbox"] != IG.INBOX["nowhere"], "A2A 不该被说成'没人取'"
    assert IG.match_for("a2a")["grade"] == "S"
    assert BC.match("a2a") is not None

    info = type("I", (), {"wakeup_method": "a2a", "tools": [], "metadata": {}})()
    assert TG.can_be_woken_by_platform(info) is True, "A2A 平台能自动派活"
    assert TG.interface_class(info) != TG.CLASS_UNKNOWN, "A2A 不该被归到'测不出来'"
    assert TG.guidance_at_register(info)["guidance_kind"] == "a2a"


# ── ⑥ 自检（/verify）也不该拿"桥"去量 A2A ─────────────────────────
def test_verify_a2a_reads_card_and_accepts_task(fake_a2a_v1):
    """真机踩过：A2A 走桥那条验证路 ⇒ 报「缺少模板必填字段: WORK_DIR」，
    一个能用的端点被说成自检失败。改成「读卡 + 真发探针再撤回」。"""
    import asyncio

    from agent_community.platform import verify_sandbox as VS

    base, seen = fake_a2a_v1
    info = type("I", (), {"api_base_url": base, "wakeup_url": "", "callback_url": ""})()
    out = asyncio.run(VS._verify_a2a(info, "h-a2a", wait=2))
    assert out["success"] is True, out
    assert out["state"] == "ok"
    assert out["bridge_kind"] is None, "A2A 没有桥"
    assert out["detail"]["accepted"] is True
    assert "SendMessage" in seen["methods"], "应当用 v1.0 方法名派活"
    assert "agent 回话内容不在本次验证范围内" in out["detail"]["channel_note"]


def test_verify_bridge_routes_a2a_away_from_bridge_path(monkeypatch, fake_a2a_v1):
    """`wakeup_method=a2a` 时 `verify_bridge` 必须**不进**建间/架桥分支。"""
    import asyncio

    from agent_community.platform import harness_adapter as HA
    from agent_community.platform import verify_sandbox as VS

    base, _seen = fake_a2a_v1
    info = type("I", (), {"api_base_url": base, "wakeup_method": "a2a",
                          "wakeup_url": "", "callback_url": "", "harness_name": "x"})()

    class _S:
        pass

    sess = _S()
    sess.info = info

    class _HM:
        sessions = {"h-a2a-verify": sess}

    monkeypatch.setattr(HA, "harness_manager", _HM(), raising=False)
    out = asyncio.run(VS.verify_bridge({}, "h-a2a-verify", data_dir=r"C:\tmp"))
    assert out["success"] is True, out
    assert out["bridge_kind"] is None
    assert "WORK_DIR" not in str(out.get("error") or "")
