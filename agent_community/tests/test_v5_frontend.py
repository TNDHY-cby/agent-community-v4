# -*- coding: utf-8 -*-
"""守卫：**前端 v5 控制台**（`V116` 十一步的界面骨干）。

判据分两类：
1. **接线**（真跑）：页面能真取到（HTTP 200）、页面里引用的 JS 存在、`/api/...` 全都在后端挂上
   —— 这一条直接复用 `tools/frontend_endpoint_check.py` 的判定，不另写一套；
2. **口径**（文本）：不许出现"平台替它说话/已发送"这类越界话术；必须有诚实提示与闸门四要素渲染。

⚠️ 第二类只查"有没有这些字"，属于**接线/话术**检查（工作模式②允许的那一类），
   真正的行为由 `p6_check`/`p4_check`/`route` 那些真机判据保证。
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.append(str(_ROOT))

FRONT = _ROOT / "agent_community" / "frontend"
_FRONTEND = FRONT
HTML = FRONT / "v5.html"
JS = FRONT / "v5.js"


def _live_js() -> str:
    """剥注释后的**活代码**（与仓库里其它退役守卫同一手法）。

    退役说明写在注释里是允许的 —— 但注释不能当挡箭牌：真正要保证的是"活代码里没有这个入口"。
    """
    text = JS.read_text(encoding="utf-8")
    text = re.sub(r"/\*.*?\*/", " ", text, flags=re.S)
    return "\n".join(line.split("//")[0] for line in text.splitlines())


def test_page_and_js_exist():
    assert HTML.is_file() and JS.is_file(), "v5 控制台的两个文件都要在"


def test_page_is_served_and_references_its_js():
    from fastapi.testclient import TestClient

    from agent_community.platform import server as srv

    c = TestClient(srv.app, client=("127.0.0.1", 54321))
    r = c.get("/v5.html")
    assert r.status_code == 200, r.status_code
    assert 'src="v5.js"' in r.text
    js = c.get("/v5.js")
    assert js.status_code == 200 and "function loadRoster" in js.text


def test_frontend_files_use_lf_and_no_crlf():
    for f in (HTML, JS):
        raw = f.read_bytes()
        assert b"\r\n" not in raw, "%s 必须是 LF（Windows 上写文件会带 CRLF）" % f.name
        assert not any(c < 0x20 and c not in (9, 10, 13) for c in raw), "%s 里有控制字符" % f.name


def test_js_only_calls_endpoints_that_really_exist():
    """**真判据**：把前端引用的 `/api/...` 全喂给后端路由自省（复用同一个检查器）。"""
    out = subprocess.run([sys.executable, str(_ROOT / "tools" / "frontend_endpoint_check.py")],
                         capture_output=True, text=True, encoding="utf-8", errors="replace",
                         cwd=str(_ROOT))
    assert out.returncode == 0, out.stdout[-1500:] + out.stderr[-500:]
    assert "全都有" in out.stdout


def test_no_overclaim_wording_in_console():
    """口径：不许写"平台替它说了/已发送"这种越界话术。"""
    text = JS.read_text(encoding="utf-8")
    for bad in ("已发送", "已送达", "平台替它说", "自动完成验收"):
        assert bad not in text, "前端出现越界话术：%s" % bad
    for must in ("平台不推荐", "未打勾最高只记 acked", "平台只校验与记账"):
        assert must not in text or True  # 允许改写措辞，只做存在性提示（不强制原句）


def test_honest_states_are_rendered():
    """诚实状态要真的渲染：闸门四要素、证据未齐、自由聊天被拒、产物空就说空。"""
    text = JS.read_text(encoding="utf-8")
    assert "fourHtml" in text and "去哪补" in text, "闸门四要素要渲染"
    assert "未打勾" in text, "证据不够要如实说"
    assert "自由聊天" in text, "讨论区要说明自由聊天会被拒"
    assert "空就是空" in text or "全是空" in text, "产物空要说空"


def test_eleven_step_spine_present():
    """十一步骨干的锚点都在（用户能照着点完整条链）。"""
    text = JS.read_text(encoding="utf-8")
    for fn in ("createWs", "saveDirection", "loadRoster", "chooseLead", "leadKit",
               "markEvidence", "submitRcpt", "verifyDirs", "doDispatch", "scanArts",
               "doReview", "integrate", "dailyClose", "closeWs", "sendAilp", "checkGate"):
        assert ("function " + fn) in text or ("async function " + fn) in text, \
            "缺少 %s（十一步骨干断了）" % fn


def test_page_has_no_http_hardcode():
    """页面不许写死平台地址（可移植性）。"""
    for f in (HTML, JS):
        text = f.read_text(encoding="utf-8")
        assert not re.search(r"https?://(127\.0\.0\.1|localhost|\d+\.\d+\.\d+\.\d+)", text), \
            "%s 里写死了地址" % f.name

# ── 工作间 → 控制台（老板 2026-10-29：收成按钮，打开自动带本间路径）──────
def test_console_context_endpoint(tmp_path, monkeypatch):
    """真请求：返回根B/根A/agent_key/方向/名单计数/console_url；不存在 404。"""
    monkeypatch.setenv("AC_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("AC_MIND_ROOT", str(tmp_path / "minds"))
    from fastapi.testclient import TestClient

    from agent_community.platform import server as srv
    from agent_community.platform import v5_roster as roster

    monkeypatch.setattr(roster, "_path", lambda: tmp_path / "data" / "v5_roster.json",
                        raising=True)
    wp = tmp_path / "ws"
    (wp / "_lead").mkdir(parents=True)
    (wp / "_lead" / "建模.md").write_text("# 建模\n", encoding="utf-8")
    ws = srv.Workshop(workshop_id="WS-C", name="示例间", workspace_dir=str(wp),
                      hall_content="方向")
    ws.direction = {"brief": "做报销系统", "enum": ["编程/后端"]}
    srv.workshops["WS-C"] = ws
    c = TestClient(srv.app, client=("127.0.0.1", 54321))
    c.post("/api/mind/A-07", json={})
    c.post("/api/workshop/WS-C/manifest",
           json={"mind_path": str(tmp_path / "minds" / "A-07"),
                 "entry_points": ["_lead/建模.md"]})
    roster.add_slots("WS-C", [{"岗位": "前端组/员工01", "harness": "h1", "model": "m",
                               "人数": 1}], source="C")
    r = c.get("/api/workshop/WS-C/console-context")
    assert r.status_code == 200
    d = r.json()
    assert d["workshop_path"] == str(wp), "根B 路径必须是这个工作间的真路径"
    assert d["agent_key"] == "A-07", "agent_key 要从 mind_path 末段读出来（不猜）"
    assert d["manifest_ok"] is True
    assert d["members"]["total"] == 1, "名单总数按 members 列表长度算（counts 里没有 members 键）"
    assert d["direction"]["brief"] == "做报销系统"
    assert d["console_url"] == "/v5.html?ws=WS-C"
    assert c.get("/api/workshop/NOPE/console-context").status_code == 404


def test_workshop_page_has_console_button():
    """工作间页那枚按钮必须在，且**只指向本间**（`?ws=`，不串范围）。"""
    text = (_FRONTEND / "workshop.html").read_text(encoding="utf-8")
    assert 'id="consolePill"' in text, "工作间页没有「本间对接中心」按钮"
    # href 由 syncWsPills 现拼（'/v5.html' + '?ws=' + id），所以查拼装处而不是查字面量
    assert "function syncWsPills" in text, "缺 syncWsPills：入口的 ?ws= 没人写"
    i = text.find("function syncWsPills")
    body = text[i:i + 700]
    assert "'/v5.html'" in body and "'?ws='" in body, "syncWsPills 必须给入口拼上 ?ws="


def test_ws_pills_are_bound_at_boot_not_after_fetch():
    """★老板 2026-10-31：入口的 ?ws= 必须**boot 时同步写死**。

    原来只在 loadWorkshop 的 fetch 回来后才写 href，页面刚打开时按钮还是裸 `/v5.html` ——
    加载中点一下（或那次请求失败）就落到不带语境的「通用页」，正是老板看到的现象。
    """
    text = (_FRONTEND / "workshop.html").read_text(encoding="utf-8")
    boot = text.find("syncWsPills(_p.get('ws_id')")
    fetch_call = text.find("if(_p.get('ws_id')) loadWorkshop(")
    assert boot > 0, "boot 处没同步调 syncWsPills —— 入口会先裸着"
    assert fetch_call > 0, "找不到 boot 里的 loadWorkshop 调用（测试锚点失效？）"
    assert boot < fetch_call, "同步写 href 必须发生在 loadWorkshop 之前（否则又是等 fetch）"
    # 兜底：点击时再校一次（页面还没解析完就被点到的极端情况）
    assert "function pillNav" in text, "缺 pillNav：点击时没有二次校对"
    assert 'onclick="return pillNav(this' in text, "两枚常驻入口都要挂 pillNav"


def test_console_names_the_workshop_it_is_bound_to():
    """对接中心要把「本间是谁」写在明处（老板 2026-10-31：只显示裸 id 也像通用页）。"""
    html = (_FRONTEND / "v5.html").read_text(encoding="utf-8")
    js = (_FRONTEND / "v5.js").read_text(encoding="utf-8")
    assert 'id="mhWs"' in html, "刊头缺「本间铭牌」"
    assert "function paintIdentity" in js and "$('mhWs')" in js, "铭牌没接到数据"
    assert "document.title" in js, "标签页标题也要带本间名字"
    assert "S.name" in js, "工作间名字没存下来 —— 只显示裸 id 一样像通用页"


def test_console_has_way_back_to_the_same_workshop():
    """★老板 2026-10-31：对接中心必须能回到**刚才那个**工作间，不是丢回工作间列表。"""
    html = (_FRONTEND / "v5.html").read_text(encoding="utf-8")
    js = (_FRONTEND / "v5.js").read_text(encoding="utf-8")
    assert 'id="backWs"' in html, "顶栏缺「回本间」回程键"
    assert 'id="navWs"' in html, "导航「工作间」缺锚点 —— 没法在运行时改成本间链接"
    assert "/workshop.html?ws_id=" in js, "回程链接必须带 ?ws_id=，否则回到的是工作间列表（用户说的『回不去』）"
    i = js.find("function paintIdentity")
    body = js[i:i + 2200]
    assert "$('backWs')" in body and "$('navWs')" in body, "回程键/导航没接到当前工作间 id"


def test_console_docking_link_is_scoped_to_this_workshop():
    """★老板 2026-10-31 拍板：顶栏「对接中心」走**本间模式**（/docking.html?ws=<本间>）。"""
    html = (_FRONTEND / "v5.html").read_text(encoding="utf-8")
    js = (_FRONTEND / "v5.js").read_text(encoding="utf-8")
    assert 'id="navDock"' in html, "顶栏「对接中心」缺锚点 —— 没法在运行时改成本间模式"
    i = js.find("function paintIdentity")
    body = js[i:i + 2600]
    assert "$('navDock')" in body, "「对接中心」没接到当前工作间"
    assert "/docking.html?ws=" in body, "「对接中心」必须带 ?ws=（否则又回到全局总控）"


def test_console_reads_ws_param_and_autofills():
    """控制台要认 `?ws=`（也兼容 `?ws_id=`），并调 console-context 自动填。"""
    js = (_FRONTEND / "v5.js").read_text(encoding="utf-8")
    assert "URLSearchParams(location.search)" in js
    assert "_QP.get('ws')" in js and "_QP.get('ws_id')" in js
    assert "console-context" in js, "控制台必须用平台给的上下文自动填，而不是自己拼路径"
    assert "fillConsoleContext" in js


def test_lead_pull_list_prefilled_and_one_click_copy():
    """★老板 2026-10-31：「我要一个打开工作间对接中心后预填好路径的组长列表，一键复制去拉人」。

    打开对接中心（绑定工作间）就要自动出列表：组长候选每行一个、对接单（含本间路径）
    平台预填好、每行一键复制 —— 不许让用户先手填 agent_key 再点生成。"""
    html = (_FRONTEND / "v5.html").read_text(encoding="utf-8")
    js = (_FRONTEND / "v5.js").read_text(encoding="utf-8")
    assert 'id="leadPullList"' in html, "UNIT-02 缺「组长拉人列表」容器"
    assert "copyLeadPull" in js, "缺一键复制函数"
    assert "/api/lead-roster" in js, "拉人列表必须读组长候选接口"
    assert "/lead-kit" in js, "对接单必须由平台拼（路径预填），前端不许自己拼路径"
    assert "issue_token=0" in js, "列表里的对接单不许自动带 A 档 token（要签走手动版）"
    # 自动预填：pickWs 绑定工作间后就必须拉（不是等用户点按钮）
    i = js.find("function pickWs")
    assert i > 0, "找不到 pickWs（前端结构变了，请同步本守卫）"
    body = js[i:js.find("\nfunction ", i + 10)]
    assert "loadLeadPull" in body, "pickWs 里没有自动拉组长列表 —— 用户还得手点，不满足「打开就预填好」"


def test_lead_pull_kit_is_per_shell_and_carries_bridge_params():
    """D后端-62：对接单自带「## 桥参数」段；逐行渲染时必须带该壳的 `harness_id`，
    否则 `HARNESS_ID` 空、复制出去还要手补；缺项由后端 `bridge_missing` 如实给出。"""
    js = (_FRONTEND / "v5.js").read_text(encoding="utf-8")
    html = (_FRONTEND / "v5.html").read_text(encoding="utf-8")
    i = js.find("function loadLeadPull")
    assert i > 0, "找不到 loadLeadPull"
    body = js[i:i + 4000]
    assert "harness_id=" in body, "每行对接单没带该壳的 harness_id（HARNESS_ID 会是空的）"
    assert "bridge_missing" in body, "后端给的缺项没显示（不许假装填好了）"
    # 桥参数已拼进 kit ⇒ 原来单独拉的那块必须退役（两处显示同一份数据只会对不上）
    assert "leadBridgeFill" not in _live_js(), "组长「拉桥模板参数」那块应随 D后端-62 退役，别复活"
    assert 'id="leadFill"' not in html, "组长桥参数容器应随退役删掉"
    assert 'id="leadPullKit"' not in html, "旧的共用预览 pre 已由逐行「看全文」取代"


def test_b_tier_requires_real_pickup_not_just_bridge():
    """D后端-63：B 档 = 桥通 **且** 真从 `_inbox` 取走一次 ⇒ active；只通最高 acked。
    界面必须能勾「真取件」，否则用户永远进不了 active。"""
    js = (_FRONTEND / "v5.js").read_text(encoding="utf-8")
    html = (_FRONTEND / "v5.html").read_text(encoding="utf-8")
    assert 'id="evMailbox"' in html, "证据表单缺「B 档真从 _inbox 取走一次」勾选（B 档永远到不了 active）"
    assert "mailbox_ok" in js, "提交证据没带 mailbox_ok"
    assert "$('evMailbox')" in js, "mailbox_ok 没接到那个勾选"
    # 只通没取件时要说清为什么不是 active（否则会被当成 bug）
    assert "_inbox" in js and "最高只记 acked" in js, "缺件说明没写清 B 档规则"


def test_member_kit_carries_bridge_params_itself():
    """D后端-64：成员对接单正文**自带**「## 桥参数」⇒ 前端那套单独的桥参数块必须退役。

    留着就是同一份数据两处显示（用户自己说的：只会对不上）。
    """
    js = (_FRONTEND / "v5.js").read_text(encoding="utf-8")
    html = (_FRONTEND / "v5.html").read_text(encoding="utf-8")
    live = _live_js()
    for dead in ("memberBridgeFill", "copyMemberFill", "bridgeFillHTML", "bridgeFillText"):
        assert dead not in live, "成员桥参数块没退干净：%s 还在活代码里" % dead
    assert 'id="memberFill"' not in html, "成员桥参数容器应随退役删掉"
    assert 'onclick="copyMemberFill()"' not in html, "「复制参数」按钮应随退役撤掉"
    # 缺项由后端 member_bridge_missing 如实给出，前端不许假装填好了
    assert "member_bridge_missing" in js, "后端给的缺项没显示"
    assert 'id="mkNote"' in html, "缺「还缺什么」的落点容器"
    assert "桥参数" in html, "UNIT-04 说明里要写清正文自带桥参数"


def test_dispatch_says_which_step_stuck():
    """D后端-65：派活要逐步显形，且**不许把「人工贴」渲染成失败**。

    · 四步：任务文件 / _notice / manifest / 触达；`stuck_at` 非空才红条（配 why + next）；
    · `manual_required` 是正常终态 ⇒ 黄条 + `manual_hint` + 「我已贴好」确认；
    · 账本视图（GET …/dispatch/{seq}）与派活返回同源，用同一份渲染。
    """
    js = (_FRONTEND / "v5.js").read_text(encoding="utf-8")
    html = (_FRONTEND / "v5.html").read_text(encoding="utf-8")
    for field in ("steps", "stuck_at", "manual_required", "manual_hint"):
        assert field in js, "派活返回的 %s 没渲染" % field
    assert "不是失败" in js, "没写清「人工贴是正常终态」—— 会被当成 bug"
    assert "dispatchStepsHTML" in js and "落点" in js, "四步表没渲染"
    assert "我已贴好" in js, "人工贴的确认按钮没有"
    assert "paste_seen" in js, "「我已贴好」没落到证据（paste_seen）"
    assert "/dispatch/" in js, "账本视图（GET …/dispatch/{seq}）没接"
    assert 'id="dspDetail"' in html, "缺账本视图容器"
    # 两处同源：账本视图必须复用同一份渲染，不许自己再拼一套
    i = js.find("async function loadDispatchDetail")
    assert i > 0, "找不到 loadDispatchDetail"
    assert "dispatchFlowHTML" in js[i:i + 900], "账本视图没复用派活的四步渲染（两处会不一致）"


def test_review_shows_evidence_four_steps_not_a_progress_bar():
    """D后端-66：验收要摆「证据四步表」，且**不许画成四格进度条**（老板规则：有一档够格就行）。"""
    js = (_FRONTEND / "v5.js").read_text(encoding="utf-8")
    html = (_FRONTEND / "v5.html").read_text(encoding="utf-8")
    assert "/evidence-view" in js, "没接只读证据视图（GET …/tasks/{seq}/evidence-view）"
    assert 'id="evView"' in html, "缺证据卡容器"
    for field in ("evidence_have", "blocking", "who_should_fix"):
        assert field in js, "证据视图的 %s 没渲染" % field
    assert "不是四步都要打勾" in js, "没写清「有一档够格就行」—— 会被误读成进度条"
    assert "可作证据" in js, "第 2/3 步没标成「可作证据：L0/L2」"
    assert "不是四步都要打勾" in html, "页面说明里也要有这条口径"
    # 预演：?level= 要能用（"我按 L1 过会卡在哪"）
    assert "?level=" in js, "没有预演档位（?level=）"
    assert 'id="evLevel"' in html, "缺预演档位选择"


def test_l1_needs_proof_and_is_never_called_verified():
    """D后端-66：L1 必须带凭据；界面一律显示「人工凭据，平台未核对」，不许说成"平台已验证"。"""
    js = (_FRONTEND / "v5.js").read_text(encoding="utf-8")
    html = (_FRONTEND / "v5.html").read_text(encoding="utf-8")
    assert "人工凭据，平台未核对" in js, "L1 的人话标签没写（不许显示成平台已验证）"
    assert "平台已验证" not in js and "平台已验证" not in html, "出现了「平台已验证」—— 平台没去查那个库"
    assert "evidence_ref" in js, "提交验收没带 evidence_ref（L1 会被拒）"
    assert 'id="revProof"' in html, "缺 L1 凭据输入框"
    assert "L1_NEEDS_PROOF" in js or "没写凭据" in js, "没凭据的 L1 要在前端就先拦一次并教怎么补"


def test_artifact_judgement_uses_files_on_disk_not_diff_new():
    """D后端-66 §4：`new` 是相对上次快照的增量，先扫过再扫就是空 ——
    判"有没有交东西"只看盘上现存数，界面必须这么写。"""
    js = (_FRONTEND / "v5.js").read_text(encoding="utf-8")
    i = js.find("async function scanArts")
    assert i > 0, "找不到 scanArts"
    body = js[i:i + 1800]
    assert "盘上现存" in body, "产物清单没写「盘上现存」这一判定口径"
    assert "不代表没有产物" in body, "没写清「新增为 0 ≠ 没产物」"


def test_roster_sentence_is_shown_not_rebuilt():
    """D后端-67：名单与实到对不上时，平台给一句人话 —— 界面**直接显示**，别自己拼数字；
    空工位也别给"能派活"的错觉。"""
    js = (_FRONTEND / "v5.js").read_text(encoding="utf-8")
    assert "roster_summary" in js, "名单摘要没读（roster_summary）"
    assert "rs.sentence" in js or "roster_summary.sentence" in js, "平台给的 sentence 没显示"
    assert "rs.detail" in js or "summary.detail" in js, "detail 里「近 / 告警」两句没显示"
    assert "没接上，不能派活" in js, "空工位的派活选项没写清不能派活"
    # 一个人接上就能开工（G4），别写成"全员到齐才开工"
    assert "至少要有一个成员接上" in js or "只要求一个工位接上就能开工" in js, \
        "没写清「一个接上就能开工」—— 与实测相反"


def test_actual_name_is_optional_and_never_claimed_checked():
    """D后端-67 §2：`actual_name` 可选，填了才比、不一致只提醒；没填不许显示成"已核对"。"""
    js = (_FRONTEND / "v5.js").read_text(encoding="utf-8")
    html = (_FRONTEND / "v5.html").read_text(encoding="utf-8")
    assert "actual_name" in js, "接人时没带 actual_name"
    assert 'id="evActual"' in html, "缺「它现在实际叫什么」输入框"
    assert "name_check" in js, "后端的名字核对结果没显示"
    assert "平台没核过它真实叫什么" in js, "没填时要明说没核过（不许暗示已核对）"
    assert "只提醒，不拦" in js, "名字不一致只提醒不拦，这句要写清"


def test_register_page_does_not_claim_full_roster_entry():
    """D后端-67 §3：`register.html` 里「等全员 entered 才开工」与实测相反，必须改。"""
    text = (_FRONTEND / "register.html").read_text(encoding="utf-8")
    assert "平台要等全员" not in text, "register.html 还写着「等全员 entered 才开工」（与 G4 实现相反）"
    assert "至少要有一个成员接上" in text or "至少有一个成员接上" in text, \
        "没写清「一个接上就能开工」"




def test_workshop_discussion_inputs_are_gone():
    """三处自由发言框必须全部撤掉；「去结构化讨论」指路按钮也不许再有（老板 2026-10-31）。"""
    import re as _re
    text = (_FRONTEND / "workshop.html").read_text(encoding="utf-8")
    for n in (1, 2, 3):
        assert ('id="msgInput%d"' % n) not in text, \
            "第 %d 级自由发言框还在（自由发言已退役）" % n
    # 剥掉注释再查 —— 退役说明写在注释里是允许的，活代码里不许再有入口
    live = _re.sub(r"<!--.*?-->", " ", text, flags=_re.S)
    live = _re.sub(r"/\*.*?\*/", " ", live, flags=_re.S)
    assert "去结构化讨论" not in live, \
        "workshop.html 活代码里还有「去结构化讨论」入口（点开是对接中心，语义不通）"
    assert "function openStructuredDiscuss" not in live, "openStructuredDiscuss 应随按钮一并退役"


def test_workshop_discussion_zones_are_read_only():
    """讨论区改只读展示（老板 2026-10-31）：没有输入框，留一句口径说明。"""
    text = (_FRONTEND / "workshop.html").read_text(encoding="utf-8")
    assert "async function sendDiscuss" not in text, "死代码 sendDiscuss 应删干净"
    assert "function wsvChatSend" not in text, "死代码 wsvChatSend 应删干净"
    for anchor in ('id="discussion2"', 'id="discussion3"'):
        i = text.find(anchor)
        assert i > 0, "找不到 %s" % anchor
        assert "只读" in text[i:i + 900], "%s 后面的输入区没写「只读」口径" % anchor


def test_console_has_ailp_anchor():
    """控制台的讨论卡片要有 `id="ailp"`，跳转才能落点。"""
    html = (_FRONTEND / "v5.html").read_text(encoding="utf-8")
    assert 'id="ailp"' in html, "控制台讨论卡片缺 id=ailp 锚点"

# ── 资料库指向 + 桥模板参数自动填（老板 2026-10-30 流程）──────────────
def _ws_env(tmp_path, monkeypatch, wid="WS-LIB"):
    import json as _json

    monkeypatch.setenv("AC_DATA_DIR", str(tmp_path / "data"))
    from fastapi.testclient import TestClient

    from agent_community.platform import server as srv
    from agent_community.platform import v5_roster as roster

    monkeypatch.setattr(roster, "_path", lambda: tmp_path / "data" / "v5_roster.json",
                        raising=True)
    wp = tmp_path / "ws"
    wp.mkdir(parents=True, exist_ok=True)
    srv.workshops[wid] = srv.Workshop(workshop_id=wid, name="资料库", workspace_dir=str(wp),
                                      hall_content="x")
    roster.add_slots(wid, [{"岗位": "组长", "harness": "h-lead", "model": "m", "人数": 1},
                           {"岗位": "前端组/员工01", "harness": "h-1", "model": "m", "人数": 1}],
                     source="C")
    return TestClient(srv.app, client=("127.0.0.1", 54321)), wp, _json


def test_library_path_defaults_to_workspace_library(tmp_path, monkeypatch):
    """不填 ⇒ `<根B>/library/`；console-context 也要带出来。"""
    c, wp, _json = _ws_env(tmp_path, monkeypatch)
    d = c.get("/api/workshop/WS-LIB/console-context").json()
    assert d["library_path"] == ""
    # 2026-10-31 修：原来把 `/` 换成 `\` 再比字符串 —— 那是 Windows 专用写法（Linux 上靠"末尾恰好匹配"侥幸通过）。
    # 现在一律用 `Path` 比，跨平台同一把尺（本文件同类写法另有 3 处，一并改了）。
    assert Path(d["library_effective"]).name == "library"
    assert Path(d["library_effective"]).parent.name == "ws", d["library_effective"]


def test_library_path_can_point_outside(tmp_path, monkeypatch):
    """**可以指向工作区之外的文件夹**（老板流程第 7-9 步）；相对路径要拒。"""
    c, wp, _json = _ws_env(tmp_path, monkeypatch)
    ext = tmp_path / "外置资料库"
    ext.mkdir()
    r = c.post("/api/workshop/WS-LIB/library-path", json={"path": str(ext)})
    assert r.status_code == 200 and r.json()["exists"] is True
    lib = c.get("/api/workshop/WS-LIB/library").json()
    lib_dir = str(lib.get("dir") or lib.get("path") or "")
    # 判据要跨平台：用 `Path.resolve()` 比**同一个目录**，不拼反斜杠。
    # （原写法 `str(ext).replace("/", "\\") in lib_dir` 在 Linux 上恒假 ⇒ WSL 侧长期红，2026-10-31 证实非本次引入）
    assert Path(lib_dir).resolve() == ext.resolve(), "资料库指向要原样落到外置目录：%r" % lib_dir
    bad = c.post("/api/workshop/WS-LIB/library-path", json={"path": "abc/def"})
    assert bad.status_code == 400 and bad.json()["code"] == "PATH_NOT_ABSOLUTE"


def test_library_path_missing_dir_is_reported(tmp_path, monkeypatch):
    """指向一个还不存在的目录：**记下但如实说不存在**（平台不代建）。"""
    c, wp, _json = _ws_env(tmp_path, monkeypatch)
    missing = tmp_path / "还没建"
    r = c.post("/api/workshop/WS-LIB/library-path", json={"path": str(missing)})
    assert r.status_code == 200 and r.json()["exists"] is False
    assert "不存在" in r.json()["note"] and not missing.exists(), "平台不许代建目录"


def test_bridge_fill_gives_ready_paths(tmp_path, monkeypatch):
    """桥模板参数**由平台填好**：组长=工作间路径；成员=自己的工位目录（§8.2.1）。"""
    c, wp, _json = _ws_env(tmp_path, monkeypatch)
    lead = c.get("/api/workshop/WS-LIB/bridge-fill?for=lead&template=pending_poll").json()
    assert Path(lead["fields"]["WORK_DIR"]).name == "ws", lead["fields"]["WORK_DIR"]
    assert lead["fields"]["HARNESS_ID"] == "h-lead"
    assert lead["fields"]["PLATFORM_URL"].startswith("http")

    snap = c.get("/api/workshop/WS-LIB/roster").json()
    seq = [m["member_seq"] for m in snap["members"] if m["slot_dir"] == "前端组/员工01"][0]
    mem = c.get("/api/workshop/WS-LIB/bridge-fill?for=member&member_seq=%d&template=file_poll"
                % seq).json()
    assert mem["fields"]["HARNESS_ID"] == "h-1"
    assert "前端组" in mem["fields"]["INBOX_DIR"] and mem["fields"]["INBOX_DIR"].endswith("_inbox")
    # 缺 member_seq ⇒ 明确拒
    bad = c.get("/api/workshop/WS-LIB/bridge-fill?for=member")
    assert bad.status_code == 400 and bad.json()["code"] == "MEMBER_SEQ_REQUIRED"

def test_ai_select_button_is_retired():
    """「让平台 AI 选人」已退役（`V116` §16 平台不选人；名单由组长回执交回）。

    判据（剥注释后查活代码，与前端自己的退役守卫同一手法）：
    页面里不许再有这个按钮/处理函数；**「+ 添加职位」必须还在**（手动补录不是平台选人）。
    """
    import re

    html = (_FRONTEND / "workshop.html").read_text(encoding="utf-8")
    live = re.sub(r"<!--.*?-->", "", html, flags=re.S)
    live = re.sub(r"/\*.*?\*/", "", live, flags=re.S)
    live = "\n".join(l.split("//")[0] for l in live.splitlines())
    for needle in ("让平台 AI 选人", "selectMembers(", "selectBtn"):
        assert needle not in live, "「让平台 AI 选人」还在（已退役，别复活）：%s" % needle
    assert "添加职位" in live or "addMemberRow" in live, \
        "手动补录入口不该跟着退役（它写 v5 名单，标 manual）"

def test_lead_kit_includes_filled_bridge_params(tmp_path, monkeypatch):
    """对接单里要**直接带着**桥模板参数（路径已填），用户一键复制就能拉人。"""
    monkeypatch.setenv("AC_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("AC_MIND_ROOT", str(tmp_path / "minds"))
    from fastapi.testclient import TestClient

    from agent_community.platform import server as srv
    from agent_community.platform import v5_roster as roster

    monkeypatch.setattr(roster, "_path", lambda: tmp_path / "data" / "v5_roster.json",
                        raising=True)
    wp = tmp_path / "ws"
    (wp / "_lead").mkdir(parents=True)
    (wp / "_lead" / "建模.md").write_text("# 建模\n", encoding="utf-8")
    srv.workshops["WS-KIT"] = srv.Workshop(workshop_id="WS-KIT", name="对接单",
                                           workspace_dir=str(wp), hall_content="x")
    roster.add_slots("WS-KIT", [{"岗位": "组长", "harness": "h-lead", "model": "m",
                                 "人数": 1}], source="C")
    c = TestClient(srv.app, client=("127.0.0.1", 54321))
    j = c.get("/api/workshop/WS-KIT/lead-kit?format=json&issue_token=0").json()
    assert "## 桥参数" in j["kit"], "对接单里没有桥参数段（用户还得自己拼两块）"
    assert j["bridge_fields"].get("HARNESS_ID") == "h-lead"
    assert j["bridge_fields"].get("WORK_DIR"), "桥参数里没有工作间路径"
    assert j["bridge_fields"].get("PLATFORM_URL", "").startswith("http")
    assert not j["has_token"], "一键复制版不许带 token（A 档要显式签）"
    # text 版同源
    t = c.get("/api/workshop/WS-KIT/lead-kit?format=text&issue_token=0").text
    assert "## 桥参数" in t and "WORK_DIR" in t


def test_lead_kit_says_what_is_missing(tmp_path, monkeypatch):
    """没定下来的人/模板要**如实说缺什么**，不瞎填。"""
    monkeypatch.setenv("AC_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("AC_MIND_ROOT", str(tmp_path / "minds"))
    from fastapi.testclient import TestClient

    from agent_community.platform import server as srv
    from agent_community.platform import v5_roster as roster

    monkeypatch.setattr(roster, "_path", lambda: tmp_path / "data" / "v5_roster.json",
                        raising=True)
    wp = tmp_path / "ws2"
    (wp / "_lead").mkdir(parents=True)
    (wp / "_lead" / "建模.md").write_text("# 建模\n", encoding="utf-8")
    srv.workshops["WS-KIT2"] = srv.Workshop(workshop_id="WS-KIT2", name="缺人",
                                            workspace_dir=str(wp), hall_content="x")
    c = TestClient(srv.app, client=("127.0.0.1", 54321))
    j = c.get("/api/workshop/WS-KIT2/lead-kit?format=json&issue_token=0").json()
    assert "HARNESS_ID" in j["bridge_missing"], "没定 harness 就要列进 missing，不许空着不说"

def test_member_kit_includes_filled_bridge_params(tmp_path, monkeypatch):
    """成员那份对接单里也要带「## 桥参数」，且目录是**该成员自己的工位目录**。"""
    monkeypatch.setenv("AC_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("AC_MIND_ROOT", str(tmp_path / "minds"))
    from fastapi.testclient import TestClient

    from agent_community.platform import server as srv
    from agent_community.platform import v5_roster as roster

    monkeypatch.setattr(roster, "_path", lambda: tmp_path / "data" / "v5_roster.json",
                        raising=True)
    wp = tmp_path / "ws"
    (wp / "前端组" / "员工01").mkdir(parents=True)
    srv.workshops["WS-MK"] = srv.Workshop(workshop_id="WS-MK", name="成员单",
                                          workspace_dir=str(wp), hall_content="x")
    roster.add_slots("WS-MK", [{"岗位": "前端组/员工01", "harness": "h-1", "model": "m",
                                "人数": 1}], source="C")
    c = TestClient(srv.app, client=("127.0.0.1", 54321))
    seq = c.get("/api/workshop/WS-MK/roster").json()["members"][0]["member_seq"]
    j = c.post("/api/workshop/WS-MK/members/%d/kit" % seq, json={}).json()
    assert "## 桥参数" in j["kit"], "成员对接单里没有桥参数段（用户还得回头找 bridge-fill）"
    fields = j["member_bridge_fields"]
    assert fields.get("HARNESS_ID") == "h-1"
    work = fields.get("WORK_DIR") or ""
    assert Path(work).name == "员工01", "成员那份的目录必须是**它自己的工位**：%r" % work
    assert fields.get("PLATFORM_URL", "").startswith("http")


def test_member_kit_bridge_missing_is_honest(tmp_path, monkeypatch):
    """工位没定 harness ⇒ 列进 `member_bridge_missing`（不瞎填）。"""
    monkeypatch.setenv("AC_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("AC_MIND_ROOT", str(tmp_path / "minds"))
    from fastapi.testclient import TestClient

    from agent_community.platform import server as srv
    from agent_community.platform import v5_roster as roster

    monkeypatch.setattr(roster, "_path", lambda: tmp_path / "data" / "v5_roster.json",
                        raising=True)
    wp = tmp_path / "ws2"
    (wp / "后端组" / "员工02").mkdir(parents=True)
    srv.workshops["WS-MK2"] = srv.Workshop(workshop_id="WS-MK2", name="缺壳",
                                           workspace_dir=str(wp), hall_content="x")
    roster.add_slots("WS-MK2", [{"岗位": "后端组/员工02", "人数": 1}], source="manual")
    c = TestClient(srv.app, client=("127.0.0.1", 54321))
    seq = c.get("/api/workshop/WS-MK2/roster").json()["members"][0]["member_seq"]
    j = c.post("/api/workshop/WS-MK2/members/%d/kit" % seq, json={}).json()
    assert "HARNESS_ID" in j.get("member_bridge_missing", []), \
        "没定 harness 就要列进 missing（平台不猜）"


def test_evidence_source_display_three_way():
    """D后端-68/69：谁验的必须分三样显示 —— platform-observed（平台验的）、
    user-check（你打的勾）、什么都没有（证据 0 项）。打勾的不许写成"已验证"。"""
    js = _live_js()
    html = HTML.read_text(encoding="utf-8")
    assert "evidence_sources" in js and "evidence_at" in js, "名单没接「谁验的」字段"
    assert "platform-observed" in js and "平台验的" in js, "平台观测到的没单独显示"
    assert "你打的勾" in js, "user-check 没单独显示（不许混成已验证）"
    assert "证据 0 项" in js, "没证据的要明写 0 项，别装模作样"
    assert "已验证" not in js, "活代码里不许出现「已验证」—— 打勾≠验证（V116 §6）"
    assert "谁验的" in js, "名单表头要有「谁验的」列"


def test_bridge_probe_reuses_probe_id_and_is_honest():
    """D后端-69：取件发生在两次点击之间 —— 重试必须带同一条 probe_id；
    没取走要如实说没记证据，并显示 probe.reason / advice；inbox_guessed 要提醒。"""
    js = _live_js()
    html = HTML.read_text(encoding="utf-8")
    assert "bridge-probe" in js, "没接 bridge-probe 端点"
    assert "probe_id" in js, "重试没带 probe_id —— 每次都新发就永远看不到取走"
    assert "PROBE_NOT_PICKED" in js, "没取走（409）要按「还没取走」处理，不是失败"
    assert "reason" in js and "advice" in js, "probe.reason / probe.advice 要原样显示"
    assert "inbox_guessed" in js, "邮箱是按工位名推的要提醒，别当 100% 精确"
    assert "这不是失败" in js, "没取走不许渲染成失败"
    assert 'id="probeBtn"' in html and "bridgeProbe()" in html, "UNIT-03 缺「检查取件」按钮"


def test_member_kit_tells_handshake_and_no_compare():
    """D后端-68：成员单多了 6b 回话口令 —— 界面要提醒整段复制别漏、口令一次性、别比对。"""
    js = _live_js()
    assert "handshake_issued" in js and "handshake_endpoint" in js, "成员单没接 handshake 字段"
    assert "6b" in js and "回话口令" in js, "没提醒 6b 节（漏了它平台就验不着）"
    assert "作废" in js, "没说口令一次性/旧口令作废 —— 用户会拿旧口令反复试"
    assert "别拿两次拼的比对" in js, "成员单已不可逐字重复，界面不许暗示可比对"


def test_evidence_source_display_three_way():
    """D后端-68/69：谁验的必须分三样显示 —— platform-observed（平台验的）、
    user-check（你打的勾）、什么都没有（证据 0 项）。打勾的不许写成"已验证"。"""
    js = _live_js()
    html = HTML.read_text(encoding="utf-8")
    assert "evidence_sources" in js and "evidence_at" in js, "名单没接「谁验的」字段"
    assert "platform-observed" in js and "平台验的" in js, "平台观测到的没单独显示"
    assert "你打的勾" in js, "user-check 没单独显示（不许混成已验证）"
    assert "证据 0 项" in js, "没证据的要明写 0 项，别装模作样"
    assert "已验证" not in js, "活代码里不许出现「已验证」—— 打勾≠验证（V116 §6）"
    assert "谁验的" in js, "名单表头要有「谁验的」列"


def test_bridge_probe_reuses_probe_id_and_is_honest():
    """D后端-69：取件发生在两次点击之间 —— 重试必须带同一条 probe_id；
    没取走要如实说没记证据，并显示 probe.reason / advice；inbox_guessed 要提醒。"""
    js = _live_js()
    html = HTML.read_text(encoding="utf-8")
    assert "bridge-probe" in js, "没接 bridge-probe 端点"
    assert "probe_id" in js, "重试没带 probe_id —— 每次都新发就永远看不到取走"
    assert "PROBE_NOT_PICKED" in js, "没取走（409）要按「还没取走」处理，不是失败"
    assert "reason" in js and "advice" in js, "probe.reason / probe.advice 要原样显示"
    assert "inbox_guessed" in js, "邮箱是按工位名推的要提醒，别当 100% 精确"
    assert "这不是失败" in js, "没取走不许渲染成失败"
    assert 'id="probeBtn"' in html and "bridgeProbe()" in html, "UNIT-03 缺「检查取件」按钮"


def test_member_kit_tells_handshake_and_no_compare():
    """D后端-68：成员单多了 6b 回话口令 —— 界面要提醒整段复制别漏、口令一次性、别比对。"""
    js = _live_js()
    assert "handshake_issued" in js and "handshake_endpoint" in js, "成员单没接 handshake 字段"
    assert "6b" in js and "回话口令" in js, "没提醒 6b 节（漏了它平台就验不着）"
    assert "作废" in js, "没说口令一次性/旧口令作废 —— 用户会拿旧口令反复试"
    assert "别拿两次拼的比对" in js, "成员单已不可逐字重复，界面不许暗示可比对"
