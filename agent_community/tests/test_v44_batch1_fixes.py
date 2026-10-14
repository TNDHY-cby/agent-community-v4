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

import os
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


# ── 老板 2026-10-13「最优解」：口径唯一化（防止再冒出第三套字母）────────────

def test_only_one_letter_alphabet_in_use():
    """**字母只允许一套**：接口适配等级 S/A/B/C/D。

    历史债（本测试就是防它复发）：同一个"挡位"曾有**三份定义** ——
      ① `tier_guidance` 的接入档位 A/B/C/D；② 自测题的 A~E；③ V46 的适配等级 S/A/B/C/D
      ⇒ 同一句话里"B 档"能指三个东西。现在：①降级为**事实判断**、②改叫 **Q1~Q5**、
      字母只留给 ③。
    """
    from agent_community.platform import tier_guidance as tg
    # ① 事实判断取代档位名：对外字典里不许再出现"档位标签"
    g = tg.guidance_at_register(type("I", (), {"wakeup_method": "clipboard", "metadata": {}})())
    assert "access_tier_label" not in g, "又输出'X 档'标签了（字母只能有一套）"
    for key in ("interface_class", "can_be_woken_by_platform", "needs_protocol"):
        assert key in g, f"接入事实缺 {key}"
    assert g["interface_class"] in tg.CLASS_LABEL
    # ③ 等级字母表本身只有一套
    assert bc.GRADES == ("S", "A", "B", "C", "D")


def test_register_template_has_no_bare_tier_letters():
    """用户可见文案里不许再出现裸的"A 档/B 档"（会与等级 S/A/B/C/D 打架）。"""
    import re
    bad = [(i, l.strip()[:100]) for i, l in enumerate(_REG.split("\n"), 1)
           if re.search(r"[A-E] 档", l)]
    assert not bad, f"模板里还有'X 档'字样：{bad[:3]}"


def test_self_test_answers_are_q_numbered():
    """自测选项必须是 **Q1~Q5**（它只是"你属于哪种接口"，不是等级）。

    ⚠️ 断言必须**同时覆盖"选哪个"与"映射表"两处**：上一版只查 `Q1 / Q2 …` 字样，
       变异把「选 **Q1**」改回「选 **A**」时**没被杀掉**（别处还有 Q1 字样）。
    """
    import re
    lines = _REG.split("\n")
    # ① "选 **Qn**" 必须逐个存在
    for q in ("Q1", "Q2", "Q3", "Q4", "Q5"):
        assert re.search(rf"选 \*\*{q}\*\*", _REG), f"自测题缺「选 **{q}**」"
        assert f"{q} " in _REG, f"映射表缺 {q}"
    # ② 映射表五行的左列必须是 Q1~Q5（不是 A~E）
    mapped = [l for l in lines if re.match(r"\s+Q[1-5] ", l)]
    assert len(mapped) >= 5, f"映射表没按 Q1~Q5 编号：{mapped[:6]}"
    assert not [l for l in lines if re.match(r"\s+[A-E] (命令拉起|盯文件夹|网页接口|手动贴|测不出来)", l)], \
        "映射表又用回 A~E 了"
    # ③ 必须有一句话说清"Q 编号不是等级"
    assert "**不是等级**" in _REG, "缺「Q 编号不是等级」的说明"


def test_register_response_has_no_legacy_tier_field():
    """注册响应里不许再出现旧的 `access_tier` 顶层字段（改名为 access_facts）。"""
    src = (Path(vs.__file__).resolve().parent / "routers" / "harness_register.py").read_text(encoding="utf-8")
    assert '"access_facts": _access' in src
    assert '"access_tier": _tier' not in src, "旧的档位字段又回来了"


# ── 端口/地址：一处真相 + 因人而异（2026-10-13 老板口径）──
from pathlib import Path

from agent_community.platform_address import DEFAULT_PORT, ENV_PORT, base_url, resolve_port
from agent_community.platform import verify_sandbox as vs

_ROOT = Path(vs.__file__).resolve().parent.parent.parent          # 仓库根（agent_community 的父）
_PKG = Path(vs.__file__).resolve().parent.parent                  # agent_community/


def test_port_resolution_priority(monkeypatch):
    """显式 > AC_PORT > 默认（端口是**用户自己的事**）。"""
    monkeypatch.delenv(ENV_PORT, raising=False)
    assert resolve_port() == DEFAULT_PORT
    assert resolve_port(45000) == 45000, "命令行必须最优先"
    monkeypatch.setenv(ENV_PORT, "34567")
    assert resolve_port() == 34567, "AC_PORT 必须生效"
    assert resolve_port(45000) == 45000, "命令行仍压过 AC_PORT"
    assert base_url() == "http://127.0.0.1:34567"
    monkeypatch.setenv(ENV_PORT, "not-a-port")          # 坏值不该崩
    assert resolve_port() == DEFAULT_PORT
    monkeypatch.setenv(ENV_PORT, "70000")               # 越界也要退回默认
    assert resolve_port() == DEFAULT_PORT


def test_default_port_has_single_definition():
    """**默认端口只有一处**：别的地方必须引用 `platform_address`，不许再写数字。

    这条直接防住原来的病：`cli.py=9103`、`server.py=9103`、`bridge_template/verify_sandbox/
    protocol_catalog/gui/watchdog=18920` —— 同一件事六处各写一套。
    """
    offenders = []
    for py in list((_PKG / "platform").rglob("*.py")) + list(_PKG.glob("*.py")) + list((_PKG / "tools").glob("*.py")):
        if py.name == "platform_address.py":
            continue
        for i, l in enumerate(py.read_text(encoding="utf-8", errors="replace").split("\n"), 1):
            s = l.strip()
            if s.startswith("#"):
                continue
            # 只抓"把它当默认端口用"的写法；测试/注释/示例文本不算
            if re.search(r'AC_PORT",\s*"\d+"', s) or re.search(r"^DEFAULT_PORT\s*=\s*\d+", s):
                offenders.append(f"{py.relative_to(_ROOT)}:{i}: {s[:80]}")
    assert not offenders, f"默认端口又被写死了（应引用 platform_address）：{offenders}"


def test_platform_url_helpers_have_no_hardcoded_fallback():
    """平台地址解析点不许再硬编码 `127.0.0.1:18920` 当兜底。"""
    for rel in ("platform/bridge_template.py", "platform/verify_sandbox.py", "platform/server.py"):
        t = (_PKG / rel).read_text(encoding="utf-8")
        code = "\n".join(l for l in t.split("\n") if not l.strip().startswith("#"))
        assert "return \"http://127.0.0.1:18920\"" not in code, f"{rel} 又硬编码了本机地址兜底"


def test_user_facing_surfaces_have_no_baked_port():
    """用户可见面不写死端口：README 用 $PORT/占位；hub 页用相对路径 / 当前 origin。"""
    checks = {
        "README.md": r"127\.0\.0\.1:18920",
        "agent_community/frontend/hub.html": r"127\.0\.0\.1:\d+",
        "agent_community/frontend/hub.js": r"127\.0\.0\.1:\d+",
    }
    for rel, rx in checks.items():
        p = _ROOT / rel
        if not p.is_file():
            continue
        hits = [(i, l.strip()[:90]) for i, l in enumerate(p.read_text(encoding="utf-8").split("\n"), 1)
                if re.search(rx, l)]
        assert not hits, f"{rel} 里还有写死的本机网址：{hits[:3]}"


def test_bridge_templates_dont_teach_a_baked_address():
    """桥模板的**示例命令**不许写死本机地址（用户会照抄）。

    允许的地方：`--url` 的**运行时默认值**（它们走 `_platform_url_default()` / 模板注入的 PLATFORM_URL）。
    """
    for tmpl in (_PKG / "bridge_templates").rglob("*.tmpl"):
        t = tmpl.read_text(encoding="utf-8")
        for i, l in enumerate(t.split("\n"), 1):
            if "127.0.0.1:18920" not in l:
                continue
            # 只允许出现在 _platform_url_default 的兜底里
            assert "_platform_url_default" in l or 'os.environ.get("AC_PORT")' in l, \
                f"{tmpl.name}:{i} 写死了本机地址：{l.strip()[:80]}"


# ── 数据落点：必须认 AC_DATA_DIR（"路径也得因人而异"）────────────────────

def test_data_roots_respect_ac_data_dir(tmp_path):
    """所有数据落点都要跟着 `AC_DATA_DIR` 走（换目录/多实例不许往仓库里写）。

    ★★ 2026-10-14 重写（这条原来自己就是污染源，务必看明白再改）：
      原实现用 `importlib.reload(module)` 去验"落点在导入时算"，**看起来无副作用，其实有毒**：
        · `platform/memory.py` 里有**模块级单例** `task_memory = TaskMemory()`；
        · `reload()` 会**重建**这个单例 ⇒ `memory.task_memory` 指向**新对象 B**，
          而 `routers/workshops_discuss.py` 早就在导入时 `from ..memory import task_memory`
          绑住了**旧对象 A**；
        · conftest 的隔离每轮只 patch **`memory.task_memory`（当时那个）** ⇒ 从那以后
          它一直在 patch B，**A 没人管**，A.data_dir 恢复成真实目录；
        · 于是后面任何走 A 的用例（实测：`test_workshops_discuss` 的 complete 流程
          → `experience_v2` → `task_memory.add` → `_save`）都**写进真实 data/**，
          会话末被 `guard_real_data_dir` 抓成「测试污染了真实数据」。
      **为什么开发机上一直没暴露**：dev 的 `agent_community/data/` 本来就有文件，
      守卫按"内容变了才报"判，尺寸恰巧没变就放过了；而 CI 是**全新 clone**，
      `memory/task_memory.json` 从"不存在"变成"存在" ⇒ 必报。
      ⇒ 又是"我这台能跑不算数"。

      现在的做法：**开子进程验**（`AC_DATA_DIR` 只在那条子进程里设）。
      既能验"导入时确实读环境变量"，又**完全不碰本进程的模块状态**、不破坏任何单例身份。
    """
    import subprocess
    import sys as _sys

    code = (
        "import json,sys\n"
        "sys.path.insert(0, r'%s')\n"
        "from agent_community.platform import memory, ai_external, local_ai, identity\n"
        "out = {\n"
        "  'memory': str(memory.DEFAULT_MEMORY_DIR),\n"
        "  'ai_external': str(ai_external.PENDING_DIR),\n"
        "  'local_ai': str(local_ai.CONFIG_PATH),\n"
        "  'identity': str(identity._default_data_root()),\n"
        "}\n"
        "print(json.dumps(out, ensure_ascii=False))\n" % str(_ROOT)
    )
    env = {**os.environ, "AC_DATA_DIR": str(tmp_path), "PYTHONIOENCODING": "utf-8"}
    r = subprocess.run([_sys.executable, "-c", code], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env=env, cwd=str(_ROOT))
    assert r.returncode == 0, f"子进程取落点失败：{r.stdout[-800:]}{r.stderr[-800:]}"
    import json as _json
    got = _json.loads(r.stdout.strip().splitlines()[-1])
    for name, path in got.items():
        assert path.startswith(str(tmp_path)), \
            f"{name} 没认 AC_DATA_DIR：{path}（应落在 {tmp_path} 下）"


def test_no_module_hardcodes_package_data_root():
    """**不许**再有模块把数据落点写死在包内 `data/`（要经过 AC_DATA_DIR）。

    这条防的是"同一件事散在多处、只改一处"（规则 20）——
    2026-10-13 实测就是 5 个模块各自写死，换目录后仓库里仍长出 data/。
    """
    import re
    offenders = []
    for py in (_PKG / "platform").rglob("*.py"):
        for i, l in enumerate(py.read_text(encoding="utf-8", errors="replace").split("\n"), 1):
            s = l.strip()
            if s.startswith("#") or "AC_DATA_DIR" in s:
                continue
            # 形如 X = Path(...) / "data" （没有 AC_DATA_DIR 兜底）
            if re.search(r'=\s*Path\(__file__\).*?/\s*"data"\s*$', s) or \
               re.search(r'=\s*Path\(__file__\).*?/\s*"data"\s*/\s*"', s):
                offenders.append(f"{py.name}:{i}: {s[:80]}")
    assert not offenders, f"这些落点没认 AC_DATA_DIR：{offenders}"


# ── 验收层：可移植性闸门必须进 pytest（陌生人只跑 pytest 也能撞上）──────────
#
# 为什么把闸门也做成测试：老板的三层制度里，验收层是「CI / pre-commit 上会红的 grep 检查」。
# 但**陌生人拿到代码第一件事是 `pytest`**，不是读 CI 配置。
# 三条自查如果只在 CI 里，本地就可能"绿着把硬编码提交上去"。
# 这里包一层，等于给 `pytest` 也装上同一把闸门（同一处真相，不复制规则）。

def test_portability_gate_passes():
    """`tools/check_portability.py` 必须 fail=0（产品面不许有硬编码）。"""
    import subprocess, sys as _sys
    root = _PKG.parent
    r = subprocess.run([_sys.executable, str(root / "tools" / "check_portability.py")],
                       cwd=str(root), capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    assert r.returncode == 0, (
        "可移植性扫描没通过（产品面有硬编码端口/地址/路径）——\n"
        + (r.stdout or "")[-3000:] + (r.stderr or "")[-1000:])
    assert "fail=0" in (r.stdout or ""), f"扫描没报出 fail=0，结果不可信：{(r.stdout or '')[:400]}"


def test_env_docs_are_not_drifted():
    """`.env.example` / README 环境变量表必须与 `env_spec` 一致（改一处漏一处的解药）。"""
    import subprocess, sys as _sys
    root = _PKG.parent
    r = subprocess.run([_sys.executable, str(root / "tools" / "gen_env_docs.py"), "--check"],
                       cwd=str(root), capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    assert r.returncode == 0, (
        "环境变量文档漂移了（改了 env_spec 却没重新生成）——跑 "
        "`python tools/gen_env_docs.py` 重新生成：\n"
        + (r.stdout or "")[-2000:] + (r.stderr or "")[-1000:])


def test_tests_only_bind_ephemeral_ports():
    """测试**不许绑固定端口**（否则别人的机器上端口被占就红，且多实例互撞）。

    只认 `("127.0.0.1", 0)` / `("localhost", 0)` 这种"让内核挑端口"的写法。
    传给了进程/桥但不真连的地址字符串不在管辖内 —— 那些走 `platform_address`。
    """
    import re
    # 只在**真会绑端口**的调用上判（构造函数 / bind / listen）；
    # 否则 `a2a._agent_card("127.0.0.1", 9104)` 这种"拼一个名片"也会被误判。
    bind_call = re.compile(r'(?:HTTPServer|TCPServer|socketserver|\.bind|\.listen)\s*\(')
    bad = []
    pat = re.compile(r'\(\s*["\'](?:127\.0\.0\.1|localhost|0\.0\.0\.0)["\']\s*,\s*(\d+)\s*\)')
    for py in sorted(_PKG.joinpath("tests").glob("*.py")):
        for i, l in enumerate(py.read_text(encoding="utf-8", errors="replace").split("\n"), 1):
            if l.strip().startswith("#") or not bind_call.search(l):
                continue
            m = pat.search(l)
            if m and m.group(1) != "0":
                bad.append(f"{py.name}:{i}: {l.strip()[:80]}")
    assert not bad, (
        "这些测试绑了固定端口，请改成 `(..., 0)` 让内核随机分配（跨机可跑）：\n  "
        + "\n  ".join(bad))


# ── ★2026-10-14 验收层实测抓出来的两个真缺陷（下面每条都是"先红后绿"的）─────────
#
# 背景：老板要的是「在陌生环境里只改环境变量就能跑」。
# 于是我**真按陌生人的方式跑了一遍**（只改环境变量、起真服务、看实际结果），抓出：
#   缺陷 A：`AC_HOST` 登记进了 env_spec / .env.example / README，**但改它没用** ——
#           `server.py` 末尾直接 `uvicorn.run(app, host="0.0.0.0")`。
#           实测 `AC_HOST=127.0.0.1` 之后仍然监听 `0.0.0.0`。
#           ⇒ 「文档承诺了一个改不动的开关」，比没写更坏。
#   缺陷 B：`AC_ENV=production` 缺必需项时**只打印警告照样起服务**。
#           上一版把 production 的两条都写成 `warn`（理由是"别让开箱即用被自己否掉"），
#           可那只对开发成立 ⇒ "生产缺必需项就启动失败"这条承诺等于没做。
#
# 教训：**这两条都不是靠读代码发现的，是"起真服务看真结果"发现的。**
# 所以守卫也必须按"真结果"写，不许退化成源码文本断言。

def test_host_resolution_priority(monkeypatch):
    """监听地址：命令行 `--host` > `AC_HOST` > 默认（不许再写死在 server.py 里）。"""
    from agent_community import platform_address as pa
    monkeypatch.delenv(pa.ENV_HOST, raising=False)
    assert pa.resolve_host() == pa.DEFAULT_HOST
    monkeypatch.setenv(pa.ENV_HOST, "127.0.0.1")
    assert pa.resolve_host() == "127.0.0.1", "AC_HOST 必须生效（缺陷 A 的回归位）"
    assert pa.resolve_host("192.168.1.9") == "192.168.1.9", "命令行必须最优先"
    monkeypatch.setenv(pa.ENV_HOST, "   ")
    assert pa.resolve_host() == pa.DEFAULT_HOST, "空白值要当没设，别绑到空字符串上"


def test_production_missing_required_fails_startup(monkeypatch):
    """`AC_ENV=production` 缺必需项 ⇒ **必须抛错**（不是打印警告就接着起）。

    用**真调 `assert_ready()`** 判，不看源码文本 —— 缺陷 B 就是"代码里写了检查、
    但检查全是 warn"，读源码看不出来。
    """
    from agent_community.platform import env_spec as es

    # ① 开发模式：什么都不设也要能起（开箱即用优先）
    monkeypatch.setenv(es.ENV_MODE, "development")
    for k in ("AC_DATA_DIR", "AC4_SHUTDOWN_PIN", "AC4_TOKEN", "AC_HOST"):
        monkeypatch.delenv(k, raising=False)
    r = es.check_requirements()
    assert r["ok"], f"开发模式不该有硬错：{r['errors']}"
    es.assert_ready()                                  # 不该抛

    # ② 生产 + 对外暴露：缺 AC_DATA_DIR / AC4_SHUTDOWN_PIN / AC4_TOKEN ⇒ 必须抛
    monkeypatch.setenv(es.ENV_MODE, "production")
    monkeypatch.setenv("AC_HOST", "0.0.0.0")
    monkeypatch.delenv("AC_DATA_DIR", raising=False)
    monkeypatch.delenv("AC4_SHUTDOWN_PIN", raising=False)
    monkeypatch.delenv("AC4_TOKEN", raising=False)
    rep = es.check_requirements()
    assert not rep["ok"], "生产模式缺必需项却报 ok —— 缺陷 B 回归了"
    names = {e["name"] for e in rep["errors"]}
    assert {"AC_DATA_DIR", "AC4_SHUTDOWN_PIN", "AC4_TOKEN"} <= names, \
        f"该报的必需项没报全，实得 {sorted(names)}"
    try:
        es.assert_ready()
    except es.EnvError as e:
        msg = str(e)
        for n in ("AC_DATA_DIR", "AC4_SHUTDOWN_PIN", "AC4_TOKEN"):
            assert n in msg, f"报错必须点名缺哪个变量，`{n}` 没出现"
    else:
        raise AssertionError("生产模式缺必需项竟然没抛 EnvError")

    # ③ 生产 + 只在本机跑：给了数据目录即可，令牌/口令属于**非必需**（只该提示）
    monkeypatch.setenv("AC_HOST", "127.0.0.1")
    monkeypatch.setenv("AC_DATA_DIR", str(_ROOT / "temp"))
    rep2 = es.check_requirements()
    assert rep2["ok"], f"生产+回环不该因为没令牌就拒绝启动：{rep2['errors']}"


def test_server_really_binds_the_configured_host(monkeypatch):
    """**真起服务**验证监听地址（缺陷 A 的真结果版）。

    只断言 `resolve_host()` 是不够的 —— 缺陷 A 的问题恰恰在"解析函数是对的，
    但 server.py 根本没拿它去绑"。所以这里起一个真进程、看真实监听地址。
    """
    import socket
    import subprocess
    import sys
    import time

    # 挑一个当前空闲的端口（不写死端口，避免与别人的环境撞）
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        free_port = s.getsockname()[1]

    env = {**os.environ,
           "AC_PORT": str(free_port),
           "AC_HOST": "127.0.0.1",
           "AC_ENV": "development",
           "AC_DATA_DIR": str(_ROOT / "temp" / "acdata_host_guard"),
           "PYTHONIOENCODING": "utf-8"}
    env.pop("AC4_TOKEN", None)
    proc = subprocess.Popen([sys.executable, "-m", "agent_community.platform.server"],
                            cwd=str(_ROOT), env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        up = False
        for _ in range(30):
            time.sleep(1)
            try:
                with socket.create_connection(("127.0.0.1", free_port), timeout=1):
                    up = True
                    break
            except OSError:
                continue
        assert up, "服务没起来，本用例无法判定监听地址"

        # Windows/Linux 通用的判据：**从本机非回环地址去连，应当连不上**。
        # （若 host 真是 0.0.0.0，则从局域网地址也能连上 ⇒ 这条会红。）
        import socket as _s
        try:
            local_ip = _s.gethostbyname(_s.gethostname())
        except OSError:
            local_ip = ""
        if local_ip and not local_ip.startswith("127."):
            try:
                with _s.create_connection((local_ip, free_port), timeout=2):
                    external_ok = True
            except OSError:
                external_ok = False
            assert not external_ok, (
                f"AC_HOST=127.0.0.1 却能从 {local_ip}:{free_port} 连上 ⇒ "
                "AC_HOST 没被真正用来绑定（缺陷 A 回归）")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


def test_powershell_scripts_keep_their_utf8_bom():
    """`.ps1` 带中文就必须带 **UTF-8 BOM** —— 否则别人的 PS 5.1 直接 ParserError。

    ★这条是 2026-10-14 又踩了一次才补的：我用编辑工具改 `publish_acv4.ps1`，
    BOM 被顺手抹掉，文件头从 `EF BB BF` 变成 `# p`；本机 pwsh 7 照跑不误，
    而 **Windows PowerShell 5.1 会按 ANSI(GBK) 去读那份 UTF-8 中文** ⇒
    满屏"缺少右 )"之类**看不出真因**的语法错（判据混淆，极难查）。

    为什么算可移植性：这是"我这台能跑、别人那台不能"的典型 ——
    自动化工具（编辑器 / 生成脚本 / Python 写文件）**反复**会抹掉 BOM，
    靠人记得保留是不可靠的，所以必须由测试守住。

    范围＝全仓库（`**/*.ps1`），不只是根目录那几个：实测 `deploy/setup_all.ps1`
    就是"中文 + 无 BOM"的漏网之鱼（同一天发现的）。
    """
    _SKIP = (".pytest_cache", ".git", "node_modules", "temp")
    bad = []
    for ps1 in sorted(_ROOT.rglob("*.ps1")):
        rel = str(ps1.relative_to(_ROOT))
        if any(part in rel for part in _SKIP):
            continue
        raw = ps1.read_bytes()
        try:
            raw.decode("ascii")
            continue                      # 纯 ASCII 不需要 BOM
        except UnicodeDecodeError:
            pass
        if not raw.startswith(b"\xef\xbb\xbf"):
            bad.append(rel)
    assert not bad, (
        "这些 .ps1 带非 ASCII 内容却没有 UTF-8 BOM（Windows PowerShell 5.1 会按 ANSI 读、"
        "报一堆看不出真因的语法错）：\n  " + "\n  ".join(bad) +
        "\n\n修法（Python，别用会抹 BOM 的写入方式）：\n"
        "  b = open(p,'rb').read()\n"
        "  if not b.startswith(b'\\xef\\xbb\\xbf'): open(p,'wb').write(b'\\xef\\xbb\\xbf'+b)")


def test_ci_definition_is_single_and_at_the_standard_path():
    """CI 定义**只能有一份**，位置是 `.github/workflows/ci.yml`（GitHub 唯一会跑的地方）。

    本用例的来龙去脉（都是 2026-10-14 实测，别再走回头路）：
      · 最初我把定义放这里，push 被 GitHub 拒收 ——
        推送令牌是 Personal Access Token 且**没有 `workflow` 权限**：
        "refusing to allow a Personal Access Token to create or update workflow ... without 'workflow' scope"。
      · 于是我退而求其次放 `ci/ci.yml`（仓库里可审阅）+ 文档写启用步骤。
      · 老板**已在 GitHub 网页端建好** `.github/workflows/ci.yml` ⇒ 现在它才是**生效的那份**，
        而 `ci/ci.yml` 变成**死文件**：两份定义并存迟早漂移（规则 20 禁止两份口径）。
      ⇒ 所以本用例现在的立场反过来：**标准位置必须有，`ci/` 那份不许再存在。**

    ⚠️ 连带约束（写在这里免得下次又踩）：当前令牌**仍不能推送 `.github/workflows/` 的改动**。
      本地这份必须与远端**逐字节一致**，改了就得走网页端（或先给令牌补 `workflow` 权限），
      否则 `publish_acv4.ps1` 推上去会被整次拒收。
    """
    wf = _ROOT / ".github" / "workflows" / "ci.yml"
    assert wf.is_file(), (
        "`.github/workflows/ci.yml` 不见了 —— 这是 GitHub 唯一会执行 CI 的位置"
        "（构建于 2026-10-14，老板在网页端建的）。若确实要挪走，请同时更新本用例与 PORTABILITY.md。")
    text = wf.read_text(encoding="utf-8")
    for need in ("check_portability.py", "gen_env_docs.py", "ubuntu-latest"):
        assert need in text, f".github/workflows/ci.yml 少了关键内容：{need}"

    legacy = _ROOT / "ci" / "ci.yml"
    assert not legacy.exists(), (
        "`ci/ci.yml` 又出现了 —— 它已经退休（CI 定义回到标准位置）。"
        "两份定义并存必然漂移（规则 20），请删掉 `ci/`。")


def test_github_issue_templates_are_not_swept_away():
    """`.github/ISSUE_TEMPLATE/` 必须还在（oss 独有内容，别被同步脚本删掉）。

    2026-10-14 实事故：`publish_acv4.ps1` 的 1.6 步第一版用了 robocopy `/MIR` 同步 `.github/`，
    而 dev 侧**没有** `ISSUE_TEMPLATE/` ⇒ `/MIR` 忠实删除，oss 的两个模板被删掉，
    开发机上完全看不出来（dev 本来就没有）。
    现已改为 copy-only（`/E`），并把两个模板收进 dev 作为唯一真相；本条守卫防复发。
    """
    tpl = _ROOT / ".github" / "ISSUE_TEMPLATE"
    assert tpl.is_dir(), "`.github/ISSUE_TEMPLATE/` 没了（被 /MIR 同步删掉了？）"
    names = {p.name for p in tpl.glob("*.yml")}
    assert {"bug_report.yml", "feature_request.yml"} <= names, \
        f"issue 模板缺文件，实得 {sorted(names)}"

    pub_path = _ROOT / "publish_acv4.ps1"
    if not pub_path.exists():
        # `publish_acv4.ps1` 是 **dev-only**（不进发布面）⇒ 公开仓库里跑本用例时
        # 只能验"模板还在"，验不了"同步脚本没再用 /MIR"（那条由 dev 侧守）。
        # ⚠️ 2026-10-14 实测踩到：第一版没判存在，公开仓库里本用例直接 FileNotFoundError
        #   —— 又一次"开发机全绿、别人那儿红"（判据必须两边都跑，见 PORTABILITY.md）。
        return
    pub = pub_path.read_text(encoding="utf-8", errors="replace")
    # 取"同步工具面那一段"＝从它自己的 Step 到**下一段**（用下一段的注释头定位，比找 "Step " 可靠）
    at = pub.find('Step "sync portability tooling')
    assert at != -1, "找不到同步工具面那一段，脚本结构变了，请同步更新本守卫"
    nxt = pub.find("# 目录型：design-docs", at)
    seg = pub[at:nxt if nxt != -1 else len(pub)]
    assert "'/MIR'" not in seg and '"/MIR"' not in seg, \
        "同步工具面又用上 /MIR 了 —— 它会删掉 oss 独有的文件（2026-10-14 的 ISSUE_TEMPLATE 事故）"


# ── 子进程的平台差异：**只有一处真相**（2026-10-14 CI 逼出来的产品 bug）────────
#
# 事故：Linux 上"一键注册"直接 500，原文
#     {"error": "桥启动失败: module 'subprocess' has no attribute 'CREATE_NEW_PROCESS_GROUP'"}
# `CREATE_NEW_PROCESS_GROUP` / `DETACHED_PROCESS` / `CREATE_NO_WINDOW` **只存在于 Windows**。
# 同一件事当时散在 5 处（3 处无条件用、1 处写成硬编码 0x8|0x200、另有 2 处自己做了平台分支）
# ⇒ 现在统一到 `platform/spawn.py::spawn_kwargs()`。

def test_spawn_kwargs_is_platform_aware(monkeypatch):
    """`spawn_kwargs()` 两个平台都要给出**各自可用**的参数（行为断言，不是看源码）。

    ★2026-10-14 重写（CI run #11 的 Linux 真因，**是这条守卫自己错了**）：
      原实现无条件断言「`_IS_WINDOWS=True` ⇒ 必有非零 `creationflags`」——
      这条**在 Linux 上永远不可能成立**：`subprocess.CREATE_NEW_PROCESS_GROUP` /
      `DETACHED_PROCESS` 这两个常量**在 Linux 上根本不存在**，而实现里用
      `getattr(..., 0)` 兜底是**故意的**（缺常量时只是少个标志，不该把功能打成 500）
      ⇒ flags 算出来就是 0、返回 `{}` ⇒ 断言必红（windows job 绿、linux job 红）。
      **教训**：守"平台差异"的用例，必须自己在**两个平台上都成立**；
      否则它不是守卫，是"只在我这台机器上成立"的又一份私人定制。
      现在的判据（两平台都成立、且仍然有牙）：
        ① 真平台分支：本机该给什么就给什么；
        ② ②③ 跨分支：切到另一个平台时，**不许给出那个平台不该用的参数**
           （Windows 分支给 `start_new_session`、POSIX 分支给 `creationflags` 都是错的 ——
             后者正是把 Linux 一键注册打成 500 的那个 bug）。
    """
    import os as _os
    import subprocess as _sp

    from agent_community.platform import spawn as sp

    # ⚠️ 这里**只能** patch 那个开关，**绝不能** patch `os.name`：
    #   3.11 的 pathlib 是实例化时读 os.name 的 ⇒ 改全局会让别处造出 WindowsPath 对象，
    #   逃出去后在 Linux 上炸成 pytest INTERNALERROR（我实测踩过，就这一条守卫造成的）。
    real_windows = sp._IS_WINDOWS                      # 模块开关（导入时由 os.name 定）
    assert real_windows == (_os.name == "nt"), \
        "开关应与本机平台一致（否则守卫的前提就不成立）"

    # ① 真平台分支：本机该给什么给什么
    kw = sp.spawn_kwargs()
    if real_windows:
        assert kw.get("creationflags"), f"Windows 上应给非零 creationflags：{kw}"
        assert "start_new_session" not in kw, f"Windows 上不该给 POSIX 参数：{kw}"
    else:
        assert kw.get("start_new_session") is True, f"POSIX 应用 start_new_session：{kw}"
        assert "creationflags" not in kw, f"POSIX 不许出现 creationflags：{kw}"

    # ② 切到 Windows 分支：绝不许漏出 POSIX 参数；本机有那两个常量时必须真用上
    monkeypatch.setattr(sp, "_IS_WINDOWS", True)
    kw = sp.spawn_kwargs()
    assert "start_new_session" not in kw, f"Windows 分支不许给 POSIX 参数：{kw}"
    if hasattr(_sp, "CREATE_NEW_PROCESS_GROUP"):
        assert kw.get("creationflags"), f"本机有 Windows 标志常量，就该用上：{kw}"

    # ③ 切到 POSIX 分支：**绝不能**出现 creationflags（那正是把 Linux 打成 500 的东西）
    monkeypatch.setattr(sp, "_IS_WINDOWS", False)
    kw = sp.spawn_kwargs()
    assert "creationflags" not in kw, f"POSIX 不许出现 creationflags：{kw}"
    assert kw.get("start_new_session") is True, f"POSIX 应用 start_new_session：{kw}"

    # ④ detach=False：两个平台都不加任何脱离相关的参数
    assert sp.spawn_kwargs(detach=False) == {}
    monkeypatch.setattr(sp, "_IS_WINDOWS", True)
    assert "start_new_session" not in sp.spawn_kwargs(detach=False)


def test_subprocess_platform_flags_live_in_one_place():
    """Windows 专属的子进程标志，只允许出现在 `platform/spawn.py`（或已做平台分支处）。

    判据：**直接属性访问**（`subprocess.CREATE_NEW_PROCESS_GROUP` / `_sp.DETACHED_PROCESS` …）
    在没有平台判断的情况下就是错的；`getattr(subprocess, "…", 0)` 与 `spawn_kwargs()` 是有意的写法。
    """
    import re
    names = ("CREATE_NEW_PROCESS_GROUP", "DETACHED_PROCESS", "CREATE_NO_WINDOW")
    offenders = []
    for py in (_PKG / "platform").rglob("*.py"):
        if py.name == "spawn.py":
            continue
        txt = py.read_text(encoding="utf-8", errors="replace")
        for i, l in enumerate(txt.splitlines(), 1):
            if not any(n in l for n in names):
                continue
            if l.lstrip().startswith("#") or "getattr(" in l:
                continue                      # 注释 / 已用 getattr 兜底
            # 平台分支里允许（看本行与上面 4 行有没有平台判断）
            window = "\n".join(txt.splitlines()[max(0, i - 5):i])
            if ("sys.platform" in window or "os.name" in window
                    or "_os.name" in window):
                continue
            offenders.append(f"{py.name}:{i}: {l.strip()[:90]}")
    assert not offenders, (
        "这些地方**无条件**用了 Windows 专属的子进程标志（Linux 上 AttributeError，"
        "实测把一键注册打成 500）：\n  " + "\n  ".join(offenders) +
        "\n\n修法：改用 `agent_community/platform/spawn.py::spawn_kwargs()`。")


def test_no_test_monkeypatches_global_os_name():
    """测试**不许** monkeypatch 全局 `os.name`（会连锁炸掉 pathlib）。

    ★实证（2026-10-14，run #8 的 Linux job）：我写的一条守卫为了验另一个平台分支，
    写了 `monkeypatch.setattr(os, "name", "nt")`。而 CPython 3.11 的
    `pathlib.Path.__new__` 是**实例化时读 `os.name`** 决定造 Posix/WindowsPath 的
    ⇒ 那个窗口里造出的 `Path` 是 `WindowsPath`；只要有一个对象逃出该用例
    （实测：pytest 自己的失败报告里就有一个），会话后半程就炸成：

        NotImplementedError: cannot instantiate 'WindowsPath' on your system
        _pytest/nodes.py: abspath = Path(os.getcwd()) != ...

    整场变成 INTERNALERROR —— **不是某个用例失败，是所有结果都不可信**。
    正确姿势：把平台判断提成被测模块自己的开关（如 `platform/spawn.py::_IS_WINDOWS`），
    patch 那个开关，而不是 patch 全局。
    """
    import re
    bad = []
    for py in sorted(_PKG.joinpath("tests").glob("*.py")):
        in_doc = False
        for i, l in enumerate(py.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            # 三引号状态机：docstring 里的**示例文本**不是代码
            # （本守卫第一版就栽在这：它把本文档字符串里那行示例当成了真代码）
            if l.count('"""') % 2 == 1:
                in_doc = not in_doc
                continue
            if in_doc or l.lstrip().startswith("#"):
                continue
            # setattr(os, "name", ...) / setattr(sp.os, "name", ...) / os.name = ...
            if re.search(r'setattr\([^,]*\bos[^,]*,\s*["\']name["\']', l) or \
               re.search(r'^\s*os\.name\s*=', l):
                bad.append(f"{py.name}:{i}: {l.strip()[:90]}")
    assert not bad, (
        "这些地方 monkeypatch 了**全局 `os.name`** —— 会让 pathlib 造出 WindowsPath，"
        "进而把整场 pytest 炸成 INTERNALERROR（所有结果都不可信）：\n  " + "\n  ".join(bad) +
        "\n\n修法：在**被测模块**里留一个平台开关（如 `_IS_WINDOWS`），patch 那个。")
