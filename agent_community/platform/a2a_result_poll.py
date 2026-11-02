# -*- coding: utf-8 -*-
"""A2A 派活后的**结果回写**（清单 B2：派了活，平台账本里永远没有结果）。

## 为什么需要它

平台按 `tasks/send` 派出后只拿到一个对端 task id；之后的产物/状态**没有任何一方写回平台**，
于是任务账本里这条永远停在 `issued` —— 工作间/验收看不到产出。

## 两条路一起走（清单 B2 的建议）

1. **平台侧轮询**（本模块）：派活成功就把 `(对端端点, 对端任务号, 工作间, 任务序号)` 记进
   `a2a_pending.json`；后台按间隔 `GetTask`，一旦到终态（或带 artifacts）就
   `task_ledger.note_external_result(...)` 写回，然后出队。
2. **回报契约**（写在任务正文里，由 `dispatch` 负责）：告诉对端"干完往哪 POST 回执"，
   它主动调 `POST /api/harness/task-result` 也行 —— 两条路谁先到算谁。

## 三条纪律（都是踩过的）

- **只记"回执留痕"，不替人验收**：`note_external_result` 只推进到 `delivered`，
  `accepted/rejected` 仍只能由人（`task_ledger.review`）给。
- **失败要说话**：连不上/被拒/认证失败都记进记录并打印，不静默丢弃。
- **有界**：`MAX_PENDING` 条上限、`TTL_SEC` 过期出队，防止对端永远不完成把表撑爆。
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

MAX_PENDING = 200
TTL_SEC = 24 * 3600          # 24 小时没结果的自动出队（留痕写明为什么）
POLL_GAP_SEC = 30            # 同一任务两次查询的最小间隔（别把对端打爆）


def _path() -> Path:
    from .env_spec import data_dir

    return Path(data_dir()) / "a2a_pending.json"


def _load() -> list:
    p = _path()
    if not p.is_file():
        return []
    try:
        d = json.loads(p.read_text(encoding="utf-8") or "[]")
        return d if isinstance(d, list) else []
    except Exception as e:                                   # noqa: BLE001 — 坏文件不拖垮主流程
        print("[a2a-poll] 读 pending 表失败（按空表处理）：%s: %s" % (type(e).__name__, e), flush=True)
        return []


def _save(items: list) -> None:
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")


def add(*, url: str, a2a_task_id: str, workshop_id: str, task_seq: int,
        harness_id: str = "", slot_dir: str = "") -> dict:
    """派活成功后登记一条待回写（幂等：同一对端任务号只留一条）。

    `slot_dir` 也记下来 —— 对端在回复里要求换会话时要按工位去标记（见 `a2a_context.request_rotate`）。
    """
    items = _load()
    for it in items:
        if it.get("a2a_task_id") == a2a_task_id and it.get("url") == url:
            return it
    rec = {"url": url, "a2a_task_id": a2a_task_id, "workshop_id": workshop_id,
           "task_seq": int(task_seq), "harness_id": harness_id, "slot_dir": slot_dir,
           "added_at": time.time(), "tries": 0, "last_at": 0.0, "last_error": ""}
    items.append(rec)
    if len(items) > MAX_PENDING:
        items = items[-MAX_PENDING:]                          # 先进先出，丢最老的
        print("[a2a-poll] pending 表超过 %d 条，丢掉最老的" % MAX_PENDING, flush=True)
    _save(items)
    return rec


def list_pending() -> list:
    return _load()


def parse_task(task: dict) -> dict:
    """把一条 A2A task 解析成 `{state, text, artifacts, terminal}`（**一处真相**）。

    ★2026-11-01：这段原来内联在 `sweep_once()` 里（派活回写账本用）。
    「跟组长说话要取回回复」也需要同一套解析 ⇒ 抽出来共用，别在两处各写一份
    （同一个错写两遍的教训已经吃过一次）。

    三个真机踩过的细节，都写在这儿：
      ① v1.0 的 `status` 是**对象**：`{"state": "TASK_STATE_WORKING", ...}`
         —— 直接 `str(status)` 会得到一整串字典文本，永远比不中终态；
      ② 枚举值带前缀：`TASK_STATE_COMPLETED` ⇒ 要归一化掉；
      ③ 结果文本优先级 = **artifacts（产物）→ status.message（附言）→ 顶层字段**，
         且一个 artifact 内部的 `parts` 是**逐词片段**，必须 `""` 直拼（用 `\n` 拼会碎句）。
    """
    task = task if isinstance(task, dict) else {}
    _raw_status = task.get("status")
    if isinstance(_raw_status, dict):
        state = str(_raw_status.get("state") or _raw_status.get("status") or "").strip().lower()
    else:
        state = str(_raw_status or task.get("state") or "").strip().lower()
    if state.startswith("task_state_"):
        state = state[len("task_state_"):]
    artifacts = task.get("artifacts") or task.get("outputs") or []
    if isinstance(artifacts, dict):
        artifacts = list(artifacts.values())
    text = ""
    if isinstance(artifacts, list):
        _t = []
        for a in artifacts:
            _parts = (a or {}).get("parts") or []
            _seg = "".join(str(p.get("text") or "") for p in _parts if isinstance(p, dict)).strip()
            if _seg:
                _t.append(_seg)
        text = "\n".join(_t).strip()
    if not text:
        _msg = (_raw_status or {}).get("message") if isinstance(_raw_status, dict) else None
        if isinstance(_msg, dict):
            _parts = _msg.get("parts") or []
            text = "".join(str((p or {}).get("text") or "") for p in _parts
                           if isinstance(p, dict)).strip()
    if not text:
        for k in ("result", "text", "summary"):
            if isinstance(task.get(k), str) and task.get(k).strip():
                text = task[k].strip()
                break
    terminal = state in ("completed", "complete", "done", "succeeded", "failed",
                         "canceled", "cancelled", "rejected", "error")
    return {"state": state, "text": text,
            "artifacts": artifacts if isinstance(artifacts, list) else [],
            "terminal": terminal,
            "task_id": str(task.get("id") or task.get("taskId") or "").strip(),
            "context_id": str(task.get("contextId") or "").strip()}


def sweep_once(*, now: float | None = None, gap: float | None = None) -> dict:
    """对每条待回写查一次 `GetTask`：终态就写进账本并出队。

    返回统计（`checked/done/failed/dropped`）—— 后台循环把它打进日志，便于看出"到底动没动"。
    """
    from .. import a2a_client as _c
    from . import task_ledger as _ledger

    now = time.time() if now is None else now
    gap = POLL_GAP_SEC if gap is None else gap
    items = _load()
    if not items:
        return {"checked": 0, "done": 0, "failed": 0, "dropped": 0}

    keep: list = []
    stats = {"checked": 0, "done": 0, "failed": 0, "dropped": 0}
    for it in items:
        # 过期出队（对端一直不完成）
        if now - float(it.get("added_at") or 0) > TTL_SEC:
            stats["dropped"] += 1
            print("[a2a-poll] 出队（超过 %ds 没结果）：%s / %s" %
                  (TTL_SEC, it.get("workshop_id"), it.get("a2a_task_id")), flush=True)
            continue
        # 同一条别查太勤
        if now - float(it.get("last_at") or 0) < gap:
            keep.append(it)
            continue

        it["last_at"] = now
        it["tries"] = int(it.get("tries") or 0) + 1
        stats["checked"] += 1
        try:
            res = _c.get_task(str(it.get("url")), str(it.get("a2a_task_id")), None, timeout=15)
        except Exception as e:                               # noqa: BLE001 — 查不到就下次再试
            it["last_error"] = "%s: %s" % (type(e).__name__, str(e)[:160])
            stats["failed"] += 1
            print("[a2a-poll] 查询失败（下次再试）：%s / %s -> %s" %
                  (it.get("workshop_id"), it.get("a2a_task_id"), it["last_error"]), flush=True)
            keep.append(it)
            continue

        task = res.get("task") if isinstance(res.get("task"), dict) else (res or {})
        # ★2026-11-01（真机跑出来的两个 bug，假对端测不出来）：
        #   ① A2A v1.0 的 `status` 是**对象**：`{"state": "TASK_STATE_WORKING", "timestamp": ...}`
        #      —— 原来直接 `str(status)` 会变成一整串字典文本，永远比不中终态（账本永远停在 issued）。
        #   ② v1.0 的枚举值带前缀：`TASK_STATE_COMPLETED` / `TASK_STATE_FAILED` …
        #      —— 要归一化掉前缀再比。
        _parsed = parse_task(task)
        state, text = _parsed["state"], _parsed["text"]
        artifacts, terminal = _parsed["artifacts"], _parsed["terminal"]
        if not terminal:
            keep.append(it)                                  # 还在干，留着
            continue

        rec = _ledger.note_external_result(
            str(it.get("workshop_id")), int(it.get("task_seq") or 0), source="a2a",
            external_task_id=str(it.get("a2a_task_id") or ""), external_state=state,
            result=text, artifacts=artifacts if isinstance(artifacts, list) else [])
        # ★2026-11-01（老板拍板：老化后换新会话）：对端在回复里带了 NEED_NEW_CONTEXT
        #   ⇒ 标记该工位"下次派活换新会话"。它只是**触发信号**，不进验收。
        if text and "[NEED_NEW_CONTEXT]" in text:
            try:
                from ..a2a_context import request_rotate as _ctx_rotate

                _ctx_rotate(str(it.get("harness_id") or ""), str(it.get("workshop_id") or ""),
                            str(it.get("slot_dir") or ""),
                            why="对端在回复里带了 [NEED_NEW_CONTEXT]")
                print("[a2a-poll] 对端要求换会话：%s / %s" %
                      (it.get("workshop_id"), it.get("a2a_task_id")), flush=True)
            except Exception as _e:                            # noqa: BLE001 — 标记失败不影响回写
                print("[a2a-poll] 标记换会话失败：%s: %s" % (type(_e).__name__, _e), flush=True)
        if rec is None:
            # 账本里没这条（工作间被删/任务号变了）⇒ 出队并说清，不无限重试
            stats["dropped"] += 1
            print("[a2a-poll] 出队（账本里没有这条任务）：%s#%s" %
                  (it.get("workshop_id"), it.get("task_seq")), flush=True)
            continue
        stats["done"] += 1
        print("[a2a-poll] 已回写：%s#%s（对端状态 %s）" %
              (it.get("workshop_id"), it.get("task_seq"), state), flush=True)

    _save(keep)
    return stats
