# -*- coding: utf-8 -*-
"""V35/V36 · 多条「具体桥模板」CRUD + 旧单数键迁移。

**断言口径对应老板 2026-10-10 的 4 条裁定**（见 `V35 §10.1`）：
  · 裁定 2：`match` 缺省 = 全局可用 ⇒ **不得**因"没指定工作间"而拒绝保存；
  · 裁定 3：**不限条数** ⇒ 不得有隐性上限；
  · 裁定 1：AI 自查只是"给建议"，**手工填必须能走通**（本模块不依赖 AI）；
  · 裁定 4：旧式不删除 ⇒ 迁移时**原单数键保留**。
"""
from __future__ import annotations

import pytest

from agent_community.platform import bridge_templates as bt


class _Info:
    """最小 HarnessInfo 替身：只需要一个可写 metadata。

    ⚠️ `metadata` 要做成**普通属性**（不能靠 `self.metadata = dict(meta)` 直接塞列表）——
    真实 `HarnessInfo.metadata` 就是 dict；塞列表会掩盖"实现里把 dict 换成 list"这类错误。
    """

    def __init__(self, **meta):
        self.metadata = {}
        for k, v in meta.items():
            self.metadata[k] = v


def test_empty_returns_empty_list():
    info = _Info()
    assert bt.list_all(info) == []
    assert bt.status_of(info)["has_any"] is False


def test_put_then_list_roundtrip():
    info = _Info()
    one = bt.put(info, {"name": "示例CDP桥", "kind": "cli_acp",
                        "path": r"D:\x\bridge.py", "params": {"ACP_CWD": r"D:\x"}})
    assert one["name"] == "示例CDP桥"
    assert one["source"] == bt.SOURCE_USER
    items = bt.list_all(info)
    assert len(items) == 1
    assert items[0]["path"] == r"D:\x\bridge.py"
    # 落盘键是**复数**
    assert "bridge_templates" in info.metadata


def test_multiple_no_limit():
    """裁定 3：不限条数（含中文名、带空格名）。"""
    info = _Info()
    names = ["A", "B 桥", "桥-三", "第四 条模板", "e"]
    for n in names:
        bt.put(info, {"name": n, "kind": "file_poll"})
    got = [t["name"] for t in bt.list_all(info)]
    assert got == names
    assert len(bt.list_all(info)) == 5


def test_same_name_overwrites_and_keeps_created_at():
    info = _Info()
    first = bt.put(info, {"name": "同名", "kind": "file_poll", "path": "old"})
    second = bt.put(info, {"name": "同名", "kind": "file_poll", "path": "new"})
    assert len(bt.list_all(info)) == 1          # 覆盖，不是追加
    assert second["path"] == "new"
    assert second["created_at"] == first["created_at"]   # 创建时间保留
    assert second["updated_at"] >= first["updated_at"]


def test_match_default_is_global():
    """★裁定 2：缺省 = 全局可用（保存**不得**因缺省而失败）。"""
    info = _Info()
    one = bt.put(info, {"name": "全局桥", "kind": "pending_poll"})
    assert one["match"] == {"workshop": None, "role": None}
    # 任意工作间/职位都应命中
    assert bt.pick_for(info, ws="ws1", role="码农")["name"] == "全局桥"
    assert bt.pick_for(info, ws="任意", role="任意")["name"] == "全局桥"
    assert bt.pick_for(info)["name"] == "全局桥"


def test_match_restricted_only_hits_that_workshop():
    info = _Info()
    bt.put(info, {"name": "只给A", "kind": "file_poll", "match": {"workshop": "wsA"}})
    assert bt.pick_for(info, ws="wsA") is not None
    assert bt.pick_for(info, ws="wsB") is None      # 不该命中别的间


def test_match_specificity_wins():
    """限定越具体越优先。"""
    info = _Info()
    bt.put(info, {"name": "全局", "kind": "file_poll"})
    bt.put(info, {"name": "只限间", "kind": "file_poll", "match": {"workshop": "wsA"}})
    bt.put(info, {"name": "限间+职位", "kind": "file_poll",
                  "match": {"workshop": "wsA", "role": "码农"}})
    assert bt.pick_for(info, ws="wsA", role="码农")["name"] == "限间+职位"
    assert bt.pick_for(info, ws="wsA", role="美术")["name"] == "只限间"
    assert bt.pick_for(info, ws="wsZ", role="码农")["name"] == "全局"


def test_delete_removes_only_that_one():
    info = _Info()
    bt.put(info, {"name": "留", "kind": "file_poll"})
    bt.put(info, {"name": "删", "kind": "file_poll"})
    assert bt.delete(info, "删") is True
    assert [t["name"] for t in bt.list_all(info)] == ["留"]
    assert bt.delete(info, "不存在") is False


def test_bad_name_rejected():
    info = _Info()
    for bad in ["", "   ", "a" * 65, "..", ".", r"a\b", "a/b", "a:b", "a*b", "a?b"]:
        with pytest.raises(bt.BridgeTemplateError):
            bt.put(info, {"name": bad, "kind": "file_poll"})
    assert bt.list_all(info) == []          # 一条都没留下


def test_bad_kind_rejected():
    info = _Info()
    with pytest.raises(bt.BridgeTemplateError):
        bt.put(info, {"name": "x", "kind": "不存在的形状"})


# ── 迁移：旧单数键 → 复数列表一条 ─────────────────────────────────────────

def test_legacy_singular_migrates_and_is_kept():
    """★裁定 4：旧式不删除 ⇒ 迁移后**原单数键仍在**（旧读取方不受影响）。"""
    legacy = {
        "template": "cli_acp",
        "wakeup_method": "acp",
        "artifact_kind": "script",
        "params": {"HARNESS_ID": "h1", "ACP_COMMAND": "x", "ACP_CWD": "y"},
        "required": ["HARNESS_ID", "ACP_COMMAND", "ACP_CWD"],
        "missing": [],
        "complete": True,
        "created_at": 111.0,
    }
    info = _Info(bridge_template=legacy)
    items = bt.list_all(info)
    assert len(items) == 1
    got = items[0]
    assert got["name"] == bt.DEFAULT_NAME
    assert got["kind"] == "cli_acp"
    assert got["source"] == bt.SOURCE_AUTO
    assert got["params"]["ACP_COMMAND"] == "x"
    assert got["complete"] is True
    # 原单数键**必须还在**
    assert info.metadata.get("bridge_template") is legacy
    # 再读一次不应重复迁移（幂等）
    assert len(bt.list_all(info)) == 1


def test_legacy_without_bridge_kind_not_migrated():
    """无桥型（template=None，走连接契约/提示词）不该被塞进"桥模板列表"。"""
    info = _Info(bridge_template={"template": None, "artifact_kind": "prompt",
                                 "params": {"HARNESS_ID": "h1"}})
    assert bt.list_all(info) == []


def test_migration_then_put_keeps_both_and_does_not_duplicate():
    """有旧单数键、再手填一条时：

    · 迁移那条**保留**（裁定 4：旧的不删）；
    · 手填那条**追加**（裁定 3：不限条数）；
    · 但**同一条不会被迁移两次**（防重复）。
    """
    legacy = {"template": "file_poll", "params": {"INBOX_DIR": "d"}, "complete": True}
    info = _Info(bridge_template=legacy)
    bt.put(info, {"name": "我手填的", "kind": "file_poll"})
    names = [t["name"] for t in bt.list_all(info)]
    # 迁移那条 + 手填那条，各一次
    assert names.count(bt.DEFAULT_NAME) == 1
    assert "我手填的" in names
    assert len(names) == 2
    # 再读一次仍然只有两条（幂等，不重复迁移）
    assert len(bt.list_all(info)) == 2
    # 旧单数键仍在
    assert info.metadata.get("bridge_template") is legacy


def test_status_reports_incomplete_by_name():
    """失败要说话：缺参数的条目要能被指名报出来（不是只给个计数）。"""
    info = _Info()
    bt.put(info, {"name": "全的", "kind": "file_poll",
                  "params": {"INBOX_DIR": "d"}, "complete": True, "missing": []})
    bt.put(info, {"name": "缺的", "kind": "cli_acp",
                  "params": {"ACP_COMMAND": ""}, "complete": False, "missing": ["ACP_COMMAND"]})
    st = bt.status_of(info)
    assert st["count"] == 2
    assert st["incomplete"] == ["缺的"]


def test_dirty_entry_does_not_break_list():
    """列表里混进脏条目（非 dict / 坏 kind）时，整表仍可读（不让一条坏数据把页面打崩）。"""
    info = _Info(bridge_templates=[
        {"name": "好的", "kind": "file_poll"},
        "我不是 dict",
        {"name": "坏形状", "kind": "不存在"},
    ])
    items = bt.list_all(info)
    assert [t["name"] for t in items] == ["好的"]


# ── V36 §2.2 具体桥引用：成员上的 `bridge_ref`（一条桥 ↔ 一个具体会话）──────────────
# 老板口径：「一条桥 = 一个具体会话」⇒ 光有 session_ref（会话）不够，还要记住
# "这条桥用的哪条模板、落在哪、绑的哪个会话"，而且**重启不能丢**。

def test_member_bridge_ref_defaults_to_unattached():
    """新成员默认 = 未架接（空 dict）—— 界面据此**必须明说"未架接"**，不许空白（V35 §6）。"""
    from agent_community.platform.workshop import Workshop, WorkshopMember
    w = Workshop(workshop_id="w1", name="t", workspace_dir="x", hall_content="任务",
                 members=[WorkshopMember(member_id="m0", role="码农", display_name="示例Harness-A")])
    assert w.members[0].bridge_ref == {}
    assert w.to_dict()["members"][0]["bridge_ref"] == {}


def test_member_bridge_ref_roundtrip_and_backward_compat():
    """落盘↔读回不丢；旧数据（无该键）读回为"未架接"而不是崩。"""
    from agent_community.platform.workshop import Workshop, WorkshopMember
    w = Workshop(workshop_id="w1", name="t", workspace_dir="x", hall_content="任务",
                 members=[WorkshopMember(member_id="m0", role="码农", display_name="示例Harness-A")])
    w.members[0].bridge_ref = {
        "template_name": "示例CDP桥",
        "bridge_path": r"D:\隔间\bridge.py",
        "session_ref": {"id": "s1"},
        "session_name": "w1-看板-码农-m0",
        "state": "attached",
        "verified": False,
    }
    back = Workshop.from_dict(w.to_dict()).members[0].bridge_ref
    assert back["template_name"] == "示例CDP桥"
    assert back["session_name"] == "w1-看板-码农-m0"
    assert back["state"] == "attached"

    old = w.to_dict()
    old["members"][0].pop("bridge_ref")
    assert Workshop.from_dict(old).members[0].bridge_ref == {}
