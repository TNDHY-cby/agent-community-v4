# -*- coding: utf-8 -*-
"""守卫（`V132`，老板 2026-11-06「做多协议选中适合的功能」）：**多选之后到底能干什么**。

判据全部是"真结果"，不查源码文本：

  ① 映射表自洽：向导层 7 个协议 → `TransportType` 只出现合法值（或 `None`＝表达不了）；
  ② **多选载荷归一化**：`http_api` / `acp` / `file_poll` / `clipboard` 不再被塞进 `transports`
     （这正是 2026-11-06 那个 400 的根因）；全量记进 `protocols`；
  ③ `HarnessInfo` 真能收下归一化后的多选体（`a2a + http_api` 旧行为是 400）；
  ④ 计划：`a2a + mcp` ⇒ 派活走 a2a，且明说 MCP 不能派活；
  ⑤ 计划：只有 mcp ⇒ `dispatch_via=none` + 说清为什么（不假装能派活）；
  ⑥ 计划：`a2a + acp` 缺 `acp_command` ⇒ 合并必填里有两边的字段，acp 那条 `usable=false`；
  ⑦ 计划：`http + http_api` ⇒ 报"同层重复"；
  ⑧ 计划：首选 `http` 但集合里有 `a2a` ⇒ 派活走 a2a + 诚实提示，**但注册载荷仍尊重用户首选**；
  ⑨ 端点：未知协议 id ⇒ 400 + 列出可用值（`POST /api/docking/protocol-plan`）；
  ⑩ 端点：真注册一份多选档案 ⇒ 200，且读回 `protocols` / `transports` 与计划一致。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.append(str(_ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from agent_community.platform import protocol_plan as plan  # noqa: E402
from agent_community.platform import protocol_tree as tree  # noqa: E402
from agent_community.platform import server as srv  # noqa: E402
from agent_community.platform.protocol import HarnessInfo, TransportType  # noqa: E402

C = TestClient(srv.app, client=("127.0.0.1", 54321))

FIELDS_OK = {"harness_id": "示例Harness-A", "api_base_url": "http://127.0.0.1:9/a2a/x",
             "acp_command": "示例命令 --noop", "ai": {"model_name": "示例模型"}}


def test_01_mapping_table_is_self_consistent():
    legal = {t.value for t in TransportType}
    for pid in tree.PROTOCOL_IDS:
        t = tree.TRANSPORT_BY_ID.get(pid)
        assert t is None or t in legal, "%s 映射到了非法传输类型 %r" % (pid, t)
    # 能派活的集合必须非空，且 a2a 在里头（否则"闭环"永远不可能成立）
    assert "a2a" in tree.DISPATCH_ORDER


def test_02_normalize_keeps_wizard_ids_out_of_transports():
    body = plan.normalize_register_body({
        "harness_id": "示例Harness-A", "wakeup_method": "a2a",
        "protocols": ["a2a", "http_api", "acp", "file_poll", "clipboard"],
        "transports": ["a2a", "http_api", "acp", "file_poll", "clipboard"],
        "preferred_transport": "http_api", "ai": {"model_name": "示例模型"}})
    legal = {t.value for t in TransportType}
    assert set(body["transports"]) <= legal, body["transports"]
    assert body["protocols"][0] == "a2a", body["protocols"]
    assert set(body["protocols"]) == {"a2a", "http_api", "acp", "file_poll", "clipboard"}
    assert body.get("preferred_transport") == "http", "http_api 应映射成 http"


def test_03_harness_info_accepts_normalized_multi_protocol_body():
    body = plan.normalize_register_body({
        "harness_id": "示例Harness-A", "harness_name": "示例Harness-A",
        "wakeup_method": "a2a", "protocols": ["a2a", "http_api"],
        "transports": ["a2a", "http_api"], "api_base_url": "http://127.0.0.1:9/a2a/x",
        "ai": {"model_name": "示例模型"}})
    info = HarnessInfo(**body)          # 旧行为：ValidationError（transports.1 非法）
    assert [t.value for t in info.transports] == ["a2a", "http"]
    assert info.protocols == ["a2a", "http_api"]


def test_04_plan_a2a_plus_mcp_dispatch_via_a2a_and_says_mcp_cannot():
    p = plan.build_plan(["a2a", "mcp"], FIELDS_OK)
    assert p["route"]["dispatch_via"] == "a2a", p["route"]
    # ★本质断言：派活路线绝不可能是 mcp，且必须有一条"工具面"的说明（措辞不写死）
    assert "mcp" not in str(p["route"]["dispatch_via"]).lower(), p["route"]
    assert any("MCP" in w and "工具面" in w for w in p["warnings"]), p["warnings"]
    assert p["capabilities"]["closed_loop"] is True
    assert p["capabilities"]["can_call_our_tools"] is True


def test_05_plan_mcp_only_is_honest_none():
    p = plan.build_plan(["mcp"], FIELDS_OK)
    assert p["route"]["dispatch_via"] == "none", p["route"]
    assert p["route"]["why"].strip()
    assert p["capabilities"]["can_dispatch"] is False
    assert p["capabilities"]["closed_loop"] is False


def test_06_plan_merges_required_and_marks_only_the_missing_one():
    fields = {"harness_id": "示例Harness-A", "api_base_url": "http://127.0.0.1:9/a2a/x",
              "ai": {"model_name": "示例模型"}}          # 故意不给 acp_command
    p = plan.build_plan(["a2a", "acp"], fields)
    assert "acp_command" in p["merged_required"]
    assert "api_base_url" in p["merged_required"]
    by = {x["id"]: x for x in p["picked"]}
    assert by["acp"]["usable"] is False and by["acp"]["missing"] == ["acp_command"]
    assert by["a2a"]["usable"] is True
    # 缺必填的那条不参与"整体能力"的汇总（否则就是假能力）
    assert p["capabilities"]["can_dispatch"] is True       # a2a 还能派活
    assert any("没有一条现在能用" in w or "acp_command" in w for w in p["warnings"])


def test_07_plan_flags_same_channel_duplicate():
    p = plan.build_plan(["http", "http_api"], FIELDS_OK)
    assert any(d.get("kind") == "same_channel" for d in p["duplicates"]), p["duplicates"]


def test_08_plan_keeps_user_preference_but_warns_route_change():
    p = plan.build_plan(["http", "a2a"], FIELDS_OK)
    assert p["route"]["dispatch_via"] == "a2a"
    assert p["route"]["honest_note"].strip(), "首选不能派活时必须说明派活实际走哪条"
    assert p["register_payload"]["wakeup_method"] == "http", "不许替用户改首选"


def test_09_endpoint_rejects_unknown_protocol_with_available_list():
    r = C.post("/api/docking/protocol-plan", json={"ids": ["a2a", "nope"]})
    assert r.status_code == 400, r.text
    body = r.json()
    assert body.get("code") == "UNKNOWN_PROTOCOL"
    assert "a2a" in (body.get("available") or [])
    # 空 ids 也要拒（不是返回空计划假装成功）
    r2 = C.post("/api/docking/protocol-plan", json={"ids": []})
    assert r2.status_code == 400 and r2.json().get("code") == "BAD_PROTOCOLS"


def test_10_endpoint_returns_plan_and_register_accepts_payload():
    r = C.post("/api/docking/protocol-plan",
               json={"ids": ["a2a", "http_api"], "fields": FIELDS_OK})
    assert r.status_code == 200, r.text
    body = r.json()
    payload = body["register_payload"]
    assert payload["transports"] == ["a2a", "http"], payload

    reg = dict(payload)
    reg.update({"harness_id": "多选守卫-示例", "harness_name": "多选守卫-示例",
                "api_base_url": "http://127.0.0.1:9/a2a/x",
                "ai": {"model_name": "示例模型"}})
    rr = C.post("/api/harness/register", json=reg)
    assert rr.status_code == 200, rr.text
    assert rr.json().get("success") is True or rr.json().get("ok") is True, rr.text
    info = srv.harness_manager.sessions.get("多选守卫-示例").info
    assert info.protocols == ["a2a", "http_api"], info.protocols
    assert [t.value for t in info.transports] == ["a2a", "http"], info.transports


# ── ★2026-11-06（老板：「clipboard 是不是可以用视觉脚本，记得说明」）──────
def test_11_clipboard_declares_visual_script_and_plan_keeps_honest():
    """`clipboard` 开箱叫不醒，但**配了视觉脚本能自动投递**；计划必须同时说清"送进去 ≠ 干完了"。"""
    clip = tree.BY_ID["clipboard"]
    assert clip["can_be_woken"] is False, "开箱事实：没有脚本时叫不醒"
    assert clip.get("visual_script") is True

    p = plan.build_plan(["clipboard"], FIELDS_OK)
    caps = p["capabilities"]
    assert caps["can_wake_with_script"] is True, caps
    assert caps["can_dispatch"] is False, "视觉脚本只能投递一句话，不是任务语义派活"
    assert caps["closed_loop"] is False
    text = json.dumps(p, ensure_ascii=False)
    assert "视觉脚本" in text and ("核不到" in text or "结果仍要人" in text), text[:400]


def test_12_dispatch_route_is_visual_only_when_script_ready():
    """有脚本 ⇒ `visual`；没脚本 ⇒ `none` 且指路去「脚本激活」工作台。"""
    ready = HarnessInfo(harness_id="示例-视觉", harness_name="示例-视觉",
                        wakeup_method="clipboard", protocols=["clipboard"],
                        activation_script={"text": "点输入框粘一句", "title": "示例窗口"},
                        ai={"model_name": "示例模型"})
    route, why = plan.dispatch_route(ready)
    assert route == "visual", (route, why)
    assert "核不到" in why, why

    no_script = HarnessInfo(harness_id="示例-无脚本", harness_name="示例-无脚本",
                            wakeup_method="clipboard", protocols=["clipboard"],
                            ai={"model_name": "示例模型"})
    route2, why2 = plan.dispatch_route(no_script)
    assert route2 == "none"
    assert "视觉脚本" in why2 and "脚本激活" in why2, why2


def test_13_visual_activate_really_delivers_but_never_claims_done(monkeypatch):
    """派活到视觉档：**真投一句话**（用脚本的标题定位），但 `result` 不许是 `ok`。"""
    from agent_community.platform import delivery as dl
    from agent_community.platform import dispatch as dp

    info = HarnessInfo(harness_id="示例-视觉", harness_name="示例-视觉",
                       wakeup_method="clipboard", protocols=["clipboard"],
                       activation_script={"text": "点输入框粘一句", "title": "示例窗口"},
                       ai={"model_name": "示例模型"})
    monkeypatch.setattr(dp, "_harness_info", lambda hid: info)
    called = {}

    def _fake(name, text, **kw):
        called["name"], called["text"] = name, text
        return {"ok": True, "verified": True, "channel": "native"}

    monkeypatch.setattr(dl, "deliver_by_name", _fake)
    out = dp.activate("示例工作间目录", "前端组/员工01", harness_id="示例-视觉",
                      tier="c_visual", task_id="T-003",
                      task_rel="前端组/员工01/任务/T-003.md")
    assert called["name"] == "示例窗口", "必须用脚本里的标题/关键词去定位窗口"
    assert "T-003" in called["text"]
    assert out["method"] == "visual", out
    assert out["result"] != "ok", "投递成功 ≠ 派活成功（不许假成功）"
    assert out["delivered"] is True and out["needs_human"] is True
    assert "核不到" in out["detail"], out["detail"]


# ── ★2026-11-06（老板：「不止把话送进去吧，它能把消息发给组长吗」）────────
def test_14_send_text_can_send_any_text_but_cannot_read_reply(monkeypatch):
    """能把**任意一段话**（对接单 / 通知）送进窗口；但口径必须写清"读不到回复"。"""
    from agent_community.platform import delivery as dl
    from agent_community.platform import visual_send as vs

    info = HarnessInfo(harness_id="示例-组长", harness_name="示例-组长",
                       wakeup_method="clipboard", protocols=["clipboard"],
                       activation_script={"text": "点输入框粘一句", "title": "示例组长窗口"},
                       ai={"model_name": "示例模型"})
    got = {}

    def _fake(name, text, **kw):
        got["name"], got["text"] = name, text
        return {"ok": True, "verified": True, "channel": "native"}

    monkeypatch.setattr(dl, "deliver_by_name", _fake)
    out = vs.send_text(info, "对接单：岗位 前端组/员工01；token 见附件")
    assert got["name"] == "示例组长窗口"
    assert "对接单" in got["text"], "送的是调用方给的原文，不是固定话术"
    assert out["ok"] is True and out["delivered"] is True and out["verified"] is True
    assert "读不到" in out["note"] and "回复" in out["note"], out["note"]


def test_15_send_text_refuses_without_script_and_gives_next_step():
    from agent_community.platform import visual_send as vs

    info = HarnessInfo(harness_id="示例-无脚本2", harness_name="示例-无脚本2",
                       wakeup_method="clipboard", protocols=["clipboard"],
                       ai={"model_name": "示例模型"})
    out = vs.send_text(info, "随便一句话")
    assert out["ok"] is False and out["code"] == "NO_VISUAL_SCRIPT"
    assert out["next"] and "脚本激活" in out["next"]
    assert "读不到" in out["note"]


def test_16_send_text_endpoint_guards():
    """端点：不存在的接入 404；空文本 400；都不假装成功。"""
    r = C.post("/api/docking/harness/不存在的接入/send-text", json={"text": "x"})
    assert r.status_code == 404, r.text
    assert r.json().get("code") == "NO_SUCH_HARNESS"

    reg = {"harness_id": "视觉守卫-示例", "harness_name": "视觉守卫-示例",
           "wakeup_method": "clipboard", "protocols": ["clipboard"],
           "ai": {"model_name": "示例模型"}}
    rr = C.post("/api/harness/register", json=reg)
    assert rr.status_code == 200, rr.text
    r2 = C.post("/api/docking/harness/视觉守卫-示例/send-text", json={"text": "   "})
    assert r2.status_code == 400 and r2.json().get("code") == "TEXT_REQUIRED", r2.text
