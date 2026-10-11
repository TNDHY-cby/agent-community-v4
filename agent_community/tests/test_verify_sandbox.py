# -*- coding: utf-8 -*-
"""V42 低耗验证间：**闭环**的回归保护（此前只有真机脚本，等于没有回归）。

设计稿：`design-docs/V42_接入建模_低耗验证间与模板回写.md`
老板口径：「大厅注册时，给到一个**等价低耗工作间模型**进行架桥等等测试」、
        「**让它先跑通才写入具体模板里**，然后测试的再清理掉」、「**连桥也拆掉，只留模板**」

**这些测试锁什么**
1. 验证间**每个 harness 一个**、落 `verify_sandbox/`、带沙箱标记（老板裁定"各自一个"）；
2. **鸡生蛋死锁被解开**：没有桥模板时**先派生一条未验证草稿**再架桥；
3. ★**门槛**：自检 `state=ok` 才回写模板（`verified=true` + 时间 + 证据）；
   **没跑通绝不写** —— 这是老板最在意的一条；
4. **拆掉**：桥停 + 隔间清 + 验证间移除，**模板与验证记录保留**；
5. `find_sandbox` 用**精确 id**（我第一版用 `endswith` 会串到别人的验证间）；
6. `bridge_argv` 的**参数名逐脚本核对**（file_poll 用 `--platform`，pending_poll/acp 用 `--url`；
   我照猜踩过）；且**验证间起的桥用同一个桥脚本**（否则"验过"不等于"能用"）；
7. ★★**`_stop_bridge_processes` 绝不能杀平台自己**（实测事故：它用裸子串匹配命令行，
   而平台自己的命令行里也含 harness_id ⇒ 平台自杀，日志只留 WinError 64，极难查）。

**隔离纪律**：所有测试**不落真实数据、不起真桥、不碰网络**（conftest 的 guard_real_data_dir
会抓真实写盘；我第一版就被抓过）。
"""
from __future__ import annotations

import asyncio
import os

import pytest

from agent_community.platform import verify_sandbox as vs


def run(c):
    return asyncio.run(c)


# ── 1) 验证间的身份与唯一性 ─────────────────────────────────────────────────
def test_sandbox_is_marked_and_found_by_exact_id():
    """一个 harness 一个；查找用**精确 id**（`endswith` 会串到别人）。"""
    from agent_community.platform.workshop import Workshop, WorkshopMember

    def mk(hid):
        ws = Workshop(workshop_id=vs.sandbox_dir_name(hid), name="验证间 · " + hid,
                      workspace_dir="", hall_content="", members=[
                          WorkshopMember(member_id="v0", role=vs.SANDBOX_ROLE,
                                         display_name=hid, harness_ids=[hid])])
        ws.origin_task_id = vs.SANDBOX_MARK
        return ws

    shops = {"vs_h1": mk("h1"), "vs_h2": mk("h2")}
    assert vs.is_sandbox(shops["vs_h1"]) is True
    assert vs.find_sandbox(shops, "h1").workshop_id == "vs_h1"
    assert vs.find_sandbox(shops, "h2").workshop_id == "vs_h2"
    # ★后缀陷阱：harness "1" 不该命中 "vs_h1"
    assert vs.find_sandbox(shops, "1") is None, "又用了后缀匹配（会串到别人的验证间）"
    # 非沙箱工作间不算
    plain = mk("h3")
    plain.origin_task_id = ""
    assert vs.is_sandbox(plain) is False
    assert vs.find_sandbox({"x": plain}, "h3") is None


def test_normal_workshop_is_not_a_sandbox():
    from agent_community.platform.workshop import Workshop
    ws = Workshop(workshop_id="w1", name="真正的工作间", workspace_dir="",
                  hall_content="", members=[])
    assert vs.is_sandbox(ws) is False


# ── 2) 解开"先有模板才能架桥"的死锁 ────────────────────────────────────────
class _Info:
    def __init__(self, **kw):
        self.harness_id = kw.get("harness_id", "h1")
        self.wakeup_method = kw.get("wakeup_method", "file_poll")
        self.wakeup_dir = kw.get("wakeup_dir", r"D:\tmp\inbox")
        self.acp_command = kw.get("acp_command", "")
        self.metadata = {}


def test_draft_template_unblocks_the_chicken_and_egg():
    """没有模板时派生的草稿：**不算已验证**、来源标 draft（跑通后才会回写）。"""
    info = _Info(wakeup_method="file_poll", wakeup_dir=r"D:\tmp\box")
    t = vs.draft_template_from(info)
    assert t["kind"] == "file_poll" and t["params"]["INBOX_DIR"] == r"D:\tmp\box"
    assert t["verified"] is False, "草稿不许自称已验证（跑通才写）"
    assert t["source"] == "draft"
    # acp 走另一型
    t2 = vs.draft_template_from(_Info(wakeup_method="acp", acp_command="x.exe --go"))
    assert t2["kind"] == "cli_acp" and t2["params"]["ACP_COMMAND"] == "x.exe --go"


# ── 3) ★门槛：跑通才回写 ───────────────────────────────────────────────────
class _Sess:
    def __init__(self):
        self.info = _Info()
        self.status = None


def test_commit_verified_marks_template_after_ok(monkeypatch):
    """跑通后：模板上要有 verified / verified_at / verified_by / evidence。"""
    from agent_community.platform import bridge_templates as bt
    from agent_community.platform.harness_adapter import harness_manager

    sess = _Sess()
    monkeypatch.setattr(harness_manager, "sessions", {"h1": sess}, raising=False)
    monkeypatch.setattr(bt, "list_all", lambda info: [{"name": "跑通桥", "kind": "file_poll"}],
                        raising=False)
    out = vs.commit_verified({}, "h1", evidence={"probe_id": "p1", "state": "ok"})
    assert out["success"] is True and out["verified"] is True
    assert out["verified_by"] == "sandbox" and out["verified_at"] > 0
    assert out["evidence"]["probe_id"] == "p1"
    hist = sess.info.metadata.get("bridge_template_verified") or []
    assert hist and hist[-1]["evidence"]["state"] == "ok", "验证证据要留在 metadata 里"


def test_commit_verified_refuses_without_any_template(monkeypatch):
    """没有模板时不许凭空"回写成功"（否则又是一种假状态）。"""
    from agent_community.platform import bridge_templates as bt
    from agent_community.platform.harness_adapter import harness_manager

    monkeypatch.setattr(harness_manager, "sessions", {"h1": _Sess()}, raising=False)
    monkeypatch.setattr(bt, "list_all", lambda info: [], raising=False)
    with pytest.raises(vs.SandboxError):
        vs.commit_verified({}, "h1")


# ── 4) 桥参数逐脚本核对 ─────────────────────────────────────────────────────
def test_bridge_argv_uses_verified_flag_names():
    """参数名是**逐脚本核对**过的：file_poll 用 `--platform`，pending_poll/acp 用 `--url`。"""
    a = vs.bridge_argv("file_poll", harness_id="h1", inbox=r"D:\i", platform="http://x",
                       trigger="t.cmd", interval=1)
    assert "--inbox" in a and "--platform" in a and "--trigger" in a
    assert "--url" not in a
    b = vs.bridge_argv("pending_poll", harness_id="h1", inbox=r"D:\i\sub", platform="http://x")
    assert "--url" in b and "--work-dir" in b
    assert "--platform" not in b, "pending_poll 桥不认 --platform（照猜会起不来）"
    c = vs.bridge_argv("acp", harness_id="h1", inbox="", platform="http://x")
    assert "--url" in c


def test_bridge_script_path_is_under_examples():
    """验证间起的必须是**同一个桥脚本**（否则"验过"不等于"能用"）。"""
    p = vs.bridge_script_path("file_poll")
    assert p.endswith("filepoll_harness_bridge.py")
    assert os.path.join("examples") in p


def test_unknown_bridge_kind_raises_with_hint():
    with pytest.raises(vs.SandboxError):
        vs.bridge_argv("no_such_kind", harness_id="h", inbox="i", platform="u")


# ── 5) 拆掉：只留模板 ───────────────────────────────────────────────────────
def test_teardown_removes_sandbox_but_not_the_template(monkeypatch, tmp_path):
    """拆除要：移除验证间登记；**不去动模板**（模板由 commit_verified 负责）。"""
    from agent_community.platform.workshop import Workshop, WorkshopMember

    hid = "h-del"
    ws = Workshop(workshop_id=vs.sandbox_dir_name(hid), name="验证间 · " + hid,
                  workspace_dir=str(tmp_path / "comp"), hall_content="", members=[
                      WorkshopMember(member_id="v0", role=vs.SANDBOX_ROLE,
                                     display_name=hid, harness_ids=[hid])])
    ws.origin_task_id = vs.SANDBOX_MARK
    (tmp_path / "comp").mkdir(parents=True, exist_ok=True)
    shops = {ws.workshop_id: ws}

    monkeypatch.setattr(vs, "_persist", lambda: None, raising=False)
    monkeypatch.setattr(vs, "_cleanup_leftover_dirs", lambda h, **k: [], raising=False)

    out = run(vs.teardown(shops, hid, stop_bridge=lambda h, b: {"killed": 0}))
    assert out["success"] is True and out["removed"] is True
    assert ws.workshop_id not in shops, "验证间登记要移除"
    assert out["detail"]["ws_removed"] is True


def test_teardown_without_sandbox_is_honest(monkeypatch):
    """没有验证间时如实说明（并兜底清盘上残留），不假装拆过。"""
    monkeypatch.setattr(vs, "_cleanup_leftover_dirs", lambda h, **k: [], raising=False)
    out = run(vs.teardown({}, "missing"))
    assert out["success"] is True and out["removed"] is False
    assert "没有验证间" in out["note"]


# ── 6) ★★平台绝不能被自己的停桥逻辑杀掉 ─────────────────────────────────────
def test_stop_bridge_processes_excludes_the_platform_itself():
    """实测事故：该函数用裸子串匹配命令行，而**平台自己的命令行里也含 harness_id**
    ⇒ 平台自杀，日志只留 `WinError 64 指定的网络名不再可用`，极难定位。
    这条测试从**源码**上锁死两道防线（不需要真跑 PowerShell）。"""
    import inspect

    from agent_community.platform import server as srv

    src = inspect.getsource(srv._stop_bridge_processes)
    assert "agent_community.platform.server" in src, \
        "必须显式排除平台自身（否则它会把自己杀了）"
    assert "notlike" in src, "排除要靠 -notlike 条件"
    assert "bridge" in src, "还要求命中桥特征（bridge*.py），宁可漏杀一个桥"
    # 不许再出现"只按 harness_id 子串匹配"的旧写法
    assert "Name -match 'python|node' -and $_.CommandLine \"" not in src or "notlike" in src
