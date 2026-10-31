# -*- coding: utf-8 -*-
"""`platform/boot_prompt`：**Boot Protocol 模板**（对方 2026-10-27 答案 2 定稿）。

口径：
- **允许且必须写死两个绝对路径**（激活那一瞬间它是唯一事实，大模型需要锚点）；
- 模板只有一处真相（本文件）；改动 = 改这里，别的都不许自己拼字符串；
- 文中出现的两个路径必须**真实存在**（判据会去 `stat`，占位符一律红）。
"""
from __future__ import annotations

from pathlib import Path

TEMPLATE = """【系统启动协议（Boot Protocol）】
1. 身份声明：你是 Agent [{agent_key}]。当前激活的动态工作区（根B）为 `{workshop_path}`。你的永久心智根（根A）为 `{mind_path}`。
2. 强制挂载：只读根A的 `00_核心基模.md` 与 `01_记忆索引表.md`，挂载你的核心记忆与规则。
3. 加载上下文：读取根B的 `manifest.json`，认知当前任务焦点（task_focus：{task_focus}）。
4. 沙盒红线：禁止使用任何工具对根B进行全盘扫描；严禁读取 `manifest.json` 中的 `ignore_zones`（{ignore_zones}）。
5. 按需唤醒：根据当前任务关键词，在 `01_记忆索引表` 中匹配，仅且只读取匹配成功的 1-3 个 memories 文件。
6. 开始执行：结合以上上下文，开始今日工作。
{extra}"""


def render(*, agent_key: str, workshop_path: str, mind_path: str, task_focus: str = "",
           ignore_zones: list | None = None, extra: str = "") -> str:
    zones = "、".join(str(z) for z in (ignore_zones or [])) or "（未设）"
    return TEMPLATE.format(agent_key=agent_key, workshop_path=str(workshop_path),
                           mind_path=str(mind_path), task_focus=(task_focus or "（未填）"),
                           ignore_zones=zones, extra=extra)


def paths_are_real(text: str) -> tuple:
    """判据用：模板里声明的两个根必须**真存在**（不是占位符）。"""
    import re

    found = re.findall(r"`([^`]+)`", text or "")
    roots = [p for p in found if p.endswith("minds") or "/minds/" in p or p.count("/") + p.count("\\") >= 2]
    bad = [p for p in roots if not Path(p).exists()]
    return (not bad), bad
