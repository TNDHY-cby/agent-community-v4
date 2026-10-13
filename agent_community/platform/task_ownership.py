# -*- coding: utf-8 -*-
"""平台侧任务/交付单元**原子所有权**登记表（V-20，47 号任务书 示例Harness-E 侧）。

对应 Paperclip 的 `FOR UPDATE SKIP LOCKED`：**同一交付单元同一时刻只允许一个 owner 持有**，
拿不到的人立刻放弃（skip），不排队、不等待、不重复干活。不引入数据库级锁。

两级 key（见 design-docs/V20_工单化审计与原子所有权设计.md §四）：
  - ``task:{task_id}``            —— 任务**领取**归属（谁能推进这个任务）
  - ``delegation:{delegation_id}`` —— **交付单元执行**归属（谁在执行这份子任务）

⚠️ 为什么临界区用 `threading.Lock` 而不是设计稿字面写的 `asyncio.Lock`（2026-10-07 实测修正）：
`asyncio.Lock` 在**竞争路径**（非快路径）会调 `_get_loop()` 把自身绑死到首次使用的 loop，
之后再在**另一个 loop** 里竞争就直接抛
`RuntimeError: ... is bound to a different event loop`（实测复现：同一模块级 Lock 连续两次
`asyncio.run` 里做 10 并发 gather，第二次必炸）。
而本项目测试逐个用例 `asyncio.run`、平台也会重启事件循环 —— 模块级单例用 `asyncio.Lock` 必踩。
本类临界区内**没有任何 await**（纯 dict 读写），故同步互斥量强度只高不低，且跨 loop / 跨线程都安全。

设计原则：
  - 零第三方依赖（仅标准库 threading / dataclasses / time / uuid）；
  - 失败不炸：非法入参返回 None / False，不抛；
  - TTL 过期即视为无主（可被他人领取）—— 这是给 示例Harness-C 侧 stranded 孤儿回收留的钩子。
"""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass
from typing import Any, Optional


# ── key 命名约定 ────────────────────────────────────────────────
def task_key(task_id: str) -> str:
    """任务**领取**归属 key。"""
    return f"task:{task_id}"


def delegation_key(delegation_id: str) -> str:
    """**交付单元执行**归属 key。"""
    return f"delegation:{delegation_id}"


@dataclass
class Lease:
    """一次成功领取的票据。`token` 用于释放时校验持有者，防止误放他人锁。"""

    key: str
    owner: str
    token: str
    acquired_at: float
    ttl: float

    def expired(self, now: Optional[float] = None) -> bool:
        now = now if now is not None else time.time()
        return (now - self.acquired_at) > self.ttl

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "owner": self.owner,
            "acquired_at": self.acquired_at,
            "ttl": self.ttl,
            "expired": self.expired(),
        }


class OwnershipRegistry:
    """进程内原子所有权登记表（纯内存，不落盘）。"""

    def __init__(self, default_ttl: float = 300.0):
        self.default_ttl = float(default_ttl)
        self._owners: dict[str, dict[str, Any]] = {}
        # 同步互斥量：临界区内无 await，跨 loop / 跨线程均安全（见模块 docstring）
        self._mutex = threading.Lock()
        self._stats = {"grants": 0, "denials": 0, "renewals": 0, "reaps": 0}

    # ── 领取 / 释放 ────────────────────────────────────────────
    async def acquire(self, key: str, owner: str, *, ttl: Optional[float] = None) -> Optional[Lease]:
        """尝试领取 `key`。拿到返回 `Lease`，拿不到返回 `None`（**拒绝，不排队**）。

        语义：
          1. 无主 / 已过期 → 授予；
          2. 同一 owner 重入 → **续租**（token 不变，容忍审查回退后的重试与兜底层二次 acquire）；
          3. 不同 owner 且未过期 → 拒绝（返回 None）。
        """
        key = str(key or "").strip()
        owner = str(owner or "").strip()
        if not key or not owner:
            return None
        _ttl = float(ttl) if ttl else self.default_ttl
        with self._mutex:
            now = time.time()
            cur = self._owners.get(key)
            if cur is not None:
                same_owner = cur["owner"] == owner
                if (now - cur["acquired_at"]) <= cur["ttl"]:
                    if same_owner:
                        # 同 owner 重入 → 续租；token 保持不变，旧票据继续有效
                        cur["acquired_at"] = now
                        cur["ttl"] = _ttl
                        self._stats["renewals"] += 1
                        return Lease(key, owner, cur["token"], now, _ttl)
                    self._stats["denials"] += 1
                    return None
                # 已过期 → 视为无主，落到下面重新授予（token 换新，旧票据彻底作废）
            token = uuid.uuid4().hex[:12]
            self._owners[key] = {
                "key": key,
                "owner": owner,
                "token": token,
                "acquired_at": now,
                "ttl": _ttl,
                "renewals": 0,
            }
            self._stats["grants"] += 1
            return Lease(key, owner, token, now, _ttl)

    async def release(self, key: str, owner: str, token: Optional[str] = None) -> bool:
        """释放 `key`。owner 不符（或传了 token 且不匹配）则不释放，返回 False。"""
        key = str(key or "").strip()
        owner = str(owner or "").strip()
        if not key or not owner:
            return False
        with self._mutex:
            cur = self._owners.get(key)
            if cur is None:
                return False
            if cur["owner"] != owner:
                return False
            if token and cur["token"] != token:
                return False
            self._owners.pop(key, None)
            return True

    # ── 只读查询 ──────────────────────────────────────────────
    def owner_of(self, key: str) -> Optional[str]:
        """当前 owner；无主或**已过期**均返回 None。"""
        with self._mutex:
            cur = self._owners.get(key)
            if cur is None:
                return None
            if (time.time() - cur["acquired_at"]) > cur["ttl"]:
                return None
            return cur["owner"]

    def snapshot(self) -> dict:
        """只读快照（供 /api 展示与测试断言）。"""
        with self._mutex:
            now = time.time()
            keys = [
                {
                    "key": v["key"],
                    "owner": v["owner"],
                    "acquired_at": v["acquired_at"],
                    "ttl": v["ttl"],
                    "renewals": v.get("renewals", 0),
                    "expired": (now - v["acquired_at"]) > v["ttl"],
                }
                for v in self._owners.values()
            ]
            return {
                "held": len(keys),
                "default_ttl": self.default_ttl,
                **self._stats,
                "keys": keys,
            }

    # ── 过期回收（stranded 孤儿修复的钩子；回收动作本身由 示例Harness-C 侧负责）──
    async def reap_expired(self) -> list[str]:
        """清除全部过期票据，返回被回收的 key 列表。"""
        with self._mutex:
            now = time.time()
            gone = [k for k, v in self._owners.items() if (now - v["acquired_at"]) > v["ttl"]]
            for k in gone:
                self._owners.pop(k, None)
            self._stats["reaps"] += len(gone)
            return gone

    def reset(self) -> None:
        """就地清空（测试隔离用；与 state.py 的 reset 语义一致，不重新绑定对象）。"""
        with self._mutex:
            self._owners.clear()
            for k in self._stats:
                self._stats[k] = 0


# 模块级单例：与 audit.py 的 audit_log、policy.py 同约定
ownership = OwnershipRegistry()
