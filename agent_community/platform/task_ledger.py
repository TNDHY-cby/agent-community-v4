# -*- coding: utf-8 -*-
"""`platform/task_ledger`：**任务账本**（设计 `V113` 第 11 步 / `V115` V7~V10，P4 批）。

## 记什么

派活（P3）已经落了：任务文件、`_notice`、`manifest.active_tasks`、激活结果。
这一层只记账：**这条任务现在是什么状态、验收结论是什么、证据等级是什么**。

```
issued → working → delivered → accepted
                        └────→ rework ──→ working …
                        └────→ rejected
```

## 两条硬规矩（对方 2026-10-27 答案 3 + `V116` §15）

1. **`evidence_level` 必须 ≥ L1** 才算"验收通过"；`L3`（大模型自报）在解析回执时就被拒（`R011`）；
2. 结论写进账本 + `_meta/evidence/`，**不改成员产物**（验收不改别人的文件）。
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from .v5_evidence_view import LEVELS as PASS_LEVELS   # 一处真相：档位定义在 v5_evidence_view
from .v5_evidence_view import check_level             # 档位校验（含 L1 凭据、产物空只许 L4）

STATES = ("issued", "working", "delivered", "accepted", "rework", "rejected")
MIN_PASS = PASS_LEVELS                                # 通过的最低要求见 requires_level()


def _path() -> Path:
    from .env_spec import data_dir

    return Path(data_dir()) / "task_ledger.json"


def _load() -> dict:
    p = _path()
    if not p.is_file():
        return {}
    try:
        d = json.loads(p.read_text(encoding="utf-8") or "{}")
        return d if isinstance(d, dict) else {}
    except Exception as e:                                     # noqa: BLE001 — 读坏要说，不能当没账
        print("[ledger] 任务账本读不出来（%s: %s）；本次按空处理（会丢历史状态，请查 %s）"
              % (type(e).__name__, e, p), flush=True)
        return {}


def _save(d: dict) -> None:
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(p)


def _key(workshop_id: str, task_seq: int) -> str:
    return "%s#%d" % (workshop_id, int(task_seq))


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def issue(workshop_id: str, task_seq: int, *, slot_dir: str, file: str,
          member_seq: int = 0, title: str = "", wake: str = "") -> dict:
    """登记一条已派出的任务（P3 派活成功后调用；幂等：同一条重复登记只更新字段）。"""
    d = _load()
    k = _key(workshop_id, task_seq)
    rec = d.get(k) or {"workshop_id": workshop_id, "task_seq": int(task_seq),
                       "slot_dir": slot_dir, "member_seq": member_seq, "file": file,
                       "title": title, "state": "issued", "created_at": _now(), "log": []}
    rec.update({"slot_dir": slot_dir, "file": file, "member_seq": member_seq,
                "title": title or rec.get("title", ""), "wakeup_result": wake or rec.get("wakeup_result", "")})
    rec.setdefault("log", []).append({"at": _now(), "state": rec["state"], "why": "派活（P3）"})
    d[k] = rec
    _save(d)
    return rec


def get(workshop_id: str, task_seq: int) -> dict | None:
    return _load().get(_key(workshop_id, task_seq))


def list_tasks(workshop_id: str) -> list:
    return sorted([v for k, v in _load().items() if v.get("workshop_id") == workshop_id],
                  key=lambda r: int(r.get("task_seq") or 0))


def _advance(rec: dict, to: str, why: str, **extra) -> None:
    rec["state"] = to
    rec.update(extra)
    rec.setdefault("log", []).append({"at": _now(), "state": to, "why": why})


def review(workshop_id: str, task_seq: int, *, result: str, evidence_level: str = "",
           note: str = "", artifacts: dict | None = None, who: str = "lead",
           evidence_ref: str = "") -> dict:
    """验收：`result` ∈ 通过/返工/拒收。**通过必须有够格的证据**（`V121` / `V116` §15.1）。

    2026-10-31 老板拍板的两条（校验统一走 `v5_evidence_view.check_level`，不在这里重写一遍）：
      · `L1` 必须带 `evidence_ref`，没有就按 `L4` 算；
      · 产物目录真扫到 0 个文件时只许 `L4`（`L0/L1/L2` 一律拒）。
    """
    d = _load()
    k = _key(workshop_id, task_seq)
    rec = d.get(k)
    if rec is None:
        return {"ok": False, "code": "NO_SUCH_TASK",
                "error": "账本里没有 T-%03d" % task_seq,
                "next": "先派活（POST …/dispatch）或补登记；账本只记真实派出过的任务"}

    lvl = str(evidence_level or "").strip().upper()
    ref = str(evidence_ref or "").strip()
    # `artifacts is None` = 这次没扫 ⇒ 不知道盘上有没有（不当作"空"）
    arts_empty = None if artifacts is None else bool(artifacts.get("empty"))
    arts_total = None if artifacts is None else int(artifacts.get("total_files") or 0)
    chk = check_level(lvl, ref, artifacts_empty=arts_empty, artifacts_total=arts_total)

    if not chk["ok"]:
        # 返工/拒收不要求证据——但**等级本身非法**（L3 / 不认识）照样拒，别让脏值落账
        if result == "通过" or chk["code"] in ("EVIDENCE_L3_REJECTED", "BAD_EVIDENCE"):
            return {"ok": False, "code": chk["code"], "error": chk["error"],
                    "next": chk["next"]}

    if result == "通过":
        extra = {"evidence_level": lvl}
        if chk.get("evidence_ref"):
            extra.update({"evidence_ref": chk["evidence_ref"],
                          "evidence_ref_is_manual": True})
        _advance(rec, "accepted", "验收通过（%s）" % lvl, review_note=note,
                 reviewed_by=who, reviewed_at=_now(), **extra)
    elif result == "返工":
        _advance(rec, "rework", "验收返工（%s）" % (lvl or "无证据等级"),
                 evidence_level=lvl, review_note=note, reviewed_by=who, reviewed_at=_now())
    else:
        _advance(rec, "rejected", "验收拒收（%s）" % (lvl or "无证据等级"),
                 evidence_level=lvl, review_note=note, reviewed_by=who, reviewed_at=_now())
    if artifacts:
        rec["artifacts"] = artifacts
    d[k] = rec
    _save(d)
    out = {"ok": True, "task_seq": int(task_seq), "state": rec["state"],
           "evidence_level": lvl, "four_element_ok": True,
           "next": "" if rec["state"] == "accepted" else "按 review_note 改完再交一次验收"}
    if chk.get("label"):
        out["evidence_label"] = chk["label"]      # 例：「L1（人工凭据，平台未核对）」
    if chk.get("warn"):
        out["evidence_warn"] = chk["warn"]
    return out


def note_external_result(workshop_id: str, task_seq: int, *, source: str,
                         external_task_id: str = "", external_state: str = "",
                         result: str = "", artifacts: list | None = None) -> dict | None:
    """记一条**外部对端回报的结果** —— 它是「回执留痕」，**不是验收结论**。

    ★2026-11-01（清单 B2：A2A 派了活、账本里永远没结果）：
    为什么单开这个函数、**不复用 `review()`**：`review()` 是**人拍板的验收结论**
    （证据四步表的第 4 步）；对端自己说"我做完了"只算**第 3 步 / L2 回执留痕**。
    混在一起就等于把对方自报当通过 —— 那正是 2026-10-25 那次用真实成本换来的教训。
    所以这里只写 `external` 段并推进到 `delivered`，`state=accepted/rejected` 仍只能由人给。

    返回更新后的记录；任务不存在 ⇒ `None`（调用方据此报"账本里没这条"）。
    """
    d = _load()
    k = _key(workshop_id, task_seq)
    rec = d.get(k)
    if rec is None:
        return None
    rec["external"] = {
        "source": source,                        # 例："a2a"
        "task_id": str(external_task_id or ""),  # 对端那边的任务号
        "state": str(external_state or ""),      # 对端报的状态（原样，不翻译成我们的验收态）
        "result": str(result or "")[:4000],      # 对端原话（截断要留痕）
        "result_truncated": len(str(result or "")) > 4000,
        "artifacts": list(artifacts or [])[:50],
        "at": _now(),
    }
    if rec.get("state") == "issued":
        _advance(rec, "delivered", "外部对端回报（%s）" % source,
                 external_state=str(external_state or ""))
    else:
        rec.setdefault("log", []).append(
            {"at": _now(), "state": rec.get("state"),
             "why": "外部对端再次回报（%s）" % source})
    d[k] = rec
    _save(d)
    return rec


def integrate(workshop_id: str, *, workshop_path: str | Path, note: str = "") -> dict:
    """汇总：把账本 + 产物清单写成 `_meta/integrate.json`（**不搬任何产物文件**）。"""
    tasks = list_tasks(workshop_id)
    wp = Path(workshop_path)
    arts = {}
    p = wp / "_meta" / "artifacts.json"
    if p.is_file():
        try:
            arts = json.loads(p.read_text(encoding="utf-8") or "{}")
        except Exception as e:                                 # noqa: BLE001 — 坏清单要说
            print("[ledger] 产物清单读不出来（%s: %s）；integrate 里按空处理" % (type(e).__name__, e),
                  flush=True)
    accepted = [t for t in tasks if t.get("state") == "accepted"]
    open_tasks = [t for t in tasks if t.get("state") not in ("accepted", "rejected")]
    body = {"workshop_id": workshop_id, "at": _now(), "note": note,
            "total": len(tasks), "accepted": len(accepted), "open": len(open_tasks),
            "tasks": [{"task_id": "T-%03d" % int(t.get("task_seq") or 0),
                       "slot_dir": t.get("slot_dir"), "state": t.get("state"),
                       "evidence_level": t.get("evidence_level", ""),
                       "file": t.get("file"), "review_note": t.get("review_note", "")}
                      for t in tasks],
            "artifacts": {k: {"count": v.get("count"), "exists": v.get("exists")}
                          for k, v in (arts.get("slots") or {}).items()},
            "artifacts_diff": arts.get("diff", {}),
            "hint": "归档只写清单，**不动 产物/ 里的文件**；要打 zip 由用户自己来"}
    out_p = wp / "_meta" / "integrate.json"
    out_p.parent.mkdir(parents=True, exist_ok=True)
    out_p.write_text(json.dumps(body, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"ok": True, "written": out_p.as_posix(), "summary": body,
            "next": "确认无误后点「归档」（POST …/close）把工作间置 closed 并吊销 token"}
