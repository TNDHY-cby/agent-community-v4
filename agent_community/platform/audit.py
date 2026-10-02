# -*- coding: utf-8 -*-
"""审计面板 P2-1：轻量操作审计（零依赖、失败不阻塞业务）。

设计原则：
- 纯内存 ring buffer + 惰性追加写 data/audit.json（上限 AUDIT_MAX），
  不新建依赖，不引入后台线程（写盘在调用线程内 try/except 包裹）；
- 任何异常仅打印并忽略，绝不阻塞主流程（与 experience_v2 同原则）；
- 事件结构统一：ts / event / actor / target / detail，供 GET /api/audit 展示。
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

DEFAULT_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
AUDIT_MAX = 500  # 内存与落盘各保留上限


class AuditLog:
    def __init__(self, data_dir: Path = DEFAULT_DATA_DIR, max_records: int = AUDIT_MAX):
        self.data_dir = data_dir
        self.max_records = max_records
        self.events: list[dict] = []
        self._path = data_dir / "audit.json"
        self._load()

    def _load(self) -> None:
        try:
            if self._path.exists():
                with open(self._path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, list):
                    self.events = data[-self.max_records:]
        except Exception as e:
            print(f"[audit] 加载 audit.json 失败（忽略）: {e}", flush=True)

    def _save(self) -> None:
        try:
            self.data_dir.mkdir(parents=True, exist_ok=True)
            with open(self._path, "w", encoding="utf-8") as f:
                json.dump(self.events[-self.max_records:], f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[audit] 写 audit.json 失败（忽略）: {e}", flush=True)

    def record(
        self,
        event: str,
        actor: str = "",
        target: str = "",
        detail: str = "",
    ) -> dict:
        ev = {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "event": event,
            "actor": actor,
            "target": target,
            "detail": detail,
        }
        self.events.append(ev)
        if len(self.events) > self.max_records:
            self.events = self.events[-self.max_records:]
        self._save()
        return ev

    def query(self, limit: int = 100, event: Optional[str] = None, actor: Optional[str] = None) -> list[dict]:
        """只读查询：按 event / actor 过滤，按时间倒序返回最近 limit 条。"""
        out = self.events
        if event:
            out = [e for e in out if e.get("event") == event]
        if actor:
            out = [e for e in out if e.get("actor") == actor]
        return out[-limit:][::-1]


audit_log = AuditLog()
