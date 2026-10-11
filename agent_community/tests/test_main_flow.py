# -*- coding: utf-8 -*-
"""老板主流程 ①②③ 测试：大厅广播 → **自动新建工作间** → 一级讨论（2026-10-07）。

设计来源：V23 §1 ①~⑧ 老板口述主流程；工作间 `status` 字段本就写着整条流程
（draft → discussing(一级讨论) → selecting(选定员工) → division(二级讨论) → running → review(三级讨论)）。
**此前缺的只是"自动建间"这一环**（实测：全库只有注释提到自动建间、无实现）。

覆盖：
  1. 广播后真的建出工作间，字段正确（name/hall_content/status/origin_task_id）
  2. **幂等**：同一任务重复广播不会建出第二个房间
  3. **成员留空**：选人是老板流程 ④，不在建间时替用户决定
  4. 工作区文件已落（hall.md 内容 = 广播原文）
  5. 上限保护、异常/脏输入不抛（建间失败绝不能打断广播）
  6. 反查函数 `_find_workshop_of_task`
  7. HTTP 面 `GET /api/tasks/{task_id}/workshop`：无 → 404；有 → 字段齐
  8. `origin_task_id` 的持久化往返（to_dict / from_dict）
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from agent_community.platform.workshop import Workshop, WorkshopMember


def run(coro):
    return asyncio.run(coro)


def _task(title="给合作社做个看板", desc="给合作社做个看板，要看信誉"):
    from agent_community.platform.protocol import Task, TaskStatus
    return Task(title=title, description=desc, status=TaskStatus.BROADCASTING)


@pytest.fixture(autouse=True)
def _clean_registries():
    """本文件自建自清；且**显式**把落点钉在 tmp 上（防写真实 data/workshops）。"""
    from agent_community.platform import server as srv
    _ws, _ts = dict(srv.workshops), dict(srv.tasks)
    srv.workshops.clear()
    srv.tasks.clear()
    yield
    srv.workshops.clear()
    srv.workshops.update(_ws)
    srv.tasks.clear()
    srv.tasks.update(_ts)


@pytest.fixture
def srv(tmp_path, monkeypatch):
    from agent_community.platform import server as _srv
    monkeypatch.setattr(_srv, "WORKSHOP_BASE", tmp_path / "workshops", raising=False)
    return _srv


class TestAutoCreate:
    def test_broadcast_creates_a_workshop(self, srv):
        t = _task()
        srv.tasks[t.id] = t
        ws = srv._auto_create_workshop_for_task(t, t.description)
        assert ws is not None
        assert ws.hall_content == t.description, "房间的任务内容应就是广播原文"
        assert ws.origin_task_id == t.id
        assert ws.status == "discussing", "自动建间即进入一级讨论（老板 ①~③）"
        assert ws.members == [], "成员留空：选人是流程 ④，不替用户决定"
        assert srv.workshops[ws.workshop_id] is ws

    def test_workspace_files_land_with_the_broadcast_text(self, srv):
        t = _task()
        ws = srv._auto_create_workshop_for_task(t, "广播原文：做一个看板")
        from pathlib import Path
        base = Path(ws.workspace_dir)
        assert base.is_dir() and (base / "hall.md").exists()
        assert (base / "hall.md").read_text(encoding="utf-8") == "广播原文：做一个看板"
        assert (base / "AGENTS.md").exists()

    def test_idempotent_no_second_room(self, srv):
        t = _task()
        a = srv._auto_create_workshop_for_task(t, t.description)
        b = srv._auto_create_workshop_for_task(t, t.description)
        assert a is b and len(srv.workshops) == 1, "重复广播不得建出第二个房间"

    def test_respects_max_workshops(self, srv, monkeypatch):
        monkeypatch.setattr(srv, "MAX_WORKSHOPS", 0, raising=False)
        t = _task()
        assert srv._auto_create_workshop_for_task(t, "x") is None
        assert len(srv.workshops) == 0

    @pytest.mark.parametrize("bad", [None, object()])
    def test_dirty_input_never_raises(self, srv, bad):
        """建间失败绝不能打断广播——宁可没房间，也不能让广播报错。"""
        assert srv._auto_create_workshop_for_task(bad, "x") is None

    def test_exception_inside_is_swallowed(self, srv, monkeypatch):
        t = _task()
        monkeypatch.setattr(srv, "Workshop", None, raising=False)   # 制造必然失败
        assert srv._auto_create_workshop_for_task(t, "x") is None


class TestLookup:
    def test_find_returns_the_room(self, srv):
        t = _task()
        ws = srv._auto_create_workshop_for_task(t, "x")
        assert srv._find_workshop_of_task(t.id) is ws

    def test_unknown_task_returns_none(self, srv):
        assert srv._find_workshop_of_task("no-such-task") is None
        assert srv._find_workshop_of_task("") is None

    def test_manually_created_room_has_no_origin(self, srv):
        ws = Workshop(workshop_id="w1", name="n", workspace_dir="/x", hall_content="h")
        srv.workshops["w1"] = ws
        assert srv._find_workshop_of_task("anything") is None, "人工建的房间不该被任务反查命中"


class TestTaskWorkshopEndpoint:
    def test_404_when_no_room(self, srv):
        from agent_community.platform.server import api_task_workshop
        resp = run(api_task_workshop("no-such-task"))
        assert resp.status_code == 404

    def test_200_reports_room_and_next_step(self, srv):
        from agent_community.platform.server import api_task_workshop
        t = _task()
        ws = srv._auto_create_workshop_for_task(t, "x")
        out = run(api_task_workshop(t.id))
        assert out["success"] and out["workshop_id"] == ws.workshop_id
        assert out["status"] == "discussing"
        assert out["member_count"] == 0
        assert "select-members" in out["next_step"], "空房间应把下一步指到选成员"

    def test_next_step_changes_once_members_exist(self, srv):
        from agent_community.platform.server import api_task_workshop
        t = _task()
        ws = srv._auto_create_workshop_for_task(t, "x")
        ws.members.append(WorkshopMember(member_id="m0", role="组长",
                                         display_name="d", harness_ids=["h"]))
        out = run(api_task_workshop(t.id))
        assert out["member_count"] == 1 and "可开工" in out["next_step"]


class TestPlanRefreshedOnRosterChange:
    """⑥ 的 `plan.md`（分工安排）必须随**名单变化**重生成。

    2026-10-07 审 FE-07 设计稿时发现：`plan.md` 只在建间与手动 `library/update` 时写，
    **选完人之后仍是「（尚无成员）」** —— 恰好在老板流程 ⑥ 那一刻是过期的。
    这是**审稿审出来的我自己的缺口**，已补；这组测试钉死三个名单变更点。
    """

    def _prep(self, tmp_path, monkeypatch):
        import agent_community.platform.routers.workshops_assign as wa
        from agent_community.platform.workshop import write_workspace_files
        ws = Workshop(workshop_id="w1", name="看板", workspace_dir=str(tmp_path / "ws_1"),
                      hall_content="做一个看板", created_at="now")
        write_workspace_files(ws)          # 建间：此时无成员
        monkeypatch.setattr(wa, "workshops", {"w1": ws}, raising=False)
        # 让 AI 选人走"无需真 AI"的路径
        monkeypatch.setattr(wa, "_sv", lambda: type("S", (), {"ai_provider": None})(), raising=False)
        return wa, ws

    def _plan(self, ws):
        from agent_community.platform.workshop import LIBRARY_DIRNAME
        return (Path(ws.workspace_dir) / LIBRARY_DIRNAME / "plan.md").read_text(encoding="utf-8")

    def test_confirm_members_regenerates_plan(self, tmp_path, monkeypatch):
        wa, ws = self._prep(tmp_path, monkeypatch)
        assert "（尚无成员）" in self._plan(ws)

        class _Req:
            def __init__(self, b): self._b = b
            async def json(self): return self._b

        run(wa.workshop_confirm_members("w1", _Req({"members": [
            {"role": "组长", "display_name": "L", "harness_ids": ["h0"]},
            {"role": "码农", "display_name": "C", "harness_ids": ["h1"]},
        ]})))
        plan = self._plan(ws)
        assert "（尚无成员）" not in plan, "确认名单后 plan.md 必须重生成"
        assert "组长_m0" in plan and "码农_m1" in plan, "分工安排要含每人隔间"

    def test_save_members_endpoint_regenerates_plan(self, tmp_path, monkeypatch):
        wa, ws = self._prep(tmp_path, monkeypatch)

        class _Req:
            def __init__(self, b): self._b = b
            async def json(self): return self._b

        # `POST /api/workshop/{id}/members` -> workshop_save_members（前端增删改名单后即时同步）
        run(wa.workshop_save_members("w1", _Req({"members": [
            {"role": "搜索者", "display_name": "S", "harness_ids": ["h9"]}]})))
        assert "搜索者_m0" in self._plan(ws), "改名单后 plan.md 必须反映新名单"


class TestStageIntegration:
    """老板流程 ⑧ 后半：「阶段完成**由一个 agent 整合**」。

    实测缺口（2026-10-08）：此前⑧只做到"**汇报文本**汇总"（`_parallel_batch_summary` 攒成员的文字回报），
    **各成员隔间 `output/` 里真正的产物没人收**。隔间建了、桥发了，产物躺着没人整合，流程没走完。
    """

    def _ws(self, tmp_path):
        from agent_community.platform.workshop import write_workspace_files
        ws = Workshop(workshop_id="wi", name="整合", workspace_dir=str(tmp_path / "ws_i"),
                      hall_content="做两样", created_at="now",
                      members=[WorkshopMember(member_id="m0", role="组长", display_name="L", harness_ids=["h0"]),
                               WorkshopMember(member_id="m1", role="码农", display_name="C", harness_ids=["h1"])])
        write_workspace_files(ws)
        return ws

    def _produce(self, ws, role, mid, name="result.md", text="# 成果"):
        d = Path(ws.workspace_dir) / f"{role}_{mid}" / "output"
        d.mkdir(parents=True, exist_ok=True)
        (d / name).write_text(text, encoding="utf-8")
        return d

    def test_collect_reports_empty_honestly(self, tmp_path):
        from agent_community.platform.workshop import collect_member_outputs
        ws = self._ws(tmp_path)
        col = collect_member_outputs(ws)
        assert col["total_files"] == 0
        assert col["without_output"] == ["m0", "m1"]
        assert "无内容可汇" in col["note"]

    def test_collect_finds_nested_files_and_non_producers(self, tmp_path):
        from agent_community.platform.workshop import collect_member_outputs
        ws = self._ws(tmp_path)
        d = self._produce(ws, "码农", "m1")
        (d / "sub").mkdir()
        (d / "sub" / "d.json").write_text("{}", encoding="utf-8")
        col = collect_member_outputs(ws)
        assert col["total_files"] == 2
        assert col["with_output"] == ["m1"] and col["without_output"] == ["m0"]
        names = [f["name"] for r in col["members"] if r["member_id"] == "m1" for f in r["files"]]
        assert "result.md" in names
        assert any(n.replace("\\", "/") == "sub/d.json" for n in names), names

    def test_manifest_marks_no_output_as_not_done(self, tmp_path):
        from agent_community.platform.workshop import write_integration_manifest
        ws = self._ws(tmp_path)
        man = write_integration_manifest(ws, integrator="L")
        txt = Path(man["path"]).read_text(encoding="utf-8")
        assert "无产物可整合" in txt
        assert "不等于「整合完成」" in txt, "准则⑤：空的也必须说清不是成功"
        assert man["total_files"] == 0

    def test_manifest_lists_files_sizes_and_missing_members(self, tmp_path):
        from agent_community.platform.workshop import write_integration_manifest
        ws = self._ws(tmp_path)
        self._produce(ws, "码农", "m1", text="# 成果\n" + "x" * 50)
        man = write_integration_manifest(ws, integrator="L（组长）")
        txt = Path(man["path"]).read_text(encoding="utf-8")
        assert "result.md" in txt and " B）" in txt, "要给文件名与大小"
        assert "产物：无" in txt, "没交的成员必须被点名"
        assert "无产物可整合" not in txt
        assert man["total_files"] == 1 and man["without_output"] == ["m0"]

    def test_manifest_does_not_move_member_files(self, tmp_path):
        """平台只出清单，**不搬动**成员文件（否则整合者会把别人隔间搞乱）。"""
        from agent_community.platform.workshop import write_integration_manifest
        ws = self._ws(tmp_path)
        d = self._produce(ws, "码农", "m1")
        write_integration_manifest(ws, integrator="L")
        assert sorted(p.name for p in d.iterdir()) == ["result.md"]

    def test_endpoint_404_and_409(self, tmp_path, monkeypatch):
        from agent_community.platform.routers.workshops_lifecycle import workshop_integrate
        import agent_community.platform.routers.workshops_lifecycle as wl
        monkeypatch.setattr(wl, "workshops", {}, raising=False)
        assert run(workshop_integrate("nope")).status_code == 404
        empty = Workshop(workshop_id="we", name="空", workspace_dir=str(tmp_path / "ws_e"),
                         hall_content="h")
        monkeypatch.setattr(wl, "workshops", {"we": empty}, raising=False)
        assert run(workshop_integrate("we")).status_code == 409, "名单为空不该算可整合"

    def test_endpoint_dispatches_and_reports_truth(self, tmp_path, monkeypatch):
        from agent_community.platform.routers.workshops_lifecycle import workshop_integrate
        import agent_community.platform.routers.workshops_lifecycle as wl
        from agent_community.platform import server as srv

        calls = []
        def _fake(hid, payload, kind=""):
            calls.append({"hid": hid, "payload": payload, "kind": kind})
            return True, "mocked"
        monkeypatch.setattr(srv, "_dispatch_to_harness", _fake, raising=False)

        ws = self._ws(tmp_path)
        monkeypatch.setattr(wl, "workshops", {"wi": ws}, raising=False)

        # 全空：必须如实说 has_output=False
        out = run(workshop_integrate("wi"))
        assert out["success"] is True and out["has_output"] is False
        assert out["integrator"] == "m0" and out["integrator_role"] == "组长"
        assert out["without_output"] == ["m0", "m1"]
        assert len(calls) == 1 and calls[0]["kind"] == "task"
        assert "无产物" in calls[0]["payload"]["message"]

        # 有产物：has_output 翻真，且提示词里带上整合者的取件路径
        self._produce(ws, "码农", "m1")
        calls.clear()
        out2 = run(workshop_integrate("wi"))
        assert out2["has_output"] is True and out2["total_files"] == 1
        assert out2["with_output"] == ["m1"] and out2["without_output"] == ["m0"]
        assert "output" in calls[0]["payload"]["message"]
        assert calls[0]["payload"]["manifest_path"].endswith("integration.md")

    def test_endpoint_writes_audit(self, tmp_path, monkeypatch):
        from agent_community.platform.routers.workshops_lifecycle import workshop_integrate
        import agent_community.platform.routers.workshops_lifecycle as wl
        from agent_community.platform import server as srv
        from agent_community.platform.audit import audit_log
        monkeypatch.setattr(srv, "_dispatch_to_harness", lambda hid, p, kind="": (True, "ok"), raising=False)
        ws = self._ws(tmp_path)
        monkeypatch.setattr(wl, "workshops", {"wi": ws}, raising=False)
        run(workshop_integrate("wi"))
        evs = audit_log.query(limit=10, event="workshop.integrate", target="wi")
        assert evs and evs[0]["integrator"] == "m0" and evs[0]["has_output"] is False


class _SessStub:
    """够用就好的 harness 会话替身。

    ⚠️ 形状要**照着真对象的接口补齐**：`info` 上 `_harness_wakeup_method` 会读 `wakeup_method`、
    `_activation_prompt` 会读 `harness_name`/`ai` —— 少一个字段就会冒出与被测行为无关的
    AttributeError（本项目踩过多次"假替身形状不对"）。
    """

    def __init__(self, hid="h1", name="示例Harness-A"):
        ai = type("AI", (), {"model_name": "stub", "capabilities": ["code"]})()
        # ⚠️ `wakeup_method` 在真对象上是**枚举**（代码读 `.value`），不是字符串 —— 替身必须照抄
        wm = type("WM", (), {"value": "file_poll"})()
        self.info = type("I", (), {"harness_id": hid, "harness_name": name,
                                   "wakeup_method": wm, "ai": ai,
                                   "wakeup_dir": "",
                                   "model_dump": lambda self=None: {}})()
        self.metadata = {}


class TestMemberAssignmentFolders:
    """老板 2026-10-08：「**每个员工分配到一个文件夹（名称：agent名-职位），里面有详细分工内容**」。

    设计意图：外端 agent **拿路径自己去看**项目与分工 —— 平台不把内容贴进激活消息
    （消息保持**通用模板**，只换路径）。
    """

    def _ws(self, tmp_path, members=None):
        from agent_community.platform.workshop import Workshop, WorkshopMember, write_library
        ws = Workshop(workshop_id="w1", name="看板项目", workspace_dir=str(tmp_path / "ws"),
                      hall_content="做看板", created_at="now",
                      members=members or [
                          WorkshopMember(member_id="m0", role="组长", display_name="老组",
                                         harness_ids=["hL"], session_aliases=["w1-看板项目-组长-m0"]),
                          WorkshopMember(member_id="m1", role="码农", display_name="小码",
                                         harness_ids=["h1"])])
        write_library(ws)
        return ws

    def test_folder_named_agent_role(self, tmp_path):
        from agent_community.platform.workshop import member_assignment_dir
        ws = self._ws(tmp_path)
        names = [member_assignment_dir(ws, m).name for m in ws.members]
        assert names == ["老组-组长", "小码-码农"], f"文件夹必须是 <agent名>-<职位>，实得 {names}"

    def test_assignment_file_has_my_work_and_paths(self, tmp_path):
        from agent_community.platform.workshop import member_assignment_dir, write_library
        ws = self._ws(tmp_path)
        ws.assignments = {"m1": [{"kind": "任务", "instruction": "做前端表格", "note": "今天交"}]}
        write_library(ws)                      # 委派记录变了，重新生成
        t = (member_assignment_dir(ws, ws.members[1]) / "assignment.md").read_text(encoding="utf-8")
        assert "我要做的事" in t and "做前端表格" in t, "委派条目要进到**本人**文件里"
        assert "码农_m1" in t, "要写清他的隔间（唯一可写目录）"
        assert "去哪看全局" in t and "plan.md" in t, "要指路到全员分工"
        t0 = (member_assignment_dir(ws, ws.members[0]) / "assignment.md").read_text(encoding="utf-8")
        assert "做前端表格" not in t0, "别人（组长）的文件里不许出现码农的活"
        assert "尚未对你细分派" in t0

    def test_duplicate_names_do_not_collide(self, tmp_path):
        from agent_community.platform.workshop import (Workshop, WorkshopMember, write_library,
                                                      member_assignment_dir)
        ws = Workshop(workshop_id="w2", name="x", workspace_dir=str(tmp_path / "ws2"), hall_content="x",
                      members=[WorkshopMember(member_id="m0", role="码农", display_name="小码"),
                               WorkshopMember(member_id="m1", role="码农", display_name="小码")])
        write_library(ws)
        a, b = (member_assignment_dir(ws, m) for m in ws.members)
        assert a != b, "重名必须兜底成不同目录，否则会互相覆盖"

    def test_activation_message_carries_paths_but_not_contents(self, tmp_path):
        """激活消息只带**路径**，不带分工正文（老板：通用模板，只更新各种路径）。"""
        from agent_community.platform import server as srv
        from agent_community.platform.workshop import member_assignment_dir
        ws = self._ws(tmp_path)
        ws.assignments = {"m1": [{"kind": "任务", "instruction": "做前端表格"}]}
        m = ws.members[1]
        p = srv._activation_prompt("h1", m.role, ws.workspace_dir, ws.workshop_id, m.member_id,
                                   "", str(tmp_path / "ws" / "码农_m1"),
                                   library_dir=str(tmp_path / "ws" / "library"),
                                   assignment_dir=str(member_assignment_dir(ws, m)),
                                   session_name="w1-看板项目-码农-m1", workshop_name="看板项目")
        for must in ("这是什么平台", "共享资料库", "你的分工文件夹", "小码-码农",
                     "拿着上面的路径自己去读", "通用模板"):
            assert must in p, f"激活消息缺少「{must}」"
        assert "做前端表格" not in p, "**不许**把分工正文贴进消息里"

    def test_activation_message_unchanged_without_paths(self, tmp_path):
        """向后兼容：不传路径时行为与以前一致（老调用方不受影响）。"""
        from agent_community.platform import server as srv
        ws = self._ws(tmp_path)
        p = srv._activation_prompt("h1", "码农", ws.workspace_dir, ws.workshop_id, "m1")
        assert "这是什么平台" not in p and "已进入工作间" in p


    def test_leader_activation_path_also_carries_paths(self, tmp_path, monkeypatch):
        """★防"只改一条激活路径"：本项目已两次栽在这上面（自动架桥一次、路径提示一次）。

        `_activate_workshop` 会把**名单第一人兜底当组长**并跳过员工分支 ⇒ 只测员工路径会漏。
        故这里直接钉住**组长路径**派发的消息也要带那四个要点。
        """
        from agent_community.platform import server as srv
        from agent_community.platform.compartment import ensure_compartment
        from agent_community.platform.workshop import Workshop, WorkshopMember, write_library
        ws = Workshop(workshop_id="w9", name="组长路径", workspace_dir=str(tmp_path / "ws9"),
                      hall_content="x", created_at="now",
                      members=[WorkshopMember(member_id="m0", role="组长", display_name="老组",
                                              harness_ids=["h1"])])
        write_library(ws)
        ensure_compartment(ws.workspace_dir, "组长", "m0")
        captured = {}

        def fake_dispatch(hid, payload, kind=""):
            captured["payload"] = payload
            return True, "mocked"
        monkeypatch.setattr(srv, "_dispatch_to_harness", fake_dispatch, raising=False)
        monkeypatch.setattr(srv.harness_manager, "sessions", {"h1": _SessStub()}, raising=False)
        srv._activate_leader_if_needed(ws)
        msg = str((captured.get("payload") or {}).get("message") or "")
        assert msg, "组长路径应当派发了激活消息"
        for must in ("这是什么平台", "共享资料库", "你的分工文件夹", "老组-组长",
                     "拿着上面的路径自己去读", "通用模板"):
            assert must in msg, f"**组长路径**的激活消息缺少「{must}」"


class TestAutoSessionSetupOnStart:
    """`/start` 开工时**自动跑步骤 4/7/8/9**（老板流程：「4. 自动按 harness 模板打开新对话」）。

    ★只在**安全**时自动做：配了 `new_session` 脚本 **且** 该动作 `foreground != required`
    （不会抢用户桌面）。会弹窗的那类**跳过**——点一下"开工"就把人家窗口弹出来，
    违反老板"不要弹出来影响用户"那条要求。
    """

    def _ws(self, tmp_path, members):
        from agent_community.platform.workshop import Workshop
        return Workshop(workshop_id="w1", name="自动建会话", workspace_dir=str(tmp_path / "ws"),
                        hall_content="x", created_at="now", members=members)

    def _call(self, monkeypatch, ws, sessions, fake=None, calls=None):
        from agent_community.platform import server as srv
        import agent_community.platform.routers.workshops_session as ws_mod
        monkeypatch.setattr(srv.harness_manager, "sessions", sessions, raising=False)
        real = ws_mod.establish_one

        def spy(w, m, **kw):
            if calls is not None:
                calls.append(m.member_id)
            return fake(w, m, **kw) if fake else real(w, m, **kw)
        monkeypatch.setattr(ws_mod, "establish_one", spy, raising=False)
        return run(srv._session_setup_for_workshop(ws))

    def _member(self, mid="m0", role="码农", hid="h1"):
        from agent_community.platform.workshop import WorkshopMember
        return WorkshopMember(member_id=mid, role=role, display_name="小", harness_ids=[hid])

    def test_auto_runs_when_configured_and_not_foreground(self, tmp_path, monkeypatch):
        from agent_community.platform import action_scripts as acts
        s = _SessStub("h1")
        acts.set_config(s, "new_session", {"NEW_SESSION_CMD": "x"}, foreground="never")
        calls = []
        out = self._call(monkeypatch, self._ws(tmp_path, [self._member()]), {"h1": s},
                         fake=lambda w, m, **kw: {"success": True, "all_verified": True,
                                                  "name": "n"}, calls=calls)
        assert out["m0"] == "auto_ok" and calls == ["m0"], "配了脚本且不抢桌面 ⇒ 应当自动跑"

    def test_skips_when_action_would_grab_the_desktop(self, tmp_path, monkeypatch):
        """**会占用桌面的动作不许自动做**（否则点一下开工就把用户窗口弹出来）。"""
        from agent_community.platform import action_scripts as acts
        s = _SessStub("h1")
        acts.set_config(s, "new_session", {"NEW_SESSION_CMD": "x"}, foreground="required")
        calls = []
        out = self._call(monkeypatch, self._ws(tmp_path, [self._member()]), {"h1": s},
                         fake=lambda w, m, **kw: {"success": True, "all_verified": True},
                         calls=calls)
        assert out["m0"] == "skipped_foreground" and calls == [], "不抢桌面才自动；这条必须跳过"

    def test_manual_when_not_configured(self, tmp_path, monkeypatch):
        calls = []
        out = self._call(monkeypatch, self._ws(tmp_path, [self._member()]), {"h1": _SessStub("h1")},
                         calls=calls)
        assert out["m0"] == "manual" and calls == []

    def test_already_established_is_skipped(self, tmp_path, monkeypatch):
        """★V28 改了这里的判据：**只看"当前是否有运行中的通道"**，不再只看"有没有会话名"。

        为什么改：停用通道后旧名字仍留在 `session_aliases` 里，若还按"有名字就跳过"，
        这里会**永远跳过** ⇒ 老板要的"换对话"根本建不起来（V28 §1 的缺口）。
        故：`channel_state == "active"` ⇒ 跳过（别每次开工白跑子进程）；
        非 active（含 pending / retired）⇒ **照常重建**（见下一条测试）。
        """
        from agent_community.platform import action_scripts as acts
        s = _SessStub("h1")
        acts.set_config(s, "new_session", {"NEW_SESSION_CMD": "x"}, foreground="never")
        m = self._member()
        m.channel_state = "active"                 # V28：跳过条件 = 已有运行中的通道
        m.session_aliases = ["w1-自动建会话-码农-m0"]
        calls = []
        out = self._call(monkeypatch, self._ws(tmp_path, [m]), {"h1": s},
                         fake=lambda w, mm, **kw: {"success": True, "all_verified": True},
                         calls=calls)
        assert out["m0"] == "already" and calls == [], "已有运行中的通道 ⇒ 别每次开工都白跑子进程"

    def test_retired_channel_is_rebuilt_on_start(self, tmp_path, monkeypatch):
        """★停用过的成员：开工时**要能重建**（否则换不了对话）。"""
        from agent_community.platform import action_scripts as acts
        s = _SessStub("h1")
        acts.set_config(s, "new_session", {"NEW_SESSION_CMD": "x"}, foreground="never")
        m = self._member()
        m.channel_state = "retired"
        m.session_aliases = ["w1-自动建会话-码农-m0"]   # 旧名字还在，但不该阻止重建
        calls = []
        out = self._call(monkeypatch, self._ws(tmp_path, [m]), {"h1": s},
                         fake=lambda w, mm, **kw: {"success": True, "all_verified": True,
                                                   "name": kw.get("new_name", "")},
                         calls=calls)
        assert out["m0"] == "auto_ok" and calls == ["m0"], "停用过的通道必须能重建"

    def test_unverified_is_not_reported_as_auto_ok(self, tmp_path, monkeypatch):
        from agent_community.platform import action_scripts as acts
        s = _SessStub("h1")
        acts.set_config(s, "new_session", {"NEW_SESSION_CMD": "x"}, foreground="never")
        out = self._call(monkeypatch, self._ws(tmp_path, [self._member()]), {"h1": s},
                         fake=lambda w, m, **kw: {"success": True, "all_verified": False,
                                                  "manual": [{"step": 4}]})
        assert out["m0"] == "unverified", "没自证就不许记成 auto_ok（准则⑤）"

    def test_failure_is_isolated_per_member(self, tmp_path, monkeypatch):
        from agent_community.platform import action_scripts as acts
        s = _SessStub("h1")
        acts.set_config(s, "new_session", {"NEW_SESSION_CMD": "x"}, foreground="never")

        def boom(w, m, **kw):
            if m.member_id == "m0":
                raise RuntimeError("炸了")
            return {"success": True, "all_verified": True}
        out = self._call(monkeypatch, self._ws(tmp_path, [self._member("m0"), self._member("m1")]),
                         {"h1": s}, fake=boom)
        assert out["m0"].startswith("failed:") and out["m1"] == "auto_ok", "一个炸了不影响别人"


class TestUnknownHarnessIsNotFaked:
    """平台**完全不认识**的 harness（没注册、也没桥进程）⇒ 不许回"已派发"（准则⑤）。

    2026-10-08 首次接入审计发现：成员绑一个从未注册的 harness 时，旧逻辑走到 pending 队列分支
    并回「已入 pending 队列（桥存活未知）」⇒ `entry_state=dispatched`，
    而**实际上没有任何人能收到这条消息**（隔间/桥/邮箱全空）。

    ⚠️ **两条激活路径都要拦**：员工 `_activate_single` **和** 组长 `_activate_leader_if_needed`
    —— `_activate_workshop` 会把**名单第一人兜底当组长**，只测员工路径会漏
    （本项目已在"自动架桥""路径提示"上栽过两次，这是第三次同类）。
    """

    def _clean(self):
        from agent_community.platform import bridge_procs, server as srv
        bridge_procs._procs.clear()
        srv.harness_manager.sessions.pop("h_ghost", None)
        return srv

    def test_employee_path_blocks_unknown_harness(self):
        from agent_community.platform import entry_state as es
        from agent_community.platform.workshop import Workshop, WorkshopMember
        srv = self._clean()
        ws = Workshop(workshop_id="w1", name="t", workspace_dir="D:\\ws\\w1", hall_content="x")
        m = WorkshopMember(member_id="m1", role="码农", display_name="小", harness_ids=["h_ghost"])
        ws.members.append(m)
        srv._activate_single(ws, m)
        assert m.entry_state == es.BLOCKED, f"不能回 dispatched，实得 {m.entry_state}"
        assert "不认识" in m.entry_reason and "h_ghost" in m.entry_reason

    def test_leader_path_blocks_unknown_harness(self):
        from agent_community.platform import entry_state as es
        from agent_community.platform.workshop import Workshop, WorkshopMember
        srv = self._clean()
        ws = Workshop(workshop_id="w2", name="t", workspace_dir="D:\\ws\\w2", hall_content="x")
        leader = WorkshopMember(member_id="m0", role="组长", display_name="老", harness_ids=["h_ghost"])
        ws.members.append(leader)
        reply, ok, _ld = srv._activate_leader_if_needed(ws)
        assert ok is False, "组长派不出去时不许回 ok=True"
        assert leader.entry_state == es.BLOCKED
        assert "不认识" in leader.entry_reason
        assert "不假装" in reply or "不认识" in reply

    def test_registered_harness_is_not_blocked_by_this_gate(self):
        """有会话记录 ⇒ 不该被这条拦截误伤（防过度拦截）。"""
        from agent_community.platform.workshop import Workshop, WorkshopMember
        srv = self._clean()
        srv.harness_manager.sessions["h_known"] = _SessStub("h_known")
        ws = Workshop(workshop_id="w3", name="t", workspace_dir="D:\\ws\\w3", hall_content="x")
        m = WorkshopMember(member_id="m1", role="码农", display_name="小", harness_ids=["h_known"])
        ws.members.append(m)
        srv._activate_single(ws, m)
        assert "不认识" not in (m.entry_reason or ""), "认识的 harness 不该被误拦"


class TestSharedLibrary:
    """老板主流程 ⑥：**共享资料库**（老板 2026-10-07：「可以是一个自动建立的文件夹，
    也可以是 git 之类的库」——本实现取前者，布局对 git 向后兼容）。"""

    def _ws(self, tmp_path, members=True):
        ws = Workshop(workshop_id="w1", name="看板", workspace_dir=str(tmp_path / "ws_1"),
                      hall_content="做一个信誉看板", created_at="now")
        if members:
            ws.members = [
                WorkshopMember(member_id="m0", role="组长", display_name="L", harness_ids=["h0"]),
                WorkshopMember(member_id="m1", role="码农", display_name="C", harness_ids=["h1"]),
            ]
        return ws

    def test_created_alongside_the_shared_files(self, tmp_path):
        from agent_community.platform.workshop import LIBRARY_DIRNAME, write_workspace_files
        ws = self._ws(tmp_path)
        write_workspace_files(ws)
        lib = Path(ws.workspace_dir) / LIBRARY_DIRNAME
        assert lib.is_dir(), "资料库属共享区，应与 hall.md 一起在建间时建好"
        names = sorted(p.name for p in lib.iterdir())
        for must in (".gitignore", "README.md", "framework.md", "plan.md"):
            assert must in names, f"共享资料库缺少 {must}"
        # 2026-10-08 起，资料库里**还会有每个员工的分工文件夹**（`<agent名>-<职位>/`），
        # 故断言"这四个文件在"，而不是"只有这四个"（老板要求新增员工文件夹）。
        dirs = sorted(p.name for p in lib.iterdir() if p.is_dir())
        assert any("-" in d for d in dirs), f"应有 <agent名>-<职位> 形式的分工文件夹，实得 {dirs}"

    def test_plan_lists_members_compartments_and_assignments(self, tmp_path):
        from agent_community.platform.workshop import LIBRARY_DIRNAME, write_library
        ws = self._ws(tmp_path)
        ws.assignments = {"m0": [{"kind": "细化", "instruction": "前端归你", "note": "2天"}]}
        write_library(ws)
        plan = (Path(ws.workspace_dir) / LIBRARY_DIRNAME / "plan.md").read_text(encoding="utf-8")
        assert "组长_m0" in plan and "码农_m1" in plan, "分工安排必须写明每人隔间"
        assert "前端归你" in plan and "2天" in plan, "组长委派记录要进资料库"
        assert "做一个信誉看板" in plan, "任务原文要在库里"
        assert "请勿手改" in plan, "要写明它是自动生成的"

    def test_framework_not_overwritten_without_explicit_content(self, tmp_path):
        from agent_community.platform.workshop import LIBRARY_DIRNAME, write_library
        ws = self._ws(tmp_path)
        write_library(ws, framework="# 框架\n三层：接入/治理/协议")
        write_library(ws)                       # 不传内容
        fw = (Path(ws.workspace_dir) / LIBRARY_DIRNAME / "framework.md").read_text(encoding="utf-8")
        assert "三层：接入/治理/协议" in fw, "人的编辑不该被平台的例行刷新覆盖"

    def test_failure_does_not_raise(self, tmp_path):
        from agent_community.platform.workshop import write_library
        f = tmp_path / "iamafile"
        f.write_text("x", encoding="utf-8")
        ws = Workshop(workshop_id="w", name="n", workspace_dir=str(f), hall_content="h")
        out = write_library(ws)
        assert out["ok"] is False and out["error"]


class TestLibraryEndpoints:
    def _mk(self, tmp_path, monkeypatch):
        from agent_community.platform.workshop import write_workspace_files
        ws = Workshop(workshop_id="w1", name="看板", workspace_dir=str(tmp_path / "ws_1"),
                      hall_content="做一个信誉看板", created_at="now",
                      members=[WorkshopMember(member_id="m0", role="组长",
                                              display_name="L", harness_ids=["h0"])])
        write_workspace_files(ws)
        import agent_community.platform.routers.workshops_lifecycle as wl
        monkeypatch.setattr(wl, "workshops", {"w1": ws}, raising=False)
        return ws

    def test_get_lists_member_assignment_folders(self, tmp_path, monkeypatch):
        """★2026-10-08 补：库视图必须**列出子目录**（每个员工的 `<agent名>-<职位>/` 分工文件夹）。

        原实现只列文件 ⇒ 界面上**永远看不到员工分工文件夹**——老板明确要的东西，接口却没给。
        """
        from agent_community.platform.routers.workshops_lifecycle import workshop_library
        self._mk(tmp_path, monkeypatch)
        out = run(workshop_library("w1"))
        dirs = {d["name"]: d for d in out.get("directories", [])}
        assert "L-组长" in dirs, f"应列出 <agent名>-<职位> 分工文件夹，实得 {list(dirs)}"
        assert dirs["L-组长"]["count"] >= 1
        assert "assignment.md" in [f["name"] for f in dirs["L-组长"]["files"]]
        assert "我要做的事" in dirs["L-组长"].get("assignment_preview", "")
        # 向后兼容：老字段照旧
        assert isinstance(out["files"], list) and isinstance(out["preview"], dict)

    def test_get_404_for_unknown(self, monkeypatch):
        from agent_community.platform.routers.workshops_lifecycle import workshop_library
        import agent_community.platform.routers.workshops_lifecycle as wl
        monkeypatch.setattr(wl, "workshops", {}, raising=False)
        assert run(workshop_library("nope")).status_code == 404

    def test_get_reports_dir_files_and_preview(self, tmp_path, monkeypatch):
        from agent_community.platform.routers.workshops_lifecycle import workshop_library
        ws = self._mk(tmp_path, monkeypatch)
        out = run(workshop_library("w1"))
        assert out["exists"] is True and out["form"].startswith("folder")
        assert {f["name"] for f in out["files"]} >= {"plan.md", "framework.md", "README.md"}
        assert "分工安排" in out["preview"]["plan.md"]

    def test_update_writes_framework_and_regenerates_plan(self, tmp_path, monkeypatch):
        from agent_community.platform.routers.workshops_lifecycle import workshop_library_update

        class _Req:
            def __init__(self, b): self._b = b
            async def json(self): return self._b

        ws = self._mk(tmp_path, monkeypatch)
        out = run(workshop_library_update("w1", _Req({"framework": "# 框架\n三层"})))
        assert out["success"] and "已写入" in out["note"]
        from agent_community.platform.workshop import LIBRARY_DIRNAME
        lib = Path(ws.workspace_dir) / LIBRARY_DIRNAME
        assert "三层" in (lib / "framework.md").read_text(encoding="utf-8")

    def test_update_without_framework_keeps_existing(self, tmp_path, monkeypatch):
        from agent_community.platform.routers.workshops_lifecycle import workshop_library_update

        class _Req:
            async def json(self): return {}

        ws = self._mk(tmp_path, monkeypatch)
        from agent_community.platform.workshop import LIBRARY_DIRNAME, write_library
        write_library(ws, framework="# 人工写的框架")
        out = run(workshop_library_update("w1", _Req()))
        assert "未传 framework" in out["note"]
        lib = Path(ws.workspace_dir) / LIBRARY_DIRNAME
        assert "人工写的框架" in (lib / "framework.md").read_text(encoding="utf-8")


class TestActivateRemainingForReview:
    """老板主流程 ⑦：开三级讨论要**激活其他员工**（各组自己的会话 + 隔间里的桥）。

    实测（2026-10-07）：此前进 review 只改状态 + 发 notice，**不激活任何员工**；
    `_dispatch_leader_review_discuss` 还只认组长、组长没进场就静默跳过 ⇒
    三级讨论里除了组长没人能发言。
    """

    def _ws(self, statuses):
        ws = Workshop(workshop_id="w", name="n", workspace_dir="/x",
                      hall_content="h", status="running")
        ws.members = [
            WorkshopMember(member_id=f"m{i}", role="员工", display_name=f"d{i}",
                           harness_ids=[f"h{i}"], status=s)
            for i, s in enumerate(statuses)
        ]
        return ws

    def test_only_not_yet_in_members_get_pulled_in(self, monkeypatch):
        from agent_community.platform import server as srv
        calls = []
        monkeypatch.setattr(srv, "_activate_single",
                            lambda ws, m: calls.append(m.member_id), raising=False)
        ws = self._ws(["pending", "entered", "working", "blocked",
                       "activating", "idle", "completed"])
        got = srv._activate_remaining_members(ws, reason="review")
        assert got == ["m0", "m3"], "只该拉 pending 与 blocked"
        assert calls == ["m0", "m3"], "**activating 的人不许被打断**（那是半路重派）"

    def test_one_failure_does_not_stop_the_rest(self, monkeypatch):
        from agent_community.platform import server as srv

        def _fake(ws, m):
            if m.member_id == "m1":
                raise RuntimeError("boom")
        monkeypatch.setattr(srv, "_activate_single", _fake, raising=False)
        ws = self._ws(["pending", "pending", "pending"])
        assert srv._activate_remaining_members(ws, reason="review") == ["m0", "m2"]

    def test_no_members_is_safe(self, monkeypatch):
        from agent_community.platform import server as srv
        ws = self._ws([])
        assert srv._activate_remaining_members(ws, reason="review") == []

    def test_all_already_in_activates_nobody(self, monkeypatch):
        from agent_community.platform import server as srv
        monkeypatch.setattr(srv, "_activate_single",
                            lambda ws, m: pytest.fail("不该激活任何人"), raising=False)
        ws = self._ws(["entered", "working", "idle", "completed"])
        assert srv._activate_remaining_members(ws, reason="review") == []

    def test_writes_audit_record(self, monkeypatch):
        from agent_community.platform import server as srv
        from agent_community.platform.audit import audit_log
        monkeypatch.setattr(srv, "_activate_single", lambda ws, m: None, raising=False)
        ws = self._ws(["pending"])
        srv._activate_remaining_members(ws, reason="review")
        evs = audit_log.query(limit=10, event="workshop.activate_remaining", target="w")
        assert evs and evs[0]["activated"] == ["m0"] and evs[0]["reason"] == "review"


class TestL1TransitionFallback:
    """一级讨论 → 选定员工 的流转兜底（2026-10-07 实测异常驱动）。

    实测：用户明确说"需求明确了，开始选人吧"，AI 两轮都没吐那句口令
    「需求已明确，可以选定员工」-> `action` 恒为 "none"。**靠 LLM 背口令驱动流程不可靠**，
    故加纯规则兜底，并把"谁决定的"标成可审计的 `action_source`。
    """

    def _ws(self, status="discussing"):
        return Workshop(workshop_id="w", name="n", workspace_dir="/x",
                        hall_content="h", status=status)

    @pytest.mark.parametrize("msg", [
        "就按你说的做，需求明确了，开始选人吧。",
        "需求已明确",
        "挑人吧",
        "选成员",
        "就这样，没问题了",
    ])
    def test_explicit_go_ahead_triggers_select(self, msg):
        from agent_community.platform.routers.workshops_discuss import _l1_action_by_rule
        assert _l1_action_by_rule(msg, self._ws()) == "select"

    @pytest.mark.parametrize("msg", [
        "需求明确了，开始选人吧？",
        "为什么要现在选人？",
        "怎么选人",
        "是不是该选人了",
        "能不能先选人",
        "可以开始选人了吗",       # ★写测试时踩到的坑：含关键词、不以 ?/？ 结尾，但仍是疑问
        "现在该选人了吧呢",
        "我们再聊聊细节",
        "",
        "   ",
    ])
    def test_questions_and_chit_chat_do_not_trigger(self, msg):
        """疑问句一律不推进——用户还在问，不该替他拍板。"""
        from agent_community.platform.routers.workshops_discuss import _l1_action_by_rule
        assert _l1_action_by_rule(msg, self._ws()) == ""

    @pytest.mark.parametrize("status", ["division", "review", "running", "draft", "done"])
    def test_only_fires_in_l1_discussing(self, status):
        from agent_community.platform.routers.workshops_discuss import _l1_action_by_rule
        assert _l1_action_by_rule("开始选人吧", self._ws(status)) == ""


class TestPersistenceRoundTrip:
    def test_origin_task_id_survives(self, tmp_path):
        ws = Workshop(workshop_id="w1", name="n", workspace_dir=str(tmp_path),
                      hall_content="h", origin_task_id="task-42")
        back = Workshop.from_dict(ws.to_dict())
        assert back.origin_task_id == "task-42"

    def test_old_records_without_the_field_default_to_empty(self, tmp_path):
        d = Workshop(workshop_id="w1", name="n", workspace_dir=str(tmp_path),
                     hall_content="h").to_dict()
        d.pop("origin_task_id", None)          # 模拟旧数据
        assert Workshop.from_dict(d).origin_task_id == ""
