"""平台侧 Agent 心跳登记表（2026-10-07 示例Harness-C 落地，47 号任务书 示例Harness-C 侧）。

背景：V-18 已实现任务卡死判定（基于 task.updated_at 超时）与回收端点，
但缺 agent 主动周期上报：无法区分"任务确实在进行（只是慢）"与"agent 已失联"。
本模块补上心跳登记表：
  - agent 周期上报 alive/idle/busy 到 POST /api/heartbeat；
  - HeartbeatRegistry 维护 last_seen / status / 周期，支持失联判定；
  - 失联列表可直接衔接 V-18 的 /api/tasks/stale 与回收端点（孤儿任务回收）。

设计原则：
  - 零第三方依赖（仅标准库 time/json）；
  - 不阻塞主流程：beat() 失败不炸，纯内存登记，可选落盘；
  - 与 wakeup_bridge / watchdog 并存：唤醒负责"叫醒"，心跳负责"知道谁还活着"。
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Optional


class HeartbeatRegistry:
    """Agent 心跳登记表。

    结构（内存 dict + 可选 JSON 落盘）::

        {
          "agent_id": {
            "harness_id": ...,
            "harness_name": ...,
            "interval": 30.0,          # 心跳周期（秒）
            "last_seen": 1728...,      # epoch 秒
            "status": "idle"|"busy"|"alive",
            "note": "...",
            "extra": {...},
            "since": 1728...,
          }
        }

    失联判定：``now - last_seen > interval * timeout_scale``（默认 2× 周期）。
    """

    def __init__(self, timeout_scale: float = 2.0, state_path: Optional[Path] = None):
        self.timeout_scale = timeout_scale
        self.state_path = state_path
        self._registry: dict[str, dict[str, Any]] = {}
        if state_path and state_path.exists():
            self._load(state_path)

    # ── 登记与上报 ─────────────────────────────────────────────
    def register(self, agent_id: str, harness_id: str = "", harness_name: str = "",
                 interval: float = 30.0, extra: Optional[dict] = None) -> dict:
        """注册（或更新）一个 agent 的心跳配置。返回登记记录。"""
        now = time.time()
        rec = self._registry.setdefault(agent_id, {})
        rec["harness_id"] = harness_id or rec.get("harness_id", "")
        rec["harness_name"] = harness_name or rec.get("harness_name", "")
        rec["interval"] = float(interval)
        rec["status"] = rec.get("status", "alive")
        rec["last_seen"] = now
        rec.setdefault("since", now)
        rec.setdefault("note", "")
        if extra:
            rec.setdefault("extra", {}).update(extra)
        self._maybe_persist()
        return rec

    def beat(self, agent_id: str, status: str = "alive", note: str = "",
             harness_id: str = "", now: Optional[float] = None) -> dict:
        """agent 周期上报。status ∈ {alive, idle, busy}。失败不炸（返回登记记录或空）。"""
        rec = self._registry.get(agent_id)
        if not rec:
            # 未登记也允许上报（幂等自登记），interval 给默认
            rec = self.register(agent_id, harness_id=harness_id, interval=30.0)
        rec["last_seen"] = now if now is not None else time.time()
        rec["status"] = status if status in ("alive", "idle", "busy") else "alive"
        rec["note"] = note or rec.get("note", "")
        if harness_id:
            rec["harness_id"] = harness_id
        self._maybe_persist()
        return rec

    def unregister(self, agent_id: str) -> bool:
        """注销（如 harness 下线时调用）。返回是否真的存在并移除。"""
        ok = self._registry.pop(agent_id, None) is not None
        if ok:
            self._maybe_persist()
        return ok

    # ── 查询与失联判定 ─────────────────────────────────────────
    def status(self, agent_id: str) -> Optional[dict]:
        rec = self._registry.get(agent_id)
        if not rec:
            return None
        return {**rec, "stale": self._is_stale(rec)}

    def online(self, now: Optional[float] = None) -> list[dict]:
        """在线（未失联）agent 列表。"""
        now = now if now is not None else time.time()
        return [
            {**rec, "agent_id": aid, "stale": False}
            for aid, rec in self._registry.items()
            if now - rec["last_seen"] <= rec["interval"] * self.timeout_scale
        ]

    def stale(self, now: Optional[float] = None) -> list[dict]:
        """失联（超时未上报）agent 列表。含 last_ago 秒与判定阈值。"""
        now = now if now is not None else time.time()
        out = []
        for aid, rec in self._registry.items():
            age = now - rec["last_seen"]
            threshold = rec["interval"] * self.timeout_scale
            if age > threshold:
                out.append({
                    **rec,
                    "agent_id": aid,
                    "last_ago": round(age, 1),
                    "threshold": round(threshold, 1),
                    "stale": True,
                })
        return sorted(out, key=lambda r: r["last_ago"], reverse=True)

    def _is_stale(self, rec: dict, now: Optional[float] = None) -> bool:
        now = now if now is not None else time.time()
        return now - rec["last_seen"] > rec["interval"] * self.timeout_scale

    # ── 持久化（可选） ─────────────────────────────────────────
    def to_dict(self) -> dict:
        return {
            "timeout_scale": self.timeout_scale,
            "registry": self._registry,
        }

    def from_dict(self, d: dict) -> None:
        if not d:
            return
        self.timeout_scale = float(d.get("timeout_scale", self.timeout_scale))
        self._registry = dict(d.get("registry", {}))

    def _maybe_persist(self) -> None:
        if not self.state_path:
            return
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            self.state_path.write_text(
                json.dumps(self.to_dict(), ensure_ascii=False, indent=1),
                encoding="utf-8",
            )
        except Exception:
            # 落盘失败不阻塞上报
            pass

    def _load(self, path: Path) -> None:
        try:
            self.from_dict(json.loads(path.read_text(encoding="utf-8")))
        except Exception:
            self._registry = {}


# 进程内单例：server.py 直接复用
heartbeat_registry = HeartbeatRegistry()
