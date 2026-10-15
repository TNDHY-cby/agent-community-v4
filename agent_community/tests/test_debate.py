# -*- coding: utf-8 -*-
"""三级讨论区「辩论模式」内核的守卫（`V52` §6）。

这一组测的不是"功能能用"，而是**设计上最容易被做错的那几件事**：
  · 同源降权（别把 2 个同源 agent 当成 2 份独立意见）；
  · 无证据 = 权重 0（但**不删原话**）；
  · 与校验器冲突 = 扣分且不进候选；
  · ★**"多数但没证据"必须输给"少数但有独立证据"** —— 这是"不做投票"的试金石；
  · 触发阈值（默认关 + 简单问题不值得付 N×R 倍 token）；
  · 终止条件（轮次上限 / 无新信息 / 边际增益）。
"""
from __future__ import annotations

import os
import sys

_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
if _ROOT not in sys.path:
    sys.path.append(_ROOT)

from agent_community.platform import debate as db  # noqa: E402


def _ev(ref, kind="url", verified=True):
    return db.Evidence(kind=kind, ref=ref, verified=verified)


# ── 证据身份归一（同一条来源的不同写法必须认成同一个）──────────────────────────
def test_evidence_identity_normalizes_case_fragment_and_trailing_slash():
    a = _ev("https://Docs.Example.com/a/b/#sec2")
    b = _ev("https://docs.example.com/a/b")
    assert a.ident() == b.ident(), (a.ident(), b.ident())


def test_jaccard_of_two_empty_sets_is_zero_not_one():
    """★两边都空 ⇒ **0**（"都没有证据"不是"互相印证"）。

    若这里返回 1.0，两个空口无凭的人会被判成同源、合并成一个簇 —— 等于
    把"都没证据"当成了"有一致的证据"，这正是修订稿警告的"误差相关互相强化"。
    """
    assert db.jaccard(set(), set()) == 0.0
    assert db.jaccard({"a"}, set()) == 0.0
    assert db.jaccard({"a", "b"}, {"b", "c"}) == 1 / 3


# ── 同源合并 / 独立保留 ─────────────────────────────────────────────────────
def test_same_source_merges_into_one_cluster_with_weight_one():
    s1 = db.Statement(speaker="A", claim="X 是对的", evidence=[_ev("https://x.example.com/p")])
    s2 = db.Statement(speaker="B", claim="X 是对的", evidence=[_ev("https://x.example.com/p")])
    out = db.arbitrate([s1, s2], allow=True, user_asked=True)
    assert len(out["clusters"]) == 1, "证据完全相同却分成两簇 ⇒ 同源降权没生效"
    assert out["clusters"][0]["weight"] == 1, "一个簇的总权重必须是 1（不是 2）"
    assert sorted(out["clusters"][0]["speakers"]) == ["A", "B"]


def test_independent_evidence_keeps_independent_weight():
    s1 = db.Statement(speaker="A", claim="X", evidence=[_ev("https://a.example.com/1")])
    s2 = db.Statement(speaker="B", claim="Y", evidence=[_ev("https://b.example.com/2")])
    out = db.arbitrate([s1, s2], allow=True, user_asked=True)
    assert len(out["clusters"]) == 2
    assert all(c["weight"] == 1 for c in out["clusters"]), "独立来源应各保留权重 1"


def test_unsupported_statement_has_zero_weight_but_is_still_ranked():
    """无证据 ⇒ 权重 0、进 unsupported、**不进结论候选** —— 但**仍然出现在排名里**（原话不删）。"""
    s = db.Statement(speaker="A", claim="我觉得是这样", evidence=[])
    out = db.arbitrate([s], allow=True, user_asked=True)
    assert out["clusters"][0]["weight"] == 0
    assert out["clusters"][0]["unsupported"] == 1
    assert out["output"]["conclusion"] == "", "没有证据的东西不该变成结论"
    assert any(r["speaker"] == "A" for r in out["ranking"]), "无证据的发言也要留痕（不删原话）"


def test_checker_conflict_is_penalized_and_excluded():
    s = db.Statement(speaker="A", claim="2+2=5", evidence=[_ev("https://a.example.com/1")],
                     conflicts_with_checker=True)
    out = db.arbitrate([s], allow=True, user_asked=True)
    assert out["clusters"][0]["conflict"] == 1
    assert out["clusters"][0]["score"] == 0, "可验证 1 − 冲突 1 = 0"
    assert out["output"]["conclusion"] == "", "被校验器打脸的结论不许进候选"


# ── ★试金石：多数但没证据 必须输给 少数但有独立证据 ─────────────────────────
def test_majority_without_evidence_loses_to_minority_with_independent_evidence():
    """★这条是"不做投票"的试金石。

    三个人异口同声、**全都没证据**（所以它们是三个独立的空集 ⇒ 三个簇、权重都是 0）；
    一个人**有一条独立且被核对过的证据**。按投票是 3:1 压过去，按证据必须是后者胜。
    """
    crowd = [db.Statement(speaker="M%d" % i, claim="一定是 A", evidence=[]) for i in range(3)]
    lone = db.Statement(speaker="E", claim="应该是 B",
                        evidence=[_ev("https://evidence.example.com/report")])
    out = db.arbitrate(crowd + [lone], allow=True, user_asked=True)
    assert out["output"]["conclusion"] == "应该是 B", \
        "多数无证据压过了少数有证据 ⇒ 又变回投票了：%r" % (out["output"],)
    assert out["output"]["confidence"] > 0
    crowd_clusters = [c for c in out["clusters"] if c["unsupported"] >= 1]
    assert crowd_clusters and all(c["weight"] == 0 for c in crowd_clusters)


def test_tie_is_reported_as_tie_not_arbitrarily_picked():
    """同分**如实并列**，不硬选一个（无 ground truth 时我们没有资格裁判"谁对"）。"""
    s1 = db.Statement(speaker="A", claim="方案甲", evidence=[_ev("https://a.example.com/1")])
    s2 = db.Statement(speaker="B", claim="方案乙", evidence=[_ev("https://b.example.com/2")])
    out = db.arbitrate([s1, s2], allow=True, user_asked=True)
    assert "并列" in out["output"]["conclusion"], out["output"]
    assert "方案甲" in out["output"]["conclusion"] and "方案乙" in out["output"]["conclusion"]


def test_bigger_cluster_with_weaker_evidence_does_not_win():
    """★第二条试金石（补上被突变体 M5 逃掉的那个洞）。

    上一版试金石里"人多"那簇**权重是 0**，所以在排序前就被排除了 ——
    于是"按人数选簇"这个突变体照样能活（守卫是**因为错误的原因**通过的）。
    这里让**两个簇都进了候选**，只有"人数 vs 分数"这一个差别：

    - 甲簇：**2 个人**，但证据同源（合并成一簇），只有 **1** 条被核对的证据 ⇒ 分数 1；
    - 乙簇：**1 个人**，**2** 条互相独立且都被核对的证据 ⇒ 分数 2。

    按分数 ⇒ 乙；按人数 ⇒ 甲。必须出乙。
    """
    two_people_same_source = [
        db.Statement(speaker="M0", claim="选甲方案", evidence=[_ev("https://shared.example.com/x")]),
        db.Statement(speaker="M1", claim="选甲方案", evidence=[_ev("https://shared.example.com/x")]),
    ]
    one_person_two_independent = db.Statement(
        speaker="E", claim="选乙方案",
        evidence=[_ev("https://e1.example.com/a"), _ev("https://e2.example.com/b")])
    out = db.arbitrate(two_people_same_source + [one_person_two_independent],
                       allow=True, user_asked=True)
    cands = [c for c in out["clusters"] if c["weight"] > 0]
    assert len(cands) == 2, "两个簇都该进候选（否则这条测不到人数 vs 分数）"
    assert out["output"]["conclusion"] == "选乙方案", \
        "按人数选了甲（人多但证据更弱）⇒ 又变回投票：%r" % (out["output"],)
    by_claim = {(c["claims"] or [""])[0]: c for c in cands}
    assert by_claim["选甲方案"]["score"] == 1 and by_claim["选乙方案"]["score"] == 2
    assert len(by_claim["选甲方案"]["speakers"]) == 2, "同源的两个人必须合在一簇里"


# ── 触发阈值 ───────────────────────────────────────────────────────────────
def test_mode_off_never_triggers_even_with_disagreement():
    s1 = db.Statement(speaker="A", claim="X", evidence=[_ev("https://a.example.com/1")])
    s2 = db.Statement(speaker="B", claim="Y", evidence=[_ev("https://b.example.com/2")])
    t = db.trigger([s1, s2], allow=False)
    assert t["triggered"] is False and "未开启" in t["reason"]


def test_no_disagreement_low_cost_does_not_trigger():
    s1 = db.Statement(speaker="A", claim="一致结论", evidence=[_ev("https://a.example.com/1")])
    s2 = db.Statement(speaker="B", claim="一致结论", evidence=[_ev("https://a.example.com/1")])
    t = db.trigger([s1, s2], allow=True)
    assert t["triggered"] is False, t
    assert "不值得" in t["reason"] or "没有分歧" in t["reason"]


def test_mutually_exclusive_claims_trigger():
    s1 = db.Statement(speaker="A", claim="X 对", evidence=[_ev("https://a.example.com/1")])
    s2 = db.Statement(speaker="B", claim="X 错", evidence=[_ev("https://b.example.com/2")])
    t = db.trigger([s1, s2], allow=True)
    assert t["triggered"] is True and "互斥" in t["reason"]


def test_checker_conflict_triggers_and_high_cost_triggers():
    s = db.Statement(speaker="A", claim="X", evidence=[_ev("https://a.example.com/1")],
                     conflicts_with_checker=True)
    assert db.trigger([s], allow=True)["triggered"] is True
    s2 = db.Statement(speaker="A", claim="X", evidence=[_ev("https://a.example.com/1")])
    assert db.trigger([s2], allow=True, error_cost="critical")["triggered"] is True


# ── 终止条件 ───────────────────────────────────────────────────────────────
def test_stops_at_round_limit():
    s = db.Statement(speaker="A", claim="X", evidence=[_ev("https://a.example.com/1")])
    out = db.arbitrate([s], allow=True, user_asked=True, round_no=3, max_rounds=3)
    assert out["stop"]["stop"] is True and "上限" in out["stop"]["reason"]


def test_stops_when_no_new_evidence_enters():
    """本轮**没有新证据进入** ⇒ 收口。

    ⚠️ 这里必须用**两个互斥立场**：若只有一个立场，"证据链闭环"会**先**命中
    （判据顺序：先无解 → 再无新料 → 再算增益 → 最后轮次上限），本条就测不到"无新信息"分支。
    """
    s1 = db.Statement(speaker="A", claim="X", evidence=[_ev("https://a.example.com/1")])
    s2 = db.Statement(speaker="B", claim="Y", evidence=[_ev("https://b.example.com/2")])
    both = {"url:https://a.example.com/1", "url:https://b.example.com/2"}
    out = db.arbitrate([s1, s2], allow=True, user_asked=True, round_no=1, max_rounds=9,
                       prev_evidence=both)
    assert out["stop"]["stop"] is True and "无新信息" in out["stop"]["reason"], out["stop"]


def test_stops_when_marginal_gain_below_threshold():
    """有新证据、但**没有**新增"被核对过"的证据 ⇒ 边际增益不足 ⇒ 收口。"""
    s = db.Statement(speaker="A", claim="X", evidence=[_ev("https://a.example.com/9", verified=False)])
    s2 = db.Statement(speaker="B", claim="Y", evidence=[_ev("https://b.example.com/2")])
    out = db.arbitrate([s2, s], allow=True, user_asked=True, round_no=1, max_rounds=9,
                       prev_evidence={"url:https://b.example.com/2"})
    assert out["stop"]["stop"] is True and "边际" in out["stop"]["reason"], out["stop"]


def test_stops_when_evidence_chain_closes():
    s1 = db.Statement(speaker="A", claim="同一个结论", evidence=[_ev("https://a.example.com/1")])
    s2 = db.Statement(speaker="B", claim="同一个结论", evidence=[_ev("https://a.example.com/1")])
    out = db.arbitrate([s1, s2], allow=True, user_asked=True, round_no=1, max_rounds=9)
    assert out["stop"]["stop"] is True and "闭环" in out["stop"]["reason"], out["stop"]


# ── 输出四件套 + 一处真相 ──────────────────────────────────────────────────
def test_output_has_all_four_pieces():
    s1 = db.Statement(speaker="A", claim="X", evidence=[_ev("https://a.example.com/1")])
    s2 = db.Statement(speaker="B", claim="Y", evidence=[_ev("https://b.example.com/2")])
    out = db.arbitrate([s1, s2], allow=True, user_asked=True)
    o = out["output"]
    assert set(o) == {"conclusion", "confidence", "key_evidence", "disagreements"}
    assert o["disagreements"], "两个独立立场之间应当报出分歧点"
    assert 0.0 <= o["confidence"] <= 1.0
    assert o["key_evidence"]


def test_confidence_is_labelled_as_heuristic_not_probability():
    """置信度必须**带着"这是启发式示意值"的说明**一起回报（不许对外宣称成概率）。"""
    s = db.Statement(speaker="A", claim="X", evidence=[_ev("https://a.example.com/1")])
    out = db.arbitrate([s], allow=True, user_asked=True)
    assert any("启发式" in n for n in out["notes"]), out["notes"]
    assert any("9 倍" in n for n in out["notes"]), "成本口径也要如实带上（别装作免费）"
