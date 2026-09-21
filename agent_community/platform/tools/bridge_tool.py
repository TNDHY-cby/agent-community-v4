"""构桥工具 — 平台 Agent 的标准桥生成能力。

平台 Agent 只需调用本工具（传 harness_id + 可选模板名/输出目录），
工具内部从 harness 注册信息取真实参数，用平台内置模板渲染生成桥脚本，
并自动登记桥坐标。平台 Agent 不自由设计桥代码，杜绝个案手写。
"""

from __future__ import annotations

import re
from pathlib import Path

from ..tool_registry import BaseTool, ToolSchema, ToolResult
from ..bridge_factory import list_templates, generate, safe_slug
from ..harness_adapter import harness_manager

# 默认桥输出根：agent_community/bridges/{harness_slug}/bridge.py
# 本文件位于 agent_community/platform/tools/ 下，需上溯三级到 agent_community，
# 与 server.py 的 bridges_root（agent_community/bridges）保持一致，避免桥目录不一致。
_BRIDGES_ROOT = Path(__file__).resolve().parent.parent.parent / "bridges"


class GenerateBridgeTool(BaseTool):
    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name="generate_bridge",
            description=(
                "为指定 harness 生成标准桥脚本：从平台内置桥模板库选择模板，"
                "根据该 harness 的注册信息（acp_command/acp_cwd/model）做模板渲染，"
                "写入 bridges 目录并自动登记桥坐标。平台标准构桥能力，不依赖手工写码。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "harness_id": {
                        "type": "string",
                        "description": "目标 harness_id（必须已注册）",
                    },
                    "template": {
                        "type": "string",
                        "description": "桥模板名，默认 cli_acp（可用列表见 list_bridge_templates）",
                    },
                    "out_dir": {
                        "type": "string",
                        "description": "输出目录，默认 agent_community/bridges/{harness_id}",
                    },
                },
                "required": ["harness_id"],
            },
        )

    async def execute(self, **params) -> ToolResult:
        harness_id = (params.get("harness_id") or "").strip()
        template = (params.get("template") or "cli_acp").strip()
        out_dir = (params.get("out_dir") or "").strip()

        if not harness_id:
            return ToolResult("generate_bridge", False, "harness_id 必填")
        sess = harness_manager.sessions.get(harness_id)
        if not sess:
            return ToolResult("generate_bridge", False, f"harness {harness_id} 未注册")

        info = sess.info
        render_params = {
            "HARNESS_ID": harness_id,
            "ACP_COMMAND": (info.acp_command or "").strip(),
            "ACP_CWD": (info.acp_cwd or "").strip() or str(Path(info.acp_cwd or "").resolve() if info.acp_cwd else ""),
            "MODEL_NAME": (info.ai.model_name if info.ai else "") or "",
            "PROVIDER": (info.ai.provider if info.ai else "") or "",
            "DESCRIPTION": (info.ai.description if info.ai else "") or "",
        }

        # 校验必填：ACP 类必须给出拉起命令
        if not render_params["ACP_COMMAND"]:
            return ToolResult(
                "generate_bridge", False,
                f"harness {harness_id} 未配置 acp_command，无法生成 cli_acp 桥（请先补注册 acp_command/acp_cwd）",
            )

        target = Path(out_dir) if out_dir else (_BRIDGES_ROOT / safe_slug(harness_id))
        try:
            bridge_file = generate(template, render_params, target)
        except Exception as e:
            return ToolResult("generate_bridge", False, f"生成失败: {e}")

        # 自动登记桥坐标
        try:
            sess.info.bridge_dir = str(target)
            if sess.info.bridge_status != "tested":
                sess.info.bridge_status = "reported"
        except Exception:
            pass

        return ToolResult(
            "generate_bridge", True,
            f"桥已生成: {bridge_file}\n"
            f"harness_id={harness_id} template={template}\n"
            f"bridge_dir={target}\n"
            f"启动命令: python -u \"{bridge_file}\" --url http://127.0.0.1:18920",
        )


class ListBridgeTemplatesTool(BaseTool):
    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name="list_bridge_templates",
            description="列出平台内置桥模板库中可用的桥模板（按接口类型划分）。",
            parameters={
                "type": "object",
                "properties": {},
            },
        )

    async def execute(self, **params) -> ToolResult:
        templates = list_templates()
        if not templates:
            return ToolResult("list_bridge_templates", False, "桥模板库为空")
        lines = [f"- {t.get('name')}: {t.get('description', '')}" for t in templates]
        return ToolResult("list_bridge_templates", True, "可用桥模板:\n" + "\n".join(lines))


__all__ = ["GenerateBridgeTool", "ListBridgeTemplatesTool"]
