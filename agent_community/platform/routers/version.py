# -*- coding: utf-8 -*-
"""`/api/version`：**给"一键更新"这类外部工具**的版本事实（只读）。

## 为什么要有它（2026-10-18 老板交办）

老板：「**我让宋去做一个一键更新的插件出来，到时候别人就可以一键更新了，不用跑到仓库来部署**」。
插件要做的第一件事是**问清楚"我现在是哪版、最新是哪版"** —— 这件事只能由平台自己回答，
不能靠插件去猜文件、也不能让用户去翻 CHANGELOG。

## 设计口径（唯一真相都从既有模块取）

- `current`：`agent_community.__version__`（与 CHANGELOG 最新条目由守卫保持一致的**唯一来源**）；
- `latest`：**离线不猜**。本端点**不主动联网**（平台是本地自部署，联网与否要看用户环境）；
  需要"远端最新版"的由插件自己去拉 Gitee Releases（契约里给了 URL 形状），
  这里只如实给出 `channel` 与 `release_url_tpl`；
- `data_dir`：本次运行的数据目录（更新只该动**安装目录**，不该动数据目录 —— 这点插件必须知道）；
- 失败要说话：任何拿不到的字段给 `null` 并附 `notes`，**不许编一个看起来成功的结果**。
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter

router = APIRouter(tags=["version"])

#: 更新渠道（Gitee 是唯一有效远端；GitHub 已于 2026-10-16 停更）
CHANNEL = {
    "repo": "https://gitee.com/TNDHY-cby/external-agents-community",
    "releases_api": "https://gitee.com/api/v5/repos/TNDHY-cby/external-agents-community/releases/latest",
    "release_tarball_tpl": "https://gitee.com/TNDHY-cby/external-agents-community/repository/archive/{tag}.tar.gz",
    "tags_ref": "refs/tags/vX.Y.Z",
}


def _platform_version() -> str:
    try:
        import agent_community
        return str(getattr(agent_community, "__version__", "") or "")
    except Exception:                                        # noqa: BLE001
        return ""


def _data_dir() -> str:
    try:
        from ..env_spec import data_dir
        return str(data_dir())
    except Exception:                                        # noqa: BLE001
        import os
        return os.environ.get("AC_DATA_DIR", "")


def _install_dir() -> str:
    """安装目录（＝本包所在的项目根）。更新**只该动这里**，不该动数据目录。"""
    return str(Path(__file__).resolve().parents[3])


def data_dir_risk(install: str, data: str) -> str:
    """数据目录与安装目录的关系 —— 返回""（安全）/ 一句警告。

    ★2026-10-18（实测本机就是这个坑）：默认 `AC_DATA_DIR` 落在**安装目录之内**
    （`<安装目录>/agent_community/data`）⇒ 插件"替换安装目录"会把用户的
    harness 注册、工作间、桥脚本、日志**一起删掉**。这必须明说，不能等出事。
    """
    if not install or not data:
        return "读不到目录，无法判断更新是否安全（插件应提示用户手工确认）"
    try:
        i = Path(install).resolve()
        d = Path(data).resolve()
    except OSError:
        return "目录路径解析失败，无法判断更新是否安全"
    if i == d:
        return "数据目录与安装目录**相同** ⇒ 更新安装目录会直接毁掉数据，必须先分离（设 AC_DATA_DIR 到别处）"
    try:
        d.relative_to(i)
    except ValueError:
        return ""                                     # 数据在安装目录之外 —— 安全形态
    return ("数据目录在**安装目录之内**（%s）⇒ 整体替换安装目录会连带删掉数据；"
            "插件必须先备份或提示用户把 AC_DATA_DIR 指到安装目录之外" % d)


@router.get("/api/version")
async def api_version():
    """版本事实（给外部更新工具；只读、不联网、缺什么就如实说 null）。"""
    notes = []
    current = _platform_version()
    if not current:
        notes.append("读不到 `agent_community.__version__`（安装可能不完整）")
    install = _install_dir()
    data = _data_dir()
    risk = data_dir_risk(install, data)
    if risk:
        notes.append(risk)
    if not data:
        notes.append("读不到数据目录（AC_DATA_DIR 未设置且默认路径不可用）")
    return {
        "current": current or None,
        "latest": None,                     # ★不猜：远端最新版由调用方按 channel 自行查询
        "update_available": None,           # ★没有 latest 就不可能有判断，如实给 null
        "channel": CHANNEL,
        "install_dir": install,
        "data_dir": data or None,
        "update_safety": {
            "replace_install_dir_only": True,
            "keep_data_dir": True,
            "restart_required": True,
            "restart_endpoint": None,       # 受控重启端点尚未提供（设计稿 V57 待拍板）
        },
        "notes": notes,
    }
