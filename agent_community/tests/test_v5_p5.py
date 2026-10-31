# -*- coding: utf-8 -*-
"""守卫：**P5 入口**（组长列表 / 对接单 / 命名 N+1）—— `V115` L1~L8。

真跑：真注册 harness、真拼对接单（比两次逐字相同）、真 HTTP。
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.append(str(_ROOT))

from agent_community.platform import kit as K  # noqa: E402
from agent_community.platform import lead_roster as LR  # noqa: E402
from agent_community.platform import v5_roster as roster  # noqa: E402

SLOT = "前端组/员工01"


def _info(*, hid="h1", name="示例Harness", tools=None, wake="mcp", model="m-1", dirs=None):
    ai = SimpleNamespace(model_name=model)
    return SimpleNamespace(harness_id=hid, harness_name=name, tools=list(tools or []),
                           capabilities=[], wakeup_method=SimpleNamespace(value=wake),
                           ai=ai, fit_directions=list(dirs or []))


# ── L4：lead_eligible 是算出来的 ─────────────────────────────────
def test_l4_fs_write_required_for_lead():
    ok = LR.tooling_of(_info(tools=["write_file", "read_file"], wake="mcp"))
    assert ok["fs_write"] is True and LR.eligible(ok) is True
    nofs = LR.tooling_of(_info(tools=["chat"], wake="mcp"))
    assert nofs["fs_write"] is False and LR.eligible(nofs) is False
    unknown = LR.tooling_of(_info(tools=[], wake="mcp"))
    assert unknown["fs_write"] is None and LR.eligible(unknown) is False
    assert "测不到" in unknown["why"], "测不出来要说明为什么"


def test_l4b_protocol_or_bridge_required():
    fs_only_gui = LR.tooling_of(_info(tools=["write_file"], wake="clipboard"))
    assert fs_only_gui["fs_write"] is True and LR.eligible(fs_only_gui) is False, "能写文件但没法回执"
    bridge = LR.tooling_of(_info(tools=["write_file"], wake="file_poll"))
    assert LR.eligible(bridge) is True


def test_l4c_roster_only_lists_eligible_and_says_not_ranked(monkeypatch):
    fake = [("h_b", _info(hid="h_b", tools=["write_file"], wake="mcp")),
            ("h_a", _info(hid="h_a", tools=["chat"], wake="mcp")),
            ("h_c", _info(hid="h_c", tools=[], wake="clipboard"))]
    monkeypatch.setattr(LR, "_harness_infos", lambda: fake, raising=True)
    out = LR.candidates("")
    assert [x["harness_id"] for x in out["candidates"]] == ["h_b"]
    assert {x["harness_id"] for x in out["excluded"]} == {"h_a", "h_c"}
    assert "不是推荐" in out["sort_note"]


def test_l5_direction_miss_falls_back_with_note(monkeypatch):
    fake = [("h1", _info(hid="h1", tools=["write_file"], wake="mcp", dirs=["编程/后端"]))]
    monkeypatch.setattr(LR, "_harness_infos", lambda: fake, raising=True)
    miss = LR.candidates("媒体/视频")
    assert miss["count"] == 1 and "退化列全量" in miss["note"], "方向未命中要退化 + 说清"
    hit = LR.candidates("编程/后端")
    assert hit["count"] == 1 and not hit["note"]


# ── L1/L2/L3：对接单 ────────────────────────────────────────────
def _kit(**kw):
    base = dict(role="组长", workshop_id="WS-T", project_short="报销系统",
                session_label_value="WS-T-报销系统-组长-01", workshop_path="/w/ws1",
                mind_path="/m/A-01", slot_dir="_lead", notice_path="_lead/_notice.md",
                entry_points=["_lead/建模.md"], ignore_zones=["_meta/evidence/"],
                member_field_value="组长", is_lead=True)
    base.update(kw)
    return K.build_kit(**base)


def test_l1_kit_is_byte_identical_twice():
    assert _kit() == _kit(), "同一输入两次拼装必须逐字相同"


def test_l2_kit_has_two_roots_and_no_placeholder():
    text = _kit()
    assert "/w/ws1" in text and "/m/A-01" in text, "两个根要写死"
    assert "Boot Protocol" in text
    for bad in ("{{", "}}", "<workshop_path>", "<mind_path>"):
        assert bad not in text, "不许留占位符：%s" % bad


def test_l3_token_only_when_given_and_never_in_template():
    assert "凭据: （B/C 档留空）" in _kit(), "没给 token 时那一行也要在（有格式）"
    withtok = _kit(credential="abc123", credential_note="一次性")
    assert "凭据: abc123" in withtok and "一次性" in withtok
    assert "abc123" not in _kit(), "模板本身绝不含令牌"


def test_l6_session_label_is_n_plus_one():
    assert K.session_label("WS-T", "报销", "员工01", 3) == "WS-T-报销-员工01-03"
    assert K.session_label("WS-T", "报销", "员工01", 10) == "WS-T-报销-员工01-10"


# ── HTTP 端到端 ─────────────────────────────────────────────────
@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("AC_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("AC_MIND_ROOT", str(tmp_path / "minds"))
    monkeypatch.setattr(roster, "_path", lambda: tmp_path / "data" / "v5_roster.json",
                        raising=True)
    from fastapi.testclient import TestClient

    from agent_community.platform import server as srv

    wp = tmp_path / "ws"
    (wp / "_lead").mkdir(parents=True)
    (wp / "_lead" / "建模.md").write_text("# 建模\n", encoding="utf-8")
    ws = srv.Workshop(workshop_id="WS-T", name="报销系统", workspace_dir=str(wp),
                      hall_content="方向", status="draft")
    srv.workshops["WS-T"] = ws
    return TestClient(srv.app, client=("127.0.0.1", 54321)), wp


def test_http_harness_tooling_and_choose_lead(client):
    c, wp = client
    # 能当组长的那个
    r1 = c.post("/api/harness/register", json={"harness_id": "h_good", "harness_name": "示例Harness-G", "harness_type": "custom",
                 "wakeup_method": "mcp", "tools": [{"name": "write_file"}, {"name": "read_file"}],
                 "ai": {"model_name": "示例模型"}})
    assert r1.status_code == 200, r1.text
    # 只能聊天的那个
    r2 = c.post("/api/harness/register", json={"harness_id": "h_chat", "harness_name": "示例Harness-C", "harness_type": "custom",
                 "wakeup_method": "clipboard", "tools": [{"name": "chat"}],
                 "ai": {"model_name": "示例模型"}})
    assert r2.status_code == 200, r2.text
    tooling = {x["harness_id"]: x for x in c.get("/api/harness-tooling").json()["items"]}
    assert tooling["h_good"]["lead_eligible"] is True
    assert tooling["h_chat"]["lead_eligible"] is False
    bad = c.post("/api/workshop/WS-T/lead", json={"harness_id": "h_chat"})
    assert bad.status_code == 409 and bad.json()["code"] == "NOT_LEAD_ELIGIBLE"
    good = c.post("/api/workshop/WS-T/lead", json={"harness_id": "h_good"})
    assert good.status_code == 200 and good.json()["status"] == "lead_in"
    r = c.get("/api/workshop/WS-T/roster").json()
    assert any(m["slot_dir"] == "_lead" for m in r["members"]), "组长要落成一个工位"


def test_http_lead_kit_and_member_kit(client):
    c, wp = client
    c.post("/api/harness/register", json={"harness_id": "h_good", "harness_name": "示例Harness-G",
                                     "harness_type": "custom", "wakeup_method": "mcp",
                                     "tools": [{"name": "write_file"}], "ai": {"model_name": "示例模型"}})
    c.post("/api/workshop/WS-T/lead", json={"harness_id": "h_good"})
    k1 = c.get("/api/workshop/WS-T/lead-kit?format=json&agent_key=A-01").json()
    k2 = c.get("/api/workshop/WS-T/lead-kit?format=json&agent_key=A-01").json()
    assert k1["ok"] and k1["kit"] == k2["kit"], "两次拼装必须一致"
    assert str(wp) in k1["kit"] and k1["mind_path"] in k1["kit"]
    assert k1["has_token"] is False, "没要 token 就不许签"
    tok = c.get("/api/workshop/WS-T/lead-kit?format=json&agent_key=A-01&issue_token=1").json()
    assert tok["has_token"] is True and "凭据:" in tok["kit"]
    # 成员 kit：命名 N+1
    roster.add_slots("WS-T", [{"岗位": SLOT, "harness": "h_good", "model": "m", "人数": 1}],
                     source="C")
    _members = c.get("/api/workshop/WS-T/roster").json()["members"]
    seq = next(m["member_seq"] for m in _members if m["slot_dir"] == SLOT)
    mk = c.post("/api/workshop/WS-T/members/%d/kit" % seq, json={}).json()
    assert mk["ok"] and mk["slot_dir"] == SLOT
    assert mk["session_label"].endswith("-%02d" % seq)
    nl = c.get("/api/workshop/WS-T/next-label?role=员工02").json()
    assert nl["next_seq"] > seq and nl["session_label"].endswith("%02d" % nl["next_seq"])

# ── 不合格必须给**具体**原因（界面只显示这一句；空着等于没说）──────────
def test_why_always_present_when_not_eligible():
    """三种缺法各有各的说法，且都不为空。"""
    cases = {
        "没报工具名单": (_info(tools=[], wake="clipboard"), "测不到"),
        "报了工具但没文件操作": (_info(tools=["chat"], wake="mcp"), "没有文件操作"),
        "能写但没回执通道": (_info(tools=["write_file"], wake="clipboard"), "没有回执通道"),
    }
    for name, (info, expect) in cases.items():
        tool = LR.tooling_of(info)
        assert LR.eligible(tool) is False, name
        assert tool["why"], "%s 的 why 不能是空的（界面会显示「平台没给原因」）" % name
        assert expect in tool["why"], "%s 的原因里要说清缺什么：%r" % (name, tool["why"])
    ok = LR.tooling_of(_info(tools=["write_file"], wake="mcp"))
    assert LR.eligible(ok) is True and ok["why"] == "", "合格的就不用给原因"


def test_why_recomputed_after_measured_override():
    """实测说能写之后，原因要换成「没有回执通道」那条，不能还留「测不到」。"""
    class _W:
        value = "clipboard"

    class _Info:
        tools = []
        capabilities = []
        wakeup_method = _W()
        harness_name = "示例"
        ai = None
        fit_directions = []

    tool = LR.tooling_of(_Info())
    assert "测不到" in tool["why"]
    merged = LR._merge_measured("h_x", dict(tool, fs_write=True, fs_write_source="measured"))
    assert merged["fs_write"] is True and LR.eligible(merged) is False
    assert "没有回执通道" in merged["why"] and "测不到" not in merged["why"]
