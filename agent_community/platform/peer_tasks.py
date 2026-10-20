# -*- coding: utf-8 -*-
"""platform/peer_tasks：**外部任务**记录（V63）——"我们派给外部公司的活"在本侧的可视记录。

为什么单独一类记录（而不是建内部工作间）：
建工作间会触发讨论/动员/通道分配，等于把自己的员工叫来干别人公司的活。
外部任务只需要：**投给谁、投的什么、现在什么状态、回执几时回**。

落盘：`<AC_DATA_DIR>/peer_tasks.json`（**只记索引与状态，不复制对端数据**）。

状态语义（诚实优先）：
  `sent` 已投出、对端没回话 / `working` 对端说在做 / `done` / `failed`
  / **`unreachable` 够不着对端**（绝不留在 working 假装还在跑）
"""
from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from typing import Optional

STATUSES = ("sent", "working", "done", "failed", "unreachable")


def tasks_file() -> Path:
    from .identity import _default_data_root
    return Path(_default_data_root()) / "peer_tasks.json"


def load_tasks() -> dict:
    p = tasks_file()
    if not p.is_file():
        return {"tasks": {}}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:                                        # noqa: BLE001
        try:
            bad = p.with_suffix(".json.bad_%d" % int(time.time()))
            p.replace(bad)
            print("[peer_tasks] 记录损坏，已备份到 %s 并重建" % bad.name, flush=True)
        except Exception:                                    # noqa: BLE001
            pass
        return {"tasks": {}}
    if not isinstance(data, dict) or not isinstance(data.get("tasks"), dict):
        return {"tasks": {}}
    return data


def save_tasks(data: dict) -> None:
    p = tasks_file()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, p)          # 原子替换


def now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def create(peer_id: str, peer_name: str, title: str, remote_task_id: str,
           workshop_id: str = "") -> dict:
    """派活成功后立刻建记录（`status=sent`）。"""
    data = load_tasks()
    pt_id = "pt_" + uuid.uuid4().hex[:8]
    rec = {
        "pt_id": pt_id,
        "peer_id": peer_id,
        "peer_name": peer_name,
        "title": title[:300],
        "workshop_id": (workshop_id or "").strip(),
        "remote_task_id": remote_task_id,
        "status": "sent",
        "created_at": now(),
        "updated_at": now(),
        "last_check": {},
        "timeline": [{"at": now(), "event": "sent",
                      "detail": "已投给 %s（对端任务 %s）" % (peer_name or peer_id, remote_task_id or "?")}],
    }
    data["tasks"][pt_id] = rec
    save_tasks(data)
    return rec


def get(pt_id: str) -> Optional[dict]:
    return load_tasks()["tasks"].get((pt_id or "").strip())


def list_tasks(peer_id: str = "", workshop_id: str = "", status: str = "") -> list:
    items = list(load_tasks()["tasks"].values())
    if peer_id:
        items = [x for x in items if x.get("peer_id") == peer_id]
    if workshop_id:
        items = [x for x in items if x.get("workshop_id") == workshop_id]
    if status:
        items = [x for x in items if x.get("status") == status]
    items.sort(key=lambda x: str(x.get("created_at") or ""), reverse=True)
    return items


def _map_remote_status(info: dict) -> tuple[str, str]:
    """把对端返回的状态翻成本侧状态 + 一句人话。"""
    raw = ""
    if isinstance(info, dict):
        raw = str(info.get("status") or (info.get("task") or {}).get("status") or "").lower()
    mapping = {
        "completed": ("done", "对端已完成"),
        "complete": ("done", "对端已完成"),
        "done": ("done", "对端已完成"),
        "failed": ("failed", "对端失败"),
        "canceled": ("failed", "对端已取消"),
        "cancelled": ("failed", "对端已取消"),
        "working": ("working", "对端在做"),
        "in_progress": ("working", "对端在做"),
        "submitted": ("working", "对端已接收"),
        "input_required": ("working", "对端需要补充信息"),
    }
    if raw in mapping:
        st, detail = mapping[raw]
        return st, "%s（对端报 %s）" % (detail, raw)
    return "working", "对端报了未知状态 %r，按在做处理（不假装完成）" % raw


def record_check(pt_id: str, ok: bool, info: dict = None, error: str = "") -> dict:
    """把一次回查结果写进记录（**失败就标 unreachable，不留 working**）。"""
    data = load_tasks()
    rec = data["tasks"].get((pt_id or "").strip())
    if not rec:
        return {}
    at = now()
    if ok:
        st, detail = _map_remote_status(info or {})
    else:
        st, detail = "unreachable", "够不着对端：%s" % (error or "未知原因")
    rec["status"] = st
    rec["updated_at"] = at
    rec["last_check"] = {"at": at, "ok": bool(ok), "status": st, "detail": detail}
    rec.setdefault("timeline", []).append({"at": at, "event": "check", "detail": detail})
    data["tasks"][rec["pt_id"]] = rec
    save_tasks(data)
    return rec


def public_view(rec: dict) -> dict:
    """给界面看的样子（不含任何凭据）。"""
    out = {k: rec.get(k) for k in ("pt_id", "peer_id", "peer_name", "title", "workshop_id",
                                   "remote_task_id", "status", "created_at", "updated_at")}
    out["last_check"] = rec.get("last_check") or {}
    out["timeline"] = (rec.get("timeline") or [])[-5:]
    return out
