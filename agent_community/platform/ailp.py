# -*- coding: utf-8 -*-
"""`platform/ailp`：**AILP 2.0 结构化消息**（设计 `V117` §7-5 / 架构师排雷③，P6 批）。

## 为什么讨论区不许是自由聊天

放开纯自然语言 ⇒ 上下文爆炸、谁说了啥无从追、`manifest` 也对不上。所以这里只收**强类型信封**：

```jsonc
{
  "ailp_version": "2.0",
  "msg_id": "msg-uuid-001",
  "msg_type": "PROPOSE | CHALLENGE | REFINE | CONTEXT_ECHO",
  "sender": "A-01",
  "target": "B-02",
  "manifest_ref": "前端组/员工01/任务/T-001.md",
  "timestamp": "2026-10-27T10:00:00Z",
  "payload": { ... }
}
```

四类 payload（**★ = 可选**：可给可不给，给了就按类型校验）：

| 类型 | 字段 |
|---|---|
| `PROPOSE` | `proposal`（做了什么/提什么）、`confidence`(0~1)、`basis_ids`(非空数组) |
| `CHALLENGE` | `contradiction`、`evidence_ids`(非空数组)、★`counter_proposal`（字符串，可空） |
| `REFINE` | `target_id`（被改的那条 msg_id）、`modifications`、`new_confidence`(0~1) |
| `CONTEXT_ECHO` | `echo_target`、`shared_context` |

**`manifest_ref` 强校验**：必须落在该工作间 `manifest.json` 的 `entry_points` 或 `active_tasks` 里，
否则 422 拒收（架构师要求）。这样每条发言都能指回一个真实文件 —— 不许空口说话。
"""
from __future__ import annotations

import hashlib
import json
import time
import uuid
from pathlib import Path

VERSION = "2.0"
TYPES = ("PROPOSE", "CHALLENGE", "REFINE", "CONTEXT_ECHO")

REQUIRED_PAYLOAD = {
    "PROPOSE": (("proposal", str), ("confidence", (int, float)), ("basis_ids", list)),
    "CHALLENGE": (("contradiction", str), ("evidence_ids", list)),
    "REFINE": (("target_id", str), ("modifications", str), ("new_confidence", (int, float))),
    "CONTEXT_ECHO": (("echo_target", str), ("shared_context", str)),
}

#: **可选**字段（`V117` §7-5 的字段表里就标着「可空」）。
#: 为什么要单独列：这些字段给了就要按类型校验，不给**不算缺**；
#: 而前端只有从 `types_catalog()` 才能知道"原来可以给这个"（否则它只能照必需字段渲染）。
OPTIONAL_PAYLOAD = {
    "CHALLENGE": (("counter_proposal", str),),
}


def _err(field: str, code: str, message: str, fix: str) -> dict:
    return {"field": field, "code": code, "message": message, "fix": fix}


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def allowed_refs(manifest_body: dict | None) -> list:
    b = manifest_body or {}
    return [str(x) for x in (b.get("entry_points") or [])] + \
           [str(x) for x in (b.get("active_tasks") or [])]


def validate(envelope, *, allowed: list | None = None, workshop_id: str = "") -> tuple:
    """校验信封。返回 `(ok, errors, normalized)`；`allowed=None` 表示不查 manifest_ref。"""
    errs = []
    if not isinstance(envelope, dict):
        return False, [_err("envelope", "A001", "消息不是 JSON 对象",
                            "按 AILP 2.0 信封写：version/msg_id/msg_type/sender/target/manifest_ref/payload")], None
    ver = str(envelope.get("ailp_version") or "").strip()
    if ver != VERSION:
        errs.append(_err("ailp_version", "A002", "版本不对（本机只认 %s，收到 %r）" % (VERSION, ver),
                         "把 `ailp_version` 写成 \"%s\"" % VERSION))
    mtype = str(envelope.get("msg_type") or "").strip().upper()
    if mtype not in TYPES:
        errs.append(_err("msg_type", "A003", "类型不认识：%r" % envelope.get("msg_type"),
                         "只能是 %s 之一（自由聊天不收）" % " / ".join(TYPES)))
    for f in ("sender", "target"):
        if not str(envelope.get(f) or "").strip():
            errs.append(_err(f, "A004", "缺 `%s`" % f, "写清是谁发的 / 发给谁（成员或组长名）"))
    ref = str(envelope.get("manifest_ref") or "").strip()
    if not ref:
        errs.append(_err("manifest_ref", "A005", "缺 `manifest_ref`（这条发言指回哪个文件）",
                         "填 `entry_points` 或 `active_tasks` 里的一个相对路径"))
    elif allowed is not None:
        norm_ref = ref.lstrip("./")
        pool = [str(a).lstrip("./") for a in allowed]
        if norm_ref not in pool:
            errs.append(_err("manifest_ref", "A006",
                             "`manifest_ref` 不在本工作间的 manifest 里：%s" % ref,
                             "可用这些：%s" % ("、".join(pool[:5]) or "（manifest 里还没有条目）")))
    payload = envelope.get("payload")
    if not isinstance(payload, dict):
        errs.append(_err("payload", "A007", "缺 `payload`（或不是对象）",
                         "按类型给字段，见 /api/ailp/types"))
        return False, errs, None

    if mtype in REQUIRED_PAYLOAD:
        for name, typ in REQUIRED_PAYLOAD[mtype]:
            if name not in payload:
                errs.append(_err("payload.%s" % name, "A008", "%s 缺 `%s`" % (mtype, name),
                                 "见 /api/ailp/types 的字段表"))
                continue
            val = payload.get(name)
            if not isinstance(val, typ) or (isinstance(val, bool) and typ is not bool):
                errs.append(_err("payload.%s" % name, "A009",
                                 "`%s` 类型不对（要 %s）" % (name, getattr(typ, "__name__", str(typ))),
                                 "按 /api/ailp/types 给正确类型"))
                continue
            if name in ("basis_ids", "evidence_ids") and not val:
                errs.append(_err("payload.%s" % name, "A010",
                                 "`%s` 不能是空数组（说这话得有依据）" % name,
                                 "至少给一个依据 id（记忆文件 / 任务号）"))
            if name in ("confidence", "new_confidence") and not (0.0 <= float(val) <= 1.0):
                errs.append(_err("payload.%s" % name, "A011", "`%s` 要在 0~1 之间" % name,
                                 "例：0.8"))
    # 可选字段：**给了才校验**（不给不算缺；给错类型要拒）
    for name, typ in OPTIONAL_PAYLOAD.get(mtype, ()):  # type: ignore[arg-type]
        if name in payload:
            val = payload.get(name)
            if not isinstance(val, typ) or (isinstance(val, bool) and typ is not bool):
                errs.append(_err("payload.%s" % name, "A012",
                                 "可选字段 `%s` 类型不对（要 %s）"
                                 % (name, getattr(typ, "__name__", str(typ))),
                                 "不给这个字段也行；要给就给正确的类型"))
    if errs:
        return False, errs, None

    norm = {"ailp_version": VERSION,
            "msg_id": str(envelope.get("msg_id") or ("msg-" + uuid.uuid4().hex[:12])),
            "msg_type": mtype,
            "sender": str(envelope.get("sender")).strip(),
            "target": str(envelope.get("target")).strip(),
            "manifest_ref": ref.lstrip("./"),
            "timestamp": str(envelope.get("timestamp") or _now()),
            "workshop_id": workshop_id,
            "payload": payload,
            "received_at": _now()}
    norm["fingerprint"] = _sha(json.dumps(norm, ensure_ascii=False, sort_keys=True))
    return True, [], norm


def types_catalog() -> dict:
    """给前端/agent 看的字段表（一处真相）。"""
    return {"ailp_version": VERSION, "types": [
        {"msg_type": t,
         "required_payload": [{"name": n, "type": getattr(ty, "__name__", str(ty))}
                              for n, ty in REQUIRED_PAYLOAD[t]],
         "optional_payload": [{"name": n, "type": getattr(ty, "__name__", str(ty))}
                              for n, ty in OPTIONAL_PAYLOAD.get(t, ())]}
        for t in TYPES],
        "envelope": ["ailp_version", "msg_id", "msg_type", "sender", "target",
                     "manifest_ref", "timestamp", "payload"],
        "rules": ["自由聊天不收（只认这四类）",
                  "`manifest_ref` 必须在本工作间 manifest 的 entry_points 或 active_tasks 里",
                  "PROPOSE/CHALLENGE 的依据数组不许为空",
                  "REFINE 要指向一条真实 msg_id（target_id）",
                  "`optional_payload` 里的字段可给可不给；给了就按类型校验"]}
