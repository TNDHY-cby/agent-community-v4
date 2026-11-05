# -*- coding: utf-8 -*-
"""V55 内核：**信息卡**（`kind="evidence"`）的校验、收敛与限量分发。一处真相，纯函数，好测。

设计稿：`design-docs/V55_信息搜索者与防信息污染设计.md`（老板 2026-10-17「最优解」拍板）。

## 文件干什么（四件事，都只做**标注**，不改写内容）

1. `validate(card)` —— 硬约束：至少 1 个来源、每源必须 `url`+`quote`、`quote≤200` 字、
   整卡 `≤1200` 字、必须有 `as_of`/`limits`。**不合规就拒收并说清怎么改**（不是默默裁剪）。
2. `grade_sources(sources)` —— 质量分档：`primary` / `secondary` / `marketing` / `anon`，
   并把 `confidence` **压到档位允许的上限**（营销稿不许自评 high）。
3. `merge_duplicates(sources)` —— 去重归并：同 host + 同 title（或摘录高度相似）⇒ 一个"来源组"，
   卡片上显示「N 家转载同一稿」，不把转载当独立证据。
4. `render_summary(card)` —— **限量再分发**：广播出去的只有摘要（断言 + 来源标题列表 + rid），
   **绝不带 `quote` 原文**（有守卫按真广播文本判）。

冲突（`disputed`）由调用方在两卡之间判定并回写，本模块只提供 `contradicts` 归一。
"""
from __future__ import annotations

import re
from typing import Any, Iterable

#: 硬约束（超了拒收；这些数字是"防污染"的第一道闸）
QUOTE_MAX = 200
CARD_MAX = 1200
TITLE_MAX = 60
NOTE_MAX = 120
URL_MAX = 500

#: 质量档（从高到低）；`anon` = 无署名无日期
GRADES = ("primary", "secondary", "marketing", "anon")
#: 档位允许的最高自评置信度（营销稿/无署名**不许** high）
GRADE_CAP = {"primary": "high", "secondary": "medium", "marketing": "low", "anon": "low"}
_CONF_ORDER = {"low": 0, "medium": 1, "high": 2}

#: 营销页的常见信号（只用于**分档**，不做内容审核、不拒收）
_MARKETING_HINTS = ("/product", "/pricing", "/buy", "/demo", "/promo", "utm_", "/solutions",
                    "免费试用", "立即购买", "联系我们", "限时")
_ANON_PUBLISHERS = ("", "unknown", "匿名", "佚名", "n/a", "none")


def _s(v: Any) -> str:
    return str(v or "").strip()


def host_of(url: str) -> str:
    m = re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://([^/?#]+)", _s(url))
    return (m.group(1).lower().lstrip("www.") if m else "")


# ── ① 硬约束 ───────────────────────────────────────────────────────────
def validate(card: dict) -> tuple[bool, list[str], dict]:
    """校验并归一信息卡。返回 `(ok, errors, normalized)`；`errors` 非空即拒收。"""
    errs: list[str] = []
    ev = (card or {}).get("evidence") or {}
    sources = ev.get("sources") or []
    name = _s(card.get("name"))
    note = _s(card.get("note"))

    if not name:
        errs.append("name（卡片标题）不能为空")
    elif len(name) > TITLE_MAX:
        errs.append("name 超过 %d 字（现在是 %d）—— 标题写短，细节放 note" % (TITLE_MAX, len(name)))
    if not note:
        errs.append("note（一句话结论）不能为空")
    elif len(note) > NOTE_MAX:
        errs.append("note 超过 %d 字（现在是 %d）—— 结论只要一句" % (NOTE_MAX, len(note)))
    if not _s(ev.get("as_of")):
        errs.append("evidence.as_of（证据时效）必填，例如 2026-10-17")
    if not _s(ev.get("limits")):
        errs.append("evidence.limits（没覆盖什么）必填 —— 不许留空，这是诚实的一部分")
    conf = _s(ev.get("confidence")).lower()
    if conf and conf not in _CONF_ORDER:
        errs.append("evidence.confidence 只能是 high / medium / low")
    if not isinstance(sources, list) or not sources:
        errs.append("evidence.sources 至少 1 条（没有来源的结论＝不许进工作间）")

    norm_sources = []
    for i, s in enumerate(sources if isinstance(sources, list) else [], 1):
        if not isinstance(s, dict):
            errs.append("sources[%d] 必须是对象" % i)
            continue
        url, quote = _s(s.get("url")), _s(s.get("quote"))
        if not url:
            errs.append("sources[%d].url 必填" % i)
        elif len(url) > URL_MAX:
            errs.append("sources[%d].url 过长（>%d）" % (i, URL_MAX))
        if not quote:
            errs.append("sources[%d].quote 必填（摘录能让人自己核）" % i)
        elif len(quote) > QUOTE_MAX:
            errs.append("sources[%d].quote 超过 %d 字（现在 %d）—— 只摘关键句，别整段搬"
                        % (i, QUOTE_MAX, len(quote)))
        norm_sources.append({
            "url": url, "title": _s(s.get("title")), "publisher": _s(s.get("publisher")),
            "quoted_at": _s(s.get("quoted_at")), "quote": quote,
            "primary": bool(s.get("primary")), "paywall": bool(s.get("paywall")),
        })

    total = len(name) + len(note) + sum(len(x["quote"]) for x in norm_sources)
    if total > CARD_MAX:
        errs.append("整卡正文超过 %d 字（现在 %d）—— 这张卡会把别人的上下文冲掉" % (CARD_MAX, total))

    norm = {
        "name": name, "note": note, "kind": "evidence",
        "evidence": {
            "as_of": _s(ev.get("as_of")), "confidence": conf or "low",
            "limits": _s(ev.get("limits")),
            "contradicts": [str(x) for x in (ev.get("contradicts") or [])],
            "sources": norm_sources,
        },
    }
    return (not errs), errs, norm


# ── ② 质量分档（并把 confidence 压到上限）────────────────────────────────
def grade_of(source: dict) -> str:
    """单条来源的档位（规则透明：一手 > 二手 > 营销 > 无署名）。"""
    pub = _s(source.get("publisher")).lower()
    url = _s(source.get("url")).lower()
    if pub in _ANON_PUBLISHERS and not _s(source.get("quoted_at")):
        return "anon"
    if any(h in url for h in _MARKETING_HINTS):
        return "marketing"
    if source.get("primary"):
        return "primary"
    if not _s(source.get("publisher")):
        return "anon"
    return "secondary"


def _cap(conf: str, cap: str) -> str:
    return conf if _CONF_ORDER.get(conf, 0) <= _CONF_ORDER.get(cap, 0) else cap


def grade_sources(sources: Iterable[dict]) -> dict:
    """给每条来源打档，并算出**本卡允许的最高置信度**与是否被压低。"""
    graded, best = [], "low"
    for s in sources:
        g = grade_of(s)
        graded.append({**s, "grade": g})
        if _CONF_ORDER[GRADE_CAP[g]] > _CONF_ORDER[best]:
            best = GRADE_CAP[g]
    return {"sources": graded, "cap": best}


def effective_confidence(self_reported: str, cap: str) -> dict:
    """自评 vs 上限 ⇒ 生效值，并如实说明"被压低"（不做静默降级）。"""
    want = _s(self_reported).lower() or "low"
    got = _cap(want, cap)
    return {"reported": want, "effective": got, "capped": got != want,
            "why": "" if got == want else "来源里没有够硬的档位（营销/无署名）⇒ 最多 %s" % cap}


# ── ③ 去重归并（转载不等于独立证据）──────────────────────────────────────
def _norm_text(s: str) -> str:
    return re.sub(r"[\s\u3000，。、；：！？,.;:!?\"'“”‘’（）()\[\]【】-]+", "", _s(s)).lower()


def _similar(a: str, b: str) -> float:
    """极简相似度：字符 3-gram 覆盖率（够用，且不引依赖）。"""
    a, b = _norm_text(a), _norm_text(b)
    if not a or not b:
        return 0.0
    ga = {a[i:i + 3] for i in range(max(1, len(a) - 2))}
    gb = {b[i:i + 3] for i in range(max(1, len(b) - 2))}
    return len(ga & gb) / max(1, len(ga | gb))


def merge_duplicates(sources: Iterable[dict], *, sim: float = 0.9) -> list[dict]:
    """同 host+同 title（或摘录高相似）⇒ 归并成一个"来源组"，组内记 `reprints`。"""
    groups: list[dict] = []
    for s in sources:
        h, t = host_of(_s(s.get("url"))), _norm_text(_s(s.get("title")))
        placed = False
        for g in groups:
            same_host = h and h == g["host"]
            same_title = t and t == g["title_norm"]
            alike = _similar(_s(s.get("quote")), g["quote"]) >= sim
            if same_host and same_title or same_title and alike or (same_host and alike):
                g["reprints"].append(s)
                placed = True
                break
        if not placed:
            groups.append({"host": h, "title_norm": t, "quote": _s(s.get("quote")),
                           "lead": s, "reprints": [s]})
    out = []
    for g in groups:
        out.append({"lead": g["lead"], "count": len(g["reprints"]),
                    "reprints": [{"url": _s(x.get("url")), "publisher": _s(x.get("publisher"))}
                                 for x in g["reprints"]]})
    return out


# ── ④ 限量再分发：广播里**不许**出现摘录原文 ────────────────────────────
def render_summary(card: dict, *, rid: str = "") -> str:
    """给工作间的**摘要**（notice 用）：断言 + 来源组标题 + 档位 + 争议标记。**不含 quote。**"""
    ev = (card or {}).get("evidence") or {}
    name = _s(card.get("name"))[:TITLE_MAX]
    note = _s(card.get("note"))[:NOTE_MAX]
    groups = merge_duplicates(ev.get("sources") or [])
    bits = []
    for g in groups:
        lead = g["lead"]
        label = _s(lead.get("publisher")) or host_of(_s(lead.get("url"))) or "未署名来源"
        bits.append(label + ("（%d 家转载同一稿）" % g["count"] if g["count"] > 1 else ""))
    conf = ev.get("confidence") or "low"
    flags = []
    if ev.get("disputed"):
        flags.append("⚠️有争议")
    if len(groups) == 0:
        flags.append("⚠️无来源")
    line = "信息卡「%s」：%s｜来源 %d 组（%s）｜置信 %s%s%s" % (
        name, note, len(groups), "、".join(bits[:4]), conf,
        ("｜" + "·".join(flags)) if flags else "", ("｜rid=%s" % rid) if rid else "")
    return line


def summary_has_quotes(card: dict, summary: str) -> bool:
    """守卫用：摘要里是否漏了摘录原文（漏了就是"污染"漏出去了）。"""
    ev = (card or {}).get("evidence") or {}
    for s in ev.get("sources") or []:
        q = _s(s.get("quote"))
        if q and len(q) >= 12 and q[:12] in summary:
            return True
    return False


# ── ⑤ 跨组织采信态与逐文件复核（`V130` §4，2026-11-04 老板拍板）──────────
#
# 两套档位**不许混**（`V130` §2 已定稿的正确说法）：
#   · 通道活性档（原 A/B/C）回答"这条回执渠道通不通"——证明通路，**不进验收**；
#   · 结果证据档（证据 L0/L1/L2/L4）回答"这个结果能不能被独立复核"——≥L1 才进验收。
# 这一节新增的是第三件事：**复核结论算不算数**（采信态），四值，落任务账本 `external` 段。

#: 采信态四值（`V130` §4 规则三）。`accepted_self` **不是**通过。
ACCEPT_STATES = ("accepted_self", "accepted_peer", "disputed", "refuted")
ACCEPT_MEANING = {
    "accepted_self": "未复核：只有它自己说",
    "accepted_peer": "已复核：本侧重算一致",
    "disputed": "有争议，不采信（冻结待裁）",
    "refuted": "已证伪（可直接降级）",
}
#: 只有这一档能进验收（§6 的唯一硬后果）
ACCEPT_CAN_PASS = {"accepted_self": False, "accepted_peer": True,
                   "disputed": False, "refuted": False}
#: 交付方还没复核时的默认值 —— 不许默认成"已采信"
ACCEPT_DEFAULT = "accepted_self"


def accept_state(*, disputed: bool = False, refuted: bool = False,
                 peer_checked: bool = False, peer_ok: bool = False) -> dict:
    """算采信态（纯函数）。优先级：**已证伪 > 有争议 > 复核结果 > 自证**。

    - 只有交付方自证（没做过本侧复核）⇒ `accepted_self`；
    - 本侧真重算且全部一致 ⇒ `accepted_peer`；
    - 有争议 ⇒ `disputed`（冻结待裁，**不**因为复核通过就自动解除）；
    - 已证伪 ⇒ `refuted`（可直接降级）。
    """
    if refuted:
        state, why = "refuted", "已有证据证明这条交付不成立"
    elif disputed:
        state, why = "disputed", "双方主张冲突且未裁决 —— 平台不替人判谁对，但**不采信**"
    elif peer_checked and peer_ok:
        state, why = "accepted_peer", "接收方独立重算一致"
    elif peer_checked and not peer_ok:
        state, why = "accepted_self", "复核做了但**没对齐** ⇒ 仍只算自证（差异表见 peer_diff）"
    else:
        state, why = "accepted_self", "只有交付方自证，接收方还没独立复核"
    return {"state": state, "meaning": ACCEPT_MEANING[state],
            "can_pass": ACCEPT_CAN_PASS[state], "why": why,
            "next": "" if ACCEPT_CAN_PASS[state] else
                    ("先挂争议并说清待谁裁（POST …/dispute）" if state == "disputed" else
                     "接收方独立重算产物 sha256（POST …/tasks/{seq}/peer-review）；"
                     "自证换不到采信")}


def _norm_sha(v: Any) -> str:
    return _s(v).lower()


def diff_peer_artifacts(peer_files: Iterable[dict], local_files: dict, *,
                        local_slot_dir: str = "") -> dict:
    """**逐文件重算比对**（`V130` §4 规则二）：对端给的 `sha256` 清单 vs 本侧真扫结果。

    - `peer_files`：`[{name, sha256, bytes, slot_dir?}]`（A2A 回执里的产物清单就是这个形状）；
    - `local_files`：`artifacts.snapshot()` 的 `files`（`{相对产物目录的路径: {sha256, bytes}}`）。

    ⚠️ **比对键是「相对 `产物/` 的路径」**，不是绝对路径：两个组织的工位目录名天然不同
    （岗位名/成员编号各叫各的），拿整条路径去比会永远对不上。键是怎么来的写在 `key_from` 里。

    返回 `ok=True` 仅当：有可比的 hash、没有 sha/字节不一致、没有"对端说有本侧没有"。
    本侧多出文件只记进 `extra_local`（不阻断，但要让人看见）。
    """
    peer = [x for x in (peer_files or []) if isinstance(x, dict)]
    local = {str(k).replace("\\", "/"): (v or {}) for k, v in (local_files or {}).items()}
    rows, seen = [], set()
    matched = mismatched = missing = no_hash = 0

    for item in peer:
        name = _s(item.get("name")).replace("\\", "/")
        if not name:
            continue
        slot = _s(item.get("slot_dir")).strip("/")
        if slot and name.startswith(slot + "/"):
            key, key_from = name[len(slot) + 1:], "剥掉对端工位目录"
        elif "/" in name and local_slot_dir and name.startswith(str(local_slot_dir).strip("/") + "/"):
            key, key_from = name.split("/", 1)[1], "剥掉本侧工位目录"
        elif "/" in name:
            key, key_from = name.split("/", 1)[1], "剥掉第一段（两边工位目录名不同）"
        else:
            key, key_from = name, "原样"
        if key in seen:
            continue
        seen.add(key)

        want, want_bytes = _norm_sha(item.get("sha256")), item.get("bytes")
        got = local.get(key) or {}
        have, have_bytes = _norm_sha(got.get("sha256")), got.get("bytes")
        row = {"key": key, "key_from": key_from, "peer_name": name,
               "peer_sha256": want, "local_sha256": have,
               "peer_bytes": want_bytes, "local_bytes": have_bytes}
        if not want:
            row.update({"status": "peer_no_hash",
                        "why": "对端只给了文件名没给 sha256 —— 没有可重算的东西，不算复核"})
            no_hash += 1
        elif not got:
            row.update({"status": "missing_local",
                        "why": "对端清单里有这个文件，本侧 `产物/` 里没有（同名相对路径都没有）"})
            missing += 1
        elif want != have:
            row.update({"status": "sha_mismatch",
                        "why": "两边 sha256 不一致 ⇒ 内容不是同一份（不是路径对不上）"})
            mismatched += 1
        elif want_bytes is not None and have_bytes is not None and int(want_bytes) != int(have_bytes):
            row.update({"status": "bytes_mismatch",
                        "why": "sha 相同但字节数不同 —— 说明有一侧的数字是编的，要重算"})
            mismatched += 1
        else:
            row.update({"status": "match", "why": "sha256（与字节数）一致"})
            matched += 1
        rows.append(row)

    extra = [{"key": k, "sha256": _s((v or {}).get("sha256"))}
             for k, v in local.items() if k not in seen]
    ok = bool(rows) and not (mismatched or missing or no_hash)
    if not rows:
        why = "对端一条可复核的文件都没给（空清单不算复核）"
        nxt = "让它交产物并给 sha256；只有「我做完了」这句话最多算 L4"
    elif not ok:
        parts = []
        if mismatched:
            parts.append("%d 个文件 sha256/字节不一致" % mismatched)
        if missing:
            parts.append("%d 个对端说有、本侧没有" % missing)
        if no_hash:
            parts.append("%d 个对端没给 sha256" % no_hash)
        why = "复核没通过：" + "；".join(parts)
        nxt = "看 peer_diff 里每个文件的差异；对不齐就挂争议（POST …/dispute）交人裁"
    else:
        why = "全部 %d 个文件 sha256 一致（本侧独立重算）" % matched
        nxt = ""
    return {"ok": ok, "matched": matched, "mismatched": mismatched,
            "missing_local": missing, "no_hash": no_hash,
            "extra_local": extra[:50], "files": rows,
            "why": why, "next": nxt}


# ── ⑥ 委派话术（写给 **agent** 看的纯文本；与 V54 话术同口径：一处真相、无 markdown 星号）──
def search_instruction(question: str, must_cover: Iterable[str] = (), deadline_minutes: int = 0,
                       base_url: str = "") -> str:
    """检索委派的正文（发给搜索者 agent 的话）。**平台不搜**，只把要求说清、把回报格式钉死。"""
    q = _s(question)
    cover = [_s(x) for x in (must_cover or []) if _s(x)]
    lines = [
        "你是本工作间的「信息搜索者」：请去搜索并回答下面这个问题，然后按**信息卡**格式回报。",
        "",
        "【要回答的问题】" + q,
    ]
    if cover:
        lines.append("【必须覆盖】" + "、".join(cover))
    if deadline_minutes:
        lines.append("【时限】约 %d 分钟；没搜完就如实报「没搜完」，别硬编答案。" % int(deadline_minutes))
    lines += [
        "",
        "【回报格式】把结果放进 POST %s/api/harness/task-result 的 result 里，字段如下（缺一不可）："
        % (base_url.rstrip("/") if base_url else "<平台地址>"),
        "{",
        '  "name": "≤60 字的卡片标题",',
        '  "note": "≤120 字的一句话结论",',
        '  "evidence": {',
        '    "as_of": "证据时效，例如 2026-10-17",',
        '    "confidence": "high / medium / low（自评；来源里没有硬档位的话平台会把上限压下来）",',
        '    "limits": "这次没覆盖/没查到什么（必填，不许留空）",',
        '    "sources": [{"url": "https://…", "title": "…", "publisher": "…",',
        '                 "quoted_at": "2026-10-17", "quote": "≤200 字的关键摘录",',
        '                 "primary": true}]',
        "  }",
        "}",
        "",
        "【纪律】",
        "  · 每条结论都要有来源；没有来源的结论不要写（那是最坏的一种污染）；",
        "  · 摘录只摘关键句（≤200 字），不要整段搬；",
        "  · 同一篇稿子被多家转载，只算一个来源（平台也会归并，但你自己先别当多条证据）；",
        "  · 两家说法冲突就都留着、并在回报里说明冲突（别挑一个当唯一真相）；",
        "  · 搜不到就说「没找到」，并写清你搜了什么、下一步该放宽什么。",
    ]
    return "\n".join(lines).replace("**", "")

