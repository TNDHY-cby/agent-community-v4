# -*- coding: utf-8 -*-
"""V23 §9 成员级隔间测试（老板 2026-10-07 口述：「单个会话拥有一个隔间和一个具体桥」）。

覆盖：
  1. `safe_part` 的清洗与兜底
  2. 隔间路径推导
  3. **越界防护**（role/member_id 可能来自请求体 => 绝不能跳出工作间）
  4. `ensure_compartment` 建 input/output/temp，幂等，失败不抛
  5. `connect_artifact.plan`：给 role 落隔间、不给 role 落工作间根（向后兼容）
  6. **安全不变量**：`plan()` 签名里不得出现任何路径入参
"""
from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from agent_community.platform import compartment as cmp


def run(coro):
    """本文件里跑 async 路由处理器（test_handover.py 有同名辅助，两个文件各自独立）。"""
    import asyncio
    return asyncio.run(coro)


class _WS:
    def __init__(self, d):
        self.workspace_dir = str(d)
        self.workshop_id = "ws-test"


class TestSafePart:
    def test_plain_names_pass_through(self):
        assert cmp.safe_part("组长") == "组长"
        assert cmp.safe_part("m0") == "m0"

    @pytest.mark.parametrize("bad", ['a/b', 'a\\b', 'a:b', 'a*b', 'a?b', 'a"b', 'a<b', 'a>b', 'a|b'])
    def test_separators_and_illegal_chars_are_replaced(self, bad):
        out = cmp.safe_part(bad)
        assert "/" not in out and "\\" not in out
        for ch in ':*?"<>|':
            assert ch not in out

    def test_dot_segments_never_survive(self):
        for bad in ("..", "../..", "a..b", " ... "):
            out = cmp.safe_part(bad)
            assert out not in (".", "..") and ".." not in out

    def test_empty_falls_back(self):
        assert cmp.safe_part("") == "未命名"
        assert cmp.safe_part(None) == "未命名"
        assert cmp.safe_part("   ") == "未命名"
        assert cmp.safe_part("", fallback="x") == "x"

    def test_length_is_capped(self):
        assert len(cmp.safe_part("z" * 200)) <= cmp._MAX_PART


class TestCompartmentDir:
    def test_layout_is_role_underscore_member(self, tmp_path):
        d = cmp.compartment_dir(tmp_path, "组长", "m0")
        assert d == Path(tmp_path) / "组长_m0"

    def test_member_and_role_are_distinct_dirs(self, tmp_path):
        a = cmp.compartment_dir(tmp_path, "员工", "m0")
        b = cmp.compartment_dir(tmp_path, "员工", "m1")
        c = cmp.compartment_dir(tmp_path, "组长", "m0")
        assert len({a, b, c}) == 3, "不同成员、不同职位必须落在不同隔间"

    @pytest.mark.parametrize("role,mid", [
        ("../../x", "y"), ("a/b", ".."), ("..", ".."), ("", ""),
        ("....//....", "....//...."), ("C:\\Windows", "System32"),
    ])
    def test_never_escapes_the_workspace(self, tmp_path, role, mid):
        d = cmp.compartment_dir(tmp_path, role, mid)
        assert cmp.is_inside(d, tmp_path), f"{role!r}/{mid!r} 跳出了工作间: {d}"

    def test_is_inside_rejects_siblings_and_parents(self, tmp_path):
        assert cmp.is_inside(tmp_path / "a", tmp_path) is True
        assert cmp.is_inside(tmp_path, tmp_path) is True
        assert cmp.is_inside(tmp_path.parent, tmp_path) is False


class TestEnsure:
    def test_creates_three_subdirs(self, tmp_path):
        out = cmp.ensure_compartment(tmp_path, "员工", "m0")
        assert out["ok"] is True
        assert sorted(out["subdirs"].keys()) == ["input", "output", "temp"]
        for name in cmp.SUBDIRS:
            assert (Path(out["dir"]) / name).is_dir()

    def test_idempotent(self, tmp_path):
        a = cmp.ensure_compartment(tmp_path, "员工", "m0")
        b = cmp.ensure_compartment(tmp_path, "员工", "m0")
        assert a["dir"] == b["dir"] and b["ok"] is True
        assert b["created"] == [], "第二次不该再报新建"

    def test_traversal_input_still_lands_inside(self, tmp_path):
        out = cmp.ensure_compartment(tmp_path, "../../evil", "../x")
        assert out["ok"] is True
        assert cmp.is_inside(Path(out["dir"]), tmp_path)

    def test_failure_does_not_raise(self, tmp_path):
        # 用一个"文件"当父目录，制造必然失败
        f = tmp_path / "iamafile"
        f.write_text("x", encoding="utf-8")
        out = cmp.ensure_compartment(f, "员工", "m0")
        assert out["ok"] is False and out["error"]


class TestPlanLandsInCompartment:
    def test_with_role_lands_in_compartment(self, tmp_path):
        from agent_community.platform import connect_artifact as ca
        info = type("I", (), {"harness_id": "h1", "wakeup_method": "file_poll"})()
        pl = ca.plan(_WS(tmp_path), info, "m0", role="组长")
        assert Path(pl["target_dir"]) == cmp.compartment_dir(tmp_path, "组长", "m0")
        assert pl["params"]["WORKSPACE_DIR"] == pl["target_dir"]

    def test_without_role_keeps_old_behaviour(self, tmp_path):
        """向后兼容：不传 role 就还是落工作间根（老调用方行为不变）。"""
        from agent_community.platform import connect_artifact as ca
        info = type("I", (), {"harness_id": "h1", "wakeup_method": "file_poll"})()
        pl = ca.plan(_WS(tmp_path), info, "m0")
        assert Path(pl["target_dir"]) == tmp_path

    def test_two_members_get_two_targets(self, tmp_path):
        from agent_community.platform import connect_artifact as ca
        info = type("I", (), {"harness_id": "h1", "wakeup_method": "file_poll"})()
        a = ca.plan(_WS(tmp_path), info, "m0", role="员工")["target_dir"]
        b = ca.plan(_WS(tmp_path), info, "m1", role="员工")["target_dir"]
        assert a != b, "两人同落一个目录 => 同名 bridge.py 会互相覆盖（这就是要修的病）"

    def test_signature_takes_no_path_input(self):
        """安全不变量（与 test_connect_artifact.py 同一条）：路径只能由平台派生。

        我做隔间时曾图省事给 `plan()` 加了个 `target_dir` 路径参数，**被这条拦下**——
        加一个"可传任意路径"的口子就把这条可断言的性质削掉了。现改为只收 `role`。
        """
        from agent_community.platform import connect_artifact as ca
        params = inspect.signature(ca.plan).parameters
        for banned in ("out_dir", "target_dir", "path", "workspace_dir"):
            assert banned not in params, f"plan() 不得接受路径入参: {banned}"


class TestCreationTiming:
    """老板 2026-10-07 更正两次后的时机：新建工作间 → 选定员工 → **架起具体桥** → 此刻建隔间。

    我一度把建隔间塞进建间路径（`write_workspace_files`），**放早了**，已退回。
    这组测试把结论钉死，防止以后有人又挪回去。
    """

    def _ws(self, tmp_path):
        from agent_community.platform.workshop import Workshop, WorkshopMember
        return Workshop(
            workshop_id="ws_t", name="t", workspace_dir=str(tmp_path / "ws_1"),
            hall_content="任务", created_at="now",
            members=[WorkshopMember(member_id="m0", role="员工",
                                    display_name="d", harness_ids=["h1"])],
        )

    def test_building_the_room_creates_no_compartment(self, tmp_path):
        from agent_community.platform.workshop import write_workspace_files
        ws = self._ws(tmp_path)
        write_workspace_files(ws)
        base = Path(ws.workspace_dir)
        assert (base / "hall.md").exists(), "共享文件仍应照写"
        assert not (base / "员工_m0").exists(), (
            "建间时**不该**建隔间——隔间是「架起具体桥」那一步才建的（老板更正）")

    def test_the_bridge_step_is_what_creates_it(self, tmp_path):
        """隔间创建点唯一确定在架桥那一步的模块里（此处只验推导，真链路见隔离实例端到端）。"""
        base = tmp_path / "ws_1"
        out = cmp.ensure_compartment(base, "员工", "m0")
        assert out["ok"] and (Path(out["dir"]) / "input").is_dir()
        assert cmp.is_inside(Path(out["dir"]), base)


class TestMemberMailbox:
    """2026-10-08 老板：「一个员工一个具体桥和隔间 ⇒ **不会发生谁收到就是谁的**」。

    邮箱是这条规则的**寻址**落点：平台只往该成员自己的 inbox 写，他的桥只从自己的 inbox 取
    ⇒ 同一 harness 开多个会话也不会串。
    """

    def test_mailbox_paths(self, tmp_path):
        assert cmp.mailbox_dir(tmp_path, "员工", "m1") == cmp.compartment_dir(tmp_path, "员工", "m1") / "inbox"
        assert cmp.mailbox_dir(tmp_path, "员工", "m1", "replies") == \
            cmp.compartment_dir(tmp_path, "员工", "m1") / "replies"
        assert cmp.mailbox_dir(tmp_path, "员工", "m1", "nope").name == "inbox"

    def test_ensure_creates_mailbox_but_keeps_work_subdirs_contract(self, tmp_path):
        out = cmp.ensure_compartment(tmp_path, "员工", "m0")
        assert out["ok"] is True
        # 前端已知契约不变：subdirs 仍是三个工作分区
        assert sorted(out["subdirs"].keys()) == ["input", "output", "temp"]
        assert sorted(out["mailbox"].keys()) == ["inbox", "replies"]
        for name in ("inbox", "replies"):
            assert (Path(out["dir"]) / name).is_dir()

    def test_two_members_get_different_mailboxes(self, tmp_path):
        a = cmp.ensure_compartment(tmp_path, "员工", "m0")
        b = cmp.ensure_compartment(tmp_path, "员工", "m1")
        assert a["inbox"] != b["inbox"], "同一 harness 的两个成员必须各写各的邮箱"


class TestMailboxWiring:
    """邮箱要真的被用上：桥模板的 INBOX_DIR 指向成员邮箱。"""

    def test_plan_injects_member_inbox(self, tmp_path):
        from agent_community.platform import connect_artifact as ca
        class _W:
            workspace_dir = str(tmp_path / "ws_1")
            workshop_id = "w1"
        info = type("I", (), {"harness_id": "h1", "wakeup_method": "file_poll",
                              "wakeup_dir": str(tmp_path / "harness_level_inbox")})()
        pl = ca.plan(_W(), info, "m1", role="员工")
        assert pl["params"]["INBOX_DIR"] == str(cmp.mailbox_dir(tmp_path / "ws_1", "员工", "m1", "inbox"))

    def test_plan_without_role_keeps_harness_level_inbox(self, tmp_path):
        from agent_community.platform import connect_artifact as ca
        class _W:
            workspace_dir = str(tmp_path / "ws_1")
            workshop_id = "w1"
        info = type("I", (), {"harness_id": "h1", "wakeup_method": "file_poll",
                              "wakeup_dir": str(tmp_path / "hlevel")})()
        pl = ca.plan(_W(), info, "m1")           # 不带 role：历史行为
        assert pl["params"]["INBOX_DIR"] == str(tmp_path / "hlevel")

    def test_member_mailbox_rescues_a_harness_without_wakeup_dir(self, tmp_path, monkeypatch):
        """修好一个真失败模式：以前 harness 没配 wakeup_dir ⇒ '缺少模板必填字段: INBOX_DIR'
        ⇒ 桥渲染失败。有了成员邮箱兜底，同一 harness 现在能渲染出来了。"""
        from agent_community.platform import connect_artifact as ca
        from agent_community.platform import bridge_template as bt
        class _W:
            workspace_dir = str(tmp_path / "ws_1")
            workshop_id = "w1"
        info = type("I", (), {"harness_id": "h1", "wakeup_method": "file_poll"})()
        monkeypatch.setattr(bt, "get", lambda i: None, raising=False)
        pl = ca.plan(_W(), info, "m1", role="员工")
        assert pl["params"]["INBOX_DIR"], "成员邮箱必须提供 INBOX_DIR"
        assert pl["complete"] is True


    def test_file_poll_send_prefers_member_mailbox(self, tmp_path):
        """派发侧也要认成员邮箱——否则隔离只做了一半（桥从成员邮箱取，平台却写 harness 邮箱）。"""
        from agent_community.platform import server as srv
        comp = tmp_path / "ws" / "员工_m1"
        comp.mkdir(parents=True)
        assert srv._file_poll_send("h1", {"type": "task", "compartment_dir": str(comp)}) is True
        files = list((comp / "inbox").glob("task_*.json"))
        assert len(files) == 1
        assert "compartment_dir" in files[0].read_text(encoding="utf-8")

    def test_file_poll_send_falls_back_to_harness_level(self, tmp_path, monkeypatch):
        from agent_community.platform import server as srv
        info = type("I", (), {"wakeup_dir": str(tmp_path / "hlevel")})()
        sess = type("S", (), {"info": info})()
        monkeypatch.setattr(srv.harness_manager, "sessions", {"h1": sess}, raising=False)
        assert srv._file_poll_send("h1", {"type": "task"}) is True, "无 compartment_dir 时应回落"
        assert len(list((tmp_path / "hlevel").glob("task_*.json"))) == 1


class TestCompartmentsEndpoint:
    """`GET /api/workshop/{ws_id}/compartments` —— 磁盘实况（应答 FE-03 §7 的申请）。"""

    def _mkws(self, tmp_path, members, monkeypatch):
        from agent_community.platform.workshop import Workshop, WorkshopMember
        ws = Workshop(
            workshop_id="ws_c", name="c", workspace_dir=str(tmp_path / "ws_1"),
            hall_content="x", created_at="now",
            members=[WorkshopMember(member_id=mid, role=role, display_name=dn, harness_ids=[h])
                     for mid, role, dn, h in members],
        )
        import agent_community.platform.routers.workshops_lifecycle as wl
        monkeypatch.setattr(wl, "workshops", {"ws_c": ws}, raising=False)
        return ws

    def test_missing_workshop_is_404(self, monkeypatch):
        from agent_community.platform.routers.workshops_lifecycle import workshop_compartments
        import agent_community.platform.routers.workshops_lifecycle as wl
        monkeypatch.setattr(wl, "workshops", {}, raising=False)
        resp = run(workshop_compartments("nope"))
        assert resp.status_code == 404

    def test_reports_truth_before_and_after_bridging(self, tmp_path, monkeypatch):
        from agent_community.platform.routers.workshops_lifecycle import workshop_compartments
        ws = self._mkws(tmp_path, [("m0", "员工", "d", "h1"), ("m1", "组长", "d2", "h2")], monkeypatch)

        # 还没架桥：隔间不存在，但路径（权威）已经给出
        out = run(workshop_compartments("ws_c"))
        assert out["count"] == 2 and out["exists_count"] == 0
        for r in out["compartments"]:
            assert r["exists"] is False and r["dir"].startswith(ws.workspace_dir)
            assert r["inside_workspace"] is True
            assert r["subdir_names"] == ["input", "output", "temp"]
        assert "不要自行推导" in out["path_authority"]

        # 架桥那一步之后：实况翻转，且带出了桥文件
        cmp.ensure_compartment(ws.workspace_dir, "员工", "m0")
        (Path(ws.workspace_dir) / "员工_m0" / "bridge.py").write_text("x", encoding="utf-8")
        out2 = run(workshop_compartments("ws_c"))
        assert out2["exists_count"] == 1
        got = [r for r in out2["compartments"] if r["member_id"] == "m0"][0]
        assert got["exists"] is True
        assert got["subdirs"] == {"input": True, "output": True, "temp": True}
        assert got["bridge_files"] == ["bridge.py"]
        other = [r for r in out2["compartments"] if r["member_id"] == "m1"][0]
        assert other["exists"] is False, "没架桥的成员不该凭空有隔间"

    def test_authoritative_path_matches_module_derivation(self, tmp_path, monkeypatch):
        from agent_community.platform.routers.workshops_lifecycle import workshop_compartments
        ws = self._mkws(tmp_path, [("m0", "组长", "d", "h1")], monkeypatch)
        out = run(workshop_compartments("ws_c"))
        assert out["compartments"][0]["dir"] == str(cmp.compartment_dir(ws.workspace_dir, "组长", "m0"))


class TestPromptTellsBothPaths:
    def test_compartment_hint_names_both_areas(self):
        from agent_community.platform.server import _compartment_hint
        txt = _compartment_hint(r"D:\ws\ws_1\员工_m0", r"D:\ws\ws_1")
        assert r"D:\ws\ws_1\员工_m0" in txt and r"D:\ws\ws_1" in txt
        assert "只读" in txt and "不要写" in txt

    def test_empty_compartment_yields_nothing(self):
        from agent_community.platform.server import _compartment_hint
        assert _compartment_hint("", r"D:\ws") == ""
