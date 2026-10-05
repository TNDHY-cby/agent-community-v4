# -*- coding: utf-8 -*-
"""V-15 步骤6 测试：接入档位判定 + 提醒工作（§6.3 / §10）。

对应设计稿 §十四 步骤6 的验证要求：
    「D 档无协议 harness 注册后 `needs_protocol=true` + 引导文案」

红线（§6.3）：**表达需求，不指定实现** —— 平台不为每个 harness 写安装步骤。
所以本文件有一个用例专门盯住：引导文案里**不得**出现具体安装命令。
"""
from __future__ import annotations

import asyncio
import re

import pytest

from agent_community.platform import tier_guidance as tg
from agent_community.platform.protocol import HarnessInfo, HarnessTool, WakeupMethod


def _info(wm, **kw):
    base = dict(harness_id="示例Harness-A", harness_name="示例Harness-A",
                wakeup_method=wm, acp_command="npx -y acp-bridge",
                ai={"model_name": "示例模型"})
    base.update(kw)
    return HarnessInfo(**base)


class FakeReq:
    def __init__(self, body=None, headers=None):
        self.headers = headers or {}
        self._b = body if body is not None else {}

    async def json(self):
        return self._b


def run(coro):
    return asyncio.run(coro)


# ── 档位判定（§6.2 接入路径是棵树）────────────────────────────
class TestTierDetection:
    def test_acp_is_tier_a(self):
        assert tg.tier_of(_info(WakeupMethod.ACP)) == tg.TIER_A

    def test_mcp_with_session_tool_is_tier_b(self):
        """拍板 #2：静默发现 new_session 类工具 -> B。"""
        info = _info(WakeupMethod.MCP, api_base_url="http://127.0.0.1:9527",
                     tools=[HarnessTool(name="read_file"),
                            HarnessTool(name="new_session")])
        assert tg.tier_of(info) == tg.TIER_B

    def test_mcp_without_session_tool_is_tier_b_but_flagged(self):
        info = _info(WakeupMethod.MCP, api_base_url="http://127.0.0.1:9527",
                     tools=[HarnessTool(name="read_file")])
        assert tg.tier_of(info) == tg.TIER_B
        g = tg.guidance_at_register(info)
        assert g["guidance_kind"] == "incomplete"
        assert "未发现" in g["guidance"]

    def test_no_protocol_defaults_to_tier_d(self):
        """平台探测不到插件市场 -> 默认 D（给插件引导），不擅自判 C。"""
        info = _info(WakeupMethod.CLIPBOARD)
        assert tg.tier_of(info) == tg.TIER_D

    def test_explicit_no_marketplace_is_tier_c(self):
        """只有显式声明没有插件市场才判 C。"""
        info = _info(WakeupMethod.CLIPBOARD,
                     metadata={"plugin_marketplace": False})
        assert tg.tier_of(info) == tg.TIER_C

    def test_needs_protocol_flag(self):
        """needs_protocol 只对无原生协议的档位成立（A/B 不该被打标）。"""
        assert tg.guidance_at_register(_info(WakeupMethod.ACP))["needs_protocol"] is False
        assert tg.guidance_at_register(_info(WakeupMethod.CLIPBOARD))["needs_protocol"] is True
        assert tg.guidance_at_register(_info(WakeupMethod.CLIPBOARD))["access_tier"] == tg.TIER_D


# ── §6.3 红线：表达需求、不指定实现 ─────────────────────────────
class TestDGuidanceRedlines:
    def test_states_requirements_not_implementation(self):
        """必须列出三项能力需求，且**不说怎么装**。"""
        g = tg.guidance_at_register(_info(WakeupMethod.CLIPBOARD))
        assert g["guidance_kind"] == "plugin"
        for i in ("①", "②", "③"):
            assert i in g["guidance"], f"三项能力需求缺 {i}"
        assert "重新注册" in g["guidance"], "要说明装完怎么让平台知道"

    def test_no_install_commands(self):
        """红线：不指定实现 —— 不得出现具体安装/实现命令。"""
        text = tg.GUIDANCE_PLUGIN
        banned = [r"\bnpm\s+install\b", r"\bpip\s+install\b", r"\bnpx\s+-y\b",
                  r"\buv\s+add\b", r"\bgem\s+install\b", r"如何编写|怎么实现",
                  r"\bextension\b|\baddon\b\s+install"]
        for pat in banned:
            assert not re.search(pat, text, re.I), f"引导文案越界：命中 {pat!r}"

    def test_tier_d_guidance_has_no_workspace_dep(self):
        """注册期还没有工作区坐标，文案里不该出现假坐标。"""
        g = tg.guidance_at_register(_info(WakeupMethod.CLIPBOARD))
        assert "ws_" not in g["guidance"]


# ── §10.2 C 档回执契约 ────────────────────────────────────────
class TestCTierReceipt:
    def test_receipt_contains_workspace_and_contract(self):
        """C 档文案必须含：工作区坐标 + 回报端点 + context_turns + 不回报的后果。"""
        info = _info(WakeupMethod.CLIPBOARD,
                     metadata={"plugin_marketplace": False})
        g = tg.guidance_at_artifact(
            info, workspace_dir="D:\\ws\\ws_abc",
            workshop_id="ws_x", member_id="m1",
            platform_url="http://127.0.0.1:18920",
        )
        assert g["access_tier"] == tg.TIER_C
        r = g["c_receipt"]
        assert "D:\\ws\\ws_abc" in r, "必须给工作区坐标"
        assert "activation-result" in r, "必须给回报端点"
        assert "context_turns" in r, "回执契约字段（§6.4）"
        assert "needs_human" in r, "必须说清不回报的后果"
        assert "ws_x" in r and "m1" in r
        assert g["c_receipt_required"] is True

    def test_tier_d_no_receipt_at_register(self):
        """D 档注册期没有工作区 -> 不给回执（等建间后给）。"""
        g = tg.guidance_at_register(_info(WakeupMethod.CLIPBOARD))
        assert "c_receipt" not in g
        assert "建间后" in g.get("note", "")


# ── §10.1 时机2：建间后提醒（启动方式 + 无桥型说明）────────────
class TestArtifactGuidance:
    def test_script_gets_startup_hint_and_process_warning(self):
        """脚本档：给启动命令 + 「桥必须常驻」提醒。"""
        g = tg.guidance_at_artifact(
            _info(WakeupMethod.ACP),
            workspace_dir="D:\\ws\\a", artifact_path="D:\\ws\\a\\bridge.py",
            artifact_kind="script", platform_url="http://127.0.0.1:18920")
        assert "startup_hint" in g and "bridge.py" in g["startup_hint"]
        assert "开机启动项" in g["reminder"], "桥不常驻等于收不到任务（注册页同款提醒）"

    def test_contract_says_no_process(self):
        g = tg.guidance_at_artifact(
            _info(WakeupMethod.HTTP_API),
            workspace_dir="D:\\ws\\b", artifact_path="D:\\ws\\b\\connect_contract.json",
            artifact_kind="contract", platform_url="http://127.0.0.1:18920")
        assert "0 进程" in g["reminder"] or "无桥" in g["reminder"]
        assert "startup_hint" not in g, "无桥型不该给启动命令"

    def test_prompt_says_manual_paste(self):
        g = tg.guidance_at_artifact(
            _info(WakeupMethod.CLIPBOARD),
            workspace_dir="D:\\ws\\c", artifact_path="D:\\ws\\c\\activation_prompt.txt",
            artifact_kind="prompt", platform_url="http://127.0.0.1:18920")
        assert "粘贴" in g["reminder"]


# ── §6.3 引导产出：needs_protocol 落 metadata ──────────────────
class TestApplyNeedsProtocol:
    def test_writes_flags_for_tier_d(self):
        info = _info(WakeupMethod.CLIPBOARD)
        changed = tg.apply_needs_protocol(info)
        assert changed is True
        assert info.metadata["needs_protocol"] is True
        assert info.metadata["access_tier"] == tg.TIER_D
        assert "插件市场" in info.metadata["protocol_guidance"]

    def test_idempotent(self):
        info = _info(WakeupMethod.CLIPBOARD)
        tg.apply_needs_protocol(info)
        assert tg.apply_needs_protocol(info) is False, "第二次不该再写（不改 created_at）"

    def test_does_not_mark_tier_a(self):
        """A 档有原生协议，不该被打 needs_protocol。"""
        info = _info(WakeupMethod.ACP)
        assert tg.apply_needs_protocol(info) is False
        assert "needs_protocol" not in info.metadata


# ── 接线：注册响应带档位 ────────────────────────────────────────
class TestWiring:
    def test_register_response_carries_tier(self):
        """注册响应必须带 access_tier，否则前端拿不到提醒（§10.1 时机1）。"""
        from agent_community.platform.routers import harness_register as hr
        info = _info(WakeupMethod.CLIPBOARD, harness_id="示例Harness-T",
                     harness_name="示例Harness-T")
        # 直接验证函数内用的那份 guidance（响应结构由返回 dict 保证）
        g = tg.guidance_at_register(info)
        assert g["access_tier"] == "D"
        assert "guidance" in g
        # 确认模块已接进 harness_register（源码含调用）
        import inspect
        src = inspect.getsource(hr.register_harness)
        assert "guidance_at_register" in src, "注册端点未接入档位提醒"
        assert "apply_needs_protocol" in src, "注册端点未写 needs_protocol"
        assert '"access_tier": _tier' in src, "注册响应未带档位"

    def test_artifact_response_carries_guidance(self):
        import inspect
        from agent_community.platform.routers import workshops_lifecycle as wl
        src = inspect.getsource(wl.workshop_connect_artifact)
        assert "guidance_at_artifact" in src, "产物端点未接入提醒"
        assert '"guidance": _g' in src, "产物响应未带提醒"
