# -*- coding: utf-8 -*-
"""`platform/receipt_store`：回执的**落盘与幂等**（设计 `V110` §4/§6，P1 批）。

三件事：
1. **原文必留**：收到的报文整段落盘（含被拒的）——事后要能回答"当时它到底写了什么"；
2. **序号与去重**：同 `(工作间, 成员, 序号)` 同内容 ⇒ 幂等 `duplicate`；内容不同 ⇒ 拒；序号回退 ⇒ 拒；
3. **流水可查**：`GET …/receipts` 读的就是这里。

落点：`AC_DATA_DIR/receipts/<workshop>/`（换数据目录跟着走，不进仓库）
    `raw/<seq>-<hash8>.rcpt`   原文
    `_state.json`               `{last_seq, seq_hash, counts}`
    `receipts.jsonl`            流水（每行一条：结果/类型/序号/来源/错误）
"""
from __future__ import annotations

import json
import time
from pathlib import Path


def _root() -> Path:
    from .server import DATA_DIR

    return Path(DATA_DIR) / "receipts"


def _safe(name: str) -> str:
    bad = '<>:"/\\|?*\n\r\t'
    out = "".join("_" if c in bad else c for c in str(name or "unknown")).strip() or "unknown"
    return out[:80]


def _ws_dir(workshop_id: str) -> Path:
    return _root() / _safe(workshop_id)


def _state_path(workshop_id: str) -> Path:
    return _ws_dir(workshop_id) / "_state.json"


def _load_state(workshop_id: str) -> dict:
    p = _state_path(workshop_id)
    if not p.is_file():
        return {"last_seq": 0, "seq_hash": {}, "counts": {"ok": 0, "rejected": 0, "duplicate": 0}}
    try:
        d = json.loads(p.read_text(encoding="utf-8") or "{}")
    except Exception as e:                                    # noqa: BLE001 — 读坏了要说，别装作没收到过
        print("[receipt] 状态读不出来（%s: %s）；本次按空处理，可能重复计数" % (type(e).__name__, e),
              flush=True)
        return {"last_seq": 0, "seq_hash": {}, "counts": {"ok": 0, "rejected": 0, "duplicate": 0}}
    d.setdefault("last_seq", 0)
    d.setdefault("seq_hash", {})
    d.setdefault("counts", {"ok": 0, "rejected": 0, "duplicate": 0})
    return d


def _save_state(workshop_id: str, st: dict) -> None:
    p = _state_path(workshop_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(p)


def save_raw(workshop_id: str, text: str, raw_hash: str, member: str = "") -> str:
    """原文落盘（被拒的也落）。返回相对路径（给流水里记）。"""
    d = _ws_dir(workshop_id) / "raw"
    d.mkdir(parents=True, exist_ok=True)
    name = "%s-%s.rcpt" % (_safe(member) or "x", raw_hash[:8])
    p = d / name
    if not p.is_file():
        p.write_text(text or "", encoding="utf-8")
    return "raw/" + name


def check_seq(workshop_id: str, member: str, seq: int, raw_hash: str) -> tuple:
    """返回 `(verdict, detail)`：`ok` / `duplicate` / `R006` / `R010`。"""
    st = _load_state(workshop_id)
    key = "%s#%d" % (_safe(member), int(seq))
    seen = st["seq_hash"].get(key)
    if seen == raw_hash:
        return "duplicate", {"seq": seq, "member": member,
                             "message": "这份回执已经收过了（序号 %d，内容一样）" % seq}
    if seen is not None:
        return "R010", {"seq": seq, "member": member,
                        "message": "序号 %d 之前用过，但内容不同" % seq,
                        "fix": "换一个新序号重发（下一个用 %d）" % (int(st.get("last_seq", 0)) + 1)}
    if int(seq) <= int(st.get("last_seq", 0)):
        return "R006", {"seq": seq, "member": member, "last_seq": int(st.get("last_seq", 0)),
                        "message": "序号回退了（上次收到 %d）" % int(st.get("last_seq", 0)),
                        "fix": "下一次用 `序号: %d`" % (int(st.get("last_seq", 0)) + 1)}
    return "ok", {}


def record(workshop_id: str, *, kind: str, source: str, member: str, seq: int | None,
           raw_hash: str, raw_rel: str, ok: bool, rtype: str = "", errors: list | None = None,
           rows: int = 0, note: str = "") -> dict:
    """记一条流水，并把序号状态推进（被拒的**不推进**序号，只记流水）。"""
    st = _load_state(workshop_id)
    if ok and seq is not None:
        st["seq_hash"]["%s#%d" % (_safe(member), int(seq))] = raw_hash
        st["last_seq"] = max(int(st.get("last_seq", 0)), int(seq))
    st["counts"]["ok" if ok and kind == "ok" else ("duplicate" if kind == "duplicate" else "rejected")] += 1
    _save_state(workshop_id, st)

    entry = {"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "kind": kind, "source": source,
             "member": member, "seq": seq, "type": rtype, "ok": bool(ok), "rows": rows,
             "raw": raw_rel, "raw_hash": raw_hash, "note": note,
             "errors": (errors or [])[:8]}
    p = _ws_dir(workshop_id) / "receipts.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return entry


def recent(workshop_id: str, limit: int = 20) -> list:
    p = _ws_dir(workshop_id) / "receipts.jsonl"
    if not p.is_file():
        return []
    out = []
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except ValueError:                                    # 坏行跳过，不影响别的行
            continue
    return list(reversed(out[-max(1, int(limit or 20)):]))


def counts(workshop_id: str) -> dict:
    return _load_state(workshop_id)["counts"]
