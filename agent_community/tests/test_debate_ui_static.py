# -*- coding: utf-8 -*-
"""辩论模式的**前端接线**守卫（`V52`）：按结构判，不看行号。

为什么要有这一层：界面入口是"可开可关"落到**用户手里**的那一步（产品化准则③ 用户视角）。
但前端最容易犯的两种错**看不见**：
  ① 面板加了、却没挂进加载流程 ⇒ 用户永远看到"关"（静默失效）；
  ② **在前端自己重算权重/分数/阈值** ⇒ 界面上显示的与后端裁决书**两套口径**
     （这正是规则 20 要防的"同一件事两份口径"）。
本守卫就盯这两条。

（JS **语法**检查在 `temp/check_workshop_ui.js`（node，`new Function` 只解析不执行）——
 CI 里没有 node，所以那一层留在开发侧；这里用 Python 跑**结构**断言，CI 能跑。）
"""
from __future__ import annotations

import os
import re
import sys

_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
if _ROOT not in sys.path:
    sys.path.append(_ROOT)

HTML = os.path.join(_ROOT, "agent_community", "frontend", "workshop.html")

_NEED = [
    ("辩论条容器", 'id="debateBar"'),
    ("状态显示", 'id="debateState"'),
    ("开关按钮", 'id="debateToggleBtn"'),
    ("陈述表单", 'id="debateForm"'),
    ("说话者输入", 'id="debateSpeaker"'),
    ("结论输入", 'id="debateClaim"'),
    ("证据输入", 'id="debateEvidence"'),
    ("读状态函数", "function loadDebate("),
    ("开关函数", "function toggleDebate("),
    ("提交陈述函数", "function submitDebateStatement("),
    ("跑裁决函数", "function runDebate("),
    ("陈述端点", "/debate/statements"),
    ("裁决端点", "/debate/run"),
]


def _html() -> str:
    with open(HTML, encoding="utf-8") as f:
        return f.read()


def test_panel_and_functions_are_all_present():
    src = _html()
    missing = [name for name, key in _NEED if key not in src]
    assert not missing, "前端缺少这些部件：%s（面板加了没接线 = 用户永远看到「关」）" % missing


def test_panel_is_wired_into_the_load_flow():
    """★最要紧的一条：**必须**在加载工作间时读一次状态。

    只加面板、不挂进 `loadWorkshop()` ⇒ 用户看到的永远是"关"，
    而开关其实在后端是开的 —— 典型的静默不一致。
    """
    src = _html()
    assert "loadDebate();" in src, "loadDebate() 没被调用过：面板不会反映真实状态"
    # 且调用的位置在 loadWorkshop 的函数体里（结构判据：取该函数体这一段来查）
    m = re.search(r"function loadWorkshop\(id\)\{", src)
    assert m, "找不到 loadWorkshop（前端结构变了，请同步本守卫）"
    body = src[m.start():m.start() + 6000]
    assert "loadDebate();" in body, "loadDebate() 不在 loadWorkshop() 里调用"


def test_panel_sits_in_the_level3_discussion_area():
    """面板必须长在**三级讨论区**那一带（V52 §1：辩论只在三级讨论里出现）。"""
    src = _html()
    level3 = src.find("💬 三级讨论")
    bar = src.find('id="debateBar"')
    form = src.find('id="debateForm"')
    assert level3 != -1 and bar != -1, "三级讨论标题或辩论面板不存在"
    assert level3 < bar < form, "辩论面板不在三级讨论区之后（位置错了）"


def test_frontend_does_not_recompute_the_verdict():
    """前端**不许**自己算 Jaccard / 分数 / 阈值 —— 否则界面上是第二套口径。

    只检查**本功能那段 JS**（`debateInfo` 到 `function startReview`），避免误伤页面别处的数字。
    """
    src = _html()
    a = src.find("let debateInfo")
    b = src.find("function startReview(){")
    assert a != -1 and b != -1 and a < b, "找不到辩论那段 JS（前端结构变了，请同步本守卫）"
    block = src[a:b]
    for bad, why in (("jaccard", "前端自己算证据同源度"), ("threshold", "前端自己设阈值"),
                     ("score_cluster", "前端自己算轨迹分")):
        assert bad not in block.lower(), "前端重算了裁决逻辑（%s）—— 会与后端裁决书变成两套口径" % why


def _slice(src: str, a: str, b: str) -> str:
    """取 `a` 到 `b` 之间那一段（结构定位，不用行号）。"""
    i = src.find(a)
    assert i != -1, "找不到锚点：%s（前端结构变了，请同步本守卫）" % a
    j = src.find(b, i)
    assert j != -1, "找不到结束锚点：%s" % b
    return src[i:j]


def test_default_off_wording_is_honest():
    """默认关这件事必须在**用户看得见的两处**都说出来（不许只体现在接口默认值里）。

    ⚠️ 本守卫**被突变体抓过一次**：第一版只查"整个文件里有没有『默认关』"，
    而注释里也有 ⇒ 把界面文案删干净它照样绿（**因为错误的原因通过**）。
    现在只查**面板初始文案**与**状态刷新函数里的文案**这两处用户真正看到的东西。
    """
    src = _html()
    panel = _slice(src, 'id="debateBar"', 'id="debateForm"')
    assert "默认关" in panel, "面板初始文案没写「默认关」（用户会以为平台会自己开辩论）"
    js = _slice(src, "function paintDebateBar(", "function openDebateForm(")
    assert "默认关" in js, "状态刷新后的文案没写「默认关」（刷新一次口径就不一致了）"


def test_evidence_free_statements_are_flagged_in_the_ui():
    """没有证据的陈述要**当场提示权重为 0**（V52 §5.1：不许显示成"参与裁决"）。

    同样只查**用户可见的两处**：提交表单的说明文案、提交成功后的提示。
    """
    src = _html()
    form = _slice(src, 'id="debateForm"', "function loadDebate(")
    assert "权重为 0" in form, "表单里没提示「没有证据 ⇒ 权重为 0」"
    js = _slice(src, "async function submitDebateStatement(", "async function toggleDebate(")
    assert "权重为 0" in js, "提交成功后的提示没告诉用户「这条没有证据、权重为 0」"
