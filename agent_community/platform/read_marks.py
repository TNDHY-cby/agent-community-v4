# -*- coding: utf-8 -*-
"""`platform/read_marks`：**用户已读到哪个 seq**（大厅"未读回报"的后端水位）。

## 为什么要它（`FE-33` §5 第 1 条如实写的缺口）

前端第一版把水位放在 `localStorage['ac_read_<ws_id>']`：**换浏览器/换设备就不同步，清缓存就重置**。
所以这里给一个**跨设备**的水位：按工作间存一份，端到端只有这一个数 —— 读、写、算未读都在这儿。

## 口径（与 `FE-33` 一致，一处真相）

- **水位** = 用户最后看过的 `seq`（整数；没看过是 `-1`，不是 0 —— 因为 `seq` 从 0 开始）；
- **未读** = `discussion` 里 `seq > 水位` **且** `role != "user"` 的消息条数
  （用户自己在大厅发的任务消息不算"新回报"，把自己说的当成别人回报是荒谬的）；
- **只前进**：`mark()` 传进来的 seq 比现有小，就**不后退**，并在返回里如实说明（除非显式 `reset=True`）。

## 边界

- 落盘 `AC_DATA_DIR/read_marks.json`（换目录跟着走，不进仓库）；
- **单用户**口径（合作社是单机单用户的平台；不做多账号，免得凭空造一个用户体系）；
- 跨**设备**可以，跨**账号**不做 —— 这一点在文档里如实写，别让人以为是多租户。
"""
from __future__ import annotations

import json
import time
from pathlib import Path

#: 没看过时的水位（`seq` 从 0 开始，所以是 -1）
UNREAD_INITIAL = -1


def _dir() -> Path:
    from .env_spec import data_dir

    return Path(data_dir())


def _path() -> Path:
    return _dir() / "read_marks.json"


def _load() -> dict:
    p = _path()
    if not p.is_file():
        return {}
    try:
        d = json.loads(p.read_text(encoding="utf-8") or "{}")
        return d if isinstance(d, dict) else {}
    except Exception as e:                                     # noqa: BLE001 — 坏文件不拖垮大厅
        print("[read-marks] 读 %s 失败（按空处理）：%s: %s" % (p.name, type(e).__name__, e),
              flush=True)
        return {}


def _save(d: dict) -> None:
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(p)


def get(workshop_id: str) -> dict:
    """读一条水位记录（不改状态）。返回 `{seq, initialized, at}`。"""
    rec = _load().get(str(workshop_id or "")) or {}
    seq = rec.get("seq")
    try:
        seq = int(seq)
    except (TypeError, ValueError):
        seq = UNREAD_INITIAL
    return {"seq": seq, "initialized": bool(rec), "at": float(rec.get("at") or 0)}


def mark(workshop_id: str, seq: int | None = None, *, only_forward: bool = True) -> dict:
    """推进水位。

    `seq=None` ⇒ 由调用方给"当前最大 seq"（路由层算好传进来，本模块不依赖 workshop 对象）。
    `only_forward=True`（默认）时**不后退**；返回里 `moved` 说明到底动没动、`why` 说清原因。
    """
    items = _load()
    wid = str(workshop_id or "")
    rec = items.get(wid) or {}
    old = rec.get("seq")
    try:
        old = int(old)
    except (TypeError, ValueError):
        old = UNREAD_INITIAL
    if seq is None:
        return {"ok": False, "code": "NO_SEQ",
                "error": "没有给出 seq —— 要一个整数（用户看到的位置）",
                "next": "传 {seq: N}；或者不传 seq，让平台按当前最大 seq 推进"}
    try:
        want = int(seq)
    except (TypeError, ValueError):
        return {"ok": False, "code": "BAD_SEQ", "error": "seq 必须是整数，收到：%r" % (seq,)}
    if only_forward and want < old:
        return {"ok": True, "seq": old, "moved": False, "requested": want,
                "why": "只前进不后退：现在是 %d，你要设的 %d 更小（要看更早的请显式 reset）" % (old, want)}
    items[wid] = {"seq": want, "at": time.time()}
    _save(items)
    return {"ok": True, "seq": want, "moved": True, "requested": want, "previous": old,
            "why": "已推进到 %d" % want}


def reset(workshop_id: str) -> dict:
    """清掉这条（回到"没看过"）。给"我想重新看一遍提醒"用。"""
    items = _load()
    wid = str(workshop_id or "")
    had = items.pop(wid, None) is not None
    _save(items)
    return {"ok": True, "seq": UNREAD_INITIAL, "cleared": had}


def unread_of(discussion: list, seq: int) -> dict:
    """算未读（**与 `FE-33` 同一口径**）：`seq > 水位` 且 `role != "user"`。

    返回 `{unread, max_seq, non_user_total, first_unread_seq}`。
    """
    best = UNREAD_INITIAL
    n_unread = 0
    n_non_user = 0
    first_unread = None
    for m in (discussion or []):
        if not isinstance(m, dict):
            continue
        try:
            s = int(m.get("seq"))
        except (TypeError, ValueError):
            continue
        best = max(best, s)
        if str(m.get("role") or "") == "user":
            continue
        n_non_user += 1
        if s > int(seq):
            n_unread += 1
            if first_unread is None or s < first_unread:
                first_unread = s
    return {"unread": n_unread, "max_seq": best, "non_user_total": n_non_user,
            "first_unread_seq": first_unread}
