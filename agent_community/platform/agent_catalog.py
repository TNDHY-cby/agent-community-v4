# -*- coding: utf-8 -*-
"""Agent 清单（V80 A.2）：**数据，不是代码**。不绑定任何厂商，不写死任何人的路径。

首批只放"通用"条目（任何 npm 全局 CLI / 任何 pip 包），并支持用户用
`AC_AGENT_CATALOG_EXTRA=<json 文件>` 追加自己的条目（换机器不用改代码）。
"""
from __future__ import annotations

import json
import os

DEFAULT = [
    {"id": "npm-cli", "name": "任意 npm 全局 CLI", "kind": "cli",
     "detect": "npm", "version_args": ["--version"],
     "install_cmd": ["npm", "install", "-g", "%PACKAGE%"],
     "docs_url": "https://docs.npmjs.com/cli/v10/commands/npm-install",
     "note": "安装时用 package 填你的包名（例：某个 CLI agent 的 npm 包）"},
    {"id": "pip-cli", "name": "任意 pip 包（含 CLI）", "kind": "cli",
     "detect": "pip", "version_args": ["--version"],
     "install_cmd": ["pip", "install", "-U", "%PACKAGE%"],
     "docs_url": "https://pip.pypa.io/en/stable/cli/pip_install/",
     "note": "安装时用 package 填包名"},
]


def extra_path() -> str:
    return (os.environ.get("AC_AGENT_CATALOG_EXTRA") or "").strip()


def load() -> tuple:
    """返回 (条目列表, 错误说明)。自定义清单坏了要**说出来**，不静默丢。"""
    items = [dict(x) for x in DEFAULT]
    p = extra_path()
    if not p:
        return items, ""
    try:
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
        rows = data if isinstance(data, list) else data.get("agents") or []
        for row in rows:
            if isinstance(row, dict) and row.get("id"):
                items.append(dict(row))
        return items, ""
    except Exception as e:                                   # noqa: BLE001
        return items, "自定义清单读不出来（%s）：%s" % (p, str(e)[:120])


def by_id(agent_id: str):
    items, _ = load()
    for row in items:
        if str(row.get("id")) == agent_id:
            return row
    return None
