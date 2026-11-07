# -*- coding: utf-8 -*-
"""`platform/v5_roster`：**slot / member 双层成员模型**（设计 `V108` §6、`V112` §2/§3，P2 批）。

## 为什么必须拆两层

旧模型只有一个 `member.status`，把「还没叫进来」（正常）和「叫了没进来」（异常）塞进同一个字段
—— 这就是 V104⑤ 的根因：协议型成员永远被判"未接上"，开工闸 409 拦死。

- **slot（岗位行）**：组长产出的名单行，可以**有岗无人**：`proposed → approved → filled|dropped`
- **member（工位）**：真占了岗才有：`pending → acked → entered → active → blocked|lost`

**`slot.proposed` 不参与任何闸门**；只有 `member.*` 参与。

## 本轮（P2）落了什么

1. 主键 `(workshop_id, member_seq)`（H10）—— 同 harness 同 model 两个工位互不干扰；
2. `slot` 1:N（H11）—— 一个 `slot_dir` 要两人就 `headcount=2`，`member_seq` 递增，够数才算 `filled`；
3. 证据阶梯（`V112` §3）—— 按档位攒证据，**攒不齐就停在 `acked`**，不许假绿；
4. 旧数据映射（`V115` §3）—— 旧 `status` 映射进新双层，映不出来的标 `legacy_unknown`，**不静默丢**。

落点：`AC_DATA_DIR/v5_roster.json`（换数据目录跟着走；不进仓库）。
"""
from __future__ import annotations

import json
import time
from pathlib import Path

SLOT_STATES = ("proposed", "approved", "filled", "dropped")
MEMBER_STATES = ("pending", "acked", "entered", "active", "blocked", "lost")

#: 旧 `WorkshopMember.status` → 新 member 状态（**只映射，不猜**）
LEGACY_MAP = {
    "pending": ("pending", ""),
    "activating": ("acked", "旧的 activating 语义模糊，按 acked 记"),
    "entered": ("entered", ""),
    "idle": ("active", "旧的 idle（工具面已通）= active"),
    "working": ("active", ""),
    "blocked": ("blocked", ""),
    "offline": ("lost", ""),
}


def _path() -> Path:
    from .server import DATA_DIR

    return Path(DATA_DIR) / "v5_roster.json"


def _load() -> dict:
    p = _path()
    if not p.is_file():
        return {}
    try:
        d = json.loads(p.read_text(encoding="utf-8") or "{}")
    except Exception as e:                                    # noqa: BLE001 — 读坏要说，不能当"没有人"
        print("[v5_roster] 名单读不出来（%s: %s）；本次按空处理（会有重复建的风险，请查这个文件）"
              % (type(e).__name__, e), flush=True)
        return {}
    return d if isinstance(d, dict) else {}


def _save(d: dict) -> None:
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(p)


def _ws(d: dict, workshop_id: str) -> dict:
    return d.setdefault(workshop_id, {"slots": [], "members": [], "seq": 0, "legacy": []})


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


# ── 读 ──────────────────────────────────────────────────────────────
def write_mirror(workshop_id: str, workshop_path: str) -> dict:
    """把名单**镜像**到工作区 `_meta/roster.json`（**只读镜像**，真源仍是主库）。

    为什么要镜像：工作区是可交接的实体 —— 人拿到一个目录，就该看得见"这间有哪些工位、谁接上了"，
    而不是必须开平台查。**它永远不是真源**：平台改名单后重写它；读的时候仍以主库为准。

    失败不抛（不能因为镜像写不了就把名单操作搞挂），但**要说**：返回 `{ok:false, why}`，
    调用方把它放进响应里（失败要说话）。
    """
    wp = str(workshop_path or "").strip()
    if not wp:
        return {"ok": False, "why": "这个工作间没有工作区路径（没地方写镜像）"}
    try:
        base = Path(wp) / "_meta"
        base.mkdir(parents=True, exist_ok=True)
        snap = roster(workshop_id)
        body = {"schema": "coop.workshop.roster/1", "workshop_id": workshop_id,
                "updated_at": _now(), "counts": snap["counts"],
                "slots": snap["slots"], "members": snap["members"],
                "note": "这是**镜像**，真源在平台主库；别手工改它"}
        f = base / "roster.json"
        tmp = f.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(body, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(f)                      # 原子：半截文件不会被当成镜像
        return {"ok": True, "path": f.as_posix(), "members": len(snap["members"])}
    except OSError as e:
        return {"ok": False, "why": "写镜像失败（%s: %s）—— 名单主库没受影响" % (type(e).__name__, e)}


def roster(workshop_id: str) -> dict:
    """给接口用的整份名单（含 slot 与 member 两层 + 计数）。"""
    d = _ws(_load(), workshop_id)
    counts = {s: sum(1 for m in d["members"] if m.get("state") == s) for s in MEMBER_STATES}
    counts["slot_" + "proposed"] = sum(1 for s in d["slots"] if s.get("state") == "proposed")
    return {"workshop_id": workshop_id, "slots": d["slots"], "members": d["members"],
            "counts": counts, "legacy": d.get("legacy", []),
            "note": "slot=岗位行（可有岗无人）；member=工位（有人才有）。只有 member.* 参与闸门"}


def effective_members(workshop_id: str) -> dict:
    """**人数与"是否接上"的唯一口径**（v5 名单为主；旧 `WorkshopMember` 只在 v5 为空时兜底）。

    为什么需要它：v4 的 `workshop_gate` 只看 `ws.members`（旧模型），而 v5 名单在 `v5_roster.json`。
    两边并存期间，只读旧模型 ⇒ 明明有 `active` 工位，闸门仍报 `no_members`（**假状态**，2026-10-30 实测到）。

    返回：`{source, total, active, members:[{member_seq, slot_dir, state, harness, model}]}`。
    `source`：`v5`（v5 名单非空）/ `legacy`（v5 空但旧 members 有）/ `empty`。
    """
    snap = roster(workshop_id)
    members = [{"member_seq": m.get("member_seq"), "slot_dir": m.get("slot_dir"),
                "state": m.get("state"),
                # ★2026-11-07（V134 落地时抓到）：成员记录里字段叫 **`harness_id`**，
                #   这里原来只读 `harness` ⇒ 永远 None，于是"按工位找它的接入"（能力面、
                #   收件目录、派活路线）全都拿不到档案。两个名字都认，优先 `harness`。
                "harness": m.get("harness") or m.get("harness_id"),
                "harness_id": m.get("harness") or m.get("harness_id"),
                "model": m.get("model"),
                # 2026-10-31（V116 §8.0 口径二）：把"它现在实际叫什么"与核对结果带出来，
                # 界面才好如实标"这个名字是你给它的，平台没核过它真实叫什么"。
                "actual_name": m.get("actual_name") or "",
                "name_check": m.get("name_check") or {},
                # ★2026-10-31：**每项证据是谁验的**（`platform-observed` / `user-check`）——
                #   老板原话「一句『有人打了 3 个勾』在台上说不下去」，所以界面必须分开显示。
                "evidence_sources": dict((m.get("evidence") or {}).get("_sources") or {}),
                "evidence_at": {k: v for k, v in (m.get("evidence") or {}).items()
                                if k.endswith("_at")},
                } for m in snap["members"]]
    active = int(snap["counts"].get("active", 0) or 0)
    if members:
        return {"source": "v5", "total": len(members), "active": active, "members": members}
    return {"source": "empty", "total": 0, "active": 0, "members": []}


def find_member(workshop_id: str, member_seq: int) -> dict | None:
    for m in _ws(_load(), workshop_id)["members"]:
        if int(m.get("member_seq", -1)) == int(member_seq):
            return m
    return None


# ── A 档「平台自验」：一次性回话口令（`V116` §6b，2026-10-31） ──────────
# 为什么要有它（老板追问「你怎么知道它接上了？」）：原来 `protocol_ok` 也靠人勾/接口喂，
# 于是「随便发一次请求」或者「自己点个勾」都能记成接上。现在改成：
#   **平台把一次性口令写进那张对接单，只有真读到单子的会话才答得出来**；答对了平台自己盖时间戳。
# 诚实边界（写在这里，别在汇报里吹）：这防的是「没接上却记成接上」；
#   **防不了用户自己拿单子里的凭据 + 口令 curl 一次**（那等于用户伪造自己的账）。
#   要让"伪造也做不到"就得让平台去判断回应内容像不像真的 —— 那违反「平台零 AI」，所以不做。
_HANDSHAKE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"      # 去掉容易看错的 I/O/0/1


def _handshake_code() -> str:
    import secrets

    raw = "".join(secrets.choice(_HANDSHAKE_ALPHABET) for _ in range(8))
    return "%s-%s" % (raw[:4], raw[4:])


def _code_hash(code: str) -> str:
    import hashlib

    return hashlib.sha256(("handshake|" + str(code or "").strip().upper()).encode("utf-8")).hexdigest()


def issue_handshake(workshop_id: str, member_seq: int) -> dict:
    """给某个工位**现生成**一次性口令（明文只返回这一次，库里只存指纹）。"""
    d = _load()
    w = _ws(d, workshop_id)
    m = next((x for x in w["members"] if int(x.get("member_seq", -1)) == int(member_seq)), None)
    if m is None:
        return {"ok": False, "code": "NO_SUCH_MEMBER",
                "error": "没有这个工位：%s" % member_seq,
                "next": "先 GET /api/workshop/{id}/roster 看有哪些 member_seq"}
    code = _handshake_code()
    m["handshake_hash"] = _code_hash(code)
    m["handshake_at"] = _now()
    m.pop("handshake_used_at", None)          # 重新发 ⇒ 旧口令彻底作废
    _save(d)
    return {"ok": True, "member_seq": int(member_seq), "handshake": code,
            "note": "把这段口令写进这个工位的对接单；库里只存指纹，重发一次旧口令即失效"}


def verify_handshake(workshop_id: str, member_seq: int, code: str, *,
                     token_fingerprint: str = "") -> dict:
    """核对回话口令 ⇒ **平台自己**把 `protocol_ok` 记上（来源 `platform-observed` + 时间戳）。

    只认这一种"平台自己看到的"：口令对 + 一次性。重放 / 错口令 / 没发过口令，一律拒。
    """
    d = _load()
    w = _ws(d, workshop_id)
    m = next((x for x in w["members"] if int(x.get("member_seq", -1)) == int(member_seq)), None)
    if m is None:
        return {"ok": False, "code": "NO_SUCH_MEMBER", "error": "没有这个工位：%s" % member_seq,
                "next": "先 GET /api/workshop/{id}/roster 看有哪些 member_seq"}
    want = str(m.get("handshake_hash") or "")
    if not want:
        return {"ok": False, "code": "HANDSHAKE_NOT_ISSUED",
                "error": "这个工位还没发过回话口令（对接单可能没重新拼）",
                "next": "先 POST …/members/%d/kit 重拼一张对接单，口令在第 6b 节" % int(member_seq)}
    if str(m.get("handshake_used_at") or ""):
        return {"ok": False, "code": "HANDSHAKE_ALREADY_USED",
                "error": "这个口令已经用过了（一次性）—— 重放不算新的「接上」证据",
                "next": "要重新证明一次就重拼对接单，拿新口令"}
    if _code_hash(code) != want:
        return {"ok": False, "code": "HANDSHAKE_MISMATCH",
                "error": "口令对不上 —— 不是这张对接单上的那一个",
                "next": "照对接单第 6b 节原样复制（大小写不敏感，分隔符别改）"}

    at = _now()
    m["handshake_used_at"] = at
    m["tier"] = m.get("tier") or "a_protocol"
    # 覆盖范围由平台自己写（"它答出了口令"就是它能覆盖到的那一段）—— 别逼用户再补一句废话
    if not str(m.get("verified_scope") or "").strip():
        m["verified_scope"] = "平台观测：它按对接单第 6b 节口令回过话（%s）" % at
    _save(d)
    out = set_evidence(workshop_id, member_seq, {"protocol_ok": True, "tier": "a_protocol"},
                       who="platform", evidence_source="platform-observed")
    out["observed"] = {"by": "platform", "at": at, "how": "handshake",
                       "note": "不是人打的勾：平台把一次性口令写进对接单，它答对了才记这一笔"}
    return out


def record_bridge_probe(workshop_id: str, member_seq: int, *, probe_id: str,
                        picked_up: bool, answered: bool, inbox_rel: str = "") -> dict:
    """**B 档平台自验**：平台自己往邮箱写一条测试消息，之后亲眼看它有没有被取走。

    与 A 档的口令是同一类做法（`V116` §6.2）：不靠谁打勾，靠**平台自己做的两件事**——
    写文件、看文件还在不在。`picked_up`（进了 `delivered/` 或在 replies 里有痕迹）
    ⇒ `bridge_ok`；`answered`（真回了话）⇒ 再加 `mailbox_ok`。两项来源都记 `platform-observed`。

    ⚠️ 路径**不由平台猜**（`bridge_selfcheck` 的既定原则：不写死任何路径）：
    `inbox_rel` 只是记账用的相对路径，真实扫描在调用方（端点）里做。
    """
    d = _load()
    w = _ws(d, workshop_id)
    m = next((x for x in w["members"] if int(x.get("member_seq", -1)) == int(member_seq)), None)
    if m is None:
        return {"ok": False, "code": "NO_SUCH_MEMBER", "error": "没有这个工位：%s" % member_seq,
                "next": "先 GET /api/workshop/{id}/roster 看有哪些 member_seq"}
    if not picked_up:
        # 没被取走 ⇒ **一个字都不许记进证据**（不然就成了"探了一次就算接上"）
        return {"ok": False, "code": "PROBE_NOT_PICKED",
                "error": "平台写进邮箱的那条测试消息**还躺在那里没人取**",
                "next": ("先把这只桥跑起来（它按自己的 inbox 扫 `task_*.json`，取走后移到 `delivered/`），"
                         "再点一次「检查取件」")}

    at = _now()
    m.setdefault("evidence", {})["_probe"] = {
        "probe_id": probe_id, "at": at, "picked_up": True, "answered": bool(answered),
        "inbox": inbox_rel,
    }
    if not str(m.get("verified_scope") or "").strip():
        m["verified_scope"] = ("平台观测：它取走了平台写进邮箱的测试消息（%s）%s"
                               % (probe_id, "，并回了话" if answered else ""))
    if not str(m.get("tier") or "").strip():
        m["tier"] = "b_bridge"
    _save(d)

    patch = {"bridge_ok": True}
    if answered:
        patch["mailbox_ok"] = True
    out = set_evidence(workshop_id, member_seq, patch, who="platform",
                       evidence_source="platform-observed")
    out["observed"] = {"by": "platform", "at": at, "how": "bridge_probe",
                       "probe_id": probe_id, "picked_up": True, "answered": bool(answered),
                       "note": ("不是人打的勾：这条测试消息是平台自己写进邮箱的，"
                                "文件进了 `delivered/` 才记这一笔")}
    return out


# ── 读：名单与实到的**一句话摘要**（`V116` §8.0 口径一，`一处真相`）──#: 已经接上、能派活的唯一状态
_LIVE = ("active",)
#: 差一点（接近但没到 active）—— 报给用户时要与"完全没动"分开说
_NEAR = ("acked", "entered")
#: 需要人去看一眼的（不是"还没开始"，是"出问题了"）
_ALARM = ("blocked", "lost")


def roster_summary(workshop_id: str) -> dict:
    """**名单 X 人 / 已接上 Y / 还差 Z（哪几个空工位）** —— 一句人话 + 结构化字段。

    为什么要它（`V116` §8.0）：模型早就写了「接 1 人 ⇒ 进 running 并**明写还有 N 人未接上**」，
    但实现里只给了 `counts` 数字，用户得自己数。这里补上那句话，**`GET …/roster` 与
    `GET /api/workshop/{id}` 共用同一份**（规则 20：一处真相，别在后端各拼一套）。
    """
    snap = roster(workshop_id)
    members = snap["members"]
    total = len(members)
    by_state = {s: [] for s in MEMBER_STATES}
    for m in members:
        by_state.setdefault(str(m.get("state") or "pending"), []).append(m)
    active = by_state.get("active", [])
    near = [m for s in _NEAR for m in by_state.get(s, [])]
    alarm = [m for s in _ALARM for m in by_state.get(s, [])]
    rest = [m for m in members
            if m.get("state") not in _LIVE + _NEAR + _ALARM]

    def _label(m: dict) -> str:
        return "%s#%s" % (m.get("slot_dir") or "?", m.get("member_seq"))

    missing = near + rest + alarm          # 不在 active 的，一律"还不能派活"
    missing_labels = [_label(m) for m in missing]

    if total == 0:
        sentence = "名单上还没有人（让组长交回名单回执，或在界面手动加人）"
    elif not missing:
        sentence = "名单 %d 人，全部接上了" % total
    elif not active:
        sentence = ("名单 %d 人，**一个都还没接上**（这 %d 个工位现在都不能派活）：%s"
                    % (total, total, "、".join(missing_labels)))
    else:
        sentence = ("名单 %d 人，已接上 %d 人，还差 %d 个（%s）—— 空工位不能派活"
                    % (total, len(active), len(missing), "、".join(missing_labels)))
    detail = []
    if near:
        detail.append("%d 个差最后一步（已收到/已进入，还没到「接上了」）：%s"
                      % (len(near), "、".join(_label(m) for m in near)))
    if alarm:
        detail.append("%d 个出问题了（受阻/失联）：%s"
                      % (len(alarm), "、".join(_label(m) for m in alarm)))
    return {"total": total, "active": len(active), "missing": len(missing),
            "missing_labels": missing_labels,
            "near": len(near), "alarm": len(alarm),
            "all_active": total > 0 and not missing,
            "can_dispatch_anyone": bool(active),
            "sentence": sentence,
            "detail": detail}


# ── 写：名单落库（由回执 `类型: 名单` 触发）──────────────────────────
def add_slots(workshop_id: str, rows: list, *, source: str = "C") -> dict:
    """把名单行落成 slot（**幂等**：同一 `slot_dir` 已存在就更新人数，不重复建）。"""
    d = _load()
    w = _ws(d, workshop_id)
    created, updated, members_added = [], [], []
    for row in rows:
        slot_dir = str(row.get("岗位") or row.get("slot_dir") or "").strip()
        if not slot_dir:
            continue
        head = int(row.get("人数") or row.get("headcount") or 1)
        harness_id = str(row.get("harness") or row.get("harness_id") or "").strip()
        model = str(row.get("model") or "").strip()
        slot = next((s for s in w["slots"] if s["slot_dir"] == slot_dir), None)
        if slot is None:
            slot = {"slot_dir": slot_dir, "harness_id": harness_id, "model": model,
                    "headcount": head, "state": "proposed", "source": source,
                    "seqs": [], "created_at": _now()}
            w["slots"].append(slot)
            created.append(slot_dir)
        else:
            slot["headcount"] = head
            if harness_id:
                slot["harness_id"] = harness_id
            if model:
                slot["model"] = model
            updated.append(slot_dir)
        # 1:N：够 headcount 个工位才算 filled；缺几个就建几个 pending member
        have = [m for m in w["members"] if m.get("slot_dir") == slot_dir]
        while len(have) < head:
            w["seq"] = int(w.get("seq", 0)) + 1
            seq = w["seq"]
            label = "%s-%s-%02d" % (workshop_id, slot_dir.replace("/", "-"), seq)
            m = {"member_seq": seq, "slot_dir": slot_dir, "harness_id": harness_id,
                 "model_snapshot": model, "session_label": label, "state": "pending",
                 "tier": "", "evidence": {}, "verified_scope": "",
                 "activated_at": "", "wakeup_result": "", "created_at": _now(),
                 "source": source, "state_log": [{"at": _now(), "to": "pending", "why": "名单落库"}]}
            w["members"].append(m)
            members_added.append(seq)
            have.append(m)
        slot["state"] = "filled" if len(have) >= head else "approved"
        slot["seqs"] = [m["member_seq"] for m in have]
    _save(d)
    return {"ok": True, "slots_created": created, "slots_updated": updated,
            "members_created": members_added, "roster": roster(workshop_id)}


def _set_state(m: dict, to: str, why: str) -> bool:
    if m.get("state") == to:
        return False
    m["state"] = to
    m.setdefault("state_log", []).append({"at": _now(), "to": to, "why": why})
    return True


# ── 写：证据 → 状态（`V112` §3 的阶梯）─────────────────────────────
def set_evidence(workshop_id: str, member_seq: int, patch: dict, *, who: str = "user",
                 tier: str = "", evidence_source: str = "user-check") -> dict:
    """攒证据并推进状态。**攒不齐就停在 acked**，并说清还缺哪项。

    `evidence_source`（2026-10-31 老板追问后加的）：**每一项证据都记"是谁验的"**——
      · `platform-observed`：平台自己看到的（答出一次性口令 / 看见桥取走文件）**并盖时间戳**；
      · `user-check`：用户在界面上打的勾（**界面上必须与上面那种分开显示**）。
    为什么必须分：老板原话「一句『有人打了 3 个勾』在台上说不下去」。分不清来源，账就是假的。
    """
    d = _load()
    w = _ws(d, workshop_id)
    m = next((x for x in w["members"] if int(x.get("member_seq", -1)) == int(member_seq)), None)
    if m is None:
        return {"ok": False, "code": "NO_SUCH_MEMBER",
                "error": "没有这个工位：%s" % member_seq,
                "next": "先 GET /api/workshop/{id}/roster 看有哪些 member_seq"}
    ev = m.setdefault("evidence", {})
    srcs = ev.setdefault("_sources", {})
    src = str(evidence_source or "user-check")
    for k in ("protocol_ok", "bridge_ok", "mailbox_ok", "paste_seen", "press_ok", "rename_seen"):
        if k in patch:
            ev[k] = bool(patch[k])
            if patch[k]:
                srcs[k] = src                    # 谁验的
                ev[k + "_at"] = _now()           # 什么时候验的（平台观测的）
    if patch.get("verified_scope"):
        m["verified_scope"] = str(patch["verified_scope"])[:200]
    # ★2026-10-31（`V116` §8.0 口径二）：**可选**填「它现在实际叫什么」——填了就与身份名比，
    #   不一致只**提醒**不拦。为什么这样：平台看不到对方应用里的会话标题，拦死就成了
    #   "改个名就派不出活"；不提醒又会让活儿记在错的人名下（2026-10-25「谁干的记错了」同类）。
    _actual = str(patch.get("actual_name") or "").strip()
    if _actual:
        _expected = str(m.get("session_label") or "").strip()
        _match = bool(_expected) and _actual == _expected
        m["actual_name"] = _actual[:120]
        m["name_check"] = {
            "expected": _expected, "actual": _actual[:120], "match": _match, "at": _now(),
            "note": ("一致" if _match else
                     "名单上叫 %s，你说它叫 %s —— 是同一个 agent 换了名字，还是换了另一个？"
                     % (_expected or "（平台没给它身份名）", _actual)),
        }
    # ★2026-10-27：`tier` 允许从 patch 里带（HTTP/回执/测试都走这条），不然调用方传了也没用
    _tier = str(tier or patch.get("tier") or "").strip()
    if _tier:
        m["tier"] = _tier[:16]
    ev["who"] = who
    ev["at"] = _now()

    tier_now = m.get("tier") or ("a_protocol" if ev.get("protocol_ok")
                                 else ("b_bridge" if ev.get("bridge_ok") else "c_visual"))
    m["tier"] = tier_now
    need = {
        "a_protocol": ["protocol_ok"],
        # ★2026-10-31 修：B 档要**桥通 + 真取走一次文件**才算齐 ——
        #   老板 SOP：「只通没取件，最高只记 acked，不参与干活」。原来只要求 bridge_ok ⇒ 判成 active（假状态）。
        "b_bridge": ["bridge_ok", "mailbox_ok"],
        "c_visual": ["paste_seen", "press_ok", "rename_seen"],
    }[tier_now if tier_now in ("a_protocol", "b_bridge", "c_visual") else "c_visual"]
    missing = [k for k in need if not ev.get(k)]
    scope_ok = bool(str(m.get("verified_scope") or "").strip())

    if not missing and scope_ok:
        _set_state(m, "active", "证据齐备（%s）+ verified_scope 已写" % tier_now)
        m["activated_at"] = m.get("activated_at") or ""
    elif ev.get("rename_seen") or (tier_now == "a_protocol" and ev.get("protocol_ok")) \
            or (tier_now == "b_bridge" and ev.get("mailbox_ok")):
        _set_state(m, "entered", "进到它会话（%s）" % tier_now)
    elif ev.get("paste_seen") or ev.get("protocol_ok") or ev.get("bridge_ok"):
        _set_state(m, "acked", "收到它已粘贴/已对上的证据（%s）" % tier_now)
    _save(d)
    out = {"ok": True, "member_seq": member_seq, "state": m["state"], "tier": tier_now,
           "evidence": ev, "verified_scope": m.get("verified_scope", "")}
    if m.get("name_check"):
        out["name_check"] = m["name_check"]
        if not m["name_check"].get("match"):
            out["name_check_warn"] = m["name_check"]["note"]
    if missing or not scope_ok:
        out["need"] = {"missing_evidence": missing,
                       "verified_scope": "要写清覆盖到哪一段（例：粘贴+改名，未验证它能回话）"
                       if not scope_ok else "已写"}
        out["next"] = ("补上这些证据再判：%s" % "、".join(missing)) if missing \
            else "写一句 verified_scope（覆盖到哪一段）"
        out["honest"] = "还没到 active —— 最高只记 %s（V112 §3：未打勾不算接上）" % m["state"]
    return out


def apply_state_rows(workshop_id: str, rows: list, *, source: str = "C") -> dict:
    """回执 `类型: 状态` 的落库（`状态: <slot_dir> | 新: <state> | 原因: …`）。"""
    d = _load()
    w = _ws(d, workshop_id)
    applied, rejected = [], []
    for row in rows:
        slot_dir = str(row.get("状态") or "").strip()
        to = str(row.get("新") or "").strip()
        why = str(row.get("原因") or "").strip() or "回执（%s）" % source
        if to not in MEMBER_STATES:
            rejected.append({"slot_dir": slot_dir, "new": to,
                             "why": "新状态只能是 %s" % "/".join(MEMBER_STATES)})
            continue
        hit = [m for m in w["members"] if m.get("slot_dir") == slot_dir]
        if not hit:
            rejected.append({"slot_dir": slot_dir, "new": to, "why": "没有这个岗位的工位"})
            continue
        for m in hit:
            _set_state(m, to, why)
            applied.append({"member_seq": m["member_seq"], "slot_dir": slot_dir, "to": to})
    _save(d)
    return {"applied": applied, "rejected": rejected}


# ── 旧数据映射（`V115` §3）─────────────────────────────────────────
def migrate_legacy(workshop_id: str, legacy_members: list) -> dict:
    """把旧的单层 `member.status` 映射成双层。**映不出来的标 legacy_unknown，不静默丢。**"""
    d = _load()
    w = _ws(d, workshop_id)
    if w["members"] and w.get("migrated_at"):
        return {"ok": True, "skipped": True, "why": "已经迁移过（%s）" % w["migrated_at"],
                "roster": roster(workshop_id)}
    mapped, unknown = [], []
    for old in legacy_members or []:
        status = str(old.get("status") or "")
        new_state, note = LEGACY_MAP.get(status, ("", ""))
        slot_dir = str(old.get("display_name") or old.get("member_id") or "").strip() or "未命名"
        if not new_state:
            unknown.append({"member_id": old.get("member_id"), "status": status})
            new_state, note = "pending", "旧状态 %r 映不出来，按 pending 记并标 legacy_unknown" % status
        w["seq"] = int(w.get("seq", 0)) + 1
        seq = w["seq"]
        w["members"].append({
            "member_seq": seq, "slot_dir": slot_dir,
            "harness_id": str((old.get("harness_ids") or [""])[0] or ""),
            "model_snapshot": "", "session_label": (old.get("session_aliases") or [""])[0],
            "state": new_state, "tier": "", "evidence": {}, "verified_scope": "",
            "activated_at": "", "wakeup_result": "",
            "created_at": _now(), "source": "legacy_migration",
            "migrated_from": {"status": status, "member_id": old.get("member_id")},
            "state_log": [{"at": _now(), "to": new_state, "why": note or "旧数据迁移"}],
        })
        mapped.append({"member_id": old.get("member_id"), "status": status, "member_seq": seq,
                       "state": new_state})
    w["slots"] = [{"slot_dir": x["slot_dir"], "harness_id": x["harness_id"], "model": "",
                   "headcount": 1, "state": "filled",
                   "seqs": [x["member_seq"]], "source": "legacy_migration",
                   "created_at": _now()} for x in w["members"]]
    w["legacy"] = unknown
    w["migrated_at"] = _now()
    _save(d)
    return {"ok": True, "mapped": mapped, "legacy_unknown": unknown,
            "note": "映不出来的旧状态已留在 legacy_unknown 里（界面上要显示出来，别静默丢）",
            "roster": roster(workshop_id)}
