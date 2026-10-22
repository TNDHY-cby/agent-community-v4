# -*- coding: utf-8 -*-
"""名册生命周期（V82 §4）：**纯函数**，只给判定，不动数据。

默认口径（保守，可申诉）：
- 连续 **7 天**健康检查全失败 ⇒ **隐藏**（不删除）；
- 健康明细保留 **90 天** ⇒ 只清理明细，汇总保留；
- 每 **48** 个巡检周期做一次死号检查。
"""
from __future__ import annotations

HIDE_AFTER_DAYS_ALL_FAILING = 7
DETAIL_RETENTION_DAYS = 90
DEAD_CHECK_EVERY_CYCLES = 48


def should_hide(days_all_failing: int) -> bool:
    return int(days_all_failing or 0) >= HIDE_AFTER_DAYS_ALL_FAILING


def should_purge_detail(age_days: int) -> bool:
    return int(age_days or 0) > DETAIL_RETENTION_DAYS


def should_check_dead(cycle_index: int) -> bool:
    return int(cycle_index or 0) > 0 and int(cycle_index) % DEAD_CHECK_EVERY_CYCLES == 0


def policy() -> dict:
    """给界面/文档用的一处真相。"""
    return {"hide_after_days_all_failing": HIDE_AFTER_DAYS_ALL_FAILING,
            "detail_retention_days": DETAIL_RETENTION_DAYS,
            "dead_check_every_cycles": DEAD_CHECK_EVERY_CYCLES,
            "note": "隐藏而非删除、过期而非永久、可申诉"}
