"""会话建立与命名（老板流程 步骤 4/7/8/9）测试。

两条路径都要钉死：
  · **没配脚本** → 仍然给出名字 + 人工指引，**不报错但如实标未核实**（basic function 可用）；
  · **配了脚本** → 自动建会话/改名并**自证**，把 evidence 落进成员的 session_ref。
附带钉住一个本轮修掉的真问题：成员 `entry_state`（派发进度）此前**不落盘**，重启即丢。
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from agent_community.platform.workshop import Workshop, WorkshopMember  # noqa: E402


def run(coro):
    return asyncio.run(coro)


class _FakeInfo:
    """真 `info` 是 pydantic 模型；`save_state()` 会调 `info.model_dump()`，
    所以替身**必须有这个方法** —— 否则测试里会冒出与用例无关的 AttributeError
    （「假替身形状不对」这个坑本项目踩过，故这里显式补齐）。"""

    def __init__(self, hid="h1", name="示例Harness-A"):
        self.harness_id = hid
        self.harness_name = name
        self.metadata = {}

    def model_dump(self) -> dict:
        return {"harness_id": self.harness_id, "harness_name": self.harness_name}


class _FakeSess:
    """够用就好的 harness 会话替身：只需要 metadata / info。"""

    def __init__(self, hid="h1", meta=None):
        self.info = _FakeInfo(hid)
        self.metadata = dict(meta or {})


def _mkws(tmp_path, members=None, name="做一个数据看板"):
    ws = Workshop(workshop_id="w7f2a1b3", name=name, workspace_dir=str(tmp_path / "ws_1"),
                  hall_content="做一个数据看板", created_at="now",
                  members=members or [WorkshopMember(member_id="m1", role="码农",
                                                     display_name="小码", harness_ids=["h1"])])
    return ws


class TestSessionNameEndpoint:
    def test_404s(self, tmp_path, monkeypatch):
        import agent_community.platform.routers.workshops_session as ws_mod
        monkeypatch.setattr(ws_mod, "workshops", {}, raising=False)
        from agent_community.platform.routers.workshops_session import member_session_name
        assert run(member_session_name("nope", "m1")).status_code == 404
        ws = _mkws(tmp_path)
        monkeypatch.setattr(ws_mod, "workshops", {"w7f2a1b3": ws}, raising=False)
        assert run(member_session_name("w7f2a1b3", "nope")).status_code == 404

    def test_returns_name_with_project(self, tmp_path, monkeypatch):
        import agent_community.platform.routers.workshops_session as ws_mod
        from agent_community.platform.routers.workshops_session import member_session_name
        ws = _mkws(tmp_path)
        monkeypatch.setattr(ws_mod, "workshops", {"w7f2a1b3": ws}, raising=False)
        out = run(member_session_name("w7f2a1b3", "m1"))
        assert out["name"] == "w7f2a1b3-做一个数据看板-码农-m1"
        assert out["project"] == "做一个数据看板"


class TestEstablishSessionManualMode:
    """**没配脚本也必须可用**：给名字 + 人工指引，且明确"未核实"。"""

    def _call(self, tmp_path, monkeypatch, body=None):
        import agent_community.platform.routers.workshops_session as ws_mod
        from agent_community.platform.routers.workshops_session import establish_session
        from agent_community.platform import server as srv
        ws = _mkws(tmp_path)
        monkeypatch.setattr(ws_mod, "workshops", {"w7f2a1b3": ws}, raising=False)
        monkeypatch.setattr(srv.harness_manager, "sessions", {"h1": _FakeSess()}, raising=False)
        req = type("R", (), {"json": staticmethod(lambda: asyncio.sleep(0, result=body or {}))})()
        out = run(establish_session("w7f2a1b3", "m1", req))
        return out, ws

    def test_manual_steps_are_not_failures(self, tmp_path, monkeypatch):
        out, _ = self._call(tmp_path, monkeypatch)
        assert out["success"] is True
        assert out["all_verified"] is False
        assert [m["step"] for m in out["manual"]] == [4, 8], "两个动作没配脚本 ⇒ 都给人工指引"
        for key in ("4_new_session", "8_rename_session"):
            assert out["steps"][key]["ok"] is True, "人工模式不是失败"
            assert out["steps"][key]["verified"] is False, "人工模式**必须**未核实"
        assert "未核实" in out["note"]

    def test_name_is_persisted_and_first_in_aliases(self, tmp_path, monkeypatch):
        out, ws = self._call(tmp_path, monkeypatch)
        m = ws.members[0]
        assert m.session_aliases[0] == out["name"] == "w7f2a1b3-做一个数据看板-码农-m1"

    def test_custom_new_name_is_respected_but_generated_stays_first(self, tmp_path, monkeypatch):
        out, ws = self._call(tmp_path, monkeypatch, body={"new_name": "我自己起的名字"})
        m = ws.members[0]
        assert "我自己取的名字".replace("取", "起") in m.session_aliases
        assert m.session_aliases[0] == "w7f2a1b3-做一个数据看板-码农-m1", \
            "平台生成的名字是身份凭据，必须留在首位"

    def test_scripts_are_rendered_into_member_compartment(self, tmp_path, monkeypatch):
        out, _ = self._call(tmp_path, monkeypatch)
        sdir = Path(out["scripts_dir"])
        assert (sdir / "new_session.py").is_file()
        assert (sdir / "rename_session.py").is_file()
        assert "码农_m1" in str(sdir), "一员工一份：脚本必须落在他自己的隔间里"

    def test_idempotent_repeat(self, tmp_path, monkeypatch):
        o1, ws = self._call(tmp_path, monkeypatch)
        o2, _ = self._call(tmp_path, monkeypatch)
        assert o1["name"] == o2["name"]
        assert ws.members[0].session_aliases.count(o2["name"]) == 1, "重复调用不得堆积别名"

    def test_audit_recorded(self, tmp_path, monkeypatch):
        from agent_community.platform.audit import audit_log
        self._call(tmp_path, monkeypatch)
        evs = audit_log.query(limit=5, event="workshop.establish_session", target="w7f2a1b3")
        assert evs and evs[0]["member_id"] == "m1" and evs[0]["verified"]["4_new_session"] is False


class TestEstablishSessionWithScripts:
    """**配了脚本**：自动建会话 + 改名，并把 evidence 落进 session_ref。"""

    def _fake_cmd(self, tmp_path: Path, payload_json: str) -> str:
        helper = tmp_path / f"harness_{abs(hash(payload_json)) % 9999}.py"
        helper.write_text("import json\n"
                          f"print(json.dumps({payload_json}))\n", encoding="utf-8")
        return f'"{sys.executable}" "{helper}"'

    def _call(self, tmp_path, monkeypatch, scripts, body=None):
        import agent_community.platform.routers.workshops_session as ws_mod
        from agent_community.platform.routers.workshops_session import establish_session
        from agent_community.platform import server as srv
        from agent_community.platform import action_scripts as acts
        ws = _mkws(tmp_path)
        sess = _FakeSess()
        acts.set_config(sess, "new_session",
                        {"NEW_SESSION_CMD": scripts[0], "HARNESS_NAME": "示例Harness-A"})
        acts.set_config(sess, "rename_session",
                        {"RENAME_CMD": scripts[1], "HARNESS_NAME": "示例Harness-A"})
        monkeypatch.setattr(ws_mod, "workshops", {"w7f2a1b3": ws}, raising=False)
        monkeypatch.setattr(srv.harness_manager, "sessions", {"h1": sess}, raising=False)
        req = type("R", (), {"json": staticmethod(lambda: asyncio.sleep(0, result=body or {}))})()
        out = run(establish_session("w7f2a1b3", "m1", req))
        return out, ws

    def test_both_steps_verified_and_ref_persisted(self, tmp_path, monkeypatch):
        new_cmd = self._fake_cmd(tmp_path, '{"session": {"id": "sess-777", "name": "随机名"}}')
        ren_cmd = self._fake_cmd(tmp_path, '{"applied_name": "__NAME__"}')
        # 改名脚本要回报"平台要求的新名" ⇒ 用真实名字动态生成
        from agent_community.platform.action_scripts import session_name_for
        ws = _mkws(tmp_path)
        want = session_name_for(ws, ws.members[0])
        ren = tmp_path / "ren.py"
        ren.write_text(f"import json\nprint(json.dumps({{'applied_name': {want!r}}}))\n",
                       encoding="utf-8")
        out, ws2 = self._call(tmp_path, monkeypatch,
                             (new_cmd, f'"{sys.executable}" "{ren}"'))
        assert out["verified"]["4_new_session"] is True
        assert out["verified"]["8_rename_session"] is True
        assert out["all_verified"] is True
        assert out["manual"] == []
        assert ws2.members[0].session_ref.get("id") == "sess-777", "建会话的 evidence 要落进 session_ref"

    def test_rename_mismatch_stays_unverified(self, tmp_path, monkeypatch):
        new_cmd = self._fake_cmd(tmp_path, '{"session": {"id": "s1"}}')
        ren = tmp_path / "ren_bad.py"
        ren.write_text('import json;print(json.dumps({"applied_name": "完全不一样"}))', encoding="utf-8")
        out, _ = self._call(tmp_path, monkeypatch, (new_cmd, f'"{sys.executable}" "{ren}"'))
        assert out["all_verified"] is False
        assert out["steps"]["8_rename_session"]["verified"] is False
        assert "未核实" in out["steps"]["8_rename_session"]["error"] or \
               "不一致" in out["steps"]["8_rename_session"]["error"]


class TestDeliverBridge:
    """步骤 5：把「具体桥 + 激活指令」送进该成员的会话。"""

    def _call(self, tmp_path, monkeypatch, scripts=None, body=None):
        import agent_community.platform.routers.workshops_session as ws_mod
        from agent_community.platform.routers.workshops_session import deliver_bridge
        from agent_community.platform import server as srv
        from agent_community.platform import action_scripts as acts
        ws = _mkws(tmp_path)
        sess = _FakeSess()
        if scripts:
            acts.set_config(sess, "deliver", scripts)
        monkeypatch.setattr(ws_mod, "workshops", {"w7f2a1b3": ws}, raising=False)
        monkeypatch.setattr(srv.harness_manager, "sessions", {"h1": sess}, raising=False)
        req = type("R", (), {"json": staticmethod(lambda: asyncio.sleep(0, result=body or {}))})()
        return run(deliver_bridge("w7f2a1b3", "m1", req)), ws

    def test_404s(self, tmp_path, monkeypatch):
        import agent_community.platform.routers.workshops_session as ws_mod
        from agent_community.platform.routers.workshops_session import deliver_bridge
        monkeypatch.setattr(ws_mod, "workshops", {}, raising=False)
        req = type("R", (), {"json": staticmethod(lambda: asyncio.sleep(0, result={}))})()
        assert run(deliver_bridge("nope", "m1", req)).status_code == 404
        monkeypatch.setattr(ws_mod, "workshops", {"w7f2a1b3": _mkws(tmp_path)}, raising=False)
        assert run(deliver_bridge("w7f2a1b3", "nope", req)).status_code == 404

    def test_manual_mode_writes_copyable_text_file(self, tmp_path, monkeypatch):
        out, ws = self._call(tmp_path, monkeypatch)
        assert out["success"] is True
        assert out["verified"] is False, "没配 deliver 脚本 ⇒ 未核实"
        assert [x["step"] for x in out["manual"]] == [5]
        tf = Path(out["text_path"])
        assert tf.is_file(), "人工模式必须落一份可直接复制的文本"
        body = tf.read_text(encoding="utf-8")
        assert "邮箱" in body and "具体桥" in body, "投递文本要说清隔间/邮箱/桥"
        assert "w7f2a1b3-做一个数据看板-码农-m1" in body, "文本里要带上平台给的会话名"
        assert "码农_m1" in body

    def test_configured_deliver_is_verified(self, tmp_path, monkeypatch):
        helper = tmp_path / "dv.py"
        helper.write_text('import json;print(json.dumps({"method":"cmd","readback":"ok"}))',
                          encoding="utf-8")
        out, _ = self._call(tmp_path, monkeypatch,
                            scripts={"DELIVER_CMD": f'"{sys.executable}" "{helper}"'})
        assert out["verified"] is True and out["manual"] == []
        assert out["steps"]["5_deliver"]["evidence"]["method"] == "cmd"

    def test_bridge_generation_is_reported_honestly(self, tmp_path, monkeypatch):
        """harness 没真注册时，桥生成会失败 —— 必须如实带出 error，不许假装有桥。"""
        out, _ = self._call(tmp_path, monkeypatch)
        assert out["bridge"]["ok"] in (True, False)
        if not out["bridge"]["ok"]:
            assert out["bridge"]["error"], "失败必须带原因（准则⑤）"

    def test_custom_text_is_respected(self, tmp_path, monkeypatch):
        out, _ = self._call(tmp_path, monkeypatch, body={"text": "自定义投递内容"})
        assert Path(out["text_path"]).read_text(encoding="utf-8") == "自定义投递内容"


class TestActionScriptsAvailabilityEndpoint:
    """给界面：哪些步骤能自动、哪些只能人工（示例Harness-F 按这个显示）。"""

    def _call(self, monkeypatch, hid, sess=None):
        import agent_community.platform.routers.harness_register as hr
        from agent_community.platform import server as srv
        monkeypatch.setattr(srv.harness_manager, "sessions",
                            ({hid: sess} if sess else {}), raising=False)
        return run(hr.harness_action_scripts(hid))

    def test_404_for_unknown(self, monkeypatch):
        assert self._call(monkeypatch, "nope").status_code == 404

    def test_all_manual_when_nothing_configured(self, monkeypatch):
        out = self._call(monkeypatch, "h1", _FakeSess())
        assert out["automatic_steps"] == [] and out["manual_steps"] == [4, 5, 8]
        assert "3 步中 0 步可自动" in out["summary"]

    def test_mixed_configuration_summary(self, monkeypatch):
        from agent_community.platform import action_scripts as acts
        s = _FakeSess()
        acts.set_config(s, "new_session", {"NEW_SESSION_CMD": "x"}, foreground="never")
        acts.set_config(s, "rename_session", {"RENAME_CMD": "y"}, foreground="never")
        out = self._call(monkeypatch, "h1", s)
        assert out["automatic_steps"] == [4, 8] and out["manual_steps"] == [5]
        assert "2 步可自动" in out["summary"] and "1 步需人工" in out["summary"]
        fg = {f["step"]: f["foreground"] for f in out["flow"]}
        assert fg[4] == "never" and fg[5] == "required", "打扰等级要如实带出来"


class TestMembersStatus:
    """成员卡片聚合数据（前端一次拿全，别 N+1）。"""

    def _setup(self, tmp_path, monkeypatch, members, sessions=None):
        import agent_community.platform.routers.workshops_session as ws_mod
        from agent_community.platform import server as srv
        ws = Workshop(workshop_id="w7f2a1b3", name="做一个数据看板",
                      workspace_dir=str(tmp_path / "ws_1"), hall_content="x", members=members)
        monkeypatch.setattr(ws_mod, "workshops", {"w7f2a1b3": ws}, raising=False)
        monkeypatch.setattr(srv.harness_manager, "sessions", sessions or {}, raising=False)
        return ws

    def _call(self, ws_id="w7f2a1b3"):
        import agent_community.platform.routers.workshops_session as ws_mod
        return run(ws_mod.members_status(ws_id))

    def test_404(self, monkeypatch):
        import agent_community.platform.routers.workshops_session as ws_mod
        monkeypatch.setattr(ws_mod, "workshops", {}, raising=False)
        assert self._call("nope").status_code == 404

    def test_row_shape_and_manual_summary(self, tmp_path, monkeypatch):
        from agent_community.platform import action_scripts as acts
        s = _FakeSess("h1")
        acts.set_config(s, "new_session", {"NEW_SESSION_CMD": "x"}, foreground="never")
        self._setup(tmp_path, monkeypatch,
                    [WorkshopMember(member_id="m1", role="码农", display_name="小码",
                                    harness_ids=["h1"], entry_state="entered", entry_reason="桥已领取")],
                    sessions={"h1": s})
        out = self._call()
        r = out["members"][0]
        assert r["entry_state"] == "entered" and r["entry_reason"] == "桥已领取", \
            "派发进度必须给前端（老板要求读它）"
        assert r["session_name"] == "w7f2a1b3-做一个数据看板-码农-m1"
        assert r["steps"]["4_new_session"]["automatic"] is True
        assert r["steps"]["4_new_session"]["foreground"] == "never"
        assert r["steps"]["8_rename_session"]["automatic"] is False
        assert out["summary"]["manual_members"] == ["m1"], "有步骤要人工的成员要被点名"

    def test_all_automatic_flag(self, tmp_path, monkeypatch):
        from agent_community.platform import action_scripts as acts
        s = _FakeSess("h1")
        for a, k in (("new_session", "NEW_SESSION_CMD"), ("deliver", "DELIVER_CMD"),
                     ("rename_session", "RENAME_CMD")):
            acts.set_config(s, a, {k: "x"})
        self._setup(tmp_path, monkeypatch,
                    [WorkshopMember(member_id="m1", role="员工", display_name="甲", harness_ids=["h1"])],
                    sessions={"h1": s})
        out = self._call()
        assert out["summary"]["all_steps_automatic"] is True
        assert out["summary"]["manual_members"] == []

    def test_compartment_and_bridge_files_from_disk(self, tmp_path, monkeypatch):
        from agent_community.platform.compartment import ensure_compartment, mailbox_dir
        ws = self._setup(tmp_path, monkeypatch,
                        [WorkshopMember(member_id="m1", role="码农", display_name="小码",
                                        harness_ids=["h1"])])
        ensure_compartment(ws.workspace_dir, "码农", "m1")
        d = Path(ws.workspace_dir) / "码农_m1"
        (d / "bridge.py").write_text("# bridge", encoding="utf-8")
        (d / "output" / "r.md").write_text("x", encoding="utf-8")
        (mailbox_dir(ws.workspace_dir, "码农", "m1", "inbox") / "task_1.json").write_text("{}",
                                                                                          encoding="utf-8")
        r = self._call()["members"][0]
        assert r["compartment"]["exists"] is True
        assert r["compartment"]["mailbox_pending"]["inbox"] == 1, "邮箱积压数要看得见（谁没取）"
        assert "bridge.py" in r["bridge_files"]
        assert r["deliver_text_path"].endswith("deliver_text.md")

    def test_root_files_and_output_files_are_distinct(self, tmp_path, monkeypatch):
        """审查自查发现的真毛病：`output_files` 曾经算的是**隔间根**文件数（含桥脚本），
        会把"接入产物"当成"成果"报给前端。现在两个计数分开且各自如实。"""
        from agent_community.platform.compartment import ensure_compartment
        ws = self._setup(tmp_path, monkeypatch,
                        [WorkshopMember(member_id="m1", role="码农", display_name="小码",
                                        harness_ids=["h1"])])
        ensure_compartment(ws.workspace_dir, "码农", "m1")
        d = Path(ws.workspace_dir) / "码农_m1"
        (d / "bridge.py").write_text("# 接入产物，不是成果", encoding="utf-8")
        (d / "deliver_text.md").write_text("x", encoding="utf-8")
        (d / "output" / "a.md").write_text("成果1", encoding="utf-8")
        (d / "output" / "sub").mkdir()
        (d / "output" / "sub" / "b.md").write_text("成果2", encoding="utf-8")
        c = self._call()["members"][0]["compartment"]
        assert c["root_files"] == 2, f"隔间根应有 2 个接入产物，实得 {c['root_files']}"
        assert c["output_files"] == 2, f"output/ 里应有 2 个成果（递归），实得 {c['output_files']}"

    def test_missing_compartment_is_reported_not_crashed(self, tmp_path, monkeypatch):
        """隔间还没建时（成员刚选定）也必须给出结构完整的行，而不是崩掉。"""
        self._setup(tmp_path, monkeypatch,
                    [WorkshopMember(member_id="m1", role="码农", display_name="小码",
                                    harness_ids=["h1"])])
        r = self._call()["members"][0]
        assert r["compartment"]["exists"] is False
        assert r["compartment"]["output_files"] == 0
        assert set(r["steps"].keys()) == {"4_new_session", "5_deliver", "8_rename_session"}


class TestOnboardMembers:
    """按名单一次走完步骤 4/7/8/9（老板流程里这四步是**自动**的）。"""

    def _setup(self, tmp_path, monkeypatch, members, sessions=None):
        import agent_community.platform.routers.workshops_session as ws_mod
        from agent_community.platform import server as srv
        ws = Workshop(workshop_id="w7f2a1b3", name="做一个数据看板",
                      workspace_dir=str(tmp_path / "ws_1"), hall_content="x", members=members)
        monkeypatch.setattr(ws_mod, "workshops", {"w7f2a1b3": ws}, raising=False)
        monkeypatch.setattr(srv.harness_manager, "sessions", sessions or {}, raising=False)
        return ws

    def _call(self, body=None):
        import agent_community.platform.routers.workshops_session as ws_mod
        req = type("R", (), {"json": staticmethod(lambda: asyncio.sleep(0, result=body or {}))})()
        return run(ws_mod.onboard_members("w7f2a1b3", req))

    def test_404_and_409(self, tmp_path, monkeypatch):
        import agent_community.platform.routers.workshops_session as ws_mod
        monkeypatch.setattr(ws_mod, "workshops", {}, raising=False)
        assert self._call().status_code == 404
        self._setup(tmp_path, monkeypatch, [])
        assert self._call().status_code == 409
        self._setup(tmp_path, monkeypatch,
                    [WorkshopMember(member_id="m1", role="码农", display_name="小", harness_ids=["h1"])])
        assert self._call({"member_ids": ["nope"]}).status_code == 409

    def test_all_manual_members_reported_not_faked(self, tmp_path, monkeypatch):
        """没配脚本 ⇒ 每人各步都如实未核实，**不许**出现在 auto_ok 里。"""
        ws = self._setup(tmp_path, monkeypatch,
                        [WorkshopMember(member_id="m0", role="组长", display_name="老", harness_ids=["h1"]),
                         WorkshopMember(member_id="m1", role="码农", display_name="小", harness_ids=["h2"])],
                        sessions={"h1": _FakeSess("h1"), "h2": _FakeSess("h2")})
        out = self._call()
        assert out["success"] and out["count"] == 2
        assert out["auto_ok"] == [] and sorted(out["need_human"]) == ["m0", "m1"]
        assert "2 名成员" in out["summary"] and "0 名全自动" in out["summary"]
        for mid in ("m0", "m1"):
            r = out["results"][mid]
            assert r["all_verified"] is False and len(r["manual"]) == 2
        # 每人都拿到自己那份名字与隔间脚本
        assert out["results"]["m0"]["name"] == "w7f2a1b3-做一个数据看板-组长-m0"
        assert out["results"]["m1"]["name"] == "w7f2a1b3-做一个数据看板-码农-m1"
        assert ws.members[1].session_aliases[0] == "w7f2a1b3-做一个数据看板-码农-m1"

    def test_configured_member_is_auto_ok(self, tmp_path, monkeypatch):
        from agent_community.platform import action_scripts as acts
        helper = tmp_path / "h.py"
        helper.write_text('import json,sys;print(json.dumps({"session":{"id":"s1"},'
                          '"applied_name": sys.argv[1] if len(sys.argv)>1 else ""}))',
                          encoding="utf-8")
        s = _FakeSess("h1")
        acts.set_config(s, "new_session", {"NEW_SESSION_CMD": f'"{sys.executable}" "{helper}"'})
        acts.set_config(s, "rename_session",
                        {"RENAME_CMD": f'"{sys.executable}" "{helper}" "{{new_name}}"'})
        self._setup(tmp_path, monkeypatch,
                    [WorkshopMember(member_id="m0", role="组长", display_name="老", harness_ids=["h1"])],
                    sessions={"h1": s})
        out = self._call()
        assert out["auto_ok"] == ["m0"] and out["need_human"] == []
        assert out["results"]["m0"]["all_verified"] is True
        assert out["results"]["m0"]["session_ref"].get("id") == "s1"

    def test_member_ids_filter(self, tmp_path, monkeypatch):
        self._setup(tmp_path, monkeypatch,
                    [WorkshopMember(member_id="m0", role="组长", display_name="老", harness_ids=["h1"]),
                     WorkshopMember(member_id="m1", role="码农", display_name="小", harness_ids=["h2"])],
                    sessions={"h1": _FakeSess("h1"), "h2": _FakeSess("h2")})
        out = self._call({"member_ids": ["m1"]})
        assert out["count"] == 1 and list(out["results"]) == ["m1"]

    def test_one_bad_member_does_not_abort_the_batch(self, tmp_path, monkeypatch):
        ws = self._setup(tmp_path, monkeypatch,
                        [WorkshopMember(member_id="m0", role="组长", display_name="老", harness_ids=["h1"]),
                         WorkshopMember(member_id="m1", role="码农", display_name="小", harness_ids=["h2"])],
                        sessions={"h1": _FakeSess("h1"), "h2": _FakeSess("h2")})
        import agent_community.platform.routers.workshops_session as ws_mod
        real = ws_mod.establish_one

        def boom(w, m, **kw):
            if m.member_id == "m0":
                raise RuntimeError("模拟单个成员炸掉")
            return real(w, m, **kw)
        monkeypatch.setattr(ws_mod, "establish_one", boom, raising=False)
        out = self._call()
        assert out["success"] is True and out["count"] == 2, "一个成员出错不得中断整批"
        assert out["results"]["m0"]["success"] is False and "炸掉" in out["results"]["m0"]["error"]
        assert out["results"]["m1"]["success"] is True
        assert "m0" in out["need_human"]


class TestEndpointsDoNotBlockEventLoop:
    """回归：动作脚本是**同步子进程**（单个最长 45s）——端点必须丢到线程里跑。

    否则会**卡住整个事件循环**：同一个进程里的其它请求（含界面轮询）全被拖住，
    表现成"平台整体卡顿/无响应"，而日志里什么都看不出来。这是我自己埋的 bug，用线程 id 钉住。
    """

    class _WSStub:
        def __init__(self, members):
            self.members = members
            self.workshop_id = "w7f2a1b3"
            self.name = "做一个数据看板"
            self.workspace_dir = ""

    def test_establish_session_runs_off_the_loop(self, monkeypatch):
        import threading
        import agent_community.platform.routers.workshops_session as ws_mod
        seen = {}

        def fake(w, m, **kw):
            seen["thread"] = threading.get_ident()
            return {"success": True, "all_verified": True, "member_id": m.member_id}
        monkeypatch.setattr(ws_mod, "establish_one", fake, raising=False)
        m = WorkshopMember(member_id="m1", role="码农", display_name="小", harness_ids=["h1"])
        ws = self._WSStub([m])
        monkeypatch.setattr(ws_mod, "workshops", {"w7f2a1b3": ws}, raising=False)
        req = type("R", (), {"json": staticmethod(lambda: asyncio.sleep(0, result={}))})()
        out = run(ws_mod.establish_session("w7f2a1b3", "m1", req))
        assert out["success"] is True
        assert seen["thread"] != threading.get_ident(), \
            "动作必须在别的线程跑；跑在事件循环线程上会卡住整个平台"

    def test_onboard_members_runs_off_the_loop(self, monkeypatch):
        import threading
        import agent_community.platform.routers.workshops_session as ws_mod
        seen = []
        monkeypatch.setattr(ws_mod, "establish_one",
                            lambda w, m, **kw: (seen.append(threading.get_ident()),
                                                {"success": True, "all_verified": True,
                                                 "member_id": m.member_id})[1], raising=False)
        ws = self._WSStub([WorkshopMember(member_id="m0", role="组长", display_name="老",
                                          harness_ids=["h1"])])
        monkeypatch.setattr(ws_mod, "workshops", {"w7f2a1b3": ws}, raising=False)
        req = type("R", (), {"json": staticmethod(lambda: asyncio.sleep(0, result={}))})()
        run(ws_mod.onboard_members("w7f2a1b3", req))
        assert seen and all(t != threading.get_ident() for t in seen)

    def test_recover_runs_off_the_loop(self, monkeypatch):
        import threading
        import agent_community.platform.routers.workshops_recover as rc
        seen = {}
        monkeypatch.setattr(rc, "_run_one",
                            lambda w, m, **kw: (seen.setdefault("thread", threading.get_ident()),
                                                {"action": "none"})[1], raising=False)
        ws = self._WSStub([WorkshopMember(member_id="m0", role="组长", display_name="老",
                                          harness_ids=["h1"])])
        monkeypatch.setattr(rc, "workshops", {"w7f2a1b3": ws}, raising=False)
        req = type("R", (), {"json": staticmethod(lambda: asyncio.sleep(0, result={}))})()
        run(rc.recover_member("w7f2a1b3", "m0", req))
        assert seen.get("thread") != threading.get_ident(), "抢救链（含窗口激活脚本）同样别卡住循环"


class TestMemberFieldsPersist:
    """本轮修掉的真问题：成员 `entry_state`（派发进度）此前**不落盘**，重启即丢。"""

    def test_round_trip_keeps_progress_and_session(self, tmp_path):
        m = WorkshopMember(member_id="m1", role="码农", display_name="小码",
                           harness_ids=["h1"], status="working", entry_state="entered",
                           entry_reason="桥已领取", session_aliases=["w1-项目-码农-m1"],
                           session_ref={"id": "s9", "name": "w1-项目-码农-m1"})
        ws = Workshop(workshop_id="w1", name="项目", workspace_dir=str(tmp_path), hall_content="x",
                      members=[m])
        d = ws.to_dict()
        assert d["members"][0]["entry_state"] == "entered", "派发进度必须落盘"
        assert d["members"][0]["entry_reason"] == "桥已领取"
        back = Workshop.from_dict(d)
        assert back.members[0].entry_state == "entered"
        assert back.members[0].session_aliases == ["w1-项目-码农-m1"]
        assert back.members[0].session_ref["id"] == "s9"

    def test_old_data_without_new_keys_still_loads(self):
        old = {"workshop_id": "w1", "name": "老数据", "workspace_dir": "d", "hall_content": "x",
               "members": [{"member_id": "m1", "role": "员工", "display_name": "甲",
                            "harness_ids": [], "status": "idle"}]}
        ws = Workshop.from_dict(old)
        m = ws.members[0]
        assert m.entry_state == "pending" and m.session_aliases == [] and m.session_ref == {}
