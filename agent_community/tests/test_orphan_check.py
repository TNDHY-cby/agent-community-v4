# -*- coding: utf-8 -*-
"""`platform/orphan_check.py`：**成员指的 agent 还在不在**。

起因（老板 2026-10-11）：「我之前把 agent 删掉了，看来删除功能并不完善」

实测：`DELETE /api/harness/{hid}` 本身很认真（清注册/队列/桥进程/桥目录/会话/审计），
但**不会清理引用它的东西** ⇒ 工作间里留着指向已不存在 agent 的成员（实测 4 个），
而成员卡照常显示 ⇒ **"看起来正常"的假状态**（准则⑤）。

本文件锁三件事：
1. 判得准（全缺 / 部分缺 / 不缺）；
2. **一定要给"怎么办"**（恢复 or 移除）—— 只说"卡住了"等于没说；
3. **不自动改数据**（模块只算，不写）—— 删 agent 是否级联清成员是产品决策。
"""
from __future__ import annotations

import inspect

from agent_community.platform import orphan_check as oc


class _M:
    def __init__(self, hids, role="码农", mid="m0"):
        self.harness_ids = hids
        self.role = role
        self.member_id = mid
        self.display_name = "示例Harness-A"
        self.session_aliases = []


class _WS:
    def __init__(self, members):
        self.workshop_id = "w1"
        self.members = members


def test_all_missing_is_orphan():
    st = oc.orphan_state(_M(["已删掉的agent"]), {"还在的agent"})
    assert st["orphan"] is True
    assert st["missing"] == ["已删掉的agent"]
    assert "不在平台上" in st["text"]
    assert st["advice"], "必须给怎么办"


def test_partially_missing_is_reported_as_partial():
    st = oc.orphan_state(_M(["还在的agent", "已删掉的agent"]), {"还在的agent"})
    assert st["orphan"] is True
    assert st["missing"] == ["已删掉的agent"]
    assert "一部分" in st["text"]


def test_present_is_not_orphan():
    st = oc.orphan_state(_M(["还在的agent"]), {"还在的agent"})
    assert st["orphan"] is False
    assert st["text"] == "" and st["advice"] == ""


def test_member_without_harness_is_not_orphan():
    """还没绑 harness 的成员不算孤儿（那是"还没到那一步"）。"""
    st = oc.orphan_state(_M([]), {"x"})
    assert st["orphan"] is False


def test_scan_workshop_headline_speaks_and_counts():
    ws = _WS([_M(["活着"]), _M(["死了"], role="组长", mid="m1")])
    out = oc.scan_workshop(ws, {"活着"})
    assert out["orphan_count"] == 1
    assert out["member_count"] == 2
    assert "已经不在了" in out["headline"]

    ok = oc.scan_workshop(_WS([_M(["活着"])]), {"活着"})
    assert ok["orphan_count"] == 0
    assert "都对着还在的 agent" in ok["headline"]


def test_module_never_writes_state():
    """★纪律：本模块只算不写（删 agent 是否级联清成员 = 产品决策，先如实报）。"""
    src = inspect.getsource(oc)
    for forbidden in ("save_state(", "write_text", "os.remove", "pop("):
        assert forbidden not in src, f"orphan_check 不应出现写操作：{forbidden}"
