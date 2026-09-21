"""桥模板渲染生成器 — 平台标准构桥能力。

职责：根据 harness 注册信息，从平台内置桥模板库中选择模板，做【纯字符串替换】渲染，
生成可直接运行的桥脚本。渲染过程不依赖任何 LLM 智能，属于机械操作，保证可复现。

模板库位置: agent_community/bridge_templates/{template_name}/
  - bridge.py.tmpl   桥脚本模板（占位符形如 {{HARNESS_ID}}）
  - template.json    模板元数据（名称/描述/适用接口类型/必填字段）

对外入口:
  list_templates() -> list[dict]
  render(template_name, params) -> str
  generate(template_name, params, out_dir) -> Path   # 渲染并落盘，返回桥文件路径
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Optional

# agent_community/bridge_templates/
TEMPLATES_ROOT = Path(__file__).resolve().parent.parent / "bridge_templates"

_PLACEHOLDER_RE = re.compile(r"\{\{\s*([A-Z0-9_]+)\s*\}\}")
_LITERAL_RE = re.compile(r"\{\{\s*([A-Z0-9_]+)_LIT\s*\}\}")


class BridgeTemplateError(Exception):
    pass


def list_templates() -> list[dict]:
    """列出平台内置桥模板及其元数据。"""
    if not TEMPLATES_ROOT.exists():
        return []
    result = []
    for d in sorted(TEMPLATES_ROOT.iterdir()):
        if not d.is_dir():
            continue
        tmpl = d / "bridge.py.tmpl"
        meta = d / "template.json"
        if not tmpl.exists():
            continue
        info: dict[str, Any] = {"name": d.name, "template_file": str(tmpl)}
        if meta.exists():
            try:
                info.update(json.loads(meta.read_text(encoding="utf-8")))
            except Exception:
                info["meta_error"] = "template.json 解析失败"
        result.append(info)
    return result


def _load_template(template_name: str) -> tuple[str, dict]:
    tdir = TEMPLATES_ROOT / template_name
    tmpl = tdir / "bridge.py.tmpl"
    if not tmpl.exists():
        raise BridgeTemplateError(f"模板不存在: {template_name}（可用: {[t['name'] for t in list_templates()]}）")
    meta: dict = {}
    meta_file = tdir / "template.json"
    if meta_file.exists():
        meta = json.loads(meta_file.read_text(encoding="utf-8"))
    return tmpl.read_text(encoding="utf-8"), meta


def _check_required(meta: dict, params: dict) -> None:
    for field in meta.get("required_fields", []):
        if field not in params or params.get(field) in (None, ""):
            raise BridgeTemplateError(f"缺少模板必填字段: {field}")


def render(template_name: str, params: dict) -> str:
    """渲染模板：占位符替换 → 校验残留占位符。返回完整脚本文本。"""
    text, meta = _load_template(template_name)
    _check_required(meta, params)

    def _sub(m: re.Match) -> str:
        key = m.group(1)
        if key not in params:
            raise BridgeTemplateError(f"模板占位符 {key} 未提供参数（模板: {template_name}）")
        return str(params[key])

    # 先处理 _LIT 字面量占位符（repr 转义，安全注入含引号/反斜杠的值）
    def _sub_lit(m: re.Match) -> str:
        key = m.group(1)
        if key not in params:
            raise BridgeTemplateError(f"模板占位符 {key}_LIT 未提供参数（模板: {template_name}）")
        return repr(str(params[key]))

    text = _LITERAL_RE.sub(_sub_lit, text)
    text = _PLACEHOLDER_RE.sub(_sub, text)
    leftover = sorted(set(_PLACEHOLDER_RE.findall(text)))
    if leftover:
        raise BridgeTemplateError(f"渲染后仍残留占位符: {leftover}（模板: {template_name}）")
    return text


def safe_slug(name: str) -> str:
    """harness_id 转安全目录名。"""
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "_", name.strip())
    return slug or "harness"


def generate(
    template_name: str,
    params: dict,
    out_dir: str | Path,
    filename: str = "bridge.py",
) -> Path:
    """渲染模板并写入 out_dir/filename，返回桥文件路径。"""
    text = render(template_name, params)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    bridge_file = out / filename
    bridge_file.write_text(text, encoding="utf-8")
    return bridge_file
