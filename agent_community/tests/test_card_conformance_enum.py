# -*- coding: utf-8 -*-
"""V82 卡片一致性的**回归**：`model_dump()` 里的枚举不许把"声明合规"判成红。

真实缺陷（2026-10-22，被 V87 接入自检当场抓到）：
`HarnessInfo.model_dump()` 默认不转 JSON 模式，`wakeup_method` 出来是
`WakeupMethod.CLIPBOARD` 枚举成员；`str()` 之后变成 `'WakeupMethod.CLIPBOARD'`，
卡片一致性判"不认识的叫醒方式" ⇒ `/api/harness/{id}/capability` 与自检里的
「声明合规」对所有这类 harness **假红**（用户以为是自己填错了卡片）。

判据口径：**两种入参（枚举 / JSON 字符串）必须得到同一个结论**。
"""
from __future__ import annotations

from agent_community.platform import card_conformance as cc
from agent_community.platform.protocol import HarnessInfo, WakeupMethod

AI = {"model_name": "示例模型", "provider": "示例provider", "capabilities": ["coding"]}


def _info(method):
    return HarnessInfo(harness_id="示例Harness-A", harness_name="示例Harness-A",
                       ai=dict(AI), wakeup_method=method)


def test_enum_member_in_model_dump_does_not_fail_the_card():
    info = _info(WakeupMethod.CLIPBOARD)
    raw = info.model_dump()
    # ⚠️ `WakeupMethod` 是 `str, Enum`：它是 str 的子类，所以不能用 isinstance 判；
    #    真正会坏的是 `str(它)` —— 那是 `'WakeupMethod.CLIPBOARD'`。
    assert "WakeupMethod" in str(raw["wakeup_method"]), \
        "前提变了：model_dump() 不再返回枚举成员？那这条回归可以直接删"
    r = cc.check_card(raw)
    assert r["conformance"] == "passed", "枚举入参被判红了（真实缺陷复现）：%s" % r["conformance_errors"]
    assert r["conformance_errors"] == []


def test_enum_and_json_mode_reach_the_same_verdict():
    info = _info(WakeupMethod.CLIPBOARD)
    a = cc.check_card(info.model_dump())
    b = cc.check_card(info.model_dump(mode="json"))
    assert a["conformance"] == b["conformance"] == "passed"
    assert a["conformance_errors"] == b["conformance_errors"] == []


def test_bad_method_is_still_reported_with_the_plain_value():
    """反向：真的填错了照样要报，而且报的是**朴素值**，不是 `WakeupMethod.XXX` 这种内部名。"""
    r = cc.check_card({"harness_id": "h1", "harness_name": "示例Harness-A",
                       "wakeup_method": "magic", "ai": dict(AI)})
    assert r["conformance"] == "failed"
    why = r["conformance_errors"][0]["why"]
    assert "'magic'" in why and "WakeupMethod" not in why, why
