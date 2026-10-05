# -*- coding: utf-8 -*-
"""platform/bridge_procs：桥进程存活表（V-15 步骤2）。

**为什么需要**：`_dispatch_to_harness` 的队列分支当前**无条件**返回
`True, "已入 pending 队列（等桥领取）"` —— 桥没跑也这么说（假成功）。
要判"有没有人会来领"，先得有一个**非阻塞的**存活源。

**现有三个「桥」的含义互不相同（步骤2 预检结论）：**

| 对象 | 是什么 | 能判存活吗 |
|---|---|---|
| `harness_manager.bridges[hid]` | 消息桥接器（HTTP/WS/文件轮询通道），**注册时创建、恒存在、无 proc** | ❌ 恒真 |
| `bridge_supervisor._procs[hid]` | supervisor 拉起的桥进程 | ✅ 但只覆盖经 supervisor 启动的 |
| `auto-connect` 的 `proc` | **局部变量，启动后不登记，函数返回即丢** | ❌ 平台事后不知道 |

另有 `_find_bridge_processes()` 会扫进程表，但它是**同步 `subprocess.run(timeout=60)`**，
放派发热路径会**阻塞事件循环最长 60 秒** —— 绝不能用于此。

**本模块解法**：由**知道自己启动了什么的代码**（`auto-connect`、supervisor）把 `proc` 登记进来，
这里只做内存 poll，**零 I/O、零阻塞**。

**三态语义（诚实，不打破手工启动流程）**：

    注册过且进程活着   -> alive   -> 允许入队
    注册过但进程已退出  -> dead    -> **拒绝入队**（返回 False）
    从未登记           -> unknown -> 允许入队，但 note 标注"存活未知"
                              （用户手动起的桥平台确实不知道，不能假装知道）

即：**平台只对它自己启动的进程下结论** —— 知道的就说，不知道的不装作知道。

纪律：内存表、无锁竞争敏感操作（GIL + 单写点）、异常不阻塞。
"""
from __future__ import annotations

import threading
import time
from typing import Any, Optional

# hid -> {"proc": Popen, "pid": int, "started_at": float, "cmd": str}
_procs: dict[str, dict] = {}
_lock = threading.RLock()


def record(harness_id: str, proc: Any, cmd: str = "") -> dict:
    """登记一个由平台启动的桥进程。`proc` 需暴露 `.poll()`。"""
    if not harness_id or proc is None:
        return {"error": "harness_id 与 proc 均必填"}
    pid = getattr(proc, "pid", None)
    with _lock:
        _procs[harness_id] = {
            "proc": proc,
            "pid": pid,
            "started_at": time.time(),
            "cmd": str(cmd)[:300],
        }
    print(f"[bridge_procs] 登记 {harness_id} pid={pid}", flush=True)
    return {"harness_id": harness_id, "pid": pid}


def status(harness_id: str) -> str:
    """返回 'alive' / 'dead' / 'unknown'。零 I/O，可热路径调用。"""
    with _lock:
        rec = _procs.get(harness_id)
    if rec is None:
        return "unknown"
    proc = rec.get("proc")
    if proc is None:
        return "dead"
    try:
        return "alive" if proc.poll() is None else "dead"
    except Exception:
        return "dead"


def is_alive(harness_id: str) -> bool:
    """平台启动过且仍活着？（从未登记 -> False，调用方需自行区分 unknown）"""
    return status(harness_id) == "alive"


def forget(harness_id: str) -> bool:
    """进程已退出/桥已注销 -> 移除记录。"""
    with _lock:
        return _procs.pop(harness_id, None) is not None


def prune() -> list[str]:
    """清理已退出的进程记录（供心跳循环调用）。返回被清理的 harness_id。"""
    dead = []
    with _lock:
        for hid, rec in list(_procs.items()):
            proc = rec.get("proc")
            try:
                if proc is None or proc.poll() is not None:
                    dead.append(hid)
                    _procs.pop(hid, None)
            except Exception:
                dead.append(hid)
                _procs.pop(hid, None)
    return dead


def describe() -> dict:
    """自省：给 GET /api/policy 或调试用。"""
    with _lock:
        out = {
            hid: {"pid": r.get("pid"),
                  "status": status(hid),
                  "started_at": r.get("started_at")}
            for hid, r in _procs.items()
        }
    return {"count": len(out), "bridges": out}
