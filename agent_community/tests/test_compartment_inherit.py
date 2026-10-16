# -*- coding: utf-8 -*-
"""V53 守卫：隔间「可被继任员工继承」—— **11 条判据**（老板 2026-10-17 拍板「可以」）。

判据全部**按真结果**判（真建隔间、真复制、真核对字节），不查源码文本。
口径见 `design-docs/V53_隔间可被继任员工继承设计.md` §2 五条 + §5 判据表。

跑法：`python -m pytest agent_community/tests/test_compartment_inherit.py -q`
突变检验：`python temp/mutation_check_inherit.py`（三处突变必须被杀）
"""
from __future__ import annotations

import hashlib
import json

import pytest

from agent_community.platform import inherit as inh
from agent_community.platform.compartment import compartment_dir


def _sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


@pytest.fixture()
def ws(tmp_path):
    """一个工作间目录 + 两个成员（旧/新）的隔间。"""
    ws_dir = tmp_path / "ws_demo"
    ws_dir.mkdir()
    old = compartment_dir(ws_dir, "码农", "m_old")
    new = compartment_dir(ws_dir, "码农", "m_new")
    old.mkdir(parents=True)
    new.mkdir(parents=True)
    (old / "output").mkdir()
    (old / "temp").mkdir()
    (old / "inbox").mkdir()
    (old / "replies").mkdir()
    (old / "output" / "plan.md").write_text("旧成员的方案\n", encoding="utf-8")
    (old / "output" / "notes.md").write_text("笔记\n", encoding="utf-8")
    (old / "input" / "spec.md").write_text("需求\n", encoding="utf-8") if (old / "input").exists() else None
    (old / "input").mkdir(exist_ok=True)
    (old / "input" / "spec.md").write_text("需求\n", encoding="utf-8")
    (old / "temp" / "scratch.txt").write_text("草稿\n", encoding="utf-8")
    (old / "inbox" / "task_1.json").write_text('{"a":1}', encoding="utf-8")
    (old / "bridge.py").write_text("# 旧成员的桥\n", encoding="utf-8")
    (old / "activation_prompt.txt").write_text("唤醒词\n", encoding="utf-8")
    return {"dir": ws_dir, "old": old, "new": new}


def _inherit(ws, **kw):
    return inh.inherit(str(ws["old"]), str(ws["new"]), str(ws["dir"]),
                       {"member_id": "m_old", "role": "码农", "display_name": "老王"}, "ws_demo", **kw)


# ── 判据 1：不指定来源 ⇒ 不搬、不写继承文（路由层判；内核层等效于"来源不存在就不动"）────────
def test_1_missing_source_does_not_touch_destination(ws):
    out = inh.inherit(str(ws["dir"] / "职位_不存在"), str(ws["new"]), str(ws["dir"]),
                      {"member_id": "x"}, "ws_demo")
    assert out["success"] is False and out["verified"] is False
    assert "不存在" in out["reason"]
    assert list(ws["new"].iterdir()) == [], "失败时目标隔间不许被写任何东西（含 legacy.md）"


# ── 判据 2：指定存在的来源 ⇒ 产物到位，且**逐项核对**（存在 + sha256 一致）────────────
def test_2_products_arrive_and_are_verified(ws):
    out = _inherit(ws)
    assert out["success"] is True and out["verified"] is True
    moved = {m["path"] for m in out["moved"]}
    assert {"output/plan.md", "output/notes.md", "input/spec.md"} <= moved, moved
    for rel in ("output/plan.md", "output/notes.md", "input/spec.md"):
        assert _sha(ws["new"] / rel) == _sha(ws["old"] / rel), "搬过去必须字节一致：%s" % rel


# ── 判据 3：继承文必含 来源 / 时间 / 已搬清单 / 未搬清单与原因 ────────────────────────
def test_3_legacy_document_is_complete(ws):
    out = _inherit(ws)
    text = (ws["new"] / "legacy.md").read_text(encoding="utf-8")
    assert "m_old" in text and "老王" in text, "来源必须写清（id + 显示名）"
    assert "继承时间" in text
    assert "output/plan.md" in text, "已搬运清单要在"
    assert "temp/scratch.txt" in text and "inbox/task_1.json" in text, "未搬项要逐条列出"
    assert "不继承" in text, "要说清哪些类别不继承（否则看的人以为漏了）"
    assert str(out["legacy_path"]).endswith("legacy.md")


# ── 判据 4：旧隔间**原样保留**（继承前后：文件清单与字节完全不变）──────────────────────
def test_4_source_compartment_is_untouched(ws):
    before = {p.relative_to(ws["old"]).as_posix(): _sha(p)
              for p in ws["old"].rglob("*") if p.is_file()}
    _inherit(ws)
    after = {p.relative_to(ws["old"]).as_posix(): _sha(p)
             for p in ws["old"].rglob("*") if p.is_file()}
    assert before == after, "继承是「追加」：不许删/改旧隔间里的任何东西"


# ── 判据 5：超单文件上限 ⇒ 进"未搬运"且原因可读；**不许**静默丢、不许整体失败 ─────────────
def test_5_oversize_single_file_is_skipped_with_reason(ws):
    big = ws["old"] / "output" / "big.bin"
    big.write_bytes(b"x" * (2 * 1024 * 1024))          # 2 MiB
    out = _inherit(ws, max_file_mb=1.0)                 # 上限 1 MiB
    skipped = {s["path"]: s["reason"] for s in out["skipped"]}
    assert "output/big.bin" in skipped and "单文件上限" in skipped["output/big.bin"]
    assert not (ws["new"] / "output" / "big.bin").exists()
    assert out["success"] is True, "跳过一项不该让整体失败（其余照搬）"
    assert (ws["new"] / "output" / "plan.md").exists()
    assert "big.bin" in (ws["new"] / "legacy.md").read_text(encoding="utf-8")


# ── 判据 6：超总量上限 ⇒ 同样跳过（已搬的保留，不回滚——回滚会连"搬了一半"都看不见）─────────
def test_6_total_limit_skips_but_keeps_what_moved(ws):
    (ws["old"] / "output" / "a.bin").write_bytes(b"a" * 700 * 1024)
    (ws["old"] / "output" / "b.bin").write_bytes(b"b" * 700 * 1024)
    out = _inherit(ws, max_file_mb=5.0, max_total_mb=1.0)   # 总量 1 MiB
    moved = {m["path"] for m in out["moved"]}
    skipped = {s["path"]: s["reason"] for s in out["skipped"]}
    assert moved, "第一项应当搬成功（不是一超限就全不搬）"
    assert any("总量上限" in r for r in skipped.values()), skipped
    for rel in moved:
        assert (ws["new"] / rel).exists()


# ── 判据 7：指名不存在的来源 ⇒ 如实失败（内核层；路由层是 404）───────────────────────
def test_7_nonexistent_source_fails_loudly(ws):
    out = inh.inherit(str(ws["dir"] / "码农_m_ghost"), str(ws["new"]), str(ws["dir"]),
                      {"member_id": "m_ghost"}, "ws_demo")
    assert out["success"] is False and "不存在" in out["reason"]


# ── 判据 8：跨工作间 ⇒ 拒绝 + 原因 ─────────────────────────────────────────────
def test_8_cross_workshop_is_refused(ws, tmp_path):
    other = tmp_path / "ws_other"
    other.mkdir()
    src = compartment_dir(other, "码农", "m_x")
    src.mkdir(parents=True)
    (src / "output").mkdir()
    (src / "output" / "plan.md").write_text("别的间的产物\n", encoding="utf-8")
    out = inh.inherit(str(src), str(ws["new"]), str(ws["dir"]), {"member_id": "m_x"}, "ws_demo")
    assert out["success"] is False and "跨间" in out["reason"]
    assert not (ws["new"] / "output" / "plan.md").exists()


# ── 判据 9：幂等——目标已存在且一致 ⇒ 跳过（不重复堆叠）；不一致 ⇒ 不覆盖 ────────────────
def test_9_idempotent_and_never_clobbers(ws):
    _inherit(ws)
    # 9a：再来一次，内容一致的项应当"跳过"而不是再搬一次
    out2 = _inherit(ws)
    moved2 = {m["path"] for m in out2["moved"]}
    skipped2 = {s["path"]: s["reason"] for s in out2["skipped"]}
    assert "output/plan.md" not in moved2, "内容一致 ⇒ 幂等跳过，不该重复堆叠"
    assert "幂等跳过" in skipped2.get("output/plan.md", "")
    # 9b：继任者自己改过这个文件 ⇒ **不许覆盖**
    (ws["new"] / "output" / "plan.md").write_text("继任者自己的新方案\n", encoding="utf-8")
    out3 = _inherit(ws)
    skipped3 = {s["path"]: s["reason"] for s in out3["skipped"]}
    assert "内容不同" in skipped3.get("output/plan.md", ""), skipped3
    assert (ws["new"] / "output" / "plan.md").read_text(encoding="utf-8") == "继任者自己的新方案\n", \
        "绝不覆盖继任者自己的东西"


# ── 判据 10：旧成员已被删除（harness 没了）但隔间还在 ⇒ 仍可继承 ───────────────────────
def test_10_inherit_works_when_source_member_is_gone(ws):
    """隔间**跨通道存活**：成员记录没了、隔间文件还在 ⇒ 继承照样成立（这里直接调内核，
    路由层"成员不存在"是另一条判据——二者不同，别混）。"""
    out = _inherit(ws)
    assert out["success"] is True and (ws["new"] / "output" / "plan.md").is_file()
    # 且：桥/身份产物**不在**搬运清单里
    moved = {m["path"] for m in out["moved"]}
    assert "bridge.py" not in moved and "activation_prompt.txt" not in moved
    assert "inbox/task_1.json" not in moved, "邮箱是寻址，不继承"


# ── 判据 11：只读的 `plan_inherit` 不写盘 ─────────────────────────────────────────
def test_11_plan_is_read_only(ws):
    before = {p.relative_to(ws["new"]).as_posix(): _sha(p)
              for p in ws["new"].rglob("*") if p.is_file()}
    plan = inh.plan_inherit(str(ws["old"]), str(ws["new"]))
    assert plan["move"] and plan["skipped"]
    after = {p.relative_to(ws["new"]).as_posix(): _sha(p)
             for p in ws["new"].rglob("*") if p.is_file()}
    assert before == after == {}, "plan 是只读的：目标隔间在 plan 阶段必须原封不动"
    assert json.dumps(plan, ensure_ascii=False), "计划要可序列化（接口要回给前端）"
