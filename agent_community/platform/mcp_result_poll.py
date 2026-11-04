# -*- coding: utf-8 -*-
"""MCP 侧**异步结果**的自动回收（V128，老板 2026-11-04「继续开发 MCP 协议接口」）。

## 先把话讲清：MCP 没有"任务账本"这回事

A2A 派活是平台**先建了任务**，所以结果能写回账本（`a2a_result_poll` 干的就是这个）。
MCP 只是一次**工具调用** —— 平台什么都没建，**结果没有家**。所以本模块给两种情形分开去处：

| 情形 | 去向 | 判据 |
|---|---|---|
| 这次调用挂在某条任务上（`workshop_id` + `task_seq` 都有） | **任务账本**（`task_ledger.note_external_result(source="mcp")`） | 账本那条从 `issued` 推进到 `delivered`，`external.source == "mcp"` |
| 没挂任务（对接中心"试一下 / 送话"） | **出站调用结果表** `mcp_outcalls.json` | 那条 `state` 从 `pending` 变终态，带 `text` / `error` |

**不做**：没有账本条目也硬塞一条账本（那会污染验收历史，谁干的活说不清）。

## 与 A2A 的关系（一处真相 / 两套队列）

- **共用**：状态归一化 `a2a_result_poll.raw_state_of()`、`mcp_client.mcp_task_get()`、
  写账本的 `task_ledger.note_external_result()`；
- **分开**：队列文件（`a2a_pending.json` 里在跑真数据，改结构风险落在会工作的功能上）；
  纪律（上限/TTL/节流/统计口径）两边**取值一致**。
"""
from __future__ import annotations

import json
import time
from pathlib import Path

MAX_PENDING = 200            # 待查队列上限（超了丢最老）
MAX_OUT = 200                # 出站结果表上限（只留最近）
TTL_SEC = 24 * 3600          # 24 小时没终态就出队（留痕写明为什么）
POLL_GAP_SEC = 30            # 同一条两次查询的最小间隔（别把对端打爆）
MAX_PER_SWEEP = 5            # 单轮最多查几条（多轮均摊）


def _dir() -> Path:
    from .env_spec import data_dir

    return Path(data_dir())


def _pending_path() -> Path:
    return _dir() / "mcp_pending.json"


def _out_path() -> Path:
    return _dir() / "mcp_outcalls.json"


def _load(path: Path) -> list:
    if not path.is_file():
        return []
    try:
        d = json.loads(path.read_text(encoding="utf-8") or "[]")
        return d if isinstance(d, list) else []
    except Exception as e:                                     # noqa: BLE001 — 坏文件不拖垮主流程
        print("[mcp-poll] 读 %s 失败（按空表处理）：%s: %s" % (path.name, type(e).__name__, e),
              flush=True)
        return []


def _save(path: Path, items: list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


# ── 出站结果表（对外可见的"这个工具调用最后怎么了"）─────────────────

def list_outcalls(harness_id: str = "", limit: int = 20) -> list:
    """最近 N 条出站调用（新的在前）；可按 harness 过滤。只读。"""
    items = _load(_out_path())
    if harness_id:
        items = [x for x in items if str(x.get("harness_id") or "") == str(harness_id)]
    n = max(1, min(int(limit or 20), MAX_OUT))
    return list(reversed(items[-n:]))


def _upsert_outcall(rec: dict) -> None:
    """按 `call_id` 覆盖写一条（表有界，只留最近 MAX_OUT 条）。"""
    items = _load(_out_path())
    cid = str(rec.get("call_id") or "")
    for i, old in enumerate(items):
        if str(old.get("call_id") or "") == cid:
            items[i] = dict(old, **rec)
            break
    else:
        items.append(rec)
    if len(items) > MAX_OUT:
        items = items[-MAX_OUT:]
    _save(_out_path(), items)


# ── 登记（调用方拿到 `async=True` 时调一次）──────────────────────────

def maybe_track(*, harness_id: str, url: str, tool: str, call: dict | None,
                workshop_id: str = "", task_seq: int = 0,
                args_key: str = "taskId") -> dict | None:
    """**只有真异步才登记**（`call.async == True` 且给了句柄）。

    返回登记的那条记录；不是异步就返回 None（调用方什么都不用做）。
    幂等：同一 `url + handle` 只留一条。
    """
    call = call if isinstance(call, dict) else {}
    if not call.get("async"):
        return None
    handle = str(call.get("handle") or "").strip()
    if not handle:
        # 对方说 pending 却没给句柄 ⇒ 记一条"查不了"，别让它静默消失
        rec = {
            "call_id": "mcp-%d-%s" % (int(time.time() * 1000), handle or "nohost"),
            "harness_id": str(harness_id or ""), "url": str(url or ""), "tool": str(tool or ""),
            "handle": "", "convention": {},
            "workshop_id": str(workshop_id or ""), "task_seq": int(task_seq or 0),
            "args_key": str(args_key or "taskId"),
            "added_at": time.time(), "last_at": 0.0, "tries": 0,
            "state": "unsupported", "text": "", "error": "",
            "why": "对方回了 pending，但没给句柄（查不了）",
        }
        _upsert_outcall(rec)
        return rec

    items = _load(_pending_path())
    for it in items:
        if str(it.get("url") or "") == str(url) and str(it.get("handle") or "") == handle:
            return it
    rec = {
        "call_id": "mcp-%d-%s" % (int(time.time() * 1000), handle[:12]),
        "harness_id": str(harness_id or ""), "url": str(url or ""), "tool": str(tool or ""),
        "handle": handle, "convention": {},
        "workshop_id": str(workshop_id or ""), "task_seq": int(task_seq or 0),
        "args_key": str(args_key or "taskId"),
        "added_at": time.time(), "last_at": 0.0, "tries": 0,
        "state": "pending", "text": "", "error": "", "why": "",
    }
    items.append(rec)
    if len(items) > MAX_PENDING:
        items = items[-MAX_PENDING:]
        print("[mcp-poll] 待查表超过 %d 条，丢掉最老的" % MAX_PENDING, flush=True)
    _save(_pending_path(), items)
    _upsert_outcall(rec)
    print("[mcp-poll] 登记异步任务：%s / %s（句柄 %s）" %
          (harness_id or "-", tool, handle[:24]), flush=True)
    return rec


def list_pending() -> list:
    return _load(_pending_path())


def reset_for_tests() -> None:
    """测试用：清空两张表（写空数组，不删文件 —— 回读路径也被测到）。"""
    _save(_pending_path(), [])
    _save(_out_path(), [])


# ── 轮询（由 `server._autonomy_recovery_loop` 每 20s 调一次）──────────

async def sweep_once(*, now: float | None = None, gap: float | None = None) -> dict:
    """对每条待查查一次状态：终态就**写回它的家**并出队。

    返回统计 `{checked, done, failed, dropped, unsupported}` —— 后台把它打进日志，
    让人能看出"到底动没动"。
    """
    from . import mcp_client as _mc
    from . import task_ledger as _ledger

    now = time.time() if now is None else now
    gap = POLL_GAP_SEC if gap is None else gap
    items = _load(_pending_path())
    if not items:
        return {"checked": 0, "done": 0, "failed": 0, "dropped": 0, "unsupported": 0}

    keep: list = []
    stats = {"checked": 0, "done": 0, "failed": 0, "dropped": 0, "unsupported": 0}
    for it in items:
        # 过期出队（对端一直不完成）
        if now - float(it.get("added_at") or 0) > TTL_SEC:
            stats["dropped"] += 1
            it = dict(it, state="expired", why="超过 %d 小时没终态" % (TTL_SEC // 3600))
            _upsert_outcall(it)
            print("[mcp-poll] 出队（超时无终态）：%s / %s" %
                  (it.get("harness_id"), it.get("handle")), flush=True)
            continue
        # 同一条别查太勤 / 单轮别查太多
        if now - float(it.get("last_at") or 0) < gap:
            keep.append(it)
            continue
        if stats["checked"] >= MAX_PER_SWEEP:
            keep.append(it)
            continue

        it["last_at"] = now
        it["tries"] = int(it.get("tries") or 0) + 1
        stats["checked"] += 1
        try:
            res = await _mc.mcp_task_get(str(it.get("url") or ""), str(it.get("handle") or ""),
                                         it.get("convention") or None,
                                         args_key=str(it.get("args_key") or "taskId"))
        except Exception as e:                                 # noqa: BLE001 — 查不到下次再试
            it["last_error"] = "%s: %s" % (type(e).__name__, str(e)[:160])
            it["error"] = it["last_error"]
            stats["failed"] += 1
            print("[mcp-poll] 查询失败（下次再试）：%s -> %s" %
                  (it.get("handle"), it["last_error"]), flush=True)
            keep.append(it)
            _upsert_outcall(it)
            continue

        it["convention"] = res.get("convention") or it.get("convention") or {}
        state = str(res.get("state") or "unknown")
        it["state"] = state
        if state == "unsupported":
            # 对方不支持任何异步约定 ⇒ 出队并**写明为什么**，不再重试（不装"还在跑"）
            it["why"] = str(res.get("error") or (it.get("convention") or {}).get("why") or "")
            stats["unsupported"] += 1
            _upsert_outcall(it)
            print("[mcp-poll] 出队（对端不支持异步）：%s -> %s" %
                  (it.get("handle"), (it["why"] or "")[:120]), flush=True)
            continue
        if not res.get("terminal"):
            keep.append(it)                                    # 还在干，留着
            _upsert_outcall(it)
            continue

        text = str(res.get("text") or "")
        it["text"] = text
        it["why"] = ""
        # ── 写回它的家 ──
        if int(it.get("task_seq") or 0) > 0 and it.get("workshop_id"):
            rec = _ledger.note_external_result(
                str(it.get("workshop_id")), int(it.get("task_seq") or 0), source="mcp",
                external_task_id=str(it.get("handle") or ""), external_state=state,
                result=text, artifacts=[])
            if rec is None:
                it["why"] = "账本里没有这条任务（工作间被删/任务号变了），结果只留在出站表"
                stats["dropped"] += 1
                _upsert_outcall(it)
                print("[mcp-poll] 出队（账本里没有这条）：%s#%s" %
                      (it.get("workshop_id"), it.get("task_seq")), flush=True)
                continue
        stats["done"] += 1
        _upsert_outcall(it)
        print("[mcp-poll] 已回写：%s（状态 %s）" % (it.get("handle"), state), flush=True)

    _save(_pending_path(), keep)
    return stats
