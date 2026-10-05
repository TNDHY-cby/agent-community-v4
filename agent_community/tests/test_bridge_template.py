# -*- coding: utf-8 -*-
"""V-15 步骤4 测试：L2 具体桥模板。

设计稿 §十四 步骤4 的验证要求是「**单测：模板可存可读**」，据此外扩：
  A. 四档映射（L1 模板名 + artifact_kind）—— §5 三层桥的中间层
  B. 可存可读 + 幂等（参数没变不覆盖）
  C. 参数完整性可校验（complete / missing）—— 注册即告知"差什么"
  D. **不被 agent_token 流程抹掉**（register_harness 里 metadata 会被
     「拷贝 -> 加 token -> 回写」动一次，这是最容易把 L2 冲掉的地方）
"""
from __future__ import annotations

import pytest

from agent_community.platform import bridge_template as bt
from agent_community.platform.protocol import (
    HarnessInfo,
    HarnessTool,
    WakeupMethod,
)


def _info(**kw):
    base = dict(
        harness_id="示例Harness-A",
        harness_name="示例Harness-A",
        acp_command="npx -y acp-bridge",
        acp_cwd="D:\\bridge",
        wakeup_dir="D:\\inbox\\A",
        description="示例桥",
        wakeup_method=WakeupMethod.ACP,
        # ⚠️ HarnessAI.model_name 是**必填**（其余字段都有默认），
        # 不给会直接 ValidationError，全部用例一起红。
        ai={"model_name": "示例模型", "provider": "示例供应商"},
    )
    base.update(kw)
    return HarnessInfo(**base)


# ── A. 四档映射 ─────────────────────────────────────────────────
class TestFourTierMapping:
    def test_acp_maps_to_cli_acp_script(self):
        t = bt.build(_info(wakeup_method=WakeupMethod.ACP))
        assert t["template"] == "cli_acp"
        assert t["artifact_kind"] == "script"

    def test_file_poll_maps_to_file_poll_script(self):
        t = bt.build(_info(wakeup_method=WakeupMethod.FILE_POLL))
        assert t["template"] == "file_poll"
        assert t["artifact_kind"] == "script"

    def test_http_api_has_no_template_but_contract(self):
        """http_api 无桥 —— 不给模板，给 contract 让下游派生连接契约。"""
        t = bt.build(_info(wakeup_method=WakeupMethod.HTTP_API,
                           api_base_url="http://127.0.0.1:9500"))
        assert t["template"] is None
        assert t["artifact_kind"] == "contract"

    def test_clipboard_maps_to_prompt(self):
        """clipboard 无桥 —— 产物是提示词（§11.1 人工粘贴）。"""
        t = bt.build(_info(wakeup_method=WakeupMethod.CLIPBOARD))
        assert t["template"] is None
        assert t["artifact_kind"] == "prompt"

    def test_unknown_falls_back_to_pending_poll(self):
        """不在映射表里的档（此处用 mcp，它不在 WM_TO_TEMPLATE 里）-> 兜底 pending_poll。
        与 auto-connect 的「else → pending_poll」分支口径一致。"""
        assert "mcp" not in bt.WM_TO_TEMPLATE, "用例前提：mcp 不在显式映射里"
        t = bt.build(_info(wakeup_method=WakeupMethod.MCP))
        assert t["template"] == "pending_poll"
        assert t["artifact_kind"] == "script"

    def test_desired_params_present(self):
        """cli_acp 必须带 L1 要求的六个参数。"""
        t = bt.build(_info())
        for k in ("HARNESS_ID", "ACP_COMMAND", "ACP_CWD", "MODEL_NAME",
                  "PROVIDER", "DESCRIPTION"):
            assert k in t["params"], f"缺参数 {k}"
        assert set(t["required"]) == set(t["params"])


# ── B. 可存可读 + 幂等 ───────────────────────────────────────────
class TestPersistAndIdempotent:
    def test_ensure_then_get_roundtrip(self):
        info = _info()
        t = bt.ensure(info)
        assert t is not None
        got = bt.get(info)
        assert got is not None
        assert got["template"] == "cli_acp"
        assert got["params"]["ACP_COMMAND"] == "npx -y acp-bridge"
        # 存进 metadata，将来随 save_state -> info.model_dump() 一起落盘
        assert bt.METADATA_KEY in info.metadata

    def test_ensure_is_idempotent_when_unchanged(self):
        info = _info()
        first = bt.ensure(info)
        second = bt.ensure(info)
        assert first["created_at"] == second["created_at"], \
            "参数没变不应重建（否则 created_at 每次被刷新，等于没幂等）"

    def test_ensure_updates_when_params_change(self):
        info = _info()
        bt.ensure(info)
        info.acp_command = "node bridge.js"
        changed = bt.ensure(info)
        assert changed["params"]["ACP_COMMAND"] == "node bridge.js"

    def test_force_regenerate(self):
        info = _info()
        a = bt.ensure(info)
        b = bt.ensure(info, force=True)
        assert b["created_at"] >= a["created_at"]


# ── C. 参数完整性可校验 ─────────────────────────────────────────
class TestCompleteness:
    def test_missing_acp_command_flagged(self):
        info = _info(acp_command="")
        t = bt.build(info)
        assert t["complete"] is False
        assert "ACP_COMMAND" in t["missing"]

    def test_complete_when_all_present(self):
        t = bt.build(_info())
        assert t["complete"] is True, f"本应完整，missing={t['missing']}"

    def test_status_of_reports_missing(self):
        info = _info(acp_command="")
        bt.ensure(info)
        s = bt.status_of(info)
        assert s["has_template"] is True
        assert s["complete"] is False
        assert "ACP_COMMAND" in s["missing"]

    def test_status_of_for_no_template_type(self):
        """无桥型：status_of 给 artifact_kind 而不是报错。"""
        info = _info(wakeup_method=WakeupMethod.HTTP_API)
        s = bt.status_of(info)
        assert s["has_template"] is False
        assert s["artifact_kind"] == "contract"


# ── D. 不被 agent_token 流程抹掉 ────────────────────────────────
class TestSurvivesTokenFlow:
    def test_metadata_copy_add_token_keeps_l2(self):
        """复刻 register_harness 的 meta = dict(...) -> 加 token -> 回写。"""
        info = _info()
        bt.ensure(info)

        meta = dict(info.metadata or {})     # ← 原代码就是这么拷的
        meta["agent_token"] = "tok_test_123"
        info.metadata = meta

        got = bt.get(info)
        assert got is not None, "**L2 模板被 token 流程抹掉了**"
        assert got["template"] == "cli_acp"
        assert info.metadata["agent_token"] == "tok_test_123"

    def test_capabilities_captured(self):
        t = bt.build(_info(tools=[HarnessTool(name="read_file"),
                                  HarnessTool(name="write_file")]))
        assert t["capabilities"] == ["read_file", "write_file"]

    def test_no_tools_yields_empty_capabilities(self):
        t = bt.build(_info(tools=[]))
        assert t["capabilities"] == []
