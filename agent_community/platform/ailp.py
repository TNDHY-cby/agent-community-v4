# -*- coding: utf-8 -*-
"""`platform/ailp`：**AILP 2.0 结构化消息**（设计 `V117` §7-5 / `V122` 补齐，P6 批）。

## 为什么讨论区不许是自由聊天

放开纯自然语言 ⇒ 上下文爆炸、谁说了啥无从追、`manifest` 也对不上。所以这里只收**强类型信封**：

```jsonc
{
  "ailp_version": "2.0",
  "msg_id": "msg-uuid-001",
  "msg_type": "PROPOSE | CHALLENGE | REFINE | CONTEXT_ECHO | CLARIFY | ACT | RECOMMEND",
  "sender": "A-01",
  "target": "B-02",
  "manifest_ref": "前端组/员工01/任务/T-001.md",
  "timestamp": "2026-10-27T10:00:00Z",
  "payload": { ... }
}
```

## 七类 payload（★ = 可选：可给可不给，给了就按类型校验）

| 类型 | 字段 | 谁产生 / 权威归属 |
|---|---|---|
| `PROPOSE` | `proposal`、`confidence`(0~1)、`basis_ids`(非空) | agent 提案 |
| `CHALLENGE` | `contradiction`、`evidence_ids`(非空)、★`counter_proposal` | agent 质疑 |
| `REFINE` | `target_id`、`modifications`、`new_confidence`、**`reason`(必填)** | agent 修订；**原消息不改**（置信度只追加） |
| `CONTEXT_ECHO` | `echo_target`、`shared_context` | agent 对齐背景 |
| `CLARIFY` | `question`、`to_ref` | 任何一方"我没听懂"的正当出口 |
| `ACT` | `task_ref`、`owner`、★`due` | **指向任务账本里的真实任务号**；任务本体与状态一律以 `task_ledger` 为准，讨论层不编号 |
| `RECOMMEND` | `target_id`、`reason`、`confidence` | agent 只能**推荐**；**不产生终态**（`DECIDED` 只能由人/组长给） |

## 三道硬校验（`V122` 批 1，2026-11-01 老板拍板）

1. **`manifest_ref` 必须在清单里**（`entry_points` / `active_tasks`），否则 `A006`；
2. **引用必须真存在**（`A013`/`A014`/`A016`）：`basis_ids` / `evidence_ids` / `target_id` / `to_ref` /
   `task_ref` 的每一项，只能是**账本内实体**——`msg_id`、`task_id`（`T-001` 或数字）、`manifest_ref`。
   外部证据（URL/文件/实验）要先登记成 `ev-xxxx`（见 `V122` §4.1 定义 1），不能直接当 id 用。
   ⚠️ **上下文为 `None` 时跳过该项校验**（只给没有上下文的单元测试/旧调用用；HTTP 端点一律传真实集合）。
3. **`REFINE` 必须带 `reason`**（`A015`）：置信度只通过**追加新消息**修正，原消息的 `confidence` 不动
   ⇒ append-only 与审计链不被破坏。
"""
from __future__ import annotations

import hashlib
import json
import re
import time
import uuid
from pathlib import Path

VERSION = "2.0"

#: ★2026-11-01（`V122` 批 1）：四类 → 七类。新增三类的**权威归属**写在各自注释里。
TYPES = ("PROPOSE", "CHALLENGE", "REFINE", "CONTEXT_ECHO", "CLARIFY", "ACT", "RECOMMEND")

REQUIRED_PAYLOAD = {
    "PROPOSE": (("proposal", str), ("confidence", (int, float)), ("basis_ids", list)),
    "CHALLENGE": (("contradiction", str), ("evidence_ids", list)),
    # ★REFINE 从 2026-11-01 起**必须带 reason**（`V122` §4.1 定义 3：置信度只追加不改，
    #   改一次要说清凭什么）。老客户端不补这个字段会被 `A015` 拒。
    "REFINE": (("target_id", str), ("modifications", str),
               ("new_confidence", (int, float)), ("reason", str)),
    "CONTEXT_ECHO": (("echo_target", str), ("shared_context", str)),
    "CLARIFY": (("question", str), ("to_ref", str)),
    "ACT": (("task_ref", str), ("owner", str)),
    "RECOMMEND": (("target_id", str), ("reason", str), ("confidence", (int, float))),
}

#: **可选**字段（`V117` §7-5 的字段表里就标着「可空」）。
#: 为什么要单独列：这些字段给了就要按类型校验，不给**不算缺**；
#: 而前端只有从 `types_catalog()` 才能知道"原来可以给这个"（否则它只能照必需字段渲染）。
OPTIONAL_PAYLOAD = {
    "CHALLENGE": (("counter_proposal", str),),
    "ACT": (("due", str),),
}

#: 这几个字段的值必须是**账本内已存在的实体**（`V122` §4.1 定义 1）。
_REF_FIELDS = {
    "REFINE": ("target_id",),
    "CLARIFY": ("to_ref",),
    "RECOMMEND": ("target_id",),
}
#: 数组型引用（每一项都要存在）。
_REF_LIST_FIELDS = ("basis_ids", "evidence_ids")


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


def _norm_set(items) -> set:
    out = set()
    for x in (items or []):
        s = str(x or "").strip().lstrip("./")
        if s:
            out.add(s)
    return out


def _known_ref(ref, *, msg_ids, task_ids, allowed) -> bool:
    """这个引用是不是**账本内真实存在**的实体（`msg_id` / `task_id` / `manifest_ref`）。"""
    r = str(ref or "").strip().lstrip("./")
    if not r:
        return False
    if msg_ids is not None and r in msg_ids:
        return True
    if task_ids is not None:
        if r in task_ids:
            return True
        # `T-001` 与任务序号 `1` 互认（任务账本里两种写法都有人用）
        m = re.match(r"^[Tt]-0*(\d+)$", r)
        if m and m.group(1) in task_ids:
            return True
        if r.isdigit() and ("T-%03d" % int(r)) in task_ids:
            return True
    if allowed is not None and r in _norm_set(allowed):
        return True
    return False


def validate(envelope, *, allowed: list | None = None, workshop_id: str = "",
             msg_ids=None, task_ids=None, members=None) -> tuple:
    """校验信封。返回 `(ok, errors, normalized)`。

    `allowed=None` / `msg_ids=None` / `task_ids=None` ⇒ **该项不校验**（只给没有上下文的调用用；
    HTTP 端点一律传真实集合，所以线上永远是强校验）。
    `members` 给了就校验 `ACT.owner` 是否在本工作间名单里。
    """
    msg_ids = _norm_set(msg_ids) if msg_ids is not None else None
    task_ids = _norm_set(task_ids) if task_ids is not None else None
    member_set = _norm_set(members) if members is not None else None

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
        if ref.lstrip("./") not in _norm_set(allowed):
            errs.append(_err("manifest_ref", "A006",
                             "`manifest_ref` 不在本工作间的 manifest 里：%s" % ref,
                             "可用这些：%s" % ("、".join(sorted(_norm_set(allowed))[:5])
                                              or "（manifest 里还没有条目）")))
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
            # 空字符串等于没给（`REFINE.reason` 尤其容易空着过）
            if typ is str and not str(val).strip():
                errs.append(_err("payload.%s" % name, "A008", "%s 的 `%s` 是空的" % (mtype, name),
                                 "写一句实质内容（空字符串不算给了）"))
                continue
            if name in ("basis_ids", "evidence_ids") and not val:
                errs.append(_err("payload.%s" % name, "A010",
                                 "`%s` 不能是空数组（说这话得有依据）" % name,
                                 "至少给一个依据 id（记忆文件 / 任务号 / manifest 条目）"))
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

    # ── 引用存在性（`V122` 批 1）：合法目标只限账本内实体 ──────────────
    have_ctx = (msg_ids is not None or task_ids is not None or allowed is not None)
    if have_ctx:
        for fname in _REF_FIELDS.get(mtype, ()):        # 单个引用（target_id / to_ref）
            val = payload.get(fname)
            if isinstance(val, str) and val.strip():
                if not _known_ref(val, msg_ids=msg_ids, task_ids=task_ids, allowed=allowed):
                    code = "A016" if fname == "task_ref" else "A013"
                    hint = ("可用消息：" + "、".join(sorted(msg_ids or [])[-5:])) if msg_ids \
                        else "先发一条再指它"
                    errs.append(_err("payload.%s" % fname, code,
                                     "`%s` 指向的东西不存在：%s" % (fname, val),
                                     "只能指向**本工作间账本里真实存在**的 id（msg_id / 任务号 / manifest 条目）。" + hint))
        for fname in _REF_LIST_FIELDS:                   # 数组引用（basis_ids / evidence_ids）
            for x in (payload.get(fname) or []):
                if not _known_ref(x, msg_ids=msg_ids, task_ids=task_ids, allowed=allowed):
                    errs.append(_err("payload.%s" % fname, "A014",
                                     "`%s` 里的依据不存在：%s" % (fname, x),
                                     "依据必须是账本内实体（msg_id / 任务号 / manifest 条目）；"
                                     "外部证据（URL/文件/实验）先用 POST …/evidence/register 登记成 ev-xxxx 再引用"))
                    break                                # 一个错就够，别刷屏

    # ── `ACT`：任务必须在任务账本里；owner 必须在名单里 ───────────────
    if mtype == "ACT" and have_ctx:
        tref = payload.get("task_ref")
        if isinstance(tref, str) and tref.strip() \
                and not _known_ref(tref, msg_ids=None, task_ids=task_ids, allowed=None if task_ids is not None else allowed):
            errs.append(_err("payload.task_ref", "A016",
                             "`task_ref` 在任务账本里不存在：%s" % tref,
                             "先调任务账本 API 拿到真实任务号再发 ACT（讨论层不许自己编号）"))
        owner = str(payload.get("owner") or "").strip()
        if member_set is not None and owner and owner not in member_set:
            errs.append(_err("payload.owner", "A017",
                             "`owner` 不在本工作间名单里：%s" % owner,
                             "写名单里的成员/组长名（GET …/roster 看有哪些）"))

    # ── ★2026-11-07（V138 §二 第一件）：「说给谁」与「回哪条」 ──────────────
    #   两个都**可选**（老消息照样读）：`to` = lead / all / member:<工位>；`thread` = 回哪条 msg_id。
    #   为什么放**信封级**而不是 `payload`：它们是"投递语义"，不是四类消息的内容，
    #   放进 payload 会污染 `/api/ailp/types` 的字段表（规则 20：一处真相）。
    to_whom = str(envelope.get("to") or "").strip()
    if to_whom:
        ok_to = (to_whom in ("lead", "all")
                 or (to_whom.startswith("member:") and len(to_whom) > len("member:"))
                 # ★2026-11-07：**整队通知** —— 说给一个小队（端点再校验它真存在）
                 or (to_whom.startswith("squad:") and len(to_whom) > len("squad:")))
        if not ok_to:
            errs.append(_err("to", "A018",
                             "`to` 值不对：%r" % envelope.get("to"),
                             "只能是 `lead` / `all` / `member:<工位目录>`（不给就是整个工作间）"))
    thread = str(envelope.get("thread") or "").strip()
    if thread and msg_ids is not None and thread not in msg_ids:
        errs.append(_err("thread", "A019",
                         "`thread` 指向的消息不存在：%s" % thread,
                         "先 GET …/ailp 拿真实 msg_id 再回它；不给 thread 就是新话题"))

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
    if to_whom:                                  # 不给就不写（老消息与老读法都不受影响）
        norm["to"] = to_whom
    if thread:
        norm["thread"] = thread
    # ★2026-11-09（修真 bug）：fingerprint **不含 received_at** —— 否则同一份内容
    #   晚一秒再发就换了指纹，"重发幂等"只在同一秒内成立（`dedup_keeps_single_row` 偶发红的根因）。
    #   ⚠️ `timestamp` 同样要排除：信封没带时间时它由服务端补 `_now()`，一秒后就变 ——
    #      第一次只排 `received_at` 仍然不幂等（跨秒用例当场抓出来的）。
    _fp_src = {k: v for k, v in norm.items()
               if k not in ("received_at", "timestamp", "fingerprint")}
    norm["fingerprint"] = _sha(json.dumps(_fp_src, ensure_ascii=False, sort_keys=True))
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
        "optional_envelope": [
            {"name": "to", "type": "str",
             "rule": "说给谁：`lead` / `all` / `member:<工位目录>` / `squad:<小队 id>`；不给＝整个工作间"},
            {"name": "thread", "type": "str",
             "rule": "回哪条：必须是本工作间真实存在的 msg_id（A019）；不给＝新话题"},
        ],
        "rules": ["自由聊天不收（只认这七类）",
                  "`manifest_ref` 必须在本工作间 manifest 的 entry_points 或 active_tasks 里",
                  "PROPOSE/CHALLENGE 的依据数组不许为空",
                  "`REFINE` 的 `target_id`、`CLARIFY` 的 `to_ref`、`RECOMMEND` 的 `target_id`"
                  "必须指向**真实存在**的 msg_id（A013）",
                  "`basis_ids` / `evidence_ids` 的每一项必须是账本内实体（msg_id / 任务号 / manifest 条目），"
                  "否则 A014；外部证据要先登记成 ev-xxxx",
                  "`REFINE` **必须带 `reason`**（置信度只追加、不原地改）",
                  "`ACT` 的 `task_ref` 必须是任务账本里的真实任务号（A016）；任务本体以任务账本为准",
                  "`ACT` 的 `owner` 必须在本工作间名单里（A017）",
                  "`RECOMMEND` 只是推荐，**不产生终态**（DECIDED 只能由人或组长给）",
                  "`optional_payload` 里的字段可给可不给；给了就按类型校验"]}
