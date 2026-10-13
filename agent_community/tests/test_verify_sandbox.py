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

    # ★2026-10-13（老板问："我要的跑通后注册具体模板实现了没？"）：
    #   标记必须落在**模板本体**上，不能只写进"验证历史"列表 ——
    #   否则模板跑通后**仍然是"草稿"**（实测：`params={}`、`source="draft"`、没有 verified），
    #   界面也无从按 V42 §130 显示"这条还没验证过"。V42 F3 定稿要求的就是本体带这几个字段。
    stored = (sess.info.metadata.get(bt.METADATA_KEY) or [])
    assert stored, "跑通后必须有一条具体模板落库"
    one = stored[0]
    assert one["verified"] is True, "模板本体缺 verified（只写了历史 = 半截功能）"
    assert one["verified_at"] and one["verified_by"] == "sandbox"
    assert one["evidence"].get("verified_scope"), "模板本体要带'验的是什么范围'"
    assert one["params"].get("PLATFORM_URL"), "跑通用的平台地址要写进模板参数（不是空壳）"
    # 历史里也要带范围（原来 append 的是原始 evidence，范围没落盘）
    assert hist[-1]["evidence"].get("verified_scope"), "验证历史里也要有 verified_scope"


def test_commit_verified_refuses_without_any_template(monkeypatch):
    """没有模板时不许凭空"回写成功"（否则又是一种假状态）。"""
    from agent_community.platform import bridge_templates as bt
    from agent_community.platform.harness_adapter import harness_manager

    monkeypatch.setattr(harness_manager, "sessions", {"h1": _Sess()}, raising=False)
    monkeypatch.setattr(bt, "list_all", lambda info: [], raising=False)
    with pytest.raises(vs.SandboxError):
        vs.commit_verified({}, "h1")


# ── 3.5) ★省略 template 时，生成器要用"跑通后登记的那条模板" ────────────────
#   老板的问题：「我要的跑通后注册具体模板实现了没？」——
#   光"登记"不算实现，**登记完还得有人用它**。实测的坑：人工中转型 harness 省略
#   `template` 时，平台硬默认 `cli_acp` ⇒ 报"未配置 acp_command"（跟它无关的错），
#   而它名下**已经有一条跑通过的 pending_poll 具体模板**。
def test_pick_template_prefers_the_verified_concrete_template(monkeypatch):
    from agent_community.platform import bridge_templates as bt
    from agent_community.platform.routers import harness_bridge as hb

    info = _Info()
    monkeypatch.setattr(bt, "list_all", lambda i: [
        {"name": "没验过的", "kind": "cli_acp", "verified": False},
        {"name": "跑通桥", "kind": "pending_poll", "verified": True},
    ], raising=False)
    kind, why = hb._pick_template_kind(info, {"cli_acp", "pending_poll", "file_poll"})
    assert kind == "pending_poll", "应当优先用**已验证**的那条，而不是列表里第一条"
    assert "跑通桥" in why and "已验证" in why, "要说明桥型从哪来的（不然接入方无法判断平台用了哪套配置）"


def test_pick_template_falls_back_to_derived_then_says_unknown(monkeypatch):
    from agent_community.platform import bridge_templates as bt
    from agent_community.platform.routers import harness_bridge as hb

    # 有模板但都没验过 ⇒ 退回"按注册信息派生"（file_poll harness）
    info = _Info(wakeup_method="file_poll", wakeup_dir=r"D:\x\inbox")
    monkeypatch.setattr(bt, "list_all", lambda i: [{"name": "草稿", "kind": "file_poll",
                                                    "verified": False}], raising=False)
    kind, why = hb._pick_template_kind(info, {"file_poll", "cli_acp"})
    assert kind == "file_poll" and "派生" in why

    # 人工中转型、什么都没验过 ⇒ **不许猜**（返回空，让端点如实说"二选一"）
    info2 = _Info(wakeup_method="clipboard")
    kind2, why2 = hb._pick_template_kind(info2, {"file_poll", "cli_acp", "pending_poll"})
    assert kind2 == "" and why2 == "", "猜不出来就要承认猜不出来，不能硬塞一个 cli_acp"


# ── 4) 桥参数逐脚本核对 ─────────────────────────────────────────────────────
def test_bridge_argv_uses_verified_flag_names():
    """参数名是**逐脚本核对**过的：file_poll 用 `--platform`，pending_poll/cli_acp 用 `--url`。

    ⚠️ 2026-10-13（V44 #10）：桥型键从 `acp` 改成**模板口径的 `cli_acp`** ——
       原来的键与模板 kind 不一致，导致 `bridge_script_path("cli_acp")` 永远找不到脚本
       （A 档 harness 在验证间里必然起不了桥）。断言跟着真相走。
    """
    a = vs.bridge_argv("file_poll", harness_id="h1", inbox=r"D:\i", platform="http://x",
                       trigger="t.cmd", interval=1)
    assert "--inbox" in a and "--platform" in a and "--trigger" in a
    assert "--url" not in a
    b = vs.bridge_argv("pending_poll", harness_id="h1", inbox=r"D:\i\sub", platform="http://x")
    assert "--url" in b and "--work-dir" in b
    assert "--platform" not in b, "pending_poll 桥不认 --platform（照猜会起不来）"
    c = vs.bridge_argv("cli_acp", harness_id="h1", inbox="", platform="http://x")
    assert "--url" in c
    assert c[2].endswith("acp_harness_bridge.py")


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


def test_teardown_stops_only_the_sandbox_bridge_not_the_users(monkeypatch, tmp_path):
    """★★2026-10-13 实测事故：**"验一下"把用户跑通的常驻桥杀了**。

    原来 teardown 传给停桥工具的是 `info.bridge_dir` —— 那是该 harness **登记在册的桥目录**，
    第 5 步跑通之后用户**正在那个目录里常驻跑桥**。于是：点一次自检 ⇒ 用户的桥被杀，
    `bridges_running` 从 1 变 0，而平台只报"验证间已拆"。
    修后判据：**只按验证间自己的目录停**（沙箱里那只临时桥的 argv 里就带这个路径）。
    """
    from agent_community.platform.workshop import Workshop, WorkshopMember

    hid = "h-user-bridge"
    comp = tmp_path / "comp"
    comp.mkdir(parents=True, exist_ok=True)
    ws = Workshop(workshop_id=vs.sandbox_dir_name(hid), name="验证间 · " + hid,
                  workspace_dir=str(comp), hall_content="", members=[
                      WorkshopMember(member_id="v0", role=vs.SANDBOX_ROLE,
                                     display_name=hid, harness_ids=[hid])])
    ws.origin_task_id = vs.SANDBOX_MARK
    shops = {ws.workshop_id: ws}

    monkeypatch.setattr(vs, "_persist", lambda: None, raising=False)
    monkeypatch.setattr(vs, "_cleanup_leftover_dirs", lambda h, **k: [], raising=False)

    seen = {}

    def fake_stop(harness_id, bridge_dir):
        seen["hid"], seen["dir"] = harness_id, bridge_dir
        return {"killed": 0}

    run(vs.teardown(shops, hid, stop_bridge=fake_stop))
    assert seen.get("hid") == hid
    got = str(seen.get("dir") or "")
    assert "verify_sandbox" in vs.sandbox_dir_name(hid) or got, "必须传一个明确的目录"
    assert got.startswith(str(comp)) or str(comp) in got, \
        "★停桥要按**验证间自己的**目录（这里是 %s），实得 %r" % (comp, got)

    # 反向：源码里不许再出现"拿 harness 的 bridge_dir 去停"的旧写法
    import inspect
    src = inspect.getsource(vs.teardown)
    assert 'getattr(info, "bridge_dir"' not in src, \
        "不许再按 harness 的 bridge_dir 停桥（那是用户常驻桥的目录，会误杀）"


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


def test_stop_bridge_only_matches_the_given_dir_not_the_whole_harness():
    """★★2026-10-13 实测事故：**验一下把用户跑通的桥杀了**。

    事故经过：老板那只常驻桥跑得好好的（`bridges/<hid>/bridge.py`），
    我跑了一次 `{hid}/verify` 想把跑通的配置登记成模板 —— 验证间 teardown 调
    `_stop_bridge_processes(hid, 沙箱桥目录)`，而老实现**只要 harness_id ≥3 字符就拿它当匹配串**
    ⇒ 沙箱那只和**用户常驻那只一起被杀**，`bridges_running` 从 1 变 0，
    而平台只报"验证间已拆"，**没说自己顺手停了用户的桥**。
    ⇒ 判据：**给了 bridge_dir 就只认它**；只有没给 dir 时才退回 harness_id。
    """
    import inspect

    from agent_community.platform import server as srv

    src = inspect.getsource(srv._stop_bridge_processes)
    assert "elif harness_id" in src, \
        "harness_id 只能作为**兜底**（给了 bridge_dir 时不许再用它匹配整只 harness 的桥）"
    i_dir, i_elif = src.find("needles.append(bridge_dir)"), src.find("elif harness_id")
    assert i_dir != -1 and i_elif != -1 and i_dir < i_elif, \
        "判断顺序必须先是 bridge_dir、再 elif harness_id（顺序反了就等于没修）"
    assert 'needles.append(harness_id)' in src
    # 顺手钉住"别报假数字"：原来杀没杀都回 killed: 0
    assert "killed = int(" in src or "killed = 0\n        for tok" in src, \
        "killed 要如实反映杀了几只（原来无论杀没杀都回 0，调用方无从判断）"
    assert "matched_by" in src, "要把'按什么匹配的'一起报出来，便于事后追"
