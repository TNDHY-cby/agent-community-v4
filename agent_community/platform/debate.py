# -*- coding: utf-8 -*-
"""platform/debate：三级讨论区「辩论模式」的**纯逻辑内核**（无 IO、无第三方依赖）。

设计稿：`design-docs/V52_三级讨论区辩论模式设计.md`（依据＝老板给的《多Agent辩论_修订版》）。

## 这个内核只做一件事

**把「谁说得对」换成「谁的证据更站得住」** —— 因为在我们这种"agent 常常共享同一批数据源、
同构模型、相似检索路径"的环境里，多数投票的前提（投票者**相互独立**）**根本不成立**：
误差相关时，多数不是纠错，是**互相强化**（Condorcet 陪审团定理的前提被破坏）。

所以这里**没有投票**：权重由**证据来源重叠度**决定，分数只由**可核对的东西**构成
（可验证证据项 / 与校验器冲突 / 无证据断言），**没有"文采分"** ——
否则自洽的错误答案会套利（把"票多者胜"换成"说得漂亮者胜"，修订稿 §二.3 明确警告过这条）。

## 为什么是纯函数

跟 `procprobe` 同一条纪律：**能单测的逻辑不要藏在 IO 里**。
本模块不认识 FastAPI、不写盘、不发网络请求 —— 接工作间开关与讨论区由路由层做（下一轮）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import urlsplit

#: 同源判定阈值（Jaccard）：≥ 它判为"同源"，合并降权
DEFAULT_OVERLAP = 0.5
#: 边际信息增益下限：本轮新增**可验证**证据项少于它 ⇒ 收口
MIN_GAIN = 1
#: 轮次上限（修订稿：2–3 轮内闭环）
MAX_ROUNDS = 3
#: 一个工作间最多留多少条陈述（防状态文件无限增长）——**一处真相**：端点与成员回报都用它
MAX_STATEMENTS = 60

#: 成本口径（照抄修订稿 §四，别装作免费）——会随裁决一起如实回报
COST_NOTE = "总成本 ≈ N_agent × R_round × 单次上下文成本 + 检索成本（N=3,R=3 时仅生成 token 就 ≥ 单模型的 9 倍）"
CONFIDENCE_NOTE = "置信度是**启发式示意值**，需按场景标定，不是概率"

#: 「代价高」的档位（触发阈值之一）
HIGH_COST = ("high", "critical", "不可逆")


@dataclass
class Evidence:
    """一条证据：**来源身份** + 是否被外部核对过。"""
    kind: str = "url"          # url / doc / tool / file
    ref: str = ""
    verified: bool = False     # 被外部校验器/检索凭证确认过

    def ident(self) -> str:
        """归一成**身份串**（同一条来源的不同写法必须归一，否则同源判定会漏）。"""
        return "%s:%s" % (str(self.kind or "url").strip().lower(), _norm_ref(self.ref))


@dataclass
class Statement:
    """一轮里某个说话者的一条陈述：**观点 → 证据 → 推理**（三段，缺证据就是缺证据）。"""
    speaker: str = ""
    claim: str = ""
    evidence: list = field(default_factory=list)     # list[Evidence] 或同形 dict
    reasoning: str = ""
    conflicts_with_checker: bool = False             # 工具/计算返回与它互斥
    id: str = ""


def _norm_ref(ref) -> str:
    """来源身份归一：小写、去 `#fragment`、去尾部 `/`（同一来源的不同写法要认成同一个）。"""
    r = str(ref or "").strip()
    if not r:
        return ""
    if r.lower().startswith(("http://", "https://")):
        try:
            sp = urlsplit(r)
            return ("%s://%s%s" % (sp.scheme.lower(), (sp.netloc or "").lower(),
                                   (sp.path or "").rstrip("/"))).lower()
        except ValueError:
            pass
    return r.lower().rstrip("/")


def _as_evidence(item) -> Evidence:
    if isinstance(item, Evidence):
        return item
    if isinstance(item, dict):
        return Evidence(kind=item.get("kind") or "url", ref=item.get("ref") or "",
                        verified=bool(item.get("verified")))
    return Evidence(ref=str(item or ""))


def evidence_set(st: Statement) -> set:
    """一条陈述的**证据身份集合**（去重、丢掉空身份）。"""
    return {e.ident() for e in (_as_evidence(x) for x in (st.evidence or [])) if e.ref and e.ident().split(":", 1)[-1]}


def jaccard(a: set, b: set) -> float:
    """Jaccard 重叠度。

    ⚠️ **两边都空 ⇒ 返回 0.0**：`"都没有证据"` 不是 `"互相印证"` ——
    若这里返回 1.0，两个空口无凭的人会被判成"同源"，进而合并成一个簇，
    那就等于**把"都没证据"当成了"有一致的证据"**。
    """
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


def cluster(statements: list, threshold: float = DEFAULT_OVERLAP) -> list:
    """按证据重叠度把陈述分簇（**连通分量**；对称、确定，不看输入顺序）。

    同簇 ⇒ 判为**同源**：它们合起来只算**一个**权重（见 `arbitrate` 的权重规则）。
    """
    n = len(statements)
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    sets = [evidence_set(s) for s in statements]
    for i in range(n):
        for k in range(i + 1, n):
            if jaccard(sets[i], sets[k]) >= threshold:
                ri, rk = find(i), find(k)
                if ri != rk:
                    parent[rk] = ri
    groups: dict = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)
    return [sorted(v) for _, v in sorted(groups.items())]


def parse_statement(body: dict, *, speaker: str = "") -> tuple:
    """把一段外部输入解析成**结构化陈述**（观点 → 证据 → 推理）。

    ★**一处真相**：对接中心的 `…/debate/statements` 端点与**成员回报**（`/api/harness/task-result`）
    走的是**同一个解析器** —— 否则两边字段口径迟早分叉（规则 20）。

    返回 `(陈述 dict, 错误原因)`；解析失败时前者为 `None`。
    - `claim` / `speaker` 必填：裁决要**指名道姓**，匿名发言没有意义；
    - **`evidence` 可以不给**（那是常态）：记成"无证据"，裁决里权重 0 ——
      **但发言本身不会被丢掉**（原话留在讨论区）；
    - 证据项 `{kind, ref, verified}`：`kind` 是自由文本（`url`/`doc`/`tool`/`file`/`commit`…），
      好让**完成声明**那套 `{commit, file, test}` 也能直接喂进来（不新增第二套字段名）。
    """
    body = body or {}
    claim = str(body.get("claim") or "").strip()
    if not claim:
        return None, "claim（一句话结论）不能为空"
    who = str(body.get("speaker") or speaker or "").strip()
    if not who:
        return None, "speaker（谁说的）不能为空 —— 裁决要指名道姓，不能匿名"
    ev_in = body.get("evidence") or []
    if not isinstance(ev_in, list):
        return None, "evidence 必须是数组：[{kind, ref, verified}]"
    evidence = []
    for e in ev_in[:20]:
        if isinstance(e, dict):
            ref = str(e.get("ref") or "").strip()
            if not ref:
                continue
            evidence.append({"kind": str(e.get("kind") or "url"), "ref": ref,
                             "verified": bool(e.get("verified"))})
        elif str(e).strip():
            evidence.append({"kind": "url", "ref": str(e).strip(), "verified": False})
    return {
        "speaker": who,
        "claim": claim,
        "evidence": evidence,
        "reasoning": str(body.get("reasoning") or "").strip()[:2000],
        "conflicts_with_checker": bool(body.get("conflicts_with_checker")),
    }, ""


def _verified_count(idents: set, statements: list, cluster_idx: list) -> int:
    """该簇里**被核对过**的证据项数（按身份去重）。"""
    out = set()
    for i in cluster_idx:
        for e in (_as_evidence(x) for x in (statements[i].evidence or [])):
            if e.verified and e.ident() in idents:
                out.add(e.ident())
    return len(out)


def trigger(statements: list, *, allow: bool = False, error_cost: str = "normal",
            user_asked: bool = False, threshold: float = DEFAULT_OVERLAP) -> dict:
    """**该不该开一轮辩论**（成本闸门）。

    `allow=False`（模式没开）⇒ 一律不触发 —— 开关是"允许"，阈值是"该不该"，两者都要。
    简单事实查询 / 纯主观偏好**不触发**；结论互斥 / 与校验器冲突 / 代价高**才触发**。
    """
    if not allow:
        return {"triggered": False, "reason": "辩论模式未开启（默认关，需用户显式开启）"}
    if not statements:
        return {"triggered": False, "reason": "还没有可裁决的陈述"}
    groups = cluster(statements, threshold)
    sets = [evidence_set(s) for s in statements]
    checker_conflict = any(bool(s.conflicts_with_checker) for s in statements)
    # "多路结论互斥"：两个簇**证据集不相交**且**结论文本不同**
    disagree = False
    for a in range(len(groups)):
        for b in range(a + 1, len(groups)):
            ea = set().union(*[sets[i] for i in groups[a]]) if groups[a] else set()
            eb = set().union(*[sets[i] for i in groups[b]]) if groups[b] else set()
            claims_a = {statements[i].claim.strip() for i in groups[a]}
            claims_b = {statements[i].claim.strip() for i in groups[b]}
            if ea.isdisjoint(eb) and claims_a != claims_b:
                disagree = True
    high_cost = str(error_cost or "").lower() in HIGH_COST
    if checker_conflict:
        return {"triggered": True, "reason": "工具/计算结果与某个结论互斥"}
    if disagree:
        return {"triggered": True, "reason": "多路结论互斥且证据互不相交"}
    if high_cost:
        return {"triggered": True, "reason": "错误代价高（%s）" % error_cost}
    if user_asked:
        return {"triggered": True, "reason": "用户显式要求"}
    return {"triggered": False, "reason": "没有分歧、代价不高 —— 不值得付 N×R 倍 token 的代价"}


def arbitrate(statements: list, *, round_no: int = 1, prev_evidence=None,
              threshold: float = DEFAULT_OVERLAP, max_rounds: int = MAX_ROUNDS,
              allow: bool = False, error_cost: str = "normal", user_asked: bool = False) -> dict:
    """**一轮裁决**：证据同源度定权重、轨迹评分排序、按终止规则决定要不要再来一轮。

    返回结构见设计稿 §5.2（`output` 必含**四件套**：结论 / 置信度 / 关键证据 / 分歧点）。
    """
    statements = list(statements or [])
    trig = trigger(statements, allow=allow, error_cost=error_cost,
                   user_asked=user_asked, threshold=threshold)
    groups = cluster(statements, threshold) if statements else []
    sets = [evidence_set(s) for s in statements]

    # ── 权重：**一个簇总权重 1**（同源合并降权：两个人同错，合并后仍是 1 而不是 2）
    #           无任何证据 ⇒ 权重 0（不进结论候选，但绝不从讨论区删掉它的原话）
    clusters_out, ranking = [], []
    all_evidence: set = set()
    for idx in groups:
        idents = set().union(*[sets[i] for i in idx]) if idx else set()
        all_evidence |= idents
        verified = _verified_count(idents, statements, idx)
        conflict = sum(1 for i in idx if statements[i].conflicts_with_checker)
        unsupported = sum(1 for i in idx if not sets[i])
        score = verified - conflict - unsupported
        has_evidence = bool(idents)
        weight = 1 if has_evidence else 0
        clusters_out.append({
            "weight": weight, "speakers": [statements[i].speaker for i in idx],
            "score": score, "verified": verified, "conflict": conflict,
            "unsupported": unsupported,
            "evidence": sorted(idents),
            "claims": [statements[i].claim for i in idx],
        })
        for i in idx:
            ranking.append({
                "speaker": statements[i].speaker, "claim": statements[i].claim,
                "cluster": len(clusters_out) - 1, "weight": weight, "score": score,
                "verified": verified, "conflict": int(bool(statements[i].conflicts_with_checker)),
                "unsupported": int(not sets[i]),
                "evidence": sorted(sets[i]),
            })

    # ── 结论候选：权重 > 0 **且** 分数 > 0 **且** 没被校验器打脸（同分并列，不硬选）
    cands = [c for c in clusters_out if c["weight"] > 0 and c["score"] > 0 and c["conflict"] == 0]
    best = max((c["score"] for c in cands), default=0)
    top = [c for c in cands if c["score"] == best]

    # ── 置信度（启发式，见 V52 §3.4；**如实标注**它不是概率）
    evidence_share = (len(top[0]["evidence"]) / len(all_evidence)) if (top and all_evidence) else 0.0
    pos_sum = sum(c["score"] for c in clusters_out if c["score"] > 0)
    score_share = (best / pos_sum) if (pos_sum and best) else 0.0
    confidence = max(0.0, min(1.0, 0.5 * evidence_share + 0.5 * score_share))

    # ── 分歧点：证据互不相交 + 结论不同 的两个簇
    disagreements = []
    for a in range(len(clusters_out)):
        for b in range(a + 1, len(clusters_out)):
            ea, eb = set(clusters_out[a]["evidence"]), set(clusters_out[b]["evidence"])
            if ea.isdisjoint(eb) and clusters_out[a]["claims"] != clusters_out[b]["claims"]:
                disagreements.append("%s 与 %s：结论不同且证据集互不相交"
                                     % ("/".join(clusters_out[a]["speakers"]) or "?",
                                        "/".join(clusters_out[b]["speakers"]) or "?"))

    # ── 终止条件（满足任一即收口；顺序＝先无解、再无新料、再算增益、最后轮次上限）
    prev = set(prev_evidence or [])
    new_evidence = all_evidence - prev if prev_evidence is not None else set(all_evidence)
    new_verified = 0
    for i, s in enumerate(statements):
        for e in (_as_evidence(x) for x in (s.evidence or [])):
            if e.verified and e.ident() in new_evidence:
                new_verified += 1
    if round_no >= max_rounds:
        stop, reason = True, "轮次已达上限（%d）" % max_rounds
    elif not disagreements and not any(c["conflict"] for c in clusters_out):
        stop, reason = True, "证据链闭环：只剩一个立场且没有未解冲突"
    elif prev_evidence is not None and not new_evidence:
        stop, reason = True, "无新信息进入"
    elif prev_evidence is not None and new_verified < MIN_GAIN:
        stop, reason = True, "边际信息增益不足（新增可验证证据 %d < %d）" % (new_verified, MIN_GAIN)
    else:
        stop, reason = False, "还可以再来一轮（有新证据或仍有分歧）"

    conclusion = top[0]["claims"][0] if top else ""
    if len(top) > 1:
        conclusion = "（并列，同分不硬选）" + " ／ ".join(c["claims"][0] for c in top if c["claims"])
    if not top:
        conclusion = ""

    return {
        "round": int(round_no), "threshold": threshold,
        "triggered": bool(trig["triggered"]), "trigger_reason": trig["reason"],
        "clusters": clusters_out,
        "ranking": sorted(ranking, key=lambda r: (-r["score"], r["speaker"])),
        "output": {
            "conclusion": conclusion,
            "confidence": round(confidence, 4),
            "key_evidence": sorted(set().union(*[set(c["evidence"]) for c in top])) if top else [],
            "disagreements": disagreements,
        },
        "stop": {"stop": bool(stop), "reason": reason},
        "notes": [CONFIDENCE_NOTE, COST_NOTE,
                  "同源者已合并降权（一个簇总权重 1）；无证据的断言权重 0，但不从讨论区删除"],
    }
