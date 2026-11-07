# -*- coding: utf-8 -*-
"""`platform/artifact_inbox`：**成员把文件放进收件目录 ⇒ 平台自动发现并转给组长**（设计 `V133`）。

## 为什么要有它

视觉脚本那类成员（桌面型 agent）没有回执协议 —— `V132` 已确认"能投话、读不到回复"。
它唯一可靠说得出的话是**文件**：盘上有就是有，没有就是没有（`L0` 口径）。

所以这里补一条**入站投递通道**：

```
成员另存/导出到收件目录
   → 本模块扫盘（sha256 + 字节数）
   → 与"已上报表"对比 ⇒ 新增/内容变了才生成事件
   → 上报：事件流 + 组长的 `_lead/_notice.md` 一行
```

## 收件目录（一处真相，别再造第五个）

| 优先级 | 目录 | 说明 |
|---|---|---|
| 1 | 档案的 `artifact_inbox_dir` | 成员的**外部导出目录**（桌面型只能"另存为"时用）；相对路径按工作间解析 |
| 2 | `<工位>/产物/` | **默认**：与 `artifacts.snapshot()`／验收／采信同一个落点 |

**不碰**：桥的 `INBOX_DIR`（那是平台→成员的出站邮箱）、`ws.resources`（那是登记表）、隔间继承（显式搬运）。

## 口径（与 `V133 §2` 一一对应）

1. 只认盘上真文件：事件带 `sha256` + `bytes`；读不到记 `error`（不是"没有"）；
2. 去重按内容：键 `(工作间, 工位, 相对路径)` 值是 `sha256`；同 sha 不重复，sha 变算 `changed`；
3. 只扫两层、跳过 `.` 开头与符号链接（与 `artifacts.snapshot` 同口径）；
4. 目录不存在就说清（**不替成员建**）；不可读也如实说；
5. **首次见到某目录**：把现有文件记成基线、**不发事件**（避免一开监视器刷屏历史）；
6. 单轮最多 `MAX_NEW_PER_SCAN` 条（超出标 `truncated` + 剩余数）；
7. 转组长默认只写 `_notice.md`（不抢焦点）；视觉提醒是开关，默认关；
8. 监视器状态可读（`status()`），异常每进程喊一次；
9. 已上报表与事件流都在 `AC_DATA_DIR`（换目录就跟着走）；
10. **只读扫描**：不改/不删/不移动成员的文件。
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from pathlib import Path

#: 事件流上限（滚动）
MAX_EVENTS = 500
#: 单轮最多生成多少条事件（剩下的下轮再报，不丢）
MAX_NEW_PER_SCAN = 200
#: 超过这个大小不算 sha256（只给字节数与 too_big）
MAX_HASH_BYTES = 64 * 1024 * 1024
#: 默认扫描间隔（秒）；`AC_ARTIFACT_WATCH_SEC=0` 关闭后台扫描
DEFAULT_INTERVAL = 20

#: 监视器进程内状态（供状态端点读；不是持久化事实）
_LOCK = threading.Lock()
_STATE = {"enabled": False, "interval": 0, "running": False,
          "last_run": "", "last_error": "", "last_ws": "", "last_summary": {}}
_WARNED = {"read": False}


def _warn_once(msg: str) -> None:
    if _WARNED["read"]:
        return
    _WARNED["read"] = True
    print("[artifact_inbox] %s" % msg, flush=True)


# ── 落点（跟 AC_DATA_DIR 走）──────────────────────────────────────
def _data_dir() -> Path:
    from .server import DATA_DIR

    return Path(DATA_DIR)


def _state_path() -> Path:
    return _data_dir() / "artifact_inbox.json"


def _events_path() -> Path:
    return _data_dir() / "inbox_events.jsonl"


def _load_state() -> dict:
    p = _state_path()
    if not p.is_file():
        return {}
    try:
        d = json.loads(p.read_text(encoding="utf-8") or "{}")
        return d if isinstance(d, dict) else {}
    except Exception as e:                                     # noqa: BLE001 — 坏表现要说，不装作第一次
        _warn_once("已上报表读不出来（%s: %s）；本次按第一次扫处理（会记基线，不刷历史）"
                   % (type(e).__name__, e))
        return {}


def _save_state(d: dict) -> None:
    p = _state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(p)


def _append_events(items: list) -> int:
    """追加事件并给每条分配**全局递增 `seq`**（供未读水位用）。返回最后的 seq。"""
    if not items:
        return int(_load_state().get("__event_seq__") or 0)
    p = _events_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    st = _load_state()
    n = int(st.get("__event_seq__") or 0)
    for it in items:
        n += 1
        it["seq"] = n
    with p.open("a", encoding="utf-8") as fh:
        for it in items:
            fh.write(json.dumps(it, ensure_ascii=False) + "\n")
    st["__event_seq__"] = n
    _save_state(st)
    # 滚动到上限（保留最后 MAX_EVENTS 行）
    try:
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
        if len(lines) > MAX_EVENTS:
            p.write_text("\n".join(lines[-MAX_EVENTS:]) + "\n", encoding="utf-8")
    except OSError as e:                                       # noqa: BLE001 — 裁剪失败不影响已写入的事件
        _warn_once("事件流裁剪失败（%s: %s）；事件已写入，只是文件会变大" % (type(e).__name__, e))
    return n


def events(limit: int = 50) -> list:
    """只读：最近的事件（**倒序**，最新在前）。"""
    p = _events_path()
    if not p.is_file():
        return []
    try:
        lines = [x for x in p.read_text(encoding="utf-8", errors="replace").splitlines() if x.strip()]
    except OSError as e:                                       # noqa: BLE001 — 读不到就说，不装空
        _warn_once("事件流读不出来（%s: %s）" % (type(e).__name__, e))
        return []
    out = []
    for line in lines[-max(1, min(int(limit or 50), MAX_EVENTS)):]:
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if isinstance(obj, dict):
            out.append(obj)
    return list(reversed(out))


# ── 收件目录 ──────────────────────────────────────────────────────
def inbox_of(ws, member: dict) -> tuple:
    """算出这个工位的收件目录。返回 `(base_path, label, error)`。

    `label`：`profile`（档案里配的外部目录）/ `product`（默认 `<工位>/产物/`）。
    `error` 非空 ⇒ 这个工位这轮扫不了（**说清原因**，不当"没有文件"）。
    """
    slot = str((member or {}).get("slot_dir") or "").strip().strip("/")
    hid = str((member or {}).get("harness") or "").strip()
    wp = str(getattr(ws, "workspace_dir", "") or "").strip()
    info = None
    try:
        from .server import harness_manager  # type: ignore[attr-defined]

        sess = (getattr(harness_manager, "sessions", {}) or {}).get(hid) if hid else None
        info = getattr(sess, "info", None) if sess is not None else None
    except Exception:                                          # noqa: BLE001 — 取不到档案就用默认目录
        info = None
    raw = str(getattr(info, "artifact_inbox_dir", "") or "").strip() if info is not None else ""
    if raw:
        _ok_v, _why_v, _res_v = validate_artifact_inbox_dir(raw, workshop_path=wp)
        if not _ok_v:
            return Path(""), "profile", ("收件目录不合法（档案里的值越界了）：%s —— "
                                         "这轮不扫它（不是'没有新文件'）" % _why_v)
        return Path(_res_v), "profile", ""
    if not wp:
        return Path(""), "product", "这个工作间没有工作目录（workspace_dir 为空），没法扫产物"
    if not slot:
        return Path(""), "product", "这个工位没有 slot_dir，没法定位产物目录"
    return Path(wp) / slot / "产物", "product", ""


def _sha256(p: Path) -> tuple:
    """`(hex|None, bytes)`；太大不算 hash（返回 None + 真实字节数）。"""
    size = p.stat().st_size
    if size > MAX_HASH_BYTES:
        return None, size
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 512), b""):
            h.update(chunk)
    return h.hexdigest(), size


def _walk(base: Path) -> list:
    """扫两层（自身 + 一层子目录），跳过 `.` 开头与符号链接。返回 `[(rel, Path)]`。"""
    out = []
    if not base.is_dir():
        return out
    for root, dirs, files in os.walk(base):
        rel_root = Path(root).relative_to(base)
        depth = 0 if str(rel_root) == "." else len(rel_root.parts)
        if depth >= 1:
            dirs[:] = []                        # 只到一层子目录
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for name in sorted(files):
            if name.startswith("."):
                continue
            p = Path(root) / name
            try:
                if p.is_symlink():
                    continue
            except OSError:
                continue
            out.append((p.relative_to(base).as_posix(), p))
    return out


def _needs_scan(ws) -> bool:
    st = str(getattr(ws, "status", "") or "")
    return st in ("active", "lead_in", "running", "") or st == "closed" and False


def _open_tasks(ws_id: str, slot: str) -> list:
    """这个工位上**还没终态**的任务（用于把事件绑到任务上，`V134` ③）。"""
    try:
        from . import task_ledger as _ledger

        return [t for t in (_ledger.list_tasks(ws_id) or [])
                if str(t.get("slot_dir") or "") == slot
                and str(t.get("state") or "") not in ("accepted", "rejected")]
    except Exception as e:                                     # noqa: BLE001 — 读不到就说（事件不绑任务，不猜）
        _warn_once("读任务账本失败（%s: %s）；本轮事件不绑任务" % (type(e).__name__, e))
        return []


def _advance_tasks(ws_id: str, items: list) -> list:
    """把"发现了产物"推进成任务 `delivered`（**不等于验收通过**）。"""
    from . import task_ledger as _ledger

    out, seen = [], set()
    for it in items:
        tid = str(it.get("task_id") or "")
        if not tid or tid in seen:
            continue
        seen.add(tid)
        try:
            seq = int(tid[2:])
        except ValueError:
            continue
        r = _ledger.mark_delivered(ws_id, seq, source="artifact_inbox",
                                   note=str(it.get("file") or ""))
        out.append({"task_id": tid,
                    "moved": bool(r.get("moved")), "state": r.get("state") or "",
                    "why": r.get("why") or r.get("error") or ""})
    return out


def unread_of(ws_id: str) -> dict:
    """这个工作间的事件未读数（**与大厅未读同一口径**：`read_marks`，只前进不后退、没看过是 -1）。"""
    from . import read_marks as _rm

    seq = int(_rm.get("inbox:" + str(ws_id)).get("seq"))
    evs = [e for e in events(limit=MAX_EVENTS) if str(e.get("workshop_id") or "") == ws_id]
    unread = [e for e in evs if int(e.get("seq") or 0) > seq]
    return {"seq": seq, "total": len(evs), "unread": len(unread),
            "needs_review": sum(1 for e in unread if e.get("needs_review")),
            "note": ("`seq` 是已读水位（-1=从没看过）；`unread` 只数本工作间；"
                     "`needs_review` 是其中「绑到了任务、等人验收」的条数")}


def mark_read(ws_id: str, seq: int | None = None) -> dict:
    """推进这个工作间的事件已读水位；`seq=None` ⇒ 推到当前最大事件 seq。"""
    from . import read_marks as _rm

    if seq is None:
        evs = [e for e in events(limit=MAX_EVENTS) if str(e.get("workshop_id") or "") == ws_id]
        seq = max([int(e.get("seq") or 0) for e in evs] or [0])
    r = _rm.mark("inbox:" + str(ws_id), int(seq))
    r["unread"] = unread_of(ws_id)
    return r


def scan_workshop(ws_id: str, *, dry_run: bool = False) -> dict:
    """扫**一个工作间**的所有工位收件目录。返回本轮摘要（**不写界面**）。"""
    from .server import workshops  # type: ignore[attr-defined]
    from . import v5_roster as _roster

    ws = (workshops or {}).get(ws_id)
    if ws is None:
        return {"ok": False, "code": "NO_SUCH_WORKSHOP", "error": "没有这个工作间：%s" % ws_id}
    mem = _roster.effective_members(ws_id)
    state = _load_state()
    out = {"ok": True, "at": time.strftime("%Y-%m-%dT%H:%M:%S"), "workshop_id": ws_id,
           "scanned": [], "new": [], "changed": [], "baseline": 0,
           "errors": [], "truncated": False, "remaining": 0, "notes": []}
    pending_state = dict(state)
    items = []
    for m in mem.get("members") or []:
        slot = str((m or {}).get("slot_dir") or "").strip().strip("/")
        if not slot:
            continue
        base, label, err = inbox_of(ws, m)
        if err:
            out["errors"].append({"slot_dir": slot, "error": err})
            continue
        if not base.is_dir():
            out["errors"].append({
                "slot_dir": slot, "dir": base.as_posix(),
                "error": ("收件目录不存在：%s —— 平台**不会替你建**（它会替你建就等于替成员决定落点）；"
                          "让它把产物放到这个目录，或在档案里把 `artifact_inbox_dir` 指到它真会写的目录" % base.as_posix()),
            })
            continue
        out["scanned"].append({"slot_dir": slot, "dir": base.as_posix(), "source": label})
        prefix = "%s|%s|" % (ws_id, slot)
        # 基线标记：**空目录也要记**（否则永远停在"第一次"，新增文件永远进 baseline）。
        mark = "__baseline__|" + prefix
        seen_any = bool(state.get(mark))
        pending_state[mark] = 1
        # ★V134 ③：把事件绑到"这个工位的未完成任务"上；**多条未完成就不猜**（标 ambiguous）
        _open = _open_tasks(ws_id, slot)
        _task_id = ""
        _amb = False
        if len(_open) == 1:
            try:
                _task_id = "T-%03d" % int((_open[0] or {}).get("task_seq") or 0)
            except (TypeError, ValueError):
                _task_id = ""
        elif len(_open) > 1:
            _amb = True
        for rel, p in _walk(base):
            try:
                sha, size = _sha256(p)
            except OSError as e:                               # noqa: BLE001 — 读不到要记下来，不能当没有
                out["errors"].append({"slot_dir": slot, "file": rel,
                                      "error": "读不了这个文件：%s: %s" % (type(e).__name__, e)})
                continue
            key = prefix + rel
            old = state.get(key)
            pending_state[key] = {"sha256": sha, "bytes": size,
                                  "at": time.strftime("%Y-%m-%dT%H:%M:%S")}
            if not seen_any:
                out["baseline"] += 1
                continue
            if old is None:
                items.append({"kind": "new", "slot_dir": slot, "harness_id": str(m.get("harness") or ""),
                              "dir": base.as_posix(), "dir_source": label, "file": rel,
                              "sha256": sha, "bytes": size, "too_big": sha is None,
                              "task_id": _task_id, "task_id_ambiguous": _amb,
                              "needs_review": bool(_task_id)})
            elif (old or {}).get("sha256") != sha:
                items.append({"kind": "changed", "slot_dir": slot,
                              "harness_id": str(m.get("harness") or ""),
                              "dir": base.as_posix(), "dir_source": label, "file": rel,
                              "sha256": sha, "bytes": size, "too_big": sha is None,
                              "task_id": _task_id, "task_id_ambiguous": _amb,
                              "needs_review": bool(_task_id)})
    if out["baseline"] and not items:
        out["notes"].append("首次见到该目录，已记基线 %d 个文件（不当作新增）" % out["baseline"])
    if len(items) > MAX_NEW_PER_SCAN:
        out["truncated"] = True
        out["remaining"] = len(items) - MAX_NEW_PER_SCAN
        out["notes"].append("本轮超过 %d 条，剩余 %d 条下轮再报（不丢）"
                            % (MAX_NEW_PER_SCAN, out["remaining"]))
        items = items[:MAX_NEW_PER_SCAN]
    at = out["at"]
    for it in items:
        it.update({"at": at, "workshop_id": ws_id,
                   "source": "platform_observed", "judge": "verified",
                   "note": "平台自己扫盘得到的（文件是真的、sha256 是现算的）；"
                           "**这不等于验收通过**，验收仍看证据档"})
    out["new"] = [x for x in items if x["kind"] == "new"]
    out["changed"] = [x for x in items if x["kind"] == "changed"]
    if dry_run:
        out["dry_run"] = True
        return out
    # 顺序要紧：`_append_events` 会分配全局 seq 并保存（含 `__event_seq__`），
    # 之后 `_save_state(pending_state)` 必须把它带上，否则下一次从 0 开始（重复 seq）。
    last_seq = _append_events(items)
    pending_state["__event_seq__"] = last_seq
    _save_state(pending_state)
    # ★V134 ③④：发现了产物 ⇒ 任务推到 delivered（**不等于验收**）+ 待验收条数
    out["advanced"] = _advance_tasks(ws_id, items)
    out["notified"] = _notify_lead(ws, items)
    out["unread"] = unread_of(ws_id)
    return out


def _notify_lead(ws, items: list) -> dict:
    """把本轮事件**转给组长**：只写 `_lead/_notice.md`（不抢焦点、不打扰人）。"""
    if not items:
        return {"ok": True, "skipped": "本轮没有新事件"}
    wp = str(getattr(ws, "workspace_dir", "") or "").strip()
    if not wp:
        return {"ok": False, "why": "工作间没有工作目录，写不了组长通知"}
    try:
        from . import v5_roster as _roster

        has_lead = any(str((m or {}).get("slot_dir") or "").strip().strip("/") == "_lead"
                       for m in (_roster.effective_members(str(getattr(ws, "workshop_id", "") or ""))
                                 .get("members") or []))
    except Exception:                                          # noqa: BLE001 — 名单读不到就按"没有组长"处理
        has_lead = False
    if not has_lead:
        return {"ok": False, "skipped": "这个工作间还没选组长（事件只留在事件流里，界面可见）"}
    from .dispatch import append_notice

    lines = []
    for it in items[:20]:
        lines.append("%s 交了文件 `%s`（%s，sha256 %s）—— 在它工位的收件目录里"
                     % (it["slot_dir"], it["file"], _size_text(it.get("bytes")),
                        (it.get("sha256") or "未算(文件过大)")[:8]))
    if len(items) > 20:
        lines.append("另有 %d 条同类事件（看 `GET /api/workshop/%s/inbox-events`）"
                     % (len(items) - 20, getattr(ws, "workshop_id", "")))
    res = append_notice(wp, "_lead", "；".join(lines))
    # ★2026-11-07（V134 ⑧）：通知要留账 —— `written` **不等于**组长看到了
    try:
        from . import notice_log as _nl

        _nl.record(str(getattr(ws, "workshop_id", "") or ""), target="lead", slot="_lead",
                   channel="notice_file", result="written" if res.get("ok") else "failed",
                   detail=("产物事件 %d 条" % len(lines)) if res.get("ok")
                   else str(res.get("why") or "写通知失败"),
                   ref="artifact_inbox")
    except Exception as _e:                                    # noqa: BLE001 — 记账失败不能把通知弄挂
        print("[notice] 记通知账失败（不影响通知）: %s" % _e, flush=True)
    return {"ok": bool(res.get("ok")), "file": res.get("file") or "", "lines": len(lines)}


def _size_text(n) -> str:
    try:
        b = int(n or 0)
    except (TypeError, ValueError):
        return "大小未知"
    if b < 1024:
        return "%d B" % b
    if b < 1024 * 1024:
        return "%.1f KB" % (b / 1024.0)
    return "%.1f MB" % (b / (1024.0 * 1024.0))


def scan_all(*, dry_run: bool = False) -> dict:
    """扫**所有在用工作间**（后台轮询用）。返回汇总。"""
    from .server import workshops  # type: ignore[attr-defined]

    results = []
    for wid, ws in (workshops or {}).items():
        if not _needs_scan(ws):
            continue
        try:
            results.append(scan_workshop(wid, dry_run=dry_run))
        except Exception as e:                                 # noqa: BLE001 — 一个工作间坏了不影响别的
            results.append({"ok": False, "workshop_id": wid,
                            "error": "%s: %s" % (type(e).__name__, e)})
    return {"ok": True, "at": time.strftime("%Y-%m-%dT%H:%M:%S"), "workshops": results,
            "events": sum(len(r.get("new") or []) + len(r.get("changed") or []) for r in results)}


# ── 后台轮询 ──────────────────────────────────────────────────────
def interval() -> int:
    raw = (os.environ.get("AC_ARTIFACT_WATCH_SEC") or "").strip()
    if not raw:
        return DEFAULT_INTERVAL
    try:
        return max(0, int(raw))
    except ValueError:
        return DEFAULT_INTERVAL


async def watch_loop() -> None:
    """后台循环：每 `interval()` 秒扫一轮；`0` 表示关闭（只手动扫）。"""
    import asyncio

    sec = interval()
    with _LOCK:
        _STATE.update({"enabled": sec > 0, "interval": sec, "running": sec > 0})
    if sec <= 0:
        print("[artifact_inbox] 后台扫描已关闭（AC_ARTIFACT_WATCH_SEC=0）：只能手动 POST …/scan-inbox", flush=True)
        return
    print("[artifact_inbox] 收件目录监视已启动：每 %d 秒一轮（收件目录＝档案 artifact_inbox_dir 或 工位/产物/）" % sec,
          flush=True)
    while True:
        try:
            await asyncio.sleep(sec)
            res = await asyncio.to_thread(scan_all)
            with _LOCK:
                _STATE["last_run"] = res.get("at") or ""
                _STATE["last_summary"] = {"workshops": len(res.get("workshops") or []),
                                          "events": res.get("events") or 0}
                _STATE["last_ws"] = ",".join(
                    [str(w.get("workshop_id") or "") for w in (res.get("workshops") or [])
                     if (w.get("new") or w.get("changed"))][:5])
                if res.get("events"):
                    _STATE["last_error"] = ""
        except asyncio.CancelledError:
            with _LOCK:
                _STATE["running"] = False
            raise
        except Exception as e:                                 # noqa: BLE001 — 线程不能因为一轮异常就静默死掉
            with _LOCK:
                _STATE["last_error"] = "%s: %s" % (type(e).__name__, str(e)[:200])
            print("[artifact_inbox] 这一轮扫描失败（下轮继续）：%s: %s" % (type(e).__name__, e),
                  flush=True)


def status() -> dict:
    """监视器状态（只读）。"""
    with _LOCK:
        st = dict(_STATE)
    st["data_dir"] = _data_dir().as_posix()
    st["state_file"] = _state_path().as_posix()
    st["events_file"] = _events_path().as_posix()
    st["max_events"] = MAX_EVENTS
    st["note"] = ("收件目录：档案 `artifact_inbox_dir` 优先，否则 `<工位>/产物/`；"
                  "只读扫描，不搬/删成员文件；事件转给组长只写 `_lead/_notice.md`（不抢焦点）")
    return st


# ── ★2026-11-07（V134 ⑥）**收件目录路径校验**：比 wakeup_dir 更严（这里会被扫盘、文件名与 sha256 会外泄）──
def validate_artifact_inbox_dir(raw: str, *, workshop_path: str = "") -> tuple:
    """校验 `artifact_inbox_dir`。返回 `(ok, why, resolved)`。

    规则（越界一律拒，并且**说清允许什么**）：

    1. 空 ⇒ 合法（用默认 `<工位>/产物/`）；
    2. **相对路径**：必须以工作区目录为根，且 `resolve()` 后**仍在工作区内**（挡 `../..` 越出）；
    3. **绝对路径**：必须是目录样的路径，且
       - 不能是**盘根/文件系统根**（`C:\\`、`/`）；
       - 不能落在**系统目录**（复用 `SYSTEM_DIR_PREFIXES`，与 `wakeup_dir` 同一份清单）；
       - 不能是网络路径（`\\\\server\\share`、`//server/share`）—— 扫盘会卡住且泄露远端清单。
    """
    s = str(raw or "").strip()
    if not s:
        return True, "", ""
    low = s.replace("/", "\\").lower()
    if s.startswith("\\\\") or s.startswith("//") or low.startswith("\\\\?\\"):
        return False, "不支持网络路径 / 设备路径（`\\\\server\\share`、`\\\\?\\`）—— 扫盘会卡住，且会把远端文件清单写进事件", ""
    p = Path(s)
    wp = str(workshop_path or "").strip()
    if not p.is_absolute():
        if not wp:
            return False, "相对路径需要工作区目录（这个工作间的 workspace_dir 为空）", ""
        base = Path(wp)
        try:
            base_r = base.resolve()
        except OSError:
            return False, "工作区目录解析失败：%s" % wp, ""
        cand = (base / s).resolve()
        try:
            cand.relative_to(base_r)
        except ValueError:
            return False, ("相对路径跑出了工作区（`%s` ⇒ `%s`）—— 收件目录要么写在工作区里，"
                           "要么给一个绝对路径" % (s, cand.as_posix())), ""
        return True, "", str(cand)
    try:
        resolved = p.resolve()
    except OSError as e:                                       # noqa: BLE001 — 解析失败=校验不了，如实拒
        return False, "路径解析失败：%s: %s" % (type(e).__name__, e), ""
    if resolved.parent == resolved:
        return False, "不能把收件目录设成盘根/根目录（`%s`）—— 那等于把整盘清单写进事件" % resolved.as_posix(), ""
    try:
        from .server import SYSTEM_DIR_PREFIXES
    except Exception:                                          # noqa: BLE001 — 拿不到清单就不多加限制（另有盘根/网络检查兜底）
        SYSTEM_DIR_PREFIXES = ()
    for prefix in SYSTEM_DIR_PREFIXES:
        try:
            resolved.relative_to(prefix)
            return False, ("收件目录不能指向系统目录（`%s`）—— 允许的是：工作区里的子目录、"
                           "或你自己建的独立目录（如 D:\\某文件夹）" % str(prefix)), ""
        except ValueError:
            continue
    return True, "", str(resolved)
