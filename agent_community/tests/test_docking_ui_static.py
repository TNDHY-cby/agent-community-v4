# -*- coding: utf-8 -*-
"""对接中心界面（`V34` P2）的静态守卫：**按结构判，不看行号**。

P2 是"只读总览"这一步，守卫盯的是 V34 §1/§9 里那几条**最容易做错**的：
  ① **两个入口、同一份实现**：`?ws=` 收窄到单间；**单间模式不许出现工作间选择器**（越权的口子）；
  ② **本页零写请求**（P2 只读）—— 白名单是 P3/P4 才需要的东西，P2 连 POST 都不该有；
  ③ **失败要说话**：后端中文 error 原样显示，**不许显示成"空列表"**；
  ④ **悬空引用要说**（成员引用了未登记的 harness 时不能装作没看见）；
  ⑤ **入口指向对**：大厅与工作间都指向对接中心；工作间的入口**必须带 `?ws=`**。
"""
from __future__ import annotations

import os
import re
import sys

_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
if _ROOT not in sys.path:
    sys.path.append(_ROOT)

FE = os.path.join(_ROOT, "agent_community", "frontend")


def _read(name: str) -> str:
    with open(os.path.join(FE, name), encoding="utf-8") as f:
        return f.read()


def test_page_and_script_exist_with_the_readonly_contract():
    html = _read("docking.html")
    js = _read("docking.js")
    assert 'src="docking.js"' in html, "docking.html 没挂上 docking.js"
    assert "/api/docking/overview" in js, "没有读那个唯一的只读总览端点"
    assert "dockingMain" in html and "dockBody" in html, "页面骨架缺关键容器"


def test_page_has_zero_write_requests():
    """★P2 只读：整页**不许有**任何写请求（POST/PUT/DELETE）。"""
    js = _read("docking.js")
    bad = re.findall(r"method\s*:\s*['\"](POST|PUT|DELETE|PATCH)['\"]", js, re.IGNORECASE)
    assert not bad, "P2 是只读总览，但出现写请求：%s" % bad


def test_single_workshop_mode_hides_the_workshop_picker():
    """★单间模式**不许**出现工作间选择器（否则工作间入口能看/动别的间 —— V34 §1/§9.6）。"""
    js = _read("docking.js")
    i = js.find("if (DOCK_GLOBAL){")
    assert i != -1, "找不到「总控才渲染选择器」的分支（前端结构变了，请同步本守卫）"
    j = js.find("} else {", i)
    assert j != -1, "找不到对应的 else 分支"
    branch = js[i:j]
    assert "pick.innerHTML" in branch, "选择器没有放在「只在总控」的分支里"
    assert "getElementById('wsPickerWrap')" in js, "选择器容器没绑定（pick 指向谁？）"
    tail = js[j:j + 300]
    assert "innerHTML = ''" in tail, "单间分支没有清掉选择器容器（会残留可点入口）"


def test_entry_scope_is_taken_from_the_url():
    js = _read("docking.js")
    assert "URLSearchParams" in js and "'ws'" in js, "没从 URL 读范围参数"
    # 总控里那个「按工作间收窄」的链接必须自己带 ?ws=（否则点进去还是总控）
    assert "docking.html?ws=" in js, "总控没给出带 ?ws= 的单间链接（点了会回到总控）"


def test_failures_are_shown_not_swallowed():
    """失败必须显示原因，**不许**把错误显示成"没有内容"（准则⑤）。

    ⚠️ 这里要断言**两处**：① 调用方真的取出了后端的 `error`（否则错误原因就丢了）；
    ② 渲染函数把那段原因显示出来（而不是替换成一句"暂无数据"）。
    """
    js = _read("docking.js")
    assert "d.error" in js, "没有取后端的 error 字段 —— 中文原因会被丢掉"
    m = re.search(r"function dockError\(msg\)\{(.*?)\n\}", js, re.S)
    assert m, "找不到 dockError（失败路径被删了？）"
    body = m.group(1)
    assert "esc(msg)" in body, "错误原因没有被渲染出来"
    assert "读不到" in body, "错误提示没有说清「读不到」，用户会以为是空的"


def test_dangling_bindings_are_surfaced():
    """悬空引用（成员指向了未登记的 harness）必须显式列出来。"""
    js = _read("docking.js")
    assert "dangling_bindings" in js, "没读悬空引用字段"
    assert "悬空引用" in js, "悬空引用没有在界面上显式说明"


def test_empty_state_guides_the_first_run():
    """空白引导（V34 §10 第 5 条：装好就能用）。"""
    js = _read("docking.js")
    assert "还没有登记任何外部 Agent" in js, "缺空白引导"
    assert "三步" in js, "空白引导没有给出可执行的三步"


# ── 入口接线（规则 20：一件事的对外表达只有一处真相，但入口要对）──────────────
def test_hall_entry_points_to_the_docking_center():
    """★大厅的**主入口按钮**必须指向对接中心。

    ⚠️ 本守卫被突变体抓过一次（M5 存活）：第一版只断言"文件里有 `/docking.html`"，
    而页面里**另一处提示链接**也有它 ⇒ 把主入口改回注册页依旧通过（**因为错误的原因通过**）。
    现在**盯住那个按钮本身**。
    """
    idx = _read("index.html")
    m = re.search(r"<button[^>]*onclick=\"location\.href='/docking\.html'\"[^>]*>([^<]*)</button>", idx)
    assert m, "大厅没有那个指向对接中心的主入口按钮（V34 §5 第 1 步：大厅入口改为对接中心）"
    assert "对接中心" in m.group(1), "按钮文案不是「对接中心」（与 V34 §0 的命名口径不一致）：%r" % m.group(1)


def test_workshop_entry_carries_the_workshop_id():
    """★工作间入口**必须带 `?ws=`**：不带就变成总控，能看别的间（串范围）。"""
    ws = _read("workshop.html")
    assert "dockingPill" in ws, "工作间没有对接中心入口"
    assert "docking.html?ws=" in ws, "工作间入口没带 ?ws= —— 会退化成总控（能越权看别的间）"
    m = re.search(r"function loadWorkshop\(id\)\{", ws)
    assert m, "找不到 loadWorkshop（前端结构变了，请同步本守卫）"
    body = ws[m.start():m.start() + 8000]
    assert "dockingPill" in body and "encodeURIComponent(id)" in body, \
        "入口的 href 没有在加载工作间时按 id 绑定"
