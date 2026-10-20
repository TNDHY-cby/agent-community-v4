# -*- coding: utf-8 -*-
"""platform/peer_poll：**对外任务回执自动收回**（V63 §6）。

设计取舍（都在设计稿里写了，这里只留实现要点）：

- **默认关**（`AC_PEER_POLL_ENABLED=0`）：老板 2026-10-07 定过"自动兜底默认关"，
  自动对外发请求属于"要用户明确点头"的那类；
- **省网络**：每轮只查 `sent` / `working` 的**最老 3 条**（`AC_PEER_POLL_MAX`），失败按指数退避；
- **诚实**：自动收回失败同样标 `unreachable` 并进时间线（不因为"后台跑的"就吞掉失败）；
- **可审计**：`peer.task.auto` 与人工点刷新（`peer.task.refresh`）分开记。
"""
from __future__ import annotations

import os
import threading
import time
from typing import Optional

_STOP = threading.Event()
_THREAD: Optional[threading.Thread] = None
_LAST: dict = {}


def _env_int(name: str, default: int) -> int:
    v = (os.environ.get(name) or "").strip()
    try:
        return int(v)
    except Exception:                                        # noqa: BLE001
        return default


def is_enabled() -> bool:
    """默认关：只有显式设了 `AC_PEER_POLL_ENABLED=1/true/yes` 才开。"""
    v = (os.environ.get("AC_PEER_POLL_ENABLED") or "").strip().lower()
    return v in ("1", "true", "yes", "on")


def interval() -> int:
    return max(10, _env_int("AC_PEER_POLL_INTERVAL", 60))


def max_per_round() -> int:
    return max(1, min(20, _env_int("AC_PEER_POLL_MAX", 3)))


def _backoff_seconds(attempt: int) -> int:
    """失败后的等待：60 × 2^n，上限 600s（10 分钟）。"""
    return min(600, interval() * (2 ** max(0, attempt)))


def tick(now: Optional[float] = None) -> dict:
    """跑**一轮**（可单独调用，便于判据；线程只是按时调它）。返回本轮结果摘要。"""
    from . import peer_tasks as _pt
    from . import peers as _peers

    now = time.time() if now is None else now
    active = [r for r in _pt.list_tasks() if r.get("status") in ("sent", "working", "unreachable")]
    active.sort(key=lambda r: str(r.get("created_at") or ""))
    picked = active[:max_per_round()]
    done = []
    for rec in picked:
        pt_id = rec.get("pt_id") or ""
        state = _LAST.get(pt_id) or {"next": 0.0, "attempt": 0}
        if now < float(state.get("next") or 0):
            continue
        peer = _peers.get_peer(rec.get("peer_id") or "") or {}
        ok, info, err = False, {}, ""
        if not peer:
            err = "对端登记已不存在"
        else:
            token = _peers.peer_token(peer)
            base = str(peer.get("base_url") or "").rstrip("/")
            try:
                from ..a2a_client import get_task
                info = get_task(base + "/", rec.get("remote_task_id") or "", token, 20)
                ok = True
            except Exception as e:                           # noqa: BLE001
                err = "%s: %s" % (type(e).__name__, str(e)[:120])
        updated = _pt.record_check(pt_id, ok, info=info, error=err)
        try:
            from ..audit import audit_log as _a
            _a.record("peer.task.auto", actor="system:poll", target=rec.get("peer_id") or "",
                      detail="%s → %s%s" % (pt_id, updated.get("status"), "" if ok else "（" + err[:60] + "）"))
        except Exception:                                    # noqa: BLE001
            pass
        if ok:
            _LAST[pt_id] = {"next": 0.0, "attempt": 0}
        else:
            attempt = int(state.get("attempt") or 0) + 1
            _LAST[pt_id] = {"next": now + _backoff_seconds(attempt), "attempt": attempt}
        done.append({"pt_id": pt_id, "status": updated.get("status"), "ok": ok})
    # 清理已结束任务的状态（避免 _LAST 无限增长）
    for key in list(_LAST):
        if not _pt.get(key):
            _LAST.pop(key, None)
    return {"picked": len(picked), "checked": len(done), "items": done, "at": now}


def _loop() -> None:
    while not _STOP.wait(interval()):
        try:
            tick()
        except Exception as e:                               # noqa: BLE001
            print("[peer_poll] 一轮失败（继续跑下一轮）: %s: %s" % (type(e).__name__, e), flush=True)


def start() -> bool:
    """按配置启动后台线程。返回是否真的启动了（**默认返回 False**）。"""
    global _THREAD
    if not is_enabled():
        return False
    if _THREAD and _THREAD.is_alive():
        return True
    _STOP.clear()
    _THREAD = threading.Thread(target=_loop, name="peer-poll", daemon=True)
    _THREAD.start()
    print("[peer_poll] 已启动：每 %ds 收一轮回执，最多 %d 条/轮" %
          (interval(), max_per_round()), flush=True)
    return True


def stop() -> None:
    global _THREAD
    _STOP.set()
    _THREAD = None


def status() -> dict:
    return {"enabled": is_enabled(), "running": bool(_THREAD and _THREAD.is_alive()),
            "interval_s": interval(), "max_per_round": max_per_round(),
            "tracked": len(_LAST), "note": "默认关；设 AC_PEER_POLL_ENABLED=1 才开"}
