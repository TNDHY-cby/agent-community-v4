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
from . import evidence as _evidence                   # 采信态四值 + 逐文件复核（V130 §4，一处真相）

STATES = ("issued", "working", "delivered", "accepted", "rework", "rejected")
MIN_PASS = PASS_LEVELS                                # 通过的最低要求见 requires_level()

#: 跨组织采信态字段名（`V130` §4：`external.peer_accepted`）；值域 `evidence.ACCEPT_STATES`
ACCEPT_FIELD = "peer_accepted"


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
          member_seq: int = 0, title: str = "", wake: str = "",
          goal: str = "", accept: str = "", due: str = "",
          fingerprint: str = "") -> dict:
    """登记一条已派出的任务（P3 派活成功后调用；幂等：同一条重复登记只更新字段）。

    ★2026-11-09：也记 `goal` / `accept` / `due` —— 不然"拒收后重派"只能继承标题，
    新任务是个空壳（`redispatch_check` 的 ⑤ 格抓到的正是这个）。老记录没有这几项 ⇒ 读出来是空的。
    """
    d = _load()
    k = _key(workshop_id, task_seq)
    rec = d.get(k) or {"workshop_id": workshop_id, "task_seq": int(task_seq),
                       "slot_dir": slot_dir, "member_seq": member_seq, "file": file,
                       "title": title, "state": "issued", "created_at": _now(), "log": []}
    rec.update({"slot_dir": slot_dir, "file": file, "member_seq": member_seq,
                "title": title or rec.get("title", ""), "wakeup_result": wake or rec.get("wakeup_result", "")})
    if goal:
        rec["goal"] = goal
    if accept:
        rec["accept"] = accept
    if due:
        rec["due"] = due
    if fingerprint:                                            # ★A2：内容指纹（查重用，不含时间）
        rec["fingerprint"] = fingerprint
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


def mark_delivered(workshop_id: str, task_seq: int, *, source: str = "artifact_inbox",
                   note: str = "") -> dict:
    """**平台在收件目录发现了产物** ⇒ 把任务从 `issued/working` 推到 `delivered`（`V134` ③）。

    ★ 这**不是验收通过**：`delivered` 只表示"它有东西了"，真正的过闸仍要走 `review`（证据档）。
    终态（`accepted` / `rejected`）不动；已经是 `delivered` 的也不重复推。
    """
    d = _load()
    k = _key(workshop_id, task_seq)
    rec = d.get(k)
    if rec is None:
        return {"ok": False, "code": "NO_SUCH_TASK",
                "error": "账本里没有 T-%03d" % int(task_seq)}
    st = str(rec.get("state") or "")
    if st in ("accepted", "rejected"):
        return {"ok": True, "moved": False, "state": st,
                "why": "任务已经是终态（%s），不再改" % st}
    if st == "delivered":
        return {"ok": True, "moved": False, "state": st, "why": "已经是 delivered"}
    _advance(rec, "delivered", "平台在收件目录发现产物（%s）" % (note or source),
             delivered_by="platform", delivered_source=str(source or ""),
             delivered_at=_now())
    d[k] = rec
    _save(d)
    return {"ok": True, "moved": True, "state": "delivered", "task_seq": int(task_seq)}


def review(workshop_id: str, task_seq: int, *, result: str, evidence_level: str = "",
           note: str = "", artifacts: dict | None = None, who: str = "user",
           who_source: str = "user",
           evidence_ref: str = "", proof_exists: bool | None = None) -> dict:
    """验收：`result` ∈ 通过/返工/拒收。**通过必须有够格的证据**（`V121` / `V116` §15.1）。

    2026-10-31 老板拍板的两条（校验统一走 `v5_evidence_view.check_level`，不在这里重写一遍）：
      · `L1` 必须带 `evidence_ref`，没有就按 `L4` 算；
      · 产物目录真扫到 0 个文件时只许 `L4`（`L0/L1/L2` 一律拒）。

    2026-11-04（`V130` §4/§6）：
      · `proof_exists`：路由层对"像路径的凭据"真 `stat` 过 —— `False` ⇒ `L1_PROOF_MISSING`，按 `L4` 算；
      · **跨组织任务（账本里有 `external` 段）通过验收，必须采信态是 `accepted_peer`**：
        `accepted_self`（只有它自己说）/ `disputed` / `refuted` 一律拒 —— 自报永远换不到采信。
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
    chk = check_level(lvl, ref, artifacts_empty=arts_empty, artifacts_total=arts_total,
                      proof_exists=proof_exists)

    if not chk["ok"]:
        # 返工/拒收不要求证据——但**等级本身非法**（L3 / 不认识）照样拒，别让脏值落账
        if result == "通过" or chk["code"] in ("EVIDENCE_L3_REJECTED", "BAD_EVIDENCE"):
            return {"ok": False, "code": chk["code"], "error": chk["error"],
                    "next": chk["next"]}

    # ★V130 §6（2026-11-04 老板拍板）：**没有独立复核的跨组织结果，不算验收通过**
    ext = rec.get("external") or {}
    if result == "通过" and ext:
        acc = str(ext.get(ACCEPT_FIELD) or _evidence.ACCEPT_DEFAULT)
        if acc == "disputed":
            return {"ok": False, "code": "DISPUTE_OPEN",
                    "error": "这条跨组织交付正挂在争议里（%s）——冻结待裁，不能验收通过"
                             % (ext.get("dispute", {}).get("adjudicator") or "未指定裁决方"),
                    "next": "等裁决方给结论：证伪就走 POST …/dispute（outcome=refuted）；"
                            "对得上就重做复核 POST …/tasks/{seq}/peer-review 后再验收",
                    "peer_accepted": acc}
        if acc == "refuted":
            return {"ok": False, "code": "PEER_REFUTED",
                    "error": "这条跨组织交付已被证据证伪，不能验收通过",
                    "next": "退回返工或拒收；要重交得重新派活并把证据补齐",
                    "peer_accepted": acc}
        if acc != "accepted_peer":
            return {"ok": False, "code": "PEER_NOT_REVIEWED",
                    "error": "跨组织任务只拿到交付方自证（%s），接收方还没独立复核 —— "
                             "自报换不到采信" % _evidence.ACCEPT_MEANING.get(acc, acc),
                    "next": "先做本侧独立重算：POST /api/workshop/%s/tasks/%d/peer-review"
                            "（传对端给的 sha256 清单或 pt_id）；对不齐就挂争议"
                            % (workshop_id, int(task_seq)),
                    "peer_accepted": acc}

    if result == "通过":
        extra = {"evidence_level": lvl}
        if chk.get("evidence_ref"):
            extra.update({"evidence_ref": chk["evidence_ref"],
                          "evidence_ref_is_manual": True})
        _advance(rec, "accepted", "验收通过（%s）" % lvl, review_note=note,
                 reviewed_by=who, reviewed_by_source=who_source, reviewed_at=_now(), **extra)
    elif result == "返工":
        _advance(rec, "rework", "验收返工（%s）" % (lvl or "无证据等级"),
                 evidence_level=lvl, review_note=note, reviewed_by=who,
                 reviewed_by_source=who_source, reviewed_at=_now())
    else:
        _advance(rec, "rejected", "验收拒收（%s）" % (lvl or "无证据等级"),
                 evidence_level=lvl, review_note=note, reviewed_by=who,
                 reviewed_by_source=who_source, reviewed_at=_now())
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
    prev_ext = rec.get("external") or {}
    rec["external"] = {
        "source": source,                        # 例："a2a"
        "task_id": str(external_task_id or ""),  # 对端那边的任务号
        "state": str(external_state or ""),      # 对端报的状态（原样，不翻译成我们的验收态）
        "result": str(result or "")[:4000],      # 对端原话（截断要留痕）
        "result_truncated": len(str(result or "")) > 4000,
        "artifacts": list(artifacts or [])[:50],
        "at": _now(),
    }
    # ★V130：回报只是**交付方自证**，采信态默认 `accepted_self`；已有的复核/争议/证伪结论**不许被覆盖**
    #   （否则对方多报一次，就能把自己"复核没过"的记录洗掉）
    for f in (ACCEPT_FIELD, "peer_checked_at", "peer_check", "peer_diff",
              "dispute", "dispute_history", "passable"):
        if f in prev_ext:
            rec["external"][f] = prev_ext[f]
    rec["external"].setdefault(ACCEPT_FIELD, _evidence.ACCEPT_DEFAULT)
    if rec["external"].get("passable") is False:
        rec["passable"] = False
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


def record_peer_review(workshop_id: str, task_seq: int, *, peer_files: list,
                       local_files: dict, local_slot_dir: str = "",
                       checked_by: str = "", note: str = "") -> dict:
    """**接收方独立复核**（`V130` §4 规则二）：喂进对端的 `sha256` 清单，本侧重算后出差异表。

    `local_files` 由**路由层真扫盘**得到（`artifacts.snapshot()` 的 `files`），本函数只做纯比对与记账。
    · 全部一致 ⇒ 采信态 `accepted_peer`，写 `peer_checked_at`；
    · 没对齐（错 sha / 本侧没有 / 对端没给 hash）⇒ 采信态**停在 `accepted_self`** 并写 `peer_diff`；
    · 已挂争议/已证伪 ⇒ 复核不自动解除，如实写进结果（`why` 里说清）。

    返回 `{ok, code?, accept_state, can_pass, why, next, diff, peer_checked_at}`。
    """
    d = _load()
    k = _key(workshop_id, task_seq)
    rec = d.get(k)
    if rec is None:
        return {"ok": False, "code": "NO_SUCH_TASK",
                "error": "账本里没有 T-%03d" % int(task_seq),
                "next": "先派活（POST …/dispatch）；账本只记真实派出过的任务"}

    diff = _evidence.diff_peer_artifacts(peer_files, local_files,
                                         local_slot_dir=local_slot_dir)
    ext = rec.setdefault("external", {})
    disp = ext.get("dispute") or {}
    disputed = bool(disp) and str(disp.get("outcome") or "disputed") == "disputed"
    refuted = bool(disp) and str(disp.get("outcome") or "") == "refuted"
    acc = _evidence.accept_state(disputed=disputed, refuted=refuted,
                                 peer_checked=True, peer_ok=bool(diff["ok"]))

    at = _now()
    ext["peer_check"] = {"at": at, "ok": bool(diff["ok"]), "checked_by": checked_by or "user",
                         "note": str(note or "")[:500],
                         "matched": diff["matched"], "mismatched": diff["mismatched"],
                         "missing_local": diff["missing_local"], "no_hash": diff["no_hash"]}
    ext["peer_diff"] = {k2: diff[k2] for k2 in ("files", "extra_local", "matched", "mismatched",
                                                "missing_local", "no_hash", "why", "next")}
    ext[ACCEPT_FIELD] = acc["state"]
    if acc["state"] == "accepted_peer":
        ext["peer_checked_at"] = at
    rec.setdefault("log", []).append(
        {"at": at, "state": rec.get("state"),
         "why": "跨组织复核：%s（一致 %d / 不一致 %d / 本侧缺 %d）"
                % (acc["state"], diff["matched"], diff["mismatched"], diff["missing_local"])})
    d[k] = rec
    _save(d)
    return {"ok": bool(diff["ok"]), "task_seq": int(task_seq),
            "accept_state": acc["state"], "accept_meaning": acc["meaning"],
            "can_pass": acc["can_pass"], "peer_checked_at": ext.get("peer_checked_at", ""),
            "checked_at": at, "who_should_fix": "" if diff["ok"] else "交付方",
            "why": acc["why"] + ("；" + diff["why"] if diff["why"] else ""),
            "next": diff["next"] or acc["next"], "diff": diff,
            "link": "/api/workshop/%s/tasks/%d/peer-review" % (workshop_id, int(task_seq))}


def mark_dispute(workshop_id: str, task_seq: int, *, raised_by: str, claims: list,
                 adjudicator: str = "", why: str = "", outcome: str = "disputed") -> dict:
    """**挂争议 / 判证伪**（`V130` §4 规则三）：写 `dispute` 记录，并把这条**从可验收里摘出**。

    - `outcome="disputed"`：有争议未决 ⇒ 采信态 `disputed`，**冻结待裁**（数据不删）；
    - `outcome="refuted"`：已被证据证伪 ⇒ 采信态 `refuted`，可直接降级。
    `claims`：双方主张，`[{by, claim}]` 或字符串数组；平台**不判谁对**，只把主张原样留痕。
    """
    d = _load()
    k = _key(workshop_id, task_seq)
    rec = d.get(k)
    if rec is None:
        return {"ok": False, "code": "NO_SUCH_TASK",
                "error": "账本里没有 T-%03d" % int(task_seq),
                "next": "先派活（POST …/dispatch）；账本只记真实派出过的任务"}
    out = str(outcome or "disputed").strip().lower()
    if out not in ("disputed", "refuted"):
        return {"ok": False, "code": "BAD_OUTCOME",
                "error": "outcome 只能是 disputed（有争议未决）/ refuted（已被证据证伪）",
                "next": "对不齐但没裁决 ⇒ disputed；有证据证明它不成立 ⇒ refuted"}

    norm = []
    for c in (claims or [])[:20]:
        if isinstance(c, dict):
            by, claim = str(c.get("by") or "").strip(), str(c.get("claim") or "").strip()
        else:
            by, claim = "", str(c).strip()
        if not claim:
            continue
        norm.append({"by": by or "未署名", "claim": claim[:500]})
    if not norm:
        return {"ok": False, "code": "CLAIMS_REQUIRED",
                "error": "claims 不能为空 —— 争议记录必须留下双方各自说了什么",
                "next": "传 claims: [{by: '交付方', claim: '我交的是 v2'}, {by: '接收方', claim: 'sha256 对不上'}]"}

    at = _now()
    ext = rec.setdefault("external", {})
    prior = ext.get("dispute")
    rec_ext = {"at": at, "raised_by": str(raised_by or "user")[:120],
               "claims": norm, "adjudicator": str(adjudicator or "")[:120],
               "why": str(why or "")[:500], "outcome": out,
               "status": "open" if out == "disputed" else "closed"}
    ext["dispute"] = rec_ext
    ext[ACCEPT_FIELD] = _evidence.ACCEPT_STATES[2] if out == "disputed" else "refuted"
    ext["passable"] = False                     # ★从可验收里摘出（不是删数据）
    rec["passable"] = False
    if prior:
        ext.setdefault("dispute_history", []).append(prior)
    rec.setdefault("log", []).append(
        {"at": at, "state": rec.get("state"),
         "why": "挂%s：%s 发起；待裁=%s" % ("争议" if out == "disputed" else "证伪",
                                        rec_ext["raised_by"], rec_ext["adjudicator"] or "未指定")})
    d[k] = rec
    _save(d)
    acc = _evidence.accept_state(disputed=(out == "disputed"), refuted=(out == "refuted"))
    return {"ok": True, "task_seq": int(task_seq), "accept_state": acc["state"],
            "accept_meaning": acc["meaning"], "can_pass": False,
            "passable": False, "dispute": rec_ext,
            "why": "已从可验收里摘出（记录不删，冻结待裁）" if out == "disputed"
                   else "已标为证伪，可直接降级",
            "next": "裁决方给结论后：证伪则保持 refuted；对得上就重新派活/重交证据再复核"}


def peer_view(workshop_id: str, task_seq: int) -> dict:
    """**只读**跨组织复核视图（给界面/深链用）：采信态 + 差异 + 争议，不扫盘、不写盘。"""
    rec = get(workshop_id, task_seq)
    if rec is None:
        return {"ok": False, "code": "NO_SUCH_TASK",
                "error": "账本里没有 T-%03d" % int(task_seq),
                "next": "先派活（POST …/dispatch）"}
    ext = rec.get("external") or {}
    has_ext = bool(ext)
    acc = _evidence.accept_state(
        disputed=bool(ext.get("dispute")) and (ext.get("dispute") or {}).get("outcome", "disputed") == "disputed",
        refuted=(ext.get("dispute") or {}).get("outcome") == "refuted",
        peer_checked=bool(ext.get("peer_check")), peer_ok=bool((ext.get("peer_check") or {}).get("ok")))
    dv = dict(ext.get("dispute") or {})
    return {"ok": True, "task_seq": int(task_seq), "state": rec.get("state"),
            "external": bool(has_ext),
            "accept_state": (ext.get(ACCEPT_FIELD) or (acc["state"] if has_ext else "")),
            "accept_meaning": acc["meaning"] if has_ext else "不是跨组织任务，没有采信态",
            "can_pass": acc["can_pass"] and has_ext,
            "peer_checked_at": ext.get("peer_checked_at", ""),
            "peer_check": ext.get("peer_check") or {},
            "peer_diff": ext.get("peer_diff") or {},
            "dispute": dv if dv else None,
            "passable": bool(rec.get("passable", True)) and str(ext.get(ACCEPT_FIELD) or "") not in ("disputed", "refuted"),
            "why": acc["why"] if has_ext else "内部任务：验收只看证据档（V121 四步表）",
            "next": acc["next"] if has_ext else ""}


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

def link_redispatch(workshop_id: str, *, from_seq: int, to_seq: int) -> dict:
    """把"重派"关系写进**两边**：原任务 `redispatched_to`，新任务 `redispatched_from`。

    为什么两边都写：从原任务能追到"后来重派给谁"，从新任务能追到"这是重派的第几次"。
    只写一边，另一头就查不到（`V134` 反复强调的"别让同一件事有两套口径"）。
    """
    if not from_seq or not to_seq or int(from_seq) == int(to_seq):
        return {"ok": False, "why": "from/to 必须是非零且不同的任务号"}
    d = _load()
    k_from, k_to = _key(workshop_id, from_seq), _key(workshop_id, to_seq)
    rec_from, rec_to = d.get(k_from), d.get(k_to)
    if rec_from is None:
        return {"ok": False, "code": "NO_SUCH_TASK", "error": "账本里没有 T-%03d" % int(from_seq)}
    if rec_to is None:
        return {"ok": False, "code": "NO_SUCH_TASK", "error": "账本里没有 T-%03d" % int(to_seq)}
    chain = list(rec_from.get("redispatch_chain") or [])
    chain.append(int(to_seq))
    rec_from.update({"redispatched_to": int(to_seq), "redispatch_chain": chain,
                     "redispatched_at": _now()})
    rec_to.update({"redispatched_from": int(from_seq),
                   "redispatch_chain": list(rec_from.get("redispatch_chain") or [])})
    d[k_from], d[k_to] = rec_from, rec_to
    _save(d)
    return {"ok": True, "from": "T-%03d" % int(from_seq), "to": "T-%03d" % int(to_seq),
            "chain": ["T-%03d" % x for x in rec_from["redispatch_chain"]]}

def mark_redispatch_budget_exhausted(workshop_id: str, task_seq: int, *, max_times: int = 3) -> dict:
    """把一条任务标成**烂尾**（重派次数到顶）。`A6` 的命名终态，可统计、进待办。"""
    d = _load()
    k = _key(workshop_id, task_seq)
    rec = d.get(k)
    if rec is None:
        return {"ok": False, "code": "NO_SUCH_TASK", "error": "账本里没有 T-%03d" % int(task_seq)}
    rec["redispatch_budget_exhausted"] = True
    rec["redispatch_budget_max"] = int(max_times)
    rec.setdefault("log", []).append({"at": _now(), "state": rec.get("state") or "",
                                      "why": "重派次数到顶（%d）⇒ 标烂尾" % int(max_times)})
    d[k] = rec
    _save(d)
    return {"ok": True, "task_seq": int(task_seq), "max": int(max_times)}
