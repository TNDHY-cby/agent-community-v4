# -*- coding: utf-8 -*-
"""V-13 拆分测试：platform/routers/workshops_discuss.py（讨论 / 插话 / 状态机 15 路由）。

覆盖重点（锁行为，不是烟雾测试）：
  - 15 个路由的「工作间不存在」404 分支；
  - discuss 输入校验（空消息 / 敏感数据拦截）与 AI 分支（未配置 / 调用失败 / 正常 / 二级讨论分流）；
  - 服务端去文本化 action 判定（回复结尾标记句 → action）；
  - review / continue / complete / start 的状态推进、状态机 ctx 与落盘归档；
  - 插话 5 动作（break / execute / resolve / dismiss / status）的合法与非法前置；
  - 插话超过 max_pending_cycles 自动 expired（含「恰好等于阈值不误判」边界）；
  - sm/resume、sm/drop 的前置条件与 dropped 终态不可恢复。

纪律：只写测试文件，不改生产代码；落盘由 conftest.isolate_data_dirs 重定向到 tmp_path。
"""
from __future__ import annotations

import asyncio
import json
import sys
import types
from pathlib import Path

import pytest

from agent_community.platform import server as server_mod
from agent_community.platform import state as state_mod
from agent_community.platform.protocol import TaskStatus
from agent_community.platform.routers import workshops_discuss as wd
from agent_community.platform.workshop import Workshop, WorkshopMember


# ═══════════════════════════════════════════════════════════════
# 基础设施：假请求 / 协程执行 / 响应拆包 / AI 替身
# ═══════════════════════════════════════════════════════════════
class FakeRequest:
    def __init__(self, body=None, headers=None):
        self.headers = headers or {}
        self._body = body or {}

    async def json(self):
        return self._body


def run(coro):
    return asyncio.run(coro)


def unpack(resp):
    """端点可能返回普通 dict，也可能是 Utf8JSONResponse（错误分支）。"""
    if hasattr(resp, "status_code") and hasattr(resp, "body"):
        return resp.status_code, json.loads(resp.body)
    return 200, resp


class FakeAI:
    """最小 AI Provider 替身：chat 立即返回预设文本，并记录调用参数。"""

    provider_type = "fake"

    def __init__(self, reply="好的，收到"):
        self.reply = reply
        self.calls: list = []

    async def chat(self, system_prompt, user_message):
        self.calls.append((system_prompt, user_message))
        return self.reply


def set_ai_provider(monkeypatch, provider):
    """routers/workshops_common._sv() 读 sys.modules['__main__']；
    pytest 下它没有 ai_provider 属性，这里补上（不影响真实 server 语义）。"""
    main = sys.modules.setdefault("__main__", types.ModuleType("__main__"))
    monkeypatch.setattr(main, "ai_provider", provider, raising=False)
    return provider


# ═══════════════════════════════════════════════════════════════
# 夹具：共享可变状态清场 + 工作间工厂
# ═══════════════════════════════════════════════════════════════
@pytest.fixture(autouse=True)
def clean_state(isolate_data_dirs, monkeypatch):
    """每个用例前后清空 workshops / tasks / 插话池 / 状态机，并清空大厅消息。

    显式依赖 conftest 的 isolate_data_dirs，保证 save_state 落盘重定向先于本夹具生效。
    """
    st = state_mod

    def _clear():
        st.workshops.clear()
        st.tasks.clear()
        st.interject_store._items.clear()
        st.interject_store._seq.clear()
        st.interject_store.max_pending_cycles = 3
        st.task_state_machine._states.clear()
        st.task_state_machine.timeout_sec = 300.0
        st.task_state_machine.max_auto_retry = 1
        server_mod.hall_messages.clear()

    _clear()
    set_ai_provider(monkeypatch, None)  # 默认无 AI，用例按需覆盖
    yield
    _clear()


@pytest.fixture()
def mkws(tmp_path):
    """造一个工作间放进 state_mod.workshops；用例结束自动摘除，不污染其它测试。"""
    created = []

    def _mk(ws_id="ws_t", status="draft", members=None,
            hall="把这段文本写成一个单元测试。", name="测试工作间"):
        ws = Workshop(
            workshop_id=ws_id,
            name=name,
            workspace_dir=str(tmp_path / ws_id),
            hall_content=hall,
            members=list(members or []),
            status=status,
        )
        state_mod.workshops[ws_id] = ws
        created.append(ws_id)
        return ws

    yield _mk
    for wid in created:
        state_mod.workshops.pop(wid, None)


def member(mid="m1", role="码农", name="小王", hids=None, status="pending"):
    return WorkshopMember(member_id=mid, role=role, display_name=name,
                          harness_ids=list(hids or []), status=status)


# ═══════════════════════════════════════════════════════════════
# 一、15 个路由：工作间不存在 → 404 + 具体错误文案
# ═══════════════════════════════════════════════════════════════
_MISSING_WS_CALLS = {
    "discuss": lambda wid: wd.workshop_discuss(wid, FakeRequest({"message": "你好"})),
    "review": lambda wid: wd.workshop_review(wid),
    "continue": lambda wid: wd.workshop_continue(wid),
    "complete": lambda wid: wd.workshop_complete(wid),
    "start": lambda wid: wd.start_workshop(wid),
    "interject_submit": lambda wid: wd.submit_interject(wid, FakeRequest({"content": "插一句"})),
    "interjects_list": lambda wid: wd.list_interjects(wid),
    "interject_break": lambda wid: wd.interject_break(wid, "it_1"),
    "interject_execute": lambda wid: wd.interject_execute(wid, "it_1"),
    "interject_resolve": lambda wid: wd.interject_resolve(wid, "it_1"),
    "interject_dismiss": lambda wid: wd.interject_dismiss(wid, "it_1"),
    "interject_status": lambda wid: wd.interject_status(wid, "it_1", FakeRequest({"status": "inserted"})),
    "sm_resume": lambda wid: wd.sm_resume(wid),
    "sm_drop": lambda wid: wd.sm_drop(wid),
    "sm_status": lambda wid: wd.sm_status(wid),
}


@pytest.mark.parametrize("name", sorted(_MISSING_WS_CALLS))
def test_workshop_missing_returns_404(name):
    status, payload = unpack(run(_MISSING_WS_CALLS[name]("no_such_ws")))
    assert status == 404, f"{name}：工作间不存在必须 404，实得 {status}"
    assert payload["error"] == "工作间不存在", f"{name}：错误文案必须明确"


# ═══════════════════════════════════════════════════════════════
# 二、discuss：校验 / 敏感拦截 / AI 分支 / action 判定
# ═══════════════════════════════════════════════════════════════
class TestDiscuss:

    def test_empty_message_400_and_nothing_written(self, mkws, monkeypatch):
        set_ai_provider(monkeypatch, FakeAI())
        ws = mkws(status="discussing")
        status, payload = unpack(run(wd.workshop_discuss("ws_t", FakeRequest({"message": "   "}))))
        assert status == 400
        assert payload["error"] == "消息不能为空"
        assert ws.discussion == [], "校验失败不得写入讨论区"

    def test_sensitive_message_400_and_status_untouched(self, mkws, monkeypatch):
        """V-25 F3：敏感数据在状态改写之前拦截，工作间状态不能被改写。"""
        set_ai_provider(monkeypatch, FakeAI())
        ws = mkws(status="draft")
        status, payload = unpack(run(wd.workshop_discuss(
            "ws_t", FakeRequest({"message": "这是我的 key: sk-abcdefghijklmnop123456"}))))
        assert status == 400
        assert payload["success"] is False
        assert "检测到敏感数据" in payload["error"]
        assert ws.discussion == [], "敏感内容不得落讨论区"
        assert ws.status == "draft", "拦截分支必须在 ws.status 改写之前返回"

    def test_no_ai_provider_400(self, mkws, monkeypatch):
        set_ai_provider(monkeypatch, None)
        mkws(status="discussing")
        status, payload = unpack(run(wd.workshop_discuss("ws_t", FakeRequest({"message": "聊需求"}))))
        assert status == 400
        assert payload["error"] == "AI Provider 未配置"

    def test_ai_failure_502_and_reply_not_appended(self, mkws, monkeypatch):
        """V-25 F4：AI 错误文本不得冒充正常回复写进讨论区。"""
        set_ai_provider(monkeypatch, FakeAI(reply="[Error: HTTP 401]"))
        ws = mkws(status="discussing")
        status, payload = unpack(run(wd.workshop_discuss("ws_t", FakeRequest({"message": "聊需求"}))))
        assert status == 502
        assert payload["success"] is False
        assert payload["error"] == "AI 服务不可用（密钥失效或后端错误）"
        assert "[Error: HTTP 401]" in payload["detail"]
        assert [m["role"] for m in ws.discussion] == ["user"], "失败文本不得落讨论区"

    def test_normal_path_structure_and_zone(self, mkws, monkeypatch):
        ai = set_ai_provider(monkeypatch, FakeAI(reply="需求已明确，可以选定员工。"))
        ws = mkws(status="discussing")
        status, payload = unpack(run(wd.workshop_discuss(
            "ws_t", FakeRequest({"message": "我想做一个爬虫"}))))
        assert status == 200
        assert payload["success"] is True
        assert payload["stage"] == "orchestrator"
        assert payload["status"] == "discussing"
        assert payload["zone"] == 1
        assert payload["reply"] == "需求已明确，可以选定员工。"
        assert payload["action"] == "select"
        assert [m["role"] for m in ws.discussion] == ["user", "orchestrator"]
        assert [m["zone"] for m in ws.discussion] == [1, 1]
        assert ai.calls and "我想做一个爬虫" in ai.calls[0][1], "AI context 必须带用户消息"

    @pytest.mark.parametrize("marker,expected_action", [
        ("任务已完成", "complete"),
        ("可以继续工作", "continue"),
    ])
    def test_reply_marker_maps_to_action(self, mkws, monkeypatch, marker, expected_action):
        """去文本化：服务端按回复结尾标记句判定 action，前端不解析文本。"""
        set_ai_provider(monkeypatch, FakeAI(reply=f"……{marker}。"))
        mkws(status="discussing")
        _, payload = unpack(run(wd.workshop_discuss("ws_t", FakeRequest({"message": "继续推进"}))))
        assert payload["action"] == expected_action, f"标记「{marker}」必须映射为 {expected_action}"

    def test_division_branch_skips_platform_ai(self, mkws, monkeypatch):
        """二级讨论：平台 AI 退出，无 ai_provider 也必须成功返回。"""
        set_ai_provider(monkeypatch, None)
        ws = mkws(status="division")  # 名单为空 → 组长无法激活
        status, payload = unpack(run(wd.workshop_discuss(
            "ws_t", FakeRequest({"message": "大家怎么看"}))))
        assert status == 200
        assert payload["success"] is True
        assert payload["stage"] == "leader_pending"
        assert payload["leader_activated"] is False
        assert payload["zone"] == 2
        assert payload["action"] == "none"
        assert ws.status == "division", "division 态不得被改写"
        assert "无法激活组长" in payload["reply"]


# ═══════════════════════════════════════════════════════════════
# 三、review / continue / complete / start
# ═══════════════════════════════════════════════════════════════
class TestLifecycle:

    def test_review_sets_status_state_machine_and_resets_flags(self, mkws):
        ws = mkws(status="running")
        status, payload = unpack(run(wd.workshop_review("ws_t")))
        assert status == 200
        assert payload == {"success": True, "status": "review", "action": "review"}
        assert ws.status == "review"
        sm = state_mod.task_state_machine
        assert sm.get_state("ws_t") == "discussing"
        assert sm._states["ws_t"]["ctx"]["stage"] == "review"
        # 三个防重标记必须重置，否则 poll 兜底唤醒与后续自动进入 review 都会失效
        assert ws._review_notified is False
        assert ws._auto_review_done is False
        assert ws._review_suff_notified is False
        last = ws.discussion[-1]
        assert last["role"] == "notice" and last["zone"] == 3
        assert "【三级讨论】" in last["content"]

    def test_continue_returns_running_and_writes_review_archive(self, mkws):
        ws = mkws(status="review")
        status, payload = unpack(run(wd.workshop_continue("ws_t")))
        assert status == 200
        assert payload == {"success": True, "status": "running", "action": "running"}
        assert ws.status == "running"
        assert any("【继续工作】" in m["content"] and m["zone"] == 2 for m in ws.discussion)
        archive = Path(ws.workspace_dir) / "REVIEW.md"
        assert archive.exists(), "continue 必须把阶段复盘归档为 REVIEW.md"
        text = archive.read_text(encoding="utf-8")
        assert "阶段复盘归档" in text and "继续工作" in text

    def test_complete_from_executing_reaches_done_and_writes_summary(self, mkws):
        ws = mkws(status="running")
        sm = state_mod.task_state_machine
        sm.set_state("ws_t", "executing", stage="running")
        status, payload = unpack(run(wd.workshop_complete("ws_t")))
        assert status == 200
        assert payload == {"success": True, "status": "done", "action": "complete"}
        assert ws.status == "done"
        assert sm.get_state("ws_t") == "done", "complete 必须把状态机推到 done 终态"
        arch = Path(ws.workspace_dir) / "FINAL_SUMMARY.md"
        assert arch.exists(), "complete 必须落盘 FINAL_SUMMARY.md"
        assert "任务完成总结" in arch.read_text(encoding="utf-8")

    def test_start_sets_executing_and_is_idempotent(self, mkws):
        ws = mkws(status="draft")
        sm = state_mod.task_state_machine
        status, payload = unpack(run(wd.start_workshop("ws_t")))
        assert status == 200
        assert payload == {"success": True, "status": "running", "action": "running"}
        assert ws.status == "running"
        assert sm.get_state("ws_t") == "executing"
        # 幂等：已在运行 → 只回 note，不改写状态机
        sm.set_state("ws_t", "discussing", stage="marker")
        status2, payload2 = unpack(run(wd.start_workshop("ws_t")))
        assert status2 == 200
        assert payload2 == {"success": True, "status": "running", "action": "running",
                            "note": "已在运行"}
        assert sm.get_state("ws_t") == "discussing", "已在运行时不得改写状态机"


# ═══════════════════════════════════════════════════════════════
# 四、插话：提交 / 列表过期 / 5 个动作
# ═══════════════════════════════════════════════════════════════
class TestInterject:

    def test_submit_validation_and_priority_fallback(self, mkws):
        ws = mkws(status="discussing")
        status, payload = unpack(run(wd.submit_interject(
            "ws_t", FakeRequest({"content": "   "}))))
        assert status == 200
        assert payload == {"success": False, "error": "内容为空"}
        assert state_mod.interject_store.list("ws_t") == [], "空内容不得建条目"

        # 非法优先级回落「一般」；讨论阶段「一般」→ allowed False / action=pool，不注入
        status2, payload2 = unpack(run(wd.submit_interject(
            "ws_t", FakeRequest({"content": "随手记一下", "priority": "超级紧急"}))))
        assert status2 == 200 and payload2["success"] is True
        assert payload2["interject"]["priority"] == "一般"
        assert payload2["judge"]["allowed"] is False
        assert payload2["judge"]["action"] == "pool"
        assert payload2["interject"]["status"] == "pending"
        assert ws.discussion == [], "未被允许的插话不得注入讨论区"

    def test_submit_urgent_injects_and_marks_inserted(self, mkws):
        ws = mkws(status="running")  # phase=executing，紧急 → insert_after_subtask
        status, payload = unpack(run(wd.submit_interject(
            "ws_t", FakeRequest({"content": "线上挂了，先修", "priority": "紧急"}))))
        assert status == 200
        assert payload["judge"]["allowed"] is True
        assert payload["judge"]["action"] == "insert_after_subtask"
        it = state_mod.interject_store.get("ws_t", payload["interject"]["id"])
        assert it["status"] == "inserted"
        assert it["inserted_at"], "注入后必须回填 inserted_at"
        assert len(ws.discussion) == 1
        assert ws.discussion[0]["zone"] == 1
        assert "【紧急插话】" in ws.discussion[0]["content"]

    def test_list_interjects_expires_backlog_over_limit(self, mkws):
        """超过 max_pending_cycles（默认 3）的积压插话自动 expired；恰好 3 条不误判。"""
        mkws(status="discussing")
        store = state_mod.interject_store
        for i in range(3):
            run(wd.submit_interject("ws_t", FakeRequest(
                {"content": f"想法{i}", "priority": "一般"})))
        status, payload = unpack(run(wd.list_interjects("ws_t")))
        assert status == 200 and payload["success"] is True
        assert [it["status"] for it in payload["interjects"]] == ["pending"] * 3, \
            "恰好等于阈值不得误判过期"

        run(wd.submit_interject("ws_t", FakeRequest(
            {"content": "想法3", "priority": "一般"})))
        status2, payload2 = unpack(run(wd.list_interjects("ws_t")))
        assert status2 == 200 and len(payload2["interjects"]) == 4
        assert store.get("ws_t", "it_4")["status"] == "expired", \
            "超出 max_pending_cycles 的积压插话必须自动过期"
        assert store.get("ws_t", "it_1")["status"] == "pending", "队首不得被误过期"

    def test_break_requires_pending_and_injects(self, mkws):
        ws = mkws(status="running")
        sm = state_mod.task_state_machine
        sm.set_state("ws_t", "executing", stage="running")
        it = state_mod.interject_store.submit("ws_t", "紧急事项", priority="紧急")

        s0, p0 = unpack(run(wd.interject_break("ws_t", "it_404")))
        assert s0 == 404 and p0["error"] == "插话不存在"

        s1, p1 = unpack(run(wd.interject_break("ws_t", it["id"])))
        assert s1 == 200 and p1["success"] is True
        assert p1["note"] == "已直接打断当前工作循环"
        assert state_mod.interject_store.get("ws_t", it["id"])["status"] == "inserted"
        assert "【紧急插话】" in ws.discussion[-1]["content"]
        # break 走 EV_TIMEOUT：executing 首次超时 → 自动重试态（不是直接暂停）
        assert sm.get_state("ws_t") == "blocked-retrying"

        # 非法前置：已 inserted → 拒绝，且状态机不得再动
        sm.set_state("ws_t", "executing")
        s2, p2 = unpack(run(wd.interject_break("ws_t", it["id"])))
        assert s2 == 200 and p2 == {"success": False, "error": "已在流程中"}
        assert sm.get_state("ws_t") == "executing", "非法前置不得触发状态机事件"

    def test_execute_creates_task_only_from_pending(self, mkws, monkeypatch):
        mkws(status="running")
        sm = state_mod.task_state_machine
        sm.set_state("ws_t", "executing")
        # 无 AI Provider → 回退广播；打桩避免后台任务真的跑（本用例只锁路由行为）
        async def _noop(*a, **k):
            return None
        monkeypatch.setattr(server_mod, "_broadcast_and_collect", _noop)
        monkeypatch.setattr(server_mod, "_orchestrated_flow", _noop)

        it = state_mod.interject_store.submit("ws_t", "立刻执行的紧急插话内容", priority="紧急")
        s1, p1 = unpack(run(wd.interject_execute("ws_t", it["id"])))
        assert s1 == 200 and p1["success"] is True
        tid = p1["task_id"]
        assert tid in state_mod.tasks, "execute 必须创建 Task 交 Orchestrator 执行"
        task = state_mod.tasks[tid]
        assert task.description == "立刻执行的紧急插话内容"
        assert task.title == "立刻执行的紧急插话内容", "30 字以内标题不加省略号"
        assert task.status == TaskStatus.BROADCASTING
        assert p1["interject"]["status"] == "inserted"
        assert p1["interject"]["related_task"] == "execute"
        assert sm.get_state("ws_t") == "executing"
        assert sm._states["ws_t"]["ctx"]["interject"] == it["id"]
        assert server_mod.hall_messages[-1].payload.get("source") == "interject"

        # 非法前置：已 inserted → 拒绝，且不得再建 Task
        before = len(state_mod.tasks)
        s2, p2 = unpack(run(wd.interject_execute("ws_t", it["id"])))
        assert s2 == 200 and p2 == {"success": False, "error": "已在流程中"}
        assert len(state_mod.tasks) == before, "非法前置不得创建 Task"

    def test_resolve_only_moves_recoverable_states(self, mkws):
        mkws(status="running")
        sm = state_mod.task_state_machine
        store = state_mod.interject_store
        sm.set_state("ws_t", "executing")
        it = store.submit("ws_t", "有争议的点", priority="紧急")

        # executing 不是可裁决态 → 事件被忽略，状态不变
        s1, p1 = unpack(run(wd.interject_resolve("ws_t", it["id"])))
        assert s1 == 200 and p1["success"] is True
        assert p1["event"]["state"] == "executing"
        assert p1["event"]["note"] == "ignored:resolve@executing"

        # stuck-paused → discussing
        sm.set_state("ws_t", "stuck-paused")
        s2, p2 = unpack(run(wd.interject_resolve("ws_t", it["id"])))
        assert s2 == 200
        assert p2["event"]["state"] == "discussing"
        assert sm.get_state("ws_t") == "discussing"
        assert store.get("ws_t", it["id"])["related_task"] == "resolved"

    def test_dismiss_and_status_endpoint(self, mkws):
        mkws(status="running")
        store = state_mod.interject_store
        it = store.submit("ws_t", "不用管", priority="一般")

        s0, p0 = unpack(run(wd.interject_dismiss("ws_t", "it_404")))
        assert s0 == 404 and p0["error"] == "插话不存在"

        s1, p1 = unpack(run(wd.interject_dismiss("ws_t", it["id"])))
        assert s1 == 200 and p1["success"] is True
        assert p1["interject"]["status"] == "ignored"
        assert p1["interject"]["inserted_at"] is None, "ignored 不回填 inserted_at"

        # 通用状态流转：非法值必须拒绝且不改动
        it2 = store.submit("ws_t", "改状态", priority="一般")
        s2, p2 = unpack(run(wd.interject_status("ws_t", it2["id"], FakeRequest({"status": "bogus"}))))
        assert s2 == 200 and p2 == {"success": False, "error": "invalid status"}
        assert store.get("ws_t", it2["id"])["status"] == "pending", "非法值不得改动插话状态"

        s3, p3 = unpack(run(wd.interject_status("ws_t", it2["id"], FakeRequest({"status": "expired"}))))
        assert s3 == 200 and p3["success"] is True
        assert p3["interject"]["status"] == "expired"


# ═══════════════════════════════════════════════════════════════
# 五、状态机端点：sm/status、sm/resume、sm/drop
# ═══════════════════════════════════════════════════════════════
class TestStateMachineEndpoints:

    def test_sm_status_reports_state_and_logs(self, mkws):
        mkws(status="running")
        sm = state_mod.task_state_machine
        s0, p0 = unpack(run(wd.sm_status("ws_t")))
        assert s0 == 200 and p0["success"] is True
        assert p0["state"] == "created", "未推进过的工作间状态机默认 created"
        assert p0["logs"] == []

        # 先落一条 created 记录，再置位：日志必须记录真实迁移 created → executing
        sm.ensure("ws_t")
        sm.set_state("ws_t", "executing", stage="interject_execute")
        s1, p1 = unpack(run(wd.sm_status("ws_t")))
        assert p1["state"] == "executing"
        assert p1["logs"][-1]["event"] == "set_state"
        assert p1["logs"][-1]["from"] == "created"
        assert p1["logs"][-1]["to"] == "executing"

    def test_sm_resume_moves_to_executing_and_resets_members(self, mkws):
        stuck = member("m1", "码农", "小王", status="stuck")
        blocked = member("m2", "测试", "小李", status="blocked")
        working = member("m3", "设计", "小张", status="working")
        mkws(status="running", members=[stuck, blocked, working])
        sm = state_mod.task_state_machine
        sm.set_state("ws_t", "stuck-paused")

        s1, p1 = unpack(run(wd.sm_resume("ws_t")))
        assert s1 == 200 and p1["success"] is True
        assert p1["event"]["state"] == "executing"
        assert p1["event"]["action"] == "resume"
        assert (stuck.status, blocked.status) == ("entered", "entered"), \
            "resume 必须把 stuck/blocked/failed 成员复位为 entered"
        assert working.status == "working", "非受阻成员不得被改动"

        # 非法前置：executing 态 resume 被忽略（状态机不前进）
        s2, p2 = unpack(run(wd.sm_resume("ws_t")))
        assert s2 == 200
        assert p2["event"]["note"] == "ignored:resume@executing"
        assert sm.get_state("ws_t") == "executing"

    def test_sm_drop_is_terminal(self, mkws):
        ws = mkws(status="running", members=[member("m1", status="stuck")])
        sm = state_mod.task_state_machine
        sm.set_state("ws_t", "executing")

        s1, p1 = unpack(run(wd.sm_drop("ws_t")))
        assert s1 == 200 and p1["success"] is True
        assert p1["event"]["state"] == "dropped"
        assert p1["event"]["action"] == "drop"
        assert sm.get_state("ws_t") == "dropped"
        assert ws.members[0].status == "entered"

        # 终态不可恢复：resume 只回 terminal，状态保持 dropped
        s2, p2 = unpack(run(wd.sm_resume("ws_t")))
        assert s2 == 200
        assert p2["event"]["note"] == "terminal:dropped"
        assert p2["event"]["state"] == "dropped"
        assert sm.get_state("ws_t") == "dropped", "dropped 是终态，不得被 resume 复活"
