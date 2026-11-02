# -*- coding: utf-8 -*-
"""A2A **会话（contextId）轮换**：一个员工一个"当前会话"，老化后换新，旧的留痕。

## 老板 2026-11-01 拍板原话

> 一个员工可开多个会话，但是必须在**上下文老化之后**（上下文压缩三四次后）。

## 这件事的关键难点（必须先说清）

**平台看不到对端的"上下文压缩次数"** —— 那是对方客户端内部的事，A2A 协议也不暴露。

所以这里用**两条腿**，并在界面上如实标注用的是哪条：

1. **机械代理指标（主）**：平台自己数「这个 contextId 下已经派了几条活 / 累计多少字」，
   到阈值就换新会话。**这是平台自己算的，不依赖对端自报**（2026-10-25 的教训：
   对方自报一律不可信）。
   - 阈值可配：`max_tasks`（默认 20）/ `max_chars`（默认 60000）；
   - "压缩三四次 ≈ 多少条活"取决于对端的上下文窗口与任务体量，**默认值只是起点，要按实际调**。
2. **对端主动要求（辅）**：派活正文里请它在"觉得该换会话了"时回一句 `[NEED_NEW_CONTEXT]`。
   平台看到这个标记就**下次换新**。
   - ⚠️ 它是**触发信号**，不是证据：只能用来"提前换"，**不能**写进验收（没换也无所谓，
     机械阈值仍会兜住）。

## 语义（对齐 V28 通道生命周期）

- 一个 `(harness, 工作间, 工位)` 组合同时只有**一个 active 会话**（`context_id` + `seq`）；
- 换新时 `seq += 1`，旧的进 `retired[]`（带时间、用到多少、为什么换）——**只追加不删**；
- 不同员工/不同工作间**互不影响**（key 里带全）。

## 为什么不用"每次新开"

老板要的是**连续性**：同一个员工在同一个工位上接着干，别每派一条活就失忆。
换新只在"上下文老化"时发生 —— 既保住连续性，又不让它的上下文一直膨胀到不可用。
"""
from __future__ import annotations

import json
import time
from pathlib import Path

#: 机械阈值（可调）。"压缩三四次"折成多少条活取决于对端窗口与任务体量，这只是起点。
DEFAULT_MAX_TASKS = 20
DEFAULT_MAX_CHARS = 60000

#: 对端在回复里带这个标记 ⇒ 它自己觉得该换会话了
NEED_NEW_MARK = "[NEED_NEW_CONTEXT]"


def _path() -> Path:
    from .env_spec import data_dir

    return Path(data_dir()) / "a2a_contexts.json"


def _load() -> dict:
    p = _path()
    if not p.is_file():
        return {}
    try:
        d = json.loads(p.read_text(encoding="utf-8") or "{}")
        return d if isinstance(d, dict) else {}
    except Exception as e:                                   # noqa: BLE001 — 坏文件不拖垮派活
        print("[a2a-ctx] 读会话表失败（按空表处理）：%s: %s" % (type(e).__name__, e), flush=True)
        return {}


def _save(d: dict) -> None:
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")


def _key(harness_id: str, workshop_id: str, slot_dir: str) -> str:
    return "%s#%s#%s" % (harness_id or "", workshop_id or "", slot_dir or "")


def _new_ctx_id(harness_id: str, workshop_id: str, slot_dir: str, seq: int) -> str:
    """生成 contextId：**可读 + 唯一**（对端会回显它，排查时一眼能看出是谁的第几轮）。

    ⚠️ 只留 **ASCII 字母数字** —— contextId 会跨实现传输，中文/全角字符有些对端不接受。
    """
    def safe(s) -> str:
        return "".join(c if (c.isascii() and (c.isalnum() or c in "-_")) else "-"
                       for c in str(s or ""))[:24] or "x"

    return "ac-%s-%s-%s-%d-%d" % (safe(harness_id), safe(workshop_id),
                                  safe(str(slot_dir).replace("/", "-")), seq, int(time.time()) % 100000)


def current_for(harness_id: str, workshop_id: str, slot_dir: str, *,
                max_tasks: int = DEFAULT_MAX_TASKS,
                max_chars: int = DEFAULT_MAX_CHARS) -> dict:
    """取这个工位的**当前会话**；没有就建；老化（到阈值或对端要求过）就换新。

    返回 `{"context_id", "seq", "tasks", "chars", "rotated", "why"}`。
    `why` 非空表示"这次为什么换了新会话"（界面要显示，别让用户以为会话无故丢了）。
    """
    d = _load()
    k = _key(harness_id, workshop_id, slot_dir)
    rec = d.get(k)
    now = time.time()
    rotated, why = False, ""

    if rec is None:
        rec = {"harness_id": harness_id, "workshop_id": workshop_id, "slot_dir": slot_dir,
               "seq": 1, "context_id": _new_ctx_id(harness_id, workshop_id, slot_dir, 1),
               "tasks": 0, "chars": 0, "created_at": now, "last_used_at": 0.0,
               "rotate_requested": False, "retired": []}
        d[k] = rec
        _save(d)
        return {"context_id": rec["context_id"], "seq": rec["seq"], "tasks": 0, "chars": 0,
                "rotated": False, "why": ""}

    # ① 对端主动要求过（上次它的回复里带了 NEED_NEW_CONTEXT）
    if rec.get("rotate_requested"):
        rotated, why = True, "对端自己要求换会话（它回复里带了 %s）" % NEED_NEW_MARK
    # ② 机械阈值：活数超了
    elif int(rec.get("tasks") or 0) >= int(max_tasks):
        rotated, why = True, "这个会话已派过 %d 条活（阈值 %d）⇒ 按「老化换新」处理" % (
            int(rec.get("tasks") or 0), int(max_tasks))
    # ③ 机械阈值：累计字数超了
    elif int(rec.get("chars") or 0) >= int(max_chars):
        rotated, why = True, "这个会话累计 %d 字（阈值 %d）⇒ 上下文多半已经压缩过" % (
            int(rec.get("chars") or 0), int(max_chars))

    if rotated:
        rec.setdefault("retired", []).append({
            "seq": int(rec.get("seq") or 0),
            "context_id": rec.get("context_id") or "",
            "tasks": int(rec.get("tasks") or 0),
            "chars": int(rec.get("chars") or 0),
            "created_at": rec.get("created_at"),
            "retired_at": now,
            "why": why,
        })
        rec["seq"] = int(rec.get("seq") or 0) + 1
        rec["context_id"] = _new_ctx_id(harness_id, workshop_id, slot_dir, rec["seq"])
        rec["tasks"] = 0
        rec["chars"] = 0
        rec["created_at"] = now
        rec["rotate_requested"] = False
        print("[a2a-ctx] %s 换新会话 -> seq=%d（%s）" % (k, rec["seq"], why), flush=True)

    rec["last_used_at"] = now
    d[k] = rec
    _save(d)
    return {"context_id": rec["context_id"], "seq": int(rec.get("seq") or 1),
            "tasks": int(rec.get("tasks") or 0), "chars": int(rec.get("chars") or 0),
            "rotated": rotated, "why": why}


def note_used(harness_id: str, workshop_id: str, slot_dir: str, *, chars: int = 0) -> dict | None:
    """派出一条活后累加用量（平台自己数，不信对端）。"""
    d = _load()
    k = _key(harness_id, workshop_id, slot_dir)
    rec = d.get(k)
    if rec is None:
        return None
    rec["tasks"] = int(rec.get("tasks") or 0) + 1
    rec["chars"] = int(rec.get("chars") or 0) + max(0, int(chars or 0))
    rec["last_used_at"] = time.time()
    d[k] = rec
    _save(d)
    return rec


def request_rotate(harness_id: str, workshop_id: str, slot_dir: str, why: str = "") -> bool:
    """标记"下次派活换新会话"（来自对端标记，或用户手动点）。"""
    d = _load()
    k = _key(harness_id, workshop_id, slot_dir)
    rec = d.get(k)
    if rec is None:
        return False
    rec["rotate_requested"] = True
    if why:
        rec["rotate_why"] = why
    d[k] = rec
    _save(d)
    return True


def rotate_now(harness_id: str, workshop_id: str, slot_dir: str) -> dict:
    """**立刻**换新（界面上「换新会话」按钮）；旧会话照样留痕。"""
    d = _load()
    k = _key(harness_id, workshop_id, slot_dir)
    rec = d.get(k) or {}
    rec.setdefault("harness_id", harness_id)
    rec.setdefault("workshop_id", workshop_id)
    rec.setdefault("slot_dir", slot_dir)
    rec["rotate_requested"] = True
    d[k] = rec
    _save(d)
    return current_for(harness_id, workshop_id, slot_dir)


def view(harness_id: str, workshop_id: str, slot_dir: str) -> dict:
    """给界面看的：当前会话 + 已用多少 + 换过几次。"""
    d = _load()
    rec = d.get(_key(harness_id, workshop_id, slot_dir)) or {}
    retired = rec.get("retired") or []
    return {"context_id": rec.get("context_id") or "",
            "seq": int(rec.get("seq") or 0),
            "tasks": int(rec.get("tasks") or 0),
            "chars": int(rec.get("chars") or 0),
            "rotate_requested": bool(rec.get("rotate_requested")),
            "retired_count": len(retired),
            "retired": retired[-5:]}

# ## 附：**contextId 的隔离性**（2026-11-01 核实，源码 + 文件双重证据）

# 老板问过「A2A 多个会话会不会互相影响上下文」。**结论：我们这个实现是隔离的** ——
# 不是靠"相信协议"，是查了实际实现、也看了实际文件：

# 1. **插件的实现是 per-context 的**（`@hanphone/dsh-a2a` 的 `ContextSessionPool`）：
#    - `handles: Map<contextId, handle>` —— **每个 contextId 一个 live agent 实例**；
#    - `sessionIdFor(contextId, ns) = a2a-<ns>-<contextId>` —— **每个 contextId 一个独立 DSH 会话**；
#    - `tails: Map<contextId, Promise>` —— **同一 context 内串行**，不同 context 并发。
#    这正是"agent 工厂"模式（每上下文一个实例），而不是"共享一个实例"那类高发漏洞的写法。

# 2. **文件层面看得见**：`~/.dsh/sessions/--…a2a-sessions--/` 下**每个 contextId 一个目录、各一个
#    `session.v4.jsonl.zstd`**（实测 `ac-ctx-test-A` 与 `-B` 是两个独立文件，互不相干）。

# 3. **会话绑定还带实例命名空间**（`scopedContextId = <serverId>:<contextId>`）——
#    两个不同 A2A 实例即使用了同名 contextId，也**不共享** DSH 会话。

# ⚠️ **但这不等于可以随便命名**：`sessionIdFor` 是 `a2a-<ns>-<contextId>`，
# ⇒ **同一实例里两个不同的东西若用了同一个 contextId，它们会挤进同一个会话**。
# 所以 `_new_ctx_id()` 必须唯一 —— 现在带 `harness-工作间-工位-序号-时间后缀`，就是为这个。

# ⚠️ **跨站点/长期累积**是另一类隐式影响（一个上下文越用越"知道太多"）。本模块的**老化轮换**
# （默认 20 条活 / 6 万字换新）就是防它 —— 换新 contextId = 换一个全新会话。
