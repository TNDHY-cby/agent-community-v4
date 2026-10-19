# -*- coding: utf-8 -*-
"""V55 守卫：信息卡（`kind="evidence"`）的**硬约束 / 分档 / 归并 / 限量分发**，以及三个端点。

设计稿：`design-docs/V55_信息搜索者与防信息污染设计.md`（老板 2026-10-17「最优解」拍板）。

判据（全部按真结果，不看源码文本）：
  ① 硬约束：没来源 / 没时效 / 没 limits / 摘录超 200 字 / 整卡超 1200 字 ⇒ **拒收**，且**每条都说清怎么改**；
  ② **三条污染样例必须被标出来**：
       A「3 家转载同一稿」⇒ 归并成 1 个来源组（count=3），不当 3 条独立证据；
       B「两张卡互相矛盾」⇒ 两张都 `disputed=true` 且互链；
       C「营销稿冒充一手」⇒ 档位 marketing、**自评 high 被压到 low 并说明原因**；
  ③ **限量再分发**：广播出去的文本里**不含摘录原文**（按真广播文本判）；
  ④ 端点：不合规 422（带 problems）、GET 默认不给 quote、`?full=1` 才给、未绑 harness 委派 400。
"""
from __future__ import annotations

import asyncio
import json
import os
import sys

import pytest

_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
if _ROOT not in sys.path:
    sys.path.append(_ROOT)

from agent_community.platform import evidence as ev                      # noqa: E402
from agent_community.platform import state as _state                    # noqa: E402
from agent_community.platform.routers import workshops_evidence as wev  # noqa: E402
from agent_community.platform.workshop import Workshop, WorkshopMember  # noqa: E402


# ── 工具 ────────────────────────────────────────────────────────────────
def _card(**kw) -> dict:
    base = {
        "name": "CUDA 12.4 支持 sm_90a 吗",
        "note": "支持，但需要 12.4.1 补丁",
        "evidence": {
            "as_of": "2026-10-17", "confidence": "high",
            "limits": "没查到 12.4.0 的官方说明",
            "sources": [{"url": "https://docs.example.com/cuda/12.4", "title": "CUDA 12.4 Release Notes",
                         "publisher": "示例官方文档", "quoted_at": "2026-10-17",
                         "quote": "sm_90a is supported starting from 12.4.1", "primary": True}],
        },
    }
    base.update(kw)
    return base


# ── ① 硬约束 ────────────────────────────────────────────────────────────
def test_validate_accepts_a_good_card():
    ok, errs, norm = ev.validate(_card())
    assert ok and errs == []
    assert norm["kind"] == "evidence" and len(norm["evidence"]["sources"]) == 1


@pytest.mark.parametrize("mutate,expect", [
    (lambda c: c["evidence"].pop("as_of"), "as_of"),
    (lambda c: c["evidence"].pop("limits"), "limits"),
    (lambda c: c["evidence"].__setitem__("sources", []), "至少 1 条"),
    (lambda c: c["evidence"]["sources"][0].pop("url"), "url 必填"),
    (lambda c: c["evidence"]["sources"][0].pop("quote"), "quote 必填"),
    (lambda c: c["evidence"]["sources"][0].__setitem__("quote", "长" * 201), "超过 200 字"),
    (lambda c: c.__setitem__("name", "标" * 61), "超过 60 字"),
    (lambda c: c.__setitem__("note", "结" * 121), "超过 120 字"),
    (lambda c: c["evidence"].__setitem__("confidence", "很高"), "只能是 high"),
])
def test_validate_rejects_and_says_how_to_fix(mutate, expect):
    c = _card()
    mutate(c)
    ok, errs, _ = ev.validate(c)
    assert not ok, "这条该被拒收：%s" % expect
    assert any(expect in e for e in errs), "拒收理由没说清（期望含『%s』）：%s" % (expect, errs)


def test_validate_rejects_oversized_card():
    c = _card()
    c["evidence"]["sources"] = [{"url": "https://a.example/1", "quote": "摘" * 200} for _ in range(7)]
    ok, errs, _ = ev.validate(c)
    assert not ok and any("整卡" in e for e in errs), errs


# ── ②-C 质量分档：营销稿不许自评 high ───────────────────────────────────
def test_marketing_source_caps_confidence_with_reason():
    c = _card()
    c["evidence"]["confidence"] = "high"
    c["evidence"]["sources"] = [{"url": "https://vendor.example/product/promo?utm_x=1",
                                 "title": "我们的产品最好", "publisher": "某厂商",
                                 "quoted_at": "2026-10-17", "quote": "业界第一"}]
    ok, _, norm = ev.validate(c)
    assert ok
    graded = ev.grade_sources(norm["evidence"]["sources"])
    assert graded["sources"][0]["grade"] == "marketing"
    conf = ev.effective_confidence(norm["evidence"]["confidence"], graded["cap"])
    assert conf["effective"] == "low" and conf["capped"] is True
    assert "营销" in conf["why"] or "档位" in conf["why"], "压低要说清为什么：%s" % conf


def test_primary_keeps_high_and_anon_is_low():
    ok, _, norm = ev.validate(_card())
    g = ev.grade_sources(norm["evidence"]["sources"])
    assert g["sources"][0]["grade"] == "primary" and g["cap"] == "high"
    anon = ev.grade_sources([{"url": "https://blog.example/x", "publisher": "", "quoted_at": ""}])
    assert anon["sources"][0]["grade"] == "anon" and anon["cap"] == "low"


# ── ②-A 三条转载 ⇒ 一个来源组 ────────────────────────────────────────────
def test_reprints_merge_into_one_group():
    same = "官方确认：sm_90a 从 12.4.1 起受支持，12.4.0 不支持"
    sources = [
        {"url": "https://news-a.example/x/1", "title": "CUDA 12.4 更新", "quote": same},
        {"url": "https://news-b.example/y/2", "title": "CUDA 12.4 更新", "quote": same},
        {"url": "https://news-c.example/z/3", "title": "CUDA 12.4 更新", "quote": same},
    ]
    groups = ev.merge_duplicates(sources)
    assert len(groups) == 1, "3 家转载同一稿必须归并成 1 组，实际 %d 组" % len(groups)
    assert groups[0]["count"] == 3


def test_different_stories_are_not_merged():
    groups = ev.merge_duplicates([
        {"url": "https://a.example/1", "title": "CUDA 12.4 更新", "quote": "sm_90a 从 12.4.1 起支持"},
        {"url": "https://b.example/2", "title": "另一个话题", "quote": "完全无关的另一段内容在这里出现"},
    ])
    assert len(groups) == 2


# ── ③ 限量再分发：摘要里不许有摘录原文 ──────────────────────────────────
def test_summary_has_no_quotes_and_flags_dispute():
    ok, _, norm = ev.validate(_card())
    norm["evidence"]["disputed"] = True
    s = ev.render_summary(norm, rid="abc12345")
    assert "CUDA 12.4 支持 sm_90a 吗" in s and "支持，但需要 12.4.1 补丁" in s
    assert "sm_90a is supported starting from 12.4.1" not in s, "摘录原文漏进广播＝污染"
    assert "有争议" in s and "rid=abc12345" in s
    assert ev.summary_has_quotes(norm, s) is False


# ── ⑤ 委派话术：纯文本、无 markdown 星号、把回报格式钉死 ────────────────
def test_search_instruction_is_plain_and_specifies_report_format():
    txt = ev.search_instruction("CUDA 12.4 支持 sm_90a 吗", ["官方文档", "发布日期"], 30,
                                base_url="http://127.0.0.1:18920")
    assert "**" not in txt
    assert "CUDA 12.4 支持 sm_90a 吗" in txt and "官方文档" in txt and "30" in txt
    for kw in ("必须覆盖", "task-result", "as_of", "limits", "quote", "没找到"):
        assert kw in txt, "话术缺关键项：%s" % kw
    for kw in ("没有来源的结论不要写", "整段搬", "转载", "冲突"):
        assert kw in txt, "话术缺纪律：%s" % kw


# ── 端点层 ──────────────────────────────────────────────────────────────
class _Req:
    def __init__(self, body=None, base="http://127.0.0.1:18920/"):
        self._body = body if body is not None else {}
        self.base_url = base

    async def json(self):
        return self._body


def _resp(resp):
    if isinstance(resp, dict):
        return 200, resp
    body = getattr(resp, "body", b"")
    try:
        return getattr(resp, "status_code", 200), json.loads(body.decode("utf-8"))
    except Exception:  # noqa: BLE001
        return getattr(resp, "status_code", 200), {}


@pytest.fixture()
def ws(tmp_path, monkeypatch):
    from agent_community.platform import server as _srv
    w = Workshop(workshop_id="ws_ev", name="检索测试间", workspace_dir=str(tmp_path / "ws_ev"),
                 hall_content="h",
                 members=[WorkshopMember(member_id="m_s1", role="信息搜索者", display_name="小搜"),
                          WorkshopMember(member_id="m_nb", role="码农", display_name="没绑")])
    (tmp_path / "ws_ev").mkdir(parents=True, exist_ok=True)
    w.members[0].harness_ids = ["hid_s1"]
    _state.workshops["ws_ev"] = w
    said = []
    monkeypatch.setattr(wev, "write_resources_manifest", lambda _ws: None)
    monkeypatch.setattr(_srv, "save_state", lambda *a, **k: None, raising=False)
    monkeypatch.setattr(_srv, "_append_msg",
                        lambda _ws, kind, text, **kw: said.append(text), raising=False)
    monkeypatch.setattr(_srv, "_dispatch_to_harness", lambda hid, payload, kind="task": (True, "ok"),
                        raising=False)
    yield {"ws": w, "said": said}
    _state.workshops.pop("ws_ev", None)


def test_post_evidence_rejects_with_problems(ws):
    bad = _card()
    bad["evidence"]["sources"][0]["quote"] = "长" * 300
    code, body = _resp(asyncio.run(wev.post_evidence("ws_ev", _Req(bad))))
    assert code == 422 and body["problems"]
    assert any("200 字" in p for p in body["problems"])
    assert "how_to_fix" in body and ws["ws"].resources == [], "拒收的卡不许落进资源库"


def test_post_evidence_ok_broadcasts_summary_without_quotes(ws):
    code, body = _resp(asyncio.run(wev.post_evidence("ws_ev", _Req({**_card(), "uploader": "m_s1"}))))
    assert code == 200, body
    rid = body["resource"]["rid"]
    assert [r["rid"] for r in ws["ws"].resources] == [rid]
    assert body["resource"]["kind"] == "evidence"
    assert body["confidence"]["effective"] == "high"
    notice = ws["said"][-1]
    assert rid in notice and "信息卡" in notice
    assert "sm_90a is supported starting from 12.4.1" not in notice, "广播里漏了摘录原文"


def test_conflict_marks_both_sides(ws):
    a = _card(name="CUDA 12.4 支持 sm_90a 吗", note="支持", evidence={
        **_card()["evidence"], "limits": "没查 12.4.0"})
    _, body_a = _resp(asyncio.run(wev.post_evidence("ws_ev", _Req(a))))
    rid_a = body_a["resource"]["rid"]
    b = _card(name="CUDA 12.4 支持 sm_90a 吗", note="不支持", evidence={
        **_card()["evidence"], "limits": "只看了一家"})
    code, body_b = _resp(asyncio.run(wev.post_evidence("ws_ev", _Req({**b, "contradicts": [rid_a]}))))
    assert code == 200
    rid_b = body_b["resource"]["rid"]
    assert body_b["marked_disputed_with"] == [rid_a]
    cards = {r["rid"]: r for r in ws["ws"].resources}
    assert cards[rid_a]["evidence"]["disputed"] is True
    assert cards[rid_b]["evidence"]["disputed"] is True
    assert rid_a in cards[rid_b]["evidence"]["contradicts"]
    assert rid_b in cards[rid_a]["evidence"]["contradicts"]


def test_conflict_also_works_from_evidence_block(ws):
    """★2026-10-19（证据链端到端判据抓到）：`contradicts` 原先**只有 body 顶层生效**，
    而 `ev.validate()` 归一化的是 `evidence.contradicts` ⇒ 按**校验器格式**交的卡永远不标争议
    （接口照回 200、卡也进了，界面上看不出错）。现在两处都认，这条守住这个"两处口径"不再回来。
    """
    a = _card(name="卡片 A", note="说法一", evidence={
        **_card()["evidence"], "limits": "只看官方"})
    _, body_a = _resp(asyncio.run(wev.post_evidence("ws_ev", _Req(a))))
    rid_a = body_a["resource"]["rid"]
    b = _card(name="卡片 B", note="说法二", evidence={
        **_card()["evidence"], "limits": "只看第三方", "contradicts": [rid_a]})
    code, body_b = _resp(asyncio.run(wev.post_evidence("ws_ev", _Req(b))))
    assert code == 200
    assert body_b["marked_disputed_with"] == [rid_a], "校验器格式的 contradicts 必须生效"
    cards = {r["rid"]: r for r in ws["ws"].resources}
    assert cards[rid_a]["evidence"]["disputed"] is True


def test_get_hides_quotes_by_default(ws):
    asyncio.run(wev.post_evidence("ws_ev", _Req(_card())))
    code, body = _resp(asyncio.run(wev.list_evidence("ws_ev")))
    assert code == 200 and body["count"] == 1
    item = body["items"][0]
    assert "quote" not in item and "quotes" not in item, "默认路径不许带摘录原文"
    code2, body2 = _resp(asyncio.run(wev.list_evidence("ws_ev", full=1)))
    assert body2["items"][0]["quotes"][0]["quote"].startswith("sm_90a")


def test_search_request_without_harness_is_400(ws):
    code, body = _resp(asyncio.run(wev.post_search_request(
        "ws_ev", _Req({"member_id": "m_nb", "question": "查一下"}))))
    assert code == 400 and "未绑定 harness" in body["error"]


def test_search_request_dispatches_with_instruction(ws):
    code, body = _resp(asyncio.run(wev.post_search_request(
        "ws_ev", _Req({"member_id": "m_s1", "question": "CUDA 12.4 支持 sm_90a 吗",
                       "must_cover": ["官方文档"], "deadline_minutes": 20}))))
    assert code == 200 and body["dispatched"] is True
    assert "task-result" in body["instruction"] and "**" not in body["instruction"]
    assert ws["ws"].assignments["m_s1"][-1]["kind"] == "search_request"
