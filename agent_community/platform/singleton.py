# -*- coding: utf-8 -*-
"""单实例守卫（V82 §2）：**操作系统文件锁**，进程退出自动释放。

用法：
    from .singleton import acquire
    lease = acquire()            # 拿不到会抛 SingletonBusy（fail_closed）或返回 degraded 租约
    ...
    lease.release()              # 正常退出时；进程崩溃时内核替我们释放

为什么不"写 pid 文件"：pid 文件在进程被杀后留在原地，需要人去清；
操作系统锁没有这个问题，也不会因为重启机器而残留。
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path


class SingletonBusy(RuntimeError):
    """另一个实例正持有数据目录。"""


def lock_path(create: bool = True) -> Path:
    """锁文件路径。`create=False` 时**只算路径不建目录**（读状态用，别产生副作用）。"""
    from .env_spec import data_dir

    d = Path(data_dir()) / "run"
    if create:
        d.mkdir(parents=True, exist_ok=True)
    return d / "platform.lock"


def _try_lock(fh) -> bool:
    """非阻塞拿排它锁。POSIX 用 fcntl.flock，Windows 用 msvcrt.locking。"""
    try:
        if os.name == "nt":
            import msvcrt

            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def _unlock(fh) -> None:
    try:
        if os.name == "nt":
            import msvcrt

            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
    except OSError:                                          # noqa: BLE001 — 有意静默：释放锁失败说明锁本来就不在了（进程退出时内核会收），不值得刷屏
        pass


class Lease:
    def __init__(self, fh, path: Path, degraded: bool = False):
        self._fh = fh
        self.path = path
        self.degraded = degraded

    def release(self) -> None:
        if self._fh is None:
            return
        try:
            _unlock(self._fh)
            self._fh.close()
        finally:
            self._fh = None


def policy() -> str:
    """拿不到锁时的行为：`fail_closed`（默认，拒起）或 `degraded`（起但标记降级）。"""
    v = (os.environ.get("AC_SINGLETON_UNAVAILABLE") or "fail_closed").strip().lower()
    return v if v in ("fail_closed", "degraded") else "fail_closed"


def who() -> dict:
    """锁文件里记的持有者信息（人话用；锁本身靠操作系统）。**只读，不建目录。**"""
    p = lock_path(create=False)
    try:
        raw = p.read_text(encoding="utf-8", errors="replace").strip()
        return json.loads(raw) if raw else {}
    except Exception:                                        # noqa: BLE001
        return {}


def acquire(strict: bool = True) -> Lease:
    """拿单实例锁。失败时：`fail_closed` 抛 SingletonBusy；`degraded` 返回 degraded 租约。"""
    p = lock_path()
    fh = open(p, "a+", encoding="utf-8")
    if _try_lock(fh):
        try:
            fh.seek(0)
            fh.truncate()
            fh.write(json.dumps({"pid": os.getpid(), "at": datetime.now().isoformat(timespec="seconds"),
                                 "python": sys.executable}, ensure_ascii=False))
            fh.flush()
        except Exception:                                    # noqa: BLE001 — 写说明失败不影响锁本身
            pass
        return Lease(fh, p)

    holder = who()
    fh.close()
    why = "另一个平台实例正持有数据目录 %s（PID=%s，起于 %s）" % (
        p.parent.parent, holder.get("pid") or "?", holder.get("at") or "?")
    if strict and policy() == "fail_closed":
        raise SingletonBusy(why + "。要先停掉那个实例，或设 AC_SINGLETON_UNAVAILABLE=degraded 强行起（不推荐）")
    print("[singleton] 降级启动：" + why, flush=True)
    return Lease(None, p, degraded=True)


def state() -> dict:
    """运行状态（给 `/api/status` 用）：谁持有锁、策略、是不是降级。"""
    holder = who()
    return {"policy": policy(), "held": bool(holder), "holder": holder,
            "degraded": bool(holder) and str(holder.get("pid")) != str(os.getpid())}
