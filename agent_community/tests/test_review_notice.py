# -*- coding: utf-8 -*-
"""守卫（2026-11-08）：**验收结果要通知到成员**（`platform/review_notice`）。

起因：`review` 以前只改账本 —— 组长点「返工」，**成员一个字都收不到**，
他不会知道要改，这条活儿就死在账本里（"失败不发声"）。

判据：

  ① 返工/拒收/通过 三种都有人话标题，且各自说清"让他做什么"；
  ② 通知里带**任务号**与**组长原话**（有 note 时）；
  ③ 通知里带**验收人**（署名可追）；
  ④ 没 `slot_dir` 的任务 ⇒ 不假成功，`ok=False` 且说清为什么；
  ⑤ 没有工作目录 ⇒ 同样如实返回（不写空文件）。
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.append(str(_ROOT))

from agent_community.platform import review_notice as RN  # noqa: E402


def test_01_three_results_have_titles_and_next_step():
    for res in ("返工", "拒收", "通过"):
        txt = RN.text_for(res, "T-002")
        assert "T-002" in txt and "【" in txt, txt
    assert "改完再交" in RN.text_for("返工", "T-002")
    assert "拒收" in RN.text_for("拒收", "T-002")


def test_02_carries_note_and_task_id():
    txt = RN.text_for("返工", "T-007", "按钮点不动，补个用例")
    assert "T-007" in txt and "按钮点不动，补个用例" in txt and "组长原话" in txt


def test_03_carries_reviewer():
    txt = RN.text_for("通过", "T-001", "", "_lead")
    assert "_lead" in txt and "验收人" in txt


def test_04_no_slot_is_not_fake_success():
    class _WS:
        workspace_dir = "C:/tmp/x"

    out = RN.notify("ws", _WS(), {"task_seq": 1, "slot_dir": ""}, result="返工")
    assert out["ok"] is False and "slot_dir" in out["why"], out


def test_05_no_workspace_dir_is_reported():
    class _WS:
        workspace_dir = ""

    out = RN.notify("ws", _WS(), {"task_seq": 1, "slot_dir": "前端组/员工01"}, result="返工")
    assert out["ok"] is False and "工作目录" in out["why"], out


def test_06_unknown_result_still_says_something():
    txt = RN.text_for("随便写的", "T-009")
    assert "T-009" in txt and txt.strip()
