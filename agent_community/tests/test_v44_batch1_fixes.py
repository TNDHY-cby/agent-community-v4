# -*- coding: utf-8 -*-
"""V44 批 1 回归：验证间桥型键、模板选序、示例桥三处、按接口引导。

对应 `design-docs/V44_五harness真机验收复盘与7个缺陷.md` 的 #10 / #11 / #12 / #13 / #14 / #15。

**每条断言都对着一个真机实测出来的缺陷**（不是造出来的场景）：
  · #10 A 档 harness 在验证间里永远起不了桥（键 `acp` ≠ 模板 kind `cli_acp`）
  · #11 省略 template 时可能挑到**没验证过**的草稿
  · #12 示例桥启动即用示例值**覆盖真注册**
  · #13 示例桥无参构造 ⇒ 只读没人设的环境变量 ⇒ 拉不起 agent
  · #14 示例桥 `post_json` 不带 `X-Agent-Token` ⇒ 回报 401
  · #15 桥型与声明的叫醒方式不匹配却无人提醒
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from agent_community.platform import bridge_templates as bt
from agent_community.platform import interface_guidance as ig
from agent_community.platform import verify_sandbox as vs

_EXAMPLES = Path(vs.__file__).resolve().parent.parent / "examples"


class _Info:
    """最小 HarnessInfo 替身（只需可写 metadata + 若干只读字段）。"""

    def __init__(self, wakeup_method="", acp_command="", wakeup_dir="", **meta):
        self.metadata = dict(meta)
        self.wakeup_method = wakeup_method
        self.acp_command = acp_command
        self.acp_cwd = ""
        self.wakeup_dir = wakeup_dir


# ── #10 验证间桥脚本键必须与模板 kind 一致 ────────────────────────────

def test_bridge_scripts_keys_are_template_kinds():
    """键必须是**模板 kind**（`cli_acp`），不是叫醒方式（`acp`）。"""
    assert "cli_acp" in vs.BRIDGE_SCRIPTS
    assert "acp" not in vs.BRIDGE_SCRIPTS, "又写成 wakeup_method 了 —— 这会让 A 档永远起不了桥"
    # 键集必须落在 bridge_templates.KINDS 里（两处口径不许各写各的）
    for kind in vs.BRIDGE_SCRIPTS:
        assert kind in bt.KINDS, f"验证间桥型 {kind} 不在模板 KINDS 里"


def test_every_bridge_script_exists():
    for kind, name in vs.BRIDGE_SCRIPTS.items():
        p = _EXAMPLES / name
        assert p.is_file(), f"{kind} 的桥脚本不存在：{p}"


def test_acp_kind_is_startable_in_sandbox():
    """有牙断言：`cli_acp` 必须能起（原来 `kind in BRIDGE_SCRIPTS` 永远不命中）。"""
    assert vs.BRIDGE_SCRIPTS.get("cli_acp") == "acp_harness_bridge.py"
    argv = vs.bridge_argv("cli_acp", harness_id="h1", inbox="", platform="http://127.0.0.1:18920")
    assert argv[2].endswith("acp_harness_bridge.py")
    assert "--url" in argv and "http://127.0.0.1:18920" in argv


def test_bridge_argv_kind_names_match_scripts():
    """参数名逐脚本核对：file_poll 用 `--platform`，pending_poll/cli_acp 用 `--url`。"""
    fp = vs.bridge_argv("file_poll", harness_id="h", inbox="D:/inbox", platform="http://p")
    assert "--platform" in fp and "--inbox" in fp
    for kind in ("pending_poll", "cli_acp"):
        a = vs.bridge_argv(kind, harness_id="h", inbox="D:/inbox", platform="http://p")
        assert "--url" in a, f"{kind} 应该用 --url"


# ── #11 省略 template 时优先「已验证」 ────────────────────────────────

def test_default_one_prefers_verified_over_draft():
    info = _Info()
    bt.put(info, {"name": "草稿桥", "kind": "pending_poll", "source": "draft", "verified": False})
    bt.put(info, {"name": "验过的桥", "kind": "file_poll",
                  "verified": True, "verified_at": 100.0})
    picked = bt.default_one(info)
    assert picked["name"] == "验过的桥", "又挑到没验证的草稿了（#11）"


def test_default_one_prefers_latest_verified():
    info = _Info()
    bt.put(info, {"name": "早验的", "kind": "pending_poll", "verified": True, "verified_at": 100.0})
    bt.put(info, {"name": "晚验的", "kind": "file_poll", "verified": True, "verified_at": 200.0})
    assert bt.default_one(info)["name"] == "晚验的"


def test_default_one_falls_back_when_nothing_verified():
    info = _Info()
    bt.put(info, {"name": "只有草稿", "kind": "pending_poll", "source": "draft", "verified": False})
    picked = bt.default_one(info)
    assert picked["name"] == "只有草稿"
    assert picked["verified"] is False, "没验过就得如实说没验过（调用方据此提示）"


# ── #12/#13/#14 示例桥的三个毛病 ──────────────────────────────────────

_SRC = (_EXAMPLES / "acp_harness_bridge.py").read_text(encoding="utf-8")


def _code_only(text: str) -> str:
    """剥掉整行注释后再判 —— 否则**判据会被自己写的注释打中**
    （本项目的老坑：注释里为了讲清"别这么做"，原样引用了反面写法）。
    只剥整行注释（`#` 开头的行）与文档字符串块，足够本次这几条断言用。"""
    out, in_doc = [], False
    for line in text.split("\n"):
        s = line.strip()
        # 三引号 docstring 块（示例桥只有模块级一段）
        if s.startswith('"""') and not in_doc:
            in_doc = not s.endswith('"""') or s == '"""'
            continue
        if in_doc:
            if s.endswith('"""'):
                in_doc = False
            continue
        if s.startswith("#"):
            continue
        # 行尾注释也剥掉（避免 `x = 1  # 别写 your-model` 这种）
        if "#" in line:
            line = line.split("#", 1)[0]
        out.append(line)
    return "\n".join(out)


_CODE = _code_only(_SRC)


def test_example_bridge_has_no_placeholder_registration_values():
    """#12：不许把 `your-model` / `your-acp-command` / `C:\\path\\to` 这类占位值带进注册。"""
    for bad in ("your-acp-command", "your-model", "your-provider", r"C:\path\to\your\harness"):
        assert bad not in _CODE, f"示例占位值 {bad!r} 还在代码里 —— 它会覆盖用户的真注册"


def test_example_bridge_does_not_register_by_default():
    """#12：默认**不复写**注册（`register()` 已被 `register_bridge()` 取代）。"""
    assert "def register_bridge(" in _CODE
    assert "def register(" not in _CODE
    assert "register_bridge(base, register=bool(args.register))" in _CODE
    assert "store_true" in _CODE                      # --register 是显式开关


def test_example_bridge_passes_acp_command_to_bridge():
    """#13：必须把命令/工作目录交给桥，不能指望 `AcpHarnessBridge()` 无参构造。"""
    assert "AcpHarnessBridge()" not in _CODE, "又用无参构造了 —— 那会只读 AC_ACP_COMMAND（没人设）"
    assert "AcpHarnessBridge(cmd=" in _CODE
    assert "shlex.split" in _CODE
    assert "--acp-command" in _CODE


def test_example_bridge_sends_agent_token():
    """#14：所有请求都要能带 `X-Agent-Token`（否则回报被自己拦下 ⇒ 401）。"""
    assert "X-Agent-Token" in _CODE
    assert "AGENT_TOKEN" in _CODE
    assert "--agent-token" in _CODE
    # 只有一处设头，其他请求都从它取（别在别处各写一遍）
    assert "def _headers()" in _CODE
    assert _CODE.count("X-Agent-Token") == 1


# ── #17 示例桥必须与"平台模板生成的桥"同能力 ──────────────────────────
#   实测（2026-10-13 20:56，真机 A 档）：#10 修好后桥**起来了**，但示例桥不领桥测试件
#   ⇒ 第 4 步永远 `not_picked`。根因是模板生成的桥有这三样、示例没有。

_TMPL = (_EXAMPLES.parent / "bridge_templates" / "cli_acp" / "bridge.py.tmpl").read_text(encoding="utf-8")


@pytest.mark.parametrize("cap,needle", [
    ("桥测试轮询", "pending-bridge-tests"),
    ("桥测试回报", "bridge-test-result"),
    ("桥坐标登记", "bridge-path"),
    ("单实例锁", "acquire_single_instance"),
    ("心跳", "heartbeat_loop"),
    ("任务回报", "task-result"),
    ("激活回报", "activation-result"),
])
def test_example_bridge_capability_parity_with_template(cap, needle):
    """**能力对齐**判据：模板生成的桥有的能力，示例桥也必须有。

    为什么要有这条：验证间用的就是这份示例 ⇒ 示例缺一样，A 档就在验证间过不去；
    而"持久跑的那只"是模板生成的 ⇒ 两边能力不等，等于"验的和跑的不是同一种桥"。
    """
    assert needle in _TMPL, f"模板里居然没有{cap}？先核对这条判据"
    assert needle in _CODE, f"示例桥缺「{cap}」（{needle}）—— 验证间就会卡在这一步"


def test_example_bridge_fills_acp_command_from_platform():
    """#13 的正解：没传 `--acp-command` 时**向平台取**（验证间不会带这个参数）。"""
    assert "_fill_acp_command_from_platform" in _CODE
    assert "/api/harness/" in _CODE and "acp_command" in _CODE


# ── #15 按接口不同引导 + 不匹配告警 ───────────────────────────────────

@pytest.mark.parametrize("wm,expect", [
    ("acp", ("需要", "需要", "需要")),          # 三步都要走桥
    ("file_poll", ("需要", "需要", "需要")),
    ("http_api", ("不适用", "不适用", "不适用")),  # 平台直推，不需要桥
    ("mcp", ("不适用", "不适用", "不适用")),
    ("clipboard", ("不适用", "可选", "可选")),    # 人工中转：没有叫醒命令
])
def test_guidance_steps_differ_by_interface(wm, expect):
    b = ig.summary_block(wm)
    got = (b["steps"][ig.STEP_WAKE], b["steps"][ig.STEP_BRIDGE], b["steps"][ig.STEP_PERSIST])
    assert got == expect, f"{wm} 的五步引导不对：{got}"


def test_guidance_unknown_is_honest():
    b = ig.summary_block("something-weird")
    assert b["tier_hint"] == "未知"
    assert "平台不知道" in "".join(b["platform_cannot"])


def test_guidance_flow_text_shares_one_truth():
    """文本版与结构版必须同源（规则 20：不留两份口径）。"""
    t = ig.flow_text("http_api")
    b = ig.summary_block("http_api")
    assert b["interface_label"] in t
    assert b["where_tasks_go"] in t


def test_kind_warning_speaks_on_mismatch():
    """#15：桥型与接口不匹配时必须说话（不禁止）。"""
    w = ig.bridge_kind_warning("http_api", "pending_poll")
    assert w and "不需要桥" in w
    w2 = ig.bridge_kind_warning("file_poll", "cli_acp")
    assert w2 and "不匹配" in w2
    # 匹配时**不许**瞎告警（否则是 alert fatigue）
    assert ig.bridge_kind_warning("file_poll", "file_poll") == ""
    assert ig.bridge_kind_warning("acp", "cli_acp") == ""


def test_register_response_carries_interface_guidance():
    """端点契约：注册响应必须带上按接口引导（界面/话术从它取）。"""
    src = (Path(vs.__file__).resolve().parent / "routers" / "harness_register.py").read_text(encoding="utf-8")
    assert '"interface_guidance"' in src
    assert '"next_steps_text"' in src
    assert "interface_guidance import" in src


def test_bridge_generate_response_carries_kind_warning():
    src = (Path(vs.__file__).resolve().parent / "routers" / "harness_bridge.py").read_text(encoding="utf-8")
    assert '"kind_warning"' in src
    assert "bridge_kind_warning" in src


# ══════════════════════════════════════════════════════════════════════════
# 老板 2026-10-13：「注册模板增加索引，给各种接口的适配性高效性排等级，
#                    不同接口匹配不同桥」
# ══════════════════════════════════════════════════════════════════════════
from agent_community.platform import bridge_catalog as bc   # noqa: E402

_REG = (Path(vs.__file__).resolve().parent.parent / "frontend" / "register.html").read_text(encoding="utf-8")


def test_every_wakeup_method_has_a_grade():
    """每个接口都要有等级（不许有"没评级"的接口）。"""
    for wm in ("http_api", "mcp", "acp", "file_poll", "clipboard", "http"):
        spec = bc.match(wm)
        assert spec is not None, f"{wm} 没有等级定义"
        assert spec["grade"] in bc.GRADES, f"{wm} 的等级 {spec['grade']} 不在 {bc.GRADES} 里"
        assert isinstance(spec["score"], int) and 1 <= spec["score"] <= 5
        assert spec["grade_label"]


def test_ranking_is_ordered_and_stable():
    rows = bc.rank_table()
    scores = [r["score"] for r in rows]
    assert scores == sorted(scores, reverse=True), "等级表必须按高效性从好到差排"
    # 已知判据：直推（无桥）比桥驱动好；桥驱动比人工好；人工比"不叫醒"好
    order = {r["wakeup_method"]: r["score"] for r in rows}
    assert order["http_api"] > order["acp"] > order["clipboard"] > order["http"]
    assert order["mcp"] == order["http_api"], "MCP 与直推同档（都不需要桥）"


def test_full_auto_flag_matches_reality():
    """`fully_auto` 必须与 interface_guidance 的"要不要桥/要不要人"一致（两处口径不许打架）。"""
    for wm in ("http_api", "mcp", "acp", "file_poll"):
        assert bc.match(wm)["fully_auto"] is True, f"{wm} 应标全自动"
    for wm in ("clipboard", "http"):
        assert bc.match(wm)["fully_auto"] is False, f"{wm} 不该标全自动"


def test_bridge_matching_per_interface():
    """★不同接口匹配不同桥：首选桥型逐接口核对。"""
    assert bc.match("acp")["primary_bridge"] == "cli_acp"
    assert bc.match("file_poll")["primary_bridge"] == "file_poll"
    assert bc.match("clipboard")["primary_bridge"] == "pending_poll"
    assert bc.match("http")["primary_bridge"] == "pending_poll"
    assert bc.match("http_api")["primary_bridge"] == "", "直推型不该有桥"
    assert bc.match("mcp")["bridges"] == []


def test_bridge_fit_verdicts():
    assert bc.bridge_fit("acp", "cli_acp")["fit"] == "best"
    assert bc.bridge_fit("acp", "pending_poll")["fit"] == "mismatch"
    assert bc.bridge_fit("http_api", "pending_poll")["fit"] == "redundant"   # 多余链路
    assert bc.bridge_fit("file_poll", "pending_poll")["fit"] == "ok"         # 可用非首选
    assert bc.bridge_fit("???", "pending_poll")["fit"] == "unknown"


def test_reverse_index_covers_all_bridges():
    rev = bc.bridges_to_wakeups()
    assert set(rev) == {"cli_acp", "file_poll", "pending_poll"}
    assert "acp" in rev["cli_acp"]
    # 反向索引里每个接口都排过序（等级高的在前）
    for k, wms in rev.items():
        scores = [bc.match(w)["score"] for w in wms]
        assert scores == sorted(scores, reverse=True), f"{k} 的接口没按等级排"


def test_catalog_endpoint_exposes_index_and_ranking():
    """索引性内容必须由**接口**给出（界面不许手抄一份）。

    ⚠️ 这里**真调端点函数**、断言响应体 —— 上一版只扫源码文本，
       变异"把 wakeup_ranking 改成 []"时**没被杀掉**（没牙）。已改。
    """
    import asyncio
    from agent_community.platform.routers import harness_bridge as hb

    r = asyncio.run(hb.bridge_catalog_list())
    assert r.get("success") is True
    assert r.get("wakeup_ranking"), "端点没给等级表（界面就只能手抄一份）"
    assert len(r["wakeup_ranking"]) >= 6
    assert r["wakeup_ranking"][0]["score"] >= r["wakeup_ranking"][-1]["score"], "等级表没排序"
    assert r.get("bridge_to_wakeups") and "cli_acp" in r["bridge_to_wakeups"]
    assert r.get("grade_labels") and "S" in r["grade_labels"]
    assert r.get("kinds"), "桥型清单不能丢"


def test_register_guidance_carries_grade_and_bridge():
    b = ig.summary_block("acp", harness_id="H1")
    assert b["grade"] == "A" and b["primary_bridge"] == "cli_acp"
    assert b["compatible_bridges"] == ["cli_acp"]
    assert b["efficiency"]["fully_auto"] is True
    t = ig.flow_text("http_api", harness_id="H1")
    assert "适配/高效性等级：S" in t and "不需要桥" in t


def test_register_template_has_index_and_rank_section():
    """注册模板要有**索引**（老板要求），且等级表要出现在页面上。"""
    assert "⚑ 索引" in _REG, "注册模板缺索引块"
    assert "rankPanel" in _REG and "接口适配 / 高效性等级" in _REG
    # 索引里的锚点必须真的存在（否则是"指不存在的路"）
    import re
    anchors = re.findall(r'href="#([A-Za-z0-9_]+)"', _REG)
    ids = set(re.findall(r'id="([A-Za-z0-9_]+)"', _REG))
    assert anchors, "索引里一个跳转都没有？"
    missing = [a for a in anchors if a not in ids]
    assert not missing, f"索引指向了不存在的锚点：{missing}"


def test_register_template_lists_per_interface_bridge_matching():
    """模板里必须写清"不同接口 → 不同桥"（老板原话）。"""
    for needle in ("cli_acp（首选）", "pending_poll（可用，非首选）", "不需要桥"):
        assert needle in _REG, f"模板缺「{needle}」"
    # 六种接口都要在模板里出现
    for wm in ("http_api", "mcp", "acp", "file_poll", "clipboard", "http"):
        assert wm in _REG, f"模板没提接口 {wm}"
