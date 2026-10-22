# -*- coding: utf-8 -*-
"""名册健康（V85）：记真实结果、算连续失败天数、隐藏/申诉。**隐藏不删数据。**"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from pathlib import Path

from . import registry_lifecycle as rl

MAX_SAMPLES = 200


#: 健康库目录（测试会把它指到 tmp；生产留空 ⇒ 用 AC_DATA_DIR）
HEALTH_DIR = None


def _path() -> Path:
    if HEALTH_DIR is not None:
        return Path(HEALTH_DIR) / "registry_health.json"
    from .env_spec import data_dir

    return Path(data_dir()) / "registry_health.json"


def _load() -> dict:
    p = _path()
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8", errors="replace"))
        return data if isinstance(data, dict) else {}
    except Exception:                                        # noqa: BLE001 — 有意静默：坏文件当空表（下面写入会覆盖，但不会编数据）
        return {}


def _save(data: dict) -> None:
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def record(hid: str, ok: bool, why: str = "") -> dict:
    """记一条真实结果（回话探针 / 桥测试）。返回该 agent 的当前状态。"""
    data = _load()
    row = data.setdefault(str(hid), {"samples": [], "hidden": False, "hidden_reason": "",
                                     "hidden_at": "", "appeals": [], "trimmed": 0})
    samples = list(row.get("samples") or [])
    samples.append({"at": _now(), "ok": bool(ok), "why": str(why)[:200]})
    if len(samples) > MAX_SAMPLES:
        dropped = len(samples) - MAX_SAMPLES
        samples = samples[-MAX_SAMPLES:]
        row["trimmed"] = int(row.get("trimmed") or 0) + dropped
    row["samples"] = samples
    data[str(hid)] = row
    _save(data)
    return status(str(hid), data=data)


def days_all_failing(row: dict) -> int:
    """连续多少天**全失败**（0 = 最近一次是好的，或没有样本）。"""
    samples = list((row or {}).get("samples") or [])
    if not samples:
        return 0
    # 从最近往回看：遇到成功就停
    last_ok = None
    for s in reversed(samples):
        if s.get("ok"):
            last_ok = s.get("at")
            break
    fail_since = last_ok or samples[0].get("at")
    if not last_ok:
        # 从来没成功过：从第一条样本算起
        fail_since = samples[0].get("at")
    try:
        t0 = datetime.fromisoformat(str(fail_since))
    except Exception:                                        # noqa: BLE001 — 时间坏就当 0 天（不误判隐藏）
        return 0
    return max(0, (datetime.now() - t0).days)


def status(hid: str, data: dict | None = None) -> dict:
    data = data if data is not None else _load()
    row = data.get(str(hid)) or {}
    days = days_all_failing(row)
    return {"harness_id": str(hid),
            "samples": len(row.get("samples") or []),
            "trimmed": int(row.get("trimmed") or 0),
            "days_all_failing": days,
            "hidden": bool(row.get("hidden")),
            "hidden_reason": row.get("hidden_reason") or "",
            "hidden_at": row.get("hidden_at") or "",
            "appeals": len(row.get("appeals") or []),
            "candidate": rl.should_hide(days) and not row.get("hidden"),
            "policy": rl.policy()}


def overview() -> dict:
    data = _load()
    rows = [status(hid, data=data) for hid in sorted(data)]
    return {"count": len(rows), "agents": rows,
            "candidates": [r["harness_id"] for r in rows if r["candidate"]],
            "hidden": [r["harness_id"] for r in rows if r["hidden"]],
            "policy": rl.policy()}


def set_hidden(hid: str, on: bool, reason: str = "") -> dict:
    data = _load()
    row = data.setdefault(str(hid), {"samples": [], "hidden": False, "hidden_reason": "",
                                     "hidden_at": "", "appeals": [], "trimmed": 0})
    row["hidden"] = bool(on)
    row["hidden_reason"] = str(reason)[:200] if on else ""
    row["hidden_at"] = _now() if on else ""
    data[str(hid)] = row
    _save(data)
    return status(str(hid), data=data)


def appeal(hid: str, note: str = "") -> dict:
    """申诉 = 恢复可见 + 留痕（**可申诉**是这套口径的一部分）。"""
    data = _load()
    row = data.setdefault(str(hid), {"samples": [], "hidden": False, "hidden_reason": "",
                                     "hidden_at": "", "appeals": [], "trimmed": 0})
    row.setdefault("appeals", []).append({"at": _now(), "note": str(note)[:200]})
    row["hidden"] = False
    row["hidden_reason"] = ""
    row["hidden_at"] = ""
    data[str(hid)] = row
    _save(data)
    return status(str(hid), data=data)


def purge_detail(days: int = 0, dry_run: bool = True) -> dict:
    """清理**明细**（保留汇总：trimmed 计数与隐藏/申诉记录不动）。"""
    keep_days = int(days or rl.DETAIL_RETENTION_DAYS)
    cutoff = datetime.now() - timedelta(days=keep_days)
    data = _load()
    plan = {}
    for hid, row in list(data.items()):
        samples = list(row.get("samples") or [])
        kept = []
        for s in samples:
            try:
                t = datetime.fromisoformat(str(s.get("at")))
            except Exception:                                # noqa: BLE001 — 时间坏就保留（宁可留也不误删）
                kept.append(s)
                continue
            if t >= cutoff:
                kept.append(s)
        drop = len(samples) - len(kept)
        if drop:
            plan[hid] = drop
            if not dry_run:
                row["samples"] = kept
                row["trimmed"] = int(row.get("trimmed") or 0) + drop
                data[hid] = row
    if plan and not dry_run:
        _save(data)
    return {"applied": not dry_run, "keep_days": keep_days, "dropped": plan}
