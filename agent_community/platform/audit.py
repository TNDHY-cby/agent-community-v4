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

DEFAULT_DATA_DIR = Path(os.environ.get("AC_DATA_DIR") or (Path(__file__).resolve().parent.parent / "data"))
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
        extra: Optional[dict] = None,
    ) -> dict:
        """写一条审计。

        V-20.1 增 `extra`（2026-10-07，FE-01 接口变更申请 #1）：允许事件附带**结构化字段**，
        使前端不必解析 `detail` 这个人类可读字符串。规则：
          - **纯新增**：`extra` 为空时行为与改动前逐字节一致；
          - **不可覆盖既有字段**（`ts`/`event`/`actor`/`target`/`detail`）—— 命中即忽略该键，
            防止调用方误用把审计的身份字段冲掉；
          - `detail` **永久保留**，老记录也没有 extra 字段，故前端必须能同时处理两种形态。
        """
        ev = {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "event": event,
            "actor": actor,
            "target": target,
            "detail": detail,
        }
        if extra:
            for k, v in extra.items():
                k = str(k)
                if k in ev:          # 既有身份字段不可被覆盖
                    continue
                ev[k] = v
        self.events.append(ev)
        if len(self.events) > self.max_records:
            self.events = self.events[-self.max_records:]
        self._save()
        return ev

    def _filtered(
        self,
        event: Optional[str] = None,
        actor: Optional[str] = None,
        target: Optional[str] = None,
    ) -> list[dict]:
        """按条件过滤（**不截断、不排序**）。query / query_page 共用，保证两者口径一致。"""
        out = self.events
        if event:
            out = [e for e in out if e.get("event") == event]
        if actor:
            out = [e for e in out if e.get("actor") == actor]
        if target:
            out = [e for e in out if e.get("target") == target]
        return out

    def query(
        self,
        limit: int = 100,
        event: Optional[str] = None,
        actor: Optional[str] = None,
        target: Optional[str] = None,
    ) -> list[dict]:
        """只读查询：按 event / actor / target 过滤，按时间倒序返回最近 limit 条。

        V-20 增 `target`：工单化审计按「工单号」检索（task.transition 事件的 target 即 ws_id）。
        """
        return self._filtered(event, actor, target)[-limit:][::-1]

    def query_page(
        self,
        limit: int = 100,
        event: Optional[str] = None,
        actor: Optional[str] = None,
        target: Optional[str] = None,
    ) -> tuple[list[dict], int]:
        """返回 `(当页事件, 过滤后命中总数)`。

        V-20.1 增（2026-10-07，FE-01 接口变更申请 #2）：`total` 是**limit 截断前**的命中数，
        让前端能区分「只有这么多」与「被 limit 截断了」——ring buffer 上限 500，
        只靠本次返回的条数无法判断（前端原话）。
        """
        hit = self._filtered(event, actor, target)
        return hit[-limit:][::-1], len(hit)


audit_log = AuditLog()
