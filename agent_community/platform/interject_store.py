"""InterjectStore — 插话存储（独立于工作循环的实体）+ 系统插话升级通道。

设计依据：《平台设计：插话与自治边界》§一（数据层 / 调度层 / 闭环）。
- 插话是独立于工作循环的实体，不混入 /api/command 的 Task 流。
- JSON 结构：id / content / priority / status / ts / inserted_at / related_task / kind / owner / level
- 状态流转：pending(待处理) → inserted(已插入) / ignored(已忽略) / expired(已过期)
- 系统插话升级通道：L1(紧急，注入讨论区) / L2(需人工，挂起等用户，不降级不放弃)
- 闭环：插入后标记 inserted 并回填 inserted_at / related_task；超 N 个循环未处理 → expired 防堆积。
- 纯数据 + 依赖注入：不 import 主模块全局单例。
"""
from __future__ import annotations

from datetime import datetime

PRIORITIES = ("紧急", "灵感", "一般")
STATUSES = ("pending", "inserted", "ignored", "expired")


def _now_ts() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class InterjectStore:
    def __init__(self, max_pending_cycles: int = 3):
        self.max_pending_cycles = max_pending_cycles
        self._items: dict[str, list[dict]] = {}  # ws_id -> [interject, ...]
        self._seq: dict[str, int] = {}           # ws_id -> id 计数

    # ── 基础 ────────────────────────────────────────────────
    def _next_id(self, ws_id: str) -> str:
        n = self._seq.get(ws_id, 0) + 1
        self._seq[ws_id] = n
        return f"it_{n}"

    def submit(self, ws_id: str, content: str, priority: str = "一般",
               kind: str = "user", owner: str = "用户", level: str = None) -> dict:
        """用户/系统提交一条插话，初始状态 pending。"""
        it = {
            "id": self._next_id(ws_id),
            "content": str(content or "").strip(),
            "priority": priority if priority in PRIORITIES else "一般",
            "status": "pending",
            "ts": _now_ts(),
            "inserted_at": None,
            "related_task": None,
            "kind": kind if kind in ("user", "system") else "user",
            "owner": owner or ("系统" if kind == "system" else "用户"),
            "level": level,
        }
        self._items.setdefault(ws_id, []).append(it)
        return it

    def system_interject(self, ws_id: str, content: str, level: str = "L1") -> dict:
        """系统插话升级通道（L1/L2 共用）。

        - L1 需决策：priority=紧急，插入后由调度层注入讨论区；
        - L2 需人工：priority=紧急，挂起等用户（不降级不放弃）。
        """
        it = self.submit(ws_id, content, priority="紧急", kind="system", owner="系统", level=level)
        return it

    # ── 查询 / 流转 ─────────────────────────────────────────
    def list(self, ws_id: str) -> list:
        """按 状态(pending 优先) × 优先级(紧急优先) × 时间 排序返回。"""
        items = list(self._items.get(ws_id, []))
        _order = {"pending": 0, "inserted": 1, "ignored": 2, "expired": 3}
        _pri = {"紧急": 0, "灵感": 1, "一般": 2}
        items.sort(key=lambda x: (_order.get(x.get("status"), 4),
                                  _pri.get(x.get("priority"), 3),
                                  x.get("ts", "")))
        return items

    def get(self, ws_id: str, it_id: str):
        for it in self._items.get(ws_id, []):
            if it["id"] == it_id:
                return it
        return None

    def mark(self, ws_id: str, it_id: str, status: str, **extra) -> dict:
        """状态流转：pending → inserted / ignored / expired；inserted 时回填 inserted_at。"""
        it = self.get(ws_id, it_id)
        if not it:
            return {"ok": False, "error": "not_found"}
        if status not in STATUSES:
            return {"ok": False, "error": "invalid_status"}
        it["status"] = status
        if status == "inserted" and not it.get("inserted_at"):
            it["inserted_at"] = _now_ts()
        if extra:
            it.update(extra)
        return {"ok": True, "interject": it}

    def expire_stale(self, ws_id: str, current_pending_cycles: int = None) -> list:
        """超过 N 个工作循环未处理 → expired 并提示，防堆积。返回过期列表。"""
        items = self._items.get(ws_id, [])
        expired = []
        limit = current_pending_cycles or self.max_pending_cycles
        for i, it in enumerate(items):
            if it.get("status") != "pending":
                continue
            # 近似判据：排在它前面且仍 pending 的条目数 >= 阈值 → 视为积压过期
            ahead = sum(1 for x in items[:i] if x.get("status") == "pending")
            if ahead >= limit:
                it["status"] = "expired"
                expired.append(it)
        return expired

    # ── 持久化 ──────────────────────────────────────────────
    def to_dict(self) -> dict:
        return {"items": self._items, "seq": self._seq,
                "max_pending_cycles": self.max_pending_cycles}

    def from_dict(self, d: dict) -> None:
        if not d:
            return
        self._items = {str(k): list(v) for k, v in (d.get("items") or {}).items()}
        self._seq = {str(k): int(v) for k, v in (d.get("seq") or {}).items()}
        self.max_pending_cycles = int(d.get("max_pending_cycles", self.max_pending_cycles))
