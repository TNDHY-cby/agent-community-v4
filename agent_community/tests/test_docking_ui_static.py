# -*- coding: utf-8 -*-
"""对接中心界面（`FE-26` 前台版）的静态守卫：**按结构判，不看行号**。

FE-26 把对接中心从"只读总览"升级成"前台"（第一屏＝功能模块 ＋ 脚本激活四屏 ＋ 档案详情），
守卫盯的是那几条**最容易做错**的：
  ① **两个入口、同一份实现**：`?ws=` 收窄到单间；**单间模式不许出现工作间选择器**（越权的口子）；
  ② **写请求白名单**（FE-26 §5 的 19 条）—— 多一条是偷偷扩权，少一条按钮点不动；
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


def _js_code(src: str) -> str:
    """去掉注释后的 JS（**判"代码里有没有"必须去注释** —— 否则注释里提一句就误报）。

    ⚠️ 这个助手被咬过**两次**，两次都是"别用天真办法剥注释"：
      ① 我写了句注释"`/discuss` 出现即 FAIL"，结果**那句注释本身**含 `/discuss` ⇒ 守卫判自己红；
      ② 第一版用 `re.sub(r"/\\*.*?\\*/", ...)` 剥块注释，而白名单里的
         `'/api/workshop/*/onboard-members'` **字符串里就含 `/*`** ⇒ 它把字符串后半段
         当注释删了，于是"动作路径不在白名单里"的**假红**。
      现在只剥**行锚定**的块注释（整行以 `/*` 开头、以 `*/` 结尾）与行注释 ——
      既够用（本文件的注释就是这么写的），又不会吃掉字符串里的 `/*`。
    """
    src = re.sub(r"(?m)^\s*/\*.*?\*/\s*$", "", src, flags=re.S)   # 整行块注释
    src = re.sub(r"(?m)^\s*\*.*$", "", src)                       # JSDoc 续行
    src = re.sub(r"(?m)//.*$", "", src)                           # 行注释
    return src


def test_page_and_script_exist_with_the_frontdesk_contract():
    html = _read("docking.html")
    js = _read("docking.js")
    assert 'src="docking.js"' in html, "docking.html 没挂上 docking.js"
    assert "/api/docking/overview" in js, "没有读那个只读总览端点"
    assert "dockingMain" in html and "dockBody" in html, "页面骨架缺关键容器"
    # ★FE-26：第一屏＝功能模块（锚点 + 前台样式文件都在）
    assert "data-dock-service" in html, "页面没有功能模块锚点（FE-26 §2a）"
    assert 'href="docking.css"' in html, "前台样式文件没挂上（docking.css）"


def test_write_whitelist_matches_the_design_contract():
    """★写请求白名单必须**正好**是 FE-26 §5 那一份（19 条）。

    对接中心能调的写端点是有明确清单的：多一条就是"偷偷扩大了权限面"，
    少一条就是"界面上的按钮会点不动"。
    """
    js = _read("docking.js")
    code = _js_code(js)
    m = re.search(r"DOCK_WRITE_WHITELIST\s*=\s*\[(.*?)\]", code, re.S)
    assert m, "找不到写请求白名单（FE-26 §5 要求白名单外零写请求）"
    got = set(re.findall(r"['\"]([^'\"]+)['\"]", m.group(1)))
    expect = {
        "/api/harness/pre-register", "/api/harness/probe-register", "/api/harness/register",
        "/api/workshop/*/onboard-members", "/api/workshop/*/recover-scan",
        "/api/workshop/*/member/*/establish-session",
        "/api/workshop/*/member/*/deliver-bridge",
        "/api/workshop/*/member/*/recover",
        "/api/workshop/*/member/*/channel/retire",
        "/api/workshop/*/member/*/channel/establish",
        # 架桥辅助（V34 §2 明列要迁进对接中心的三个写动作）
        "/api/harness/bridge-path", "/api/harness/bridge-verify", "/api/harness/auto-connect",
        # 平台 Agent 直聊（V34 §5 登记过：属"协助架桥"，进对接中心白名单）
        "/api/assistant/chat",
        # 脚本激活工作台（FE-26 §5 + §12：定位/试投/生成正文/一键激活/档案读写）
        "/api/docking/locate", "/api/docking/try-activate",
        "/api/docking/compose-script", "/api/docking/run-script",
        "/api/docking/harness/*",
    }
    assert got == expect, "白名单与 FE-26 §5 不一致：多=%s 少=%s" % (sorted(got - expect), sorted(expect - got))


def test_every_action_path_is_inside_the_whitelist():
    """界面上的**每一个**动作路径都必须落在白名单里（否则按钮会被自己的闸拦住）。"""
    code = _js_code(_read("docking.js"))
    m = re.search(r"DOCK_PATH\s*=\s*\{(.*?)\};", code, re.S)
    assert m, "找不到 DOCK_PATH 路径表（动作路径的唯一来源）"
    paths = re.findall(r"['\"]([^'\"]*\{ws\}[^'\"]*)['\"]", m.group(1))
    assert paths, "DOCK_PATH 里没有带 {ws} 的路径？"
    wl = _read("docking.js")
    wl = re.search(r"DOCK_WRITE_WHITELIST\s*=\s*\[(.*?)\]", _js_code(wl), re.S).group(1)
    allowed = set(re.findall(r"['\"]([^'\"]+)['\"]", wl))

    def norm(p):
        return p.replace("{ws}", "*").replace("{mid}", "*")

    outside = [p for p in paths if norm(p) not in allowed]
    assert not outside, "这些动作路径不在白名单里（按钮会被闸拦住）：%s" % outside


def test_bridge_helpers_are_per_harness_not_per_member_path():
    """架桥辅助按 **harness** 作用 ⇒ 路径里**不该**带 `{ws}`/`{mid}`（参数走 body）。"""
    code = _js_code(_read("docking.js"))
    m = re.search(r"DOCK_PATH\s*=\s*\{(.*?)\};", code, re.S)
    block = m.group(1)
    for key in ("bridgePath", "bridgeVerify", "autoConnect"):
        mm = re.search(key + r"\s*:\s*['\"]([^'\"]+)['\"]", block)
        assert mm, "DOCK_PATH 里没有 %s" % key
        assert "{ws}" not in mm.group(1) and "{mid}" not in mm.group(1), \
            "%s 的路径带了 ws/mid（架桥辅助是按 harness 作用的）：%s" % (key, mm.group(1))
    act = _js_code(_read("docking.js"))
    i = act.find("async function dockBridgeAct(")
    assert i != -1 and "harness_id" in act[i:i + 400], "dockBridgeAct 没有把 harness_id 放进 body"


def test_legacy_register_page_is_a_readonly_mirror_not_the_entry():
    """V34 §5 第二步：旧注册页**不再是入口**，但按设计稿允许的第二个选项**留作只读镜像**。

    ⚠️ **本守卫的来历（一次真事故）**：我一度把 `register.html` 整个换成重定向页 ——
    结果**静默删掉了产品内容**（注册模板的字段契约、自测题 Q1~Q5、按桥型的架桥匹配表），
    是**四条既有守卫当场变红**才发现的（`test_template_contract` / `test_v44_batch1_fixes`）。
    而这批内容**不能抄进对接中心**：抄一遍就是"同一件事两份口径"（规则 20 明令禁止）。
    ⇒ 结论：**留只读镜像**（V34 §5 原文允许），内容原地保留、入口改向。

    判据：① 文件在且**内容完整**（模板/自测题仍在）；② 顶上明说已被取代并指向对接中心；
    ③ 大厅主入口不再导向它（由 `test_hall_entry_points_to_the_docking_center` 保证）。
    """
    reg = _read("register.html")
    assert "⚑ 索引" in reg, "旧页的注册模板索引块被删了（产品内容）"
    assert "选 **Q1**" in reg and "选 **Q5**" in reg, "旧页的自测题 Q1~Q5 被删了（产品内容）"
    assert "/docking.html" in reg, "旧页没有指向对接中心"
    assert "已被「对接中心」取代" in reg or "已被对接中心取代" in reg, \
        "旧页没有说明它已被取代（人会以为这里才是正主）"
    assert "只读镜像" in reg, "旧页没有说明自己现在的身份是「只读镜像」"
    # ★"只读"不能只是自称：**必须有闸**把写操作拦下来（V34 §5 的措辞就是"只读镜像"）。
    assert "window.fetch" in reg and "只读闸" in reg, "旧页自称只读，却没有拦截写请求的闸"
    assert "m !== 'GET'" in reg or 'm !== "GET"' in reg, "只读闸没有按 HTTP 方法判（拦不住写请求）"
    assert "403" in reg and "moved_to" in reg, "只读闸没有明确回绝（会出现「点了没反应」的死状态）"


def test_no_discuss_no_bypass_writes():
    """两条纪律：**代码里（去注释后）不许出现 `/discuss`**；**写请求只有 `dockPost` 一条通道**。

    后者是突变体逼出来的：只要能绕过 `dockPost` 直接 fetch，白名单那道闸就是摆设。
    """
    code = _js_code(_read("docking.js"))
    assert "/discuss" not in code, \
        "**代码里**出现了 /discuss —— V34 §4.3 明令：出现即 FAIL（注释里提一句不算）"
    i = code.find("async function dockPost(")
    assert i != -1, "找不到 dockPost 通道"
    j = code.find("\n}", i)
    outside = code[:i] + code[j:]
    bypass = re.findall(r"fetch\([^)]*method\s*:\s*['\"]POST['\"]", outside)
    assert not bypass, "有写请求绕过了 dockPost 通道（白名单闸被绕过）：%s" % bypass


def test_activation_panel_is_single_workshop_only():
    """★激活是 `/{ws}/…` 的语义 ⇒ 只在单间挂载；总控要先选一个间再进来（V34 §1）。"""
    js = _read("docking.js")
    i = js.find("async function dockLoadActivation(){")
    assert i != -1, "找不到 dockLoadActivation"
    j = js.find("\n}", i)
    body = js[i:j]
    assert "DOCK_GLOBAL" in body and "innerHTML = ''" in body, \
        "激活面板没有「只在单间挂载、总控清空」的分支"
    assert "dockLoadActivation();" in js, "激活面板没被真正调用（用户看不到它）"


def test_unverified_steps_are_never_rendered_as_done():
    """★V34 §7.3：`verified=false` **一律**显示"⚠ 待人工/未核实"，**绝不**显示"完成"。

    这里按结构判两件事：① 步骤徽章对"自动但未核实"和"人工模式"都给 ⚠；
    ② 动作结果只有在 `all_verified === true` 时才给 ✓ 的成功样式；其余情况必须出现"⚠"或"未核实"字样。
    """
    js = _read("docking.js")
    i = js.find("function dockStepBadge(")
    assert i != -1, "找不到 dockStepBadge"
    badge = js[i:js.find("\n}", i)]
    assert "⚠" in badge and "未核实" in badge and "人工模式" in badge, \
        "步骤徽章没有把「未核实/人工模式」如实标出来：%s" % badge
    k = js.find("async function dockMemberAct(")
    act = js[k:js.find("\n}", k)]
    assert "all_verified === true" in act, "动作结果没有按 all_verified 分档（会把未核实显示成成功）"
    assert "待人工" in act or "未核实" in act, "未核实的路径没有给出「待人工/未核实」的说法"


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
    """空状态给**动作**（点什么），不给一段解释（FE-26 §2：前台气质）。"""
    js = _read("docking.js")
    assert "还没有登记任何外部 Agent" in js, "缺空白引导"
    assert re.search(r"dockGo\((?:\\'|'\")register(?:\\'|'\")\)", js), \
        "空状态没有给可点的动作（登记入口）—— 前台要给动作，不给解释"


# ── 入口接线（规则 20：一件事的对外表达只有一处真相，但入口要对）──────────────
def test_register_panel_is_global_only():
    """★注册是**全局能力** ⇒ 只在总控挂载；单间模式整块不出现（V34 §1）。"""
    js = _read("docking.js")
    i = js.find("function dockMountRegister(){")
    assert i != -1, "找不到 dockMountRegister"
    j = js.find("\n}", i)
    body = js[i:j]
    assert "DOCK_GLOBAL" in body and "innerHTML = ''" in body, \
        "注册区没有「只在总控挂载、单间清空」的分支"
    assert "dockMountRegister();" in js, "注册区没被真正调用（那用户看不到它）"


def test_register_errors_are_shown_verbatim():
    """注册失败必须**原样显示后端的中文原因**（V34 §9.2 验收：错误路径要有中文 error 直显）。

    ⚠️ 本守卫被突变体抓过一次（M8 存活）：第一版只查函数里有没有 `esc(`，
    而同一函数里**别的地方**（渲染其它字段、渲染探测输出）也有 `esc(` ⇒
    把错误那行的渲染换成一句"操作未完成"依旧通过（**因为错误的原因通过**）。
    现在要求**错误值本身**经过转义送进页面：`esc(d.error`。
    """
    js = _read("docking.js")
    i = js.find("function dockRenderResult(")
    assert i != -1, "找不到 dockRenderResult"
    j = js.find("\n}", i)
    body = js[i:j]
    assert "esc(d.error" in body, "后端 error **本身**没有被渲染出来（只是函数里别处有 esc()）"
    assert "没内容" in body, "失败提示没有明确说「不显示成没内容」（准则⑤）"


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
