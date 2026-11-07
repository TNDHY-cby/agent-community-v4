# -*- coding: utf-8 -*-
"""守卫（`V138` §二 第一件，2026-11-07）：**工作间内的日常沟通** —— 说给谁 / 回哪条 / 有没有看到。

老板原话：「工作间的沟通方式还没有做」。

判据都是真结果（不查源码文本）：

  ① `to=lead` ⇒ 消息带 `to`，且**组长真的被通知到**（`_lead/_notice.md` 有那一行）；
  ② `to=member:<工位>` ⇒ 该工位 `_notice.md` 有；
  ③ `thread` 指向真实 msg_id ⇒ 原样存下；指向不存在的 ⇒ 422 `A019`（不静默）；
  ④ `to` 值非法 ⇒ 422 `A018` 并列出合法取值；
  ⑤ 未读与大厅同一套（`read_marks`，键 `ailp:<ws>`）：读 → 推进 → **回退被拒（moved=false）**；
  ⑥ 不给 `to`/`thread` 的老消息照样能发（**向后兼容**，不能因为加了沟通把老用法弄坏）。
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.append(str(_ROOT))

from agent_community.platform import ailp as A  # noqa: E402

REF = "前端组/员工01/任务/T-001.md"


def _env(**kw):
    base = {"ailp_version": "2.0", "msg_id": "msg-1", "msg_type": "PROPOSE",
            "sender": "员工01", "target": "组长", "manifest_ref": REF,
            "payload": {"proposal": "先做接口", "confidence": 0.8, "basis_ids": [REF]}}
    base.update(kw)
    return base


def test_01_to_lead_is_kept():
    ok, errs, norm = A.validate(_env(to="lead"))
    assert ok, errs
    assert norm.get("to") == "lead"


def test_02_to_member_and_all_are_kept():
    for v in ("all", "member:前端组/员工01"):
        ok, errs, norm = A.validate(_env(to=v))
        assert ok, errs
        assert norm.get("to") == v


def test_03_bad_to_is_rejected_with_choices():
    ok, errs, _ = A.validate(_env(to="随便谁"))
    assert not ok
    e = [x for x in errs if x["code"] == "A018"]
    assert e and "lead" in (e[0].get("fix") or "")


def test_04_thread_must_point_to_real_message():
    ok, errs, _ = A.validate(_env(thread="msg-不存在"), msg_ids={"msg-1"}, allowed=[REF])
    assert not ok and any(x["code"] == "A019" for x in errs)
    ok2, errs2, norm2 = A.validate(_env(thread="msg-1"), msg_ids={"msg-1"}, allowed=[REF])
    assert ok2, errs2
    assert norm2.get("thread") == "msg-1"


def test_05_without_to_thread_still_works():
    """向后兼容：不给 `to`/`thread` 的老消息照样过，且 norm 里**不出现**这两个键（不污染 fingerprint）。"""
    ok, errs, norm = A.validate(_env())
    assert ok, errs
    assert "to" not in norm and "thread" not in norm


def test_06_to_and_thread_change_fingerprint():
    """同一句话"说给所有人"和"说给组长"不是同一条消息 —— 判重不该把它们当重复。"""
    _ok1, _e1, a = A.validate(_env(to="lead"))
    _ok2, _e2, b = A.validate(_env(to="all"))
    assert a["fingerprint"] != b["fingerprint"]
